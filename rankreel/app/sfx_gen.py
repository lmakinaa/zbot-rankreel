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
