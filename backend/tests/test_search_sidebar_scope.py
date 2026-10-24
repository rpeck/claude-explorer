"""Tests for /api/search sidebar-scope propagation (2026-05-14).

The sidebar's three composing filters — source, workspace (organization_id),
and the client-side active-filter graph — must narrow full-text search the
same way they narrow the sidebar list. The pre-existing `source`,
`conversation_uuid`, `project_path`, and `bookmarks` params still compose;
this file adds:

  * `organization_id` (workspace filter)
  * `conversation_uuids` (the post-active-filter set, computed client-side
    and passed as CSV on GET or a JSON array on POST)
  * POST /api/search with a JSON body (for callers whose `conversation_uuids`
    set is too large for a GET query string).

Council convergence (2026-05-14, Gemini-3-Pro + GPT-5.2):

  * GET CSV is unsafe at scale — h11/uvicorn default request-line limits
    (~8 KB) and corporate proxies can refuse large GETs. POST is the
    correct transport for the UI's "narrow to this UUID set" case.
  * The FTS5 fast path MUST push `conversation_uuids` into SQL via a
    TEMP TABLE join — naive Python post-filter after `LIMIT 5000`
    silently drops real matches when the filter is restrictive AND the
    user's corpus is large enough that the top-5000 by bm25 fall outside
    the allowed set.

Invariants pinned here (spec §5):

  * I1 — Visibility-set parity (search ⊆ sidebar list).
  * I3 — Active-filter sidebar↔search parity (passing UUIDs constrain search).
  * I4 — Restoration (omit param → behavior identical to no constraint).
"""

import json

import pytest
from fastapi.testclient import TestClient

from backend import config as cfg
from backend.cache import _conversation_cache
from backend.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
#
# Conventions (mirroring backend/tests/test_search_scope.py):
#   * Real-UUID-shaped basenames so backend/store.py's
#     `_UUID_FILENAME_RE` filter doesn't silently skip them.
#   * Claude Desktop blobs live in `<data_dir>/by-org/<org>/<uuid>.json`.
#   * Claude Code blobs live in `<claude_dir>/projects/<encoded-cwd>/<uuid>.jsonl`.
#
# UUIDs are not random: each gets a stable 36-char hex pattern so test
# assertions can use the symbolic names below.

UUID_A = "00000000-0000-0000-0000-0000000000aa"
UUID_B = "00000000-0000-0000-0000-0000000000bb"
UUID_C = "00000000-0000-0000-0000-0000000000cc"
UUID_NONEXISTENT = "00000000-0000-0000-0000-0000deadbeef"


def _desktop_conv(
    uuid: str,
    name: str,
    *,
    project_path: str | None = None,
    text: str = "needle in haystack",
    organization_id: str | None = None,
):
    """Build a Claude Desktop conversation dict (lives as JSON on disk).

    `source` is always "CLAUDE_AI" because the store skips JSON files with
    source="CLAUDE_CODE" (those must come from JSONLs under claude_dir).
    """
    return {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": f"{uuid}-m1",
        "project_path": project_path,
        "source": "CLAUDE_AI",
        "organization_id": organization_id,
        "chat_messages": [
            {
                "uuid": f"{uuid}-m1",
                "sender": "human",
                "text": text,
                "content": [{"type": "text", "text": text}],
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "parent_message_uuid": None,
            },
        ],
    }


