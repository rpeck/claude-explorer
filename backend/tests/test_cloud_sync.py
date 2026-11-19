"""N3: cloud-sync folder detection (D13).

Plan: PLANS/2026.11.18-codex-source.md §4.3 ("Gates", "Detection") and the
"U2 Rule" in reviews/2026.11.18-codex-source/council_report.md. The tests
pin the provider list, because the install paths of each client are not
verified. Every root is checked on every platform, so the tests run the
same on macOS, Linux, and Windows. All folders are synthetic.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from backend.tests._platform_home import patch_home

pytestmark = pytest.mark.xfail(strict=True, reason="N3 not implemented yet")

_ENV_VARS = (
    "OneDrive",
    "OneDriveCommercial",
    "OneDriveConsumer",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A fake home, and no real sync-client env var leaks in."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    h = tmp_path / "home"
    h.mkdir()
    patch_home(monkeypatch, h)
    return h


def detect(path: Path):
    from backend.cloud_sync import detect_cloud_sync

    return detect_cloud_sync(path)


def made(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# Each provider


def test_icloud_mobile_documents_is_detected(home):
    target = made(home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "x")
    assert "icloud" in detect(target).casefold()


def test_icloud_drive_in_userprofile_is_detected(home, tmp_path, monkeypatch):
    profile = made(tmp_path / "winprofile")
    monkeypatch.setenv("USERPROFILE", str(profile))
    target = made(profile / "iCloudDrive" / "explorer")
    assert "icloud" in detect(target).casefold()


def test_home_dropbox_is_detected(home):
    target = made(home / "Dropbox" / "explorer" / "conversations")
    assert "dropbox" in detect(target).casefold()


def test_dropbox_info_json_personal_path_is_detected(home, tmp_path):
    root = made(tmp_path / "Elsewhere" / "Team Box")
    made(home / ".dropbox")
    (home / ".dropbox" / "info.json").write_text(
        json.dumps({"personal": {"path": str(root), "host": 1}}), encoding="utf-8"
    )
    assert "dropbox" in detect(made(root / "data")).casefold()


def test_dropbox_info_json_business_path_is_detected(home, tmp_path):
    root = made(tmp_path / "Corp Sync")
    made(home / ".dropbox")
    (home / ".dropbox" / "info.json").write_text(
        json.dumps({"business": {"path": str(root)}}), encoding="utf-8"
    )
    assert "dropbox" in detect(made(root / "data")).casefold()


@pytest.mark.parametrize("env_name", ["APPDATA", "LOCALAPPDATA"])
def test_dropbox_info_json_in_windows_appdata_is_detected(
    home, tmp_path, monkeypatch, env_name
):
    appdata = made(tmp_path / env_name)
    monkeypatch.setenv(env_name, str(appdata))
    root = made(tmp_path / "D Drive Box")
    made(appdata / "Dropbox")
    (appdata / "Dropbox" / "info.json").write_text(
        json.dumps({"personal": {"path": str(root)}}), encoding="utf-8"
    )
    assert "dropbox" in detect(made(root / "data")).casefold()


def test_damaged_dropbox_info_json_fails_closed(home):
    """The Dropbox root is unknown, so any folder counts as cloud-synced."""
    made(home / ".dropbox")
    (home / ".dropbox" / "info.json").write_text("{not json", encoding="utf-8")
    assert detect(made(home / "plain" / "data"))
    assert "dropbox" in detect(made(home / "Dropbox" / "data")).casefold()


@pytest.mark.parametrize(
    "env_name", ["OneDrive", "OneDriveCommercial", "OneDriveConsumer"]
)
def test_onedrive_env_var_folder_is_detected(home, tmp_path, monkeypatch, env_name):
    root = made(tmp_path / "Contoso Files")
    monkeypatch.setenv(env_name, str(root))
    assert "onedrive" in detect(made(root / "data")).casefold()


@pytest.mark.parametrize(
    "name", ["OneDrive", "OneDrive - Contoso", "OneDrive-Personal"]
)
def test_home_onedrive_glob_is_detected(home, name):
    target = made(home / name / "data")
    assert "onedrive" in detect(target).casefold()


@pytest.mark.parametrize("name", ["Google Drive", "My Drive"])
def test_home_google_drive_is_detected(home, name):
    target = made(home / name / "data")
    assert "google" in detect(target).casefold()


@pytest.mark.parametrize(
    "name", ["Dropbox", "OneDrive-Personal", "GoogleDrive-a@example.com", "Box-Box"]
)
def test_macos_cloudstorage_folder_is_detected(home, name):
    target = made(home / "Library" / "CloudStorage" / name / "data")
    assert detect(target)


# --------------------------------------------------------------------------
# Path handling


def test_symlink_into_cloud_root_is_detected(home, tmp_path):
    real = made(home / "Dropbox" / "explorer")
    link = tmp_path / "local-looking-data"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need extra rights on this Windows host")
    assert "dropbox" in detect(link).casefold()


def test_symlinked_parent_into_cloud_root_is_detected(home, tmp_path):
    made(home / "Dropbox" / "explorer")
    link = tmp_path / "linked"
    try:
        link.symlink_to(home / "Dropbox", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need extra rights on this Windows host")
    assert "dropbox" in detect(link / "explorer" / "conversations").casefold()


def test_missing_target_under_cloud_root_is_detected(home):
    made(home / "Dropbox")
    assert "dropbox" in detect(home / "Dropbox" / "not" / "made" / "yet").casefold()


def test_cloud_root_itself_is_detected(home):
    assert "dropbox" in detect(made(home / "Dropbox")).casefold()


@pytest.mark.skipif(
    sys.platform not in ("darwin", "win32"),
    reason="case folding only on macOS and Windows",
)
def test_case_differences_are_folded(home):
    made(home / "Dropbox")
    assert "dropbox" in detect(home / "dropbox" / "data").casefold()


def test_unresolvable_path_counts_as_cloud_synced(home, monkeypatch):
    target = made(home / "plain" / "data")

    def broken(*args, **kwargs):
        raise OSError("cannot resolve")

    monkeypatch.setattr(os.path, "realpath", broken)
    assert detect(target)


# --------------------------------------------------------------------------
# Folders that are not cloud-synced


def test_plain_local_folder_is_not_detected(home):
    assert detect(made(home / ".claude-explorer" / "conversations")) is None


@pytest.mark.parametrize(
    "parts",
    [
        ("DropboxBackup", "data"),
        ("NotDropbox", "data"),
        ("Library", "Mobile DocumentsX", "data"),
        ("Library", "CloudStorageOld", "data"),
        ("Documents", "Google Drive notes"),
        ("MyDrive", "data"),
    ],
)
def test_lookalike_names_are_not_detected(home, parts):
    """Roots match whole path components, not string prefixes."""
    assert detect(made(home.joinpath(*parts))) is None


def test_folder_beside_onedrive_env_root_is_not_detected(home, tmp_path, monkeypatch):
    root = made(tmp_path / "Contoso")
    monkeypatch.setenv("OneDrive", str(root))
    assert detect(made(tmp_path / "Contoso2" / "data")) is None


def test_empty_onedrive_env_var_is_ignored(home, monkeypatch):
    """An empty value must not become "." (the current folder)."""
    monkeypatch.setenv("OneDrive", "")
    monkeypatch.chdir(home)
    assert detect(made(home / "plain" / "data")) is None


def test_detection_creates_nothing(home):
    before = sorted(p.relative_to(home) for p in home.rglob("*"))
    detect(home / "Dropbox" / "data")
    detect(home / "plain" / "data")
    assert sorted(p.relative_to(home) for p in home.rglob("*")) == before
