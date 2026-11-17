"""Duration and rank/order checks. Errors block rendering; warnings and hints don't."""

from __future__ import annotations

from pydantic import BaseModel

from .config import sfx_path
from .models import Clip, Project

MAX_CLIPS = 10
MIN_CLIPS_HINT = 3
SINGLE_CLIP_WARN = 8.0
SWEET_MIN, SWEET_MAX = 27.0, 35.0
HINT_LOW, HINT_HIGH = 20.0, 35.0
_EPS = 0.05  # tolerance for end slightly past the probed duration


class ValidationResult(BaseModel):
    total_seconds: float
    known_total: bool  # False if some clip's length is still unknown (not fetched)
    clip_lengths: dict[str, float | None]
    errors: list[str]
    warnings: list[str]
    hints: list[str]
    over_limit: bool

    @property
    def ok(self) -> bool:
        return not self.errors


def clip_bounds(clip: Clip) -> tuple[float, float | None]:
    """(start, end) with defaults applied: missing start = 0, missing end = source end."""
    start = clip.start or 0.0
    end = clip.end if clip.end is not None else clip.source_duration
    if end is not None and clip.source_duration is not None:
        end = min(end, clip.source_duration)
    return start, end


def _label(clip: Clip, single: bool = False) -> str:
    if single:
        return "The clip"
    return f"#{clip.rank}" + (f" ({clip.name})" if clip.name else "")


def validate_project(project: Project, require_sources: bool = False) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    hints: list[str] = []
    lengths: dict[str, float | None] = {}

    single = project.format == "single"
    clips = project.clips
    n = len(clips)
    if n == 0:
        errors.append("Add at least one clip.")
    if single:
        if n > 1:
            errors.append(f"The single-clip format uses one clip (got {n}).")
        if not project.caption.strip():
            hints.append("Add the text (a fact or caption about the clip).")
        else:
            from .overlay import single_text_is_cut

            try:
                if single_text_is_cut(project):
                    warnings.append("The text is too long to fit; the end is cut off. Shorten it.")
            except FileNotFoundError:
                pass
    elif n > MAX_CLIPS:
        errors.append(f"{n} clips is too many (max {MAX_CLIPS}).")
    elif 0 < n < MIN_CLIPS_HINT:
        hints.append(f"Only {n} clip(s); ranking videos usually have 3-10 (5 is typical).")

    # --- ranks: exactly {1..N} -------------------------------------------
    ranks = [c.rank for c in clips]
    expected = set(range(1, n + 1))
    seen: set[int] = set()
    dups = sorted({r for r in ranks if r in seen or seen.add(r)})
    if dups:
        errors.append("Duplicate rank(s): " + ", ".join(f"#{r}" for r in dups))
    missing = sorted(expected - set(ranks))
    extra = sorted(set(ranks) - expected)
    if missing:
        errors.append("Missing rank(s): " + ", ".join(f"#{r}" for r in missing))
    if extra:
        errors.append(f"Rank(s) out of range 1..{n}: " + ", ".join(f"#{r}" for r in extra))

    # --- play order: permutation of the ranks ----------------------------
    order = project.play_order
    if sorted(order) != sorted(ranks) or len(set(order)) != len(order):
        errors.append(
            "Playback order must contain every rank exactly once "
            f"(got {order}, ranks are {sorted(ranks)})."
        )
    elif n and order[-1] != 1 and 1 in ranks and not single:
        hints.append("Trend tip: #1 usually plays last.")

    sfx = project.style.transition_sfx
    if sfx and not sfx_path(sfx).exists():
        errors.append(f"Transition sound not found: {sfx} (pick another sound).")

    # --- per-clip timing ---------------------------------------------------
    total = 0.0
    known = True
    for c in clips:
        start, end = clip_bounds(c)
        if c.end is not None and c.source_duration is not None and c.end > c.source_duration + _EPS:
            errors.append(
                f"{_label(c, single)}: end {c.end:.2f}s is past the video's end ({c.source_duration:.2f}s)."
            )
        if c.source_duration is not None and start >= c.source_duration:
            errors.append(
                f"{_label(c, single)}: start {start:.2f}s is past the video's end ({c.source_duration:.2f}s)."
            )
            lengths[c.id] = None
            known = False
            continue
        if end is None:
            lengths[c.id] = None
            known = False
            if require_sources:
                errors.append(f"{_label(c, single)}: video not downloaded yet.")
            continue
        if end <= start:
            errors.append(f"{_label(c, single)}: end ({end:.2f}s) must be after start ({start:.2f}s).")
            lengths[c.id] = None
            continue
        length = end - start
        lengths[c.id] = round(length, 3)
        total += length
        if single:
            continue
        if length > SINGLE_CLIP_WARN:
            warnings.append(f"{_label(c, single)} is {length:.1f}s; trend clips are short (3-7s).")
        if not c.name.strip():
            hints.append(f"#{c.rank} has no name.")

    total += project.style.end_hold_seconds
    total = round(total, 3)
    over = total > project.max_seconds_warning
    if over:
        warnings.append(f"Total {total:.1f}s exceeds {project.max_seconds_warning:g}s")
    elif total and known and not single:
        if total < HINT_LOW:
            hints.append(f"Total {total:.1f}s is short; the sweet spot is {SWEET_MIN:g}-{SWEET_MAX:g}s.")
        elif total > HINT_HIGH:
            hints.append(f"Total {total:.1f}s; the sweet spot is {SWEET_MIN:g}-{SWEET_MAX:g}s.")

    return ValidationResult(
        total_seconds=total,
        known_total=known,
        clip_lengths=lengths,
        errors=errors,
        warnings=warnings,
        hints=hints,
        over_limit=over,
    )
