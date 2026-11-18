"""Text-to-speech synthesis with word-level timestamps using Edge TTS."""

import logging
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


class TTSEngine:
    """Synthesizes English voiceovers and extracts word boundaries."""

    def __init__(self, voice: str = "en-US-AndrewMultilingualNeural", rate: str = "+8%") -> None:
        self.voice = voice
        self.rate = rate

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
    async def synthesize(self, text: str, output_audio_path: Path) -> TTSResult:
        """Generate MP3 voiceover and word-level timestamps."""
        output_audio_path.parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(text=text, voice=self.voice, rate=self.rate)

        words: list[WordTiming] = []
        audio_chunks = bytearray()
        max_end_sec = 0.0

        async for chunk in communicate.stream():
            chunk_type = chunk.get("type")
            if chunk_type == "audio":
                audio_chunks.extend(chunk["data"])
            elif chunk_type == "WordBoundary":
                # Edge TTS offsets/durations are in 100-nanosecond ticks (1e7 ticks = 1 sec)
                start_sec = chunk["offset"] / 10_000_000.0
                dur_sec = chunk["duration"] / 10_000_000.0
                end_sec = start_sec + dur_sec
                word_text = str(chunk.get("text", "")).strip()
                if word_text:
                    words.append(WordTiming(text=word_text, start_sec=start_sec, end_sec=end_sec))
                if end_sec > max_end_sec:
                    max_end_sec = end_sec

        output_audio_path.write_bytes(bytes(audio_chunks))
        # Add a 0.65s tail pad so the final sentence always finishes completely before the clip ends
        total_duration = max(max_end_sec + 0.65, 5.0)
        logger.info(
            "Synthesized TTS: %.2fs, %d word timings -> %s",
            total_duration,
            len(words),
            output_audio_path.name,
        )
        return TTSResult(audio_path=output_audio_path, duration_sec=total_duration, words=words)
