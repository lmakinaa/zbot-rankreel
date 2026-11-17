import hashlib

import pytest

from PIL import Image

from app.overlay import layout_title, overlay_png, overlay_state, render_overlay, font_path

YELLOW = (0xFF, 0xD4, 0x00)
WHITE = (0xFF, 0xFF, 0xFF)


def _row_box(p, rank):
    s = p.style
    top = s.rank_top + (rank - 1) * s.rank_spacing
    return (0, top - 5, s.width, top + s.rank_font_size)


def _colors(img, box):
    region = img.crop(box)
    px = [c[:3] for c in (region.get_flattened_data() if hasattr(region, 'get_flattened_data') else region.getdata()) if c[3] == 255]
    return set(px)


def _name_has_ink(p, img, rank):
    # Name column starts well right of the "N." number.
    x0, y0, x1, y1 = _row_box(p, rank)
    region = img.crop((p.style.rank_x + 140, y0, x1, y1))
    return region.getbbox() is not None


def test_state_cumulative(project_factory):
    p = project_factory(n=5, order=[4, 2, 5, 3, 1])
    assert overlay_state(p, -1) == (set(), None)
    assert overlay_state(p, 0) == ({4}, 4)
    assert overlay_state(p, 2) == ({4, 2, 5}, 5)
    assert overlay_state(p, 4) == ({1, 2, 3, 4, 5}, 1)


def test_revealed_names_follow_play_order(project_factory):
    order = [4, 2, 5, 3, 1]
    p = project_factory(n=5, order=order)
    for step in range(5):
        img = render_overlay(p, step)
        assert img.size == (1080, 1920) and img.mode == "RGBA"
        played = set(order[: step + 1])
        for rank in range(1, 6):
            assert _name_has_ink(p, img, rank) == (rank in played), (step, rank)


def test_active_rank_is_colored(project_factory):
    p = project_factory(n=5, order=[4, 2, 5, 3, 1])
    img = render_overlay(p, 1)  # rank 2 playing, rank 4 already played
    assert YELLOW in _colors(img, _row_box(p, 2))
    assert YELLOW not in _colors(img, _row_box(p, 4))
    assert WHITE in _colors(img, _row_box(p, 4))


def test_title_colors_present(project_factory):
    p = project_factory()
    img = render_overlay(p, 0)
    title = _colors(img, (0, 0, 1080, p.style.title_area_height))
    assert YELLOW in title and (0xFF, 0x3B, 0x30) in title and WHITE in title


def test_title_shrinks_to_two_lines(project_factory):
    from app.models import parse_title_markup
    p = project_factory()
    fp = font_path(p.style)
    size, lines = layout_title(parse_title_markup("TOP 5"), p.style, fp)
    assert size == p.style.title_font_size_max and len(lines) == 1
    p.title = p.title * 2
    size, lines = layout_title(p.title, p.style, fp)
    assert size < p.style.title_font_size_max and len(lines) <= 2


def _no_emoji_font(monkeypatch):
    from app import emoji

    for f in (emoji.emoji_font, emoji._emoji_cmap, emoji.emoji_image):
        f.cache_clear()
    monkeypatch.setattr(emoji, "_paths", lambda: [])
    return emoji


def test_emoji_drawn_in_color(project_factory):
    from app import emoji
    from app.models import parse_title_markup

    if not emoji.emoji_font():
        pytest.skip("no color emoji font on this machine")
    p = project_factory()
    fp = font_path(p.style)
    _, lines = layout_title(parse_title_markup("FUNNY 😭 CATS"), p.style, fp)
    assert [w for line in lines for w, _ in line] == ["FUNNY", "😭", "CATS"]
    p.title = parse_title_markup("😭")
    img = render_overlay(p, -1)
    region = img.crop((0, 0, 1080, 400))
    px = region.get_flattened_data() if hasattr(region, "get_flattened_data") else region.getdata()
    colors = {c[:3] for c in px if c[3] > 200}
    assert any(r > 200 and 120 < g < 230 and b < 90 for r, g, b in colors)  # the face's yellow


def test_emoji_dropped_without_emoji_font(project_factory, monkeypatch):
    from app.models import parse_title_markup

    emoji = _no_emoji_font(monkeypatch)
    try:
        p = project_factory()
        _, lines = layout_title(parse_title_markup("FUNNY 😭 CATS"), p.style, font_path(p.style))
        assert [w for line in lines for w, _ in line] == ["FUNNY", "CATS"]
    finally:
        monkeypatch.undo()
        for f in (emoji.emoji_font, emoji._emoji_cmap, emoji.emoji_image):
            f.cache_clear()


