"""Integration: synthetic clips -> final MP4, checked with ffprobe."""

import pytest

from app.compose import compose
from app.models import Project
from app.probe import probe
from app.render_job import credits_text
from app.synthetic import make_test_clip

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    return [
        make_test_clip(d / "a.mp4", duration=4, w=720, h=1280, audio=True, freq=440),
        make_test_clip(d / "b.mp4", duration=4, w=640, h=640, audio=False),  # no audio, square
        make_test_clip(d / "c.mp4", duration=5, w=1280, h=720, audio=True, freq=660),  # landscape
    ]


def _project(clips, **style):
    return Project.model_validate({
        "title": "TOP {#FFD400:3} TEST",
        "clips": [
            {"url": "https://www.tiktok.com/@a/video/1", "rank": 1, "name": "One",
             "start": 0.5, "end": 3.5, "local_path": str(clips[0]), "creator": "@a"},
            {"url": "https://www.tiktok.com/@b/video/2", "rank": 2, "name": "Two",
             "start": 1.0, "end": 3.0, "local_path": str(clips[1]), "creator": "@b"},
            {"url": "https://www.tiktok.com/@c/video/3", "rank": 3, "name": "Three",
             "start": "0:01", "end": "0:03.5", "local_path": str(clips[2])},
        ],
        "play_order": [3, 2, 1],
        "style": style,
    })


@pytest.mark.parametrize("style,expected", [
    ({}, 2.5 + 2.0 + 3.0),
    ({"fit": "contain", "end_hold_seconds": 0.3, "loudnorm": False}, 2.5 + 2.0 + 3.0 + 0.3),
])
def test_compose_output(clips, tmp_path, style, expected):
    out = compose(_project(clips, **style), tmp_path / "out.mp4", tmp_path / "work")
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_video and info.has_audio
    assert info.duration == pytest.approx(expected, abs=0.1)
    assert not (tmp_path / "work").exists()  # cleaned up


def test_compose_with_sfx(clips, tmp_path):
    from app.synthetic import make_test_clip as mk
    sfx = tmp_path / "whoosh.m4a"
    # A 0.3s tone stands in for a whoosh (audio-only file).
    import subprocess
    from app.config import ffmpeg_bin
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=1000:duration=0.3", "-c:a", "aac", str(sfx)], check=True)
    out = compose(_project(clips, transition_sfx=str(sfx)), tmp_path / "o.mp4", tmp_path / "w")
    info = probe(out)
    assert info.has_audio and info.duration == pytest.approx(7.5, abs=0.1)


def test_credits_lists_every_creator(clips):
    text = credits_text(_project(clips))
    assert text.startswith("Clips: ")
    assert "@a (https://www.tiktok.com/@a/video/1)" in text
    assert "@b" in text and "unknown creator" in text  # clip 3 has no creator


@pytest.mark.parametrize("kind", ["flash", "zoom", "punch", "fade", "slide", "shake"])
def test_transitions_keep_duration(clips, tmp_path, kind):
    out = compose(_project(clips, transition=kind, transition_seconds=0.3),
                  tmp_path / "t.mp4", tmp_path / "w")
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    assert info.duration == pytest.approx(7.5, abs=0.1)


def _frame_mean(path, t):
    """Mean brightness of the bottom of the frame (below the rank list) at time t."""
    import subprocess
    from app.config import ffmpeg_bin
    raw = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1",
         "-vf", "crop=1080:400:0:1500,scale=54:20,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    return sum(raw) / len(raw)


def test_flash_is_white_at_cut_and_first_clip_untouched(clips, tmp_path):
    p = _project(clips, transition="flash", transition_seconds=0.4)
    out = compose(p, tmp_path / "f.mp4", tmp_path / "w")
    plain = compose(_project(clips), tmp_path / "p.mp4", tmp_path / "w2")
    cut = 2.5  # first segment (#3) is 2.5s
    assert _frame_mean(out, cut + 0.02) > _frame_mean(plain, cut + 0.02) + 40  # flash at the cut
    assert abs(_frame_mean(out, 0.02) - _frame_mean(plain, 0.02)) < 3  # no flash on the hook
    assert abs(_frame_mean(out, cut + 1.0) - _frame_mean(plain, cut + 1.0)) < 3  # gone after


