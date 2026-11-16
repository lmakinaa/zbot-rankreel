from app.validate import validate_project


def test_valid_project(project_factory):
    res = validate_project(project_factory(n=5, order=[4, 2, 5, 3, 1]))
    assert res.errors == []
    assert res.total_seconds == 25.0
    assert res.known_total


def test_duplicate_ranks(project_factory):
    p = project_factory(n=3)
    p.clips[2].rank = 2
    p.play_order = [2, 2, 1]
    res = validate_project(p)
    assert any("Duplicate rank" in e for e in res.errors)
    assert any("Missing rank" in e and "#3" in e for e in res.errors)


def test_bad_play_order(project_factory):
    res = validate_project(project_factory(n=3, order=[3, 1, 1]))
    assert any("Playback order" in e for e in res.errors)
    res = validate_project(project_factory(n=3, order=[3, 1]))
    assert any("Playback order" in e for e in res.errors)


def test_end_not_after_start(project_factory):
    p = project_factory(n=3)
    p.clips[0].start, p.clips[0].end = 4.0, 4.0
    res = validate_project(p)
    assert any("must be after start" in e for e in res.errors)


def test_end_past_source(project_factory):
    p = project_factory(n=3)
    p.clips[0].end = 12.0
    assert any("past the video's end" in e for e in validate_project(p).errors)


def test_over_40_is_warning_not_error(project_factory):
    p = project_factory(n=5)
    for c in p.clips:
        c.end = 9.0  # 5 x 9s = 45s
    res = validate_project(p)
    assert res.errors == []
    assert res.over_limit
    assert "Total 45.0s exceeds 40s" in res.warnings
    assert any("trend clips are short" in w for w in res.warnings)  # 9s > 8s per clip


def test_hints_short_and_long(project_factory):
    p = project_factory(n=3)  # 15s
    assert any("short" in h for h in validate_project(p).hints)
    p = project_factory(n=5)
    for c in p.clips:
        c.end = 7.6  # 38s
    res = validate_project(p)
    assert not res.over_limit and any("sweet spot" in h for h in res.hints)


def test_missing_end_uses_source_duration(project_factory):
    p = project_factory(n=3)
    p.clips[0].end = None
    p.clips[0].start = None
    assert validate_project(p).clip_lengths[p.clips[0].id] == 10.0


def test_unfetched_clip_is_unknown(project_factory):
    p = project_factory(n=3)
    p.clips[0].end = None
    p.clips[0].source_duration = None
    res = validate_project(p)
    assert not res.known_total and res.errors == []
    assert any("not downloaded" in e for e in validate_project(p, require_sources=True).errors)


def test_end_hold_counts(project_factory):
    p = project_factory(n=3)
    p.style.end_hold_seconds = 0.3
    assert validate_project(p).total_seconds == 15.3
