from app.probe import probe
from app.sfx_gen import SOUNDS, ensure_default_sfx


def test_generates_all_sounds(tmp_path):
    paths = ensure_default_sfx(tmp_path)
    assert sorted(p.name for p in paths) == sorted(SOUNDS)
    for p in paths:
        info = probe(p)
        assert info.has_audio and not info.has_video
        assert 0.3 <= info.duration <= 0.6


def test_does_not_overwrite_without_force(tmp_path):
    (tmp_path / "vibrate.wav").write_bytes(b"custom")
    ensure_default_sfx(tmp_path)
    assert (tmp_path / "vibrate.wav").read_bytes() == b"custom"
