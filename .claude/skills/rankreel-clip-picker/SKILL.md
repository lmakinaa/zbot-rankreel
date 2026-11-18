---
name: rankreel-clip-picker
description: Analyze TikTok videos and pick the exact start/end timestamps to clip for a viral ranking Short, then rank and order them and save a ready-to-load RankReel project. Use this whenever the user pastes one or more TikTok links and wants clips, timestamps, "the best moments", a ranking/top-N video, trims for RankReel, or asks which parts to cut — even if they only paste links with a word like "clip these", "timestamps", or "make a ranking".
---

# RankReel clip picker

The user turns TikTok links into ranked-clip Shorts with RankReel, their local video tool. Your job: watch each video (via contact sheets + audio timeline), find the moments most likely to stop a scroller and get rewatched, cut them tight, rank them with the user's rules, and hand back a project they can open and render.

## The user's ranking rules (why they matter)

The opener decides whether people swipe away, and the finale decides whether they watch to the end and rewatch. So:

- **The single most eye-catching, most viral clip is ranked #2 and plays FIRST.** It's the hook. Viewers see "#2" and stay to find out what could beat it.
- **The #1 clip plays LAST.** Pick the strongest *remaining* clip, ideally one with a clear payoff (a fail landing, a reveal, a big reaction), so the wait feels rewarded.
- **Everything else: rank and order don't matter, but shuffle them.** `build_project.py` does this for you (random ranks 3..N, random positions, never a plain countdown).

You only decide which clip is the hook and which is the finale; the script assigns all ranks and the play order.

## Where you're running (check first)

The scripts are in this skill's `scripts/` folder; `<skill>` below means this skill's directory.

