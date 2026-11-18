#!/usr/bin/env python3
"""End-to-end test: Claude Desktop using RankReel through its MCP server.

Claude Desktop launches the MCP server from its config, gives Claude the server's tools and
instructions, and runs every tool call Claude makes. This script does the same thing with the
Claude API, sends prompts a user would type in Desktop, and then checks the result in the
running app (not in Claude's reply).

Scenarios:
  new-reel    "I picked these 5 moments, make the ranking and save it."
              Checks: one new draft with the right clips and timestamps, the hook ranked #2
              and playing first, #1 playing last, a colored title, no validation errors, and
              the editor link in Claude's reply works.
  edit-draft  "Open that draft, switch to the shake transition with the vibrate sound, rename
              the goat clip, save it as a new draft." (a fresh conversation, like a new chat)
              Checks: Claude read the draft back, the new draft has the changes and nothing
              else, and the original is untouched.

Setup (on the computer that runs the RankReel container):
  python3 -m venv .venv-e2e && .venv-e2e/bin/pip install -r e2e/requirements.txt
  export ANTHROPIC_API_KEY=...        # or sign in with `ant auth login`

Run:
  .venv-e2e/bin/python e2e/claude_desktop_e2e.py            # both scenarios; deletes the drafts it made
  .venv-e2e/bin/python e2e/claude_desktop_e2e.py --keep     # leave the drafts in RankReel to show them
  .venv-e2e/bin/python e2e/claude_desktop_e2e.py --offline  # no Claude: replay the expected tool calls
                                                            # (checks the server, the app and this script)

The live run calls the Claude API (Claude Opus 5.5) and is billed to that key: two short
conversations, typically a few tool calls each.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from mcp import Client, StdioServerParameters

MODEL = "claude-opus-5-5"
CONTAINER = "rankreel"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# What Claude Desktop tells the model about itself is its own; this stands in for it.
DESKTOP_SYSTEM = (
    "You are Claude, running in the Claude Desktop app. The user connected the RankReel app "
    "as an MCP server. Use its tools to do what they ask, and reply briefly, the way you would "
    "in a chat.\n\nInstructions from the RankReel MCP server:\n{instructions}"
)

# --------------------------------------------------------------------------- the reel
CLIPS = [  # (url, start, end, what happens)
    ("https://www.tiktok.com/@creator2/video/2222222222222222222", 3.0, 9.0, "cat steals pizza"),
    ("https://www.tiktok.com/@creator1/video/1111111111111111111", 1.5, 7.0, "dog vs vacuum"),
    ("https://www.tiktok.com/@creator3/video/3333333333333333333", 0.0, 5.5, "goat scream"),
    ("https://www.tiktok.com/@creator4/video/4444444444444444444", 2.0, 8.0, "parrot sings"),
    ("https://www.tiktok.com/@creator5/video/5555555555555555555", 10.0, 15.0, "hamster escape"),
]
HOOK_URL, FINALE_URL, GOAT_URL = CLIPS[0][0], CLIPS[1][0], CLIPS[2][0]
NEW_GOAT_NAME = "Goat lost it"

NEW_REEL_PROMPT = """\
I picked the moments for a funny pets ranking Short. Can you make it and save it to RankReel?

- https://www.tiktok.com/@creator2/video/2222222222222222222 from 0:03 to 0:09, a cat steals a pizza. \
This is the most eye-catching one, so it's the hook.
- https://www.tiktok.com/@creator1/video/1111111111111111111 from 0:01.5 to 0:07, dog vs vacuum. \
Best payoff, this one is #1.
- https://www.tiktok.com/@creator3/video/3333333333333333333 from 0:00 to 0:05.5, a goat screaming
- https://www.tiktok.com/@creator4/video/4444444444444444444 from 0:02 to 0:08, a parrot singing
- https://www.tiktok.com/@creator5/video/5555555555555555555 from 0:10 to 0:15, a hamster escaping

