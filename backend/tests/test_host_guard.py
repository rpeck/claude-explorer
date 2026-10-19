"""Only loopback Host names reach the app (DNS-rebinding guard).

Earned 2026-10-19 in the PR #6 review. A web page on any site can make its
own domain resolve to 127.0.0.1 (DNS rebinding). Its requests to this server
are then same-origin: no CORS preflight, and the page reads every response.
A probe with ``Host`` and ``Origin`` both set to the attacker's domain got
200 from ``/api/conversations`` and ``/api/account``, and 202 from
``POST /api/shutdown``. The page cannot change the Host header, so the server
refuses any Host that is not a loopback name. See ``backend/host_guard.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import host_guard
from backend.main import app

REBIND_HOST = "rebind.evil.example:8765"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/config", "/api/conversations?limit=1", "/api/account", "/"])
def test_a_foreign_host_is_refused(client: TestClient, path: str) -> None:
    r = client.get(path, headers={"Host": REBIND_HOST})
    assert r.status_code == 400
    assert r.text == "Invalid host header"


def test_a_rebinding_page_cannot_stop_the_server(client: TestClient, monkeypatch) -> None:
    from backend import shutdown

    sent: list[int] = []
    monkeypatch.setattr(shutdown, "_send_signal", sent.append)
    monkeypatch.setattr(shutdown, "_on_main_thread", lambda: True)
    r = client.post(
        "/api/shutdown",
        headers={
            "Host": REBIND_HOST,
            "Origin": f"http://{REBIND_HOST}",
            "X-Claude-Explorer-Action": "shutdown",
        },
    )
    assert r.status_code == 400
    assert sent == []


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765", "LOCALHOST:8765", "localhost"])
def test_loopback_hosts_are_served(client: TestClient, host: str) -> None:
    assert client.get("/api/config", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "header, name",
    [
        ("localhost:8765", "localhost"),
        ("[::1]:8765", "::1"),
        ("127.0.0.1", "127.0.0.1"),
        ("Evil.Example:80", "evil.example"),
        ("", None),
        ("[::1", None),
    ],
)
def test_host_name_parsing(header: str, name: str | None) -> None:
    assert host_guard.host_name(header) == name


def test_a_missing_host_header_is_refused(monkeypatch) -> None:
    monkeypatch.delenv(host_guard.ENV, raising=False)
    assert host_guard.host_allowed(None) is False
    assert host_guard.host_allowed("localhost:1") is True
    assert host_guard.host_allowed("testserver") is False


def test_extra_hosts_and_wildcard_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv(host_guard.ENV, " myhost , other ")
    assert host_guard.host_allowed("myhost:8765") is True
    assert host_guard.host_allowed("evil.example") is False
    monkeypatch.setenv(host_guard.ENV, "*")
    assert host_guard.host_allowed("evil.example") is True


def test_no_cors_headers_for_other_origins(client: TestClient) -> None:
    """The web page calls /api on its own origin, and Vite proxies /api in
    development, so no browser request needs CORS. Answering CORS would let
    any page on localhost:5173 (any project's dev server) read the archive."""
    r = client.options(
        "/api/config",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = client.get("/api/config", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_the_guard_is_the_outermost_middleware() -> None:
    """It must run before anything else, so no route or middleware sees a
    request with a foreign Host."""
    assert app.user_middleware[0].cls is host_guard.HostGuardMiddleware


@pytest.mark.parametrize(
    "host, opened",
    [("127.0.0.1", False), ("localhost", False), ("::1", False), ("0.0.0.0", True), ("192.168.1.20", True)],
)
def test_serve_opens_the_guard_only_for_a_network_host(monkeypatch, host: str, opened: bool) -> None:
    """``serve --host 0.0.0.0`` means the user chose to expose the server on a
    network; other machines then reach it by names that are not loopback."""
    import uvicorn
    from click.testing import CliRunner

    from cli import main as cli_main

    monkeypatch.setenv(host_guard.ENV, "testserver,test")  # restored after the test
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    monkeypatch.setattr(cli_main, "_refuse_busy_port", lambda host, port: None)

    result = CliRunner().invoke(cli_main.main, ["serve", "--host", host, "--port", "8799"])

    assert result.exit_code == 0, result.output
    import os

    assert ("*" in os.environ[host_guard.ENV].split(",")) is opened
    assert ("reachable from other machines" in result.output) is opened