- **Next to RankReel** (Claude Code in the user's project, which has a `rankreel/` folder with `rankreel/.venv/`): run the scripts with `rankreel/.venv/bin/python` from the folder that contains `rankreel/`. Downloads go into RankReel's cache, and the project is saved straight into RankReel's **Load project…** list. Work folder: `rankreel/cache/picks/<YYYYMMDD-HHMM>/`.
- **Anywhere else** (e.g. Claude.ai): first `pip install yt-dlp pillow imageio-ffmpeg` (skip what's already installed; ffmpeg from the system works too) and run the scripts with `python`. Use a work folder like `./picks/`. The scripts detect that RankReel is missing and switch to standalone mode on their own.
  - If downloading a link fails because the environment can't reach TikTok, ask the user to **upload the video files** and give you the matching links. Pass each as `path/to/file.mp4::https://www.tiktok.com/@user/video/123`. The link is still needed: RankReel uses it to credit the creator and download the clip when rendering.
  - At the end, the project file is the deliverable: save or copy it to the place this environment uses for files the user can download (e.g. `/mnt/user-data/outputs/`).
- **RankReel connector available** (you have RankReel's `save_project` tool, e.g. Claude Desktop with the RankReel MCP server): analyze and build exactly as in the standalone case. Then, instead of handing over a file, call `save_project` with the contents of the built project `.json` (pass the object; JSON text also works). It saves the draft straight into the user's RankReel and returns an `editor_url`. If it refuses with errors, fix the picks, rebuild and save again. A taken name never gets overwritten (it saves as `_2`), so don't retry with the same picks once it has succeeded. `check_project` runs the same checks without saving, `list_sounds` gives valid `style.transition_sfx` names, and `list_projects` / `get_project` read existing drafts (e.g. "tweak yesterday's reel": get it, change it, save it as a new draft). The connector can't render: the user reviews and renders in the editor.

## Workflow

### 1. Work out what's being asked
- Clip count: what the user says. Otherwise 5, or as many as it takes to reach ~27s (RankReel takes 1-10; 5-8 is typical). Compilations often have 3-4s moments, so 7-8 tight clips beat 5 clips padded with dead air; say so if you go above 5. With fewer links than clips, take several moments from the same video. With more links than clips, keep the best and say which you dropped and why.

### 2. Analyze the videos
```bash
python <skill>/scripts/analyze.py URL1 URL2 ... --out <workdir>          # links
python <skill>/scripts/analyze.py clip1.mp4::URL1 clip2.mp4::URL2 --out <workdir>   # uploaded files
```
(Use `rankreel/.venv/bin/python` instead of `python` when RankReel is there.) It downloads each video and writes, per video, a folder with:
- `summary.md`: caption, duration, views/likes, **loudest moments**, **quiet stretches** (dead air), **shots** (with black title cards like "fail x1" marked SKIP), subtitles if TikTok has any. Read this first.
- `sheet_NN.png`: frames every 0.5s, each labeled with its timestamp. **Look at every sheet** (Read the image), since this is how you actually see the video. Don't pick moments from the summary alone.
- `audio.txt`: loudness every 0.5s. Spikes are usually screams, laughs, crashes or the music drop. If the loud moments line up with the shot boundaries or title cards, the audio is just a music bed hitting on each cut; ignore it and judge by the frames.

If a link fails (private, removed, photo post), tell the user which one and why, and carry on with the rest.

### 3. Find the moments
For each video, note candidate moments with a timestamp range and what happens. What tends to go viral in ranking Shorts:
- **Instant visual hook**: something surprising or chaotic is visible within the first ~1s of the clip (mid-action, not setup).
- **A clear payoff**: fail, impact, unexpected twist, reveal, absurd behavior. The audience should "get it" without context or sound.
- **A reaction**: a scream or laugh (loudness spike) right after the action doubles the payoff.
- **Fast, readable action**: one thing happening and easy to follow on a phone.

Avoid: slow setups, intros, talking before the action, dead air (quiet stretches), the creator's end cards/outros, black title cards, and moments that only work with long context.

### 4. Cut tight, with exact timestamps
- **Length: 3-7s per clip** (RankReel warns above 8s; a very fast gag can be ~2.5s). Aim for a **total of 27-35s**; over 40s gets flagged.
- **Start** ~0.3-1.0s before the action kicks off, just enough to see what's about to happen. Never start on a title card or a cut.
- **End** ~0.5-1.0s after the payoff or reaction peaks, before it goes flat. Don't cut mid-scream or mid-word.
- Stay inside one shot; don't run across a SKIP card or into the next compilation entry.
- **Refine every boundary with a zoom sheet** (0.2s frames) around your rough start and end:
  ```bash
  python <skill>/scripts/analyze.py --zoom <workdir>/01_<creator>_<id> 4.0 11.0
  ```
  Look at it and pick the exact frame. Use 0.1s precision in the final numbers.

### 5. Choose the hook and the finale
Score each pick on how fast it grabs attention (hook) and how satisfying it ends (payoff). The one that most makes you stop scrolling in the first second is the **hook** (`"role": "hook"`). From the rest, the best payoff is the **finale** (`"role": "finale"`). If two clips come from the same video, that's fine; just don't reuse overlapping time ranges.

### 6. Name each clip and write a title
- Names appear on screen next to the rank: **2-4 words**, punchy, internet humor in the user's style: "Bro needs glasses", "Aura recovery", "Boy's ready", "Malinoi crash". Censor swears with `*` ("sh*t").
- Title: RankReel markup with 1-2 colored words, e.g. `RANKING THE {#FFD400:FUNNIEST} DOG {#FF3B30:FAILS} 😭` (yellow `#FFD400`, red `#FF3B30`, green `#34C759`). Match the content (fails / funniest / craziest / most unexpected...).

### 7. Build the project
Write `<workdir>/picks.json` (format in the script's header) and run:
```bash
python <skill>/scripts/build_project.py <workdir>/picks.json
```
It assigns ranks and the play order with the rules above, checks the clips (RankReel's own validation when available), saves the project, and prints the plan table. Next to RankReel the project goes to `rankreel/projects/<name>.json`; standalone, it goes next to `picks.json` (or `--out <folder>`). If it reports errors (e.g. end past the video's end), it saves nothing; fix the picks and rerun. Keep the default style unless the user asked for a transition or sound (e.g. `"style": {"transition": "shake", "transition_sfx": "vibrate.wav"}`).

## What to give the user

1. The plan table from `build_project.py` (play order, rank, name, exact timestamps, length, why), plus the total duration.
2. One line on why the hook opens and why the finale closes.
3. Two or three alternative titles.
4. How to use it:
   - Next to RankReel: open RankReel (reload the page so the list refreshes) → **Load project…** → the project name. Or render directly: `cd rankreel && .venv/bin/python cli.py render projects/<name>.json`.
   - Saved through the RankReel connector: give them the `editor_url` link (it opens the draft in the editor) and the saved name (it's also under **Load project…**). No file or Import step needed.
   - Standalone: give them the project `.json` file to download, and show its contents in a ```json code block too. In RankReel they click **Import** (top bar), paste it or choose the file, and click Import; the clips download automatically. (Putting the file in `rankreel/projects/` and using **Load project…** also works.)
5. Anything you dropped or doubted (a failed link, a weak video, a clip that only works with sound).

Next to RankReel, offer to render it; don't render unprompted, since the user may want to tweak trims first.
