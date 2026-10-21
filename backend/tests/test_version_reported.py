"""The server reports the installed version (2026-10-21).

The web page showed no version, so a user could not tell which release
runs. The server reported one, but it was wrong: ``/api/info``, the OpenAPI
schema, and the API-only ``/`` all said "0.1.0", hard-coded since the first
PyPI release. ``mcp_server.__version__`` is the single source of truth
(``pyproject.toml`` reads it), and ``/api/config`` now carries it for the
Settings page.
"""

from __future__ import annotations

from mcp_server import __version__


def test_config_reports_the_version(client) -> None:
    body = client.get("/api/config").json()
    assert body["version"] == __version__


def test_api_info_reports_the_version(client) -> None:
    assert client.get("/api/info").json()["version"] == __version__


def test_openapi_reports_the_version(client) -> None:
    assert client.get("/openapi.json").json()["info"]["version"] == __version__


def test_no_route_hard_codes_a_version() -> None:
    from pathlib import Path

    import backend.main

    source = Path(backend.main.__file__).read_text(encoding="utf-8")
    assert '"0.1.0"' not in source


def test_config_stats_reports_the_version(client) -> None:
    # AppConfigStats extends AppConfig, so the stats route needs it too.
    response = client.get("/api/config/stats")
    assert response.status_code == 200, response.text
    assert response.json()["version"] == __version__
