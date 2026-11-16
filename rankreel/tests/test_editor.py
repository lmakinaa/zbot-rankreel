"""Run the editor regressions with Node's built-in test runner, when available."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for editor tests")
def test_editor_regressions():
    result = subprocess.run(
        [shutil.which("node"), "--test", str(Path(__file__).with_name("editor.test.cjs"))],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
