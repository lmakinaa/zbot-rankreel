# RankReel (local)

Turn TikTok links into a vertical ranked-clip Short: a big title on top with colored words, a 1–N rank list that fills in as each clip plays, clips in your chosen order, #1 last.

Output: a 1080×1920 / 30 fps MP4 plus a `credits.txt` with each creator's handle and link for the description.

## Install

You need **Python 3.11+** and **ffmpeg** (with ffprobe):

```bash
# ffmpeg: pick one
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
winget install ffmpeg        # Windows

python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

No admin rights for ffmpeg? `pip install static-ffmpeg` and RankReel will use its bundled binaries automatically. Or point at any binary with `RANKREEL_FFMPEG` / `RANKREEL_FFPROBE`.

Keep yt-dlp current; TikTok changes often break old versions:

```bash
pip install -U yt-dlp
```

## Use the app

```bash
python -m app.main            # → http://127.0.0.1:8765  (--port to change)
```

It only listens on 127.0.0.1.

1. **Title.** Type it, then click words to cycle colors (white → yellow → red → green → custom).
2. **Clips.** Set **Number of clips** (1–10; 5–7 is typical), or use **+ Add clip** / **✕** on a row. Paste a TikTok link in each row (it fetches on paste/Enter), pick the rank, and name it. Use the clip player with **Start = now / End = now** to trim, and **▶ Trim** to check it. Start/end also accept `ss`, `mm:ss`, `mm:ss.ms`, `hh:mm:ss`.
3. **Playback order.** Drag chips (or focus one and use ← →). Presets: *Worst → best* and *Suspense* (e.g. 4, 2, 5, 3, 1).
4. **Style.** Choose a **Transition** for each cut: flash, zoom punch, flash + zoom, dip to black, slide in or shake (vibrate), at fast/normal/slow speed. It plays on clips 2…N (the opening hook stays a clean cut), applies to the footage only so the title and ranks stay still, and doesn't change the total length. Also here: cover/contain, a 0.3s end freeze for a cleaner loop, a transition sound, the active-rank color, uppercase, loudness normalization.
5. **Preview.** The phone frame shows each step's overlay over its clip. **▶ Play preview** plays the trimmed clips in order.
6. **Render.** Watch progress, then download the MP4 and copy the credits.

The total duration updates live. It turns red with "Over 40s — trim clips" above 40s (a warning, not a block). You also get hints under 20s or over 35s (the sweet spot is 27–35s), and a warning for any clip over 8s.

Your work is kept in the browser's localStorage. **Save** writes it to `projects/`, and **Load project…** reopens it. Every render also saves its project JSON next to the output name.

## CLI

```bash
python cli.py validate examples/example_project.json
python cli.py render   examples/example_project.json
python cli.py title "RANKING {#FFD400:FUNNIEST} {#FF3B30:MOMENTS} 😭"   # markup → tokens
python cli.py demo     # offline test render with synthetic clips
```

Project JSON (see [examples/example_project.json](examples/example_project.json)):

- `title` is either a token list `[{"text": "RANKING", "color": "#FFFFFF"}, …]` or a markup string. In markup, `{#hex:words}` colors every word inside it; bare words are white.
- `clips[]`: `url`, `rank` (1 = best), `name`, and optional `start` / `end` (seconds or timestamps). A missing start means 0; a missing end means the end of the video.
- `play_order` lists ranks in playback order. If omitted, it's worst → best (#1 last).
- `style.transition`: `none` (default) · `flash` · `zoom` · `punch` · `fade` · `slide` · `shake`; `style.transition_seconds` (default 0.25).
- `style.transition_sfx`: a file name in `assets/sfx/` (e.g. `"vibrate.wav"`) or `null`; `style.sfx_volume` (default 0.8).
- `style` sets layout, colors, `fit`, `end_hold_seconds`, `transition_sfx`, `loudnorm`, `font_path`, and more (see `app/models.py`).

## Outputs

- `output/<slug>_<timestamp>.mp4`: H.264 + AAC, `+faststart`, ready to upload as a Short
- `output/<slug>_<timestamp>_credits.txt`: `Clips: @user1 (url), @user2 (url)` plus one line per rank
- `projects/<slug>_<timestamp>.json`: re-render or tweak later

## Notes

- **Emoji** aren't in the overlay font, so they're dropped from the burned-in title. Keep them in your YouTube title.
- **HEVC clips.** TikTok often serves H.265. The original is kept for rendering, and a small H.264 copy (`*.preview.mp4`) is made only for the in-browser preview.
- **Default transition sound.** New projects use *Core sound effect* (`assets/sfx/core_sound_effect.wav`). It's prepared from your `core-sound-effect.mp3`: leading silence trimmed so the hit lands on the cut, ends de-clicked, level matched. To regenerate it: `.venv/bin/python -c "from pathlib import Path; from app.sfx_gen import prepare_sound; prepare_sound(Path('../core-sound-effect.mp3'), Path('assets/sfx/core_sound_effect.wav'))"`. If the file is missing, the app falls back to no sound.
- **Sync.** The transition sound fits its transition: it starts when the effect starts (on the cut for flash/zoom/punch/slide/shake; when the screen starts darkening for dip to black) and is cut to the transition's length with a short fade, so it ends when the effect ends. Choose a slower transition speed to hear more of a long sound; with no transition, the whole sound plays from the cut. Measured within 5 ms in the rendered video, and the live preview plays it the same way. Segments keep lossless audio and the final video's audio is encoded once, so clip audio stays in sync with the picture across every cut.
- **Transition sounds.** Three vibration sounds are built in: *Vibrate*, *Vibrate double* and *Vibrate heavy*. They're synthesized by RankReel (original, royalty-free) in `assets/sfx/` and recreated on startup if deleted (`python cli.py make-sfx --force` regenerates them). The sound plays on each cut, starting at the same moment as the visual transition (pair it with **Shake**), not on the opening clip. Picking a sound in the UI plays it, and **Sound volume** sets Low/Medium/High. **Your own sounds:** click **⬆ Upload sound…** under the sound list. Accepted: `.mp3 .wav .m4a .aac .ogg .flac`, up to 10 MB and 10s. Each file is checked to be real audio, prepared like the default sound (leading silence trimmed so it hits on the cut, de-clicked, level matched, saved as `.wav`), saved to `assets/sfx/`, listed under *Your sounds*, selected, and played once. **Delete** removes a custom sound (built-ins can't be deleted). You can also drop files straight into `assets/sfx/`.
- **Links that need login.** Export a Netscape `cookies.txt` from your browser and set `RANKREEL_COOKIES=/path/cookies.txt`, or put `{"cookies_file": "cookies.txt"}` in `config.json`.
- **Errors.** Private, removed and photo/slideshow posts, and rate limits, each show a plain message on the clip row.
- **Font.** Montserrat Black (SIL OFL, `assets/fonts/OFL.txt`). Change `style.font_path` to use another TTF/OTF.
- **Cache.** Downloads live in `cache/downloads/<sha1(url)>.mp4` with a metadata JSON. TikTok tracking query strings are ignored, so the same video isn't fetched twice. Delete `cache/` any time.
- RankReel has no watermark-removal feature, by design.

### Copyright

Reused TikTok clips can get copyright claims or fail YouTube's reused-content checks. Credit the creators (paste the credits file into the description), keep clips short, and add your own edit: the ranking, titles and pacing.

## Development

```bash
pytest                       # ~30s; compose tests run real ffmpeg encodes
pytest -m "not slow"         # quick
RANKREEL_NETWORK_TESTS=1 pytest -k real_tiktok   # one real download
```

Editor regression tests also run through pytest when Node.js is installed; no npm packages are needed.

Layout: `app/models.py` (data model and title markup), `timeparse.py`, `validate.py`, `overlay.py` (Pillow), `compose.py` (ffmpeg), `downloader.py` (yt-dlp and cache), `render_job.py` (pipeline and background jobs), `main.py` (FastAPI), `static/` (UI), `cli.py`.