Title: something like RANKING THE FUNNIEST PET MOMENTS, with a couple of words colored. \
Give each clip a short funny name."""

EDIT_PROMPT = f"""\
Open my most recent funny pets draft in RankReel. Switch the transition to shake with the vibrate \
sound, and rename the goat clip to "{NEW_GOAT_NAME}". Save it as a new draft and leave the \
original as it is."""


# --------------------------------------------------------------------------- app access
@dataclass
class App:
    base: str
    engine: str

    def get(self, path: str) -> Any:
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.loads(r.read())

    def post(self, path: str, body: Any) -> Any:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def status(self, url: str) -> int:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def names(self) -> list[str]:
        return [p["name"] for p in self.get("/api/projects")]

    def delete(self, name: str) -> None:
        if SAFE_NAME.match(name):
            subprocess.run([self.engine, "exec", CONTAINER, "rm", "-f", f"/app/projects/{name}.json"], check=True)


def pick_engine(wanted: str) -> str:
    for engine in ([wanted] if wanted != "auto" else ["docker", "podman"]):
        if not shutil.which(engine):
            continue
        r = subprocess.run([engine, "inspect", "-f", "{{.State.Running}}", CONTAINER],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip() == "true":
            return engine
    sys.exit(f"The '{CONTAINER}' container isn't running. Start it first "
             "(`docker compose up -d` or `podman start rankreel`).")


# --------------------------------------------------------------------------- checks
@dataclass
class Run:
    """What happened in one conversation."""
    reply: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)


class Checks:
    def __init__(self, scenario: str) -> None:
        self.scenario, self.failed = scenario, 0

    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ({detail})" if detail and not ok else ""))
        self.failed += not ok
        return ok


def by_url(project: dict) -> dict[str, dict]:
    return {c["url"]: c for c in project["clips"]}


def check_new_reel(app: App, run: Run, created: list[str], c: Checks) -> str | None:
    if not c.check(len(created) == 1, "exactly one new draft saved", f"new drafts: {created}"):
        return None
    name = created[0]
    p = app.get(f"/api/projects/{name}")
    clips = by_url(p)
    c.check(set(clips) == {u for u, *_ in CLIPS}, "the draft has the 5 given clips", f"got {sorted(clips)}")
    times_ok = all(u in clips and abs(clips[u]["start"] - s) < 0.06 and abs(clips[u]["end"] - e) < 0.06
                   for u, s, e, _ in CLIPS)
    c.check(times_ok, "every start/end matches what the user asked for",
            json.dumps({u[-4:]: (clips[u]["start"], clips[u]["end"]) for u in clips}))
    order = p.get("play_order") or []
    ranks = {u: clips[u]["rank"] for u in clips}
    c.check(sorted(order) == list(range(1, len(CLIPS) + 1)), "play order uses every rank once", str(order))
    c.check(ranks.get(HOOK_URL) == 2 and order[:1] == [2], "the hook (cat) is #2 and plays first",
            f"cat rank {ranks.get(HOOK_URL)}, order {order}")
    c.check(ranks.get(FINALE_URL) == 1 and order[-1:] == [1], "the #1 (dog) plays last",
            f"dog rank {ranks.get(FINALE_URL)}, order {order}")
    words = " ".join(t["text"] for t in p["title"]).upper()
    colored = [t["text"] for t in p["title"] if t["color"].upper() != "#FFFFFF"]
    c.check("FUNNIEST" in words or "PET" in words, "title fits the brief", words)
    c.check(len(colored) >= 1, "title has colored words", words)
    c.check(all(cl["name"].strip() for cl in p["clips"]), "every clip has a name")
    v = app.post("/api/validate", p)
    c.check(not v["errors"], "RankReel's own validation finds no errors", "; ".join(v["errors"]))
    c.check(f"?project={name}" in run.reply, "Claude's reply includes the editor link", run.reply[-300:])
    c.check(app.status(f"{app.base}/?project={name}") == 200, "the editor link opens")
    return name


def check_edit(app: App, run: Run, created: list[str], c: Checks, original: str, before: dict) -> None:
    called = [n for n, _ in run.tool_calls]
    c.check("get_project" in called, "Claude read the existing draft (get_project)", str(called))
    c.check(app.get(f"/api/projects/{original}") == before, "the original draft is unchanged")
    if not c.check(len(created) == 1, "exactly one new draft saved", f"new drafts: {created}"):
        return
    p = app.get(f"/api/projects/{created[0]}")
    c.check(p["style"]["transition"] == "shake", "transition is shake", p["style"]["transition"])
    c.check(p["style"]["transition_sfx"] == "vibrate.wav", "sound is vibrate.wav", str(p["style"]["transition_sfx"]))
    goat = by_url(p).get(GOAT_URL, {})
    c.check(goat.get("name", "").strip().lower() == NEW_GOAT_NAME.lower(), "the goat clip is renamed",
            goat.get("name", ""))
    same = lambda d: ({u: (x["start"], x["end"], x["rank"]) for u, x in by_url(d).items()}, d["play_order"])  # noqa: E731
    c.check(same(p) == same(before), "clips, timestamps, ranks and order are otherwise the same")
    others = {u: x["name"] for u, x in by_url(p).items() if u != GOAT_URL}
    c.check(others == {u: x["name"] for u, x in by_url(before).items() if u != GOAT_URL},
            "the other clip names are unchanged")
    c.check(f"?project={created[0]}" in run.reply, "Claude's reply includes the new editor link", run.reply[-300:])


# --------------------------------------------------------------------------- talking to Claude
async def ask_claude(mcp: Client, prompt: str) -> Run:
    """One Desktop conversation: the server's tools and instructions, one user message."""
    from anthropic import AsyncAnthropic
    from anthropic.lib.tools.mcp import async_mcp_tool

    client = AsyncAnthropic()
    tools = (await mcp.list_tools()).tools
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        max_iterations=15,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # route a refused request to a fallback model instead of failing
        system=DESKTOP_SYSTEM.format(instructions=mcp.instructions or ""),
        tools=[async_mcp_tool(t, mcp.session) for t in tools],
        messages=[{"role": "user", "content": prompt}],
    )
    run, last = Run(), None
    async for message in runner:
        last = message
        for block in message.content:
            if block.type == "tool_use":
                run.tool_calls.append((block.name, block.input))
                print(f"    → {block.name}({_short(block.input)})")
            elif block.type == "text" and block.text.strip():
                print("    Claude: " + block.text.strip().replace("\n", "\n            "))
    if last is None or last.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined: {getattr(last, 'stop_details', None)}")
    run.reply = "".join(b.text for b in last.content if b.type == "text")
    return run


