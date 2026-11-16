"""Paths and external-binary resolution.

Settings come from (highest priority first): environment variables, an optional
``config.json`` in the project root, then defaults.

    RANKREEL_FFMPEG / "ffmpeg"     path to the ffmpeg binary
    RANKREEL_FFPROBE / "ffprobe"   path to the ffprobe binary
    RANKREEL_COOKIES / "cookies_file"  Netscape cookies.txt for links that need login
    RANKREEL_COOKIES_FROM_BROWSER / "cookies_from_browser"  e.g. "firefox": use that browser's login
"""

from __future__ import annotations

import json
import os
import shutil
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
ASSETS_DIR = ROOT / "assets"
SFX_DIR = ASSETS_DIR / "sfx"
BRAND_DIR = ASSETS_DIR / "brand"  # channel avatars for the channel card
CACHE_DIR = ROOT / "cache"
DOWNLOAD_DIR = CACHE_DIR / "downloads"
OVERLAY_DIR = CACHE_DIR / "overlays"
WORK_DIR = CACHE_DIR / "work"
PROJECTS_DIR = ROOT / "projects"
OUTPUT_DIR = ROOT / "output"


def ensure_dirs() -> None:
    for d in (DOWNLOAD_DIR, OVERLAY_DIR, WORK_DIR, PROJECTS_DIR, OUTPUT_DIR, SFX_DIR, BRAND_DIR):
        d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def _file_config() -> dict:
    path = ROOT / "config.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def setting(env: str, key: str) -> str | None:
    return os.environ.get(env) or _file_config().get(key) or None


def resolve_path(p: str | os.PathLike) -> Path:
    """Resolve a path relative to the project root (absolute paths pass through)."""
    p = Path(p).expanduser()
    return p if p.is_absolute() else ROOT / p


def _find_binary(name: str, env: str) -> str:
    explicit = setting(env, name)
    if explicit:
        return explicit
    found = shutil.which(name)
    if found:
        return found
    # Optional fallback: the `static-ffmpeg` pip package ships both binaries.
    try:
        import static_ffmpeg.run as sf  # type: ignore

        ffmpeg, ffprobe = sf.get_or_fetch_platform_executables_else_raise()
        return ffmpeg if name == "ffmpeg" else ffprobe
    except Exception:
        pass
    raise RuntimeError(
        f"{name} not found. Install ffmpeg (brew install ffmpeg / apt install ffmpeg / "
        f"winget install ffmpeg) or set {env} to the binary path."
    )


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    return _find_binary("ffmpeg", "RANKREEL_FFMPEG")


@lru_cache(maxsize=1)
def ffprobe_bin() -> str:
    return _find_binary("ffprobe", "RANKREEL_FFPROBE")


def cookies_file() -> str | None:
    c = setting("RANKREEL_COOKIES", "cookies_file")
    return str(resolve_path(c)) if c else None


def cookies_from_browser() -> str | None:
    """Use a browser's logged-in session (e.g. "firefox", "chrome"); Instagram often needs it."""
    b = setting("RANKREEL_COOKIES_FROM_BROWSER", "cookies_from_browser")
    return b.strip().lower() if b else None


SFX_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


# Sounds that were renamed: projects saved with the old name still find them.
SFX_ALIASES = {"core_sound_effect.mp3": "core_sound_effect.wav"}


def sfx_path(name: str) -> Path:
    """A transition sound: a bare file name lives in assets/sfx; anything with a
    slash is a path (relative to the project root)."""
    if "/" in name or "\\" in name:
        return resolve_path(name)
    if not (SFX_DIR / name).exists() and name in SFX_ALIASES:
        name = SFX_ALIASES[name]
    return SFX_DIR / name


def avatar_path(name: str) -> Path:
    """A channel avatar: a bare file name lives in assets/brand; anything with a slash
    is a path (relative to the project root)."""
    if "/" in name or "\\" in name:
        return resolve_path(name)
    return BRAND_DIR / name
