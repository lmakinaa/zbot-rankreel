"""RankReel CLI.

    python cli.py render projects/my_video.json
    python cli.py validate projects/my_video.json
    python cli.py title "RANKING {#FFD400:FUNNIEST} {#FF3B30:MOMENTS}"
    python cli.py make-sfx        # regenerate the built-in vibration sounds
    python cli.py demo            # render a synthetic 5-clip video (no network)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.config import ensure_dirs
from app.models import Project, parse_title_markup


def load_project(path: str) -> Project:
    try:
        return Project.model_validate_json(Path(path).read_text())
    except FileNotFoundError:
        sys.exit(f"No such file: {path}")
    except ValidationError as e:
        sys.exit(f"Invalid project file {path}:\n{e}")


def print_report(res) -> None:
    status = "unknown (some clips not downloaded)" if not res.known_total else f"{res.total_seconds:.1f}s"
    print(f"Total duration: {status}")
    for e in res.errors:
        print(f"  ERROR   {e}")
    for w in res.warnings:
        print(f"  WARNING {w}")
    for h in res.hints:
        print(f"  hint    {h}")


def cmd_validate(args) -> int:
    from app.downloader import fill_from_cache
    from app.validate import validate_project

    project = load_project(args.project)
    fill_from_cache(project)
    res = validate_project(project)
    print_report(res)
    return 1 if res.errors else 0


def cmd_render(args) -> int:
    from app.render_job import RenderError, run_render
    from app.downloader import FetchError
    from app.compose import ComposeError
    from app.validate import validate_project

    project = load_project(args.project)
    last = {"msg": None}

    def progress(f, msg):
        if msg != last["msg"]:
            print(f"[{f * 100:5.1f}%] {msg}", flush=True)
            last["msg"] = msg

    try:
        res = run_render(project, progress=progress)
    except (RenderError, FetchError, ComposeError) as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    print()
    print_report(validate_project(project))
    print(f"\nVideo:   {res.video}")
    print(f"Credits: {res.credits}")
    print(f"Project: {res.project_file}")
    return 0


def cmd_title(args) -> int:
    tokens = parse_title_markup(args.markup)
    print(json.dumps([t.model_dump() for t in tokens], ensure_ascii=False, indent=2))
    return 0


def cmd_demo(args) -> int:
    from app.render_job import run_render
    from app.synthetic import make_test_clip
    from app.config import CACHE_DIR

    d = CACHE_DIR / "demo"
    d.mkdir(parents=True, exist_ok=True)
    names = ["Dog vs vacuum", "Cat steals pizza", "Goat scream", "Parrot sings", "Hamster escape"]
    clips = []
    for rank in range(1, 6):
        p = d / f"demo_{rank}.mp4"
        if not p.exists():
            make_test_clip(p, duration=6.0, w=720, h=1280 if rank % 2 else 720,
                           audio=rank != 3, freq=300 + rank * 100)
        clips.append({"url": f"https://www.tiktok.com/@demo{rank}/video/{rank}",
                      "rank": rank, "name": names[rank - 1], "start": 0.5, "end": 6.0,
                      "local_path": str(p), "creator": f"@demo{rank}"})
    project = Project.model_validate({
        "title": "RANKING THE {#FFD400:FUNNIEST} PET {#FF3B30:MOMENTS}",
        "clips": clips, "play_order": [4, 2, 5, 3, 1],
    })
    res = run_render(project, progress=lambda f, m: print(f"[{f * 100:5.1f}%] {m}", flush=True))
    print(f"\nVideo: {res.video}\nCredits: {res.credits}")
    return 0


def cmd_make_sfx(args) -> int:
    from app.sfx_gen import ensure_default_sfx
    for path in ensure_default_sfx(force=args.force):
        print(path)
    return 0


def main(argv=None) -> int:
    ensure_dirs()
    from app.sfx_gen import ensure_default_sfx
    ensure_default_sfx()
    ap = argparse.ArgumentParser(prog="rankreel", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("render", help="download + render a project JSON")
    p.add_argument("project")
    p.set_defaults(func=cmd_render)
    p = sub.add_parser("validate", help="check durations, ranks and order")
    p.add_argument("project")
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("title", help="parse title markup to tokens")
    p.add_argument("markup")
    p.set_defaults(func=cmd_title)
    p = sub.add_parser("make-sfx", help="(re)generate the built-in vibration sounds")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.set_defaults(func=cmd_make_sfx)
    p = sub.add_parser("demo", help="render a synthetic demo (no network)")
    p.set_defaults(func=cmd_demo)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
