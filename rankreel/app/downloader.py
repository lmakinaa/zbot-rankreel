"""yt-dlp wrapper with an on-disk cache keyed by sha1(url).

No watermark removal: the clip is downloaded as published.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import DOWNLOAD_DIR, cookies_file, cookies_from_browser, ffmpeg_bin
from .models import Clip, Project
from .probe import probe


class FetchError(RuntimeError):
    """A user-facing download failure."""


@dataclass
class ClipMeta:
    url: str
    webpage_url: str
    uploader: str | None
    creator: str | None
    title: str | None
    duration: float
    has_audio: bool
    file: str  # file name inside DOWNLOAD_DIR
    preview_file: str | None = None  # browser-friendly H.264 proxy (only when needed)

    @property
    def handle(self) -> str | None:
        h = self.uploader or self.creator
        if not h:
            return None
        return h if h.startswith("@") else f"@{h}"


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


_IG_POST = re.compile(r"^/(?:[^/]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]+)")


def normalize_url(url: str) -> str:
    """Canonical form of a video link, without tracking query strings.

    TikTok:    https://www.tiktok.com/@user/video/123?is_from_webapp=1 -> .../@user/video/123
    Instagram: instagram.com/reels/ABC/?igsh=..., /p/ABC, /user/reel/ABC -> https://www.instagram.com/reel/ABC/
    """
    url = url.strip()
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.endswith("tiktok.com") and "/video/" in parts.path:
        return urlunsplit((parts.scheme or "https", host, parts.path.rstrip("/"), "", ""))
    if host.endswith("instagram.com") or host.endswith("instagr.am"):
        m = _IG_POST.match(parts.path)
        if m:
            return f"https://www.instagram.com/reel/{m.group(1)}/"
    return url


def site_name(url: str) -> str:
    host = urlsplit(url.strip()).netloc.lower()
    if host.endswith(("instagram.com", "instagr.am")):
        return "Instagram"
    if host.endswith("tiktok.com"):
        return "TikTok"
    return "The site"


def _looks_like_handle(v) -> bool:
    return isinstance(v, str) and v.strip() != "" and not any(c.isspace() for c in v) and not v.isdigit()


def pick_handle(info: dict, url: str) -> str | None:
    """The creator's @username for credits (each site puts it in a different field)."""
    if site_name(url) == "Instagram":
        keys = ("channel", "uploader_id", "uploader")  # uploader = display name, uploader_id = number
    else:
        keys = ("uploader", "uploader_id", "channel", "creator")  # TikTok: uploader = @handle
    return next((info[k] for k in keys if _looks_like_handle(info.get(k))), None)