def _write_cc_jsonl(
    claude_dir,
    *,
    session_uuid: str,
    cwd: str,
    text: str,
):
    """Write a 2-message Claude Code JSONL session.

    Mirrors the shape used in backend/tests/fixtures/claude/projects/.
    The `cwd` of "/work/foo" gets encoded to "-work-foo" as the directory
    name (matches the production layout discovered by claude_code_reader).
    """
    encoded = cwd.replace("/", "-")
    proj_dir = claude_dir / "projects" / encoded
    proj_dir.mkdir(parents=True, exist_ok=True)
    user_uuid = f"{session_uuid}-u"
    asst_uuid = f"{session_uuid}-a"
    lines = [
        {
            "cwd": cwd,
            "entrypoint": "cli",
            "gitBranch": "main",
            "isSidechain": False,
            "message": {"content": text, "role": "user"},
            "parentUuid": None,
            "sessionId": session_uuid,
            "timestamp": "2026-05-01T12:00:00Z",
            "type": "user",
            "userType": "external",
            "uuid": user_uuid,
            "version": "2.0.0",
        },
        {
            "cwd": cwd,
            "entrypoint": "cli",
            "gitBranch": "main",
            "isSidechain": False,
            "message": {
                "content": [{"text": "ok", "type": "text"}],
                "id": "asst-msg-id",
                "model": "claude-sonnet-4-6",
                "role": "assistant",
                "stop_reason": "end_turn",
                "type": "message",
                "usage": {
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
            },
            "parentUuid": user_uuid,
            "sessionId": session_uuid,
            "timestamp": "2026-05-01T12:01:00Z",
            "type": "assistant",
            "userType": "external",
            "uuid": asst_uuid,
            "version": "2.0.0",
        },
    ]
    path = proj_dir / f"{session_uuid}.jsonl"
    with path.open("w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path


@pytest.fixture
def fixture_three(tmp_path, monkeypatch):
    """Three conversations:
      * ConvA = CLAUDE_AI (Desktop), project_path /work/foo,
                organization_id=org_a, title "foo project chat",
                body "needle in conv A".
      * ConvB = CLAUDE_CODE, cwd /work/bar (project_name "bar"),
                organization_id=None (CC has no org), body "needle in conv B".
      * ConvC = CLAUDE_CODE, cwd /work/foo (project_name "foo"),
                organization_id=None, body "needle in conv C".

    All three contain "needle" in the message body.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()

    org_a = "00000000-0000-0000-0000-aaaaaaaaaaaa"

    # ConvA — Desktop JSON
    conv_a = _desktop_conv(
        UUID_A,
        "foo project chat",
        project_path="/work/foo",
        text="needle in conv A",
        organization_id=org_a,
    )
    by_org = data_dir / "by-org" / org_a
    by_org.mkdir(parents=True)
    (by_org / f"{UUID_A}.json").write_text(json.dumps(conv_a))

    # ConvB — CC JSONL, cwd=/work/bar
    _write_cc_jsonl(
        claude_dir,
        session_uuid=UUID_B,
        cwd="/work/bar",
        text="needle in conv B",
    )

    # ConvC — CC JSONL, cwd=/work/foo
    _write_cc_jsonl(
        claude_dir,
        session_uuid=UUID_C,
        cwd="/work/foo",
        text="needle in conv C",
    )

    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()
    yield {
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "claude_dir": claude_dir,
        "org_a": org_a,
    }
    _conversation_cache.clear()
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Sanity: existing behavior still holds (pin tests we depend on)
# ---------------------------------------------------------------------------


def test_unscoped_returns_all_three(fixture_three):
    """Sanity pin: the fixture itself works and 'needle' hits all 3."""
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle"})
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == sorted([UUID_A, UUID_B, UUID_C])


def test_source_claude_code_returns_b_and_c(fixture_three):
    """Sanity pin: existing source filter still narrows."""
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle", "source": "CLAUDE_CODE"})
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == sorted([UUID_B, UUID_C])


def test_project_path_foo_returns_a_and_c(fixture_three):
    """Sanity pin: existing project_path filter still narrows."""
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle", "project_path": "/work/foo"})
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == sorted([UUID_A, UUID_C])


def test_source_and_project_compose(fixture_three):
    """Sanity pin: source AND project_path → C only."""
    client = TestClient(app)
    r = client.get(
        "/api/search",
        params={"q": "needle", "source": "CLAUDE_CODE", "project_path": "/work/foo"},
    )
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == [UUID_C]


# ---------------------------------------------------------------------------
# NEW: organization_id (workspace) filter
# ---------------------------------------------------------------------------


def test_organization_id_filter_org_a_returns_only_conv_a(fixture_three):
    """ConvA has organization_id=org_a; ConvB and ConvC are CC (no org_id).
    A workspace filter on org_a returns ONLY ConvA — CC sessions don't carry
    an organization_id, so they fall outside any workspace filter (which is
    the correct sidebar behavior: the workspace selector only filters
    workspace-aware Claude.ai conversations).
    """
    client = TestClient(app)
    org_a = fixture_three["org_a"]
    r = client.get("/api/search", params={"q": "needle", "organization_id": org_a})
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == [UUID_A]


def test_organization_id_composes_with_source(fixture_three):
    """org_a + source=CLAUDE_CODE → empty (CC has no org_id, so the
    intersection is empty). Confirms ANDing, not most-specific-wins.
    """
    client = TestClient(app)
    org_a = fixture_three["org_a"]
    r = client.get(
        "/api/search",
        params={"q": "needle", "organization_id": org_a, "source": "CLAUDE_CODE"},
    )
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_organization_id_composes_with_project_path(fixture_three):
    """org_a (only ConvA) AND project_path=/work/foo (ConvA+ConvC) → ConvA."""
    client = TestClient(app)
    org_a = fixture_three["org_a"]
    r = client.get(
        "/api/search",
        params={"q": "needle", "organization_id": org_a, "project_path": "/work/foo"},
    )
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == [UUID_A]


def test_unknown_organization_id_returns_empty(fixture_three):
    """A workspace filter for a UUID nobody has → empty results."""
    client = TestClient(app)
    r = client.get(
        "/api/search",
        params={"q": "needle", "organization_id": UUID_NONEXISTENT},
    )
    assert r.status_code == 200
    assert r.json()["results"] == []


# ---------------------------------------------------------------------------
# NEW: conversation_uuids filter (GET / CSV form)
# ---------------------------------------------------------------------------


def test_conversation_uuids_csv_filters_to_set(fixture_three):
    """Comma-separated UUID list constrains search to that set (active filter)."""
    client = TestClient(app)
    r = client.get(
        "/api/search",
        params={"q": "needle", "conversation_uuids": f"{UUID_A},{UUID_C}"},
    )
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == sorted([UUID_A, UUID_C])


def test_conversation_uuids_with_unknown_uuid_silently_ignored(fixture_three):
    """An unknown UUID in the set doesn't error — it just contributes no rows."""
    client = TestClient(app)
    r = client.get(
        "/api/search",
        params={"q": "needle", "conversation_uuids": f"{UUID_A},{UUID_NONEXISTENT}"},
    )
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == [UUID_A]


def test_conversation_uuids_empty_string_returns_empty(fixture_three):
    """conversation_uuids='' means 'filter excludes everything' → empty results.

    Distinguishes from absence of the param (which means 'no constraint').
    Spec §2 empty-set semantics.
    """
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle", "conversation_uuids": ""})
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_conversation_uuids_absent_means_no_constraint(fixture_three):
    """Param absence is distinct from empty set: all three convs still match."""
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "needle"})  # no conversation_uuids
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == sorted([UUID_A, UUID_B, UUID_C])


