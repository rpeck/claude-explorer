"""``POST /api/shutdown``: the sidebar's power button stops the server.

Contract: UX.md → "Quit (power button)" and ``backend/routers/shutdown.py``.
The server stops itself with SIGINT, as Ctrl+C does. Tests replace the
signalling boundary (``backend.shutdown._send_signal``), so no test
interrupts the test runner.
"""

from __future__ import annotations

import signal
import time

import pytest
from fastapi.testclient import TestClient

from backend import shutdown

HEADER = {"X-Claude-Explorer-Action": "shutdown"}


@pytest.fixture
def signals(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record signals instead of sending them; pretend to be uvicorn's
    main-thread loop, which a TestClient request is not."""
    sent: list[int] = []
    monkeypatch.setattr(shutdown, "_send_signal", sent.append)
    monkeypatch.setattr(shutdown, "_on_main_thread", lambda: True)
    monkeypatch.setattr(shutdown, "START_DELAY_SEC", 0.01)
    return sent


@pytest.fixture
def live_client(monkeypatch: pytest.MonkeyPatch):
    """A client whose event loop outlives each request, as uvicorn's does.

    The shutdown is scheduled on the loop with ``call_later``; a bare
    ``TestClient(app)`` closes its loop after every request. Entering the
    client runs the app's startup, so skip its background work: these tests
    need none of it, and an index build still running at teardown makes
    the loop wait for its thread.
    """
    for name in ("CLAUDE_EXPLORER_DISABLE_SEARCH_INDEX", "CLAUDE_EXPLORER_DISABLE_CC_WATCHER",
                 "CLAUDE_EXPLORER_DISABLE_CC_WARM", "CLAUDE_EXPLORER_DISABLE_SUMMARY_CACHE_WARM",
                 "CLAUDE_EXPLORER_DISABLE_FTS5_WARM"):
        monkeypatch.setenv(name, "1")
    from backend.main import app

    with TestClient(app) as c:
        yield c


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_shutdown_interrupts_the_server_like_ctrl_c(live_client: TestClient, signals) -> None:
    r = live_client.post("/api/shutdown", headers=HEADER)

    assert r.status_code == 202
    assert r.json() == {"status": "stopping"}
    assert _wait(lambda: signals == [signal.SIGINT])


def test_a_stuck_graceful_shutdown_is_forced_by_a_second_interrupt(
    live_client: TestClient, signals, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutdown, "FORCE_AFTER_SEC", 0.05)

    live_client.post("/api/shutdown", headers=HEADER)

    assert _wait(lambda: signals == [signal.SIGINT, signal.SIGINT])


def test_the_vite_dev_origin_is_allowed(client: TestClient, signals) -> None:
    r = client.post("/api/shutdown", headers={**HEADER, "Origin": "http://localhost:5173"})
    assert r.status_code == 202


@pytest.mark.parametrize(
    "headers, detail",
    [
        ({}, "Missing header X-Claude-Explorer-Action: shutdown."),
        ({"X-Claude-Explorer-Action": "yes"}, "Missing header X-Claude-Explorer-Action: shutdown."),
        ({**HEADER, "Origin": "https://evil.example"}, "Cross-origin shutdown refused."),
        ({**HEADER, "Origin": "http://localhost.evil.example"}, "Cross-origin shutdown refused."),
    ],
)
def test_requests_another_site_could_send_are_refused(
    live_client: TestClient, signals, headers: dict, detail: str
) -> None:
    r = live_client.post("/api/shutdown", headers=headers)

    assert r.status_code == 403
    assert r.json() == {"detail": detail}
    time.sleep(0.1)
    assert signals == []


def test_a_get_never_stops_the_server(live_client: TestClient, signals) -> None:
    r = live_client.get("/api/shutdown", headers=HEADER)

    assert r.status_code != 202
    time.sleep(0.1)
    assert signals == []


def test_off_the_main_thread_it_refuses_instead_of_signalling(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TestClient runs the app off the main thread; uvicorn would never see
    the signal there, so the endpoint must say it cannot stop."""
    sent: list[int] = []
    monkeypatch.setattr(shutdown, "_send_signal", sent.append)

    r = client.post("/api/shutdown", headers=HEADER)

    assert r.status_code == 503
    assert sent == []


def test_serve_caps_the_graceful_shutdown_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """An open Refresh stream must not keep Quit (or Ctrl+C) waiting forever."""
    import uvicorn
    from click.testing import CliRunner

    from cli import main as cli_main

    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(cli_main, "_refuse_busy_port", lambda host, port: None)

    result = CliRunner().invoke(cli_main.main, ["serve", "--port", "8799"])

    assert result.exit_code == 0, result.output
    assert calls[0]["timeout_graceful_shutdown"] == 5


# ------------------------------------------------- PR #6 review, 2026-10-19


def test_the_origin_check_does_not_trust_the_host_header() -> None:
    """A DNS-rebinding page sends an Origin equal to its own Host. Matching
    Origin against Host therefore proves nothing; only the allowlist counts.
    (The Host guard refuses such a request first: test_host_guard.py.)"""
    from backend.routers.shutdown import _origin_allowed

    assert not _origin_allowed("http://rebind.evil.example:8765", "rebind.evil.example:8765")
    assert _origin_allowed("http://localhost:5173", "localhost:8765")
    assert _origin_allowed("http://[::1]:8765", "[::1]:8765")
    assert _origin_allowed(None, "localhost:8765")


def test_a_supervised_server_refuses_instead_of_stopping_one_worker(
    live_client: TestClient, signals, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under ``uvicorn --reload`` or ``--workers``, the app runs in a child
    process. SIGINT would stop only that child, and the supervisor would keep
    running in its terminal. The route must say so instead."""
    import multiprocessing

    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())

    r = live_client.post("/api/shutdown", headers=HEADER)

    assert r.status_code == 503, r.text
    assert "--reload" in r.json()["detail"], r.text
    time.sleep(0.1)
    assert signals == []


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_real_serve_process_stops_and_exits_cleanly(tmp_path) -> None:
    """Every other test here replaces the signal. This one proves, on each CI
    platform, that ``POST /api/shutdown`` really stops ``claude-explorer serve``
    and that it exits with code 0. Windows is the case that matters: the
    launchers start the server with pythonw.exe there."""
    import os
    import subprocess
    import sys
    import urllib.error
    import urllib.request
    from pathlib import Path

    port = _free_port()
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    # Keep the server out of the real home and app-data folders on every OS.
    for name in ("HOME", "USERPROFILE"):
        env[name] = str(home)
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        env[name] = str(home / name.lower())
    env.pop("CLAUDE_EXPLORER_DATA_DIR", None)
    env.pop("CLAUDE_EXPLORER_ALLOWED_HOSTS", None)  # the real server uses the real guard
    env["CLAUDE_EXPLORER_DISABLE_ACCOUNT_LOOKUP"] = "1"

    log_path = tmp_path / "serve.log"
    repo = Path(__file__).resolve().parents[2]
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "cli.main", "serve", "--port", str(port)],
            cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT,
        )

    def log_tail() -> str:
        return log_path.read_text(encoding="utf-8", errors="replace")[-3000:]

    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 90
        while True:
            if proc.poll() is not None:
                pytest.fail(f"serve exited early ({proc.returncode}):\n{log_tail()}")
            try:
                with urllib.request.urlopen(f"{base}/api/config", timeout=2) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"serve never answered:\n{log_tail()}")
            time.sleep(0.5)

        request = urllib.request.Request(
            f"{base}/api/shutdown", data=b"", method="POST",
            headers={"X-Claude-Explorer-Action": "shutdown"},
        )
        with urllib.request.urlopen(request, timeout=10) as r:
            assert r.status == 202

        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail(f"serve did not stop within 30 s:\n{log_tail()}")
        assert code == 0, f"serve exited with {code}:\n{log_tail()}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
