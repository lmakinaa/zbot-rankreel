import pytest
from pydantic import ValidationError

from app.models import Project, TitleToken, parse_title_markup, tokens_to_markup


def test_markup_basic():
    toks = parse_title_markup("RANKING {#FFD400:FUNNIEST} {#FF3B30:MOMENTS} 😭")
    assert [(t.text, t.color) for t in toks] == [
        ("RANKING", "#FFFFFF"),
        ("FUNNIEST", "#FFD400"),
        ("MOMENTS", "#FF3B30"),
        ("😭", "#FFFFFF"),
    ]


def test_markup_group_of_words_and_short_hex():
    toks = parse_title_markup("{#0f0:very funny} cats")
    assert [(t.text, t.color) for t in toks] == [
        ("very", "#00FF00"), ("funny", "#00FF00"), ("cats", "#FFFFFF"),
    ]


def test_markup_no_space_between_group_and_word():
    toks = parse_title_markup("TOP{#FF0000:5}")
    assert [t.text for t in toks] == ["TOP", "5"]


@pytest.mark.parametrize("bad", ["RANKING {FUNNIEST}", "{#FFD400:open", "close}", "{#XYZ:word}"])
def test_markup_malformed(bad):
    with pytest.raises(ValueError):
        parse_title_markup(bad)


def test_markup_roundtrip():
    s = "RANKING {#FFD400:FUNNIEST} {#FF3B30:MOMENTS}"
    assert tokens_to_markup(parse_title_markup(s)) == s


def test_project_title_accepts_markup_string(project_factory):
    p = project_factory()
    assert p.title[1] == TitleToken(text="FUNNIEST", color="#FFD400")


def test_default_play_order_is_worst_to_best(project_factory):
    assert project_factory(n=4).play_order == [4, 3, 2, 1]


def test_clip_accepts_timestamp_strings():
    p = Project.model_validate({
        "title": "x",
        "clips": [{"url": "https://www.tiktok.com/@a/video/1", "rank": 1, "start": "0:01.5", "end": "1:00"}],
    })
    assert (p.clips[0].start, p.clips[0].end) == (1.5, 60.0)


def test_bad_color_rejected():
    with pytest.raises(ValidationError):
        TitleToken(text="x", color="yellow")
