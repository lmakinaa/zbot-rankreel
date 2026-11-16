"""Project data model (pydantic) and the title-markup parser."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from .timeparse import parse_time

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _check_hex(v: str) -> str:
    v = v.strip()
    if not _HEX_RE.match(v):
        raise ValueError(f"Not a hex color: {v!r} (use #RGB or #RRGGBB)")
    if len(v) == 4:  # #RGB -> #RRGGBB
        v = "#" + "".join(c * 2 for c in v[1:])
    return v.upper()


class TitleToken(BaseModel):
    text: str
    color: str = "#FFFFFF"

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return _check_hex(v)


class Clip(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    url: HttpUrl
    start: float | None = None  # seconds
    end: float | None = None
    rank: int = Field(ge=1)  # 1 = best
    name: str = ""
    # filled after download:
    local_path: str | None = None
    source_duration: float | None = None
    creator: str | None = None
    webpage_url: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _parse_ts(cls, v):
        return parse_time(v)


class Style(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    title_area_height: int = 400
    title_font_size_max: int = 96
    title_font_size_min: int = 56
    title_stroke: int = 8
    title_uppercase: bool = True
    rank_font_size: int = 46
    rank_stroke: int = 5
    rank_x: int = 40
    rank_top: int = 470
    rank_spacing: int = 78
    rank_name_max_width: int = 560  # px; longer names shrink (all together) so the list stays compact
    rank_color: str = "#FFFFFF"
    rank_active_color: str = "#FFD400"  # the rank currently playing
    # cover: fill the frame, but never cut off much of the picture: when a clip isn't
    #        (nearly) 9:16, show it whole on a blurred background instead.
    # blur: always whole, on a blurred background. contain: whole, plain bars.
    # crop: always fill by cropping.
    fit: Literal["cover", "blur", "contain", "crop"] = "cover"
    background: str = "#000000"
    font_path: str = "assets/fonts/Montserrat-Black.ttf"
    transition_sfx: str | None = None  # file name (or path) in assets/sfx
    sfx_volume: float = Field(default=0.8, ge=0, le=4)
    loudnorm: bool = True
    end_hold_seconds: float = Field(default=0.0, ge=0, le=5)
    # Visual transition on each cut (clips 2..N). Older projects default to "none".
    transition: Literal["none", "flash", "zoom", "punch", "fade", "slide", "shake"] = "none"
    transition_seconds: float = Field(default=0.25, ge=0.05, le=1.0)

    @field_validator("rank_color", "rank_active_color", "background")
    @classmethod
    def _colors(cls, v: str) -> str:
        return _check_hex(v)

    @model_validator(mode="before")
    @classmethod
    def _old_rank_defaults(cls, data):
        # Projects saved before the compact rank list stored the old defaults (64px text,
        # 110px rows), which the editor never offered to change: give them the new look.
        if isinstance(data, dict) and data.get("rank_font_size") == 64 and data.get("rank_spacing") == 110:
            data = {**data, "rank_font_size": 46, "rank_spacing": 78}
            if data.get("rank_stroke") == 6:
                data["rank_stroke"] = 5
        return data

    @field_validator("transition_sfx", mode="before")
    @classmethod
    def _empty_none(cls, v):
        return v or None


class Project(BaseModel):
    title: list[TitleToken]
    clips: list[Clip]
    play_order: list[int] = Field(default_factory=list)  # ranks in playback order
    style: Style = Field(default_factory=Style)
    max_seconds_warning: float = 40.0

    @field_validator("title", mode="before")
    @classmethod
    def _title_markup(cls, v):
        # Allow `"title": "RANKING {#FFD400:FUNNIEST} MOMENTS"` in project JSON.
        if isinstance(v, str):
            return [t.model_dump() for t in parse_title_markup(v)]
        return v

    @model_validator(mode="after")
    def _default_order(self):
        if not self.play_order and self.clips:
            # Worst -> best, #1 last.
            self.play_order = sorted((c.rank for c in self.clips), reverse=True)
        return self

    def clip_by_rank(self, rank: int) -> Clip:
        for c in self.clips:
            if c.rank == rank:
                return c
        raise KeyError(rank)

    def title_text(self) -> str:
        return " ".join(t.text for t in self.title)


# ---------------------------------------------------------------------------
# Title markup:  RANKING {#FFD400:FUNNIEST} {#FF3B30:MOMENTS} 😭
# A {#hex:words} group colors every word inside it; bare words are white.
# ---------------------------------------------------------------------------

_GROUP_RE = re.compile(r"\{\s*(#[0-9a-fA-F]{3,6})\s*:([^{}]*)\}")


def parse_title_markup(markup: str, default_color: str = "#FFFFFF") -> list[TitleToken]:
    tokens: list[TitleToken] = []
    pos = 0
    for m in _GROUP_RE.finditer(markup):
        plain = markup[pos : m.start()]
        if "{" in plain or "}" in plain:
            raise ValueError(f"Malformed title markup near: {plain.strip()!r}")
        tokens += [TitleToken(text=w, color=default_color) for w in plain.split()]
        color = _check_hex(m.group(1))
        tokens += [TitleToken(text=w, color=color) for w in m.group(2).split()]
        pos = m.end()
    rest = markup[pos:]
    if "{" in rest or "}" in rest:
        raise ValueError(f"Malformed title markup near: {rest.strip()!r}")
    tokens += [TitleToken(text=w, color=default_color) for w in rest.split()]
    return tokens


def tokens_to_markup(tokens: list[TitleToken], default_color: str = "#FFFFFF") -> str:
    out = []
    for t in tokens:
        out.append(t.text if t.color.upper() == default_color.upper() else f"{{{t.color}:{t.text}}}")
    return " ".join(out)
