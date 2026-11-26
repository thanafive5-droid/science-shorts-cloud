"""FFmpeg-based 9:16 fast-paced 1.0-2.5 second cut video editor with burned-in captions."""

import array
import logging
import math
import random
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
    """Group words into character-bounded kinetic highlight captions with 4 rotated themes."""
    accent_color, margin_v = random.SystemRandom().choice([
        ("&H0000FFFF", 400),  # Bright Yellow highlight
        ("&H00FFFF00", 435),  # Electric Cyan highlight
        ("&H0066FF66", 390),  # Vibrant Lime/Mint highlight
        ("&H0000AAFF", 420),  # Warm Gold/Orange highlight
    ])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,100,130,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    current_len = 0
    for w in words:
        w_len = len(w.text)
        next_len = current_len + (1 if current else 0) + w_len
        if current and (len(current) >= 3 or next_len > 16):
            groups.append(current)
            current = [w]
            current_len = w_len
        else:
            current.append(w)
            current_len = next_len
    if current:
        groups.append(current)

    for g_idx, group in enumerate(groups):
        group_end = group[-1].end_sec + 0.08
        if g_idx + 1 < len(groups):
            group_end = min(group_end, groups[g_idx + 1][0].start_sec)
        tokens = [w.text.upper().replace("{", "").replace("}", "") for w in group]
        for w_idx, w in enumerate(group):
            w_start = w.start_sec
            w_end = group[w_idx + 1].start_sec if w_idx + 1 < len(group) else group_end
            if w_end <= w_start:
                w_end = w_start + 0.08
            rendered = []
            for k, tok in enumerate(tokens):
                if k == w_idx:
                    rendered.append(rf"{{\c{accent_color}&\fscx108\fscy108}}{tok}{{\r}}")
                else:
                    rendered.append(tok)
            lines.append(
                f"Dialogue: 0,{_format_ass_time(w_start)},{_format_ass_time(w_end)},Default,,0,0,0,,{' '.join(rendered)}\n"
            )

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


