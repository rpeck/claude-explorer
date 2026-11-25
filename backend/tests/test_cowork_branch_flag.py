"""L5: a Cowork list row never reports ``has_branches: true``.

A Cowork session is a chronological log with no branch tree, but the list
computed the flag as for Desktop. Cowork messages have no parent links, so
every row with 2 or more messages read true (N1 council, 2026-11-19).
See PLANS/2026.11.18-codex-source.md §6.1, row L5.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

LIST_GOLDEN = Path(__file__).resolve().parent / "goldens" / "api" / "list.json"

COWORK_UUIDS = {
    "aaaa1111-2222-3333-4444-555566667777",  # local
    "session_01FIXTUREREMOTE000000000001",  # remote
}

#: The has_branches value of each non-Cowork row in the N0 golden. L5 must
#: not change them.
NON_COWORK_FLAGS = {
    "0f415a45-9c62-8671-d4ad-53b84acb7e1a": False,
    "c8da94ab-641d-bf28-d4a6-cba9bc72468e": False,
    "a3c33811-ab27-c195-6938-54121b8673e4": False,
    "435e56ed-50ba-21c0-3495-0e8749b20543": False,
    # Issue #90: the real Code Mode recordings.
    "2339aab4-744c-4556-95b8-5ee5552c1b85": False,
    "b007d327-80c4-46ea-b737-53d84b30c624": False,
}

NO_PARENTS_ONE = [{"uuid": "m1"}]
NO_PARENTS_TWO = [{"uuid": "m1"}, {"uuid": "m2"}]
NO_PARENTS_FIVE = [{"uuid": f"m{i}"} for i in range(5)]
#: Two children of one parent: has_branches() is True.
BRANCHED = [
    {"uuid": "m1", "parent_message_uuid": None},
    {"uuid": "m2", "parent_message_uuid": "m1"},
    {"uuid": "m3", "parent_message_uuid": "m1"},
]
LINEAR = [
    {"uuid": "m1", "parent_message_uuid": None},
    {"uuid": "m2", "parent_message_uuid": "m1"},
]


@pytest.fixture(autouse=True)
def _fresh_registry():
    from backend.sources import registry

    registry.reset_for_tests()
    yield
    registry.reset_for_tests()


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    from backend.tests.goldens.corpus import build_corpus

    return build_corpus(tmp_path, monkeypatch)


@pytest.fixture
def store(corpus):
    from backend.store import ConversationStore

    return ConversationStore()


@pytest.fixture
def api(corpus) -> TestClient:
    from backend.main import app

    # No ``with``: the lifespan (watcher, index build) stays off.
    return TestClient(app)


def _list(api: TestClient, **params) -> list[dict]:
    r = api.get("/api/conversations", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --- the adapter rule --------------------------------------------------------------


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param(NO_PARENTS_ONE, id="no-parents-1"),
        pytest.param(NO_PARENTS_TWO, id="no-parents-2"),
        pytest.param(NO_PARENTS_FIVE, id="no-parents-5"),
        pytest.param(LINEAR, id="linear"),
        pytest.param(BRANCHED, id="branched"),
        pytest.param([], id="empty"),
    ],
)
def test_cowork_branch_flag_is_always_false(store, messages):
    from backend.sources.registry import get_adapter

    data = {"uuid": "x", "source": "CLAUDE_COWORK", "chat_messages": messages}
    assert get_adapter("CLAUDE_COWORK", store).branch_flag(data, messages) is False


def test_cowork_branch_flag_ignores_a_stored_true_flag(store):
    """A summary row has no messages; its cached ``has_branches`` must not leak."""
    from backend.sources.registry import get_adapter

    data = {"uuid": "x", "source": "CLAUDE_COWORK", "has_branches": True, "message_count": 4}
    assert get_adapter("CLAUDE_COWORK", store).branch_flag(data, []) is False


def test_desktop_branch_flag_still_detects_a_branch(store):
    from backend.sources.registry import get_adapter

    data = {"uuid": "x", "source": "CLAUDE_AI", "chat_messages": BRANCHED}
    assert get_adapter("CLAUDE_AI", store).branch_flag(data, BRANCHED) is True


def test_desktop_branch_flag_still_reads_a_stored_flag_without_messages(store):
    from backend.sources.registry import get_adapter

    data = {"uuid": "x", "source": "CLAUDE_AI", "has_branches": True}
    assert get_adapter("CLAUDE_AI", store).branch_flag(data, []) is True


# --- the store list ----------------------------------------------------------------


def test_store_list_reports_no_branches_for_any_cowork_row(store):
    rows = store.list_conversations(source="CLAUDE_COWORK", show_archived=True)
    assert {r.uuid for r in rows} == COWORK_UUIDS
    assert {r.uuid: r.has_branches for r in rows} == dict.fromkeys(COWORK_UUIDS, False)


def test_store_summary_of_a_full_cowork_row_has_no_branches(store):
    """The full-content rows (search, export) carry the messages themselves."""
    rows = store._get_all_conversations_data("CLAUDE_COWORK", full_content=True)
    assert {r["uuid"] for r in rows} == COWORK_UUIDS
    for data in rows:
        assert len(data["chat_messages"]) >= 2, data["uuid"]
        assert store._make_summary(data).has_branches is False, data["uuid"]


# --- the web API -------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [{"show_archived": "true"}, {"source": "CLAUDE_COWORK", "show_archived": "true"}, {}],
    ids=["all-sources", "cowork-only", "default"],
)
def test_api_list_reports_no_branches_for_any_cowork_row(api, params):
    rows = [r for r in _list(api, **params) if r["source"] == "CLAUDE_COWORK"]
    assert rows, "the corpus must list a Cowork row"
    assert {r["uuid"]: r["has_branches"] for r in rows} == {r["uuid"]: False for r in rows}


def test_api_list_keeps_the_flag_of_every_other_source(api):
    rows = [r for r in _list(api, show_archived="true") if r["source"] != "CLAUDE_COWORK"]
    assert {r["uuid"]: r["has_branches"] for r in rows} == NON_COWORK_FLAGS


def test_api_list_still_flags_a_branched_desktop_conversation(corpus, api):
    org = "00000000-1111-2222-3333-444444444444"
    uuid = "bbbb2222-3333-4444-5555-666677778888"
    messages = [
        {
            "uuid": m["uuid"],
            "parent_message_uuid": m["parent_message_uuid"],
            "sender": "human" if m["uuid"] == "m1" else "assistant",
            "text": f"L5 fixture {m['uuid']}",
            "content": [{"type": "text", "text": f"L5 fixture {m['uuid']}"}],
            "created_at": "2026-04-02T10:00:00Z",
            "updated_at": "2026-04-02T10:00:00Z",
            "attachments": [],
            "files": [],
            "files_v2": [],
        }
        for m in BRANCHED
    ]
    conv = {
        "uuid": uuid,
        "name": "L5 fixture: real branch",
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-04-02T10:00:00Z",
        "updated_at": "2026-04-02T10:00:00Z",
        "is_starred": False,
        "organization_id": org,
        "current_leaf_message_uuid": "m3",
        "chat_messages": messages,
    }
    (corpus.data_dir / "by-org" / org / f"{uuid}.json").write_text(json.dumps(conv), encoding="utf-8")
    rows = {r["uuid"]: r for r in _list(api, source="CLAUDE_AI")}
    assert rows[uuid]["has_branches"] is True


# --- the golden --------------------------------------------------------------------


def _golden_rows() -> list[tuple[str, dict]]:
    golden = json.loads(LIST_GOLDEN.read_text(encoding="utf-8"))
    return [(key, row) for key, rows in golden.items() for row in rows]


def test_list_golden_records_no_branches_for_any_cowork_row():
    cowork = [(key, row) for key, row in _golden_rows() if row["source"] == "CLAUDE_COWORK"]
    assert {row["uuid"] for _, row in cowork} == COWORK_UUIDS
    flagged = [(key, row["uuid"]) for key, row in cowork if row["has_branches"] is not False]
    assert flagged == []


def test_list_golden_keeps_the_flag_of_every_other_row():
    other = [(key, row) for key, row in _golden_rows() if row["source"] != "CLAUDE_COWORK"]
    changed = [
        (key, row["uuid"], row["has_branches"])
        for key, row in other
        if row["has_branches"] is not NON_COWORK_FLAGS[row["uuid"]]
    ]
    assert changed == []
