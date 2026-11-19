"""N7: every caller runs the archive copy with its lock policy (plan §4.3).

The callers are the watcher (event pass and backstop scan), Refresh and
``/fetch/start``, ``claude-explorer fetch``, and each MCP tool call. A fake
source adapter, registered through ``backend.sources.registry``, records
each ``archive_sync(ctx)`` call. Its pass runs under the real
``ArchiveLock``, so "busy" and "never collide" are real lock results.

Contract (orchestrator, 2026-11-19):

- ``backend.write_gate.run_archive_syncs(store, reason, lock_timeout_s)``
  checks the gates (corrupt config, then cloud sync) and then calls
  ``adapter.archive_sync(ctx)`` for each adapter, in registry order.
- The watcher calls it with ``"watcher"`` or ``"backstop"`` and 0 s, and
  calls ``cc_watcher._schedule_drift(path)`` when the lock is busy.
- MCP ``list_sessions`` and ``get_session_outline`` have the key
  ``sync_pending: true`` only while the lock is busy; else the key is absent.
- MCP debounces with ``CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC``. A busy
  try does not start the interval.
  ``mcp_server.server.reset_archive_sync_for_tests()`` resets the state.

The cloud-sync gate has its own file: ``test_cloud_sync_gate.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from backend.tests._platform_home import patch_home

pytestmark = pytest.mark.xfail(strict=True, reason="N7 not implemented yet")

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_ID = "FAKE_SYNC"
_ORG_ID = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"


# ---------------------------------------------------------------------------
# The fake source
# ---------------------------------------------------------------------------


@dataclass
class FakeSource:
    """The state of one fake adapter. Each adapter instance shares it."""

    source_home: Path
    archive_dir: Path
    root: Path
    root_create: bool = False
    patterns: tuple[str, ...] = (
        "*.jsonl",
        "*.jsonl.zst",
        "session_index.jsonl",
        "state_5.sqlite*",
    )
    calls: list[dict[str, Any]] = field(default_factory=list)
    hold_s: float = 0.0
    forced: Any = None
    entered: threading.Event = field(default_factory=threading.Event)
    log: list[str] | None = None
    #: A second ``create=False`` root, so a test can show that the watcher
    #: reads this adapter's roots at all.
    extra_root: Path | None = None

    def archive_lock(self) -> Any:
        from backend.archive_sync import LOCK_FILE_NAME, ArchiveLock

        return ArchiveLock(self.archive_dir / LOCK_FILE_NAME)


def fake_descriptor(source_id: str = FAKE_ID) -> Any:
    from backend.sources.base import SourceDescriptor

    return SourceDescriptor(
        id=source_id,  # type: ignore[arg-type]
        label="Fake",
        short_label="Fake",
        chronological=True,
        has_projects=False,
        has_archive_state=False,
        has_organizations=False,
        refetchable=False,
        messages_change_in_place=False,
    )


class FakeAdapter:
    def __init__(self, store: Any, src: FakeSource, source_id: str = FAKE_ID) -> None:
        self._store = store
        self._src = src
        self._id = source_id

    def descriptor(self) -> Any:
        return fake_descriptor(self._id)

    def watch_roots(self) -> list[Any]:
        from backend.sources.base import WatchRoot

        src = self._src
        roots = [WatchRoot(src.root, src.patterns, create=src.root_create)]
        if src.extra_root is not None:
            roots.append(WatchRoot(src.extra_root, src.patterns, create=False))
        return roots

    def discover(self) -> list[Path]:
        return []

    def list_rows(
        self, *, full_content: bool, include_phantom: bool
    ) -> list[dict[str, Any]]:
        return []

    def find(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        return None

    def load_for_index(self, path: Path) -> dict[str, Any] | None:
        return None

    def branch_flag(
        self, data: dict[str, Any], chat_messages: list[dict[str, Any]]
    ) -> bool:
        return False

    def archive_sync(self, ctx: Any) -> Any:
        from backend.archive_sync import run_locked_pass

        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        src = self._src
        call: dict[str, Any] = {
            "source": self._id,
            "reason": ctx.reason,
            "lock_timeout_s": ctx.lock_timeout_s,
            "settings": ctx.settings,
            "on_event_loop": on_loop,
            "thread": threading.current_thread(),
        }
        src.calls.append(call)
        if src.log is not None:
            src.log.append(f"archive_sync:{self._id}")
        if src.forced is not None:
            return src.forced

        def body(report: Any) -> None:
            call["start"] = time.monotonic()
            src.entered.set()
            time.sleep(src.hold_s)
            call["end"] = time.monotonic()

        report = run_locked_pass(
            ctx, source_home=src.source_home, archive_dir=src.archive_dir, body=body
        )
        call["skipped_reason"] = report.skipped_reason
        return report


def register_fake(src: FakeSource, source_id: str = FAKE_ID) -> None:
    from backend.sources.registry import register_adapter

    register_adapter(
        fake_descriptor(source_id), lambda store: FakeAdapter(store, src, source_id)
    )


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``Path.home()`` is a temp folder, so config.json is a test file."""
    from backend import config

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    config.get_settings.cache_clear()
    return home


