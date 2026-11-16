import hashlib

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


def test_emoji_dropped_from_overlay(project_factory):
    p = project_factory()
    fp = font_path(p.style)
    from app.models import parse_title_markup
    _, lines = layout_title(parse_title_markup("FUNNY 😭 CATS"), p.style, fp)
    assert [w for line in lines for w, _ in line] == ["FUNNY", "CATS"]


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
