"""Paired-endpoint contract tests (Hunt #15).

Each test pins an invariant BETWEEN two endpoints — the failure class
where each endpoint passes its own unit tests but the seam silently
breaks. The canonical example was the LIST↔DETAIL gap fixed in
``backend/store.py:_find_conversation_data`` (commit e208441), pinned
by ``test_cc_detail_filename_session_mismatch.py``.

This file extends the coverage to:

  * **SEARCH ↔ DETAIL** — every ``conversation_uuid`` returned by
    ``GET /api/search`` must resolve via ``GET
    /api/conversations/{uuid}``. The user-visible failure mode was
    exactly this: search panel shows a hit, click navigates to the
    conversation, detail returns 404, UI renders "Conversation not
    found". The fix shipped (e208441) addresses
    ``_find_conversation_data``; this test pins the contract so a
    future regression in either endpoint surfaces immediately.
  * **LIST ↔ TREE** — every uuid in ``GET /api/conversations`` must
    have a working ``GET /api/conversations/{uuid}/tree`` endpoint.
    The tree view (rendered for branched conversations) is reached
    via the sidebar list; a 404 here would silently break branch UI.
  * **LIST ↔ EXPORT/markdown** — every uuid in the list must be
    exportable. The export buttons live on the detail pane reached
    from the sidebar list; a 404 here would break the user's
    save/share flow.

Each test seeds a multi-conversation corpus to avoid trivial-pass
(1-item) failures. Bidirectional pair: each contract test is
accompanied by a "uuid that's NOT in the list / search results /
bookmark set must 404" check so the contract test couldn't pass
against an implementation that returns 200 for everything.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import config as cfg
from backend.cache import _conversation_cache
from backend.main import app


def _conv(
    uuid: str,
    name: str,
    *,
    summary: str = "",
    project_path: str | None = None,
    body: str = "needle_alpha body text",
):
    """Build a Claude Desktop conversation JSON with a single message."""
    return {
        "uuid": uuid,
        "name": name,
        "summary": summary,
        "model": "claude-sonnet-4-6",
        "created_at": "2026-05-01T12:00:00Z",
        "updated_at": "2026-05-01T13:00:00Z",
        "is_starred": False,
        "source": "CLAUDE_AI",
        "project_path": project_path,
        "current_leaf_message_uuid": "msg-1",
        "chat_messages": [
            {
                "uuid": "msg-1",
                "parent_message_uuid": None,
                "sender": "human",
                "text": body,
                "created_at": "2026-05-01T12:00:00Z",
                "updated_at": "2026-05-01T12:00:00Z",
                "content": [{"type": "text", "text": body}],
            },
        ],
    }


@pytest.fixture
def contracts_data_dir(tmp_path, monkeypatch):
    """Seed 3 conversations with a shared substring (``contractprobe``)
    so search returns multiple hits. Each has a distinct uuid; all
    three must be reachable via every paired endpoint.
    """
    convs = [
        _conv("contract-a", "Alpha contract", body="contractprobe in alpha"),
        _conv("contract-b", "Beta contract",  body="contractprobe in beta"),
        _conv("contract-c", "Gamma contract", body="contractprobe in gamma"),
    ]
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True)
    for c in convs:
        (by_org / f"{c['uuid']}.json").write_text(json.dumps(c))
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_DIR", str(empty_claude))
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    _conversation_cache.clear()
    yield tmp_path
    _conversation_cache.clear()
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SEARCH ↔ DETAIL  (user-visible regression class)
# ---------------------------------------------------------------------------


def test_search_result_uuids_resolve_in_detail(contracts_data_dir):
    """Every ``conversation_uuid`` returned by ``GET /api/search`` MUST
    return 200 from ``GET /api/conversations/{uuid}``. Otherwise the
    search panel shows hits that 404 on click — exactly the bug the
    user reported on 2026-05-18.

    Seeds 3 conversations all matching the query so the test runs >1
    detail probe; a trivial impl that hardcodes one match would fail
    the second probe.
    """
    client = TestClient(app)
    r = client.get("/api/search", params={"q": "contractprobe"})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    uuids = list({m["conversation_uuid"] for m in results})
    assert len(uuids) >= 2, (
        f"fixture should yield >=2 search hits; got {uuids}. "
        f"Without >1 hit, this test could pass against a trivial impl."
    )

    for u in uuids:
        detail = client.get(f"/api/conversations/{u}")
        assert detail.status_code == 200, (
            f"SEARCH advertised uuid={u} but DETAIL returned "
            f"{detail.status_code}. Click-through from search panel "
            f"would render 'Conversation not found'."
        )
        body = detail.json()
        assert body["uuid"] == u


def test_search_negative_pair_unknown_uuid_still_404s(contracts_data_dir):
    """Bidirectional pair: a uuid that's NOT in the corpus must 404 on
    detail. Without this, the contract test could trivially pass
    against an impl that returns 200 for everything.
    """
    client = TestClient(app)
    r = client.get("/api/conversations/this-uuid-not-in-corpus-zzzz")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# LIST ↔ TREE
# ---------------------------------------------------------------------------


def test_list_uuids_have_working_tree_endpoint(contracts_data_dir):
    """Every uuid in ``GET /api/conversations`` must have a working
    ``GET /api/conversations/{uuid}/tree`` endpoint. The branch-tree
    view is reached via the sidebar list; a 404 here would silently
    break branch UI on click.
    """
    client = TestClient(app)
    list_resp = client.get("/api/conversations")
    assert list_resp.status_code == 200
    uuids = [c["uuid"] for c in list_resp.json()]
    assert len(uuids) >= 2

    for u in uuids:
        tree = client.get(f"/api/conversations/{u}/tree")
        assert tree.status_code == 200, (
            f"LIST advertised uuid={u} but TREE returned "
            f"{tree.status_code}. Branch-view click would fail."
        )


def test_tree_negative_pair_unknown_uuid_404s(contracts_data_dir):
    """Bidirectional pair for the tree endpoint."""
    client = TestClient(app)
    r = client.get("/api/conversations/this-uuid-not-in-corpus-zzzz/tree")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# LIST ↔ EXPORT/markdown
# ---------------------------------------------------------------------------


def test_list_uuids_are_markdown_exportable(contracts_data_dir):
    """Every uuid in the list must be exportable as markdown. The
    export buttons live on the detail pane reached from the sidebar
    list; a 404 here would break the user's save/share flow.

    We test markdown specifically because (a) it's the lightest of
    the three export formats (no WeasyPrint dependency), (b) the
    markdown bundle and PDF endpoints reuse the same underlying
    ``store.get_conversation`` call, so a markdown 200 implies the
    detail-load contract holds for the other two too.
    """
    client = TestClient(app)
    list_resp = client.get("/api/conversations")
    uuids = [c["uuid"] for c in list_resp.json()]
    assert len(uuids) >= 2

    for u in uuids:
        export = client.get(f"/api/conversations/{u}/export/markdown")
        assert export.status_code == 200, (
            f"LIST advertised uuid={u} but EXPORT/markdown returned "
            f"{export.status_code}. Save/share button would fail."
        )
        # Sanity: markdown export must not be empty for a real conv.
        assert len(export.content) > 0


def test_export_negative_pair_unknown_uuid_404s(contracts_data_dir):
    """Bidirectional pair for export."""
    client = TestClient(app)
    r = client.get(
        "/api/conversations/this-uuid-not-in-corpus-zzzz/export/markdown"
    )
    assert r.status_code == 404