@pytest.fixture
def fake_source(tmp_path: Path, fake_home: Path) -> Iterator[FakeSource]:
    from backend.sources.registry import reset_for_tests

    src = FakeSource(
        source_home=tmp_path / "fake_source_home",
        archive_dir=tmp_path / "fake_archive",
        root=tmp_path / "fake_watch_root",
    )
    src.source_home.mkdir()
    register_fake(src)
    try:
        yield src
    finally:
        reset_for_tests()


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
def scheduled(monkeypatch: pytest.MonkeyPatch, watcher_state: None) -> list[Path]:
    """Record each ``cc_watcher._schedule_drift(path)`` call; start no timer."""
    from backend import cc_watcher

    calls: list[Path] = []
    monkeypatch.setattr(
        cc_watcher, "_schedule_drift", lambda path: calls.append(Path(path))
    )
    return calls


def write_corrupt_config(home: Path) -> Path:
    path = home / ".claude-explorer" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    return path


def _v2_creds() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "session_key": "sk-ant-sid01-fake-test-key",
        "cf_bm": None,
        "cf_clearance": None,
        "captured_at": "2026-05-01T00:00:00+00:00",
        "orgs": [
            {
                "uuid": _ORG_ID,
                "name": None,
                "capabilities": [],
                "seen_in_response": False,
            }
        ],
        "primary_org_id": _ORG_ID,
        "legacy_migration_target": _ORG_ID,
        "org_id": _ORG_ID,
    }


