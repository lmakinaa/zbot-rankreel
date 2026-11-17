"""Pillow: one transparent full-frame PNG per segment (title + rank list)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .config import OVERLAY_DIR, avatar_path, resolve_path
from .models import _GROUP_RE, Project, Style, TitleToken

OVERLAY_VERSION = 2  # bump to invalidate cached PNGs after layout changes
TITLE_MARGIN_X = 50
TITLE_ABS_MIN_SIZE = 32
RANK_RIGHT_MARGIN = 40
RANK_NAME_MIN_RATIO = 0.6
LINE_PITCH = 1.12
STROKE_FILL = "#000000"

# Channel card (tweet-style header above the title)
CARD_TOP = 64
CARD_AVATAR = 104
CARD_GAP = 22          # avatar -> text
CARD_NAME_SIZE = 46
CARD_HANDLE_SIZE = 32
CARD_BADGE = 42        # verified check diameter
CARD_TITLE_GAP = 14    # card bottom -> title area
CARD_HANDLE_COLOR = "#D9DEE3"
VERIFIED_BLUE = "#1D9BF0"
SS = 4                 # supersampling for smooth circles

# Single-clip post text
POST_SIZE_MAX = 58
POST_SIZE_MIN = 34
POST_MAX_LINES = 8
POST_PITCH = 1.28
POST_TOP = 110         # without the card
POST_BOTTOM = 980      # keep the text in the top half
POST_CARD_GAP = 44     # card bottom -> text


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
    tokens: list[TitleToken], style: Style, fpath: str, area_h: float | None = None
) -> tuple[int, list[list[tuple[str, str]]]]:
    """Pick the largest font size (max -> min) that fits in <= 2 lines.

    Falls back below ``title_font_size_min`` (down to 32px) for very long
    titles rather than overflowing; returns (font_size, lines).
    """
    area_h = style.title_area_height if area_h is None else area_h
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
        if fits and len(lines) <= 2 and len(lines) * pitch <= area_h:
            return size, lines
    return sizes[-1], lines


def title_box(style: Style) -> tuple[float, float]:
    """(top, height) of the space the title is centred in: below the channel card if shown."""
    top = card_bottom(style) + CARD_TITLE_GAP if style.channel_card else 0
    return top, max(120, style.title_area_height - top)


def _draw_title(draw: ImageDraw.ImageDraw, project: Project, fpath: str) -> None:
    style = project.style
    top, area_h = title_box(style)
    size, lines = layout_title(project.title, style, fpath, area_h)
    if not lines:
        return
    font = _font(fpath, size)
    space = font.getlength(" ")
    cap_h = -font.getbbox("H", anchor="ls")[1]
    pitch = size * LINE_PITCH
    block_h = cap_h + (len(lines) - 1) * pitch
    baseline = top + (area_h - block_h) / 2 + cap_h
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


# --------------------------------------------------------------------------- channel card
def card_bottom(style: Style) -> int:
    return CARD_TOP + CARD_AVATAR


def _avatar(style: Style, d: int, fpath: str) -> Image.Image:
    """Round avatar with a white ring and a soft shadow, as an RGBA tile (d + margins)."""
    pad = 14
    tile = Image.new("RGBA", (d + 2 * pad, d + 2 * pad), (0, 0, 0, 0))
    # soft drop shadow so the card reads on bright footage
    sh = Image.new("L", tile.size, 0)
    ImageDraw.Draw(sh).ellipse((pad, pad + 4, pad + d, pad + d + 4), fill=150)
    tile.putalpha(sh.filter(ImageFilter.GaussianBlur(7)))
    big = d * SS
    src = None
    if style.channel_avatar:
        p = avatar_path(style.channel_avatar)
        if p.is_file():
            try:
                src = ImageOps.fit(Image.open(p).convert("RGB"), (big, big), Image.LANCZOS)
            except OSError:
                src = None
    if src is None:  # no picture: a coloured circle with the first letter
        src = Image.new("RGB", (big, big), VERIFIED_BLUE)
        letter = renderable(style.channel_name[:1].upper(), fpath) or "?"
        ImageDraw.Draw(src).text((big / 2, big / 2), letter, font=_font(fpath, int(big * 0.5)),
                                 fill="#FFFFFF", anchor="mm")
    ring = max(2, round(d * 0.04)) * SS
    disc = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    m = Image.new("L", (big, big), 0)
    ImageDraw.Draw(m).ellipse((0, 0, big - 1, big - 1), fill=255)
    disc.paste((255, 255, 255, 255), (0, 0), m)  # white ring
    inner = Image.new("L", (big, big), 0)
    ImageDraw.Draw(inner).ellipse((ring, ring, big - 1 - ring, big - 1 - ring), fill=255)
    disc.paste(src, (0, 0), inner)
    disc = disc.resize((d, d), Image.LANCZOS)
    tile.alpha_composite(disc, (pad, pad))
    return tile


def _verified(d: int) -> Image.Image:
    """Blue scalloped badge with a white check (like a verified mark)."""
    big = d * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    c = big / 2

    def rosette(r_out: float) -> list[tuple[float, float]]:
        pts = []
        for i in range(240):
            a = 2 * math.pi * i / 240
            r = r_out * (0.9 + 0.1 * math.cos(8 * a))
            pts.append((c + r * math.cos(a), c + r * math.sin(a)))
        return pts

    dr.polygon(rosette(big / 2), fill="#000000")           # thin dark edge for contrast
    dr.polygon(rosette(big / 2 - SS * 2.2), fill=VERIFIED_BLUE)
    w = big * 0.1
    dr.line([(big * 0.3, big * 0.51), (big * 0.44, big * 0.65), (big * 0.71, big * 0.37)],
            fill="#FFFFFF", width=round(w), joint="curve")
    for x, y in [(big * 0.3, big * 0.51), (big * 0.71, big * 0.37)]:
        dr.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill="#FFFFFF")
    return img.resize((d, d), Image.LANCZOS)


def _draw_card(img: Image.Image, style: Style, fpath: str, align: str = "center") -> None:
    """Avatar + name (+ verified) + @handle at the top of the frame: centred as a group
    (ranking) or at the left margin like a post (single clip)."""
    name = renderable(style.channel_name, fpath).strip()
    handle = renderable(style.channel_handle, fpath).strip()
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    d = CARD_AVATAR
    name_font = _font(fpath, CARD_NAME_SIZE)
    handle_font = _font(fpath, CARD_HANDLE_SIZE)
    badge = CARD_BADGE if style.channel_verified and name else 0
    max_text = style.width - 2 * TITLE_MARGIN_X - d - CARD_GAP - (badge + 12 if badge else 0)
    name = _ellipsize(name, max_text, name_font, fpath) if name else ""
    handle = _ellipsize(handle, max_text, handle_font, fpath) if handle else ""
    name_w = name_font.getlength(name) + (12 + badge if badge else 0)
    text_w = max(name_w, handle_font.getlength(handle)) if (name or handle) else 0
    group_w = d + (CARD_GAP + text_w if text_w else 0)
    x0 = TITLE_MARGIN_X if align == "left" else round((style.width - group_w) / 2)
    tile = _avatar(style, d, fpath)
    pad = (tile.width - d) // 2
    img.alpha_composite(tile, (x0 - pad, CARD_TOP - pad))
    if not text_w:
        return
    draw = ImageDraw.Draw(img)
    tx = x0 + d + CARD_GAP
    name_cap = -name_font.getbbox("H", anchor="ls")[1]
    handle_cap = -handle_font.getbbox("H", anchor="ls")[1]
    line_gap = 16
    block = (name_cap if name else 0) + (handle_cap if handle else 0) + (line_gap if name and handle else 0)
    y = CARD_TOP + (d - block) / 2
    if name:
        base = y + name_cap
        draw.text((tx, base), name, font=name_font, fill="#FFFFFF", anchor="ls",
                  stroke_width=5, stroke_fill=STROKE_FILL)
        if badge:
            bx = round(tx + name_font.getlength(name) + 12)
            by = round(base - name_cap / 2 - badge / 2)
            img.alpha_composite(_verified(badge), (bx, by))
        y = base + line_gap
    if handle:
        draw.text((tx, y + handle_cap), handle, font=handle_font, fill=CARD_HANDLE_COLOR, anchor="ls",
                  stroke_width=4, stroke_fill=STROKE_FILL)


# --------------------------------------------------------------------------- single-clip post text
def text_font_path(style: Style) -> str:
    p = resolve_path(style.text_font_path)
    return str(p) if p.exists() else font_path(style)


Word = list[tuple[str, str]]  # a word as colored pieces: "years" + "." can differ in color


def post_paragraphs(caption: str, fpath: str) -> list[list[Word]]:
    """Post text -> lines (from the user's line breaks) of words. ``{#hex:words}`` colors
    words; punctuation right after a group stays on the same word. Stray braces are
    shown as typed."""
    paras = []
    for line in caption.replace("\r\n", "\n").split("\n"):
        runs, pos = [], 0
        for m in _GROUP_RE.finditer(line):
            runs.append((line[pos:m.start()], "#FFFFFF"))
            runs.append((m.group(2), _hex6(m.group(1))))
            pos = m.end()
        runs.append((line[pos:], "#FFFFFF"))
        words: list[Word] = []
        cur: Word = []
        for text, color in runs:
            for piece in re.split(r"(\s+)", renderable(text, fpath)):
                if not piece:
                    continue
                if piece.isspace():
                    if cur:
                        words.append(cur)
                    cur = []
                else:
                    cur.append((piece, color))
        if cur:
            words.append(cur)
        paras.append(words)
    while paras and not paras[-1]:
        paras.pop()
    while paras and not paras[0]:
        paras.pop(0)
    return paras


def _hex6(v: str) -> str:
    v = v.strip()
    return ("#" + "".join(c * 2 for c in v[1:])) if len(v) == 4 else v.upper()


def _word_w(font, word: Word) -> float:
    return sum(font.getlength(t) for t, _ in word)


def layout_post(caption: str, style: Style, fpath: str, top: float):
    """Largest size (POST_SIZE_MAX..MIN) at which the text fits between ``top`` and
    POST_BOTTOM in <= POST_MAX_LINES lines. Returns (size, lines); a line is a list of
    words, [] for a blank line, and whether the text had to be cut (ellipsis)."""
    paras = post_paragraphs(caption, fpath)
    if not paras:
        return POST_SIZE_MAX, [], False
    max_w = style.width - 2 * TITLE_MARGIN_X
    avail = POST_BOTTOM - top
    lines: list[list[Word]] = []
    for size in range(POST_SIZE_MAX, POST_SIZE_MIN - 1, -2):
        font = _font(fpath, size)
        space = font.getlength(" ")
        lines = []
        for para in paras:
            if not para:
                lines.append([])
                continue
            cur, cur_w = [], 0.0
            for word in para:
                ww = min(_word_w(font, word), max_w)
                add = ww if not cur else cur_w + space + ww
                if cur and add > max_w:
                    lines.append(cur)
                    cur, cur_w = [word], ww
                else:
                    cur.append(word)
                    cur_w = add
            lines.append(cur)
        if len(lines) <= POST_MAX_LINES and _post_height(lines, size) <= avail:
            return size, lines, False
    # Too long even at the smallest size: keep what fits and end with an ellipsis.
    size = POST_SIZE_MIN
    while lines and (len(lines) > POST_MAX_LINES or _post_height(lines, size) > avail):
        lines.pop()
    while lines and not lines[-1]:
        lines.pop()
    if lines:
        word = lines[-1][-1]
        text, color = word[-1]
        lines[-1][-1] = word[:-1] + [(text + _ellipsis(fpath), color)]
    return size, lines, True


def post_top(style: Style) -> float:
    return card_bottom(style) + POST_CARD_GAP if style.channel_card else POST_TOP


def post_is_cut(project: Project) -> bool:
    """True when the single-clip text is too long and gets cut off."""
    return layout_post(project.caption, project.style, text_font_path(project.style), post_top(project.style))[2]


def _post_height(lines, size: int) -> float:
    pitch = size * POST_PITCH
    return sum(pitch * (0.5 if not ln else 1) for ln in lines)


def _shade(img: Image.Image, bottom: float, strength: float = 0.55) -> None:
    """Dark gradient from the top of the frame, fading out at ``bottom``: keeps white text
    readable on bright footage without a visible box."""
    h = max(1, int(bottom))
    col = Image.new("L", (1, h))
    col.putdata([int(255 * strength * (1 - (y / h)) ** 1.6) for y in range(h)])
    layer = Image.new("RGBA", (img.width, h), (0, 0, 0, 0))
    layer.putalpha(col.resize((img.width, h)))
    img.alpha_composite(layer, (0, 0))


def _draw_post(img: Image.Image, project: Project, fpath: str) -> None:
    style = project.style
    tpath = text_font_path(style)
    top = post_top(style)
    size, lines, _ = layout_post(project.caption, style, tpath, top)
    text_bottom = top + _post_height(lines, size)
    if style.text_shade and (lines or style.channel_card):
        _shade(img, (text_bottom if lines else card_bottom(style)) + 220)
    if style.channel_card:
        _draw_card(img, style, fpath, align="left")
    if not lines:
        return
    draw = ImageDraw.Draw(img)
    font = _font(tpath, size)
    space = font.getlength(" ")
    cap_h = -font.getbbox("H", anchor="ls")[1]
    pitch = size * POST_PITCH
    stroke = max(3, round(size / 13))
    y = top
    for line in lines:
        if not line:
            y += pitch * 0.5
            continue
        x = TITLE_MARGIN_X
        base = y + cap_h
        for word in line:
            for text, color in word:
                draw.text((x, base), text, font=font, fill=color, anchor="ls",
                          stroke_width=stroke, stroke_fill=STROKE_FILL)
                x += font.getlength(text)
            x += space
        y += pitch


# --------------------------------------------------------------------------- ranks
def _name_font_size(names: list[str], max_w: float, style: Style, fpath: str) -> int:
    """One size for every name: the largest (up to rank_font_size) at which all fit.

    Uses all names, revealed or not, so the size doesn't jump as names appear.
    """
    min_size = max(12, int(style.rank_font_size * RANK_NAME_MIN_RATIO))
    for size in range(style.rank_font_size, min_size - 1, -2):
        font = _font(fpath, size)
        if all(font.getlength(n) <= max_w for n in names):
            return size
    return min_size


def _ellipsize(name: str, max_w: float, font: ImageFont.FreeTypeFont, fpath: str) -> str:
    if font.getlength(name) <= max_w:
        return name
    ell = _ellipsis(fpath)
    text = name
    while text and font.getlength(text.rstrip() + ell) > max_w:
        text = text[:-1]
    return (text.rstrip() + ell) if text else ell


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
    max_name_w = min(style.width - name_x - RANK_RIGHT_MARGIN, style.rank_name_max_width)
    all_names = [renderable(names.get(r, ""), fpath).strip() for r in range(1, n + 1)]
    name_font = _font(fpath, _name_font_size([x for x in all_names if x], max_name_w, style, fpath))
    cap_h = -num_font.getbbox("H", anchor="ls")[1]
    for rank in range(1, n + 1):
        color = style.rank_active_color if rank == active else style.rank_color
        baseline = style.rank_top + (rank - 1) * style.rank_spacing + cap_h
        kw = dict(anchor="ls", stroke_width=style.rank_stroke, stroke_fill=STROKE_FILL)
        draw.text((style.rank_x, baseline), f"{rank}.", font=num_font, fill=color, **kw)
        if rank in revealed:
            name = all_names[rank - 1]
            if name:
                text = _ellipsize(name, max_name_w, name_font, fpath)
                draw.text((name_x, baseline), text, font=name_font, fill=color, **kw)


# --------------------------------------------------------------------------- public
def render_overlay(project: Project, step: int) -> Image.Image:
    style = project.style
    fpath = font_path(style)
    img = Image.new("RGBA", (style.width, style.height), (0, 0, 0, 0))
    if project.format == "single":
        _draw_post(img, project, fpath)
        return img
    if style.channel_card:
        _draw_card(img, style, fpath)
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
        "format": project.format,
        "caption": project.caption if project.format == "single" else None,
        "title": [t.model_dump() for t in project.title],
        "rows": [
            [c.rank, c.name if c.rank in revealed else None]
            for c in sorted(project.clips, key=lambda c: c.rank)
        ],
        "active": active,
        "style": project.style.model_dump(exclude={"transition_sfx", "sfx_volume", "loudnorm", "end_hold_seconds", "fit", "background", "fps"}),
        "font": [str(fp), st.st_size, st.st_mtime_ns],
        "text_font": _file_stamp(resolve_path(project.style.text_font_path)),
        "avatar": _avatar_stamp(project.style),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _file_stamp(p: Path) -> list:
    if not p.is_file():
        return [str(p), None]
    st = p.stat()
    return [str(p), st.st_size, st.st_mtime_ns]


def _avatar_stamp(style: Style) -> list | None:
    if not (style.channel_card and style.channel_avatar):
        return None
    p = avatar_path(style.channel_avatar)
    if not p.is_file():
        return [str(p), None]
    st = p.stat()
    return [str(p), st.st_size, st.st_mtime_ns]


def overlay_png(project: Project, step: int, cache_dir: Path = OVERLAY_DIR) -> Path:
    """Render (or reuse from cache) the overlay PNG for ``play_order[step]``."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{overlay_key(project, step)}.png"
    if not path.exists():
        tmp = path.with_suffix(".tmp.png")
        render_overlay(project, step).save(tmp, optimize=False, compress_level=3)
        tmp.replace(path)
    return path
