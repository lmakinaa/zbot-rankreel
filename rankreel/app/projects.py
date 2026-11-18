"""Saved projects in ``projects/``: list, load, parse pasted JSON, save.

Shared by the web API (``app.main``) and the MCP server (``app.mcp_server``).
Errors are plain exceptions; each caller turns them into its own error type.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from . import config
from .models import Project
from .render_job import slugify

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def safe_file(base: Path, name: str) -> Path:
    """A file directly inside ``base``; raises FileNotFoundError for anything else."""
    if not SAFE_NAME.match(name) or name.startswith("."):
        raise FileNotFoundError(name)
    p = (base / name).resolve()
    if p.parent != base.resolve() or not p.is_file():
        raise FileNotFoundError(name)
    return p


def list_projects() -> list[dict]:
    """Saved projects, newest first."""
    items = []
    for p in sorted(config.PROJECTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
            title = " ".join(t["text"] for t in data.get("title", []))
            clips = len(data.get("clips") or [])
        except (OSError, json.JSONDecodeError, TypeError, KeyError, AttributeError):
            title, clips = "", 0
        items.append({
            "name": p.stem, "title": title, "clips": clips,
            "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return items


def load_project(name: str) -> Project:
    """Raises FileNotFoundError if there's no saved project with that name."""
    p = safe_file(config.PROJECTS_DIR, f"{name}.json")
    return Project.model_validate_json(p.read_text())


def extract_json(text: str) -> str:
    """Accept raw JSON, or JSON inside a ```json code block / surrounded by chat text."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith("{") and "{" in t and "}" in t:
        t = t[t.index("{"): t.rindex("}") + 1]
    return t


def parse_project_data(data: object) -> Project:
    """Validate a decoded project (or ``{"project": {...}}``); raises ValueError with a readable message."""
    if isinstance(data, dict) and isinstance(data.get("project"), dict):
        data = data["project"]
    if not isinstance(data, dict) or "clips" not in data:
        raise ValueError("That JSON isn't a RankReel project (it needs \"title\" and \"clips\").")
    try:
        project = Project.model_validate(data)
    except ValidationError as e:
        problems = []
        for err in e.errors()[:5]:
            where = ".".join(str(x) for x in err["loc"])
            if err["loc"][:1] == ("clips",) and len(err["loc"]) > 1 and isinstance(err["loc"][1], int):
                where = f"clip {err['loc'][1] + 1}" + ("." + ".".join(map(str, err["loc"][2:])) if err["loc"][2:] else "")
            problems.append(f"{where}: {err['msg']}")
        raise ValueError("The project has problems:\n- " + "\n- ".join(problems)) from None
    for c in project.clips:  # never trust machine-local paths from outside
        c.local_path = None
    return project


def parse_project_text(text: str) -> Project:
    """Check pasted project JSON (e.g. from the clip-picker skill); raises ValueError."""
    if not text.strip():
        raise ValueError("Paste the project JSON first.")
    try:
        data = json.loads(extract_json(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"That isn't valid JSON (line {e.lineno}, column {e.colno}): {e.msg}.") from None
    return parse_project_data(data)


def save_project(project: Project, name: str | None = None, unique: bool = False) -> str:
    """Write ``projects/<name>.json`` and return the name used.

    ``unique=True`` never overwrites: it adds _2, _3... if the name is taken.
    """
    base = slugify(name or project.title_text() or "project", 64)
    out, k = base, 2
    while unique and (config.PROJECTS_DIR / f"{out}.json").exists():
        out, k = f"{base}_{k}", k + 1
    project = project.model_copy(deep=True)
    for c in project.clips:  # machine-local; the app re-fills it from its cache
        c.local_path = None
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.PROJECTS_DIR / f"{out}.json").write_text(project.model_dump_json(indent=2))
    return out