@pytest.fixture
def fetch_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Valid credentials and a claude.ai list that is empty.

    Returns a counter: ``list`` counts the claude.ai list calls, so a test
    can prove that the fetch continued after the copy step.
    """
    from backend.routers import fetch as fetch_router

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_v2_creds()))
    out = tmp_path / "out" / "conversations"
    out.mkdir(parents=True)
    monkeypatch.setattr(fetch_router, "DEFAULT_CREDENTIALS_PATH", creds)
    monkeypatch.setattr(fetch_router, "fetch_output_dir", lambda: out)
    monkeypatch.setattr(
        fetch_router, "fetch_files_dir", lambda: tmp_path / "out" / "files"
    )
    counter = {"list": 0}

    def _list(self: Any) -> list[dict[str, Any]]:
        counter["list"] += 1
        return []

    monkeypatch.setattr(fetch_router.ClaudeFetcher, "fetch_conversation_list", _list)
    monkeypatch.setattr(
        fetch_router.ClaudeFetcher, "save_index", lambda self, *a, **k: None
    )
    return counter


def _sse(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def _report(**kwargs: Any) -> Any:
    from backend.archive_sync import SyncReport

    return SyncReport(**kwargs)


def _store() -> Any:
    from backend.store import ConversationStore

    return ConversationStore()


# ---------------------------------------------------------------------------
# backend.write_gate.run_archive_syncs
# ---------------------------------------------------------------------------


def test_run_archive_syncs_gives_each_adapter_the_reason_timeout_and_settings(
    fake_source: FakeSource,
) -> None:
    from backend.config import Settings
    from backend.write_gate import run_archive_syncs

    reports = run_archive_syncs(_store(), "cli", 60)

    assert len(fake_source.calls) == 1, fake_source.calls
    call = fake_source.calls[0]
    assert call["reason"] == "cli"
    assert call["lock_timeout_s"] == 60
    assert isinstance(call["settings"], Settings)
    assert any(r is not None and r.skipped_reason is None for r in reports), reports


def test_run_archive_syncs_calls_the_adapters_in_registry_order(
    fake_source: FakeSource, tmp_path: Path
) -> None:
    from backend.write_gate import run_archive_syncs

    order: list[str] = []
    fake_source.log = order
    second = FakeSource(
        source_home=fake_source.source_home,
        archive_dir=tmp_path / "second_archive",
        root=tmp_path / "second_root",
        log=order,
    )
    register_fake(second, "FAKE_SYNC_B")

    run_archive_syncs(_store(), "refresh", 60)

    assert order == [f"archive_sync:{FAKE_ID}", "archive_sync:FAKE_SYNC_B"]


def test_run_archive_syncs_skips_every_adapter_when_the_config_is_corrupt(
    fake_source: FakeSource, fake_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from backend.write_gate import run_archive_syncs

    write_corrupt_config(fake_home)
    with caplog.at_level(logging.WARNING):
        reports = run_archive_syncs(_store(), "watcher", 0)

    assert fake_source.calls == []
    assert [r.skipped_reason for r in reports] == ["corrupt_config"]
    assert any(r.levelno == logging.WARNING for r in caplog.records), caplog.text


def test_run_archive_syncs_reads_the_config_again_on_each_pass(
    fake_source: FakeSource, fake_home: Path
) -> None:
    """A config that breaks after the settings were cached still closes the gate."""
    from backend.config import get_settings
    from backend.write_gate import run_archive_syncs

    assert get_settings().config_corrupt_reason is None  # cached as valid
    write_corrupt_config(fake_home)

    reports = run_archive_syncs(_store(), "mcp", 0)

    assert fake_source.calls == []
    assert [r.skipped_reason for r in reports] == ["corrupt_config"]


def test_write_gate_imports_no_watcher_deps_or_router_module() -> None:
    """The MCP closure forbids these modules, and MCP imports write_gate."""
    code = (
        "import sys, json\n"
        "import backend.write_gate\n"
        "bad = sorted(m for m in sys.modules if m in ('backend.cc_watcher', 'backend.deps')"
        " or m == 'backend.routers' or m.startswith('backend.routers.'))\n"
        "print(json.dumps(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


# ---------------------------------------------------------------------------
# The watcher: event pass and backstop scan
# ---------------------------------------------------------------------------


def test_event_drift_pass_copies_with_reason_watcher_and_no_wait(
    fake_source: FakeSource, scheduled: list[Path]
) -> None:
    from backend import cc_watcher

    cc_watcher._event_drift_pass()

    assert [(c["reason"], c["lock_timeout_s"]) for c in fake_source.calls] == [
        ("watcher", 0)
    ]
    assert fake_source.calls[0]["skipped_reason"] is None


def test_backstop_scan_copies_with_reason_backstop_and_no_wait(
    fake_source: FakeSource, scheduled: list[Path]
) -> None:
    from backend import cc_watcher

    cc_watcher.scan_once()

    assert [(c["reason"], c["lock_timeout_s"]) for c in fake_source.calls] == [
        ("backstop", 0)
    ]


def test_event_drift_pass_copies_before_the_search_drift(
    fake_source: FakeSource, scheduled: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy runs at the top of the pass, so the drift sees the new files."""
    from backend import cc_watcher
    from backend import search_index as si

    order: list[str] = []
    fake_source.log = order
    monkeypatch.setattr(
        si, "update_drifted_files", lambda *a, **k: order.append("drift") or 0
    )

    cc_watcher._event_drift_pass()

    assert order == [f"archive_sync:{FAKE_ID}", "drift"]


def test_event_drift_pass_reschedules_when_the_lock_is_busy(
    fake_source: FakeSource, scheduled: list[Path]
) -> None:
    from backend import cc_watcher

    lock = fake_source.archive_lock()
    assert lock.acquire(0)
    try:
        cc_watcher._event_drift_pass()
    finally:
        lock.release()

    assert [c["skipped_reason"] for c in fake_source.calls] == ["busy"]
    assert len(scheduled) >= 1, (
        "a busy lock must schedule another try after the debounce"
    )


def test_backstop_scan_reschedules_when_the_lock_is_busy(
    fake_source: FakeSource, scheduled: list[Path]
) -> None:
    from backend import cc_watcher

    lock = fake_source.archive_lock()
    assert lock.acquire(0)
    try:
        cc_watcher.scan_once()
    finally:
        lock.release()

    assert [c["skipped_reason"] for c in fake_source.calls] == ["busy"]
    assert len(scheduled) >= 1