def _short(args: dict) -> str:
    s = json.dumps(args, ensure_ascii=False)
    return s if len(s) < 140 else s[:137] + "..."


# --------------------------------------------------------------------------- offline replay
async def _call(mcp: Client, run: Run, name: str, args: dict) -> Any:
    run.tool_calls.append((name, args))
    print(f"    → {name}({_short(args)})")
    res = await mcp.call_tool(name, args)
    if res.is_error:
        raise RuntimeError(f"{name} failed: {res.content[0].text}")
    out = res.structured_content
    return out.get("result", out) if isinstance(out, dict) and set(out) == {"result"} else out


async def offline_new_reel(mcp: Client) -> Run:
    run = Run()
    names = ["Pizza thief", "Vacuum war", "Goat scream", "Parrot idol", "Great escape"]
    ranks = [2, 1, 4, 3, 5]
    project = {"title": "RANKING THE {#FFD400:FUNNIEST} PET {#FF3B30:MOMENTS}",
               "clips": [{"url": u, "start": s, "end": e, "rank": r, "name": n}
                         for (u, s, e, _), r, n in zip(CLIPS, ranks, names)],
               "play_order": [2, 4, 5, 3, 1]}
    await _call(mcp, run, "check_project", {"project": project})
    saved = await _call(mcp, run, "save_project", {"project": project})
    run.reply = f"Saved! Open it here: {saved['editor_url']}"
    return run


