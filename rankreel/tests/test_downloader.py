import json
import os

import pytest

from app import downloader as dl
from app.synthetic import make_test_clip


def test_normalize_strips_tiktok_tracking():
    a = "https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc"
    b = "https://www.tiktok.com/@user/video/123/"
    assert dl.normalize_url(a) == dl.normalize_url(b) == "https://www.tiktok.com/@user/video/123"
    assert dl.cache_key(a) == dl.cache_key(b)
    # Non-canonical links (short links) are kept as-is.
    assert dl.normalize_url("https://vm.tiktok.com/ZMabc/") == "https://vm.tiktok.com/ZMabc/"


@pytest.mark.parametrize("msg,expect", [
    ("ERROR: This video is private", "private"),
    ("HTTP Error 429: Too Many Requests", "rate-limiting"),
    ("ERROR: [TikTok] 123: No video formats found!", "photo/slideshow"),
    ("ERROR: Unsupported URL: https://example.com", "Unsupported URL"),
    ("ERROR: [TikTok] 1: Video not available, status code 10204", "removed"),
    ("This post may not be comfortable for some audiences. Log in for access", "logged-in"),
])
def test_friendly_errors(msg, expect):
    assert expect in dl._friendly(msg)


def test_rejects_non_url(tmp_path):
    with pytest.raises(dl.FetchError):
        dl.fetch("not a url", tmp_path)


def test_cache_hit_skips_network(tmp_path, monkeypatch):
    url = "https://www.tiktok.com/@user/video/42"
    key = dl.cache_key(url)
    make_test_clip(tmp_path / f"{key}.mp4", duration=2)
    (tmp_path / f"{key}.json").write_text(json.dumps({
        "url": url, "webpage_url": url, "uploader": "user", "creator": "User",
        "title": "t", "duration": 2.0, "has_audio": True, "file": f"{key}.mp4",
    }))
    monkeypatch.setattr(dl, "_download", lambda *a: pytest.fail("should not download"))
    meta = dl.fetch(url + "?lang=en", tmp_path)
    assert meta.handle == "@user" and meta.duration == 2.0


def test_fill_from_cache(tmp_path, project_factory):
    p = project_factory(n=3)
    for c in p.clips:
        c.source_duration = None
    url = str(p.clips[0].url)
    key = dl.cache_key(url)
    (tmp_path / f"{key}.mp4").write_bytes(b"x")
    (tmp_path / f"{key}.json").write_text(json.dumps({
        "url": url, "webpage_url": url, "uploader": "@me", "creator": None,
        "title": None, "duration": 7.5, "has_audio": True, "file": f"{key}.mp4",
    }))
    dl.fill_from_cache(p, tmp_path)
    assert p.clips[0].source_duration == 7.5 and p.clips[0].creator == "@me"
    assert p.clips[1].source_duration is None


@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("RANKREEL_NETWORK_TESTS"), reason="set RANKREEL_NETWORK_TESTS=1")
def test_real_tiktok_download(tmp_path):
    url = os.environ.get("RANKREEL_TEST_URL", "https://www.tiktok.com/@scout2015/video/6718335390845095173")
    meta = dl.fetch(url, tmp_path)
    assert meta.duration > 0 and (tmp_path / meta.file).exists()
    assert meta.handle
