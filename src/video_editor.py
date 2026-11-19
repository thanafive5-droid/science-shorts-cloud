"""FFmpeg-based 9:16 fast-paced 2-3 second cut video editor with burned-in captions."""

import logging
import subprocess
from pathlib import Path
from src.tts_engine import TTSResult, WordTiming

logger = logging.getLogger(__name__)


def _format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp H:MM:SS.cc."""
    seconds = max(0.0, seconds)
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


def build_ass_subtitles(words: list[WordTiming], ass_path: Path) -> Path:
    """Group words into punchy 2-3 word Shorts captions in ASS format."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,74,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,380,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    chunk_size = 3
    for i in range(0, len(words), chunk_size):
        group = words[i : i + chunk_size]
        start = _format_ass_time(group[0].start_sec)
        end = _format_ass_time(group[-1].end_sec + 0.08)
        text = " ".join(w.text for w in group).upper().replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")

    ass_path.write_text("".join(lines), encoding="utf-8")
    return ass_path


def _run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {proc.stderr.strip()}")


def _probe_video_duration(video_path: Path) -> float:
    """Measure video clip duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            return float(proc.stdout.strip())
        except ValueError:
            pass
    return 0.0


def _choose_action_offset(clip_dur: float, cut_dur: float, pass_num: int) -> float:
    """Seek past intro title cards/logos (first 25-35%) so every cut starts on real footage."""
    if clip_dur <= cut_dur + 1.0:
        return 0.0
    max_start = max(0.0, clip_dur - cut_dur - 0.3)
    if clip_dur >= 14.0:
        # Skip NASA/Wikimedia 3-8s text intros and land directly on action footage
        base = max(5.0, clip_dur * 0.35)
    elif clip_dur >= 6.0:
        base = clip_dur * 0.28
    else:
        base = 0.5
    return min(base + (pass_num * (cut_dur + 1.0)), max_start)


def _detect_video_encoder() -> tuple[str, list[str]]:
    """Use NVIDIA NVENC GPU encoder when available (e.g. Google Colab), else CPU libx264."""
    try:
        gpu_check = subprocess.run(["nvidia-smi"], capture_output=True, text=True)
        if gpu_check.returncode == 0:
            enc_check = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True
            )
            if "h264_nvenc" in enc_check.stdout:
                logger.info("NVIDIA GPU detected: using h264_nvenc hardware acceleration")
                return "h264_nvenc", ["-preset", "p4", "-cq", "22"]
    except FileNotFoundError:
        pass
    return "libx264", ["-preset", "veryfast", "-crf", "22"]


class VideoEditor:
    """Assembles 2-3 second real video cuts into a 1080x1920 YouTube Short."""

    def render_short(
        self,
        raw_clips: list[Path],
        tts_result: TTSResult,
        work_dir: Path,
        output_path: Path,
    ) -> Path:
        """Trim each clip to ~2-3s, concat, burn captions, and mux voiceover."""
        if not raw_clips:
            raise ValueError("No raw clips provided to render_short")

        vcodec, vcodec_flags = _detect_video_encoder()

        # Calculate target clip duration (~2.0 to 3.0 seconds per cut)
        # Add +0.4s video buffer beyond TTS duration so the final spoken sentence is never cut off
        total_dur = tts_result.duration_sec + 0.4
        num_clips = len(raw_clips)
        per_clip_dur = max(2.0, min(3.0, total_dur / num_clips))

        # Probe clip durations so we can skip intro title cards and seek into live action
        clip_durations = {clip: _probe_video_duration(clip) for clip in raw_clips}

        # If total_dur exceeds num_clips * 3.0s, cycle clips to keep every cut 2-3s
        cut_plan: list[tuple[Path, float, float]] = []
        elapsed = 0.0
        idx = 0
        while elapsed < total_dur:
            clip = raw_clips[idx % num_clips]
            pass_num = idx // num_clips
            dur = min(per_clip_dur, total_dur - elapsed)
            start_offset = _choose_action_offset(clip_durations.get(clip, 0.0), dur, pass_num)
            if dur < 1.2 and cut_plan:
                # Absorb tiny remainder into previous cut
                prev_clip, prev_off, prev_dur = cut_plan[-1]
                cut_plan[-1] = (prev_clip, prev_off, prev_dur + dur)
                break
            cut_plan.append((clip, start_offset, dur))
            elapsed += dur
            idx += 1

        # 1. Normalize & trim each 2-3s cut individually (low RAM usage!)
        trimmed_dir = work_dir / "trimmed"
        trimmed_dir.mkdir(parents=True, exist_ok=True)
        concat_list_path = work_dir / "concat.txt"
        concat_lines: list[str] = []

        vf_scale = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,fps=30,format=yuv420p"
        )

        for i, (clip_path, start_offset, dur) in enumerate(cut_plan):
            seg_path = trimmed_dir / f"seg_{i:03d}.mp4"
            try:
                _run_ffmpeg(
                    [
                        "-ss",
                        f"{start_offset:.2f}",
                        "-t",
                        f"{dur:.2f}",
                        "-i",
                        str(clip_path),
                        "-an",
                        "-vf",
                        vf_scale,
                        "-c:v",
                        vcodec,
                        *vcodec_flags,
                        str(seg_path),
                    ]
                )
            except RuntimeError:
                # Retry from 0.0s in case clip is shorter than start_offset
                _run_ffmpeg(
                    [
                        "-stream_loop",
                        "-1",
                        "-t",
                        f"{dur:.2f}",
                        "-i",
                        str(clip_path),
                        "-an",
                        "-vf",
                        vf_scale,
                        "-c:v",
                        vcodec,
                        *vcodec_flags,
                        str(seg_path),
                    ]
                )
            concat_lines.append(f"file '{seg_path.as_posix()}'\n")

        concat_list_path.write_text("".join(concat_lines), encoding="utf-8")

        # 2. Concatenate segments
        stitched_path = work_dir / "stitched.mp4"
        _run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list_path),
                "-c",
                "copy",
                str(stitched_path),
            ]
        )

        # 3. Burn in dynamic Shorts subtitles + mux voiceover audio
        ass_path = build_ass_subtitles(tts_result.words, work_dir / "captions.ass")
        ass_escaped = ass_path.as_posix().replace(":", "\\:")

        _run_ffmpeg(
            [
                "-i",
                str(stitched_path),
                "-i",
                str(tts_result.audio_path),
                "-vf",
                f"ass='{ass_escaped}'",
                "-c:v",
                vcodec,
                *vcodec_flags,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )

        logger.info(
            "Rendered Short (%d cuts, %.2fs) -> %s",
            len(cut_plan),
            total_dur,
            output_path.name,
        )
        return output_path
