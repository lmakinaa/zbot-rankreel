"""Pillow: one transparent full-frame PNG per segment (title + rank list)."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import OVERLAY_DIR, resolve_path
from .models import Project, Style, TitleToken

OVERLAY_VERSION = 1  # bump to invalidate cached PNGs after layout changes
TITLE_MARGIN_X = 50
TITLE_ABS_MIN_SIZE = 32
RANK_RIGHT_MARGIN = 40
RANK_NAME_MIN_RATIO = 0.6
LINE_PITCH = 1.12
STROKE_FILL = "#000000"


# --------------------------------------------------------------------------- fonts
@lru_cache(maxsize=256)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=8)
def _cmap(path: str) -> frozenset[int]:
    from fontTools.ttLib import TTFont

    with TTFont(path, lazy=True) as f:
        return frozenset(f.getBestCmap().keys())


def font_path(style: Style) -> str:
    p = resolve_path(style.font_path)
    if not p.exists():
        raise FileNotFoundError(f"Font not found: {p}")
    return str(p)


def renderable(text: str, fpath: str) -> str:
    """Drop characters the font has no glyph for (e.g. emoji)."""
    cmap = _cmap(fpath)
    return "".join(ch for ch in text if ch.isspace() or ord(ch) in cmap)


def _ellipsis(fpath: str) -> str:
    return "…" if ord("…") in _cmap(fpath) else "..."


# --------------------------------------------------------------------------- state
def overlay_state(project: Project, step: int) -> tuple[set[int], int | None]:
    """(revealed ranks, active rank) while play_order[step] is playing.

    A rank's name is shown once it has played or is playing (cumulative reveal).
    ``step < 0`` means "before anything plays": numbers only.
    """
    if step < 0:
        return set(), None
    order = project.play_order
    return set(order[: step + 1]), order[step]


# --------------------------------------------------------------------------- title
def _title_words(tokens: list[TitleToken], style: Style, fpath: str) -> list[tuple[str, str]]:
    words = []
    for t in tokens:
        text = t.text.upper() if style.title_uppercase else t.text
        text = renderable(text, fpath).strip()
        for w in text.split():
            words.append((w, t.color))
    return words


def layout_title(
    tokens: list[TitleToken], style: Style, fpath: str
) -> tuple[int, list[list[tuple[str, str]]]]:
    """Pick the largest font size (max -> min) that fits in <= 2 lines.

    Falls back below ``title_font_size_min`` (down to 32px) for very long
    titles rather than overflowing; returns (font_size, lines).
    """
    words = _title_words(tokens, style, fpath)
    if not words:
        return style.title_font_size_max, []
    max_w = style.width - 2 * TITLE_MARGIN_X - 2 * style.title_stroke
    sizes = list(range(style.title_font_size_max, style.title_font_size_min - 1, -2))
    sizes += list(range(min(sizes[-1], style.title_font_size_min) - 2, TITLE_ABS_MIN_SIZE - 1, -2))
    lines: list[list[tuple[str, str]]] = []
    for size in sizes:
        font = _font(fpath, size)
        space = font.getlength(" ")
        lines, cur, cur_w, fits = [], [], 0.0, True
        for w, color in words:
            ww = font.getlength(w)
            if ww > max_w:
                fits = False
                break
            add = ww if not cur else cur_w + space + ww
            if cur and add > max_w:
                lines.append(cur)
                cur, cur_w = [(w, color)], ww
            else:
                cur.append((w, color))
                cur_w = add
        if cur:
            lines.append(cur)
        pitch = size * LINE_PITCH
        if fits and len(lines) <= 2 and len(lines) * pitch <= style.title_area_height:
            return size, lines
    return sizes[-1], lines


def _draw_title(draw: ImageDraw.ImageDraw, project: Project, fpath: str) -> None:
    style = project.style
    size, lines = layout_title(project.title, style, fpath)
    if not lines:
        return
    font = _font(fpath, size)
    space = font.getlength(" ")
    cap_h = -font.getbbox("H", anchor="ls")[1]
    pitch = size * LINE_PITCH
    block_h = cap_h + (len(lines) - 1) * pitch
    baseline = (style.title_area_height - block_h) / 2 + cap_h
    for line in lines:
        widths = [font.getlength(w) for w, _ in line]
        total = sum(widths) + space * (len(line) - 1)
        x = (style.width - total) / 2
        for (w, color), ww in zip(line, widths):
            draw.text(
                (x, baseline), w, font=font, fill=color, anchor="ls",
                stroke_width=style.title_stroke, stroke_fill=STROKE_FILL,
            )
            x += ww + space
        baseline += pitch


# --------------------------------------------------------------------------- ranks
def _fit_name(name: str, max_w: float, style: Style, fpath: str) -> tuple[str, ImageFont.FreeTypeFont]:
    """Shrink the name's font to fit; at the minimum size, ellipsize."""
    min_size = max(12, int(style.rank_font_size * RANK_NAME_MIN_RATIO))
    for size in range(style.rank_font_size, min_size - 1, -2):
        font = _font(fpath, size)
        if font.getlength(name) <= max_w:
            return name, font
    font = _font(fpath, min_size)
    ell = _ellipsis(fpath)
    text = name
    while text and font.getlength(text.rstrip() + ell) > max_w:
        text = text[:-1]
    return (text.rstrip() + ell) if text else ell, font


