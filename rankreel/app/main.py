"""FastAPI app: serves the UI and the local API. Run with ``python -m app.main``."""

from __future__ import annotations

import io
from pathlib import Path

import shutil
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import projects
from .config import DOWNLOAD_DIR, OUTPUT_DIR, SFX_DIR, SFX_EXTENSIONS, STATIC_DIR, WORK_DIR, ensure_dirs
from .downloader import FetchError, fetch, fill_from_cache
from .models import Project, parse_title_markup
from .overlay import render_overlay
from .render_job import JobManager, slugify
from .probe import probe
from .sfx_gen import SOUNDS as BUILTIN_SFX, ensure_default_sfx
from .validate import validate_project

ensure_dirs()
ensure_default_sfx()
app = FastAPI(title="RankReel", docs_url="/api/docs", redoc_url=None)
jobs = JobManager()

_SFX_EXT = SFX_EXTENSIONS
MAX_SFX_BYTES = 10 * 1024 * 1024
MAX_SFX_SECONDS = 10.0


def _safe_file(base: Path, name: str) -> Path:
    try:
        return projects.safe_file(base, name)
    except FileNotFoundError:
        raise HTTPException(404) from None


def _api_project(project: Project) -> Project:
    """Requests from the page may only use cached clips and sounds in assets/sfx."""
    fill_from_cache(project, trust_local=False)
    sfx = project.style.transition_sfx
    if sfx and ("/" in sfx or "\\" in sfx):
        raise HTTPException(422, "Pick a transition sound from the list.")
    return project


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
        name = _unique_sfx_name(slugify(Path(filename).stem, 40), ext)
        shutil.move(str(tmp), SFX_DIR / name)
    finally:
        tmp.unlink(missing_ok=True)
    return {"name": name, "duration": round(info.duration, 3)}


@app.delete("/api/sfx/{name}")
def delete_sfx(name: str):
    if name in BUILTIN_SFX:
        raise HTTPException(422, "Built-in sounds can't be deleted.")
    p = _safe_file(SFX_DIR, name)
    if p.suffix.lower() not in _SFX_EXT:
        raise HTTPException(404)
    p.unlink()
    return {"deleted": name}


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
    return projects.list_projects()


@app.get("/api/projects/{name}")
def projects_get(name: str):
    try:
        return projects.load_project(name).model_dump(mode="json")
    except FileNotFoundError:
        raise HTTPException(404) from None


class ImportIn(BaseModel):
    text: str


@app.post("/api/projects/import")
def projects_import(body: ImportIn):
    """Check pasted project JSON (e.g. from the clip-picker skill) and return it normalized."""
    try:
        return projects.parse_project_text(body.text).model_dump(mode="json")
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@app.post("/api/projects")
def projects_save(body: SaveIn):
    return {"name": projects.save_project(body.project, body.name, unique=body.unique)}


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
