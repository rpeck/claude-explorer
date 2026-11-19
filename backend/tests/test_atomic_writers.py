"""L2: the bookmarks and preferences writers replace their file atomically.

Both writers must go through ``fetcher.atomic.atomic_write_bytes``:

- a unique temp file in the target folder, so two writes never share a name;
- ``fsync`` of the temp file before the rename, so a crash cannot leave an
  empty file;
- the Windows retry of ``atomic_replace`` for a transient sharing violation.

The tests drive the HTTP routes and watch the ``os.fsync`` and ``os.replace``
boundary (TESTING.md §5.2, §5.8). They do not pin which helper the code calls.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient

BOOKMARK = {
    "conversation_id": "conv-1",
    "message_uuid": "msg-1",
    "source": "claude_code",
    "snippet": "a snippet",
}


@pytest.fixture
def bookmarks(tmp_path, monkeypatch):
    target = tmp_path / "bm" / "bookmarks.json"
    monkeypatch.setenv("CLAUDE_EXPLORER_BOOKMARKS_FILE", str(target))
    import backend.routers.bookmarks as bm_router
    from backend import main as backend_main

    importlib.reload(bm_router)
    importlib.reload(backend_main)
    return TestClient(backend_main.app), target


@pytest.fixture
def prefs(tmp_path, monkeypatch):
    data_dir = tmp_path / "home" / "conversations"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import config as cfg

    cfg.get_settings.cache_clear()
    import backend.routers.preferences as prefs_router
    from backend import main as backend_main

    importlib.reload(prefs_router)
    importlib.reload(backend_main)
    return TestClient(backend_main.app), data_dir.parent / "preferences.json"


@pytest.fixture
def fs_spy(monkeypatch):
    """Record each ``os.fsync`` (by inode) and each ``os.replace`` in order."""
    events: list[tuple] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        events.append(("fsync", os.fstat(fd).st_ino))
        return real_fsync(fd)

    def replace(src, dst, *args, **kwargs):
        events.append(("replace", os.fspath(src), os.fspath(dst), os.stat(src).st_ino))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    return events


def _replaces_into(events, target):
    return [e for e in events if e[0] == "replace" and e[2] == os.fspath(target)]


def _seed_bookmark(client) -> str:
    r = client.post("/api/bookmarks", json=BOOKMARK)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bookmark_write(client, kind):
    if kind == "create":
        r = client.post("/api/bookmarks", json={**BOOKMARK, "message_uuid": "msg-2"})
    elif kind == "update":
        bid = client.get("/api/bookmarks").json()["bookmarks"][0]["id"]
        r = client.patch(f"/api/bookmarks/{bid}", json={"note": "new note"})
    else:
        bid = client.get("/api/bookmarks").json()["bookmarks"][0]["id"]
        r = client.delete(f"/api/bookmarks/{bid}")
    assert r.status_code < 300, r.text


def _prefs_write(client, kind, value="dark"):
    call = client.put if kind == "put" else client.patch
    r = call("/api/preferences", json={"data": {"theme": value}})
    assert r.status_code == 200, r.text


def _assert_fsync_before_replace(events, target):
    replaces = _replaces_into(events, target)
    assert len(replaces) == 1, f"expected one replace into {target}, got {events}"
    replace_at = events.index(replaces[0])
    synced = {e[1] for e in events[:replace_at] if e[0] == "fsync"}
    assert replaces[0][3] in synced, (
        f"the temp file {replaces[0][1]} was not fsynced before the replace; events={events}"
    )


def _assert_unique_temps_in_target_folder(events, target):
    srcs = [e[1] for e in _replaces_into(events, target)]
    assert len(srcs) == 2, f"expected two replaces into {target}, got {events}"
    assert all(os.path.dirname(s) == os.fspath(target.parent) for s in srcs), srcs
    assert srcs[0] != srcs[1], f"two writes used the same temp name: {srcs[0]}"


def _sharing_violation_once(monkeypatch, target):
    """Make the first replace into ``target`` fail like a Windows sharing violation."""
    real_replace = os.replace
    calls = {"n": 0}

    def replace(src, dst, *args, **kwargs):
        if os.fspath(dst) == os.fspath(target):
            calls["n"] += 1
            if calls["n"] == 1:
                exc = PermissionError(13, "The process cannot access the file")
                exc.winerror = 32
                raise exc
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    return calls


def _fail_replace(monkeypatch, target):
    real_replace = os.replace

    def replace(src, dst, *args, **kwargs):
        if os.fspath(dst) == os.fspath(target):
            raise OSError("simulated rename failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)


def _leftovers(target):
    return sorted(p.name for p in target.parent.iterdir() if p.is_file() and p != target)


# --- bookmarks -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["create", "update", "delete"])
def test_bookmark_write_fsyncs_temp_file_before_replace(bookmarks, fs_spy, kind):
    client, target = bookmarks
    _seed_bookmark(client)
    fs_spy.clear()
    _bookmark_write(client, kind)
    _assert_fsync_before_replace(fs_spy, target)


def test_bookmark_writes_use_unique_temp_files_in_target_folder(bookmarks, fs_spy):
    client, target = bookmarks
    _seed_bookmark(client)
    _bookmark_write(client, "update")
    _assert_unique_temps_in_target_folder(fs_spy, target)


def test_bookmark_write_retries_windows_sharing_violation(bookmarks, monkeypatch):
    client, target = bookmarks
    calls = _sharing_violation_once(monkeypatch, target)
    r = client.post("/api/bookmarks", json=BOOKMARK)
    assert r.status_code == 201, r.text
    assert calls["n"] == 2, f"expected one retry, got {calls['n']} replace calls"
    assert client.get("/api/bookmarks").json()["bookmarks"][0]["id"] == r.json()["id"]


def test_bookmark_failed_replace_keeps_old_file_and_leaves_no_temp(bookmarks, monkeypatch):
    client, target = bookmarks
    _seed_bookmark(client)
    before = target.read_bytes()
    _fail_replace(monkeypatch, target)
    with pytest.raises(OSError, match="simulated rename failure"):
        client.post("/api/bookmarks", json={**BOOKMARK, "message_uuid": "msg-2"})
    assert target.read_bytes() == before
    assert _leftovers(target) == []


# --- preferences -----------------------------------------------------------


@pytest.mark.parametrize("kind", ["put", "patch"])
def test_preferences_write_fsyncs_temp_file_before_replace(prefs, fs_spy, kind):
    client, target = prefs
    _prefs_write(client, kind)
    _assert_fsync_before_replace(fs_spy, target)


def test_preferences_writes_use_unique_temp_files_in_target_folder(prefs, fs_spy):
    client, target = prefs
    _prefs_write(client, "patch", "dark")
    _prefs_write(client, "patch", "light")
    _assert_unique_temps_in_target_folder(fs_spy, target)


def test_preferences_write_retries_windows_sharing_violation(prefs, monkeypatch):
    client, target = prefs
    calls = _sharing_violation_once(monkeypatch, target)
    _prefs_write(client, "patch")
    assert calls["n"] == 2, f"expected one retry, got {calls['n']} replace calls"
    assert client.get("/api/preferences").json()["data"] == {"theme": "dark"}


def test_preferences_failed_replace_keeps_old_file_and_leaves_no_temp(prefs, monkeypatch):
    client, target = prefs
    _prefs_write(client, "put", "dark")
    before = target.read_bytes()
    _fail_replace(monkeypatch, target)
    with pytest.raises(OSError, match="simulated rename failure"):
        _prefs_write(client, "patch", "light")
    assert target.read_bytes() == before
    assert _leftovers(target) == []


def test_preferences_file_is_hardened_for_the_current_user(prefs, monkeypatch):
    """The Windows ACL step (``harden_path_permissions``) still runs on the file."""
    client, target = prefs
    import backend.routers.preferences as prefs_router

    hardened: list[str] = []
    real_harden = prefs_router.harden_path_permissions

    def harden(path, *args, **kwargs):
        hardened.append(os.fspath(path))
        return real_harden(path, *args, **kwargs)

    monkeypatch.setattr(prefs_router, "harden_path_permissions", harden)
    _prefs_write(client, "patch")
    # Hardening the temp before the rename or the target after it both work;
    # the temp's name is unknown here, so accept any file in the target folder.
    assert any(os.path.dirname(p) == os.fspath(target.parent) for p in hardened), hardened


@pytest.mark.skipif(sys.platform == "win32", reason="Windows ignores mode bits")
def test_preferences_file_mode_is_owner_only(prefs):
    client, target = prefs
    _prefs_write(client, "patch")
    assert target.stat().st_mode & 0o777 == 0o600
