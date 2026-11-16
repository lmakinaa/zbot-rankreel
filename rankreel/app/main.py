"""FastAPI app: serves the UI and the local API. Run with ``python -m app.main``."""

from __future__ import annotations

import io
import json
import re
from datetime import datetime
from pathlib import Path

import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .config import (BRAND_DIR, DOWNLOAD_DIR, OUTPUT_DIR, PROJECTS_DIR, SFX_DIR, SFX_EXTENSIONS, STATIC_DIR,
                     WORK_DIR, ensure_dirs)
from .downloader import FetchError, fetch, fill_from_cache
from .models import Project, parse_title_markup
from .overlay import render_overlay
from .render_job import JobManager, slugify
from .probe import probe
from .sfx_gen import DEFAULT_SFX, SOUNDS, ensure_default_sfx, prepare_sound

BUILTIN_SFX = {*SOUNDS, DEFAULT_SFX}  # listed as built-in and protected from deletion
from .validate import validate_project

ensure_dirs()
ensure_default_sfx()
app = FastAPI(title="RankReel", docs_url="/api/docs", redoc_url=None)
jobs = JobManager()

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SFX_EXT = SFX_EXTENSIONS
MAX_SFX_BYTES = 10 * 1024 * 1024
MAX_SFX_SECONDS = 10.0


def _safe_file(base: Path, name: str) -> Path:
    if not _SAFE_NAME.match(name) or name.startswith("."):
        raise HTTPException(404)
    p = (base / name).resolve()
    if p.parent != base.resolve() or not p.is_file():
        raise HTTPException(404)
    return p


def _api_project(project: Project) -> Project:
    """Requests from the page may only use cached clips and sounds in assets/sfx."""
    fill_from_cache(project, trust_local=False)
    sfx = project.style.transition_sfx
    if sfx and ("/" in sfx or "\\" in sfx):
        raise HTTPException(422, "Pick a transition sound from the list.")
    _check_avatar(project)
    return project


def _check_avatar(project: Project) -> None:
    av = project.style.channel_avatar
    if av and ("/" in av or "\\" in av):
        raise HTTPException(422, "Upload the avatar picture instead of giving a path.")


# --------------------------------------------------------------------------- UI + files
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/media/{name}", include_in_schema=False)
def media(name: str):
    return FileResponse(_safe_file(DOWNLOAD_DIR, name), media_type="video/mp4")


@app.get("/output/{name}", include_in_schema=False)
def output(name: str, download: bool = False):
    p = _safe_file(OUTPUT_DIR, name)
    return FileResponse(p, filename=p.name if download else None)


# --------------------------------------------------------------------------- clips
class FetchIn(BaseModel):
    url: str


@app.post("/api/clips/fetch")
async def clips_fetch(body: FetchIn):
    try:
        meta = await run_in_threadpool(fetch, body.url)
    except FetchError as e:
        raise HTTPException(422, str(e))
    return {
        "url": body.url,
        "webpage_url": meta.webpage_url,
        "duration": meta.duration,
        "creator": meta.handle,
        "title": meta.title,
        "has_audio": meta.has_audio,
        "preview_url": f"/media/{meta.preview_file or meta.file}",
    }


# --------------------------------------------------------------------------- validate / preview
@app.post("/api/validate")
def api_validate(project: Project):
    _api_project(project)
    return validate_project(project).model_dump()


class PreviewIn(BaseModel):
    project: Project
    step: int = -1  # -1: numbers only (before anything plays)


@app.post("/api/preview-overlay")
def preview_overlay(body: PreviewIn):
    p = body.project
    _check_avatar(p)
    step = max(-1, min(body.step, len(p.play_order) - 1))
    if sorted(p.play_order) != sorted(c.rank for c in p.clips):
        step = -1  # order invalid: show numbers only rather than failing
    try:
        img = render_overlay(p, step)
    except FileNotFoundError as e:
        raise HTTPException(422, str(e))
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return Response(buf.getvalue(), media_type="image/png")


class MarkupIn(BaseModel):
    markup: str


