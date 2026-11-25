"""Batch visual AI safety & relevance filter using FFmpeg frame extraction + Gemini Vision."""

import json
import logging
import subprocess
import time
from pathlib import Path
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

VISION_MODELS = (
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
)
TRANSIENT_MARKERS = (
    "503",
    "504",
    "unavailable",
    "deadline",
    "timed out",
    "timeout",
    "high demand",
    "overloaded",
)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _extract_preview_bytes(media_path: Path, work_dir: Path) -> bytes | None:
    """Return JPEG bytes for visual inspection (direct resize for images, 35%-seek frame for videos)."""
    frame_path = work_dir / f"{media_path.stem}_check.jpg"
    try:
        if media_path.suffix.lower() in IMAGE_EXTS:
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(media_path), "-vf", "scale=384:-1", "-q:v", "7", str(frame_path),
            ]
        else:
            dur_cmd = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(media_path),
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
                "-ss", f"{seek:.2f}", "-i", str(media_path),
                "-vframes", "1", "-vf", "scale=384:-1", "-q:v", "7", str(frame_path),
            ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and frame_path.exists() and frame_path.stat().st_size > 800:
            return frame_path.read_bytes()
        return None
    finally:
        frame_path.unlink(missing_ok=True)


class VisualSafetyFilter:
    """Batch-inspects video frames & photos with Gemini Vision (1 API call per chunk to save quota)."""

    def __init__(self, api_key: str) -> None:
        self.client = genai.Client(api_key=api_key) if api_key else None

    def check_batch(
        self,
        items: list[tuple[Path, str]],
        work_dir: Path,
        allow_animations: bool = True,
        video_topic: str = "",
    ) -> list[tuple[bool, str]]:
        """Check media items in chunks of <= 8 via Gemini Vision, anchored to overall video_topic."""
        if not items:
            return []
        if self.client is None:
            return [(True, "no_api_key")] * len(items)

        chunk_size = 8
        all_results: list[tuple[bool, str]] = []
        for start in range(0, len(items), chunk_size):
            chunk = items[start : start + chunk_size]
            all_results.extend(
                self._check_chunk(
                    chunk,
                    work_dir,
                    allow_animations=allow_animations,
                    video_topic=video_topic,
                )
            )
        return all_results

    def _check_chunk(
        self,
        items: list[tuple[Path, str]],
        work_dir: Path,
        allow_animations: bool = True,
        video_topic: str = "",
    ) -> list[tuple[bool, str]]:
        """Inspect up to 8 media items in a single Gemini Vision call with transient retry."""
        results: list[tuple[bool, str]] = [(False, "unreadable")] * len(items)
        valid_indices: list[int] = []
        parts: list = []

        rule_4 = (
            "4. Ugly abstract wireframes, meaningless fractals, or blurry/dark/low-res footage "
            "(Clear, colorful educational science animations OR sharp real photos/videos ARE ALLOWED)."
            if allow_animations
            else "4. Abstract lines, wireframes, 2D cartoons, CGI, or blurry footage (MUST be sharp real camera footage)."
        )

        topic_line = f'OVERALL VIDEO TOPIC: "{video_topic}"\n' if video_topic else ""
        header = (
            "You are inspecting numbered visual frames for a high-quality, family-friendly YouTube Science Short.\n"
            f"{topic_line}"
            "For EACH numbered image below, reject (safe=false) if ANY apply:\n"
            "1. Nudity, suggestive/sexual content, exposed skin/swimwear, gore, blood, or medical surgery.\n"
            "2. Mostly text on a plain background, title card, slide, book page, chart, diagram, map, or logo screen.\n"
            "3. Random people talking, crowds, sports stadium, news studio, buildings, or political speech.\n"
            f"{rule_4}\n"
            "5. Visually UNRELATED to BOTH its requested beat query AND the OVERALL VIDEO TOPIC/habitat, wrong-category/homonym mismatch "
            "(e.g. a bird/forest/volcano/desert/building in a deep-sea fish video, a cockatoo when asking for a parrotfish, space/planets in a biology/chemistry video), "
            "or blank/black/very dark unreadable frame.\n\n"
            'Return ONLY a JSON object: {"results": [{"index": 0, "safe": true/false, "reason": "short reason"}, ...]}\n'
        )
        parts.append(header)

        for idx, (path, topic_query) in enumerate(items):
            img_bytes = _extract_preview_bytes(path, work_dir)
            if not img_bytes:
                results[idx] = (False, "corrupt_or_unreadable")
                continue
            valid_indices.append(idx)
            parts.append(f"\nImage index {idx} (requested query: '{topic_query}'):")
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))

        if not valid_indices:
            return results

        for m_idx, model_name in enumerate(VISION_MODELS):
            # Retry transient 503/504/timeout up to 2 extra times on primary 500/day model
            max_attempts = 3 if m_idx == 0 else 1
            for attempt in range(max_attempts):
                try:
                    resp = self.client.models.generate_content(
                        model=model_name,
                        contents=parts,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.0,
                            http_options=types.HttpOptions(timeout=45000),
                        ),
                    )
                    parsed = json.loads(resp.text)
                    for entry in parsed.get("results", []):
                        i = int(entry.get("index", -1))
                        if 0 <= i < len(results):
                            results[i] = (bool(entry.get("safe", False)), str(entry.get("reason", "ok")))
                    return results
                except Exception as exc:
                    err_low = str(exc).lower()
                    is_transient = any(tok in err_low for tok in TRANSIENT_MARKERS)
                    if is_transient and attempt + 1 < max_attempts:
                        delay = 2.0 if attempt == 0 else 4.5
                        logger.warning(
                            "Transient vision error on %s (%s); retrying in %.1fs...",
                            model_name,
                            exc,
                            delay,
                        )
                        time.sleep(delay)
                        continue
                    logger.warning("Batch vision check on %s failed (%s); trying fallback...", model_name, exc)
                    break

        # If every Gemini Vision model failed, fail closed for safety
        for i in valid_indices:
            results[i] = (False, "vision_api_unavailable")
        return results

    def is_clip_safe_and_relevant(
        self,
        video_path: Path,
        topic_query: str,
        work_dir: Path,
        allow_animations: bool = True,
        video_topic: str = "",
    ) -> tuple[bool, str]:
        """Single-item convenience wrapper."""
        return self.check_batch(
            [(video_path, topic_query)],
            work_dir,
            allow_animations=allow_animations,
            video_topic=video_topic,
        )[0]
