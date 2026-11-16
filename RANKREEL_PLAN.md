# RankReel (local) — Build Plan

Build a local tool that turns TikTok links into a ranked-clip vertical Short in the style of trending "ranking" channels (e.g. RankZilla): a title at the top with colored words, a rank list (1–N) that fills in as each clip plays, clips shown in a custom order, and the #1 clip last.

Hand this whole file to Claude Code and build it milestone by milestone.

---

## 1. Requirements

**Input per clip**
- TikTok URL (required)
- Start / end timestamp (optional; accepts `ss`, `mm:ss`, `mm:ss.ms`, `hh:mm:ss`)
- Rank number (1 = best)
- Rank name (the label shown next to its number, e.g. "Dog vs vacuum")

**Input per video**
- Title, with chosen words colored
- Playback order: which rank plays 1st, 2nd, … (default: worst to best, #1 last)

**Output**
- One MP4, 1080×1920, 9:16, ready to upload as a Short
- A `credits.txt` with each clip's creator handle and source URL (for the description)

**Rules**
- Warn (don't block) when the total duration is over **40s**. Show the total live in the UI.
- Soft hint when the total is under 20s or over 35s (the trend sweet spot is 27–35s).
- Every rank 1..N used exactly once; the playback order must contain every rank once.

---

## 2. Stack

| Part | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Simple, good media libs |
| Download | `yt-dlp` (pip) | Supports TikTok, gives uploader metadata |
| Video processing | `ffmpeg` / `ffprobe` (system binary) | Fast, reliable trimming and encoding |
| Text overlays | `Pillow` | Per-word colors + thick outline are easy in Pillow, hard in ffmpeg drawtext |
| Backend | `FastAPI` + `uvicorn` | Small local API |
| Frontend | One `index.html` + vanilla JS (no build step) | Drag-to-reorder, preview, live duration |
| Data model | `pydantic` | Validates the project JSON |
| Tests | `pytest` | |

Font: bundle an open-licensed heavy font in `assets/fonts/` (e.g. **Montserrat Black** or **Anton**, both SIL OFL). Make the font path configurable.

---

## 3. Project layout

```
rankreel/
  app/
    main.py            # FastAPI app, serves UI + API
    models.py          # pydantic Project / Clip / TitleToken / Style
    timeparse.py       # timestamp parsing
    downloader.py      # yt-dlp wrapper + cache
    probe.py           # ffprobe helpers (duration, size, has_audio)
    overlay.py         # Pillow: renders title + rank list PNG per segment
    compose.py         # ffmpeg: trim, scale/crop, overlay, concat
    validate.py        # duration + rank/order checks, warnings
    render_job.py      # background render with progress
  static/
    index.html
    app.js
    style.css
  assets/
    fonts/Montserrat-Black.ttf
    sfx/               # optional user-supplied whoosh/hit sounds (royalty-free)
  cache/downloads/     # keyed by sha1(url)
  projects/            # saved project JSON files
  output/
  tests/
  cli.py               # render a project JSON without the UI
  requirements.txt
  README.md
```

---

## 4. Data model (`models.py`)

```python
class TitleToken(BaseModel):
    text: str
    color: str = "#FFFFFF"        # hex

class Clip(BaseModel):
    id: str                        # uuid
    url: HttpUrl
    start: float | None = None     # seconds
    end: float | None = None
    rank: int                      # 1 = best
    name: str                      # label shown next to rank
    # filled after download:
    local_path: str | None = None
    source_duration: float | None = None
    creator: str | None = None     # yt-dlp "uploader" / "creator"

class Style(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    title_area_height: int = 400
    title_font_size_max: int = 96
    title_font_size_min: int = 56
    title_stroke: int = 8
    rank_font_size: int = 64
    rank_x: int = 40
    rank_top: int = 470
    rank_spacing: int = 110
    rank_color: str = "#FFFFFF"
    rank_active_color: str = "#FFD400"   # the rank currently playing
    fit: Literal["cover", "contain"] = "cover"
    background: str = "#000000"
    font_path: str = "assets/fonts/Montserrat-Black.ttf"
    transition_sfx: str | None = None    # path in assets/sfx
    loudnorm: bool = True

class Project(BaseModel):
    title: list[TitleToken]
    clips: list[Clip]
    play_order: list[int]          # ranks in playback order, e.g. [5,4,3,2,1]
    style: Style = Style()
    max_seconds_warning: float = 40.0
```

**Title coloring input.** Support both:
1. The UI: type the title, it splits into words, and clicking a word cycles its color (white → yellow → red → green → custom).
2. Markup for the CLI: `RANKING {#FFD400:FUNNIEST} {#FF3B30:MOMENTS} 😭` → parse to tokens. Unit-test the parser.

Titles are rendered **UPPERCASE** by default (toggle in style).

---

## 5. Pipeline

### 5.1 Download (`downloader.py`)
- `yt_dlp.YoutubeDL({"format": "mp4/bestvideo+bestaudio/best", "outtmpl": "cache/downloads/<sha1>.%(ext)s", "merge_output_format": "mp4", "quiet": True})`
- Cache: skip if `cache/downloads/<sha1(url)>.mp4` exists.
- Save metadata JSON next to it (`uploader`, `webpage_url`, `duration`).
- Clear errors for private/removed videos, photo-slideshow posts (no video stream), and rate limits. Optional `cookies.txt` path in config for links that need login.
- Do **not** add any watermark-removal feature.

### 5.2 Validate (`validate.py`)
- Each clip: `0 <= start < end <= source_duration`. Missing start = 0, missing end = source end.
- Clip length = `end - start`. Warn if a single clip is over 8s (trend clips are short).
- Ranks are exactly `{1..N}`; `play_order` is a permutation of them.
- Total = sum of clip lengths (+ any hold frames). Return `warnings: list[str]`, e.g. `"Total 43.2s exceeds 40s"`.
- An API endpoint returns this so the UI updates live.

### 5.3 Overlay per segment (`overlay.py`)
For each step `i` in `play_order`, render one transparent 1080×1920 PNG containing:

**Title (top zone, y 0 → `title_area_height`)**
- Centered, max 2 lines, word-wrapped by measured width (`ImageDraw.textlength`).
- Auto-shrink font from max → min size until it fits in 2 lines.
- Each word drawn in its token color with a black stroke (`stroke_width`, `stroke_fill="#000"`).
- Emoji: if the font can't render it, drop it from the overlay (keep it in the YouTube title only). Note this in the README.

**Rank list (left side, over the video)**
- Rows `1.` to `N.` at `rank_top + (rank-1) * rank_spacing`; numbers always visible.
- A rank's **name is shown only if that rank has already played or is playing now** (cumulative reveal; this is the core of the trend format).
- The currently playing rank uses `rank_active_color`; the others are white. Black stroke on everything.
- Long names: shrink to fit `width - rank_x - 40`, then ellipsize.

Cache PNGs by a hash of (title, revealed set, active rank, style).

### 5.4 Compose (`compose.py`)
Per segment (one clip + its overlay), make an intermediate MP4 with identical encoding params so concat needs no re-encode:

```
ffmpeg -y -ss {start} -to {end} -i clip.mp4 -loop 1 -i overlay.png \
  -filter_complex "
    [0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,
         fps=30,setsar=1[v];
    [v][1:v]overlay=0:0:shortest=1,format=yuv420p[out]" \
  -map "[out]" -map 0:a? \
  -af "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100" \
  -c:v libx264 -preset veryfast -crf 20 -r 30 \
  -c:a aac -b:a 160k -ac 2 \
  -t {end-start} seg_{i}.mp4
```
- `fit="contain"`: scale with `decrease`, pad to 1080×1920 with the background color (black bars).
- **No audio stream** in a clip: add `anullsrc` so every segment has audio (concat needs it).
- Optional transition sfx: mix a short whoosh at the start of each segment with `amix` (user-provided file only).
- Final: concat demuxer (`-f concat -safe 0 -i list.txt -c copy`) then `-movflags +faststart`.
- Run segments in a background job; report progress (`segment k of N`) to the UI by polling.

### 5.5 Outputs
- `output/<slug>_<timestamp>.mp4`
- `output/<slug>_<timestamp>_credits.txt`, e.g. `Clips: @user1 (url), @user2 (url)`
- Save the project JSON to `projects/` so a video can be re-rendered or tweaked.

---

## 6. API (`main.py`)

| Method | Path | Does |
|---|---|---|
| GET | `/` | Serves the UI |
| POST | `/api/clips/fetch` | `{url}` → downloads, returns duration, creator, preview URL |
| GET | `/media/{file}` | Serves cached clips for in-browser preview |
| POST | `/api/validate` | Project → `{total_seconds, warnings, errors}` |
| POST | `/api/render` | Project → `{job_id}` |
| GET | `/api/render/{job_id}` | `{status, progress, output_url, warnings}` |
| GET/POST | `/api/projects` | List / save projects |

Bind to `127.0.0.1` only.

---

## 7. UI (`static/`)

One page, top to bottom:
1. **Title editor**: text input, then clickable word chips to set colors, plus a live title preview drawn on a canvas (or a PNG from `/api/preview-overlay`).
2. **Clips list**: "Add clip" row with URL, start, end, rank (dropdown), name. After fetch, show a small `<video>` preview with "set start = current time" / "set end = current time" buttons.
3. **Playback order**: drag-to-reorder chips showing `#rank name`. Presets: "Worst → best (#1 last)" and "Suspense" (e.g. 4, 2, 5, 3, 1 for N=5: strong opener, #1 last).
4. **Duration bar**: total seconds, turns red with the message *"Over 40s — trim clips"* above 40s.
5. **Render** button, progress bar, then a download link and the credits text with a copy button.

Persist the current project to `localStorage` (wrap in try/catch) so a refresh doesn't lose work.

---

## 8. CLI (`cli.py`)

```
python cli.py render projects/my_video.json
python cli.py validate projects/my_video.json
```
Prints warnings (including the 40s warning) and the output path.

---

## 9. Trend format defaults (from the competitor analysis)

- Total **27–35s**, 5 clips (allow 3–10).
- Each clip **3–7s**, punchline inside the first ~5s; trim dead air.
- Title on top, big, uppercase, 1–2 colored words, black outline.
- Rank numbers visible from frame 1, names revealed as clips play, so viewers stay to see what fills the empty slots.
- **#1 always last.** The first clip played should still be strong (hook in the first 1.5s).
- Vertical 1080×1920, 30fps, loud and normalized audio (Shorts are sound-on).
- Optional: end on a 0.3s freeze of the last frame for a cleaner loop (style flag `end_hold_seconds`, default 0).

---

## 10. Milestones (build in this order)

1. **Core + CLI, no network.** `models`, `timeparse`, `validate`, `overlay`, `compose`. Test with local MP4s generated by `ffmpeg -f lavfi -i testsrc=...` and `sine` audio. Output a correct 1080×1920 MP4.
2. **Downloader** with cache + metadata + credits file.
3. **FastAPI** endpoints + background render job.
4. **UI**: clip rows, preview + set start/end, title word coloring, drag reorder, live duration warning.
5. **Polish**: optional sfx, contain/cover toggle, end hold, project save/load, README.

## 11. Tests (`tests/`)

- `timeparse`: `"5"`, `"1:05"`, `"1:05.5"`, `"01:02:03"`, invalid input.
- Title markup parser → tokens.
- `validate`: duplicate ranks, a bad play order, end ≤ start, total over 40s produces a warning (not an error).
- `overlay`: snapshot test; revealed names match the play order at each step; the active rank is colored.
- `compose` integration: 3 synthetic clips (one without audio) → output has the expected duration (±0.1s), 1080×1920, both audio and video streams (check with ffprobe).

## 12. Acceptance checklist

- [ ] 5 TikTok links + timestamps → one MP4 with no manual editing
- [ ] Title on top with chosen words colored, outline readable on bright footage
- [ ] Rank names reveal cumulatively in the chosen order; the current rank is highlighted
- [ ] Warning shown when total > 40s
- [ ] Output plays in a phone browser and uploads to YouTube Shorts as 9:16
- [ ] `credits.txt` lists every creator

## 13. Notes

- Reused TikTok clips can get copyright claims or fail YouTube's reused-content checks. Credit creators (the credits file helps), keep clips short, and add your own edit (ranking, titles, pacing).
- `requirements.txt`: `fastapi uvicorn[standard] yt-dlp pillow pydantic pytest`. The README must say to install ffmpeg separately (`brew install ffmpeg` / `apt install ffmpeg` / winget).
- Keep yt-dlp updated (`pip install -U yt-dlp`); TikTok changes break old versions often.
