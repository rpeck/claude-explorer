"""Port-conflict surfaces an actionable error.

Build-8 #3 (BLOCKER) — see PLANS/explorer-improvements-build.md.

2026-10-13: ``serve`` now checks the port before uvicorn starts. uvicorn
does not raise on a busy port: it runs the whole app startup, logs
``[Errno 48] ... address already in use``, and exits. A user on a new
laptop saw only that log, while an earlier Claude Explorer server already
answered on the port. The pre-check names the cause and the fix.
"""

from __future__ import annotations

import http.server
import re
import socket
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from cli.main import main


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _plain_listener() -> Iterator[int]:
    """A TCP socket that accepts connections but speaks no HTTP."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        yield s.getsockname()[1]


@contextmanager
def _fake_explorer() -> Iterator[int]:
    """An HTTP server that answers ``/api/config`` like Claude Explorer."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status = 200 if self.path == "/api/config" else 404
            self.send_response(status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: object) -> None:
            pass

    # socketserver.TCPServer, not HTTPServer: HTTPServer.server_bind calls
    # socket.getfqdn, which took 35 s on the macOS CI runners.
    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _record_uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw))
    return calls


def test_busy_port_stops_before_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_uvicorn_run(monkeypatch)
    with _plain_listener() as port:
        result = CliRunner().invoke(main, ["serve", "--port", str(port)])

    assert result.exit_code == 1
    assert calls == [], "serve must not start the app on a busy port"
    assert f"another program already uses port {port}" in result.output
    # The fix commands name a port that is free now.
    from cli.desktop_launcher import port_in_use

    serve_fix = re.search(r"claude-explorer serve --port (\d+)", result.output)
    app_fix = re.search(r"claude-explorer install-app --port (\d+)", result.output)
    assert serve_fix and app_fix, result.output
    assert serve_fix.group(1) == app_fix.group(1)
    assert not port_in_use(int(serve_fix.group(1)))


def test_port_check_prints_the_fix_for_the_macos_app() -> None:
    """The macOS app runs `claude-explorer port-check` before it starts the
    server. Line 1 is the command that its Copy Command button copies."""
    with _plain_listener() as port:
        result = CliRunner().invoke(main, ["port-check", "--port", str(port)])
    assert result.exit_code == 0
    first, _, rest = result.output.partition("\n")
    assert re.fullmatch(r"claude-explorer install-app --port \d+", first)
    assert first in rest and "Copy Command" in rest


def test_port_check_is_silent_when_the_app_can_start() -> None:
    with _fake_explorer() as port:
        assert CliRunner().invoke(main, ["port-check", "--port", str(port)]).output == ""
    assert CliRunner().invoke(main, ["port-check", "--port", str(_free_port())]).output == ""


def test_running_explorer_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_uvicorn_run(monkeypatch)
    with _fake_explorer() as port:
        result = CliRunner().invoke(main, ["serve", "--port", str(port)])

    assert result.exit_code == 0
    assert calls == []
    assert "already runs" in result.output
    assert f"http://127.0.0.1:{port}" in result.output


def test_free_port_starts_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_uvicorn_run(monkeypatch)
    port = _free_port()
    result = CliRunner().invoke(main, ["serve", "--port", str(port)])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["port"] == port


def test_address_in_use_oserror_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise OSError(48, "Address already in use")

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", boom)

    port = _free_port()
    runner = CliRunner()
    result = runner.invoke(main, ["serve", "--port", str(port)])

    assert result.exit_code != 0
    combined = (result.output or "") + (str(result.exception) if result.exception else "")
    assert str(port) in combined
    assert "port" in combined.lower()
    assert "--port" in combined


def test_other_oserror_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise OSError(13, "Permission denied")

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", boom)

    runner = CliRunner()
    result = runner.invoke(main, ["serve", "--port", str(_free_port())])

    assert result.exit_code != 0
    assert isinstance(result.exception, OSError)
    assert result.exception.errno == 13
