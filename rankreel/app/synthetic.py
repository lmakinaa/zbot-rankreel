"""Synthetic test clips (ffmpeg testsrc + sine) for tests and the offline demo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import ffmpeg_bin


def make_test_clip(path: Path, duration: float = 5.0, w: int = 720, h: int = 1280,
                   audio: bool = True, freq: int = 440, fps: int = 25) -> Path:
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration={duration}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={duration}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path