def test_long_name_stays_inside_frame(project_factory):
    p = project_factory(n=3, names=["A" * 200, "b", "c"])
    img = render_overlay(p, 2)
    box = _row_box(p, 1)
    bbox = img.crop(box).getbbox()
    assert bbox is not None and bbox[2] <= p.style.width - 30


def test_snapshot_stable_and_cached(project_factory, tmp_path):
    p = project_factory(n=5, order=[4, 2, 5, 3, 1])
    a = overlay_png(p, 2, tmp_path)
    b = overlay_png(p, 2, tmp_path)
    assert a == b and len(list(tmp_path.glob("*.png"))) == 1
    h1 = hashlib.sha1(Image.open(a).tobytes()).hexdigest()
    h2 = hashlib.sha1(render_overlay(p, 2).tobytes()).hexdigest()
    assert h1 == h2  # deterministic render
    assert overlay_png(p, 3, tmp_path) != a


# ---------------------------------------------------------------- compact rank list
def _row_ink_right_edge(p, img, rank):
    s = p.style
    top = s.rank_top + (rank - 1) * s.rank_spacing
    box = img.crop((0, top - 5, s.width, top + s.rank_font_size + 5)).getbbox()
    return box[2] if box else 0


def test_names_stay_within_the_max_width(project_factory):
    names = ["Streamer jumpscared", "Bathroom meltdown", "Suit man evacuates", "Coffee spill", "X"]
    p = project_factory(n=5, names=names)
    img = render_overlay(p, 4)  # everything revealed
    limit = p.style.rank_x + 140 + p.style.rank_name_max_width  # number column + name width
    for rank in range(1, 6):
        assert _row_ink_right_edge(p, img, rank) <= limit, rank


def test_all_names_share_one_size_and_it_is_stable(project_factory):
    from app.overlay import _name_font_size, font_path
    p = project_factory(n=3, names=["Short", "A considerably longer clip name here", "Mid size"])
    fp = font_path(p.style)
    size = _name_font_size(["Short", "A considerably longer clip name here", "Mid size"],
                           p.style.rank_name_max_width, p.style, fp)
    assert size < p.style.rank_font_size  # shrunk for the long one, applied to all
    # Same overlay for "Short" whether or not the long name is revealed yet.
    early, late = render_overlay(p, 0), render_overlay(p, 2)
    row = (p.style.rank_x + 60, p.style.rank_top + 2 * p.style.rank_spacing - 5,
           p.style.width, p.style.rank_top + 2 * p.style.rank_spacing + p.style.rank_font_size + 5)
    # rank 3 ("Mid size") is revealed in both (play order 3,2,1): same pixels except colour.
    assert early.crop(row).getbbox() == late.crop(row).getbbox()


def test_old_saved_rank_size_becomes_compact():
    from app.models import Style
    s = Style.model_validate({"rank_font_size": 64, "rank_spacing": 110, "rank_stroke": 6})
    assert (s.rank_font_size, s.rank_spacing, s.rank_stroke) == (46, 78, 5)
    custom = Style.model_validate({"rank_font_size": 64, "rank_spacing": 100})
    assert custom.rank_font_size == 64  # a deliberate custom size is left alone


# --------------------------------------------------------------------------- channel card
def _ink_rows(img, x0=0, x1=None):
    """Rows (y) of the image that have any opaque-ish pixel."""
    a = img.getchannel("A").crop((x0, 0, x1 or img.width, img.height))
    w, h = a.size
    data = a.tobytes()
    return [y for y in range(h) if max(data[y * w:(y + 1) * w]) > 128]


def test_channel_card_off_by_default(project_factory):
    p = project_factory()
    assert p.style.channel_card is False
    plain = render_overlay(p, 0)
    p.style.channel_name = "Someone Else"  # ignored while the card is off
    assert render_overlay(p, 0).tobytes() == plain.tobytes()


def test_channel_card_draws_avatar_and_pushes_title_down(project_factory):
    from app.overlay import CARD_AVATAR, CARD_TOP

    off = render_overlay(project_factory(), 0)
    on_p = project_factory(style={"channel_card": True})
    on = render_overlay(on_p, 0)
    # Avatar: the RankCracker picture sits in the card row; nothing is there without the card.
    mid = CARD_TOP + CARD_AVATAR // 2
    assert any(off.getpixel((x, mid))[3] == 0 for x in range(0, 1080))
    row = [on.getpixel((x, mid)) for x in range(1080)]
    assert sum(1 for c in row if c[3] == 255) > CARD_AVATAR  # avatar + name ink
    # The title moves below the card but stays in the title area (above the rank list).
    title_rows_off = [y for y in _ink_rows(off) if y < on_p.style.title_area_height]
    title_rows_on = [y for y in _ink_rows(on) if CARD_TOP + CARD_AVATAR + 10 < y < on_p.style.rank_top - 20]
    assert min(title_rows_on) > CARD_TOP + CARD_AVATAR
    assert max(title_rows_on) <= on_p.style.title_area_height
    assert min(title_rows_off) < CARD_TOP + CARD_AVATAR
    # The rank list doesn't move.
    assert off.crop((0, 450, 1080, 1920)).tobytes() == on.crop((0, 450, 1080, 1920)).tobytes()