@app.post("/api/title/parse")
def title_parse(body: MarkupIn):
    try:
        return [t.model_dump() for t in parse_title_markup(body.markup)]
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/sfx")
def list_sfx():
    files = sorted(p.name for p in SFX_DIR.iterdir()
                   if p.is_file() and p.suffix.lower() in _SFX_EXT and not p.name.startswith("."))
    return [{"name": n, "builtin": n in BUILTIN_SFX} for n in files]


def _unique_sfx_name(stem: str, ext: str) -> str:
    name, k = f"{stem}{ext}", 2
    while name in BUILTIN_SFX or (SFX_DIR / name).exists():
        name, k = f"{stem}_{k}{ext}", k + 1
    return name


@app.post("/api/sfx")
async def upload_sfx(request: Request, filename: str):
    """Upload a custom transition sound. The file is the raw request body."""
    ext = Path(filename).suffix.lower()
    if ext not in _SFX_EXT:
        raise HTTPException(422, f"Use an audio file: {', '.join(sorted(_SFX_EXT))}.")
    if int(request.headers.get("content-length") or 0) > MAX_SFX_BYTES:
        raise HTTPException(413, "That file is over 10 MB; transition sounds should be short.")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = WORK_DIR / f"upload-{uuid.uuid4().hex}{ext}"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_SFX_BYTES:
                    raise HTTPException(413, "That file is over 10 MB; transition sounds should be short.")
                f.write(chunk)
        if size == 0:
            raise HTTPException(422, "The file is empty.")
        try:
            info = await run_in_threadpool(probe, tmp)
        except RuntimeError:
            raise HTTPException(422, "Couldn't read that file as audio.")
        if not info.has_audio:
            raise HTTPException(422, "That file has no audio.")
        if info.duration > MAX_SFX_SECONDS:
            raise HTTPException(
                422, f"That sound is {info.duration:.1f}s; keep transition sounds under {MAX_SFX_SECONDS:g}s "
                     "(trim it first).")
        # Saved as a cut-ready WAV: leading silence trimmed so the sound hits exactly on
        # the cut (in sync with the visual transition), ends de-clicked, level matched.
        name = _unique_sfx_name(slugify(Path(filename).stem, 40), ".wav")
        try:
            duration = await run_in_threadpool(prepare_sound, tmp, SFX_DIR / name)
        except ValueError as e:
            raise HTTPException(422, f"That sound can't be used: {e}.")
    finally:
        tmp.unlink(missing_ok=True)
    return {"name": name, "duration": round(duration, 3)}


@app.delete("/api/sfx/{name}")
def delete_sfx(name: str):
    if name in BUILTIN_SFX:
        raise HTTPException(422, "Built-in sounds can't be deleted.")
    p = _safe_file(SFX_DIR, name)
    if p.suffix.lower() not in _SFX_EXT:
        raise HTTPException(404)
    p.unlink()
    return {"deleted": name}


MAX_AVATAR_BYTES = 15 * 1024 * 1024


@app.post("/api/avatar")
async def upload_avatar(request: Request, filename: str = "avatar"):
    """Upload a channel avatar (raw image body). Saved square-cropped as a JPEG in assets/brand."""
    if int(request.headers.get("content-length") or 0) > MAX_AVATAR_BYTES:
        raise HTTPException(413, "That picture is over 15 MB.")
    data = await request.body()
    if not data:
        raise HTTPException(422, "The file is empty.")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(413, "That picture is over 15 MB.")

    def save() -> str:
        from PIL import Image, ImageOps, UnidentifiedImageError

        try:
            im = Image.open(io.BytesIO(data))
            im = ImageOps.exif_transpose(im).convert("RGB")
        except (UnidentifiedImageError, OSError):
            raise HTTPException(422, "Couldn't read that file as a picture (use JPG, PNG or WebP).")
        side = min(512, *im.size)
        im = ImageOps.fit(im, (side, side), Image.LANCZOS)
        BRAND_DIR.mkdir(parents=True, exist_ok=True)
        stem = slugify(Path(filename).stem, 40) or "avatar"
        name, k = f"{stem}.jpg", 2
        while (BRAND_DIR / name).exists():
            name, k = f"{stem}_{k}.jpg", k + 1
        im.save(BRAND_DIR / name, quality=92)
        return name

    return {"name": await run_in_threadpool(save)}


