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


def _tone_with_silence(path, lead=0.3, body=0.4, ext="mp3"):
    import subprocess
    from app.config import ffmpeg_bin
    out = path / f"padded.{ext}"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=700:duration={body}", "-af", f"adelay={int(lead * 1000)}:all=1,apad=pad_dur=0.2",
                    str(out)], check=True)
    return out


def test_prepare_sound_trims_leading_silence_and_levels(tmp_path):
    from app.sfx_gen import TARGET_RMS_DB, prepare_sound, sound_bounds
    src = _tone_with_silence(tmp_path)
    onset, end, _, _ = sound_bounds(src)
    assert 0.28 <= onset <= 0.35  # the raw file starts late
    dst = tmp_path / "ready.wav"
    dur = prepare_sound(src, dst)
    onset2, end2, rms2, peak2 = sound_bounds(dst)
    assert onset2 <= 0.006  # attack now at the very start
    assert abs(dur - 0.4) < 0.03  # leading and trailing silence removed
    assert abs(rms2 - TARGET_RMS_DB) < 1.0 and peak2 <= -0.9


def test_prepare_sound_rejects_silence(tmp_path):
    import subprocess
    import pytest
    from app.config import ffmpeg_bin
    from app.sfx_gen import prepare_sound
    src = tmp_path / "silent.wav"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", "0.5", str(src)], check=True)
    with pytest.raises(ValueError):
        prepare_sound(src, tmp_path / "out.wav")