def test_old_project_without_transition_fields_loads():
    p = Project.model_validate({"title": "x", "clips": [
        {"url": "https://www.tiktok.com/@a/video/1", "rank": 1}], "style": {"fit": "cover"}})
    assert p.style.transition == "none" and p.style.transition_seconds == 0.25


def _rms_db(path, start, dur):
    """RMS level (dB) of the mixed audio between start and start+dur."""
    import re, subprocess
    from app.config import ffmpeg_bin
    err = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-ss", f"{start}", "-t", f"{dur}", "-i", str(path),
         "-af", "astats=measure_overall=RMS_level:measure_perchannel=none", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    m = re.search(r"RMS level dB:\s*(-?inf|-?[\d.]+)", err)
    return float("-inf") if m.group(1).endswith("inf") else float(m.group(1))


def test_vibrate_sound_lands_on_each_cut(tmp_path):
    """Silent clips + the built-in vibrate sound: silence on the hook, buzz at each cut."""
    from app.sfx_gen import ensure_default_sfx
    from app.synthetic import make_test_clip as mk
    sfx_dir = tmp_path / "sfx"
    ensure_default_sfx(sfx_dir)
    srcs = [mk(tmp_path / f"s{i}.mp4", duration=3, audio=False) for i in range(3)]
    p = Project.model_validate({
        "title": "T", "play_order": [3, 2, 1],
        "clips": [{"url": f"https://www.tiktok.com/@a/video/{i}", "rank": i + 1, "start": 0, "end": 2,
                   "local_path": str(srcs[i])} for i in range(3)],
        "style": {"transition": "shake", "transition_sfx": str(sfx_dir / "vibrate.wav")},
    })
    out = compose(p, tmp_path / "o.mp4", tmp_path / "w")
    assert _rms_db(out, 0.05, 1.5) < -60  # opening hook: no sound
    assert _rms_db(out, 2.0, 0.3) > -30  # first cut: buzz
    assert _rms_db(out, 2.6, 1.2) < -60  # gone again
    assert _rms_db(out, 4.0, 0.3) > -30  # second cut: buzz


def _onsets(path, gate_db=-35.0, min_gap=0.5):
    """Times where the mixed audio jumps above gate_db (5 ms resolution)."""
    import array, math, subprocess
    from app.config import ffmpeg_bin
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    pcm = array.array("h"); pcm.frombytes(raw)
    win, gate, out, last = 40, 32768 * 10 ** (gate_db / 20), [], -9.0
    for i in range(0, len(pcm), win):
        c = pcm[i:i + win]
        if c and math.sqrt(sum(x * x for x in c) / len(c)) >= gate:
            t = i / 8000
            if t - last > min_gap:
                out.append(t)
            last = t
    return out