def _generate_ambient_bgm(
    duration_sec: float, wav_path: Path, mood: str = "upbeat_tech"
) -> Path:
    """Synthesize distinct topic-matched copyright-free BGM across 6 musical genres, keys, and tempos."""
    import random

    sys_rng = random.SystemRandom()

    # Scale frequencies cleanly across random musical keys (-3 to +5 semitones) so every clip is unique
    semitones = sys_rng.choice([-3, -2, 0, 2, 3, 5])
    pitch_scale = 2.0 ** (semitones / 12.0)

    # Distinct chord banks & melody motifs per genre so every video fits its topic & feels fresh
    genre_bank = {
        "cosmic_ambient": {
            "bpm_range": (114.0, 122.0),
            "chords": [
                [(65.41, 196.0, 246.94, 293.66), (55.00, 164.81, 220.0, 261.63), (73.42, 220.0, 261.63, 329.63), (61.74, 196.0, 246.94, 329.63)],
                [(55.00, 164.81, 220.0, 261.63), (65.41, 196.0, 246.94, 329.63), (58.27, 174.61, 220.0, 293.66), (49.00, 146.83, 196.0, 246.94)],
                [(73.42, 220.0, 277.18, 329.63), (55.00, 164.81, 220.0, 277.18), (61.74, 185.00, 246.94, 293.66), (65.41, 196.0, 246.94, 329.63)],
            ],
            "melodies": [
                (523.25, 0.0, 659.25, 587.33, 0.0, 783.99, 659.25, 0.0),
                (440.00, 523.25, 0.0, 659.25, 523.25, 0.0, 392.00, 0.0),
                (587.33, 0.0, 739.99, 0.0, 659.25, 587.33, 0.0, 880.0),
            ],
        },
        "cinematic_tension": {
            "bpm_range": (124.0, 134.0),
            "chords": [
                [(73.42, 146.83, 174.61, 220.0), (58.27, 116.54, 146.83, 174.61), (65.41, 130.81, 164.81, 196.0), (69.30, 138.59, 174.61, 220.0)],
                [(55.00, 110.0, 130.81, 164.81), (58.27, 116.54, 146.83, 174.61), (65.41, 130.81, 155.56, 196.0), (51.91, 110.0, 138.59, 164.81)],
                [(65.41, 130.81, 155.56, 196.0), (61.74, 123.47, 146.83, 185.0), (51.91, 103.83, 130.81, 155.56), (49.00, 98.00, 123.47, 146.83)],
            ],
            "melodies": [
                (293.66, 293.66, 349.23, 293.66, 440.0, 349.23, 329.63, 293.66),
                (261.63, 311.13, 293.66, 261.63, 392.0, 311.13, 293.66, 246.94),
                (220.00, 220.00, 261.63, 293.66, 261.63, 246.94, 220.00, 196.0),
            ],
        },
        "upbeat_tech": {
            "bpm_range": (126.0, 134.0),
            "chords": [
                [(110.0, 220.0, 261.63, 329.63), (87.31, 174.61, 220.0, 261.63), (130.81, 196.0, 261.63, 329.63), (98.0, 196.0, 246.94, 293.66)],
                [(130.81, 261.63, 329.63, 392.0), (98.0, 196.0, 246.94, 293.66), (110.0, 220.0, 261.63, 329.63), (87.31, 174.61, 220.0, 261.63)],
                [(146.83, 293.66, 369.99, 440.0), (110.0, 220.0, 277.18, 329.63), (123.47, 246.94, 293.66, 369.99), (98.0, 196.0, 246.94, 293.66)],
            ],
            "melodies": [
                (523.25, 659.25, 783.99, 659.25, 587.33, 659.25, 783.99, 523.25),
                (440.00, 523.25, 659.25, 523.25, 587.33, 659.25, 880.00, 659.25),
                (587.33, 739.99, 880.00, 739.99, 659.25, 739.99, 587.33, 440.0),
            ],
        },
        "nature_organic": {
            "bpm_range": (120.0, 128.0),
            "chords": [
                [(130.81, 261.63, 329.63, 392.0), (98.0, 196.0, 246.94, 293.66), (110.0, 220.0, 261.63, 329.63), (87.31, 174.61, 220.0, 261.63)],
                [(87.31, 174.61, 220.0, 261.63), (130.81, 261.63, 329.63, 392.0), (146.83, 293.66, 349.23, 440.0), (98.0, 196.0, 246.94, 293.66)],
                [(98.0, 196.0, 246.94, 293.66), (146.83, 293.66, 369.99, 440.0), (164.81, 246.94, 329.63, 392.0), (130.81, 261.63, 329.63, 392.0)],
            ],
            "melodies": [
                (523.25, 587.33, 659.25, 783.99, 659.25, 587.33, 523.25, 659.25),
                (392.00, 440.00, 523.25, 659.25, 523.25, 440.00, 392.00, 440.0),
                (587.33, 659.25, 783.99, 880.00, 783.99, 659.25, 587.33, 493.88),
            ],
        },
        "energetic_drive": {
            "bpm_range": (130.0, 138.0),
            "chords": [
                [(82.41, 164.81, 196.0, 246.94), (130.81, 261.63, 329.63, 392.0), (98.0, 196.0, 246.94, 293.66), (146.83, 293.66, 369.99, 440.0)],
                [(92.50, 185.0, 220.0, 277.18), (146.83, 293.66, 369.99, 440.0), (110.0, 220.0, 277.18, 329.63), (82.41, 164.81, 207.65, 246.94)],
                [(98.0, 196.0, 233.08, 293.66), (155.56, 311.13, 392.0, 466.16), (116.54, 233.08, 293.66, 349.23), (87.31, 174.61, 220.0, 261.63)],
            ],
            "melodies": [
                (329.63, 493.88, 392.0, 493.88, 329.63, 587.33, 493.88, 392.0),
                (369.99, 554.37, 440.0, 554.37, 587.33, 739.99, 554.37, 440.0),
                (392.00, 587.33, 466.16, 587.33, 698.46, 587.33, 466.16, 392.0),
            ],
        },
        "mystery_piano": {
            "bpm_range": (116.0, 124.0),
            "chords": [
                [(130.81, 261.63, 311.13, 392.0), (103.83, 207.65, 261.63, 311.13), (116.54, 233.08, 293.66, 349.23), (98.0, 196.0, 246.94, 311.13)],
                [(110.0, 220.0, 261.63, 329.63), (87.31, 174.61, 220.0, 261.63), (82.41, 164.81, 207.65, 246.94), (98.0, 196.0, 246.94, 293.66)],
                [(123.47, 246.94, 293.66, 369.99), (98.0, 196.0, 246.94, 293.66), (92.50, 185.0, 220.0, 277.18), (82.41, 164.81, 220.0, 246.94)],
            ],
            "melodies": [
                (523.25, 622.25, 783.99, 622.25, 698.46, 783.99, 622.25, 466.16),
                (440.00, 523.25, 659.25, 523.25, 587.33, 659.25, 493.88, 392.0),
                (493.88, 587.33, 739.99, 587.33, 659.25, 739.99, 554.37, 440.0),
            ],
        },
    }

    active_mood = mood if mood in genre_bank else "upbeat_tech"
    cfg = genre_bank[active_mood]
    bpm = round(sys_rng.uniform(*cfg["bpm_range"]))
    raw_chords = sys_rng.choice(cfg["chords"])
    raw_melody = sys_rng.choice(cfg["melodies"])
    chords = [tuple(f * pitch_scale for f in chord) for chord in raw_chords]
    melody = tuple(f * pitch_scale if f > 0 else 0.0 for f in raw_melody)

    sample_rate = 24000
    total_samples = int(duration_sec * sample_rate)
    beat_dur = 60.0 / bpm
    step_dur = beat_dur / 2.0
    substep_dur = beat_dur / 4.0
    chord_dur = beat_dur * 8.0
    fade_in = int(0.30 * sample_rate)
    fade_out = int(0.80 * sample_rate)
    buf = array.array("h")
    two_pi = 2.0 * math.pi
    rng = random.Random(sys_rng.randrange(1, 1_000_000))

    logger.info("Synthesized BGM: mood=%s, bpm=%.0f, key=%+d st (%.1fs)", active_mood, bpm, semitones, duration_sec)

    for i in range(total_samples):
        t = i / sample_rate
        chord_idx = int(t // chord_dur) % len(chords)
        bass_f, f1, f2, f3 = chords[chord_idx]

        local_t = t % chord_dur
        chord_env = min(1.0, local_t / 0.16) * min(1.0, (chord_dur - local_t) / 0.16)

        beat_num = int(t // beat_dur)
        beat_t = t % beat_dur
        step_t = t % step_dur
        step_idx = int(t // step_dur) % len(melody)
        sub_t = t % substep_dur
        mel_f = melody[step_idx]

        if active_mood == "cosmic_ambient":
            # Lush detuned space pad + glassy FM bells + soft deep sub-pulse (no hard kick)
            lfo = 0.78 + 0.22 * math.sin(two_pi * 0.45 * t)
            pad = (
                math.sin(two_pi * f1 * t) + 0.55 * math.sin(two_pi * (f1 * 1.004) * t)
                + math.sin(two_pi * f2 * t) + 0.45 * math.sin(two_pi * f3 * t)
            ) / 3.0
            bass = math.sin(two_pi * bass_f * t) * 0.34
            bell = (
                math.sin(two_pi * mel_f * t + 1.1 * math.sin(two_pi * (mel_f * 2.76) * t))
                * math.exp(-4.8 * step_t) * 0.22
            ) if mel_f > 0 else 0.0
            pulse = math.sin(two_pi * (48.0 + 30.0 * math.exp(-16.0 * beat_t)) * beat_t) * math.exp(-7.0 * beat_t) * 0.22
            bed = (0.44 * pad * chord_env * lfo) + bass + bell + pulse

        elif active_mood == "cinematic_tension":
            # Bowed string/cello texture + dramatic double heartbeat thump + clockwork suspense tick
            cello = (
                math.sin(two_pi * f1 * t) + 0.45 * math.sin(two_pi * f1 * 2 * t) + 0.22 * math.sin(two_pi * f1 * 3 * t)
                + 0.75 * math.sin(two_pi * f2 * t) + 0.55 * math.sin(two_pi * f3 * t)
            ) / 2.97
            hb1 = math.exp(-14.0 * beat_t)
            hb2 = math.exp(-18.0 * ((beat_t - 0.16) if beat_t >= 0.16 else 9.0))
            heartbeat = math.sin(two_pi * 55.0 * beat_t) * (hb1 * 0.36 + hb2 * 0.24)
            suspense_tick = (
                (rng.uniform(-1.0, 1.0) * 0.12 + math.sin(two_pi * 1850.0 * sub_t) * 0.08)
                * math.exp(-85.0 * sub_t)
            )
            ostinato = (math.sin(two_pi * mel_f * t) + 0.3 * math.sin(two_pi * mel_f * 2 * t)) * math.exp(-9.0 * step_t) * 0.24
            bass = math.sin(two_pi * bass_f * t) * 0.32
            bed = (0.34 * cello * chord_env) + heartbeat + suspense_tick + ostinato + bass

        elif active_mood == "upbeat_tech":
            # Woody marimba/kalimba plucks + bouncy bass + crisp shaker/rimshot
            pad = (math.sin(two_pi * f1 * t) + 0.8 * math.sin(two_pi * f2 * t) + 0.6 * math.sin(two_pi * f3 * t)) / 2.4
            marimba = (
                math.sin(two_pi * mel_f * t) + 0.35 * math.sin(two_pi * mel_f * 4 * t) * math.exp(-42.0 * step_t)
            ) * math.exp(-12.5 * step_t) * 0.34
            kick = math.sin(two_pi * (55.0 + 90.0 * math.exp(-32.0 * beat_t)) * beat_t) * math.exp(-14.0 * beat_t) * 0.38
            rim = (math.sin(two_pi * 780.0 * beat_t) * math.exp(-60.0 * beat_t) * 0.16) if (beat_num % 2 == 1) else 0.0
            shaker = (rng.uniform(-1.0, 1.0) * math.exp(-65.0 * step_t) * 0.10) if (step_idx % 2 == 1) else 0.0
            bass = math.sin(two_pi * bass_f * t) * (0.35 + 0.65 * math.exp(-7.0 * step_t)) * 0.36
            bed = (0.28 * pad * chord_env) + marimba + kick + rim + shaker + bass

        elif active_mood == "nature_organic":
            # Warm acoustic harp plucks + airy flute-like melody shimmer + soft hand drum
            pad = (math.sin(two_pi * f1 * t) + 0.9 * math.sin(two_pi * f2 * t) + 0.8 * math.sin(two_pi * f3 * t)) / 2.7
            arp_notes = (f1, f2, f3, f2)
            harp_f = arp_notes[step_idx % 4]
            harp = (
                math.sin(two_pi * harp_f * t)
                + 0.40 * math.sin(two_pi * harp_f * 2 * t) * math.exp(-9.0 * step_t)
                + 0.18 * math.sin(two_pi * harp_f * 3 * t) * math.exp(-15.0 * step_t)
            ) * math.exp(-6.5 * step_t) * 0.30
            flute_vib = 1.0 + 0.004 * math.sin(two_pi * 5.2 * t)
            flute = math.sin(two_pi * (mel_f * flute_vib) * t) * math.exp(-5.0 * step_t) * 0.16
            hand_drum = math.sin(two_pi * (82.0 + 45.0 * math.exp(-24.0 * beat_t)) * beat_t) * math.exp(-11.0 * beat_t) * 0.28
            bass = math.sin(two_pi * bass_f * t) * 0.30
            bed = (0.30 * pad * chord_env) + harp + flute + hand_drum + bass

        elif active_mood == "energetic_drive":
            # Driving 16th-note synthwave bass + offbeat chord stabs + crisp kick/snare/hihat
            offbeat_env = math.exp(-10.0 * ((beat_t - step_dur) if beat_t >= step_dur else 9.0))
            stab = (
                math.sin(two_pi * f1 * t) + 0.4 * math.sin(two_pi * f1 * 2 * t)
                + math.sin(two_pi * f2 * t) + math.sin(two_pi * f3 * t)
            ) / 3.4 * offbeat_env * 0.30
            bass16 = (math.sin(two_pi * bass_f * t) + 0.35 * math.sin(two_pi * bass_f * 2 * t)) * math.exp(-13.0 * sub_t) * 0.40
            lead = (math.sin(two_pi * mel_f * t) + 0.3 * math.sin(two_pi * mel_f * 2 * t)) * math.exp(-11.0 * step_t) * 0.26
            kick = math.sin(two_pi * (54.0 + 98.0 * math.exp(-34.0 * beat_t)) * beat_t) * math.exp(-13.0 * beat_t) * 0.42
            clap = (rng.uniform(-1.0, 1.0) * math.exp(-36.0 * beat_t) * 0.18) if (beat_num % 2 == 1) else 0.0
            hihat = (rng.uniform(-1.0, 1.0) * math.exp(-78.0 * step_t) * 0.12) if (step_idx % 2 == 1) else 0.0
            bed = stab + bass16 + lead + kick + clap + hihat

        else:  # mystery_piano
            # Warm electric/felt piano chords + delicate high celesta/music box + gentle pulse
            chorus_pad = (
                math.sin(two_pi * f1 * t) + 0.35 * math.sin(two_pi * (f1 * 1.003) * t)
                + math.sin(two_pi * f2 * t) + math.sin(two_pi * f3 * t)
            ) / 3.35
            chord_strike = math.exp(-4.5 * (t % (beat_dur * 2.0)))
            celesta = (
                math.sin(two_pi * mel_f * t) + 0.25 * math.sin(two_pi * mel_f * 3 * t) * math.exp(-28.0 * step_t)
            ) * math.exp(-8.5 * step_t) * 0.24
            bass = math.sin(two_pi * bass_f * t) * (0.45 + 0.55 * math.exp(-4.0 * beat_t)) * 0.34
            tick = (rng.uniform(-1.0, 1.0) * math.exp(-90.0 * beat_t) * 0.08)
            bed = (0.36 * chorus_pad * (0.35 + 0.65 * chord_strike) * chord_env) + celesta + bass + tick

        if i < fade_in:
            bed *= i / fade_in
        elif i > total_samples - fade_out:
            bed *= max(0.0, (total_samples - i) / fade_out)

        hook_sfx = 0.0
        if t < 0.24:
            # Subtle Frame-1 attention swell (cleaner and less boomy so Beat 1 narration is crystal clear)
            hook_env = math.exp(-12.0 * t)
            hook_freq = 50.0 + 110.0 * math.exp(-26.0 * t)
            hook_sfx = (
                math.sin(two_pi * hook_freq * t) * hook_env * 0.45
                + rng.uniform(-1.0, 1.0) * math.exp(-20.0 * t) * 0.12
            )
        val = bed + hook_sfx

        val = math.tanh(val * 1.05)
        sample = int(max(-1.0, min(1.0, val * 0.88)) * 32767)
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


def _build_cut_durations(
    total_dur: float,
    num_clips: int,
    cut_min_sec: float = 1.0,
    cut_max_sec: float = 2.5,
) -> list[float]:
    """Generate varied cut durations summing to total_dur, strictly clamped to [cut_min_sec, cut_max_sec] when feasible."""
    if num_clips <= 0:
        raise ValueError("num_clips must be positive")
    if num_clips == 1:
        return [round(total_dur, 3)]

    avg = total_dur / num_clips
    # If total_dur falls outside [min*N, max*N], use even proportional pacing safely above 0.8s
    if avg < cut_min_sec or avg > cut_max_sec:
        durs = [round(avg, 3) for _ in range(num_clips - 1)]
        durs.append(round(max(0.8, total_dur - sum(durs)), 3))
        return durs

    # Organic fast Shorts cadence: punchy Beat 0 (~0.84x avg), varied rhythm on middle/closing beats
    rng = random.SystemRandom()
    weights = [0.84] + [rng.uniform(0.86, 1.16) for _ in range(num_clips - 1)]
    w_sum = sum(weights)
    durs = [total_dur * (w / w_sum) for w in weights]

    # Iteratively clamp into [cut_min_sec, cut_max_sec] and redistribute residual to unlocked slots
    for _ in range(16):
        durs = [min(cut_max_sec, max(cut_min_sec, d)) for d in durs]
        residual = total_dur - sum(durs)
        if abs(residual) < 1e-4:
            break
        adjustable = [
            i for i, d in enumerate(durs)
            if (residual > 0 and d < cut_max_sec - 1e-4) or (residual < 0 and d > cut_min_sec + 1e-4)
        ]
        if not adjustable:
            break
        step = residual / len(adjustable)
        for i in adjustable:
            durs[i] = min(cut_max_sec, max(cut_min_sec, durs[i] + step))

    # Final exact rounding adjustment on an interior/last slot that preserves bounds
    rounded = [round(d, 3) for d in durs]
    diff = round(total_dur - sum(rounded), 3)
    if diff != 0.0:
        for i in range(num_clips - 1, -1, -1):
            candidate = round(rounded[i] + diff, 3)
            if cut_min_sec <= candidate <= cut_max_sec:
                rounded[i] = candidate
                break
        else:
            rounded[-1] = round(max(0.8, rounded[-1] + diff), 3)
    return rounded


class VideoEditor:
    """Assembles fast-paced 1.0-2.5 second real video/photo cuts into a 1080x1920 YouTube Short."""

    def render_short(
        self,
        raw_clips: list[Path],
        tts_result: TTSResult,
        work_dir: Path,
        output_path: Path,
        cut_min_sec: float = 1.0,
        cut_max_sec: float = 2.5,
        bgm_volume: float = 0.28,
        bgm_mood: str = "upbeat_tech",
    ) -> Path:
        """Trim each clip once (zero repeats), concat, burn captions, and mux voiceover."""
        raw_clips = list(dict.fromkeys(raw_clips))
        if not raw_clips:
            raise ValueError("No raw clips provided to render_short")

        vcodec, vcodec_flags = _detect_video_encoder()

        # Add +0.25s video buffer beyond TTS duration so the ending finishes cleanly with fast loop replay
        total_dur = tts_result.duration_sec + 0.25
        max_possible_clips = max(1, int(total_dur // max(0.5, cut_min_sec)))
        if len(raw_clips) > max_possible_clips:
            raw_clips = raw_clips[:max_possible_clips]

        cut_durations = _build_cut_durations(
            total_dur=total_dur,
            num_clips=len(raw_clips),
            cut_min_sec=cut_min_sec,
            cut_max_sec=cut_max_sec,
        )

        # Probe clip durations so we can skip intro title cards and seek into live action
        clip_durations = {clip: _probe_video_duration(clip) for clip in raw_clips}

        # Strict one-pass cut plan: every unique visual appears AT MOST ONCE (zero intra-video repeats)
        cut_plan: list[tuple[Path, float, float]] = []
        for clip, dur in zip(raw_clips, cut_durations):
            start_offset = _choose_action_offset(clip_durations.get(clip, 0.0), dur, pass_num=0)
            cut_plan.append((clip, start_offset, dur))

        # 1. Normalize & trim each fast 1.0-2.5s cut individually (low RAM usage!)
        trimmed_dir = work_dir / "trimmed"
        trimmed_dir.mkdir(parents=True, exist_ok=True)
        concat_list_path = work_dir / "concat.txt"
        concat_lines: list[str] = []

        # 1 of 3 subtle natural cinematic color grades so consecutive uploads never share identical histograms
        color_eq = random.SystemRandom().choice([
            "eq=contrast=1.07:saturation=1.22:brightness=0.02",
            "eq=contrast=1.10:saturation=1.16:brightness=0.01:gamma=0.98",
            "eq=contrast=1.06:saturation=1.28:brightness=0.02:gamma=1.02",
        ])

        # Cinematic color pop + sharpness + 1080x1920 framing so every clip looks crisp & vibrant
        vf_scale = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,{color_eq},"
            "unsharp=5:5:0.75:3:3:0.35,fps=30,format=yuv420p"
        )

        for i, (clip_path, start_offset, dur) in enumerate(cut_plan):
            seg_path = trimmed_dir / f"seg_{i:03d}.mp4"
            is_image = clip_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            if is_image:
                # Snappy Ken Burns animation (single input frame into zoompan — NEVER use -loop 1!)
                frames = max(30, int(round(dur * 30)))
                zoom_expr = (
                    "min(zoom+0.0022,1.14)"
                    if i % 2 == 0
                    else "if(eq(on,1),1.14,max(zoom-0.0022,1.0))"
                )
                vf_img = (
                    "scale=1280:2276:force_original_aspect_ratio=increase,"
                    "crop=1280:2276,"
                    f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30,"
                    f"{color_eq},format=yuv420p"
                )
                _run_ffmpeg(
                    [
                        "-i",
                        str(clip_path),
                        "-an",
                        "-vf",
                        vf_img,
                        "-t",
                        f"{dur:.2f}",
                        "-r",
                        "30",
                        "-video_track_timescale",
                        "15360",
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
                            "-i",
                            str(clip_path),
                            "-an",
                            "-vf",
                            vf_scale,
                            "-t",
                            f"{dur:.2f}",
                            "-r",
                            "30",
                            "-video_track_timescale",
                            "15360",
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
                            "-i",
                            str(clip_path),
                            "-an",
                            "-vf",
                            vf_scale,
                            "-t",
                            f"{dur:.2f}",
                            "-r",
                            "30",
                            "-video_track_timescale",
                            "15360",
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
        bgm_path = _generate_ambient_bgm(total_dur, work_dir / "bgm.wav", mood=bgm_mood)

        audio_filter = (
            "[1:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
            "highpass=f=75,lowpass=f=11500,"
            "acompressor=threshold=0.1585:ratio=2.2:attack=12:release=140:makeup=1.4,"
            "volume=1.08,asplit=2[vo_mix][vo_sc];"
            f"[2:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,volume={bgm_volume:.2f}[bgm_raw];"
            "[bgm_raw][vo_sc]sidechaincompress=threshold=0.04:ratio=3.5:attack=25:release=260[bgm_ducked];"
            "[vo_mix][bgm_ducked]amix=inputs=2:duration=first:normalize=0:dropout_transition=2,"
            "alimiter=limit=0.92:attack=5:release=60[aout]"
        )

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
                audio_filter,
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
                "-map_metadata",
                "-1",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )

        avg_cut = total_dur / len(cut_plan)
        min_cut = min(d for _, _, d in cut_plan)
        max_cut = max(d for _, _, d in cut_plan)
        logger.info(
            "Rendered Short (%d cuts, %.2fs, avg %.2fs/cut, range %.2f-%.2fs) -> %s",
            len(cut_plan),
            total_dur,
            avg_cut,
            min_cut,
            max_cut,
            output_path.name,
        )
        return output_path
