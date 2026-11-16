#!/usr/bin/env python3
"""Turn TikTok videos into material Claude can "watch": timestamped contact sheets,
a loudness timeline, shots/title cards, caption/subtitles and stats.

Inputs can be TikTok links, local video files, or `file.mp4::https://tiktok-link`
(a file you already have, plus its link for credits/rendering).

    python analyze.py URL_OR_FILE [...] --out WORKDIR
    python analyze.py --zoom WORKDIR/01_xxx 12.0 16.5 [--step 0.2]

Works in two modes, picked automatically:
  - next to RankReel (rankreel/ found): uses its download cache and ffmpeg, so a later
    render needs no re-download. Run with rankreel/.venv/bin/python.
  - standalone (e.g. Claude.ai): needs ffmpeg, Pillow and yt-dlp (for links):
        pip install yt-dlp pillow imageio-ffmpeg

Overview mode writes, per video, WORKDIR/NN_<handle>_<id>/:
    summary.md        read this first (metadata, loud moments, shots, sheet index)
    sheet_01.png ...  frames every --step seconds, each labeled with its timestamp
    audio.txt         loudness every 0.5s as a bar chart
    info.json         machine-readable metadata (url, local file, duration, ...)
Zoom mode writes zoom_<start>-<end>_NN.png: a fine-grained sheet to place cut points.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


# --------------------------------------------------------------------------- setup
def find_rankreel(explicit: str | None) -> Path:
    cands = [Path(explicit)] if explicit else []
    for base in [Path.cwd(), Path(__file__).resolve().parent]:
        for d in [base, *base.parents]:
            cands += [d / "rankreel", d]
    for c in cands:
        if (c / "app" / "downloader.py").exists():
            return c.resolve()
    if explicit:
        sys.exit(f"No RankReel app in {explicit}.")
    return None


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # pip install imageio-ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    try:
        import static_ffmpeg.run as sf  # pip install static-ffmpeg
        return sf.get_or_fetch_platform_executables_else_raise()[0]
    except Exception:
        pass
    sys.exit("ffmpeg not found. Install it (apt install ffmpeg) or: pip install imageio-ffmpeg")


@dataclass
class Media:
    duration: float
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool


def probe_media(ffmpeg: str, path: Path) -> Media:
    """Duration/size/streams from `ffmpeg -i` (no ffprobe needed)."""
    err = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    video = [ln for ln in err.splitlines() if "Video:" in ln and "attached pic" not in ln]
    size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", video[0]) if video else None
    return Media(dur, int(size.group(1)) if size else None, int(size.group(2)) if size else None,
                 bool(video), "Audio:" in err)


def fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


# --------------------------------------------------------------------------- media helpers
def extract_frames(ffmpeg: str, src: Path, start: float, end: float, step: float, width: int, tmp: Path):
    """Frames at start, start+step, ... < end. Returns [(t, path)]."""
    tmp.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
           "-vf", f"fps=1/{step},scale={width}:-2", "-q:v", "4", str(tmp / "f_%05d.jpg")]
    subprocess.run(cmd, check=True)
    files = sorted(tmp.glob("f_*.jpg"))
    return [(start + i * step, f) for i, f in enumerate(files)]


def contact_sheets(frames, out_prefix: Path, cols: int, rows: int, label_size: int) -> list[tuple[Path, float, float]]:
    from PIL import Image, ImageDraw, ImageFont

    if not frames:
        return []
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", label_size)
    except OSError:
        font = ImageFont.load_default()
    w, h = Image.open(frames[0][1]).size
    per = cols * rows
    sheets = []
    for n, i in enumerate(range(0, len(frames), per), start=1):
        chunk = frames[i:i + per]
        r = math.ceil(len(chunk) / cols)
        sheet = Image.new("RGB", (cols * w + (cols - 1) * 4, r * h + (r - 1) * 4), (40, 40, 40))
        draw = ImageDraw.Draw(sheet)
        for k, (t, f) in enumerate(chunk):
            x, y = (k % cols) * (w + 4), (k // cols) * (h + 4)
            sheet.paste(Image.open(f), (x, y))
            label = fmt(t)
            tw = draw.textlength(label, font=font)
            draw.rectangle([x, y, x + tw + 10, y + label_size + 8], fill=(0, 0, 0))
            draw.text((x + 5, y + 3), label, font=font, fill=(255, 230, 0))
        path = Path(f"{out_prefix}_{n:02d}.png")
        sheet.save(path)
        sheets.append((path, chunk[0][0], chunk[-1][0]))
    return sheets


def loudness(ffmpeg: str, src: Path, window: float = 0.5) -> list[tuple[float, float]]:
    """RMS level (dBFS) per window, from mono 8 kHz PCM."""
    rate = 8000
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate),
                          "-f", "s16le", "-"], capture_output=True).stdout
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) // 2 * 2])
    n = int(rate * window)
    out = []
    for i in range(0, len(pcm), n):
        chunk = pcm[i:i + n]
        if not chunk:
            break
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk))
        out.append((i / rate, 20 * math.log10(rms / 32768) if rms > 0 else -90.0))
    return out


def scene_cuts(ffmpeg: str, src: Path, threshold: float = 0.3) -> list[float]:
    err = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src), "-an", "-vf",
                          f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", err)]


def black_intervals(ffmpeg: str, src: Path) -> list[tuple[float, float]]:
    """Black or nearly-black stretches (title cards like "fail x1", fades)."""
    err = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src), "-an", "-vf",
                          "blackdetect=d=0.2:pic_th=0.85:pix_th=0.12", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]


def shots(cuts: list[float], duration: float, blacks: list[tuple[float, float]]):
    """Continuous shots between scene cuts: [(start, end, is_title_card)]."""
    edges = [0.0, *cuts, duration]
    out = []
    for a, b in zip(edges, edges[1:]):
        if b - a < 0.25:
            continue
        black = sum(max(0.0, min(b, y) - max(a, x)) for x, y in blacks)
        out.append((a, b, black >= 0.6 * (b - a)))
    return out


def loud_moments(levels: list[tuple[float, float]], top: int = 8) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """(peaks, quiet stretches). Peaks: local maxima clearly above the median."""
    if not levels:
        return [], []
    vals = sorted(v for _, v in levels)
    median = vals[len(vals) // 2]
    peaks = [(t, v) for k, (t, v) in enumerate(levels)
             if v > median + 6
             and v >= max(x for _, x in levels[max(0, k - 2):k + 3])]
    peaks = sorted(sorted(peaks, key=lambda p: -p[1])[:top])
    quiet, run = [], None
    for t, v in levels:
        if v < median - 12 or v < -50:
            run = run or t
        else:
            if run is not None and t - run >= 1.0:
                quiet.append((run, t))
            run = None
    return peaks, quiet


def fetch_subtitles(url: str) -> list[str]:
    """Best-effort: TikTok sometimes has captions. Returns 'm:ss.s text' lines."""
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
            info = y.extract_info(url, download=False)
        subs = info.get("subtitles") or info.get("automatic_captions") or {}
        lang = next((k for k in subs if k.startswith("en")), next(iter(subs), None))
        if not lang:
            return []
        vtt = next((s for s in subs[lang] if s.get("ext") in ("vtt", "webvtt")), subs[lang][0])
        text = urllib.request.urlopen(vtt["url"], timeout=15).read().decode("utf-8", "replace")
    except Exception:
        return []
    lines, cur = [], None
    for line in text.splitlines():
        m = re.match(r"(\d+):(\d+)(?::(\d+))?\.(\d+)\s*-->", line)
        if m:
            a, b, c, ms = m.groups()
            secs = (int(a) * 3600 + int(b) * 60 + int(c)) if c else (int(a) * 60 + int(b))
            cur = secs + int(ms) / 1000
        elif line.strip() and cur is not None and not line.startswith(("WEBVTT", "NOTE")):
            lines.append(f"{fmt(cur)}  {line.strip()}")
            cur = None
    return lines


# --------------------------------------------------------------------------- modes
def analyze_one(idx: int, item: str, out: Path, step: float, rr) -> Path:
    meta = rr.get(item)
    url = meta.url or ""
    src = meta.path
    info = probe_media(rr.ffmpeg, src)
    if not info.has_video:
        raise RuntimeError("no video stream (photo/slideshow post?)")
    handle = (meta.handle or "unknown").lstrip("@")
    vid = re.search(r"/video/(\d+)", meta.webpage_url or url)
    d = out / f"{idx:02d}_{re.sub(r'[^A-Za-z0-9_.-]', '', handle)[:24]}_{vid.group(1)[-6:] if vid else idx}"
    d.mkdir(parents=True, exist_ok=True)

    # Long videos: coarser overview; zoom in later.
    eff_step = step if info.duration <= 90 else max(step, 1.0) if info.duration <= 240 else 2.0
    with tempfile.TemporaryDirectory() as tmp:
        frames = extract_frames(rr.ffmpeg, src, 0, info.duration, eff_step, 216, Path(tmp))
        sheets = contact_sheets(frames, d / "sheet", cols=6, rows=4, label_size=22)

    levels = loudness(rr.ffmpeg, src) if info.has_audio else []
    peaks, quiet = loud_moments(levels)
    cuts = scene_cuts(rr.ffmpeg, src)
    blacks = black_intervals(rr.ffmpeg, src)
    shot_list = shots(cuts, info.duration, blacks)
    subs = fetch_subtitles(url) if url else []

    stats = {}
    try:
        if not url:
            raise ValueError("no link")
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as y:
            ii = y.extract_info(url, download=False)
        stats = {k: ii.get(k) for k in ("view_count", "like_count", "comment_count", "repost_count")}
    except Exception:
        pass

    if levels:
        lo = min(v for _, v in levels)
        hi = max(v for _, v in levels)
        bars = [f"{fmt(t):>7}  {v:6.1f} dB  " + "█" * int(round((v - lo) / max(1e-6, hi - lo) * 30))
                for t, v in levels]
        (d / "audio.txt").write_text("Loudness every 0.5s (louder = longer bar)\n" + "\n".join(bars) + "\n")

    info_json = {
        "index": idx, "url": url or None, "webpage_url": meta.webpage_url, "creator": meta.handle,
        "caption": meta.title, "duration": round(info.duration, 3), "has_audio": info.has_audio,
        "width": info.width, "height": info.height, "local_file": str(src), "stats": stats,
        "frame_step": eff_step, "sheets": [str(p.name) for p, _, _ in sheets],
        "shots": [{"start": round(a, 2), "end": round(b, 2), "title_card": card} for a, b, card in shot_list],
    }
    (d / "info.json").write_text(json.dumps(info_json, indent=2))

    md = [f"# Video {idx}: {meta.handle or 'unknown creator'}",
          f"- URL: {url or '(none given: ask the user for the TikTok link, it is needed for credits and rendering)'}",
          f"- Caption: {meta.title or '(none)'}",
          f"- Duration: {fmt(info.duration)} ({info.duration:.1f}s), {info.width}x{info.height}, "
          f"{'has audio' if info.has_audio else 'NO AUDIO'}",
          f"- Stats: " + (", ".join(f"{k.replace('_count', 's')}={v:,}" for k, v in stats.items() if v is not None) or "n/a"),
          "", "## Contact sheets (frame every %.1fs, label = timestamp)" % eff_step]
    md += [f"- {p.name}: {fmt(a)} → {fmt(b)}" for p, a, b in sheets]
    md += ["", "## Loudest moments (screams, laughs, impacts, music drops)"]
    md += [f"- {fmt(t)}  {v:.1f} dB" for t, v in peaks] or ["- (no audio or flat audio)"]
    md += ["", "## Quiet stretches ≥1s (likely dead air)"]
    md += [f"- {fmt(a)} → {fmt(b)}" for a, b in quiet] or ["- none"]
    md += ["", "## Shots (between scene cuts)"]
    if len(shot_list) <= 1:
        md += ["- one continuous shot (no cuts detected)"]
    else:
        md += ["Black/title-card shots (e.g. \"fail x1\") are marked SKIP; never put them inside a clip.",
               "Several real shots back-to-back usually means a compilation: each shot is a candidate clip."]
        md += [f"- {fmt(a)} → {fmt(b)}  ({b - a:.1f}s)" + ("  SKIP: black/title card" if card else "")
               for a, b, card in shot_list]
    md += ["", "## Subtitles / spoken text"]
    md += subs or ["- none available"]
    if levels:
        md += ["", "Full loudness timeline: audio.txt"]
    (d / "summary.md").write_text("\n".join(md) + "\n")
    return d


def zoom(video_dir: Path, start: float, end: float, step: float, rr) -> Path:
    info = json.loads((video_dir / "info.json").read_text())
    src = Path(info["local_file"])
    start, end = max(0.0, start), min(info["duration"], end)
    with tempfile.TemporaryDirectory() as tmp:
        frames = extract_frames(rr.ffmpeg, src, start, end, step, 270, Path(tmp))
        sheets = contact_sheets(frames, video_dir / f"zoom_{start:.1f}-{end:.1f}", cols=5, rows=4, label_size=26)
    for p, a, b in sheets:
        print(f"{p}  ({fmt(a)} → {fmt(b)}, every {step}s)")
    return video_dir


@dataclass
class Source:
    path: Path
    url: str | None
    webpage_url: str | None
    handle: str | None
    title: str | None


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.netloc.lower().endswith("tiktok.com") and "/video/" in parts.path:
        return urlunsplit((parts.scheme or "https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    return url.strip()


class RR:
    """RankReel's download cache when available, otherwise a standalone yt-dlp cache."""

    def __init__(self, root: Path | None, cache: Path):
        self.mode = "rankreel" if root else "standalone"
        if root:
            sys.path.insert(0, str(root))
            from app import config, downloader  # noqa: E402

            config.ensure_dirs()
            self._fetch = downloader.fetch
            self.dl_dir = config.DOWNLOAD_DIR
            self.ffmpeg = config.ffmpeg_bin()
        else:
            self.ffmpeg = find_ffmpeg()
            self.dl_dir = cache

    def get(self, item: str) -> Source:
        path_part, _, link = item.partition("::") if "::" in item else (item, "", "")
        if not path_part.lower().startswith(("http://", "https://")):
            p = Path(path_part).expanduser()
            if not p.exists():
                raise RuntimeError(f"file not found: {p}")
            who = re.search(r"/(@[^/?#]+)", link) if link else None
            return Source(p.resolve(), link or None, normalize_url(link) if link else None,
                          who.group(1) if who else None, p.stem)
        url = path_part
        if self.mode == "rankreel":
            m = self._fetch(url)
            return Source(self.dl_dir / m.file, url, m.webpage_url, m.handle, m.title)
        return self._download(url)

    def _download(self, url: str) -> Source:
        try:
            import yt_dlp
        except ImportError:
            raise RuntimeError("yt-dlp missing: pip install yt-dlp")
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(normalize_url(url).encode()).hexdigest()
        mp4, meta = self.dl_dir / f"{key}.mp4", self.dl_dir / f"{key}.json"
        if mp4.exists() and meta.exists():
            d = json.loads(meta.read_text())
        else:
            opts = {"format": "mp4/bestvideo+bestaudio/best", "outtmpl": str(self.dl_dir / f"{key}.%(ext)s"),
                    "merge_output_format": "mp4", "quiet": True, "no_warnings": True, "noprogress": True,
                    "noplaylist": True, "ffmpeg_location": str(Path(self.ffmpeg).parent)}
            try:
                with yt_dlp.YoutubeDL(opts) as y:
                    info = y.extract_info(url, download=True)
            except Exception as e:
                msg = str(e).strip().splitlines()[-1][:300]
                raise RuntimeError(f"download failed ({msg}). If this environment can't reach TikTok, "
                                   "ask the user to upload the video file and pass it as file.mp4::link")
            if not mp4.exists():
                other = next((p for p in self.dl_dir.glob(f"{key}.*") if p.suffix not in (".json", ".part")), None)
                if not other:
                    raise RuntimeError("no video file downloaded (photo/slideshow post?)")
                other.rename(mp4)
            up = info.get("uploader") or info.get("uploader_id")
            d = {"webpage_url": normalize_url(info.get("webpage_url") or url),
                 "handle": (up if not up or up.startswith("@") else f"@{up}"), "title": info.get("title")}
            meta.write_text(json.dumps(d))
        return Source(mp4, url, d["webpage_url"], d["handle"], d["title"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*", help="TikTok links, video files, or file.mp4::link")
    ap.add_argument("--out", help="work folder for the analysis")
    ap.add_argument("--step", type=float, default=0.5, help="seconds between frames (overview 0.5, zoom 0.2)")
    ap.add_argument("--zoom", nargs=3, metavar=("VIDEO_DIR", "START", "END"),
                    help="fine-grained sheet of one window, to place exact cut points")
    ap.add_argument("--rankreel", help="path to the rankreel folder (auto-detected)")
    ap.add_argument("--standalone", action="store_true", help="ignore RankReel even if present")
    a = ap.parse_args()
    root = None if a.standalone else find_rankreel(a.rankreel)
    cache = Path(a.out or ".").resolve() / "_downloads" if a.out else Path(tempfile.gettempdir()) / "rankreel_dl"
    rr = RR(root, cache)

    if a.zoom:
        zoom(Path(a.zoom[0]), float(a.zoom[1]), float(a.zoom[2]), a.step if a.step != 0.5 else 0.2, rr)
        return 0
    if not a.urls or not a.out:
        ap.error("give URLs and --out (or use --zoom)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"mode: {rr.mode}")
    failed = 0
    for i, url in enumerate(a.urls, start=1):
        try:
            d = analyze_one(i, url, out, a.step, rr)
            print(f"[{i}] {url}\n    -> {d / 'summary.md'}")
        except Exception as e:  # keep going; report which link failed and why
            failed += 1
            print(f"[{i}] {url}\n    !! FAILED: {e}")
    return 1 if failed == len(a.urls) else 0


if __name__ == "__main__":
    sys.exit(main())
