"""Built-in transition sounds, synthesized with ffmpeg (original, royalty-free).

They're written to assets/sfx/ on startup if missing, so they're always available.
Regenerate with:  python cli.py make-sfx --force
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import SFX_DIR, ffmpeg_bin

RATE = 44100
PEAK_DB = -1.0


def _buzz(freq: float, start: float, length: float, release: float = 0.05) -> str:
    """One vibration-motor burst: a buzzy square-ish tone with motor wobble and a soft envelope."""
    t = f"(t-{start})"
    env = (f"between(t,{start},{start + length})"
           f"*min(1,{t}/0.006)*min(1,({start + length}-t)/{release})")
    tone = (f"(0.55*sgn(sin(2*PI*{freq}*{t}))+0.35*sin(2*PI*{freq * 2}*{t})+0.2*sin(2*PI*{freq * 3}*{t}))"
            f"*(0.8+0.2*sin(2*PI*31*{t}))")
    return f"{env}*{tone}"


# name -> (aevalsrc expression, duration, extra filters)
SOUNDS: dict[str, tuple[str, float, str]] = {
    # Phone-on-the-table buzz; lines up with a 0.25-0.4s transition.
    "vibrate.wav": (_buzz(170, 0.0, 0.32), 0.36, "highpass=f=90,lowpass=f=2400"),
    # Two short pulses, like a notification.
    "vibrate_double.wav": (
        f"{_buzz(175, 0.0, 0.12, 0.03)}+{_buzz(175, 0.19, 0.14, 0.04)}", 0.36,
        "highpass=f=90,lowpass=f=2400"),
    # Heavier, lower buzz with a sub thump for big reveals (#1).
    "vibrate_heavy.wav": (
        f"{_buzz(110, 0.0, 0.42, 0.1)}+0.9*sin(2*PI*55*t)*exp(-t*14)", 0.48,
        "highpass=f=40,lowpass=f=1800"),
}


def generate(name: str, out_dir: Path = SFX_DIR, force: bool = False) -> Path:
    expr, dur, filters = SOUNDS[name]
    out = out_dir / name
    if out.exists() and not force:
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    src = ["-f", "lavfi", "-i", f"aevalsrc='{expr}':s={RATE}:d={dur}"]
    chain = f"{filters},afade=t=out:st={dur - 0.02:.3f}:d=0.02"
    # Pass 1: measure the peak. (Loudness meters need >= 0.4s, so level by peak.)
    probe = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostdin", *src, "-af", f"{chain},volumedetect", "-f", "null", "-"],
        check=True, capture_output=True, text=True).stderr
    peak = float(re.search(r"max_volume:\s*(-?[\d.]+) dB", probe).group(1))
    # Pass 2: write it peaking at PEAK_DB.
    cmd = [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", *src,
        "-af", f"{chain},volume={PEAK_DB - peak:.2f}dB", "-ac", "2", "-c:a", "pcm_s16le", str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def ensure_default_sfx(out_dir: Path = SFX_DIR, force: bool = False) -> list[Path]:
    paths = []
    for name in SOUNDS:
        try:
            paths.append(generate(name, out_dir, force))
        except (subprocess.CalledProcessError, RuntimeError):
            pass  # never block startup over a sound effect
    return paths


# --------------------------------------------------------------------------- user sounds
# RankReel starts a transition sound exactly on the cut, the same frame the visual
# transition (flash/zoom/shake) begins. Real-world files often start with silence
# (and MP3s add encoder padding), which would make the hit land late. prepare_sound()
# trims that so the attack is at 0s, removes trailing silence, de-clicks both ends and
# levels the sound to match the built-in ones.

DEFAULT_SFX = "core_sound_effect.wav"  # the default transition sound for new projects
TARGET_RMS_DB = -12.0  # loudness of the active part, close to the vibration sounds
_PRE_ROLL = 0.004  # keep a few ms before the attack so the transient stays crisp


def _pcm(path: Path, rate: int = RATE):
    import array

    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) // 2 * 2])
    return pcm


def sound_bounds(path: Path, threshold_db: float = -40.0) -> tuple[float, float, float, float]:
    """(onset, end, active_rms_db, peak_db) of a sound, relative to its own peak level."""
    import math

    pcm = _pcm(path)
    if not pcm:
        raise ValueError("empty audio")
    win = RATE // 200  # 5 ms windows
    rms = []
    for i in range(0, len(pcm), win):
        c = pcm[i:i + win]
        rms.append(math.sqrt(sum(x * x for x in c) / len(c)))
    loudest = max(rms)
    if loudest <= 0:
        raise ValueError("the sound is silent")
    gate = loudest * 10 ** (threshold_db / 20)
    active = [k for k, v in enumerate(rms) if v >= gate]
    onset, end = active[0] * win / RATE, min(len(pcm), (active[-1] + 1) * win) / RATE
    seg = pcm[int(onset * RATE):int(end * RATE)]
    act_rms = math.sqrt(sum(x * x for x in seg) / len(seg)) / 32768
    peak = max(abs(x) for x in pcm) / 32768
    return onset, end, 20 * math.log10(act_rms), 20 * math.log10(peak)


def prepare_sound(src: Path, dst: Path) -> float:
    """Write a cut-ready WAV of ``src`` to ``dst``; returns its duration (s)."""
    onset, end, rms_db, peak_db = sound_bounds(src)
    start = max(0.0, onset - _PRE_ROLL)
    dur = end - start
    gain = min(TARGET_RMS_DB - rms_db, PEAK_DB - peak_db)  # never clip
    fade_out = min(0.05, dur / 4)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Trim by sample count on the decoded audio (the same reference sound_bounds measured);
    # time-based seeking would be thrown off by MP3/AAC encoder delay.
    first, last = int(start * RATE), int(end * RATE)
    # Mono -> stereo by duplication: ffmpeg's default upmix would lower it by 3 dB.
    info = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(src)], capture_output=True, text=True).stderr
    upmix = "pan=stereo|c0=c0|c1=c0," if re.search(r"Audio:.*\bmono\b", info) else ""
    subprocess.run([
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
        "-af", f"{upmix}aresample={RATE},atrim=start_sample={first}:end_sample={last},asetpts=N/SR/TB,"
               f"afade=t=in:d=0.003,afade=t=out:st={dur - fade_out:.4f}:d={fade_out:.4f},"
               f"volume={gain:.2f}dB",
        "-ac", "2", "-c:a", "pcm_s16le", str(dst),
    ], check=True, capture_output=True)
    return dur