@app.get("/sfx/{name}", include_in_schema=False)
def sfx_file(name: str):
    p = _safe_file(SFX_DIR, name)
    if p.suffix.lower() not in _SFX_EXT:
        raise HTTPException(404)
    return FileResponse(p)


# --------------------------------------------------------------------------- render
@app.post("/api/render")
def api_render(project: Project):
    _api_project(project)
    res = validate_project(project)
    if res.errors:
        raise HTTPException(422, {"errors": res.errors})
    job = jobs.submit(project)
    return {"job_id": job.id, "warnings": res.warnings}


@app.get("/api/render/{job_id}")
def api_render_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return job.public()


# --------------------------------------------------------------------------- projects
class SaveIn(BaseModel):
    name: str | None = None
    project: Project
    unique: bool = False  # True: never overwrite; add _2, _3... if the name is taken


@app.get("/api/projects")
def projects_list():
    items = []
    for p in sorted(PROJECTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            title = " ".join(t["text"] for t in json.loads(p.read_text()).get("title", []))
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            title = ""
        items.append({
            "name": p.stem, "title": title,
            "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return items


@app.get("/api/projects/{name}")
def projects_get(name: str):
    p = _safe_file(PROJECTS_DIR, f"{name}.json")
    return Project.model_validate_json(p.read_text()).model_dump(mode="json")


class ImportIn(BaseModel):
    text: str


def _extract_json(text: str) -> str:
    """Accept raw JSON, or JSON inside a ```json code block / surrounded by chat text."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith("{") and "{" in t and "}" in t:
        t = t[t.index("{"): t.rindex("}") + 1]
    return t


@app.post("/api/projects/import")
def projects_import(body: ImportIn):
    """Check pasted project JSON (e.g. from the clip-picker skill) and return it normalized."""
    if not body.text.strip():
        raise HTTPException(422, "Paste the project JSON first.")
    try:
        data = json.loads(_extract_json(body.text))
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"That isn't valid JSON (line {e.lineno}, column {e.colno}): {e.msg}.")
    if isinstance(data, dict) and isinstance(data.get("project"), dict):
        data = data["project"]
    if not isinstance(data, dict) or "clips" not in data:
        raise HTTPException(422, "That JSON isn't a RankReel project (it needs \"title\" and \"clips\").")
    try:
        project = Project.model_validate(data)
    except ValidationError as e:
        problems = []
        for err in e.errors()[:5]:
            where = ".".join(str(x) for x in err["loc"])
            if err["loc"][:1] == ("clips",) and len(err["loc"]) > 1 and isinstance(err["loc"][1], int):
                where = f"clip {err['loc'][1] + 1}" + ("." + ".".join(map(str, err["loc"][2:])) if err["loc"][2:] else "")
            problems.append(f"{where}: {err['msg']}")
        raise HTTPException(422, "The project has problems:\n- " + "\n- ".join(problems))
    for c in project.clips:  # never trust machine-local paths from pasted text
        c.local_path = None
    return project.model_dump(mode="json")


@app.post("/api/projects")
def projects_save(body: SaveIn):
    base = slugify(body.name or body.project.title_text() or "project", 64)
    name, k = base, 2
    while body.unique and (PROJECTS_DIR / f"{name}.json").exists():
        name, k = f"{base}_{k}", k + 1
    path = PROJECTS_DIR / f"{name}.json"
    project = body.project.model_copy(deep=True)
    for c in project.clips:  # machine-local; the app re-fills it from its cache
        c.local_path = None
    path.write_text(project.model_dump_json(indent=2))
    return {"name": name}


def run() -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="RankReel local server")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    print(f"RankReel running at http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    run()
