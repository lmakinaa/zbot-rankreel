#!/usr/bin/env python3
"""Apply the ranking rules to picked clips and write a RankReel project.

    python build_project.py picks.json [--seed N] [--name NAME] [--out FOLDER]

picks.json:
{
  "title": "RANKING THE {#FFD400:FUNNIEST} DOG {#FF3B30:FAILS} 😭",
  "clips": [
    {"url": "...", "start": 5.7, "end": 9.8, "name": "Bro needs glasses", "role": "hook",   "why": "..."},
    {"url": "...", "start": 25.0, "end": 28.6, "name": "...",            "role": "finale", "why": "..."},
    {"url": "...", "start": 0.0, "end": 4.5, "name": "...",              "why": "..."}
  ],
  "style": {"transition": "punch"}          (optional RankReel style overrides)
}

Rules (the user's format):
  - role "hook"   -> rank #2, plays FIRST (the most eye-catching clip opens the video)
  - role "finale" -> rank #1, plays LAST
  - every other clip gets a random rank 3..N and a shuffled position in between;
    the shuffle is re-drawn until the middle isn't simply in rank order.

Next to RankReel: writes rankreel/projects/<name>.json (shows up under "Load project…")
and validates it with RankReel's own checks. Standalone (e.g. Claude.ai): writes the same
file format next to picks.json (or --out) for the user to download into rankreel/projects/.
Prints the plan as a table either way.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import find_rankreel, fmt  # noqa: E402


def assign(clips: list[dict], rng: random.Random) -> tuple[list[dict], list[int]]:
    hooks = [c for c in clips if c.get("role") == "hook"]
    finales = [c for c in clips if c.get("role") == "finale"]
    n = len(clips)
    if n >= 2 and (len(hooks) != 1 or len(finales) != 1):
        sys.exit(f"Need exactly one 'hook' and one 'finale' clip (got {len(hooks)} hook, {len(finales)} finale).")
    if n == 1:
        clips[0]["rank"] = 1
        return clips, [1]
    hook, finale = hooks[0], finales[0]
    hook["rank"], finale["rank"] = 2, 1
    middle = [c for c in clips if c is not hook and c is not finale]
    ranks = list(range(3, n + 1))
    def looks_shuffled(seq: list[int]) -> bool:
        # No three neighbours counting up or down by one (e.g. 4,5,6 or 7,6,5).
        return not any(seq[i + 1] - seq[i] == seq[i + 2] - seq[i + 1] and abs(seq[i + 1] - seq[i]) == 1
                       for i in range(len(seq) - 2))

    order = ranks[:]
    for _ in range(200):
        rng.shuffle(ranks)
        order = ranks[:]
        rng.shuffle(order)
        # Also check the whole sequence as it plays, including the hook (#2) at the start.
        if looks_shuffled([2, *order, 1]):
            break
    for c, r in zip(middle, ranks):
        c["rank"] = r
    return clips, [2, *order, 1]


# --------------------------------------------------------------------------- standalone helpers
_GROUP = re.compile(r"\{\s*(#[0-9a-fA-F]{3,6})\s*:([^{}]*)\}")


def parse_title(markup: str) -> list[dict]:
    """RankReel title markup -> tokens: `RANKING {#FFD400:FUNNIEST} MOMENTS`."""
    tokens, pos = [], 0
    for m in _GROUP.finditer(markup):
        tokens += [{"text": w, "color": "#FFFFFF"} for w in markup[pos:m.start()].split()]
        color = m.group(1).upper()
        if len(color) == 4:
            color = "#" + "".join(c * 2 for c in color[1:])
        tokens += [{"text": w, "color": color} for w in m.group(2).split()]
        pos = m.end()
    tokens += [{"text": w, "color": "#FFFFFF"} for w in markup[pos:].split()]
    return tokens


def slug(text: str, n: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()[:n].strip("_") or "picks"


def standalone_checks(clips: list[dict], durations: dict[str, float]) -> tuple[list[str], list[str], float]:
    errors, warnings, total = [], [], 0.0
    for c in clips:
        s, e = float(c["start"]), float(c["end"])
        label = f"#{c['rank']} {c.get('name', '')}".strip()
        if not str(c.get("url", "")).startswith(("http://", "https://")):
            errors.append(f"{label}: needs the TikTok link (url).")
        if e <= s:
            errors.append(f"{label}: end must be after start.")
        dur = durations.get(c.get("url", ""))
        if dur and e > dur + 0.05:
            errors.append(f"{label}: end {e:.2f}s is past the video's end ({dur:.2f}s).")
        if e - s > 8:
            warnings.append(f"{label} is {e - s:.1f}s; trend clips are short (3-7s).")
        total += max(0.0, e - s)
    if total > 40:
        warnings.append(f"Total {total:.1f}s exceeds 40s")
    return errors, warnings, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("picks")
    ap.add_argument("--seed", type=int, help="fix the shuffle (default: random)")
    ap.add_argument("--name", help="project file name (default: from the title)")
    ap.add_argument("--out", help="folder to save the project in (default: rankreel/projects, "
                                  "or next to picks.json when RankReel isn't installed)")
    ap.add_argument("--rankreel", help="path to the rankreel folder (auto-detected)")
    ap.add_argument("--standalone", action="store_true", help="ignore RankReel even if present")
    a = ap.parse_args()

    picks = json.loads(Path(a.picks).read_text())
    clips = [dict(c) for c in picks["clips"]]
    if not 1 <= len(clips) <= 10:
        sys.exit(f"RankReel takes 1-10 clips (got {len(clips)}).")
    clips, order = assign(clips, random.Random(a.seed))
    title = picks.get("title") or "RANKING THE {#FFD400:CRAZIEST} {#FF3B30:MOMENTS}"
    root = None if a.standalone else find_rankreel(a.rankreel)

    if root:  # validate with RankReel itself
        sys.path.insert(0, str(root))
        from app.config import PROJECTS_DIR, ensure_dirs
        from app.downloader import fill_from_cache
        from app.models import Project
        from app.validate import validate_project

        ensure_dirs()
        project = Project.model_validate({
            "title": title,
            "clips": [{"url": c["url"], "start": c["start"], "end": c["end"], "rank": c["rank"],
                       "name": c.get("name", "")} for c in clips],
            "play_order": order, "style": picks.get("style") or {},
        })
        fill_from_cache(project)
        res = validate_project(project)
        errors, warnings, hints, total = res.errors, res.warnings, res.hints, res.total_seconds
        data = project.model_dump(mode="json")
        for c in data["clips"]:  # machine-local fields; the app re-fills them from its cache
            for k in ("local_path", "source_duration", "creator", "webpage_url"):
                c[k] = None
        out_dir = Path(a.out) if a.out else PROJECTS_DIR
        title_text = project.title_text()
    else:  # standalone: same file format RankReel saves
        durations = {}
        info_files = list(Path(a.picks).resolve().parent.glob("*/info.json"))
        for f in info_files:
            i = json.loads(f.read_text())
            if i.get("url"):
                durations[i["url"]] = i.get("duration")
        errors, warnings, total = standalone_checks(clips, durations)
        hints = []
        data = {
            "title": parse_title(title),
            "clips": [{"id": uuid.uuid4().hex[:12], "url": c["url"], "start": float(c["start"]),
                       "end": float(c["end"]), "rank": c["rank"], "name": c.get("name", ""),
                       "local_path": None, "source_duration": None, "creator": None, "webpage_url": None}
                      for c in clips],
            "play_order": order,
            "style": picks.get("style") or {},
            "max_seconds_warning": 40.0,
        }
        out_dir = Path(a.out) if a.out else Path(a.picks).resolve().parent
        title_text = " ".join(t["text"] for t in data["title"])

    if errors:  # don't hand over a project that RankReel would reject
        for e in errors:
            print(f"ERROR: {e}")
        print("\nNot saved: fix these in picks.json and run again.")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    name = slug(a.name or title_text or "picks")
    out = out_dir / f"{name}.json"
    if out.exists() and not a.name:
        k = 2
        while (out_dir / f"{name}_{k}.json").exists():
            k += 1
        out = out_dir / f"{name}_{k}.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    by_rank = {c["rank"]: c for c in clips}
    print("| Plays | Rank | Name | Clip | Length | Source | Why |")
    print("|---|---|---|---|---|---|---|")
    for i, r in enumerate(order, start=1):
        c = by_rank[r]
        tag = " (hook)" if c.get("role") == "hook" else " (finale)" if c.get("role") == "finale" else ""
        print(f"| {i} | #{r}{tag} | {c.get('name', '')} | {fmt(float(c['start']))} → {fmt(float(c['end']))} "
              f"| {float(c['end']) - float(c['start']):.1f}s | {c['url']} | {c.get('why', '')} |")
    print(f"\nTotal: {total:.1f}s")
    for w in warnings:
        print(f"WARNING: {w}")
    for h in hints:
        print(f"hint: {h}")
    print(f"\nProject saved: {out}")
    if root:
        print(f"Open RankReel → Load project… → {out.stem}")
    else:
        print("RankReel isn't installed here: give the user this file. In RankReel they click Import "
              "and paste it (or choose the file).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