async def offline_edit(mcp: Client) -> Run:
    run = Run()
    drafts = await _call(mcp, run, "list_projects", {})
    name = next(d["name"] for d in drafts if "PET" in d["title"].upper())
    p = await _call(mcp, run, "get_project", {"name": name})
    p["style"].update(transition="shake", transition_sfx="vibrate.wav")
    for c in p["clips"]:
        if c["url"] == GOAT_URL:
            c["name"] = NEW_GOAT_NAME
    saved = await _call(mcp, run, "save_project", {"project": p})
    run.reply = f"Done, saved as a new draft: {saved['editor_url']}"
    return run


# --------------------------------------------------------------------------- main
async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="replay the expected tool calls instead of asking Claude")
    ap.add_argument("--keep", action="store_true", help="keep the drafts this run saves")
    ap.add_argument("--engine", default="auto", choices=["auto", "docker", "podman"])
    ap.add_argument("--url", default="http://127.0.0.1:8766", help="where the RankReel app answers")
    a = ap.parse_args()

    if not a.offline:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            sys.exit("Install the test's requirements first: pip install -r e2e/requirements.txt")
        # The SDK reads ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN, or a profile from `ant auth login`.
        profiles = Path.home() / ".config" / "anthropic"
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (profiles.is_dir() and any(profiles.iterdir()))):
            sys.exit("No Claude API credentials: export ANTHROPIC_API_KEY=... (or run `ant auth login`), "
                     "or use --offline to replay the expected tool calls without Claude.")

    app = App(a.url.rstrip("/"), pick_engine(a.engine))
    try:
        app.get("/api/projects")
    except OSError as e:
        sys.exit(f"RankReel isn't answering at {app.base}: {e}")

    # Exactly how the Claude Desktop config starts the server.
    server = StdioServerParameters(command=app.engine, args=["exec", "-i", CONTAINER, "python", "-m", "app.mcp_server"])
    print(f"MCP server: {app.engine} exec -i {CONTAINER} python -m app.mcp_server")
    print(f"Mode: {'offline replay (no Claude)' if a.offline else f'live, {MODEL}'}\n")

    made: list[str] = []
    failed = 0
    try:
        async with Client(server) as mcp:
            print(f"Tools offered to Claude: {', '.join(sorted(t.name for t in (await mcp.list_tools()).tools))}\n")

            async def scenario(title: str, prompt: str, offline: Callable[[Client], Awaitable[Run]]):
                print(f"== {title}\n  User: " + prompt.replace("\n", "\n        ") + "\n")
                before = set(app.names())
                run = await offline(mcp) if a.offline else await ask_claude(mcp, prompt)
                created = [n for n in app.names() if n not in before]
                made.extend(created)
                print()
                return run, created

            c1 = Checks("new-reel")
            run, created = await scenario("new-reel", NEW_REEL_PROMPT, offline_new_reel)
            original = check_new_reel(app, run, created, c1)
            failed += c1.failed

            c2 = Checks("edit-draft")
            if original:
                before = app.get(f"/api/projects/{original}")
                print()
                run, created = await scenario("edit-draft", EDIT_PROMPT, offline_edit)
                check_edit(app, run, created, c2, original, before)
            else:
                c2.check(False, "skipped: new-reel didn't produce a draft")
            failed += c2.failed
    finally:
        if made and not a.keep:
            for n in made:
                app.delete(n)
            print(f"\nCleaned up the drafts this run saved: {', '.join(made)}")
        elif made:
            print("\nKept these drafts (open them in RankReel):")
            for n in made:
                print(f"  {app.base}/?project={n}")

    print(f"\n{'ALL CHECKS PASSED' if not failed else f'{failed} CHECK(S) FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
