"""N7: no Explorer writer writes into a cloud-sync folder without the opt-in (D13).

Plan §4.3 ("Gates", the writer table) and §5 (the N7 privacy tests). Every
test builds a temp home with a ``Dropbox`` folder and puts ``data_dir`` in
it, so ``backend.cloud_sync.detect_cloud_sync`` finds "Dropbox". Nothing
reads a real home folder.

Contract (orchestrator, 2026-11-19):

- ``backend.write_gate.cloud_sync_block(settings)`` returns
  ``(provider, resolved target)`` or ``None``. The targets are
  ``data_dir``, its sibling ``search-index.sqlite``, and ``cc-images/``.
- The opt-in is ``allow_writes_in_cloud_sync`` in config.json, equal to
  ``os.path.realpath(data_dir)``.
- ``POST /api/write-consent/cloud-sync`` with the header
  ``X-Claude-Explorer-Action: allow-cloud-sync-writes`` and the body
  ``{"data_dir", "provider"}`` writes the flag.
- MCP ``list_sessions`` has a top-level ``notices`` list only when it is
  not empty.
- Pending images: ``~/.claude-explorer/cc-images-pending/`` in the
  ``cc-images/`` layout.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from backend.tests._platform_home import patch_home
from backend.tests.test_archive_sync_wiring import (
    FakeSource,
    _tool,
    _v2_creds,
    register_fake,
    write_corrupt_config,
)

pytestmark = pytest.mark.xfail(strict=True, reason="N7 not implemented yet")

PROVIDER = "Dropbox"
CONSENT_URL = "/api/write-consent/cloud-sync"
CONSENT_HEADERS = {"X-Claude-Explorer-Action": "allow-cloud-sync-writes"}
CC_SESSION = "7a0c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3"
DESKTOP_UUID = "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9"
NEEDLE = "zebrafishneedle"
PNG = b"\x89PNG\r\n\x1a\n cloud gate image bytes"


@dataclass
class CloudEnv:
    home: Path
    data_dir: Path
    claude_dir: Path
    image: Path
    src: FakeSource

    @property
    def config_path(self) -> Path:
        return self.home / ".claude-explorer" / "config.json"

    @property
    def pending_root(self) -> Path:
        return self.home / ".claude-explorer" / "cc-images-pending"

    @property
    def cc_images(self) -> Path:
        return self.data_dir.parent / "cc-images"

    @property
    def sha8(self) -> str:
        return hashlib.sha256(PNG).hexdigest()[:8]

    def image_name(self) -> str:
        return f"{self.image.parent.name}--{self.image.stem}.{self.sha8}.png"

    def write_config(self, data: dict[str, Any]) -> None:
        from backend import config

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(data), encoding="utf-8")
        config.get_settings.cache_clear()

    def opt_in(self) -> None:
        self.write_config(
            {"allow_writes_in_cloud_sync": os.path.realpath(self.data_dir)}
        )


def _setup_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data_dir_of: Any
) -> Iterator[CloudEnv]:
    from backend import config
    from backend import search_index as si
    from backend import summary_cache as sc
    from backend.sources.registry import reset_for_tests
    from backend.tests.builders import (
        build_cc_assistant_entry,
        build_cc_user_entry,
        build_desktop_conv,
        write_cc_jsonl,
        write_desktop_conv,
    )

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    (home / "Dropbox").mkdir()
    data_dir = data_dir_of(home)
    data_dir.mkdir(parents=True)
    claude_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    # The real layout: the index is a sibling of data_dir.
    index_path = data_dir.parent / "search-index.sqlite"
    monkeypatch.setattr(si, "default_index_path", lambda: index_path)
    monkeypatch.setattr(sc, "default_index_path", lambda: index_path)
    si.reset_search_index_for_tests()
    sc.reset_summary_cache_for_tests()
    config.get_settings.cache_clear()

    write_desktop_conv(
        data_dir,
        build_desktop_conv(
            uuid=DESKTOP_UUID, name="Desktop chat", body=f"a {NEEDLE} here"
        ),
    )
    write_cc_jsonl(
        claude_dir / "projects" / "-tmp-p" / f"{CC_SESSION}.jsonl",
        [
            build_cc_user_entry(
                uuid="u1", text=f"code {NEEDLE}", session_id=CC_SESSION
            ),
            build_cc_assistant_entry(
                uuid="a1",
                msg_id="m1",
                text="ok",
                session_id=CC_SESSION,
                parent_uuid="u1",
            ),
        ],
    )
    image = claude_dir / "image-cache" / CC_SESSION / "1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG)

    src = FakeSource(
        source_home=tmp_path / "fake_source_home",
        archive_dir=data_dir / "fake" / "archive",
        root=tmp_path / "fake_watch_root",
    )
    src.source_home.mkdir()
    register_fake(src)
    try:
        yield CloudEnv(
            home=home, data_dir=data_dir, claude_dir=claude_dir, image=image, src=src
        )
    finally:
        reset_for_tests()
        si.reset_search_index_for_tests()
        sc.reset_summary_cache_for_tests()
        config.get_settings.cache_clear()


@pytest.fixture
def cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watcher_state: None
) -> Iterator[CloudEnv]:
    """``data_dir`` is ``~/Dropbox/explorer/conversations``."""
    yield from _setup_env(
        tmp_path,
        monkeypatch,
        lambda home: home / "Dropbox" / "explorer" / "conversations",
    )


@pytest.fixture
def local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watcher_state: None
) -> Iterator[CloudEnv]:
    """``data_dir`` is ``~/.claude-explorer/conversations``: not cloud-synced."""
    yield from _setup_env(
        tmp_path, monkeypatch, lambda home: home / ".claude-explorer" / "conversations"
    )


@pytest.fixture
def watcher_state() -> Iterator[None]:
    from backend import cc_watcher

    cc_watcher.reset_projects_drift_for_tests()
    cc_watcher.reset_seen_for_tests()
    try:
        yield
    finally:
        cc_watcher.reset_projects_drift_for_tests()
        cc_watcher.reset_seen_for_tests()


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from mcp_server import server

    monkeypatch.setenv("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC", "30")
    server._settings = None
    server._store = None
    server.reset_archive_sync_for_tests()
    try:
        yield server
    finally:
        server.reset_archive_sync_for_tests()
        server._settings = None
        server._store = None


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count each claude.ai fetcher and each credential capture that starts."""
    from backend.routers import fetch as fetch_router

    counter = {"fetcher": 0, "capture": 0}

    class _Refused:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            counter["fetcher"] += 1
            raise AssertionError(
                "no claude.ai fetcher may start while the gate is closed"
            )

    async def _capture(*args: Any, **kwargs: Any) -> Any:
        counter["capture"] += 1
        raise AssertionError("no capture may start while the gate is closed")

    monkeypatch.setattr(fetch_router, "ClaudeFetcher", _Refused)
    monkeypatch.setattr(fetch_router, "capture_credentials", _capture)
    monkeypatch.setattr("fetcher.bulk_fetch.ClaudeFetcher", _Refused)
    return counter


