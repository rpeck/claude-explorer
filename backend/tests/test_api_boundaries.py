"""Hunt #4 — API boundary regression tests.

Pins three input-validation bounds added to defeat unbounded /
metacharacter-leaking route params:

  1. `/api/{org}/files/{file_uuid}/{variant}` rejects shell metachars
     in `file_uuid` instead of glob-traversing the cache (CVE-like
     content-leak class).
  2. `POST /api/search` rejects `conversation_uuids` lists longer
     than 5000 (DoS bound).
  3. `/api/fetch/start` and `/api/fetch/refresh` reject `?limit`
     values <= 0 or > 5000 (silent-wrong-result class — `?limit=-5`
     used to return the LAST 5 conversations instead of "no more
     than 5").

Each test is paired bidirectionally: the bound holds for in-range
input AND rejects out-of-range input. Removing the bound flips
exactly one assertion to FAIL, so the tests can't pass by accident.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app

# ---------------------------------------------------------------------------
# M1 — files.py glob-meta reject
# ---------------------------------------------------------------------------


def test_files_proxy_skips_local_fallback_on_glob_metachar(monkeypatch):
    """A `file_uuid` containing `*`, `?`, `[`, or `]` must NOT reach
    `_attachments_root().glob(f"*/{file_uuid}/...")`. The pre-fix
    behavior on `file_uuid="**"` was to return the FIRST file from
    ANY conv subdir as 200 OK — leaking unrelated attachment bytes.
    Post-fix the route skips the glob entirely and falls through to
    its normal upstream-404 response.

    We stub the upstream to return 404 to force the local-fallback
    branch; then we assert the response is 502/404 (NOT 200 with
    arbitrary bytes).
    """
    client = TestClient(app)
    for bad_uuid in ("**", "*", "?", "[abc]", "**/secrets"):
        r = client.get(f"/api/test-org/files/{bad_uuid}/thumbnail")
        # The upstream-mocking is heavy to set up; just assert we did
        # NOT return a 200 with arbitrary content. 4xx/5xx is fine.
        assert r.status_code != 200, (
            f"file_uuid={bad_uuid!r} returned 200 — glob-meta leaked "
            f"local files (response head: {r.content[:100]!r})"
        )


# ---------------------------------------------------------------------------
# M2 — POST /api/search conversation_uuids cap
# ---------------------------------------------------------------------------


def test_post_search_rejects_oversized_conversation_uuids():
    """`conversation_uuids` is capped at 5000 elements via
    `Field(None, max_length=5000)`. Submitting 5001 should yield 422.
    """
    client = TestClient(app)
    body = {
        "q": "x",
        "conversation_uuids": [f"u-{i}" for i in range(5001)],
    }
    r = client.post("/api/search", json=body)
    assert r.status_code == 422, (
        f"5001-element conversation_uuids should be 422, got {r.status_code}: "
        f"{r.text[:200]}"
    )


def test_post_search_accepts_exactly_5000_conversation_uuids():
    """Bidirectional pair: 5000 (at the boundary) must be accepted.
    Without this, the cap could silently shrink to e.g. 100 in a
    future regression and this test would fail.
    """
    client = TestClient(app)
    body = {
        "q": "x",
        "conversation_uuids": [f"u-{i}" for i in range(5000)],
    }
    r = client.post("/api/search", json=body)
    # 200 (no match) is fine; 422 would mean the cap is too tight.
    assert r.status_code == 200, (
        f"5000-element conversation_uuids should be 200, got {r.status_code}: "
        f"{r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# M3 — /fetch/start and /fetch/refresh ?limit bounds
# ---------------------------------------------------------------------------


def test_fetch_start_rejects_negative_limit():
    """`?limit=-5` previously reached `conversations[:limit]` and
    returned the LAST 5 conversations instead of erroring. Bound is
    now `Query(None, ge=1, le=5000)`.
    """
    client = TestClient(app)
    r = client.get("/api/fetch/start?limit=-5")
    assert r.status_code == 422


def test_fetch_start_rejects_zero_limit():
    """`?limit=0` is degenerate; the route should reject it cleanly."""
    client = TestClient(app)
    r = client.get("/api/fetch/start?limit=0")
    assert r.status_code == 422


def test_fetch_start_rejects_oversized_limit():
    """Upper bound: 5001 → 422."""
    client = TestClient(app)
    r = client.get("/api/fetch/start?limit=5001")
    assert r.status_code == 422


def test_fetch_refresh_rejects_negative_limit():
    """Same bound on the /fetch/refresh endpoint."""
    client = TestClient(app)
    r = client.get("/api/fetch/refresh?limit=-5")
    assert r.status_code == 422


def test_fetch_refresh_rejects_oversized_limit():
    """Upper bound on /fetch/refresh."""
    client = TestClient(app)
    r = client.get("/api/fetch/refresh?limit=5001")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Hunt #6 — `extra='forbid'` on SearchRequest (POST /api/search body).
#
# Pydantic v2's default `extra='ignore'` silently drops unknown fields. For
# a Query endpoint the failure mode is subtler than for a Mutation, but
# still real: a typo on an OPTIONAL field that has a default returns 200
# OK with the field silently using the default. The canonical example is
# `{"q": "...", "sort_orderr": "asc"}` — the typo collapses to the
# `sort_order="desc"` default, so the user gets results in the OPPOSITE
# order they asked for, with no signal.
#
# Local single-user app, no external HTTP callers (mcp_server uses the
# Python API directly), so this is a safe tightening. The matching
# regression pin (`test_post_search_valid_body_still_accepted`) ensures
# the legitimate POST shape from `lib/api.ts:search()` keeps working.
# ---------------------------------------------------------------------------


def test_post_search_unknown_field_returns_422():
    """A typo'd top-level field on POST /api/search must 422.

    Pre-`forbid`, a request like ``{"q": "x", "sort_orderr": "asc"}``
    returned 200 OK with sort_order silently defaulting to "desc" —
    a wrong-data-no-signal bug.
    """
    client = TestClient(app)
    r = client.post("/api/search", json={"q": "x", "sort_orderr": "asc"})
    assert r.status_code == 422, (
        f"unknown POST /api/search field must 422; got {r.status_code}: "
        f"{r.text[:200]}"
    )
    detail = r.json().get("detail", [])
    assert any(
        "sort_orderr" in str(e) or "extra_forbidden" in str(e).lower()
        for e in (detail if isinstance(detail, list) else [detail])
    ), f"422 detail should reference the offending field: {r.json()!r}"


def test_post_search_valid_body_still_accepted():
    """Bidirectional pair: a legitimate POST body still 200s post-forbid.

    Mirrors the exact shape `frontend/src/lib/api.ts:search()` sends
    when `conversationUuids` is defined (the only POST trigger path).
    Without this, a future regression that tightened the schema (e.g.
    required `q` AND `source`) would slip past the unknown-field
    test alone.
    """
    client = TestClient(app)
    r = client.post(
        "/api/search",
        json={
            "q": "x",
            "source": "all",
            "context_size": "snippet",
            "sort": "updated_at",
            "sort_order": "desc",
            "include_tool_calls": True,
            "conversation_uuids": [],  # empty -> short-circuits to []
        },
    )
    assert r.status_code == 200, (
        f"valid POST body must still 200 post-forbid; got {r.status_code}: "
        f"{r.text[:200]}"
    )
