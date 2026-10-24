"""Cowork sessions that exist in more than one org or deployment directory,
and the workspace of remote Cowork tasks.

Earned 2026-10-21 (review of the remote Cowork task archive and the
Cowork list cache):

1. ``find_cowork_session_dir`` returned the first directory that held the
   session, and the detail path read only that one. When the first copy
   had an empty or user-less ``audit.jsonl``, the list showed the
   conversation (built from the good copy) but opening it returned 404.
2. Remote tasks had ``organization_id = None`` although their sidecar
   records ``organizationId``, so a workspace filter hid every remote task.
3. The list did not deduplicate Cowork sessions by uuid. One remote task
   written under two org directories gave two sidebar rows with one uuid.

Directory iteration order is not defined, so each duplicate test runs with
the good (or newer) copy in each of the two directories.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend import store as store_mod
from backend.cowork_reader import list_cowork_conversations
from backend.store import ConversationStore
from backend.tests.test_store_cowork_integration import (
    HAPPY_SESSION_DIR,
    HAPPY_SIDECAR,
)
from fetcher.remote_sessions import _build_sidecar

LOCAL_UUID = "dup00000-2222-3333-4444-555566667777"
REMOTE_ID = "cse_01DuplicateTask"
ORG_A = "aaaaaaaa-0000-0000-0000-000000000001"
ORG_B = "bbbbbbbb-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def _fresh_cache():
    store_mod.clear_cowork_summary_cache()
    yield
    store_mod.clear_cowork_summary_cache()


def _happy_lines() -> list[str]:
    return (HAPPY_SESSION_DIR / "audit.jsonl").read_text(encoding="utf-8").splitlines()


def _write_local(cowork_root: Path, deployment: str, audit_text: str) -> Path:
    org_dir = cowork_root / deployment / "o_org"
    session_dir = org_dir / f"local_{LOCAL_UUID}"
    session_dir.mkdir(parents=True)
    (session_dir / "audit.jsonl").write_text(audit_text, encoding="utf-8")
    sidecar = json.loads(HAPPY_SIDECAR.read_text(encoding="utf-8"))
    sidecar["sessionId"] = f"local_{LOCAL_UUID}"
    sidecar["title"] = f"Copy in {deployment}"
    (org_dir / f"local_{LOCAL_UUID}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return session_dir


def _bad_audit(kind: str) -> str:
    if kind == "empty":
        return ""
    # "no_user": every line except the user turns, so the reader returns None.
    lines = [line for line in _happy_lines() if json.loads(line).get("type") != "user"]
    assert lines, "the fixture must have non-user lines"
    return "\n".join(lines) + "\n"


def _write_remote(data_dir: Path, org: str, last_event_at: str, title: str) -> Path:
    """A remote task in the shape that ``fetcher.remote_sessions`` writes."""
    org_dir = data_dir / "cowork-remote" / org
    session_dir = org_dir / f"remote_{REMOTE_ID}"
    session_dir.mkdir(parents=True)
    shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", session_dir / "audit.jsonl")
    session = {
        "id": REMOTE_ID,
        "title": title,
        "status": "active",
        "created_at": "2026-10-01T10:00:00Z",
        "updated_at": last_event_at,
        "last_event_at": last_event_at,
        "config": {"model": "claude-test"},
    }
    (org_dir / f"remote_{REMOTE_ID}.json").write_text(
        json.dumps(_build_sidecar(org, session)), encoding="utf-8"
    )
    return session_dir


def _store(tmp_path: Path, cowork_root: Path | None = None) -> ConversationStore:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return ConversationStore(
        data_dir=data_dir,
        claude_dir=tmp_path / "claude",
        cowork_root=cowork_root or tmp_path / "no-local-cowork",
    )


# ----- 1. Detail reads the good copy of a duplicated session ---------------


@pytest.mark.parametrize("bad_kind", ["empty", "no_user"])
@pytest.mark.parametrize("good_deployment", ["d_first", "d_second"])
def test_detail_opens_a_session_whose_other_copy_is_unreadable(
    tmp_path: Path, bad_kind: str, good_deployment: str
) -> None:
    cowork_root = tmp_path / "local-agent-mode-sessions"
    for deployment in ("d_first", "d_second"):
        text = (
            "\n".join(_happy_lines()) + "\n"
            if deployment == good_deployment
            else _bad_audit(bad_kind)
        )
        _write_local(cowork_root, deployment, text)
    store = _store(tmp_path, cowork_root)

    rows = [c for c in store.list_conversations(source="CLAUDE_COWORK") if c.uuid == LOCAL_UUID]
    assert len(rows) == 1, "the list must show the session once"

    detail = store.get_conversation(LOCAL_UUID)
    assert detail is not None, "the list shows the session, so opening it must not 404"
    assert detail.uuid == LOCAL_UUID
    assert detail.name == f"Copy in {good_deployment}"
    assert detail.messages

    as_dict = store.get_conversation_dict(LOCAL_UUID)
    assert as_dict is not None and as_dict["uuid"] == LOCAL_UUID


# ----- 2. Remote tasks belong to their workspace ---------------------------


def test_workspace_filter_includes_remote_tasks_of_that_workspace_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_remote(store.data_dir, ORG_A, "2026-10-05T10:00:00Z", "Task in A")

    def uuids(org: str | None) -> list[str]:
        return [
            c.uuid
            for c in store.list_conversations(source="CLAUDE_COWORK", organization_id=org)
        ]

    assert uuids(None) == [REMOTE_ID]
    assert uuids(ORG_A) == [REMOTE_ID]
    assert uuids(ORG_B) == []

    (row,) = store.list_conversations(source="CLAUDE_COWORK")
    assert row.organization_id == ORG_A


def test_local_cowork_sessions_stay_without_a_workspace(tmp_path: Path) -> None:
    cowork_root = tmp_path / "local-agent-mode-sessions"
    _write_local(cowork_root, "d_first", "\n".join(_happy_lines()) + "\n")
    store = _store(tmp_path, cowork_root)

    (row,) = store.list_conversations(source="CLAUDE_COWORK")
    assert row.organization_id is None
    assert store.list_conversations(source="CLAUDE_COWORK", organization_id=ORG_A) == []


def test_search_index_repairs_remote_tasks_indexed_without_a_workspace(tmp_path: Path) -> None:
    """An index built before this fix holds remote tasks with an empty
    workspace. The audit files do not change, so the drift pass alone never
    re-reads them; the index must re-read them once on open."""
    from backend.search_index import SearchIndex, build_full_index, fts5_available

    if not fts5_available():
        pytest.skip("sqlite3 without FTS5")

    cowork_root = tmp_path / "local-agent-mode-sessions"
    local_dir = _write_local(cowork_root, "d_first", "\n".join(_happy_lines()) + "\n")
    store = _store(tmp_path, cowork_root)
    remote_dir = _write_remote(store.data_dir, ORG_A, "2026-10-05T10:00:00Z", "Task in A")
    index_path = tmp_path / "index" / "search-index.sqlite"

    # Seed the legacy shape: what the old reader wrote for both sessions.
    from backend.cowork_reader import read_cowork_conversation

    index = SearchIndex(index_path)
    try:
        for session_dir in (remote_dir, local_dir):
            conv = read_cowork_conversation(session_dir)
            conv["organization_id"] = None
            audit = session_dir / "audit.jsonl"
            index.upsert_conversation(conv, audit, audit.stat().st_mtime)
    finally:
        index.close()

    index = SearchIndex(index_path)
    try:
        indexed = set(index._read_indexed_files_map())
        assert str(local_dir / "audit.jsonl") in indexed, "local Cowork rows are correct as they are"
        build_full_index(store, index=index)
    finally:
        index.close()

    with closing(sqlite3.connect(index_path)) as conn, conn:
        orgs = dict(conn.execute("SELECT conv_uuid, organization_id FROM conversations"))
        msg_orgs = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT organization_id FROM messages WHERE conv_uuid = ?",
                (REMOTE_ID,),
            )
        }
    assert orgs[REMOTE_ID] == ORG_A
    assert msg_orgs == {ORG_A}
    assert not orgs[LOCAL_UUID]


# ----- 3. One row per uuid, the newest copy wins ---------------------------


@pytest.mark.parametrize("newer_org", [ORG_A, ORG_B])
def test_full_content_list_keeps_one_copy_of_a_duplicated_remote_task(
    tmp_path: Path, newer_org: str
) -> None:
    data_dir = tmp_path / "data"
    older_org = ORG_B if newer_org == ORG_A else ORG_A
    _write_remote(data_dir, older_org, "2026-10-02T10:00:00Z", "Older copy")
    _write_remote(data_dir, newer_org, "2026-10-05T10:00:00Z", "Newer copy")

    convs = list_cowork_conversations(tmp_path / "no-local-cowork", data_dir / "cowork-remote")

    assert [c["uuid"] for c in convs] == [REMOTE_ID]
    assert convs[0]["name"] == "Newer copy"
    assert convs[0]["organization_id"] == newer_org


@pytest.mark.parametrize("newer_org", [ORG_A, ORG_B])
def test_sidebar_list_keeps_one_copy_of_a_duplicated_remote_task(
    tmp_path: Path, newer_org: str
) -> None:
    store = _store(tmp_path)
    older_org = ORG_B if newer_org == ORG_A else ORG_A
    _write_remote(store.data_dir, older_org, "2026-10-02T10:00:00Z", "Older copy")
    _write_remote(store.data_dir, newer_org, "2026-10-05T10:00:00Z", "Newer copy")

    for attempt in ("cold", "from the cache"):
        rows = store.list_conversations(source="CLAUDE_COWORK")
        assert [r.uuid for r in rows] == [REMOTE_ID], attempt
        assert rows[0].name == "Newer copy", attempt
        assert rows[0].organization_id == newer_org, attempt

    # Search and export read the full sessions; they see one copy too.
    raw = store.get_all_conversations_raw(source="CLAUDE_COWORK")
    assert [c["uuid"] for c in raw] == [REMOTE_ID]
    assert raw[0]["name"] == "Newer copy"

    # Opening the row shows the copy that the list shows.
    detail = store.get_conversation(REMOTE_ID)
    assert detail is not None and detail.name == "Newer copy"


@pytest.mark.parametrize("newer_deployment", ["d_first", "d_second"])
def test_sidebar_list_keeps_one_copy_of_a_duplicated_local_session(
    tmp_path: Path, newer_deployment: str
) -> None:
    cowork_root = tmp_path / "local-agent-mode-sessions"
    text = "\n".join(_happy_lines()) + "\n"
    for deployment in ("d_first", "d_second"):
        session_dir = _write_local(cowork_root, deployment, text)
        sidecar_path = session_dir.with_suffix(".json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        # 2025-10-05 vs 2025-10-02 10:00 UTC, as epoch ms.
        sidecar["lastActivityAt"] = (
            1_759_658_400_000 if deployment == newer_deployment else 1_759_399_200_000
        )
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    store = _store(tmp_path, cowork_root)

    rows = store.list_conversations(source="CLAUDE_COWORK")
    assert [r.uuid for r in rows] == [LOCAL_UUID]
    assert rows[0].name == f"Copy in {newer_deployment}"

    detail = store.get_conversation(LOCAL_UUID)
    assert detail is not None and detail.name == f"Copy in {newer_deployment}"


def test_a_remote_task_carries_its_workspace_name(tmp_path: Path) -> None:
    """2026-10-23: the list groups by organization_name. Remote tasks had
    none, so they showed as "Workspace (aaaaaaaa)"."""
    from backend.cowork_reader import read_cowork_conversation

    data_dir = tmp_path / "data"
    remote_dir = _write_remote(data_dir, ORG_A, "2026-10-05T10:00:00Z", "Task in A")
    sidecar_path = remote_dir.parent / f"{remote_dir.name}.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["organizationName"] = "Acme Research"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    conv = read_cowork_conversation(remote_dir)
    assert conv["organization_name"] == "Acme Research"

    cowork_root = tmp_path / "local-agent-mode-sessions"
    cowork_root.mkdir()
    (row,) = _store(tmp_path, cowork_root).list_conversations(source="CLAUDE_COWORK")
    assert row.organization_name == "Acme Research"