def _snapshot(root: Path) -> dict[str, str]:
    """Every file and folder under ``root``, with a hash of each file."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            out[os.path.relpath(os.path.join(dirpath, name), root) + os.sep] = "dir"
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _names_folder(text: str, env: CloudEnv) -> bool:
    return os.path.realpath(env.data_dir) in text or str(env.data_dir) in text


def _client() -> TestClient:
    from backend.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Detection and the opt-in
# ---------------------------------------------------------------------------


def test_cloud_sync_block_names_the_provider_and_the_resolved_data_dir(
    cloud: CloudEnv,
) -> None:
    from backend.config import get_settings
    from backend.write_gate import cloud_sync_block

    block = cloud_sync_block(get_settings())

    assert block is not None
    provider, target = block
    assert provider == PROVIDER
    assert os.path.realpath(target) == os.path.realpath(cloud.data_dir)


def test_cloud_sync_block_is_none_for_a_local_data_dir(local: CloudEnv) -> None:
    from backend.config import get_settings
    from backend.write_gate import cloud_sync_block

    assert cloud_sync_block(get_settings()) is None


@pytest.mark.skipif(
    sys.platform == "win32", reason="a symlink needs extra rights on Windows"
)
def test_cloud_sync_block_finds_a_cc_images_sibling_that_links_into_a_cloud_root(
    local: CloudEnv,
) -> None:
    from backend.config import get_settings
    from backend.write_gate import cloud_sync_block

    target = local.home / "Dropbox" / "images"
    target.mkdir()
    (local.data_dir.parent / "cc-images").symlink_to(target, target_is_directory=True)

    block = cloud_sync_block(get_settings())

    assert block is not None and block[0] == PROVIDER


@pytest.mark.skipif(
    sys.platform == "win32", reason="a symlink needs extra rights on Windows"
)
def test_cloud_sync_block_finds_a_search_index_sibling_that_links_into_a_cloud_root(
    local: CloudEnv,
) -> None:
    from backend.config import get_settings
    from backend.write_gate import cloud_sync_block

    target = local.home / "Dropbox" / "index.sqlite"
    target.write_bytes(b"")
    (local.data_dir.parent / "search-index.sqlite").symlink_to(target)

    block = cloud_sync_block(get_settings())

    assert block is not None and block[0] == PROVIDER


def test_settings_read_the_opt_in_flag_from_config_json(cloud: CloudEnv) -> None:
    from backend.config import Settings

    cloud.opt_in()

    assert Settings.load().allow_writes_in_cloud_sync == os.path.realpath(
        cloud.data_dir
    )


def test_the_opt_in_for_the_resolved_data_dir_opens_the_gate(cloud: CloudEnv) -> None:
    from backend.config import get_settings
    from backend.write_gate import cloud_sync_block

    cloud.opt_in()

    assert cloud_sync_block(get_settings()) is None


def test_an_opt_in_for_another_folder_does_not_open_the_gate(cloud: CloudEnv) -> None:
    """A moved data_dir closes the gate again."""
    from backend.config import get_settings
    from backend.write_gate import cloud_sync_block

    cloud.write_config(
        {
            "allow_writes_in_cloud_sync": str(
                cloud.home / "Dropbox" / "old" / "conversations"
            )
        }
    )

    block = cloud_sync_block(get_settings())

    assert block is not None and block[0] == PROVIDER


def test_a_true_value_is_not_an_opt_in(cloud: CloudEnv) -> None:
    from backend.config import get_settings
    from backend.write_gate import cloud_sync_block

    cloud.write_config({"allow_writes_in_cloud_sync": True})

    assert cloud_sync_block(get_settings()) is not None


# ---------------------------------------------------------------------------
# The archive copy
# ---------------------------------------------------------------------------


def test_run_archive_syncs_skips_every_adapter_while_the_gate_is_closed(
    cloud: CloudEnv,
) -> None:
    from backend.store import ConversationStore
    from backend.write_gate import run_archive_syncs

    reports = run_archive_syncs(ConversationStore(), "refresh", 60)

    assert cloud.src.calls == []
    assert [r.skipped_reason for r in reports] == ["cloud_sync_gate"]
    assert not (cloud.data_dir / "fake").exists()


def test_run_archive_syncs_checks_the_corrupt_config_before_the_cloud_gate(
    cloud: CloudEnv,
) -> None:
    from backend.store import ConversationStore
    from backend.write_gate import run_archive_syncs

    write_corrupt_config(cloud.home)

    reports = run_archive_syncs(ConversationStore(), "watcher", 0)

    assert [r.skipped_reason for r in reports] == ["corrupt_config"]


def test_run_archive_syncs_copies_after_the_opt_in(cloud: CloudEnv) -> None:
    from backend.store import ConversationStore
    from backend.write_gate import run_archive_syncs

    cloud.opt_in()

    run_archive_syncs(ConversationStore(), "cli", 60)

    assert [c["reason"] for c in cloud.src.calls] == ["cli"]


def test_a_revoked_opt_in_closes_the_gate_and_keeps_the_copies(cloud: CloudEnv) -> None:
    from backend.store import ConversationStore
    from backend.write_gate import run_archive_syncs

    cloud.opt_in()
    run_archive_syncs(ConversationStore(), "cli", 60)
    kept = cloud.data_dir / "fake" / "kept.jsonl"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("{}\n")
    cloud.write_config({})

    reports = run_archive_syncs(ConversationStore(), "cli", 60)

    assert [r.skipped_reason for r in reports] == ["cloud_sync_gate"]
    assert len(cloud.src.calls) == 1
    assert kept.read_text() == "{}\n"


def test_watcher_logs_a_warning_that_names_the_provider_and_copies_nothing(
    cloud: CloudEnv, caplog: pytest.LogCaptureFixture
) -> None:
    from backend import cc_watcher

    with caplog.at_level(logging.WARNING):
        cc_watcher.scan_once()

    assert cloud.src.calls == []
    assert any(
        r.levelno == logging.WARNING and PROVIDER in r.getMessage()
        for r in caplog.records
    ), caplog.text


def test_event_drift_pass_copies_only_after_the_opt_in(cloud: CloudEnv) -> None:
    from backend import cc_watcher

    cc_watcher._event_drift_pass()
    assert cloud.src.calls == []
    cloud.opt_in()

    cc_watcher._event_drift_pass()

    assert [c["reason"] for c in cloud.src.calls] == ["watcher"]


def test_mcp_list_sessions_returns_a_gate_notice_that_is_not_sync_pending(
    cloud: CloudEnv, mcp: Any
) -> None:
    result = _tool(mcp, "list_sessions")

    assert cloud.src.calls == []
    assert "sync_pending" not in result
    assert any(PROVIDER in notice for notice in result.get("notices", [])), result


def test_mcp_list_sessions_has_no_gate_notice_for_a_local_data_dir(
    local: CloudEnv, mcp: Any
) -> None:
    result = _tool(mcp, "list_sessions")

    assert "notices" not in result


# ---------------------------------------------------------------------------
# The Desktop fetch and the remote Cowork sync
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/api/fetch/start?incremental=true"),
        ("GET", "/api/fetch/refresh?incremental=true"),
        ("POST", f"/api/fetch/conversation/{DESKTOP_UUID}"),
    ],
)
def test_fetch_route_refuses_with_409_before_any_network_call(
    method: str, url: str, cloud: CloudEnv, no_network: dict[str, int]
) -> None:
    response = _client().request(method, url)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert PROVIDER in detail
    assert _names_folder(detail, cloud), detail
    assert no_network == {"fetcher": 0, "capture": 0}
    assert cloud.src.calls == []


def test_fetch_route_clears_the_busy_flag_after_a_gate_refusal(
    cloud: CloudEnv, no_network: dict[str, int]
) -> None:
    from backend.routers import fetch as fetch_router

    _client().get("/api/fetch/refresh?incremental=true")

    assert fetch_router._refresh_in_progress is False


def test_cli_fetch_refuses_and_says_how_to_allow_the_writes(
    cloud: CloudEnv, no_network: dict[str, int], tmp_path: Path
) -> None:
    from cli.main import main

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_v2_creds()))

    result = CliRunner().invoke(main, ["fetch", "--credentials", str(creds)])

    assert result.exit_code != 0
    assert PROVIDER in result.output
    assert _names_folder(result.output, cloud), result.output
    assert "allow_writes_in_cloud_sync" in result.output
    assert no_network["fetcher"] == 0
    assert cloud.src.calls == []


# ---------------------------------------------------------------------------
# The search index and the summary cache
# ---------------------------------------------------------------------------


def test_search_falls_back_to_the_linear_scan_and_writes_no_index(
    cloud: CloudEnv,
) -> None:
    response = _client().get(f"/api/search?q={NEEDLE}")

    assert response.status_code == 200, response.text
    assert NEEDLE in response.text
    assert not (cloud.data_dir.parent / "search-index.sqlite").exists()


def test_get_search_index_and_get_summary_cache_return_none_while_the_gate_is_closed(
    cloud: CloudEnv,
) -> None:
    from backend.search_index import get_search_index
    from backend.summary_cache import get_summary_cache

    assert get_search_index() is None
    assert get_summary_cache() is None
    assert not (cloud.data_dir.parent / "search-index.sqlite").exists()


def test_the_conversation_list_still_shows_every_source_and_caches_no_summary(
    cloud: CloudEnv,
) -> None:
    response = _client().get("/api/conversations")

    assert response.status_code == 200, response.text
    assert CC_SESSION in response.text
    assert DESKTOP_UUID in response.text
    assert not (cloud.data_dir.parent / "search-index.sqlite").exists()


def test_search_index_opens_after_the_opt_in(cloud: CloudEnv) -> None:
    from backend import search_index as si

    assert si.get_search_index() is None
    cloud.opt_in()
    si.reset_search_index_for_tests()

    assert si.get_search_index() is not None


def test_mcp_outline_returns_the_messages_and_writes_no_outline_cache_while_the_gate_is_closed(
    cloud: CloudEnv, mcp: Any
) -> None:
    desktop = _tool(mcp, "get_session_outline", conversation_id=DESKTOP_UUID)
    code = _tool(mcp, "get_session_outline", conversation_id=CC_SESSION)

    assert desktop["message_count"] == 1
    assert [m["sender"] for m in desktop["messages"]] == ["human"]
    assert NEEDLE in desktop["messages"][0]["summary"]
    assert code["message_count"] == 2
    assert NEEDLE in code["messages"][0]["summary"]
    assert not (cloud.data_dir.parent / "cache.db").exists()


# ---------------------------------------------------------------------------
# Preferences (data_dir.parent / "preferences.json")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_preferences_write_refuses_with_409_while_the_gate_is_closed_and_read_still_works(
    method: str, cloud: CloudEnv
) -> None:
    client = _client()

    response = client.request(
        method, "/api/preferences", json={"data": {"theme": "dark"}}
    )

    assert response.status_code == 409, response.text
    assert PROVIDER in response.json()["detail"]
    assert client.get("/api/preferences").status_code == 200
    assert not (cloud.data_dir.parent / "preferences.json").exists()


def test_preferences_write_works_after_the_opt_in(cloud: CloudEnv) -> None:
    client = _client()
    assert (
        client.patch("/api/preferences", json={"data": {"theme": "dark"}}).status_code
        == 409
    )
    cloud.opt_in()

    response = client.patch("/api/preferences", json={"data": {"theme": "dark"}})

    assert response.status_code == 200, response.text
    assert (cloud.data_dir.parent / "preferences.json").exists()


def test_preferences_write_checks_the_corrupt_config_before_the_cloud_gate(
    cloud: CloudEnv,
) -> None:
    client = _client()
    assert (
        client.put("/api/preferences", json={"data": {"theme": "dark"}}).status_code
        == 409
    )
    write_corrupt_config(cloud.home)

    response = client.put("/api/preferences", json={"data": {"theme": "dark"}})

    assert response.status_code == 503, response.text


# ---------------------------------------------------------------------------
# Claude Code image capture and the pending folder
# ---------------------------------------------------------------------------


def test_a_new_image_lands_in_the_pending_folder_while_the_gate_is_closed(
    cloud: CloudEnv,
) -> None:
    from backend import cc_watcher

    cc_watcher.scan_once()

    pending = cloud.pending_root / CC_SESSION / cloud.image_name()
    assert pending.read_bytes() == PNG
    assert not cloud.cc_images.exists()


def test_pending_images_move_into_cc_images_after_the_opt_in(cloud: CloudEnv) -> None:
    from backend import cc_watcher

    cc_watcher.scan_once()
    pending = cloud.pending_root / CC_SESSION / cloud.image_name()
    assert pending.exists()
    cloud.opt_in()

    cc_watcher.scan_once()

    assert (cloud.cc_images / CC_SESSION / cloud.image_name()).read_bytes() == PNG
    assert not pending.exists(), (
        "the pending copy is deleted only after the archive copy is safe"
    )


def test_pending_images_stay_while_the_gate_is_closed(cloud: CloudEnv) -> None:
    from backend import cc_watcher

    cc_watcher.scan_once()
    cc_watcher.reset_seen_for_tests()
    cc_watcher.scan_once()

    assert (cloud.pending_root / CC_SESSION / cloud.image_name()).read_bytes() == PNG


def test_the_image_route_serves_a_pending_image_after_claude_code_deletes_it(
    cloud: CloudEnv,
) -> None:
    from backend import cc_watcher

    cc_watcher.scan_once()
    cloud.image.unlink()
    assert not cloud.cc_images.exists()

    response = _client().get("/api/cc-image", params={"path": str(cloud.image)})

    assert response.status_code == 200, response.text
    assert response.content == PNG


def test_the_image_route_copies_a_live_image_to_the_pending_folder(
    cloud: CloudEnv,
) -> None:
    response = _client().get("/api/cc-image", params={"path": str(cloud.image)})

    assert response.status_code == 200
    assert (cloud.pending_root / CC_SESSION / cloud.image_name()).read_bytes() == PNG
    assert not cloud.cc_images.exists()


def _dropbox_info(home: Path, root: Path) -> None:
    info = home / ".dropbox" / "info.json"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps({"personal": {"path": str(root)}}), encoding="utf-8")


@pytest.mark.skipif(
    sys.platform == "win32", reason="the Windows cache folder is not under a temp home"
)
def test_a_cloud_synced_pending_folder_moves_to_the_platform_cache_folder(
    local: CloudEnv,
) -> None:
    import platformdirs

    from backend import cc_watcher

    _dropbox_info(local.home, local.home / ".claude-explorer")

    cc_watcher.scan_once()

    cache_pending = (
        Path(platformdirs.user_cache_dir("claude-explorer")) / "cc-images-pending"
    )
    assert (cache_pending / CC_SESSION / local.image_name()).read_bytes() == PNG
    assert not local.pending_root.exists()


@pytest.mark.skipif(
    sys.platform == "win32", reason="the Windows cache folder is not under a temp home"
)
def test_an_image_is_still_captured_and_an_error_logged_when_every_pending_folder_is_cloud_synced(
    local: CloudEnv, caplog: pytest.LogCaptureFixture
) -> None:
    import platformdirs

    from backend import cc_watcher

    _dropbox_info(local.home, local.home)

    with caplog.at_level(logging.ERROR):
        cc_watcher.scan_once()

    cache_pending = (
        Path(platformdirs.user_cache_dir("claude-explorer")) / "cc-images-pending"
    )
    assert (cache_pending / CC_SESSION / local.image_name()).read_bytes() == PNG
    assert any(r.levelno >= logging.ERROR for r in caplog.records), caplog.text


# ---------------------------------------------------------------------------
# §5: no writer adds or changes a file
# ---------------------------------------------------------------------------


def test_no_writer_adds_or_changes_a_file_under_the_cloud_folder(
    cloud: CloudEnv, no_network: dict[str, int], mcp: Any, tmp_path: Path
) -> None:
    from backend import cc_watcher
    from cli.main import main

    before = _snapshot(cloud.data_dir.parent)
    client = _client()

    cc_watcher.scan_once()
    cc_watcher._event_drift_pass()
    client.get("/api/conversations")
    client.get(f"/api/conversations/{CC_SESSION}")
    client.get(f"/api/search?q={NEEDLE}")
    client.get("/api/cc-image", params={"path": str(cloud.image)})
    client.get("/api/fetch/start?incremental=true")
    client.get("/api/fetch/refresh?incremental=true")
    client.post(f"/api/fetch/conversation/{DESKTOP_UUID}")
    CliRunner().invoke(main, ["fetch", "--credentials", str(tmp_path / "missing.json")])
    _tool(mcp, "list_sessions")
    _tool(mcp, "list_sessions", query=NEEDLE)
    _tool(mcp, "get_session_outline", conversation_id=DESKTOP_UUID)
    _tool(mcp, "get_session_outline", conversation_id=CC_SESSION)
    client.put("/api/preferences", json={"data": {"theme": "dark"}})
    client.patch("/api/preferences", json={"data": {"theme": "dark"}})

    assert _snapshot(cloud.data_dir.parent) == before
    assert cloud.src.calls == []


# ---------------------------------------------------------------------------
# The consent route
# ---------------------------------------------------------------------------


def _consent_body(env: CloudEnv, provider: str = PROVIDER) -> dict[str, str]:
    return {"data_dir": os.path.realpath(env.data_dir), "provider": provider}


def test_consent_route_writes_the_resolved_data_dir_and_keeps_other_keys(
    cloud: CloudEnv,
) -> None:
    from backend.config import get_settings
    from backend.write_gate import cloud_sync_block

    cloud.write_config({"claude_dir": str(cloud.claude_dir)})

    response = _client().post(
        CONSENT_URL, json=_consent_body(cloud), headers=CONSENT_HEADERS
    )

    assert response.status_code == 200, response.text
    saved = json.loads(cloud.config_path.read_text(encoding="utf-8"))
    assert saved == {
        "claude_dir": str(cloud.claude_dir),
        "allow_writes_in_cloud_sync": os.path.realpath(cloud.data_dir),
    }
    get_settings.cache_clear()
    assert cloud_sync_block(get_settings()) is None


def test_consent_route_creates_the_config_file_when_there_is_none(
    cloud: CloudEnv,
) -> None:
    response = _client().post(
        CONSENT_URL, json=_consent_body(cloud), headers=CONSENT_HEADERS
    )

    assert response.status_code == 200, response.text
    saved = json.loads(cloud.config_path.read_text(encoding="utf-8"))
    assert saved == {"allow_writes_in_cloud_sync": os.path.realpath(cloud.data_dir)}


def test_consent_route_refuses_a_request_without_the_action_header(
    cloud: CloudEnv,
) -> None:
    response = _client().post(CONSENT_URL, json=_consent_body(cloud))

    assert response.status_code == 403
    assert not cloud.config_path.exists()


def test_consent_route_refuses_a_foreign_origin(cloud: CloudEnv) -> None:
    response = _client().post(
        CONSENT_URL,
        json=_consent_body(cloud),
        headers={**CONSENT_HEADERS, "Origin": "https://evil.example.com"},
    )

    assert response.status_code == 403
    assert not cloud.config_path.exists()


def test_consent_route_accepts_a_loopback_origin(cloud: CloudEnv) -> None:
    response = _client().post(
        CONSENT_URL,
        json=_consent_body(cloud),
        headers={**CONSENT_HEADERS, "Origin": "http://localhost:5173"},
    )

    assert response.status_code == 200, response.text


def test_consent_route_refuses_while_the_config_is_corrupt(cloud: CloudEnv) -> None:
    path = write_corrupt_config(cloud.home)
    before = path.read_bytes()

    response = _client().post(
        CONSENT_URL, json=_consent_body(cloud), headers=CONSENT_HEADERS
    )

    assert response.status_code == 503
    assert path.read_bytes() == before


def test_consent_route_refuses_a_folder_that_is_not_cloud_synced(
    local: CloudEnv,
) -> None:
    response = _client().post(
        CONSENT_URL, json=_consent_body(local), headers=CONSENT_HEADERS
    )

    assert response.status_code == 400
    assert not local.config_path.exists()


def test_consent_route_refuses_a_provider_that_does_not_match_the_detection(
    cloud: CloudEnv,
) -> None:
    response = _client().post(
        CONSENT_URL, json=_consent_body(cloud, "OneDrive"), headers=CONSENT_HEADERS
    )

    assert response.status_code == 409
    assert not cloud.config_path.exists()


def test_consent_route_refuses_a_data_dir_that_does_not_match_the_current_one(
    cloud: CloudEnv,
) -> None:
    body = {"data_dir": str(cloud.home / "Dropbox" / "other"), "provider": PROVIDER}

    response = _client().post(CONSENT_URL, json=body, headers=CONSENT_HEADERS)

    assert response.status_code == 409
    assert not cloud.config_path.exists()


def test_consent_route_accepts_only_post(cloud: CloudEnv) -> None:
    response = _client().get(CONSENT_URL, headers=CONSENT_HEADERS)

    assert response.status_code == 405