def _draw_ranks(draw: ImageDraw.ImageDraw, project: Project, step: int, fpath: str) -> None:
    style = project.style
    revealed, active = overlay_state(project, step)
    n = len(project.clips)
    if n == 0:
        return
    names = {c.rank: c.name for c in project.clips}
    num_font = _font(fpath, style.rank_font_size)
    num_w = max(num_font.getlength(f"{r}.") for r in range(1, n + 1))
    name_x = style.rank_x + num_w + num_font.getlength(" ") * 0.8
    max_name_w = style.width - name_x - RANK_RIGHT_MARGIN
    cap_h = -num_font.getbbox("H", anchor="ls")[1]
    for rank in range(1, n + 1):
        color = style.rank_active_color if rank == active else style.rank_color
        baseline = style.rank_top + (rank - 1) * style.rank_spacing + cap_h
        kw = dict(anchor="ls", stroke_width=style.rank_stroke, stroke_fill=STROKE_FILL)
        draw.text((style.rank_x, baseline), f"{rank}.", font=num_font, fill=color, **kw)
        if rank in revealed:
            name = renderable(names.get(rank, ""), fpath).strip()
            if name:
                text, font = _fit_name(name, max_name_w, style, fpath)
                draw.text((name_x, baseline), text, font=font, fill=color, **kw)


# --------------------------------------------------------------------------- public
def render_overlay(project: Project, step: int) -> Image.Image:
    style = project.style
    fpath = font_path(style)
    img = Image.new("RGBA", (style.width, style.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_title(draw, project, fpath)
    _draw_ranks(draw, project, step, fpath)
    return img


def overlay_key(project: Project, step: int) -> str:
    revealed, active = overlay_state(project, step)
    fp = Path(font_path(project.style))
    st = fp.stat()
    payload = {
        "v": OVERLAY_VERSION,
        "title": [t.model_dump() for t in project.title],
        "rows": [
            [c.rank, c.name if c.rank in revealed else None]
            for c in sorted(project.clips, key=lambda c: c.rank)
        ],
        "active": active,
        "style": project.style.model_dump(exclude={"transition_sfx", "sfx_volume", "loudnorm", "end_hold_seconds", "fit", "background", "fps"}),
        "font": [str(fp), st.st_size, st.st_mtime_ns],
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def overlay_png(project: Project, step: int, cache_dir: Path = OVERLAY_DIR) -> Path:
    """Render (or reuse from cache) the overlay PNG for ``play_order[step]``."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{overlay_key(project, step)}.png"
    if not path.exists():
        tmp = path.with_suffix(".tmp.png")
        render_overlay(project, step).save(tmp, optimize=False, compress_level=3)
        tmp.replace(path)
    return path
