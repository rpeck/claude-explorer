"""Tests for `list_sessions` honoring sidebar scope params (2026-05-14).

Spec §3 — MCP contract:
  * ADD: `organization_id` param. MCP and UI agree on the COMMON SUBSET
    of scope filters (source, project, organization_id) for any given
    query.
  * DO NOT ADD: `active_filter` (a `conversation_uuids` parameter). UI-only
    convenience; MCP callers don't know the user's filter names, and the
    filter graph is private to the user's preferences blob.

Invariants pinned here:
  * I2 (subset) — `list_sessions(source=X, project=Y, organization_id=Z)`
    returns the same conversation set as `/api/search?...` with the same
    three filters.
  * Regression guard — `list_sessions` MUST NOT expose `active_filter` as
    a parameter. If a future contributor adds it, this guard breaks.
"""

from __future__ import annotations

import inspect


from mcp_server.server import list_sessions


def _call(**kwargs):
    fn = getattr(list_sessions, "fn", list_sessions)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Regression guard — MCP must NOT learn the active-filter concept.
# ---------------------------------------------------------------------------


def test_list_sessions_does_not_expose_active_filter_param():
    """Spec §3: MCP gets `source`, `project`, and `organization_id` —
    nothing more. Active-filter / conversation_uuids is UI-only.

    If a future contributor adds `active_filter`, `conversation_uuids`,
    or any similar parameter to `list_sessions`, this test fails and
    documents the scope decision.
    """
    fn = getattr(list_sessions, "fn", list_sessions)
    sig = inspect.signature(fn)
    forbidden = {"active_filter", "conversation_uuids", "filter_id"}
    intersection = forbidden & set(sig.parameters.keys())
    assert intersection == set(), (
        f"list_sessions exposes UI-only filter params: {intersection}. "
        "Spec §3 forbids this. If you have a real use case, update the "
        "spec first and remove this guard."
    )


# ---------------------------------------------------------------------------
# NEW: organization_id param
# ---------------------------------------------------------------------------


def test_list_sessions_accepts_organization_id_param():
    """The parameter exists in the signature (typed `str | None`)."""
    fn = getattr(list_sessions, "fn", list_sessions)
    sig = inspect.signature(fn)
    assert "organization_id" in sig.parameters, (
        "list_sessions must accept organization_id per spec §3"
    )


def _desktop_with_org(mcp_data, name: str, *, org_id: str | None, title: str):
    """Write a Claude Desktop conversation tagged with the given org_id.

    The base McpFixture.add_desktop_session() doesn't set organization_id;
    we patch the file after writing so we don't have to widen the fixture
    helper for one test file.
    """
    import json
    uuid = mcp_data.add_desktop_session(name, name=title)
    path = mcp_data.data_dir / f"{uuid}.json"
    blob = json.loads(path.read_text())
    blob["organization_id"] = org_id
    path.write_text(json.dumps(blob, indent=2))
    return uuid


def test_organization_id_filter_returns_only_matching_org(mcp_data):
    """Two Desktop sessions: one in org_a, one in org_b. Filtering
    by org_a returns only the first.
    """
    org_a = "11111111-1111-1111-1111-111111111111"
    org_b = "22222222-2222-2222-2222-222222222222"
    u_a = _desktop_with_org(mcp_data, "ua", org_id=org_a, title="A conv")
    _desktop_with_org(mcp_data, "ub", org_id=org_b, title="B conv")

    result = _call(organization_id=org_a)
    uuids = {s["uuid"] for s in result["sessions"]}
    assert uuids == {u_a}


def test_organization_id_composes_with_source(mcp_data):
    """org_a Desktop + CC session in same project: filtering by
    organization_id=org_a AND source=CLAUDE_AI returns Desktop only.
    """
    org_a = "11111111-1111-1111-1111-111111111111"
    u_a = _desktop_with_org(mcp_data, "ua", org_id=org_a, title="Desktop")
    mcp_data.add_cc_session("cc-1")

    result = _call(organization_id=org_a, source="CLAUDE_AI")
    uuids = {s["uuid"] for s in result["sessions"]}
    assert uuids == {u_a}


