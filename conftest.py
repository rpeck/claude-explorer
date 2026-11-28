"""Root pytest configuration.

Holds the test-client host names for the backend's Host guard, and one
diagnostic: report threads that would block interpreter shutdown. Windows CI passed every test and still failed the step, because a
worker process never exited and was interrupted at threading shutdown. A
non-daemon thread left running is the usual cause, and it is invisible on
POSIX when the same thread happens to be collected earlier.
"""

from __future__ import annotations

import gc
import os
import threading
from pathlib import Path

import pytest

# The backend answers only loopback Host names (backend/host_guard.py).
# Starlette's TestClient sends "Host: testserver", and the httpx ASGI clients
# in backend/tests use "test". Set here, at the root, because mcp_server/tests
# also calls the backend app.
os.environ.setdefault("CLAUDE_EXPLORER_ALLOWED_HOSTS", "testserver,test")


@pytest.fixture(autouse=True)
def _collect_garbage_after_each_test():
    """Collect garbage at the end of each test.

    pyproject.toml turns ResourceWarning into an error (2026-10-23). The
    warning fires when garbage collection finds an unclosed connection,
    file, or socket. Without this, collection ran at random times, and the
    error failed whatever test ran then. Now it fails the teardown of the
    test that leaked. It costs about 4 s for the suite.
    """
    yield
    gc.collect()


# The four folder isolations below were in backend/tests/conftest.py, which
# does not reach fetcher/tests: there each `claude-explorer fetch` test copied
# the real ~/.claude into the real archive (2026-11-27). Here every suite gets them.


@pytest.fixture(autouse=True)
def _isolate_data_dir_by_default(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """Autouse: point CLAUDE_EXPLORER_DATA_DIR at an isolated empty dir.

    Without this, any code path that calls ``get_settings()`` directly
    (not through a FastAPI dependency a test overrides) resolves the
    developer's real ``~/.claude-explorer/conversations``, and files that
    live next to it, such as ``preferences.json``. On 2026-10-14
    ``test_writer_gate_config_corrupt.py`` wrote ``{"theme": "dark"}`` into
    a developer's real preferences that way, and the installed app opened
    in dark mode.

    Tests that need their own data dir still set the variable (or delete
    it) with ``monkeypatch``; their setting wins because it runs later.
    """
    from backend import config

    data_dir = tmp_path_factory.mktemp("home_isolated") / "conversations"
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Also an empty Claude dir. Without it, a test that runs the app's
    # startup (``with TestClient(app)``) builds the search index over the
    # developer's real ~/.claude/projects: minutes of work in a background
    # thread. The event loop then waits up to 300 s for that thread at exit,
    # and the search-index fixture's teardown closed SQLite connections the
    # thread still used, which crashed an xdist worker (2026-10-19).
    claude_dir = data_dir.parent / "claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    config.get_settings.cache_clear()
    try:
        yield
    finally:
        config.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_cowork_app_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """Autouse: point CLAUDE_DESKTOP_APP_DIR at an isolated empty dir.

    Without this, ``ConversationStore.__init__`` falls through to
    ``platformdirs.user_data_path("Claude")`` which on a developer's
    Mac resolves to ``~/Library/Application Support/Claude``. Tests
    that exercise ``list_conversations(source='all')`` would then
    pull in the developer's REAL Cowork sessions and break dozens of
    fixtures that assume a known-empty corpus.

    Tests that explicitly want Cowork data must either:
      * pass ``cowork_root=`` to ``ConversationStore`` directly
        (e.g. test_store_cowork_integration.py), OR
      * point CLAUDE_DESKTOP_APP_DIR at their own fixture tree via
        monkeypatch BEFORE the store is constructed.

    Cache-clear discipline matches ``isolated_data_dir``: clear the
    @lru_cache on get_settings both before yielding and on teardown.
    """
    from backend import config

    # Use one tmp dir per test session — the dir is empty (no
    # ``local-agent-mode-sessions/`` subdir), so the Cowork reader's
    # ``cowork_root.exists()`` check returns False and the walk
    # short-circuits at zero cost.
    cowork_app_dir = tmp_path_factory.mktemp("cowork_app_isolated")
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(cowork_app_dir))
    config.get_settings.cache_clear()
    try:
        yield
    finally:
        config.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_codex_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """Autouse: point CODEX_HOME at an existing, empty temp folder.

    Without this, ``Settings.codex_home`` falls back to the developer's
    real ``~/.codex``, and no test may read that (plan §5, D10). Tests that
    want Codex data build one with ``backend.tests.codex_fake`` and point
    CODEX_HOME at it with ``monkeypatch``; their setting wins.
    """
    from backend import config

    monkeypatch.setenv("CODEX_HOME", str(tmp_path_factory.mktemp("codex_home_isolated")))
    config.get_settings.cache_clear()
    try:
        yield
    finally:
        config.get_settings.cache_clear()


_REAL_HOME = Path.home()


@pytest.fixture(scope="session")
def _no_identity_path(tmp_path_factory) -> Path:
    """A path that can be neither read nor written: its parent is a file."""
    blocker = tmp_path_factory.mktemp("no-machine-identity") / "not-a-folder"
    blocker.write_text("")
    return blocker / "machine.json"


@pytest.fixture(autouse=True)
def _isolate_machine_identity(_no_identity_path, monkeypatch):
    """Issue #92: a test never reads or writes the machine id of the computer it runs on.

    The id is in ~/.claude-explorer/machine.json, and every Claude Code row
    names it. A test that keeps the real home gets NO machine id: the
    identity cannot be read or made, so the archive copy skips ("this machine
    has no id") and the rows have no machine. A test that sets HOME keeps the
    normal path under that HOME.

    On 2026-11-27 a temporary id per test made each `claude-explorer fetch`
    test copy the real ~/.claude/projects into a new machines/<id>/ folder of
    the real archive (30 folders, the disk filled).
    """
    from backend import archive_sync

    real = archive_sync._identity_file
    monkeypatch.setattr(
        archive_sync, "_identity_file",
        lambda: _no_identity_path if Path.home() == _REAL_HOME else real(),
    )
    monkeypatch.setattr(archive_sync, "_identity_cache", None)
    monkeypatch.setattr(archive_sync, "_holders", {})


def pytest_sessionfinish(session, exitstatus):
    """Name any non-daemon thread still alive when the session ends."""
    lingering = [
        t
        for t in threading.enumerate()
        if t is not threading.main_thread() and t.is_alive() and not t.daemon
    ]
    if not lingering:
        return
    print("\n=== NON-DAEMON THREADS STILL ALIVE AT SESSION END ===")
    for t in lingering:
        print(f"  name={t.name!r} class={type(t).__module__}.{type(t).__qualname__}")
    print("=== these block interpreter shutdown ===")
