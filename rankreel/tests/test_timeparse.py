import pytest

from app.timeparse import format_time, parse_time


@pytest.mark.parametrize(
    "s,expected",
    [
        ("5", 5.0),
        ("5.25", 5.25),
        ("1:05", 65.0),
        ("1:05.5", 65.5),
        ("01:02:03", 3723.0),
        ("0:00:01.25", 1.25),
        (" 12 ", 12.0),
        ("1,5", 1.5),
        (7, 7.0),
        (2.5, 2.5),
    ],
)
def test_valid(s, expected):
    assert parse_time(s) == pytest.approx(expected)


@pytest.mark.parametrize("s", [None, ""])
def test_empty_is_none(s):
    assert parse_time(s) is None


@pytest.mark.parametrize("s", ["abc", "1:2:3:4", "1:75", "1:60:00", "-3", "1.5:20", ":30", "5s", True, -1])
def test_invalid(s):
    with pytest.raises(ValueError):
        parse_time(s)


def test_format():
    assert format_time(65.5) == "1:05.5"
    assert format_time(3723) == "1:02:03.0"
