"""``/api/config`` reports the home folder, so the web page can show
project paths as ``~/Source/clientco/ai`` (PLANS/2026.10.26-list-project-key.md).

The page has no other way to know the home folder: a browser cannot read
it, and guessing it from the paths (the longest common prefix) breaks for
a user whose projects all sit under one folder such as ``~/Source``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.main import app
from backend.tests._platform_home import patch_home


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAUDE_EXPLORER_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_EXPORTER_DATA_DIR", raising=False)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def test_config_reports_the_home_folder(isolated_home: Path) -> None:
    body = TestClient(app).get("/api/config").json()
    assert body["home_dir"] == str(isolated_home)


def test_config_follows_a_changed_home(isolated_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Not a value frozen at import time: a second home gives a second answer."""
    other = tmp_path_factory.mktemp("other-home")
    patch_home(monkeypatch, other)
    body = TestClient(app).get("/api/config").json()
    assert body["home_dir"] == str(other)