def test_watcher_does_not_reschedule_after_a_finished_copy(
    fake_source: FakeSource, scheduled: list[Path]
) -> None:
    from backend import cc_watcher

    cc_watcher._event_drift_pass()

    assert [c["skipped_reason"] for c in fake_source.calls] == [None]
    assert scheduled == []


def test_corrupt_config_skips_the_watcher_copy_but_the_drift_still_runs(
    fake_source: FakeSource,
    fake_home: Path,
    scheduled: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D9: the gate skips only the copy."""
    from backend import cc_watcher
    from backend import search_index as si

    drift_calls: list[Any] = []
    monkeypatch.setattr(
        si, "update_drifted_files", lambda *a, **k: drift_calls.append(a) or 0
    )
    cc_watcher._event_drift_pass()
    assert len(fake_source.calls) == 1  # the copy runs while the config is valid
    write_corrupt_config(fake_home)

    with caplog.at_level(logging.WARNING):
        cc_watcher._event_drift_pass()

    assert len(fake_source.calls) == 1
    assert len(drift_calls) == 2
    assert any(r.levelno == logging.WARNING for r in caplog.records), caplog.text


def test_corrupt_config_skips_the_backstop_copy_but_images_are_still_captured(
    fake_source: FakeSource, fake_home: Path, scheduled: list[Path]
) -> None:
    from backend import cc_watcher
    from backend.cc_image_cache import cache_dir
    from backend.config import get_settings

    cc_watcher.scan_once()
    assert len(fake_source.calls) == 1  # the copy runs while the config is valid
    image = get_settings().claude_dir / "image-cache" / "sess-corrupt" / "1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG corrupt-config image")
    write_corrupt_config(fake_home)

    cc_watcher.scan_once()

    assert len(fake_source.calls) == 1
    copied = list((cache_dir() / "sess-corrupt").glob("sess-corrupt--1.*.png"))
    assert len(copied) == 1, "image capture must run while the copy is gated"


# ---------------------------------------------------------------------------
# The watcher: watch roots
# ---------------------------------------------------------------------------


class _FakeObserver:
    """Stands in for ``watchdog.observers.Observer``; records each watch."""

    instances: ClassVar[list[_FakeObserver]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.daemon = False
        self.watches: list[tuple[Any, Path]] = []
        _FakeObserver.instances.append(self)

    def schedule(
        self, handler: Any, path: Any, recursive: bool = False, **kwargs: Any
    ) -> object:
        self.watches.append((handler, Path(path)))
        return object()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def unschedule_all(self) -> None:
        pass


@pytest.fixture
def fake_observers(
    monkeypatch: pytest.MonkeyPatch, watcher_state: None
) -> type[_FakeObserver]:
    import watchdog.observers

    _FakeObserver.instances = []
    monkeypatch.setattr(watchdog.observers, "Observer", _FakeObserver)
    return _FakeObserver


def _handlers_for(root: Path) -> list[Any]:
    real = os.path.realpath(root)
    return [
        handler
        for obs in _FakeObserver.instances
        for handler, path in obs.watches
        if os.path.realpath(path) == real
    ]


async def _run_watcher_for(seconds: float) -> None:
    from backend import cc_watcher

    stop = asyncio.Event()
    task = asyncio.create_task(cc_watcher.run_watcher(stop))
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(task, timeout=15)


def _dispatch_modified(handler: Any, path: Path) -> None:
    from watchdog.events import FileModifiedEvent

    handler.dispatch(FileModifiedEvent(str(path)))


async def test_watcher_never_creates_a_missing_root_with_create_false(
    fake_source: FakeSource,
    fake_observers: type[_FakeObserver],
    scheduled: list[Path],
    tmp_path: Path,
) -> None:
    fake_source.extra_root = tmp_path / "fake_extra_root"
    fake_source.extra_root.mkdir()

    await _run_watcher_for(0.3)

    assert len(_handlers_for(fake_source.extra_root)) == 1, (
        "the watcher must read the adapter's roots"
    )
    assert not fake_source.root.exists()
    assert _handlers_for(fake_source.root) == []


async def test_watcher_watches_an_existing_root_with_create_false(
    fake_source: FakeSource, fake_observers: type[_FakeObserver], scheduled: list[Path]
) -> None:
    fake_source.root.mkdir()

    await _run_watcher_for(0.3)

    assert len(_handlers_for(fake_source.root)) == 1


async def test_watcher_schedules_drift_for_each_file_that_matches_a_root_pattern(
    fake_source: FakeSource, fake_observers: type[_FakeObserver], scheduled: list[Path]
) -> None:
    fake_source.root.mkdir()
    await _run_watcher_for(0.3)
    (handler,) = _handlers_for(fake_source.root)
    names = [
        "sessions/2026/11/19/rollout-a.jsonl",
        "sessions/2026/11/19/rollout-b.jsonl.zst",
        "session_index.jsonl",
        "state_5.sqlite-wal",
    ]

    for name in names:
        _dispatch_modified(handler, fake_source.root / name)

    assert [p.name for p in scheduled] == [Path(n).name for n in names]


async def test_watcher_ignores_a_file_that_matches_no_root_pattern(
    fake_source: FakeSource, fake_observers: type[_FakeObserver], scheduled: list[Path]
) -> None:
    fake_source.root.mkdir()
    await _run_watcher_for(0.3)
    (handler,) = _handlers_for(fake_source.root)

    _dispatch_modified(handler, fake_source.root / "history.jsonl.bak")
    _dispatch_modified(handler, fake_source.root / "config.toml")

    assert scheduled == []


async def test_backstop_starts_watching_a_root_that_appears_later(
    fake_source: FakeSource,
    fake_observers: type[_FakeObserver],
    scheduled: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend import cc_watcher

    monkeypatch.setattr(cc_watcher, "SCAN_INTERVAL_SEC", 0.1)
    stop = asyncio.Event()
    task = asyncio.create_task(cc_watcher.run_watcher(stop))
    try:
        await asyncio.sleep(0.3)
        assert _handlers_for(fake_source.root) == []
        fake_source.root.mkdir()
        deadline = time.monotonic() + 5
        while not _handlers_for(fake_source.root) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=15)

    assert len(_handlers_for(fake_source.root)) == 1, (
        "the backstop must check a missing root again"
    )


def test_cowork_watch_root_patterns_include_the_session_sidecar() -> None:
    from backend.sources.registry import get_adapter

    (root,) = get_adapter("CLAUDE_COWORK", _store()).watch_roots()

    assert "audit.jsonl" in root.patterns
    assert "local_*.json" in root.patterns


async def test_cowork_sidecar_change_schedules_a_drift_pass(
    fake_observers: type[_FakeObserver], scheduled: list[Path], fake_home: Path
) -> None:
    """L1 gap: a sidecar-only change waited for the backstop scan."""
    from backend.config import get_settings

    root = get_settings().claude_desktop_app_dir / "local-agent-mode-sessions"
    session = root / "dep-1" / "org-1" / "local_0b0e8a8e-0000-4000-8000-000000000001"
    session.mkdir(parents=True)
    await _run_watcher_for(0.3)
    handlers = _handlers_for(root)
    assert len(handlers) == 1
    sidecar = session.parent / f"{session.name}.json"

    _dispatch_modified(handlers[0], sidecar)
    _dispatch_modified(handlers[0], session / "audit.jsonl")
    _dispatch_modified(handlers[0], session / "outputs" / "report.txt")

    assert scheduled == [sidecar, session / "audit.jsonl"]


# ---------------------------------------------------------------------------
# Refresh and /fetch/start
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/api/fetch/start", "/api/fetch/refresh"])
def test_fetch_route_copies_with_reason_refresh_and_a_60s_wait_off_the_event_loop(
    route: str, fake_source: FakeSource, fetch_ok: dict[str, int]
) -> None:
    from backend.main import app

    response = TestClient(app).get(f"{route}?incremental=true")

    assert response.status_code == 200, response.text
    assert [(c["reason"], c["lock_timeout_s"]) for c in fake_source.calls] == [
        ("refresh", 60)
    ]
    assert fake_source.calls[0]["on_event_loop"] is False, (
        "the 60 s wait must run in a worker thread"
    )
    assert "complete" in [e["type"] for e in _sse(response.text)]


@pytest.mark.parametrize("route", ["/api/fetch/start", "/api/fetch/refresh"])
def test_fetch_route_reports_a_timed_out_copy_as_a_codex_progress_frame_and_continues(
    route: str, fake_source: FakeSource, fetch_ok: dict[str, int]
) -> None:
    from backend.main import app

    fake_source.forced = _report(skipped_reason="timeout")

    response = TestClient(app).get(f"{route}?incremental=true")

    assert response.status_code == 200, response.text
    events = _sse(response.text)
    codex = [e for e in events if e.get("phase") == "codex"]
    assert len(codex) == 1, events
    assert codex[0]["type"] == "progress"
    assert codex[0]["message"].strip()
    assert fetch_ok["list"] == 1, (
        "the claude.ai fetch must continue after a skipped copy"
    )
    assert events[-1]["type"] == "complete", events


@pytest.mark.parametrize("route", ["/api/fetch/start", "/api/fetch/refresh"])
def test_fetch_route_sends_no_codex_frame_after_a_finished_copy(
    route: str, fake_source: FakeSource, fetch_ok: dict[str, int]
) -> None:
    from backend.main import app

    response = TestClient(app).get(f"{route}?incremental=true")

    assert [c["skipped_reason"] for c in fake_source.calls] == [None]
    assert [e for e in _sse(response.text) if e.get("phase") == "codex"] == []


def test_fetch_progress_model_accepts_the_codex_phase() -> None:
    from backend.models import FetchProgress

    frame = FetchProgress(type="progress", message="Codex copy is busy", phase="codex")

    assert frame.phase == "codex"


def test_watcher_and_refresh_at_the_same_time_never_overlap(
    fake_source: FakeSource, fetch_ok: dict[str, int], scheduled: list[Path]
) -> None:
    """D5: the watcher holds the lock; the Refresh waits, then copies after it."""
    from backend import cc_watcher
    from backend.main import app

    fake_source.hold_s = 0.6
    watcher = threading.Thread(target=cc_watcher._event_drift_pass)
    watcher.start()
    assert fake_source.entered.wait(10), "the watcher pass did not start"
    try:
        response = TestClient(app).get("/api/fetch/refresh?incremental=true")
    finally:
        watcher.join(15)

    by_reason = {c["reason"]: c for c in fake_source.calls}
    assert set(by_reason) == {"watcher", "refresh"}, fake_source.calls
    assert by_reason["watcher"]["skipped_reason"] is None
    assert by_reason["refresh"]["skipped_reason"] is None
    assert by_reason["refresh"]["start"] >= by_reason["watcher"]["end"], (
        "the two copies overlapped"
    )
    assert [e for e in _sse(response.text) if e.get("phase") == "codex"] == []
    assert _sse(response.text)[-1]["type"] == "complete"


def test_watcher_during_a_refresh_copy_finds_the_lock_busy(
    fake_source: FakeSource, fetch_ok: dict[str, int], scheduled: list[Path]
) -> None:
    from backend import cc_watcher
    from backend.main import app

    fake_source.hold_s = 0.6
    result: dict[str, Any] = {}
    refresh = threading.Thread(
        target=lambda: result.setdefault(
            "r", TestClient(app).get("/api/fetch/refresh?incremental=true")
        )
    )
    refresh.start()
    try:
        assert fake_source.entered.wait(10), "the Refresh copy did not start"
        cc_watcher._event_drift_pass()
    finally:
        refresh.join(30)

    by_reason = {c["reason"]: c for c in fake_source.calls}
    assert by_reason["watcher"]["skipped_reason"] == "busy"
    assert by_reason["refresh"]["skipped_reason"] is None
    assert len(scheduled) >= 1
    assert _sse(result["r"].text)[-1]["type"] == "complete"


# ---------------------------------------------------------------------------
# claude-explorer fetch
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_fetcher(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Stand in for ``ClaudeFetcher``; count each construction and run."""
    counter = {"init": 0, "run": 0}

    class _Stub:
        def __init__(self, **kwargs: Any) -> None:
            counter["init"] += 1

        def run(self, limit: int | None = None) -> None:
            counter["run"] += 1

    monkeypatch.setattr("fetcher.bulk_fetch.ClaudeFetcher", _Stub)
    return counter


def test_cli_fetch_copies_before_it_loads_the_credentials(
    fake_source: FakeSource, cli_fetcher: dict[str, int], tmp_path: Path
) -> None:
    """A user without credentials can still copy."""
    from cli.main import main

    result = CliRunner().invoke(
        main, ["fetch", "--credentials", str(tmp_path / "missing.json")]
    )

    assert [(c["reason"], c["lock_timeout_s"]) for c in fake_source.calls] == [
        ("cli", 60)
    ]
    assert fake_source.calls[0]["skipped_reason"] is None
    assert result.exit_code != 0, "the claude.ai fetch still needs credentials"
    assert "capture" in result.output
    assert cli_fetcher["init"] == 0


def test_cli_fetch_prints_a_timed_out_copy_and_continues(
    fake_source: FakeSource, cli_fetcher: dict[str, int], tmp_path: Path
) -> None:
    from cli.main import main

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_v2_creds()))
    fake_source.forced = _report(skipped_reason="timeout")
    quiet = CliRunner().invoke(main, ["fetch", "--credentials", str(creds)])
    fake_source.forced = None

    result_ok = CliRunner().invoke(main, ["fetch", "--credentials", str(creds)])

    assert quiet.exit_code == 0, quiet.output
    assert cli_fetcher["run"] == 2, "the fetch must continue after a skipped copy"
    extra = [
        line
        for line in quiet.output.splitlines()
        if line not in result_ok.output.splitlines()
    ]
    assert extra, "the CLI must print a message when the copy timed out"


