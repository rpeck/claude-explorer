"""Layer 3 of PLANS/2026.05.18-config-corruption-safe-mode.md:
``/api/config`` carries ``config_corrupt_reason`` so the UI banner can
render the recovery hint, and the route clears the ``get_settings``
lru_cache on every request so the user's "fix the file, refresh the
UI" recovery flow works without a server restart.

Why a new file: ``test_config.py`` pins the existing
``data_dir``/``conversation_count`` contract; this file pins the L3
additions. Splitting keeps the failure-mode docstrings tight.

Discipline:

* **Bidirectional pairs**: every "reason present" test pairs with
  "reason absent". A trivially-broken impl that always emitted the
  field with a hard-coded string would pass the present-side alone.
* **Recovery-flow E2E**: the lru_cache test simulates the actual
  user-visible flow — corrupt file at boot, /api/config returns
  reason → user fixes file → next /api/config returns null reason.
  Without this, a future "skip cache_clear for performance" refactor
  could silently break the recovery flow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tests._platform_home import patch_home
from fastapi.testclient import TestClient

from backend import config
from backend.main import app


@pytest.fixture
def isolated_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point HOME at a tmp dir so the real /api/config endpoint reads
    the test-only ``config.json``.

    Unlike the L2 tests (which override ``get_settings`` via
    ``app.dependency_overrides``), this layer's recovery flow MUST
    drive Settings.load() through its real on-disk path — otherwise
    the cache_clear → re-read invariant is untested.
    """
    patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAUDE_EXPLORER_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_EXPORTER_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_DIR", raising=False)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def _write_canonical_config(home: Path, contents: str) -> Path:
    cfg_dir = home / config.CANONICAL_HOME_DIR_NAME
    cfg_dir.mkdir(exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(contents)
    return cfg_path


# -- Response-shape pair: reason present vs absent ---------------------


def test_api_config_omits_reason_when_clean(isolated_home: Path) -> None:
    """Bidirectional pair: a clean config produces a response with
    ``config_corrupt_reason: null``.

    Without this pin, a "trivially-broken" impl that always emitted
    the field with the same fixed string would pass the corrupt-side
    test alone. Tightening the JSON shape on both sides forces the
    field to be derived from real Settings state.
    """
    custom = isolated_home / "custom_data"
    _write_canonical_config(
        isolated_home, json.dumps({"data_dir": str(custom)})
    )

    client = TestClient(app)
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "config_corrupt_reason" in body, (
        f"AppConfig must always carry the field (even when null) so "
        f"the frontend's optional-chaining works; got keys={list(body.keys())!r}"
    )
    assert body["config_corrupt_reason"] is None


def test_api_config_carries_corruption_reason(isolated_home: Path) -> None:
    """The L3 wire contract: when the config file fails to parse,
    /api/config surfaces the failure reason in the response body so
    the UI banner can render it verbatim.
    """
    cfg_path = _write_canonical_config(
        isolated_home, '{"data_dir":'  # truncated JSON
    )

    client = TestClient(app)
    r = client.get("/api/config")
    assert r.status_code == 200, (
        "config endpoint must stay 200 even when config is corrupt — "
        f"the banner consumes this response. got {r.status_code}"
    )
    body = r.json()
    reason = body["config_corrupt_reason"]
    assert reason is not None, (
        f"config_corrupt_reason must be populated when config.json is corrupt; "
        f"got body={body!r}"
    )
    # Path provenance — the banner shows the user WHICH file to fix.
    assert str(cfg_path) in reason, (
        f"reason must include the corrupt file's path; got {reason!r}"
    )
    # Exception detail — the banner shows WHY.
    assert "JSONDecodeError" in reason, (
        f"reason must name the underlying exception class; got {reason!r}"
    )


# -- The recovery flow: cache_clear lets the UI banner clear -----------


def test_api_config_cache_clear_picks_up_repaired_file(
    isolated_home: Path,
) -> None:
    """User's mid-session recovery flow:

      1. /api/config returns corrupt reason (banner appears).
      2. User opens config.json in their editor, fixes the syntax.
      3. User refreshes the UI; next /api/config call returns reason=null
         (banner clears) WITHOUT a server restart.

    This is the linchpin test for the cache-clear strategy: if the
    /api/config route doesn't clear the ``get_settings`` lru_cache on
    each request, step 3 silently fails — the user fixes the file, the
    UI keeps polling, but the cached Settings still carries the old
    corruption reason. The user concludes the app is broken even
    though their fix was correct.

    Validates Risk R1 in PLANS/2026.05.18-config-corruption-safe-mode.md.
    """
    cfg_path = _write_canonical_config(
        isolated_home, '{"data_dir":'  # corrupt
    )

    client = TestClient(app)

    # Step 1: corrupt → reason populated.
    r1 = client.get("/api/config")
    assert r1.status_code == 200
    assert r1.json()["config_corrupt_reason"] is not None, (
        "precondition: first /api/config call should detect corruption"
    )

    # Step 2: user fixes the file (mid-session).
    custom = isolated_home / "fixed_data"
    cfg_path.write_text(json.dumps({"data_dir": str(custom)}))

    # Step 3: next call MUST reflect the repaired state. Without the
    # per-request cache_clear, this assertion fails — the lru_cache
    # returns the original corrupt Settings forever.
    r2 = client.get("/api/config")
    assert r2.status_code == 200
    assert r2.json()["config_corrupt_reason"] is None, (
        "after repair, /api/config must reflect the fixed state — "
        "the route's per-request lru_cache clear is the recovery "
        f"linchpin; got {r2.json()!r}"
    )
    # The fixed data_dir should also be honored.
    assert r2.json()["data_dir"] == str(custom)


# -- Stats endpoint untouched: existing contract preserved -------------


def test_api_config_stats_unaffected_when_clean(isolated_home: Path) -> None:
    """/api/config/stats is a separate, slower endpoint. The L3 work
    intentionally does NOT add cache_clear there (Python Expert
    decision: avoid double-clearing during concurrent /config + /stats
    polls). Pin that /stats response shape stays as-is when config
    is clean — guards against an unintended cross-endpoint change.
    """
    custom = isolated_home / "custom_data"
    _write_canonical_config(
        isolated_home, json.dumps({"data_dir": str(custom)})
    )

    client = TestClient(app)
    r = client.get("/api/config/stats")
    assert r.status_code == 200
    body = r.json()
    # Existing contract: data_dir and conversation_count.
    assert "data_dir" in body
    assert "conversation_count" in body
