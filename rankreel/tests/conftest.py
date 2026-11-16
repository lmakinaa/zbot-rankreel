import pytest

from app.models import Project


def make_project(n=5, order=None, names=None, **kw):
    names = names or [f"Clip {i}" for i in range(1, n + 1)]
    clips = [
        {"url": f"https://www.tiktok.com/@u{i}/video/{i}", "rank": i, "name": names[i - 1],
         "start": 0, "end": 5, "source_duration": 10}
        for i in range(1, n + 1)
    ]
    data = {"title": "RANKING {#FFD400:FUNNIEST} {#FF3B30:MOMENTS}", "clips": clips}
    if order is not None:
        data["play_order"] = order
    data.update(kw)
    return Project.model_validate(data)


@pytest.fixture
def project_factory():
    return make_project
