"""MCP server (stdio) so Claude can save draft reels for the user to review and render.

    python -m app.mcp_server

With Podman, Claude Desktop runs it inside the app container, so drafts land in
the same projects volume the web app reads:

    podman exec -i rankreel python -m app.mcp_server

Claude can list, read, check and save projects. It can't delete or render:
the user opens the draft in the editor, checks the trims, and renders.

stdout carries the MCP protocol, so nothing here may print to it.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import projects
from .config import SFX_DIR, SFX_EXTENSIONS, ensure_dirs
from .downloader import fill_from_cache
from .models import Project
from .validate import validate_project

INSTRUCTIONS = """\
RankReel turns TikTok links into a ranked-clip vertical Short. These tools save draft projects \
into the user's local RankReel app. They don't render: the user opens the draft in the editor, \
reviews the trims and clicks Render.

A project is the JSON the rankreel-clip-picker skill builds: {"title": markup or tokens, \
"clips": [{"url", "rank", "name", "start", "end"}], "play_order": [ranks], "style": {...}}. \
start/end are seconds; rank 1 is the best clip; play_order lists every rank once, in the order \
the clips play. Title markup colors words like `RANKING THE {#FFD400:FUNNIEST} {#FF3B30:FAILS}`.

The user's format, unless they ask otherwise: the most eye-catching clip (the hook) is rank #2 \
and plays FIRST; the best payoff is rank #1 and plays LAST; the other clips get ranks 3..N in a \
shuffled order in between, never a plain countdown.

Call check_project to see problems first if you like, then save_project, and give the user the \
editor_url it returns. To change an existing draft, get_project it, edit it, and save_project it \
again: that saves a new draft and leaves the original as it was."""

mcp = MCPServer("rankreel", instructions=INSTRUCTIONS, log_level="WARNING")


def editor_url(name: str) -> str:
    base = os.environ.get("RANKREEL_PUBLIC_URL", "http://127.0.0.1:8766").rstrip("/")
    return f"{base}/?project={quote(name)}"


def _to_project(project: dict[str, Any] | str) -> Project:
    try:
        p = (projects.parse_project_text(project) if isinstance(project, str)
             else projects.parse_project_data(project))
    except ValueError as e:
        raise ToolError(str(e)) from None
    sfx = p.style.transition_sfx
    if sfx and ("/" in sfx or "\\" in sfx):
        raise ToolError("style.transition_sfx must be a sound name from list_sounds, not a path.")
    return p


def _check(p: Project) -> dict[str, Any]:
    fill_from_cache(p, trust_local=False)
    res = validate_project(p)
    return {"errors": res.errors, "warnings": res.warnings, "hints": res.hints,
            "total_seconds": round(res.total_seconds, 2)}


@mcp.tool()
def list_projects() -> list[dict[str, Any]]:
    """List saved RankReel projects, newest first: name, title, clip count, last modified."""
    return projects.list_projects()


@mcp.tool()
def get_project(name: str) -> dict[str, Any]:
    """Read a saved project by name (as list_projects shows it)."""
    try:
        return projects.load_project(name).model_dump(mode="json")
    except FileNotFoundError:
        raise ToolError(f"No saved project named {name!r}. Call list_projects to see the names.") from None


@mcp.tool()
def list_sounds() -> list[str]:
    """Transition sound names that style.transition_sfx can use."""
    return sorted(p.name for p in SFX_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() in SFX_EXTENSIONS and not p.name.startswith("."))


@mcp.tool()
def check_project(project: dict[str, Any] | str) -> dict[str, Any]:
    """Check a project without saving it. Returns errors (must fix), warnings, hints and the total length.

    `project` is the project object, or its JSON text (a ```json block is fine).
    Clips the app hasn't downloaded yet are fetched when the user opens or renders the draft.
    """
    return _check(_to_project(project))


@mcp.tool()
def save_project(project: dict[str, Any] | str, name: str | None = None) -> dict[str, Any]:
    """Save a draft project into RankReel. It never overwrites: a taken name gets _2, _3...

    `project` is the project object, or its JSON text. `name` defaults to one made from the title.
    Nothing is saved if the project has errors. Returns the saved name, a link that opens it in
    the editor (give this to the user), and any warnings.
    """
    p = _to_project(project)
    result = _check(p)
    if result["errors"]:
        raise ToolError("Not saved. Fix these first:\n- " + "\n- ".join(result["errors"]))
    saved = projects.save_project(p, name, unique=True)
    return {"name": saved, "editor_url": editor_url(saved), "total_seconds": result["total_seconds"],
            "warnings": result["warnings"], "hints": result["hints"]}


def main() -> None:
    ensure_dirs()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