@pytest.mark.parametrize("which", ["padded", "core"])
def test_transition_sound_hits_exactly_on_the_cut(tmp_path, which):
    """End to end: the sound's attack must land within one frame of each cut, together
    with the visual transition, even when the source file starts with silence."""
    from app.config import SFX_DIR
    from app.sfx_gen import DEFAULT_SFX, prepare_sound
    from app.synthetic import make_test_clip as mk
    if which == "core":
        sfx = SFX_DIR / DEFAULT_SFX
        if not sfx.exists():
            pytest.skip("core_sound_effect.wav not installed on this machine")
    else:
        import subprocess
        from app.config import ffmpeg_bin
        raw = tmp_path / "padded.mp3"
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=700:duration=0.4", "-af", "adelay=300:all=1", str(raw)], check=True)
        sfx = tmp_path / "ready.wav"
        prepare_sound(raw, sfx)
    srcs = [mk(tmp_path / f"s{i}.mp4", duration=3, audio=False) for i in range(3)]
    p = Project.model_validate({
        "title": "T", "play_order": [3, 2, 1],
        "clips": [{"url": f"https://www.tiktok.com/@a/video/{i}", "rank": i + 1, "start": 0, "end": 2.0,
                   "local_path": str(srcs[i])} for i in range(3)],
        "style": {"transition": "flash", "transition_sfx": str(sfx)},
    })
    out = compose(p, tmp_path / "o.mp4", tmp_path / "w")
    onsets = _onsets(out)
    assert len(onsets) == 2, onsets  # one hit per cut, none on the opening clip
    for got, cut in zip(onsets, [2.0, 4.0]):
        assert abs(got - cut) <= 1 / 30, f"sound starts at {got:.3f}s, cut is at {cut}s"


def _sound_spans(path, gate_db=-40.0):
    """[(start, end)] where the audio is above gate_db (5 ms resolution, 30 ms gaps merged)."""
    import array, math, subprocess
    from app.config import ffmpeg_bin
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    pcm = array.array("h"); pcm.frombytes(raw)
    win, gate, spans = 40, 32768 * 10 ** (gate_db / 20), []
    for i in range(0, len(pcm), win):
        c = pcm[i:i + win]
        if c and math.sqrt(sum(x * x for x in c) / len(c)) >= gate:
            t = i / 8000
            if spans and t - spans[-1][1] <= 0.03:
                spans[-1][1] = t + win / 8000
            else:
                spans.append([t, t + win / 8000])
    return [tuple(s) for s in spans]


@pytest.fixture(scope="module")
def long_sound(tmp_path_factory):
    """A 1.0 s tone with leading silence: longer than any transition."""
    import subprocess
    from app.config import ffmpeg_bin
    from app.sfx_gen import prepare_sound
    d = tmp_path_factory.mktemp("snd")
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=600:duration=1.0", "-af", "adelay=200:all=1", str(d / "raw.mp3")], check=True)
    prepare_sound(d / "raw.mp3", d / "long.wav")
    return d / "long.wav"


@pytest.mark.parametrize("kind,seconds,window", [
    ("flash", 0.25, (0.0, 0.25)),   # effect runs from the cut for its duration
    ("punch", 0.4, (0.0, 0.4)),
    ("shake", 0.15, (0.0, 0.15)),
    ("fade", 0.4, (-0.2, 0.2)),     # dip to black straddles the cut
    ("none", 0.25, (0.0, 1.0)),     # no visual transition: the whole sound plays
])
def test_sound_fits_the_transition_window(tmp_path, long_sound, kind, seconds, window):
    from app.synthetic import make_test_clip as mk
    srcs = [mk(tmp_path / f"s{i}.mp4", duration=3, audio=False) for i in range(3)]
    p = Project.model_validate({
        "title": "T", "play_order": [3, 2, 1],
        "clips": [{"url": f"https://www.tiktok.com/@a/video/{i}", "rank": i + 1, "start": 0, "end": 2.0,
                   "local_path": str(srcs[i])} for i in range(3)],
        "style": {"transition": kind, "transition_seconds": seconds, "transition_sfx": str(long_sound)},
    })
    out = compose(p, tmp_path / "o.mp4", tmp_path / "w")
    spans = _sound_spans(out)
    assert len(spans) == 2, spans  # one sound per cut, nothing on the opening clip
    for (s, e), cut in zip(spans, [2.0, 4.0]):
        want_s, want_e = cut + window[0], cut + window[1]
        assert abs(s - want_s) <= 1 / 30, f"{kind}: sound starts {s:.3f}s, transition starts {want_s:.3f}s"
        assert abs(e - want_e) <= 0.04, f"{kind}: sound ends {e:.3f}s, transition ends {want_e:.3f}s"