def test_organization_id_composes_with_project(mcp_data):
    """An organization_id filter for an org with one Desktop session AND a
    `project="foo"` filter (substring on project_name) → empty, because
    the Desktop session has no project_name. Confirms ANDing semantics.
    """
    org_a = "11111111-1111-1111-1111-111111111111"
    _desktop_with_org(mcp_data, "ua", org_id=org_a, title="A conv")
    # And one CC session with project "foo" — but NOT tagged with org_a.
    mcp_data.add_cc_session("cc-1", cwd="/work/foo")

    result = _call(organization_id=org_a, project="foo")
    # Intersection is empty: org_a side has no project_name, CC side has
    # no org_a tag.
    assert result["sessions"] == []


def test_organization_id_with_query_intersects(mcp_data):
    """A search query + an organization_id filter intersect: only matches
    in the named org are returned.
    """
    org_a = "11111111-1111-1111-1111-111111111111"
    org_b = "22222222-2222-2222-2222-222222222222"
    needle_text = "NEEDLE_FOR_ORG_TEST"
    u_a = _desktop_with_org(
        mcp_data,
        "ua",
        org_id=org_a,
        title="A conv with " + needle_text,
    )
    _desktop_with_org(
        mcp_data,
        "ub",
        org_id=org_b,
        title="B conv with " + needle_text,
    )

    result = _call(query=needle_text, organization_id=org_a)
    uuids = {s["uuid"] for s in result["sessions"]}
    # Only the org_a conversation surfaces.
    assert uuids == {u_a}


def test_organization_id_unknown_returns_empty(mcp_data):
    """Unknown org_id → empty (consistent with /api/search behavior)."""
    org_a = "11111111-1111-1111-1111-111111111111"
    _desktop_with_org(mcp_data, "ua", org_id=org_a, title="Some conv")

    result = _call(organization_id="00000000-dead-beef-0000-000000000000")
    assert result["sessions"] == []
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# Invariant I2 — UI/MCP common-subset parity
# ---------------------------------------------------------------------------


def test_mcp_source_filter_matches_api_search_source_filter(mcp_data):
    """`list_sessions(query=Q, source=S)` returns the same set as
    `/api/search?q=Q&source=S` (modulo response shape).

    This pins the structural invariant that MCP and UI share the same
    `search_conversations()` internals for the common-subset of filters.
    """
    from fastapi.testclient import TestClient
    from backend.main import app

    # Plant one Desktop + one CC with the needle in both.
    needle = "NEEDLE_PARITY_TEST"
    u_a = mcp_data.add_desktop_session(
        "ua",
        name=f"A with {needle}",
        messages=[
            {
                "uuid": "h-1",
                "sender": "human",
                "text": needle,
                "content": [{"type": "text", "text": needle}],
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-01T10:00:00Z",
                "parent_message_uuid": None,
            },
        ],
    )
    cc1 = mcp_data.add_cc_session("cc-1", user_text=needle)

    mcp_result = _call(query=needle, source="CLAUDE_AI")
    mcp_uuids = sorted(s["uuid"] for s in mcp_result["sessions"])

    client = TestClient(app)
    api_result = client.get(
        "/api/search", params={"q": needle, "source": "CLAUDE_AI"}
    )
    assert api_result.status_code == 200
    api_uuids = sorted(item["conversation_uuid"] for item in api_result.json()["results"])

    assert mcp_uuids == api_uuids == [u_a]

    # And confirm CC parity in the other direction.
    mcp_cc = _call(query=needle, source="CLAUDE_CODE")
    assert sorted(s["uuid"] for s in mcp_cc["sessions"]) == [cc1]
