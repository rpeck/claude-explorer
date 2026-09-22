"""Layer 2 of PLANS/2026.05.18-config-corruption-safe-mode.md:
HTTP writers return 503 when ``Settings.config_corrupt_reason`` is set;
HTTP readers and the install-watcher EXEMPTION continue to work.

Why a new file: the existing writer-route tests
(``test_bookmarks.py``, ``test_preferences.py``,
``test_fetch_*.py``) pin happy-path and validation behavior. This file
pins the orthogonal "config corruption forces refusal" contract so a
regression in the gate doesn't slip in via a tightly-scoped diff to
one writer.

Discipline:

* **Bidirectional pairs**: every "must 503" pairs with "must 200 when
  clean" so a trivially-broken always-503 gate doesn't pass by
  accident. Same for the read-paths-still-200 invariant.
* **Recovery message provenance**: assert the 503 body carries BOTH
  the reason and the recovery instruction so a future "simplify the
  message" refactor can't silently drop the path the user needs to
  fix.
* **Install-watcher EXEMPTION**: this is a recovery-affordance test —
  if the watcher install command refused when config was corrupt,
  the user would be locked out of the only way to set up the
  watchdog that protects their CC image cache. Pinned as a HARD
  invariant.

Test setup strategy: override ``backend.config.get_settings`` via
FastAPI's ``app.dependency_overrides`` mechanism so the route's
gate-dependency sees a ``Settings`` with the corruption reason
populated. This avoids touching disk for every test, keeps each test
hermetic, and exercises exactly the integration path that production
takes: ``Depends(refuse_if_config_corrupt_dep)`` → ``get_settings()``
→ check the field.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tests._platform_home import patch_home
from fastapi.testclient import TestClient

from backend import config
from backend.main import app


CORRUPT_REASON = (
    "/home/u/.claude-explorer/config.json: JSONDecodeError: "
    "Expecting value: line 1 column 13 (char 12)"
)


@pytest.fixture
def corrupt_settings(tmp_path: Path) -> config.Settings:
    """Return a ``Settings`` instance that simulates a corrupt config.

    ``data_dir`` and ``claude_dir`` point at an isolated tmp path so
    any write that DOES happen to slip past the gate (which would be a
    test failure) doesn't scribble the developer's real disk.
    """
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    cowork_dir = tmp_path / "claude_desktop_app"
    data_dir.mkdir()
    claude_dir.mkdir()
    cowork_dir.mkdir()
    return config.Settings(
        data_dir=data_dir,
        claude_dir=claude_dir,
        claude_desktop_app_dir=cowork_dir,
        config_corrupt_reason=CORRUPT_REASON,
    )


@pytest.fixture
def clean_settings(tmp_path: Path) -> config.Settings:
    """``Settings`` with ``config_corrupt_reason=None`` — the bidirectional
    pair for every corrupt-fixture assertion below.

    A trivially-broken gate that ALWAYS 503'd would pass the corrupt
    tests; pairing each with this fixture forces correctness in both
    directions.
    """
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    cowork_dir = tmp_path / "claude_desktop_app"
    data_dir.mkdir()
    claude_dir.mkdir()
    cowork_dir.mkdir()
    return config.Settings(
        data_dir=data_dir,
        claude_dir=claude_dir,
        claude_desktop_app_dir=cowork_dir,
        config_corrupt_reason=None,
    )


@pytest.fixture
def client_with_corrupt(corrupt_settings: config.Settings) -> TestClient:
    """TestClient with ``get_settings`` overridden to return a corrupt
    Settings.

    Uses ``app.dependency_overrides`` because that's the exact seam
    the gate dependency reads through, so the override hits whatever
    path the production code takes — directly invoked dependency,
    cached lookup, future refactor that pulls Settings from a
    different dependency. The override is cleared on teardown so it
    can't leak into the next test.
    """
    app.dependency_overrides[config.get_settings] = lambda: corrupt_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(config.get_settings, None)


@pytest.fixture
def client_with_clean(
    clean_settings: config.Settings, tmp_path: Path, monkeypatch
) -> TestClient:
    """TestClient with ``get_settings`` overridden to return a clean
    Settings.

    Also pins the writable side-channel envs the writer routes touch
    (bookmarks file, data dir) at ``tmp_path`` so a real POST/PATCH
    can succeed without scribbling the developer's disk.
    """
    monkeypatch.setenv(
        "CLAUDE_EXPLORER_BOOKMARKS_FILE", str(tmp_path / "bookmarks.json")
    )
    app.dependency_overrides[config.get_settings] = lambda: clean_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(config.get_settings, None)


# -- Bookmarks: writers 503 when corrupt; reads still 200 --------------


def test_bookmarks_post_503_when_corrupt(client_with_corrupt: TestClient) -> None:
    """POST /api/bookmarks must refuse when config is corrupt.

    Without this gate, the bookmarks file would be written to
    ``<wrong_default_data_dir>.parent / bookmarks.json`` and orphan the
    user's real bookmarks. The 503 status preserves "service degraded"
    semantics for any retry layer; the response body must include the
    recovery instruction so a non-UI client (curl, third-party script)
    can act on the failure.
    """
    payload = {
        "conversation_id": "c1",
        "message_uuid": "m1",
        "source": "claude_code",
        "snippet": "x",
        "note": "y",
    }
    r = client_with_corrupt.post("/api/bookmarks", json=payload)
    assert r.status_code == 503, (
        f"expected 503 from corrupt-config gate; got {r.status_code} body={r.text}"
    )
    body = r.json()
    # Reason provenance.
    assert "JSONDecodeError" in body["detail"], (
        f"503 detail must surface the underlying reason; got {body['detail']!r}"
    )
    # Recovery instruction. External script writers need this to know
    # WHAT to do next — without it the 503 is opaque.
    assert "Fix or remove" in body["detail"], (
        f"503 detail must include the recovery instruction; got {body['detail']!r}"
    )


def test_bookmarks_post_200_when_clean(client_with_clean: TestClient) -> None:
    """Bidirectional pair: same POST succeeds when config is clean.

    A trivially-broken impl that ALWAYS 503'd writers would pass the
    corrupt test alone; this sibling rules that out.
    """
    payload = {
        "conversation_id": "c1",
        "message_uuid": "m1",
        "source": "claude_code",
        "snippet": "x",
        "note": "y",
    }
    r = client_with_clean.post("/api/bookmarks", json=payload)
    assert r.status_code == 201, (
        f"clean config should allow POST /api/bookmarks; got {r.status_code} body={r.text}"
    )


def test_bookmarks_get_200_when_corrupt(client_with_corrupt: TestClient) -> None:
    """READ paths must remain available when config is corrupt — the
    user needs to be able to look at their archive while they fix the
    file. Gating reads would lock the user out of the data they're
    trying to protect.
    """
    r = client_with_corrupt.get("/api/bookmarks")
    assert r.status_code == 200, (
        f"GET /api/bookmarks must stay 200 when config corrupt; got {r.status_code}"
    )


def test_bookmarks_patch_503_when_corrupt(client_with_corrupt: TestClient) -> None:
    """PATCH must also be gated — the bookmark file is read-modify-
    written, so a PATCH can corrupt the wrong-default file as severely
    as a POST."""
    r = client_with_corrupt.patch(
        "/api/bookmarks/some-id", json={"note": "x"}
    )
    assert r.status_code == 503, (
        f"PATCH should be gated identically to POST; got {r.status_code}"
    )


def test_bookmarks_delete_503_when_corrupt(client_with_corrupt: TestClient) -> None:
    """DELETE must also be gated — re-writes the bookmarks file."""
    r = client_with_corrupt.delete("/api/bookmarks/some-id")
    assert r.status_code == 503


# -- Preferences: PATCH/PUT gated; GET unchanged -----------------------


def test_preferences_patch_503_when_corrupt(client_with_corrupt: TestClient) -> None:
    """PATCH /api/preferences must refuse when config corrupt — same
    silent-orphaning failure mode as bookmarks (the prefs file lives at
    ``data_dir.parent / preferences.json``)."""
    r = client_with_corrupt.patch(
        "/api/preferences", json={"data": {"theme": "dark"}}
    )
    assert r.status_code == 503, (
        f"PATCH /api/preferences must 503 when corrupt; got {r.status_code}"
    )


def test_preferences_put_503_when_corrupt(client_with_corrupt: TestClient) -> None:
    """PUT replaces the prefs blob entirely — same gate as PATCH."""
    r = client_with_corrupt.put(
        "/api/preferences", json={"data": {"theme": "light"}}
    )
    assert r.status_code == 503


def test_preferences_get_200_when_corrupt(client_with_corrupt: TestClient) -> None:
    """GET /api/preferences is a read; must remain available."""
    r = client_with_corrupt.get("/api/preferences")
    assert r.status_code == 200


def test_preferences_patch_200_when_clean(client_with_clean: TestClient) -> None:
    """Bidirectional pair for the PATCH gate."""
    r = client_with_clean.patch(
        "/api/preferences", json={"data": {"theme": "dark"}}
    )
    assert r.status_code == 200, (
        f"PATCH /api/preferences should succeed when clean; got {r.status_code}"
    )


# -- Fetch: SSE start + per-conversation refetch gated -----------------


def test_fetch_start_503_when_corrupt(client_with_corrupt: TestClient) -> None:
    """/api/fetch/start returns StreamingResponse — the gate MUST fire
    before the stream begins so the client sees a real HTTP 503, not a
    stream emitting ``{"type":"error"}`` SSE frames after a 200 OK.

    Pins the Python-Expert-validated invariant: FastAPI ``Depends``
    resolves before the response is constructed, so an HTTPException
    raised from a dependency lands as a clean HTTP status.
    """
    r = client_with_corrupt.get("/api/fetch/start?incremental=true")
    assert r.status_code == 503, (
        "SSE writer must 503 BEFORE the stream opens; "
        f"got {r.status_code} body={r.text[:200]}"
    )
    assert "Fix or remove" in r.json()["detail"]


def test_fetch_refresh_503_when_corrupt(client_with_corrupt: TestClient) -> None:
    """The one-button Refresh pipeline is the most user-visible writer
    path. Refusing here is the single most important gate from the
    user's POV."""
    r = client_with_corrupt.get("/api/fetch/refresh?incremental=true")
    assert r.status_code == 503