def test_channel_card_verified_and_handle_optional(project_factory):
    full = render_overlay(project_factory(style={"channel_card": True}), 0)
    bare = render_overlay(project_factory(style={
        "channel_card": True, "channel_handle": "", "channel_verified": False}), 0)
    blue = (0x1D, 0x9B, 0xF0)
    top = (0, 0, 1080, 200)
    assert blue in _colors(full, top) and blue not in _colors(bare, top)


def test_channel_card_missing_avatar_falls_back(project_factory, tmp_path):
    p = project_factory(style={"channel_card": True, "channel_avatar": "does_not_exist.jpg"})
    img = render_overlay(p, -1)
    assert img.getchannel("A").crop((0, 0, 1080, 200)).getbbox() is not None
    # The overlay cache tells the two avatars apart.
    from app.overlay import overlay_key
    q = project_factory(style={"channel_card": True})
    assert overlay_key(p, 0) != overlay_key(q, 0)


# --------------------------------------------------------------------------- single clip + text
def _single(caption="Cats can remember a puzzle for {#FFD400:10 years}.", **style):
    from app.models import Project

    return Project(format="single", caption=caption,
                   clips=[{"url": "https://www.tiktok.com/@a/video/1", "rank": 1, "start": 0, "end": 5,
                           "source_duration": 9}],
                   style={"channel_card": True, **style})


def test_single_stacks_card_text_then_video():
    from app.overlay import CARD_AVATAR, single_layout

    p = _single()
    lay = single_layout(p)
    x, y, w, h = lay.video
    # Not downloaded yet: assumes 16:9, full width, centred-ish, everything in order top to bottom.
    assert (x, w) == (0, 1080) and abs(h - 608) <= 2
    assert lay.card_top < lay.text_top < y and y + h <= 1920 - 150
    img = render_overlay(p, 0)
    # The overlay leaves the video area clear and draws nothing below it: no rank list.
    assert img.crop((0, y + 4, 1080, 1920)).getbbox() is None
    cols = _colors(img, (0, 0, 1080, y))
    assert YELLOW in cols and (0xFF, 0x3B, 0x30) not in cols
    # Card and text are centred.
    bbox = img.getchannel("A").crop((0, lay.card_top, 1080, lay.card_top + CARD_AVATAR)).getbbox()
    assert abs((bbox[0] + bbox[2]) / 2 - 540) < 40
    tb = img.getchannel("A").crop((0, lay.text_top - 10, 1080, y)).getbbox()
    assert abs((tb[0] + tb[2]) / 2 - 540) < 20


def test_single_tall_clip_shrinks_to_fit_and_card_is_optional():
    from app.overlay import single_layout

    tall = single_layout(_single(), (720, 1280)).video
    assert tall[3] <= 1920 - 2 * 150 and tall[2] < 1080 and tall[0] == (1080 - tall[2]) // 2 // 2 * 2
    without = single_layout(_single(channel_card=False), (16, 9))
    assert without.card_top is None and without.text_top < single_layout(_single(), (16, 9)).text_top


def test_post_punctuation_sticks_to_colored_word():
    from app.overlay import post_paragraphs, text_font_path

    fp = text_font_path(_single().style)
    (line,) = post_paragraphs("up to {#FFD400:10 years}. Wow", fp)
    assert ["".join(t for t, _ in w) for w in line] == ["up", "to", "10", "years.", "Wow"]
    assert line[3] == [("years", "#FFD400"), (".", "#FFFFFF")]
    # Line breaks are kept; stray braces don't crash.
    paras = post_paragraphs("one\n\ntwo {oops", fp)
    assert len(paras) == 3 and paras[1] == [] and "".join(t for t, _ in paras[2][1]) == "{oops"


def test_single_text_shrinks_then_cuts():
    from app.overlay import SINGLE_TEXT_MAX, SINGLE_TEXT_MIN, single_layout, single_text_is_cut

    assert single_layout(_single("short")).size == SINGLE_TEXT_MAX
    medium = " ".join(["word"] * 45)
    lay = single_layout(_single(medium))
    assert SINGLE_TEXT_MIN <= lay.size < SINGLE_TEXT_MAX and not lay.cut
    assert single_text_is_cut(_single(" ".join(["longword"] * 66)))


def test_single_overlay_key_tracks_caption():
    from app.overlay import overlay_key

    assert overlay_key(_single("a"), 0) != overlay_key(_single("b"), 0)
