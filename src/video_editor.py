"""FFmpeg-based 9:16 fast-paced 2-3 second cut video editor with burned-in captions."""

import array
import logging
import math
import subprocess
import wave
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


def _generate_ambient_bgm(duration_sec: float, wav_path: Path) -> Path:
    """Synthesize a rich, clearly audible, copyright-free modern science Shorts electronic beat."""
    import random

    sample_rate = 22050
    total_samples = int(duration_sec * sample_rate)
    # Modern upbeat science chord progression (Hz): Am -> F -> C -> G
    chords = [
        (110.0, 220.0, 261.63, 329.63),  # Am
        (87.31, 174.61, 220.0, 261.63),  # F
        (130.81, 196.0, 261.63, 329.63), # C
        (98.0, 196.0, 246.94, 293.66),   # G
    ]
    arpeggio_notes = [
        (440.0, 523.25, 659.25, 523.25, 440.0, 659.25, 523.25, 392.0),
        (349.23, 440.0, 523.25, 440.0, 349.23, 523.25, 440.0, 329.63),
        (392.0, 523.25, 659.25, 783.99, 659.25, 523.25, 392.0, 523.25),
        (392.0, 493.88, 587.33, 493.88, 392.0, 587.33, 493.88, 392.0),
    ]
    chord_dur = 4.0
    beat_dur = 0.5   # 120 BPM quarter-note kick/pulse
    step_dur = 0.25  # eighth-note melody/hi-hat
    fade_in = int(0.35 * sample_rate)
    fade_out = int(0.9 * sample_rate)
    buf = array.array("h")
    two_pi = 2.0 * math.pi
    rng = random.Random(42)

    for i in range(total_samples):
        t = i / sample_rate
        chord_idx = int(t // chord_dur) % len(chords)
        bass_f, f1, f2, f3 = chords[chord_idx]
        arp_freqs = arpeggio_notes[chord_idx]

        # 1. Warm harmonic synth pad (fundamental + gentle 2nd harmonic for rich speaker presence)
        local_t = t % chord_dur
        chord_env = min(1.0, local_t / 0.18) * min(1.0, (chord_dur - local_t) / 0.18)
        pad = (
            math.sin(two_pi * f1 * t) + 0.25 * math.sin(two_pi * f1 * 2 * t)
            + math.sin(two_pi * f2 * t) + 0.20 * math.sin(two_pi * f2 * 2 * t)
            + math.sin(two_pi * f3 * t)
        ) / 3.45

        # 2. Soft modern electronic kick thump (pitch-swept sine on every beat)
        beat_t = t % beat_dur
        kick_freq = 52.0 + 95.0 * math.exp(-32.0 * beat_t)
        kick = math.sin(two_pi * kick_freq * beat_t) * math.exp(-12.0 * beat_t) * 0.55

        # 3. Warm rhythmic bassline + crisp plucked melody + subtle off-beat hi-hat
        step_t = t % step_dur
        step_idx = int(t / step_dur) % len(arp_freqs)
        bass = math.sin(two_pi * bass_f * t) * (0.4 + 0.6 * math.exp(-5.0 * beat_t)) * 0.42
        arp_f = arp_freqs[step_idx]
        arp = (
            math.sin(two_pi * arp_f * t) + 0.3 * math.sin(two_pi * arp_f * 2 * t)
        ) * math.exp(-9.0 * step_t) * 0.34
        hihat = (rng.uniform(-1.0, 1.0) * math.exp(-65.0 * step_t) * 0.14) if (step_idx % 2 == 1) else 0.0

        val = (0.42 * pad * chord_env) + kick + bass + arp + hihat

        if i < fade_in:
            val *= i / fade_in
        elif i > total_samples - fade_out:
            val *= max(0.0, (total_samples - i) / fade_out)

        # Soft saturation for fuller perceived loudness on mobile speakers
        val = math.tanh(val * 1.15)
        sample = int(max(-1.0, min(1.0, val * 0.92)) * 32767)
        buf.append(sample)

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(buf.tobytes())
    return wav_path


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

        # Cinematic color pop + sharpness + 1080x1920 framing so every clip looks crisp & vibrant
        vf_scale = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,eq=contrast=1.08:saturation=1.24:brightness=0.02,"
            "unsharp=5:5:0.75:3:3:0.35,fps=30,format=yuv420p"
        )

        for i, (clip_path, start_offset, dur) in enumerate(cut_plan):
            seg_path = trimmed_dir / f"seg_{i:03d}.mp4"
            is_image = clip_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            if is_image:
                # Smooth Ken Burns zoom-in animation for high-res real photos
                frames = max(30, int(round(dur * 30)))
                vf_img = (
                    "scale=1280:2276:force_original_aspect_ratio=increase,"
                    "crop=1280:2276,"
                    f"zoompan=z='min(zoom+0.0012,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30,"
                    "eq=contrast=1.08:saturation=1.22:brightness=0.02,format=yuv420p"
                )
                _run_ffmpeg(
                    [
                        "-loop",
                        "1",
                        "-t",
                        f"{dur:.2f}",
                        "-i",
                        str(clip_path),
                        "-an",
                        "-vf",
                        vf_img,
                        "-c:v",
                        vcodec,
                        *vcodec_flags,
                        str(seg_path),
                    ]
                )
            else:
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

        # 3. Burn in dynamic Shorts subtitles + mix voiceover with copyright-free ambient BGM
        ass_path = build_ass_subtitles(tts_result.words, work_dir / "captions.ass")
        ass_escaped = ass_path.as_posix().replace(":", "\\:")
        bgm_path = _generate_ambient_bgm(total_dur, work_dir / "bgm.wav")

        _run_ffmpeg(
            [
                "-i",
                str(stitched_path),
                "-i",
                str(tts_result.audio_path),
                "-i",
                str(bgm_path),
                "-vf",
                f"ass='{ass_escaped}'",
                "-filter_complex",
                "[1:a]volume=1.05[vo];[2:a]volume=0.38[bgm];[vo][bgm]amix=inputs=2:duration=first:normalize=0:dropout_transition=2,alimiter=limit=0.95[aout]",
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
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