# ---------------------------------------------------------------------------
# MCP tool calls
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp(fake_source: FakeSource, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
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


def _tool(server: Any, name: str, **kwargs: Any) -> Any:
    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


def _seed_desktop_conversation() -> str:
    from backend.config import get_settings
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    uuid = "5d1c2f4e-1a2b-4c3d-8e9f-0a1b2c3d4e5f"
    write_desktop_conv(
        get_settings().data_dir,
        build_desktop_conv(uuid=uuid, name="Seeded", body="hello"),
    )
    return uuid


def test_mcp_list_sessions_runs_one_inline_copy_with_no_wait(
    mcp: Any, fake_source: FakeSource
) -> None:
    result = _tool(mcp, "list_sessions")

    assert [(c["reason"], c["lock_timeout_s"]) for c in fake_source.calls] == [
        ("mcp", 0)
    ]
    assert fake_source.calls[0]["thread"] is threading.current_thread(), (
        "no thread and no timer"
    )
    assert "sync_pending" not in result


def test_mcp_get_session_outline_runs_the_inline_copy(
    mcp: Any, fake_source: FakeSource
) -> None:
    uuid = _seed_desktop_conversation()

    result = _tool(mcp, "get_session_outline", conversation_id=uuid)

    assert [c["reason"] for c in fake_source.calls] == ["mcp"]
    assert "sync_pending" not in result


def test_mcp_copies_at_most_once_per_debounce_interval(
    mcp: Any, fake_source: FakeSource
) -> None:
    _tool(mcp, "list_sessions")
    _tool(mcp, "list_sessions")
    _tool(mcp, "list_projects")

    assert len(fake_source.calls) == 1, fake_source.calls


def test_mcp_copies_again_after_the_debounce_interval(
    mcp: Any, fake_source: FakeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC", "0.05")

    _tool(mcp, "list_sessions")
    time.sleep(0.2)
    _tool(mcp, "list_sessions")

    assert len(fake_source.calls) == 2, fake_source.calls


def test_mcp_returns_sync_pending_and_the_archive_as_it_is_when_the_lock_is_busy(
    mcp: Any, fake_source: FakeSource
) -> None:
    uuid = _seed_desktop_conversation()
    lock = fake_source.archive_lock()
    assert lock.acquire(0)
    try:
        listed = _tool(mcp, "list_sessions")
        outline = _tool(mcp, "get_session_outline", conversation_id=uuid)
    finally:
        lock.release()

    assert listed.get("sync_pending") is True
    assert outline.get("sync_pending") is True
    assert [s["uuid"] for s in listed["sessions"]] == [uuid]
    assert outline["message_count"] == 1


def test_mcp_busy_try_does_not_start_the_debounce_interval(
    mcp: Any, fake_source: FakeSource
) -> None:
    lock = fake_source.archive_lock()
    assert lock.acquire(0)
    try:
        busy = _tool(mcp, "list_sessions")
    finally:
        lock.release()

    after = _tool(mcp, "list_sessions")

    assert busy.get("sync_pending") is True
    assert [c["skipped_reason"] for c in fake_source.calls] == ["busy", None]
    assert "sync_pending" not in after, (
        "sync_pending ends after a pass of this process finishes"
    )


def test_mcp_corrupt_config_skips_the_copy_without_sync_pending(
    mcp: Any, fake_source: FakeSource, fake_home: Path
) -> None:
    write_corrupt_config(fake_home)

    result = _tool(mcp, "list_sessions")

    assert fake_source.calls == []
    assert "sync_pending" not in result
