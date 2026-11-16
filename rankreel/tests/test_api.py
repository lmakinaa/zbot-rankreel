"""API tests (offline): synthetic clips are seeded into the download cache."""

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import DOWNLOAD_DIR, OUTPUT_DIR, PROJECTS_DIR
from app.downloader import cache_key
from app.main import app
from app.synthetic import make_test_clip

client = TestClient(app)


@pytest.fixture(scope="module")
def seeded():
    """Three fake TikTok URLs whose clips are already 'downloaded'."""
    tag = uuid.uuid4().hex[:8]
    urls, files = [], []
    for i in range(1, 4):
        url = f"https://www.tiktok.com/@test{tag}/video/{i}"
        key = cache_key(url)
        mp4, meta = DOWNLOAD_DIR / f"{key}.mp4", DOWNLOAD_DIR / f"{key}.json"
        make_test_clip(mp4, duration=3, audio=i != 2)
        meta.write_text(json.dumps({
            "url": url, "webpage_url": url, "uploader": f"test{i}", "creator": None,
            "title": None, "duration": 3.0, "has_audio": i != 2, "file": mp4.name,
        }))
        urls.append(url)
        files += [mp4, meta]
    yield urls
    for f in files:
        f.unlink(missing_ok=True)


def _project(urls, **extra):
    return {
        "title": [{"text": "TOP", "color": "#FFFFFF"}, {"text": "3", "color": "#FFD400"}],
        "clips": [{"url": u, "rank": i + 1, "name": f"Clip {i + 1}", "start": "0.5", "end": "2.5"}
                  for i, u in enumerate(urls)],
        "play_order": [3, 2, 1],
        **extra,
    }


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "RankReel" in r.text


def test_fetch_uses_cache(seeded):
    r = client.post("/api/clips/fetch", json={"url": seeded[0] + "?lang=en"})
    assert r.status_code == 200
    body = r.json()
    assert body["duration"] == 3.0 and body["creator"] == "@test1"
    assert "local_path" not in body
    assert client.get(body["preview_url"], headers={"Range": "bytes=0-99"}).status_code == 206


def test_fetch_bad_url():
    r = client.post("/api/clips/fetch", json={"url": "nope"})
    assert r.status_code == 422 and "URL" in r.json()["detail"]


def test_validate_fills_durations_from_cache(seeded):
    r = client.post("/api/validate", json=_project(seeded))
    body = r.json()
    assert body["errors"] == [] and body["total_seconds"] == 6.0 and body["known_total"]


def test_validate_reports_errors(seeded):
    r = client.post("/api/validate", json=_project(seeded, play_order=[1, 1, 2]))
    assert any("Playback order" in e for e in r.json()["errors"])


def test_api_ignores_client_local_path(seeded):
    from app.downloader import fill_from_cache
    from app.models import Project
    p = _project(seeded)
    p["clips"][0]["local_path"] = "/etc/passwd"
    p["clips"][1]["local_path"] = "/etc/passwd"
    p["clips"][1]["url"] = "https://www.tiktok.com/@nobody/video/999"
    project = Project.model_validate(p)
    fill_from_cache(project, trust_local=False)
    assert project.clips[0].local_path.startswith(str(DOWNLOAD_DIR))  # replaced by cache
    assert project.clips[1].local_path is None  # not cached -> dropped


def test_preview_overlay_png(seeded):
    r = client.post("/api/preview-overlay", json={"project": _project(seeded), "step": 1})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:4] == b"\x89PNG"


def test_title_parse():
    r = client.post("/api/title/parse", json={"markup": "A {#FF0000:B}"})
    assert r.json() == [{"text": "A", "color": "#FFFFFF"}, {"text": "B", "color": "#FF0000"}]
    assert client.post("/api/title/parse", json={"markup": "{oops"}).status_code == 422


def test_media_path_traversal():
    assert client.get("/media/..%2F..%2Fapp%2Fmain.py").status_code == 404
    assert client.get("/output/.hidden").status_code == 404


def test_render_rejects_invalid(seeded):
    r = client.post("/api/render", json=_project(seeded, play_order=[3, 2]))
    assert r.status_code == 422