def test_conversation_uuids_composes_with_source(fixture_three):
    """ConvA + ConvC (active-filter set) AND source=CLAUDE_CODE → C only."""
    client = TestClient(app)
    r = client.get(
        "/api/search",
        params={
            "q": "needle",
            "conversation_uuids": f"{UUID_A},{UUID_C}",
            "source": "CLAUDE_CODE",
        },
    )
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == [UUID_C]


def test_conversation_uuids_composes_with_organization_id(fixture_three):
    """ConvB + ConvC (active-filter set) AND organization_id=org_a → empty.
    (Neither ConvB nor ConvC has org_a; only ConvA does, and ConvA is not
    in the active-filter set.)
    """
    client = TestClient(app)
    org_a = fixture_three["org_a"]
    r = client.get(
        "/api/search",
        params={
            "q": "needle",
            "conversation_uuids": f"{UUID_B},{UUID_C}",
            "organization_id": org_a,
        },
    )
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_pin_conversation_uuid_overrides_conversation_uuids(fixture_three):
    """Pin scope (singular conversation_uuid) is most-specific; wins over the
    active-filter set. If a user pinned ConvB and the active filter set is
    [ConvA, ConvC], the search returns ConvB.
    Spec §2 precedence rule: most-specific filter wins.
    """
    client = TestClient(app)
    r = client.get(
        "/api/search",
        params={
            "q": "needle",
            "conversation_uuid": UUID_B,
            "conversation_uuids": f"{UUID_A},{UUID_C}",
        },
    )
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    # conversation_uuid (singular) wins over the set; ConvB matches.
    assert uuids == [UUID_B]


