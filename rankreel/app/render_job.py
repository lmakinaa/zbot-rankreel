"""The full render (download -> validate -> compose -> credits) and background jobs."""

from __future__ import annotations

import re
import threading
import time
import traceback
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .compose import compose
from .config import OUTPUT_DIR, PROJECTS_DIR, WORK_DIR
from .downloader import prepare_project
from .models import Project
from .validate import validate_project


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    video: Path
    credits: Path
    credits_text: str
    project_file: Path
    total_seconds: float
    warnings: list[str]


def slugify(text: str, max_len: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return text[:max_len].strip("_") or "rankreel"


def credits_text(project: Project) -> str:
    clips = [project.clip_by_rank(r) for r in project.play_order]
    parts = []
    for c in clips:
        who = c.creator or "unknown creator"
        parts.append(f"{who} ({c.webpage_url or c.url})")
    lines = ["Clips: " + ", ".join(parts), ""]
    for c in sorted(project.clips, key=lambda c: c.rank):
        if project.format == "single":
            lines.append(f"{c.creator or 'unknown creator'} - {c.webpage_url or c.url}")
            continue
        lines.append(f"#{c.rank} {c.name} - {c.creator or 'unknown creator'} - {c.webpage_url or c.url}")
    return "\n".join(lines) + "\n"


def run_render(project: Project, progress=None, out_dir: Path = OUTPUT_DIR,
               projects_dir: Path = PROJECTS_DIR, job_id: str | None = None) -> RenderResult:
    """Synchronous render used by both the CLI and background jobs.

    progress(fraction, message): downloading is 0-15%, encoding 15-100%.
    """
    report = progress or (lambda f, m: None)
    prepare_project(project, progress=lambda f, m: report(0.15 * f, m))
    result = validate_project(project, require_sources=True)
    if result.errors:
        raise RenderError("Fix these first:\n- " + "\n- ".join(result.errors))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{slugify(project.name_text(), 40)}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"{base}.mp4"
    work = WORK_DIR / (job_id or uuid.uuid4().hex[:10])
    compose(project, video, work, progress=lambda f, m: report(0.15 + 0.85 * f, m))

    text = credits_text(project)
    credits = out_dir / f"{base}_credits.txt"
    credits.write_text(text)
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_file = projects_dir / f"{base}.json"
    project_file.write_text(project.model_dump_json(indent=2))
    return RenderResult(video, credits, text, project_file, result.total_seconds, result.warnings)


# --------------------------------------------------------------------------- jobs
@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = "Queued"
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    output_file: str | None = None
    credits_file: str | None = None
    credits_text: str | None = None
    project_file: str | None = None
    created: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "warnings": self.warnings,
            "error": self.error,
            "output_url": f"/output/{self.output_file}" if self.output_file else None,
            "credits_url": f"/output/{self.credits_file}" if self.credits_file else None,
            "credits_text": self.credits_text,
            "project_file": self.project_file,
        }


class JobManager:
    """Renders run one at a time (ffmpeg already uses all cores)."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")

    def submit(self, project: Project) -> Job:
        job = Job(id=uuid.uuid4().hex[:12])
        job.warnings = validate_project(project).warnings
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, project.model_copy(deep=True))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job, project: Project) -> None:
        job.status = "running"

        def progress(f: float, msg: str) -> None:
            job.progress = f
            job.message = msg

        try:
            res = run_render(project, progress=progress, job_id=job.id)
            job.output_file = res.video.name
            job.credits_file = res.credits.name
            job.credits_text = res.credits_text
            job.project_file = res.project_file.name
            job.warnings = res.warnings
            job.progress = 1.0
            job.message = "Done"
            job.status = "done"
        except Exception as e:  # report every failure to the UI
            job.status = "error"
            job.error = str(e) or e.__class__.__name__
            job.message = "Failed"
            traceback.print_exc()