@pytest.mark.slow
def test_render_job_end_to_end(seeded):
    r = client.post("/api/render", json=_project(seeded, style={"end_hold_seconds": 0.3}))
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        st = client.get(f"/api/render/{job_id}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.3)
    assert st["status"] == "done", st
    assert "@test1" in st["credits_text"]
    assert client.get(st["output_url"]).status_code == 200
    for name in (st["output_url"].split("/")[-1], st["credits_url"].split("/")[-1]):
        (OUTPUT_DIR / name).unlink(missing_ok=True)
    (PROJECTS_DIR / st["project_file"]).unlink(missing_ok=True)


def test_projects_save_list_load(seeded):
    name = f"test_{uuid.uuid4().hex[:6]}"
    r = client.post("/api/projects", json={"name": name, "project": _project(seeded)})
    assert r.json()["name"] == name
    try:
        assert any(p["name"] == name for p in client.get("/api/projects").json())
        loaded = client.get(f"/api/projects/{name}").json()
        assert loaded["play_order"] == [3, 2, 1] and loaded["clips"][0]["start"] == 0.5
    finally:
        (PROJECTS_DIR / f"{name}.json").unlink(missing_ok=True)


def test_unknown_job():
    assert client.get("/api/render/nope").status_code == 404


def test_builtin_vibration_sounds_available():
    items = client.get("/api/sfx").json()
    builtin = {i["name"] for i in items if i["builtin"]}
    assert {"vibrate.wav", "vibrate_double.wav", "vibrate_heavy.wav"} <= builtin
    r = client.get("/sfx/vibrate.wav")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert client.get("/sfx/..%2Fapp%2Fmain.py").status_code == 404



def _tone(tmp_path, seconds=0.5, ext="wav"):
    import subprocess
    from app.config import ffmpeg_bin
    p = tmp_path / f"tone.{ext}"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=800:duration={seconds}", str(p)], check=True)
    return p.read_bytes()


def test_upload_custom_sound_and_delete(tmp_path):
    from app.config import SFX_DIR
    r = client.post("/api/sfx?filename=My Whoosh!.wav", content=_tone(tmp_path))
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    try:
        assert name == "my_whoosh.wav" or name.startswith("my_whoosh_")
        items = {i["name"]: i["builtin"] for i in client.get("/api/sfx").json()}
        assert items[name] is False
        assert client.get(f"/sfx/{name}").status_code == 200
    finally:
        r = client.delete(f"/api/sfx/{name}")
    assert r.status_code == 200 and not (SFX_DIR / name).exists()


def test_upload_never_overwrites_builtin(tmp_path):
    r = client.post("/api/sfx?filename=vibrate.wav", content=_tone(tmp_path))
    name = r.json()["name"]
    try:
        assert name != "vibrate.wav" and name.startswith("vibrate_")
    finally:
        client.delete(f"/api/sfx/{name}")


@pytest.mark.parametrize("filename,body,code,msg", [
    ("notes.txt", b"hello", 422, "audio file"),
    ("fake.mp3", b"definitely not audio", 422, "audio"),
    ("empty.wav", b"", 422, "empty"),
])
def test_upload_rejects_bad_files(filename, body, code, msg):
    r = client.post(f"/api/sfx?filename={filename}", content=body)
    assert r.status_code == code and msg in r.json()["detail"]


def test_upload_rejects_long_sound(tmp_path):
    r = client.post("/api/sfx?filename=song.wav", content=_tone(tmp_path, seconds=12))
    assert r.status_code == 422 and "under 10s" in r.json()["detail"]


def test_cannot_delete_builtin_or_escape():
    assert client.delete("/api/sfx/vibrate.wav").status_code == 422
    assert client.delete("/api/sfx/..%2Fconfig.py").status_code in (404, 405)
    assert client.delete("/api/sfx/README.txt").status_code == 404


def test_missing_sound_is_a_validation_error(seeded):
    body = client.post("/api/validate", json=_project(seeded, style={"transition_sfx": "gone.wav"})).json()
    assert any("Transition sound not found" in e for e in body["errors"])
    r = client.post("/api/validate", json=_project(seeded, style={"transition_sfx": "../../etc/x.wav"}))
    assert r.status_code == 422


# ---------------------------------------------------------------- import popup
USER_PROJECT = {
    "title": [{"text": "RANKING", "color": "#FFFFFF"}, {"text": "FUNNIEST", "color": "#FFD400"},
              {"text": "SCARE", "color": "#FF3B30"}, {"text": "😭", "color": "#FFFFFF"}],
    "clips": [
        {"id": "77b7938f5583", "url": "https://www.tiktok.com/@scare.prank.usa5/video/7356216950659484974",
         "start": 20.0, "end": 23.4, "rank": 2, "name": "Mickey shirt panic", "local_path": "/etc/passwd",
         "source_duration": None, "creator": None, "webpage_url": None},
        {"id": "56b4ad32135d", "url": "https://www.tiktok.com/@scare.prank.usa5/video/7356216950659484974",
         "start": 14.9, "end": 18.9, "rank": 1, "name": "Suit man evacuates"},
        {"id": "c61973870697", "url": "https://www.tiktok.com/@weirddarkchocolate/video/7659645889635536158",
         "start": 2.9, "end": 7.6, "rank": 3, "name": "Streamer jumpscared"},
    ],
    "play_order": [2, 3, 1], "style": {}, "max_seconds_warning": 40.0,
}


def test_import_valid_project():
    r = client.post("/api/projects/import", json={"text": json.dumps(USER_PROJECT)})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["play_order"] == [2, 3, 1] and p["clips"][0]["name"] == "Mickey shirt panic"
    assert p["clips"][0]["local_path"] is None  # pasted local paths are dropped
    assert p["title"][1] == {"text": "FUNNIEST", "color": "#FFD400"}


def test_import_accepts_code_fence_and_chat_text():
    text = "Here's your project:\n```json\n" + json.dumps(USER_PROJECT, indent=2) + "\n```\nEnjoy!"
    assert client.post("/api/projects/import", json={"text": text}).status_code == 200
    assert client.post("/api/projects/import", json={"text": "blah " + json.dumps(USER_PROJECT)}).status_code == 200


@pytest.mark.parametrize("text,msg", [
    ("", "Paste the project JSON"),
    ('{"title": [], "clips": [', "isn't valid JSON"),
    ('{"hello": 1}', "isn't a RankReel project"),
    (json.dumps({**USER_PROJECT, "clips": [{**USER_PROJECT["clips"][0], "url": "not a link"}]}), "clip 1.url"),
])
def test_import_errors_are_readable(text, msg):
    r = client.post("/api/projects/import", json={"text": text})
    assert r.status_code == 422 and msg in r.json()["detail"], r.json()


def test_save_unique_never_overwrites():
    name = f"test_import_{uuid.uuid4().hex[:6]}"
    body = {"name": name, "project": USER_PROJECT}
    try:
        assert client.post("/api/projects", json=body).json()["name"] == name
        assert client.post("/api/projects", json={**body, "unique": True}).json()["name"] == f"{name}_2"
        assert client.post("/api/projects", json={**body, "unique": True}).json()["name"] == f"{name}_3"
        saved = json.loads((PROJECTS_DIR / f"{name}.json").read_text())
        assert saved["clips"][0]["local_path"] is None
    finally:
        for suffix in ("", "_2", "_3"):
            (PROJECTS_DIR / f"{name}{suffix}.json").unlink(missing_ok=True)



def test_uploaded_mp3_is_trimmed_to_hit_on_the_cut(tmp_path):
    import subprocess
    from app.config import SFX_DIR, ffmpeg_bin
    from app.sfx_gen import sound_bounds
    src = tmp_path / "late.mp3"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=900:duration=0.5", "-af", "adelay=250:all=1", str(src)], check=True)
    r = client.post("/api/sfx?filename=Late Hit.mp3", content=src.read_bytes())
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    try:
        assert name.startswith("late_hit") and name.endswith(".wav")
        assert sound_bounds(SFX_DIR / name)[0] <= 0.006
    finally:
        client.delete(f"/api/sfx/{name}")


def test_default_sound_is_protected_if_installed():
    from app.config import SFX_DIR
    from app.sfx_gen import DEFAULT_SFX
    if not (SFX_DIR / DEFAULT_SFX).exists():
        pytest.skip("default sound not installed on this machine")
    items = {i["name"]: i["builtin"] for i in client.get("/api/sfx").json()}
    assert items[DEFAULT_SFX] is True
    assert client.delete(f"/api/sfx/{DEFAULT_SFX}").status_code == 422



def test_old_core_sound_name_still_works(seeded):
    """Projects saved while the default was core_sound_effect.mp3 find the prepared .wav."""
    from app.config import SFX_DIR, sfx_path
    if not (SFX_DIR / "core_sound_effect.wav").exists():
        pytest.skip("default sound not installed on this machine")
    assert sfx_path("core_sound_effect.mp3") == SFX_DIR / "core_sound_effect.wav"
    body = client.post("/api/validate", json=_project(seeded, style={"transition_sfx": "core_sound_effect.mp3"})).json()
    assert not any("sound" in e for e in body["errors"]), body["errors"]


def test_avatar_upload_and_path_rejected(seeded, tmp_path, monkeypatch):
    import io as _io

    from PIL import Image as _Image

    from app import main as m

    monkeypatch.setattr(m, "BRAND_DIR", tmp_path)
    buf = _io.BytesIO()
    _Image.new("RGB", (800, 600), (200, 30, 30)).save(buf, format="PNG")
    r = client.post("/api/avatar?filename=My Face.png", content=buf.getvalue())
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert name == "my_face.jpg" and _Image.open(tmp_path / name).size == (512, 512)
    assert client.post("/api/avatar?filename=x.png", content=b"not an image").status_code == 422
    bad = _project(seeded, style={"channel_card": True, "channel_avatar": "../../etc/passwd"})
    assert client.post("/api/preview-overlay", json={"project": bad, "step": 0}).status_code == 422
