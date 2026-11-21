"""End-to-end automated pipeline for generating and publishing a single Science Short."""

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from src.config import CHANNELS, Settings
from src.database import FactoryDatabase
from src.media_fetcher import MediaFetcher
from src.safety_filter import VisualSafetyFilter
from src.script_generator import ScriptGenerator
from src.tts_engine import TTSEngine
from src.video_editor import VideoEditor
from src.youtube_uploader import YouTubeUploader

logger = logging.getLogger(__name__)


class ShortsPipeline:
    """Coordinates script generation, TTS, real video fetching, editing, and upload."""

    def __init__(self, settings: Settings, db: FactoryDatabase) -> None:
        self.settings = settings
        self.db = db
        self.script_gen = ScriptGenerator(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )
        self.tts = TTSEngine(voice=settings.tts_voice, rate=settings.tts_rate)
        self.safety_filter = VisualSafetyFilter(api_key=settings.gemini_api_key)
        self.media = MediaFetcher(
            pexels_api_key=settings.pexels_api_key,
            pixabay_api_key=settings.pixabay_api_key,
            safety_filter=self.safety_filter,
        )
        self.editor = VideoEditor()
        self.uploader = YouTubeUploader()
        # Serialize heavy FFmpeg rendering so cloud container RAM stays predictable
        self._render_lock = asyncio.Lock()

    async def run_channel_job(self, channel_id: str, force: bool = False) -> None:
        """Execute one full Short creation & upload cycle for a channel."""
        if not force:
            allowed, reason = self.db.can_post_now(channel_id)
            if not allowed:
                logger.info("Skipping [%s] scheduled tick: %s (0 quota used)", channel_id, reason)
                return

        channel = CHANNELS[channel_id]
        job_id = f"{channel_id}_{uuid.uuid4().hex[:8]}"
        job_dir = self.settings.work_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        topic = "unknown"
        title = "unknown"

        async with self._render_lock:
            logger.info("=== Starting job [%s] for channel [%s] ===", job_id, channel.display_name)
            try:
                recent_topics = self.db.get_recent_topics(channel_id, limit=80)
                script = await asyncio.to_thread(self.script_gen.generate, channel, recent_topics)
                topic = script.topic
                title = script.title

                tts_result = await self.tts.synthesize(
                    script.full_narration,
                    job_dir / "voiceover.mp3",
                )

                queries = [beat.search_query for beat in script.beats]
                raw_clips = await self.media.fetch_clips_for_beats(
                    queries=queries,
                    output_dir=job_dir / "clips",
                    fallback_query=f"{channel.display_name.lower()} science",
                    nasa_ratio=channel.nasa_ratio,
                    allow_animations=channel.allow_animations,
                )

                final_video = job_dir / "final_short.mp4"
                await asyncio.to_thread(
                    self.editor.render_short,
                    raw_clips,
                    tts_result,
                    job_dir,
                    final_video,
                )

                creds = self.settings.get_youtube_credentials(channel_id)
                if self.settings.dry_run or creds is None:
                    reason = "DRY_RUN=true" if self.settings.dry_run else "missing YouTube OAuth env vars"
                    logger.warning("Skipping upload for [%s] (%s)", channel_id, reason)
                    self.db.mark_topic_used(channel_id, topic)
                    self.db.record_post(
                        channel_id=channel_id,
                        topic=topic,
                        title=title,
                        status="dry_run",
                        duration_sec=round(tts_result.duration_sec, 2),
                        clip_count=len(raw_clips),
                    )
                else:
                    video_id = await asyncio.to_thread(
                        self.uploader.upload_short,
                        creds,
                        final_video,
                        script.title,
                        script.description,
                        script.tags,
                    )
                    self.db.mark_topic_used(channel_id, topic)
                    self.db.record_post(
                        channel_id=channel_id,
                        topic=topic,
                        title=title,
                        status="uploaded",
                        youtube_video_id=video_id,
                        duration_sec=round(tts_result.duration_sec, 2),
                        clip_count=len(raw_clips),
                    )

                logger.info("=== Completed job [%s] ===", job_id)

            except Exception as exc:
                logger.exception("Job [%s] failed: %s", job_id, exc)
                self.db.record_post(
                    channel_id=channel_id,
                    topic=topic,
                    title=title,
                    status="failed",
                    error_message=str(exc)[:500],
                )
            finally:
                # Always clean up temporary video files to keep cloud disk free
                shutil.rmtree(job_dir, ignore_errors=True)
