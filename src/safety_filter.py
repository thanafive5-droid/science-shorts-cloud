"""Batch visual AI safety & relevance filter using FFmpeg frame extraction + Gemini Vision."""

import json
import logging
import subprocess
from pathlib import Path
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

VISION_MODELS = (
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _extract_preview_bytes(media_path: Path, work_dir: Path) -> bytes | None:
    """Return JPEG bytes for visual inspection (direct resize for images, 35%-seek frame for videos)."""
    frame_path = work_dir / f"{media_path.stem}_check.jpg"
    try:
        if media_path.suffix.lower() in IMAGE_EXTS:
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(media_path), "-vf", "scale=480:-1", "-q:v", "6", str(frame_path),
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
                "-vframes", "1", "-vf", "scale=480:-1", "-q:v", "6", str(frame_path),
            ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and frame_path.exists() and frame_path.stat().st_size > 800:
            return frame_path.read_bytes()
        return None
    finally:
        frame_path.unlink(missing_ok=True)


class VisualSafetyFilter:
    """Batch-inspects video frames & photos with Gemini Vision (1 API call per batch to save quota)."""

    def __init__(self, api_key: str) -> None:
        self.client = genai.Client(api_key=api_key) if api_key else None

    def check_batch(
        self,
        items: list[tuple[Path, str]],
        work_dir: Path,
        allow_animations: bool = True,
    ) -> list[tuple[bool, str]]:
        """Check up to ~12 media items in ONE single Gemini Vision call (cuts API usage by ~85%)."""
        if not items:
            return []
        if self.client is None:
            return [(True, "no_api_key")] * len(items)

        results: list[tuple[bool, str]] = [(False, "unreadable")] * len(items)
        valid_indices: list[int] = []
        parts: list = []

        rule_4 = (
            "4. Ugly abstract wireframes, meaningless fractals, or blurry/dark/low-res footage "
            "(Clear, colorful educational science animations OR sharp real photos/videos ARE ALLOWED)."
            if allow_animations
            else "4. Abstract lines, wireframes, 2D cartoons, CGI, or blurry footage (MUST be sharp real camera footage)."
        )

        header = (
            "You are inspecting numbered visual frames for a high-quality, family-friendly YouTube Science Short.\n"
            "For EACH numbered image below, reject (safe=false) if ANY apply:\n"
            "1. Nudity, suggestive/sexual content, exposed skin/swimwear, gore, blood, or medical surgery.\n"
            "2. Mostly text on a plain background, title card, slide, book page, or logo screen.\n"
            "3. Random people talking, crowds, sports stadium, news studio, or political speech.\n"
            f"{rule_4}\n"
            "5. Visually UNRELATED to its requested topic, or blank/black/very dark frame.\n\n"
            "Return ONLY a JSON object: {\"results\": [{\"index\": 0, \"safe\": true/false, \"reason\": \"short reason\"}, ...]}\n"
        )
        parts.append(header)

        for idx, (path, topic_query) in enumerate(items):
            img_bytes = _extract_preview_bytes(path, work_dir)
            if not img_bytes:
                results[idx] = (False, "corrupt_or_unreadable")
                continue
            valid_indices.append(idx)
            parts.append(f"\nImage index {idx} (requested topic: '{topic_query}'):")
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))

        if not valid_indices:
            return results

        for model_name in VISION_MODELS:
            try:
                resp = self.client.models.generate_content(
                    model=model_name,
                    contents=parts,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.0,
                        http_options=types.HttpOptions(timeout=25000),
                    ),
                )
                parsed = json.loads(resp.text)
                for entry in parsed.get("results", []):
                    i = int(entry.get("index", -1))
                    if 0 <= i < len(results):
                        results[i] = (bool(entry.get("safe", False)), str(entry.get("reason", "ok")))
                return results
            except Exception as exc:
                logger.warning("Batch vision check on %s failed (%s); trying fallback...", model_name, exc)

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
    ) -> tuple[bool, str]:
        """Single-item convenience wrapper."""
        return self.check_batch([(video_path, topic_query)], work_dir, allow_animations=allow_animations)[0]
