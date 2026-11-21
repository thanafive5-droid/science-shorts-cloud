"""Visual AI safety & quality filter using FFmpeg frame extraction + Gemini Vision."""

import json
import logging
import subprocess
from pathlib import Path
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def _extract_preview_frame(video_path: Path, out_jpg: Path) -> bool:
    """Extract a single JPEG frame from ~35% into the video for visual inspection."""
    dur_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
    ]
    dur_proc = subprocess.run(dur_cmd, capture_output=True, text=True)
    seek = 2.0
    if dur_proc.returncode == 0 and dur_proc.stdout.strip():
        try:
            clip_dur = float(dur_proc.stdout.strip())
            seek = max(0.5, min(clip_dur * 0.35, max(0.5, clip_dur - 1.0)))
        except ValueError:
            pass

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{seek:.2f}", "-i", str(video_path),
        "-vframes", "1", "-vf", "scale=480:-1", "-q:v", "6", str(out_jpg),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and out_jpg.exists() and out_jpg.stat().st_size > 1000


class VisualSafetyFilter:
    """Inspects downloaded video frames with Gemini Vision to block NSFW, crowds, and title cards."""

    def __init__(self, api_key: str) -> None:
        self.client = genai.Client(api_key=api_key) if api_key else None

    def is_clip_safe_and_relevant(
        self,
        video_path: Path,
        topic_query: str,
        work_dir: Path,
        allow_animations: bool = False,
    ) -> tuple[bool, str]:
        """Return (True, 'ok') only if the clip frame is 100% YouTube-safe and visual."""
        if self.client is None:
            return True, "no_api_key"

        frame_path = work_dir / f"{video_path.stem}_check.jpg"
        if not _extract_preview_frame(video_path, frame_path):
            return False, "corrupt_or_unreadable_video"

        try:
            img_bytes = frame_path.read_bytes()
            if allow_animations:
                rule_4 = (
                    "4. Confusing abstract geometric wireframes, meaningless fractals, or low-quality clutter "
                    "(Clear, easy-to-understand educational Earth/science animations OR real camera footage ARE ALLOWED)."
                )
            else:
                rule_4 = (
                    "4. Abstract geometric lines, wireframes, 2D cartoons, CGI simulations, fractals, or shapes "
                    "on a black background (MUST be real-world camera footage or real telescope/microscope imagery)."
                )
            prompt = (
                f"Inspect this video frame intended for a family-friendly YouTube Science Short "
                f"about '{topic_query}'.\n"
                "Reject (safe=false) if ANY of these apply:\n"
                "1. Nudity, suggestive/sexual content, exposed skin/swimwear, gore, blood, or medical surgery.\n"
                "2. Mostly text on a solid/plain background, title card, slide, book page, or logo screen.\n"
                "3. Random people talking, crowds, sports stadium, news studio, or political speech.\n"
                f"{rule_4}\n"
                "5. Completely blank/black frame.\n"
                "Return ONLY JSON: {\"safe\": true/false, \"reason\": \"short reason\"}"
            )
            resp = self.client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                    http_options=types.HttpOptions(timeout=15000),
                ),
            )
            data = json.loads(resp.text)
            is_safe = bool(data.get("safe", False))
            reason = str(data.get("reason", "unknown"))
            return is_safe, reason
        except Exception as exc:
            logger.warning("Visual safety check failed (%s); rejecting unverified clip to stay safe.", exc)
            return False, f"check_error:{exc}"
        finally:
            frame_path.unlink(missing_ok=True)