# ---------------------------------------------------------------------------
# NEW: POST /api/search with JSON body
# ---------------------------------------------------------------------------


def test_post_search_with_json_body_returns_filtered_set(fixture_three):
    """POST /api/search with conversation_uuids in JSON body must work."""
    client = TestClient(app)
    body = {
        "q": "needle",
        "conversation_uuids": [UUID_A, UUID_C],
    }
    r = client.post("/api/search", json=body)
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == sorted([UUID_A, UUID_C])


def test_post_search_empty_conversation_uuids_returns_empty(fixture_three):
    """POST with conversation_uuids=[] → empty results (matches GET semantics)."""
    client = TestClient(app)
    body = {"q": "needle", "conversation_uuids": []}
    r = client.post("/api/search", json=body)
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_post_search_absent_conversation_uuids_means_no_constraint(fixture_three):
    """POST without conversation_uuids → all three results."""
    client = TestClient(app)
    body = {"q": "needle"}
    r = client.post("/api/search", json=body)
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == sorted([UUID_A, UUID_B, UUID_C])


def test_post_search_with_all_scope_params(fixture_three):
    """End-to-end: POST with source + project_path + conversation_uuids
    intersects correctly. ConvA (Desktop, /work/foo) + ConvB (CC, /work/bar)
    + ConvC (CC, /work/foo):
      source=CLAUDE_CODE        → B + C
      project_path=/work/foo    → A + C
      uuids=[A, B, C]           → all 3 (no narrowing)
      Intersection             → C only.
    """
    client = TestClient(app)
    body = {
        "q": "needle",
        "source": "CLAUDE_CODE",
        "project_path": "/work/foo",
        "conversation_uuids": [UUID_A, UUID_B, UUID_C],
    }
    r = client.post("/api/search", json=body)
    assert r.status_code == 200
    uuids = sorted(item["conversation_uuid"] for item in r.json()["results"])
    assert uuids == [UUID_C]


def test_post_search_get_parity_for_common_params(fixture_three):
    """The POST body and GET query params must produce IDENTICAL responses
    for any value combination the GET can express. Pin invariant: same
    `search_conversations()` internals; only the transport differs.
    """
    client = TestClient(app)
    get_r = client.get(
        "/api/search",
        params={
            "q": "needle",
            "source": "CLAUDE_CODE",
            "project_path": "/work/foo",
        },
    )
    post_r = client.post(
        "/api/search",
        json={
            "q": "needle",
            "source": "CLAUDE_CODE",
            "project_path": "/work/foo",
        },
    )
    assert get_r.status_code == 200
    assert post_r.status_code == 200
    # Identical conversation lists (ordering preserved by the same sort path).
    assert get_r.json() == post_r.json()


# ---------------------------------------------------------------------------
# Bidirectional restoration (Invariant I4)
# ---------------------------------------------------------------------------


def test_restoring_filter_restores_excluded_results(fixture_three):
    """Toggling the active filter off restores previously-excluded results.

    Operationally: passing conversation_uuids=A → only A; removing the param
    → all three. No re-fetch tricks needed; the absence of the param means
    'no constraint', identical to the no-filter state.
    """
    client = TestClient(app)
    # With active filter restricting to ConvA only.
    r = client.get("/api/search", params={"q": "needle", "conversation_uuids": UUID_A})
    assert sorted(i["conversation_uuid"] for i in r.json()["results"]) == [UUID_A]

    # Filter removed (param absent) — all three back.
    r = client.get("/api/search", params={"q": "needle"})
    assert sorted(i["conversation_uuid"] for i in r.json()["results"]) == sorted(
        [UUID_A, UUID_B, UUID_C]
    )
