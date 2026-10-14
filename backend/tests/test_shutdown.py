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
def live_client():
    """A client whose event loop outlives each request, as uvicorn's does.

    The shutdown is scheduled on the loop with ``call_later``; a bare
    ``TestClient(app)`` closes its loop after every request.
    """
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
