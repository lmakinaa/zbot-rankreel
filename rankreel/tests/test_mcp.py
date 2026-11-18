"""MCP server tools (offline). Projects are written to a temporary folder."""

import json
import sys
from pathlib import Path

import pytest

from app import config
from app import mcp_server as srv
from mcp.server.mcpserver.exceptions import ToolError


@pytest.fixture(autouse=True)
def projects_dir(tmp_path, monkeypatch, project_factory):
    global _factory
    _factory = project_factory
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    return tmp_path


def _data(**kw):
    return _factory(n=3, **kw).model_dump(mode="json")


def test_save_list_get_round_trip(projects_dir):
    out = srv.save_project(_data(), name="Dog fails")
    assert out["name"] == "dog_fails"
    assert out["editor_url"] == "http://127.0.0.1:8766/?project=dog_fails"
    assert (projects_dir / "dog_fails.json").is_file()
    listed = srv.list_projects()
    assert [(p["name"], p["clips"]) for p in listed] == [("dog_fails", 3)]
    assert listed[0]["title"] == "RANKING FUNNIEST MOMENTS"
    got = srv.get_project("dog_fails")
    assert [c["url"] for c in got["clips"]] == [c["url"] for c in _data()["clips"]]


def test_save_never_overwrites():
    assert srv.save_project(_data(), name="reel")["name"] == "reel"
    assert srv.save_project(_data(), name="reel")["name"] == "reel_2"
    assert srv.save_project(_data())["name"] == "ranking_funniest_moments"


def test_save_strips_local_paths(projects_dir):
    data = _data()
    data["clips"][0]["local_path"] = "/etc/passwd"
    name = srv.save_project(data)["name"]
    saved = json.loads((projects_dir / f"{name}.json").read_text())
    assert all(c["local_path"] is None for c in saved["clips"])


def test_save_accepts_fenced_json_text():
    text = "Here you go:\n```json\n" + json.dumps({"project": _data()}) + "\n```"
    assert srv.save_project(text, name="pasted")["name"] == "pasted"


def test_invalid_project_is_not_saved(projects_dir):
    bad = _data(order=[1, 2])  # rank 3 missing from the play order
    with pytest.raises(ToolError, match="Not saved"):
        srv.save_project(bad, name="bad")
    assert not list(projects_dir.iterdir())
    assert srv.check_project(bad)["errors"]


def test_malformed_input_reports_the_problem():
    with pytest.raises(ToolError, match="needs \"title\" and \"clips\""):
        srv.save_project({"title": "x"})
    with pytest.raises(ToolError, match="isn't valid JSON"):
        srv.check_project("{not json")


def test_sound_paths_are_rejected():
    with pytest.raises(ToolError, match="sound name"):
        srv.save_project(_data(style={"transition_sfx": "../../etc/passwd"}))


def test_unknown_sound_is_not_saved():
    with pytest.raises(ToolError, match="nope.wav"):
        srv.save_project(_data(style={"transition_sfx": "nope.wav"}))


def test_get_unknown_and_unsafe_names():
    for name in ("missing", "../secret", ".hidden"):
        with pytest.raises(ToolError, match="No saved project"):
            srv.get_project(name)


def test_editor_url_uses_public_url(monkeypatch):
    monkeypatch.setenv("RANKREEL_PUBLIC_URL", "http://localhost:9000/")
    assert srv.editor_url("a b") == "http://localhost:9000/?project=a%20b"


def test_stdio_server_lists_tools():
    import anyio
    from mcp import Client, StdioServerParameters

    root = Path(__file__).resolve().parent.parent
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(root))

    async def main():
        async with Client(params) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert names == {"list_projects", "get_project", "list_sounds", "check_project", "save_project"}
            res = await c.call_tool("list_sounds", {})
            assert not res.is_error

    anyio.run(main)