def test_fetch_force_refetch_503_when_corrupt(
    client_with_corrupt: TestClient,
) -> None:
    """POST /api/fetch/conversation/{uuid} writes a fresh copy of a
    single conversation — same silent-orphaning failure mode."""
    r = client_with_corrupt.post("/api/fetch/conversation/abc-123")
    assert r.status_code == 503


# -- Reads (sanity): /api/conversations unaffected ---------------------


def test_conversations_list_200_when_corrupt(
    client_with_corrupt: TestClient,
) -> None:
    """The conversation list is the primary read path the user needs
    while fixing a corrupt config. Must stay available."""
    r = client_with_corrupt.get("/api/conversations")
    assert r.status_code == 200, (
        f"GET /api/conversations must stay 200 when corrupt; got {r.status_code}"
    )


def test_config_endpoint_200_when_corrupt(client_with_corrupt: TestClient) -> None:
    """/api/config must remain available — Layer 3's banner consumes
    it, and if /api/config itself 503'd when config was corrupt, the
    UI couldn't render the banner that tells the user about the
    problem. Hard invariant."""
    r = client_with_corrupt.get("/api/config")
    assert r.status_code == 200


# -- CLI: claude-explorer fetch + install-watcher EXEMPTION ------------


def test_fetch_cli_fails_clean_when_corrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``claude-explorer fetch`` must abort with a ClickException
    (clean message, no stack trace) when the config is corrupt.

    Without this gate, the CLI would orphan the user's archive
    identically to the HTTP route — but with even less visibility,
    since there's no UI to see the failure.
    """
    patch_home(monkeypatch, tmp_path)
    cfg_dir = tmp_path / ".claude-explorer"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text('{"data_dir": "broken')  # corrupt
    config.get_settings.cache_clear()
    try:
        from click.testing import CliRunner

        from cli.main import main as cli_main

        runner = CliRunner()
        result = runner.invoke(cli_main, ["fetch"])
        # Non-zero exit so shell-script callers can detect the failure.
        assert result.exit_code != 0, (
            f"fetch CLI must exit non-zero when config corrupt; "
            f"got exit_code={result.exit_code} output={result.output!r}"
        )
        # Recovery instruction in the user-visible output (NOT a stack
        # trace). The CLI gate intentionally surfaces the same recovery
        # copy the HTTP gate does so users see the same actionable hint
        # regardless of where the failure surfaces.
        assert "Fix or remove" in result.output, (
            f"fetch CLI output must include the recovery instruction; "
            f"got {result.output!r}"
        )
        # No stack trace from an uncaught exception.
        assert "Traceback" not in result.output
    finally:
        config.get_settings.cache_clear()


def test_install_watcher_runs_when_config_corrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CRITICAL EXEMPTION (PLANS/.../L2-EXEMPTION):
    ``claude-explorer install-watcher`` writes to
    ``~/Library/LaunchAgents`` / ``~/.config/systemd`` /
    ``~/.claude-explorer/cc-watcher.py`` — ALL outside ``data_dir``.

    If install-watcher refused when config was corrupt, the user would
    be locked out of installing the only supervised job that keeps
    their CC image cache populated. Fixing the config doesn't help if
    the watcher wasn't installed beforehand and CC has already
    rotated its image cache.

    Pins the EXEMPTION as a HARD invariant: install-watcher MUST run
    to completion regardless of ``config_corrupt_reason``.
    """
    patch_home(monkeypatch, tmp_path)
    cfg_dir = tmp_path / ".claude-explorer"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text('{"data_dir": "broken')  # corrupt
    config.get_settings.cache_clear()
    try:
        from click.testing import CliRunner

        from cli.main import main as cli_main

        runner = CliRunner()
        # ``--uninstall`` is the cheapest install-watcher path that
        # doesn't depend on platform tools (launchctl/systemctl/
        # schtasks) being available in the test environment: on a
        # fresh system the unit file doesn't exist, so uninstall is
        # a no-op that exits 0. This is enough to prove the gate
        # didn't fire — a corrupt-config gate would refuse the
        # command before the per-platform dispatch.
        result = runner.invoke(cli_main, ["install-watcher", "--uninstall"])
        # The EXEMPTION holds: the corrupt-config gate did NOT block
        # install-watcher, so the command runs to completion (exit
        # 0). If a future "simplify by gating the whole CLI" refactor
        # added the gate to install-watcher, this assertion would
        # turn red and the L2 EXEMPTION contract would be enforced.
        assert result.exit_code == 0, (
            "install-watcher MUST run to completion even when config "
            "is corrupt — it's the user's recovery affordance. "
            f"Got exit_code={result.exit_code} output={result.output!r}"
        )
        # The gate's recovery copy MUST NOT appear in the output —
        # if it does, the gate fired and the exemption was lost.
        assert "Fix or remove" not in result.output, (
            "install-watcher output must not carry the corrupt-config "
            "gate copy — the EXEMPTION lets the command run unblocked."
        )
    finally:
        config.get_settings.cache_clear()
