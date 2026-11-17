"""Color emoji in overlay text.

The text fonts (Montserrat) have no emoji, so text is split into runs: ordinary text
is drawn with the text font, emoji runs with a color emoji font. Color emoji fonts
only come in fixed bitmap sizes, so an emoji is drawn at the font's size and scaled to
match the text around it.

The emoji font is the first one found of: RANKREEL_EMOJI_FONT / "emoji_font" in
config.json, assets/fonts/NotoColorEmoji.ttf, then the usual system locations
(Noto Color Emoji on Linux, Apple Color Emoji on macOS, Segoe UI Emoji on Windows).
Without one, emoji are left out (as before).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

from .config import ASSETS_DIR, resolve_path, setting

CANDIDATES = [
    ASSETS_DIR / "fonts" / "NotoColorEmoji.ttf",
    Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/noto/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/noto-emoji/NotoColorEmoji.ttf"),
    Path("/usr/share/fonts/TTF/NotoColorEmoji.ttf"),
    Path("/System/Library/Fonts/Apple Color Emoji.ttc"),
    Path("C:/Windows/Fonts/seguiemj.ttf"),
]
STRIKES = (109, 160, 136, 128, 96, 72, 64, 48)  # bitmap sizes color fonts ship with
HEIGHT = 0.98   # emoji height as a share of the text's font size
DROP = 0.13     # how far the emoji's bottom sits below the baseline (share of font size)
ZWJ_ETC = {0x200D, 0xFE0E, 0xFE0F, 0x20E3}  # joiners, variation selectors, keycap


def _joiner(cp: int) -> bool:
    return cp in ZWJ_ETC or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F


def _paths() -> list[Path]:
    explicit = setting("RANKREEL_EMOJI_FONT", "emoji_font")
    return ([resolve_path(explicit)] if explicit else []) + CANDIDATES


@lru_cache(maxsize=1)
def emoji_font() -> tuple[ImageFont.FreeTypeFont, float] | None:
    """(font at a size it supports, its emoji height in px), or None."""
    engine = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    for p in _paths():
        if not p.is_file():
            continue
        for size in STRIKES:
            try:
                f = ImageFont.truetype(str(p), size, layout_engine=engine)
                box = f.getbbox("😀")
                ImageDraw.Draw(Image.new("RGBA", (box[2] + 2, box[3] + 2))).text(
                    (0, 0), "😀", font=f, embedded_color=True)
            except OSError:
                continue
            h = box[3] - box[1]
            if h > 0:
                return f, float(h)
    return None


@lru_cache(maxsize=1)
def _emoji_cmap() -> frozenset[int]:
    ef = emoji_font()
    if not ef:
        return frozenset()
    from fontTools.ttLib import TTFont

    path = ef[0].path
    try:
        with TTFont(path, lazy=True, fontNumber=0) as f:
            return frozenset(f.getBestCmap().keys())
    except Exception:
        return frozenset()


def font_stamp() -> str | None:
    ef = emoji_font()
    return ef[0].path if ef else None


def runs(text: str, text_cmap: frozenset[int]) -> list[tuple[str, bool]]:
    """Split into (piece, is_emoji). Characters neither font can draw are dropped."""
    emap = _emoji_cmap()
    out: list[tuple[str, bool]] = []
    for ch in text:
        cp = ord(ch)
        if ch.isspace() or cp in text_cmap and not (_joiner(cp) and out and out[-1][1]):
            kind = False
        elif _joiner(cp):
            if not (out and out[-1][1]):
                continue  # a joiner with nothing to join
            kind = True
        elif cp in emap:
            kind = True
        else:
            continue
        if out and out[-1][1] == kind:
            out[-1] = (out[-1][0] + ch, kind)
        else:
            out.append((ch, kind))
    return out


def _scale(size: int) -> float:
    ef = emoji_font()
    return size * HEIGHT / ef[1] if ef else 0.0


def emoji_width(run: str, size: int) -> float:
    ef = emoji_font()
    if not ef:
        return 0.0
    return ef[0].getlength(run) * _scale(size)


@lru_cache(maxsize=256)
def emoji_image(run: str, size: int) -> Image.Image | None:
    """The emoji run drawn in color and scaled to sit next to ``size``-px text."""
    ef = emoji_font()
    if not ef:
        return None
    f, _ = ef
    box = f.getbbox(run)
    w, h = max(1, box[2]), max(1, box[3])
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 0), run, font=f, embedded_color=True)
    k = _scale(size)
    return img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)


def paste(img: Image.Image, x: float, baseline: float, run: str, size: int) -> None:
    em = emoji_image(run, size)
    if em is None:
        return
    top = baseline + size * DROP - em.height
    img.alpha_composite(em, (round(x), round(top)))