def cache_key(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()


def _paths(key: str, base: Path) -> tuple[Path, Path]:
    return base / f"{key}.mp4", base / f"{key}.json"


def cached_meta(url: str, base: Path = DOWNLOAD_DIR) -> ClipMeta | None:
    mp4, meta = _paths(cache_key(url), base)
    if mp4.exists() and meta.exists():
        try:
            return ClipMeta(**json.loads(meta.read_text()))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
    return None


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RESTRICTED = ("isn't available to everyone", "isn\u2019t available to everyone", "certain audiences",
               "age-restricted", "age restricted", "inappropriate for some users", "sensitive content")


def _friendly(msg: str, url: str = "") -> str:
    msg = _ANSI.sub("", msg)
    m = msg.lower()
    site = site_name(url) if url else "The site"
    if "private" in m:
        return "This video is private."
    if any(k in m for k in _RESTRICTED):
        if cookies_from_browser() or cookies_file():
            return (f"{site} restricts this video (age or audience), and your login didn't get past it. "
                    f"Make sure you're logged in to {site} in that browser, then try again.")
        return (f"{site} only shows this video to logged-in accounts (age or audience restriction). "
                f"Log in to {site} in your browser, then set cookies_from_browser in config.json "
                "(e.g. \"firefox\" or \"brave\") and restart. See the README.")
    if "log in" in m or "login" in m or "cookies" in m or "empty media response" in m:
        how = ("then set cookies_from_browser in config.json (e.g. \"firefox\" or \"chrome\"), "
               "or export cookies.txt and set cookies_file. See the README.")
        if site != "The site":
            return f"{site} wants you to be logged in for this video. Log in to {site} in your browser, {how}"
        return f"This video needs a logged-in session. Log in to the site in your browser, {how}"
    if "429" in m or "too many requests" in m or "rate limit" in m or "rate-limit" in m:
        return f"{site} is rate-limiting requests. Wait a few minutes and try again."
    if ("no video formats" in m or "requested format is not available" in m
            or "photo" in m or "slideshow" in m or "/photo/" in m):
        return "This looks like a photo/slideshow post; only video posts are supported."
    if "unsupported url" in m:
        return "Unsupported link. Paste a TikTok video or Instagram Reel link."
    if ("404" in m or "not available" in m or "removed" in m or "does not exist" in m
            or "unable to find" in m):
        return "Video not found: it may have been removed or the link is wrong."
    return "Download failed: " + msg.strip().splitlines()[-1][:300]


def fetch(url: str, base: Path = DOWNLOAD_DIR) -> ClipMeta:
    """Download (or reuse) a clip. Raises FetchError with a readable message."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise FetchError("Not a URL. Paste a full TikTok or Instagram Reel link (https://...).")
    base.mkdir(parents=True, exist_ok=True)
    key = cache_key(url)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        meta = cached_meta(url, base) or _download(url, key, base)
        return ensure_preview(meta, base)


def _download(url: str, key: str, base: Path) -> ClipMeta:
    import yt_dlp

    mp4, meta_path = _paths(key, base)
    opts = {
        "format": "mp4/bestvideo+bestaudio/best",
        "outtmpl": str(base / f"{key}.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "no_color": True,
        "ffmpeg_location": str(Path(ffmpeg_bin()).parent),
    }
    cookies = cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
    browser = cookies_from_browser()
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise FetchError(_friendly(str(e), url)) from e
    except Exception as e:  # extractor bugs, network errors, unreadable browser cookies
        raise FetchError(_friendly(str(e), url)) from e
    if info is None:
        raise FetchError("Download failed: no info returned.")
    if info.get("_type") == "playlist":
        raise FetchError("That link is a playlist, profile or multi-video post; paste a single video link.")

    if not mp4.exists():
        others = [p for p in base.glob(f"{key}.*") if p.suffix not in (".json", ".part")]
        if not others:
            raise FetchError(_friendly("no video formats found", url))
        others[0].rename(mp4)

    media = probe(mp4)
    if not media.has_video:
        mp4.unlink(missing_ok=True)
        raise FetchError(_friendly("photo slideshow", url))

    meta = ClipMeta(
        url=url,
        webpage_url=normalize_url(info.get("webpage_url") or url),
        uploader=pick_handle(info, url),
        creator=info.get("uploader") if site_name(url) == "Instagram" else (info.get("creator") or info.get("channel")),
        title=info.get("title"),
        duration=media.duration or float(info.get("duration") or 0),
        has_audio=media.has_audio,
        file=mp4.name,
    )
    meta_path.write_text(json.dumps(asdict(meta), indent=2))
    return meta


BROWSER_CODECS = {"h264"}


def ensure_preview(meta: ClipMeta, base: Path = DOWNLOAD_DIR) -> ClipMeta:
    """TikTok often serves HEVC, which many browsers can't play. Keep the original
    for rendering and make a small H.264 proxy for the in-browser preview."""
    src = base / meta.file
    if meta.preview_file and (base / meta.preview_file).exists():
        return meta
    if probe(src).vcodec in BROWSER_CODECS:
        return meta
    proxy = src.with_name(src.stem + ".preview.mp4")
    if not proxy.exists():
        tmp = proxy.with_name(proxy.stem + ".part.mp4")
        cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
               "-vf", "scale=-2:'min(960,ih)'", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
               "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(tmp)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return meta  # preview is best-effort; rendering uses the original
        tmp.replace(proxy)
    meta.preview_file = proxy.name
    (base / f"{Path(meta.file).stem}.json").write_text(json.dumps(asdict(meta), indent=2))
    return meta


def apply_meta(clip: Clip, meta: ClipMeta, base: Path = DOWNLOAD_DIR) -> None:
    clip.local_path = str(base / meta.file)
    clip.source_duration = meta.duration
    clip.creator = meta.handle
    clip.webpage_url = meta.webpage_url


def prepare_project(project: Project, progress=None, base: Path = DOWNLOAD_DIR) -> None:
    """Make sure every clip has a local file + metadata (download if needed)."""
    n = len(project.clips)
    for i, clip in enumerate(project.clips):
        if clip.local_path and Path(clip.local_path).exists():
            if clip.source_duration is None:
                clip.source_duration = probe(clip.local_path).duration
            continue
        if progress:
            progress(i / max(n, 1), f"Downloading clip {i + 1} of {n} (#{clip.rank})")
        try:
            meta = fetch(str(clip.url), base)
        except FetchError as e:
            raise FetchError(f"#{clip.rank} {clip.name}: {e}") from e
        apply_meta(clip, meta, base)


def fill_from_cache(project: Project, base: Path = DOWNLOAD_DIR, trust_local: bool = True) -> None:
    """Fill paths/durations/creators for clips already in the cache (no network).

    With ``trust_local=False`` (API requests) any client-supplied ``local_path``
    is discarded, so only files in the download cache can ever be rendered.
    """
    for clip in project.clips:
        if not trust_local:
            clip.local_path = None
        elif clip.local_path and clip.source_duration is not None:
            continue
        meta = cached_meta(str(clip.url), base)
        if meta:
            apply_meta(clip, meta, base)
