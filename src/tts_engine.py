"""Text-to-speech synthesis with word-level timestamps and exact ffprobe duration."""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
import edge_tts
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


@dataclass
class WordTiming:
    """Exact start/end time in seconds for a spoken word."""

    text: str
    start_sec: float
    end_sec: float


@dataclass
class TTSResult:
    """Synthesized audio file plus word timings and total duration."""

    audio_path: Path
    duration_sec: float
    words: list[WordTiming]


def _probe_audio_duration(audio_path: Path) -> float:
    """Measure exact audio duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            return float(proc.stdout.strip())
        except ValueError:
            pass
    return 0.0


def _estimate_word_timings(text: str, total_audio_sec: float) -> list[WordTiming]:
    """Fallback proportional word timings if Edge TTS omits WordBoundary events."""
    raw_words = [w for w in text.split() if w.strip()]
    if not raw_words or total_audio_sec <= 0:
        return []
    total_chars = sum(max(len(w), 1) for w in raw_words)
    timings: list[WordTiming] = []
    cursor = 0.0
    for w in raw_words:
        dur = total_audio_sec * (max(len(w), 1) / total_chars)
        timings.append(WordTiming(text=w, start_sec=cursor, end_sec=cursor + dur))
        cursor += dur
    return timings


class TTSEngine:
    """Synthesizes English voiceovers and extracts word boundaries + exact duration."""

    def __init__(self, voice: str = "en-US-AndrewMultilingualNeural", rate: str = "+8%") -> None:
        self.voice = voice
        self.rate = rate

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
    async def synthesize(self, text: str, output_audio_path: Path) -> TTSResult:
        """Generate MP3 voiceover, exact ffprobe duration, and word-level timestamps."""
        output_audio_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.rate,
                boundary="WordBoundary",
            )
        except TypeError:
            # Older edge-tts versions without explicit boundary kwarg
            communicate = edge_tts.Communicate(text=text, voice=self.voice, rate=self.rate)

        words: list[WordTiming] = []
        audio_chunks = bytearray()
        max_end_sec = 0.0

        async for chunk in communicate.stream():
            chunk_type = chunk.get("type")
            if chunk_type == "audio":
                audio_chunks.extend(chunk["data"])
            elif chunk_type in ("WordBoundary", "SentenceBoundary"):
                start_sec = chunk["offset"] / 10_000_000.0
                dur_sec = chunk["duration"] / 10_000_000.0
                end_sec = start_sec + dur_sec
                word_text = str(chunk.get("text", "")).strip()
                if word_text and chunk_type == "WordBoundary":
                    words.append(WordTiming(text=word_text, start_sec=start_sec, end_sec=end_sec))
                if end_sec > max_end_sec:
                    max_end_sec = end_sec

        if not audio_chunks:
            raise RuntimeError("Edge TTS returned empty audio")

        output_audio_path.write_bytes(bytes(audio_chunks))

        # Always measure real MP3 duration with ffprobe so audio is NEVER cut off early!
        probed_sec = _probe_audio_duration(output_audio_path)
        actual_audio_sec = max(probed_sec, max_end_sec)
        if actual_audio_sec < 5.0:
            raise RuntimeError(f"Audio duration unexpectedly short ({actual_audio_sec:.2f}s)")

        if not words:
            logger.warning("No WordBoundary events received; using proportional fallback captions")
            words = _estimate_word_timings(text, actual_audio_sec)

        # Add +0.55s tail pad so the final sentence always finishes cleanly before the clip ends
        total_duration = actual_audio_sec + 0.55
        logger.info(
            "Synthesized TTS: probed=%.2fs, total=%.2fs, %d word timings -> %s",
            probed_sec,
            total_duration,
            len(words),
            output_audio_path.name,
        )
        return TTSResult(audio_path=output_audio_path, duration_sec=total_duration, words=words)
