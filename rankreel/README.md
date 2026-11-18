# RankReel (local)

Turn TikTok and Instagram Reel links into a vertical ranked-clip Short: a big title on top with colored words, a 1–N rank list that fills in as each clip plays, clips in your chosen order, #1 last.

Output: a 1080×1920 / 30 fps MP4 plus a `credits.txt` with each creator's handle and link for the description.

## Run with Podman

Run these commands from the repository root (the directory containing `Containerfile`). The base image comes directly from Fedora's registry (`registry.fedoraproject.org/fedora:44`). The image includes Python, FFmpeg/FFprobe (downloaded at build time by `static-ffmpeg`), the font, and the Core sound effect.

On macOS or Windows, initialize Podman's VM once if needed, then start it:

```bash
podman machine init          # first time only
podman machine start        # if it is not already running
```

Build and start the app:

```bash
podman build --format docker -t localhost/rankreel:latest .
podman run -d --name rankreel --init --restart unless-stopped \
  -p 127.0.0.1:8766:8765 \
  -v rankreel-cache:/app/cache \
  -v rankreel-projects:/app/projects \
  -v rankreel-output:/app/output \
  -v rankreel-sfx:/app/assets/sfx \
  localhost/rankreel:latest
```

Open [RankReel](http://127.0.0.1:8766). It runs as a non-root user inside the container and the published port listens only on this computer. The image format keeps the built-in health check available to Podman. The host uses port 8766 so it can coexist with services already using 8765; the app still uses 8765 inside the container.

The four named volumes preserve downloaded clips, projects, finished videos, and uploaded sounds when the container is recreated. The sounds volume starts with the bundled sounds, including Core sound effect. These volumes are separate from any files made by a host Python install. Download videos through the app, or copy files out with `podman cp rankreel:/app/output/. ./exports`.

```bash
podman stop rankreel         # stop
podman start rankreel        # start the existing container again
podman logs -f rankreel      # view logs
podman healthcheck run rankreel
```

To update, rebuild the image, stop and remove just the container with `podman stop rankreel` and `podman rm rankreel`, then repeat `podman run` above with the same volume names. Saved data stays in those volumes.

Run the full test suite, including the editor tests, in a disposable test container:

```bash
podman build --format docker --target test -t localhost/rankreel-test .
podman run --rm localhost/rankreel-test
```

Local `config.json`, cookies, caches, projects, renders, and other uploaded sounds are excluded from the build context. If a link needs login, mount your cookies file read-only at `/run/cookies.txt` and add `-e RANKREEL_COOKIES=/run/cookies.txt` to `podman run`.

If image downloads report an unknown certificate authority on a managed Mac, import the Mac's trusted certificates into the VM, then retry:

```bash
podman machine stop
podman machine set --import-native-ca
podman machine start
```

See the [Podman build](https://docs.podman.io/en/stable/markdown/podman-build.1.html) and [run](https://docs.podman.io/en/stable/markdown/podman-run.1.html) references for more options.

## Run with Docker

Docker builds the same `Containerfile`. From the repository root, with Docker Compose:

```bash
docker compose up -d --build     # build and start
docker compose logs -f           # view logs
docker compose down              # stop and remove the container (volumes and data stay)
```

Open [RankReel](http://127.0.0.1:8766). [`compose.yaml`](../compose.yaml) uses the same port, container name (`rankreel`) and volume names as the Podman setup above. To update, run `docker compose up -d --build` again. For links that need login, uncomment the cookies lines in `compose.yaml`.

If the build hangs at `FROM registry.fedoraproject.org/fedora:44` (Fedora's registry can be very slow on some networks), pull the same official image from Docker Hub instead: `RANKREEL_BASE_IMAGE=docker.io/library/fedora:44 docker compose up -d --build`, or add `--build-arg BASE_IMAGE=docker.io/library/fedora:44` to `docker build`.

Without Compose:

```bash
docker build -f Containerfile --target runtime -t rankreel:latest .
docker run -d --name rankreel --init --restart unless-stopped \
  -p 127.0.0.1:8766:8765 \
  -v rankreel-cache:/app/cache \
  -v rankreel-projects:/app/projects \
  -v rankreel-output:/app/output \
  -v rankreel-sfx:/app/assets/sfx \
  rankreel:latest
```

Run the tests in a disposable container:

```bash
docker build -f Containerfile --target test -t rankreel-test .
docker run --rm rankreel-test
```

Docker and Podman share `.dockerignore` (Podman reads it when there's no `.containerignore`), so local `config.json`, cookies, caches, projects and renders stay out of the image with either tool.

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
4. **Style.** Choose a **Transition** for each cut: flash, zoom punch, flash + zoom, dip to black, slide in or shake (vibrate), at fast/normal/slow speed. It plays on clips 2…N (the opening hook stays a clean cut), applies to the footage only so the title and ranks stay still, and doesn't change the total length. Also here: **Fit** (see below), a 0.3s end freeze for a cleaner loop, a transition sound, the active-rank color, uppercase, loudness normalization.
5. **Preview.** The phone frame shows each step's overlay over its clip. **▶ Play preview** plays the trimmed clips in order.
6. **Render.** Watch progress, then download the MP4 and copy the credits.

**Formats.** Pick one at the top of the editor:

- **Ranking** is everything above.
- **Single clip + text** is a reels-style video: black background, your text centred above one clip, and your channel card above the text if it's on. Use it for a fact or a caption about the clip. A wide or cropped clip spans the full width; a tall one is shown as large as fits.
  - Line breaks are kept. Color words with `{#FFD400:these words}`, or select them and click a color dot.
  - Long text shrinks to fit. If it still doesn't fit, the end is cut and you get a warning.
  - Switching formats keeps each format's clips, so you can go back and forth.

In project JSON this is `"format": "single"` with a `"caption"`.

**Crop.** Every clip, in either format, has a **✂ Crop** button next to the trim buttons. Its sliders cut 0–45% off the top, bottom, left and right. Use it for burned-in captions, borders or a watermark at the edge.

- The thumbnail frames what's kept, and the phone preview shows the result.
- **Shape** buttons (9:16, 4:5, 1:1, 16:9) set a centred crop to that shape; fine-tune it with the sliders. In a ranking, 9:16 makes the clip fill the screen. In the single-clip format, use them to cut a reel's own bars, captions or reply bubble down to the real video.
- Cropping happens before **Fit**. If what's left isn't close to 9:16, smart fill still shows it whole on a blurred background.
- In JSON: `"crop": {"top": 0.1, "left": 0.2, "right": 0.2}` (shares of the source, 0–0.45).

**Channel card.** Under Style → **Channel card** you can show it in rankings too: it goes centred above the title. You can change the name, the handle, the verified check and the avatar. The RankCracker avatar is `assets/brand/rankcracker.jpg`.

The total duration updates live. It turns red with "Over 40s — trim clips" above 40s (a warning, not a block). You also get hints under 20s or over 35s (the sweet spot is 27–35s), and a warning for any clip over 8s.

Your work is kept in the browser's localStorage. **Save** writes it to `projects/`, and **Load project…** reopens it. Every render also saves its project JSON next to the output name.

## Use with Claude Desktop

RankReel includes a small MCP server so Claude can save draft reels straight into the app. You then open the draft, check the trims and render it yourself. Claude can list, read, check and save projects, but it can't delete or render.

The server runs inside the running container, so drafts land in the same `rankreel-projects` volume the app reads, and there's no extra port or anything to install on your computer. Add this to Claude Desktop's config (**Settings → Developer → Edit Config**, which opens `claude_desktop_config.json`), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "rankreel": {
      "command": "podman",
      "args": ["exec", "-i", "rankreel", "python", "-m", "app.mcp_server"]
    }
  }
}
```

With Docker, use `"command": "docker"` and the same `args`. The `rankreel` container has to be running (`podman start rankreel` or `docker compose up -d`). If Claude Desktop can't find `podman` or `docker`, use the full path (`which podman` / `which docker`). Without a container, point `command` at your venv's Python (`/path/to/rankreel/.venv/bin/python`), use `"args": ["-m", "app.mcp_server"]`, and add `"env": {"PYTHONPATH": "/path/to/rankreel"}`.

Tools:

- `save_project`: checks the project and saves it as a new draft (it never overwrites; a taken name gets `_2`). It returns an `editor_url` like `http://127.0.0.1:8766/?project=<name>` that opens the draft in the editor. Set `RANKREEL_PUBLIC_URL` if the app is on a different address.
- `check_project`: the same checks without saving.
- `list_projects`, `get_project`: read saved drafts.
- `list_sounds`: names usable as `style.transition_sfx`.

Drafts also appear under **Load project…**. The clip-picker skill uses `save_project` automatically when the tool is connected. claude.ai on the web can't reach your computer, so there you keep using **Import**.

To try the server without Claude: `npx @modelcontextprotocol/inspector podman exec -i rankreel python -m app.mcp_server` (or `docker exec …`).

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

- **Emoji** are drawn in color in the title, rank names, channel card and single-clip text, using a color emoji font: Noto Color Emoji on Linux, Apple Color Emoji on macOS or Segoe UI Emoji on Windows. To use another, put it at `assets/fonts/NotoColorEmoji.ttf` or set `"emoji_font"` in `config.json`. With no emoji font, emoji are left out.
- **HEVC clips.** TikTok often serves H.265. The original is kept for rendering, and a small H.264 copy (`*.preview.mp4`) is made only for the in-browser preview.
- **Default transition sound.** New projects use *Core sound effect* (`assets/sfx/core_sound_effect.wav`). It's prepared from your `core-sound-effect.mp3`: leading silence trimmed so the hit lands on the cut, ends de-clicked, level matched. To regenerate it: `.venv/bin/python -c "from pathlib import Path; from app.sfx_gen import prepare_sound; prepare_sound(Path('../core-sound-effect.mp3'), Path('assets/sfx/core_sound_effect.wav'))"`. If the file is missing, the app falls back to no sound.
- **Sync.** The transition sound fits its transition: it starts when the effect starts (on the cut for flash/zoom/punch/slide/shake; when the screen starts darkening for dip to black) and is cut to the transition's length with a short fade, so it ends when the effect ends. Choose a slower transition speed to hear more of a long sound; with no transition, the whole sound plays from the cut. Measured within 5 ms in the rendered video, and the live preview plays it the same way. Segments keep lossless audio and the final video's audio is encoded once, so clip audio stays in sync with the picture across every cut.
- **Transition sounds.** Three vibration sounds are built in: *Vibrate*, *Vibrate double* and *Vibrate heavy*. They're synthesized by RankReel (original, royalty-free) in `assets/sfx/` and recreated on startup if deleted (`python cli.py make-sfx --force` regenerates them). The sound plays on each cut, starting at the same moment as the visual transition (pair it with **Shake**), not on the opening clip. Picking a sound in the UI plays it, and **Sound volume** sets Low/Medium/High. **Your own sounds:** click **⬆ Upload sound…** under the sound list. Accepted: `.mp3 .wav .m4a .aac .ogg .flac`, up to 10 MB and 10s. Each file is checked to be real audio, prepared like the default sound (leading silence trimmed so it hits on the cut, de-clicked, level matched, saved as `.wav`), saved to `assets/sfx/`, listed under *Your sounds*, selected, and played once. **Delete** removes a custom sound (built-ins can't be deleted). You can also drop files straight into `assets/sfx/`.
- **Fit (non-vertical clips).** The default *Smart fill* fills the 9:16 frame when a clip is already (nearly) vertical, and shows the whole video at full width over a blurred copy of itself when filling would cut off more than ~6% (square or 4:5 Reels, landscape clips), so nothing at the sides is lost. Other options: *Show whole video, blurred background*, *Show whole video, black bars*, *Crop to fill (always)*. Rotated phone videos are measured the way they're displayed.
- **Instagram Reels** work like TikTok links: paste `instagram.com/reel/…` (also `/reels/`, `/p/`, `/tv/`, and share links with `?igsh=…`). Credits use the creator's @username. Multi-video (carousel) posts aren't supported; use a single-video post.
- **Links that need login.** Instagram often asks for a login (TikTok sometimes does). Easiest: log in to the site in your browser, then create `rankreel/config.json` with `{"cookies_from_browser": "firefox"}` (or `"chrome"`, `"edge"`, `"brave"`…; env var `RANKREEL_COOKIES_FROM_BROWSER`). Or export a Netscape `cookies.txt` and set `RANKREEL_COOKIES=/path/cookies.txt` / `{"cookies_file": "cookies.txt"}`. Restart the server after changing it.
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

Layout: `app/models.py` (data model and title markup), `timeparse.py`, `validate.py`, `overlay.py` (Pillow), `compose.py` (ffmpeg), `downloader.py` (yt-dlp and cache), `render_job.py` (pipeline and background jobs), `projects.py` (saved projects), `main.py` (FastAPI), `mcp_server.py` (MCP tools for Claude), `static/` (UI), `cli.py`.
