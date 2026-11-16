"""ffprobe helpers."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ffprobe_bin


@dataclass
class MediaInfo:
    duration: float
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool
    vcodec: str | None = None


def probe(path: str | Path) -> MediaInfo:
    cmd = [
        ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {res.stderr.strip()[-500:]}")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    # Cover-art images show up as video streams with attached_pic; ignore them.
    video = [
        s for s in streams
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = data.get("format", {}).get("duration")
    if duration is None and video:
        duration = video[0].get("duration")
    return MediaInfo(
        duration=float(duration or 0.0),
        width=int(video[0]["width"]) if video else None,
        height=int(video[0]["height"]) if video else None,
        has_video=bool(video),
        has_audio=bool(audio),
        vcodec=video[0].get("codec_name") if video else None,
    )
