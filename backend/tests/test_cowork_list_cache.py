"""The conversation list caches each Cowork session's summary.

Earned 2026-10-19 (PR #6 review, then measured with
``benchmarks/bench_cowork_list.py``): every ``/api/conversations`` call read
and parsed every Cowork session's whole ``audit.jsonl``, on the event loop.
On a real corpus (79 sessions, 67 MB) that was 231 ms of every list call,
cold or warm. Cloud Cowork tasks archived by Refresh add to the same cost.

Contract:
* A second list call reads no unchanged session.
* A change to a session's ``audit.jsonl`` or sidecar is picked up on the
  next list call; a deleted session disappears.
* Each cached row equals the row built from a full read.
* Search and export (``full_content=True``) still get the full messages.

The tests count session reads instead of timing them: a count does not
depend on how busy the machine is.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend import cowork_reader
from backend import store as store_mod
from backend.store import ConversationStore
from backend.tests.test_store_cowork_integration import _make_cowork_corpus


@pytest.fixture(autouse=True)
def _fresh_cache():
    store_mod.clear_cowork_summary_cache()
    yield
    store_mod.clear_cowork_summary_cache()


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every session that the reader parses."""
    seen: list[Path] = []
    real = cowork_reader.read_cowork_conversation

    def counting(session_dir: Path):
        seen.append(session_dir)
        return real(session_dir)

    monkeypatch.setattr(cowork_reader, "read_cowork_conversation", counting)
    return seen


def _store(tmp_path: Path, count: int = 3) -> tuple[ConversationStore, Path]:
    cowork_root = _make_cowork_corpus(tmp_path, count=count)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return ConversationStore(data_dir=data_dir, claude_dir=tmp_path / "claude", cowork_root=cowork_root), cowork_root


def _rows(store: ConversationStore) -> dict[str, dict]:
    return {c.uuid: c.model_dump() for c in store.list_conversations(source="CLAUDE_COWORK")}


def _bump(path: Path) -> None:
    """Move the modification time forward, so a same-size edit is seen too."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_a_second_list_reads_no_unchanged_session(tmp_path: Path, reads: list[Path]) -> None:
    store, _ = _store(tmp_path, count=3)

    first = _rows(store)
    assert len(first) == 3 and len(reads) == 3

    reads.clear()
    second = _rows(store)
    assert reads == [], "an unchanged session was parsed again"
    assert second == first


def test_a_changed_audit_file_is_read_again(tmp_path: Path, reads: list[Path]) -> None:
    store, root = _store(tmp_path, count=2)
    before = _rows(store)
    audit = next(root.rglob("audit.jsonl"))
    lines = audit.read_text(encoding="utf-8").splitlines()
    user_line = next(line for line in lines if json.loads(line).get("type") == "user")
    extra = json.loads(user_line)
    extra["uuid"] = "added-later"
    with audit.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(extra) + "\n")
    _bump(audit)

    reads.clear()
    after = _rows(store)

    assert reads == [audit.parent], "only the changed session may be parsed again"
    uuid = next(u for u in after if after[u] != before[u])
    assert after[uuid]["message_count"] > before[uuid]["message_count"]


def test_a_changed_sidecar_is_read_again(tmp_path: Path, reads: list[Path]) -> None:
    store, root = _store(tmp_path, count=2)
    _rows(store)
    sidecar = next(root.rglob("local_*.json"))
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["title"] = "Renamed in Claude Desktop"
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    _bump(sidecar)

    reads.clear()
    names = {row["name"] for row in _rows(store).values()}

    assert len(reads) == 1
    assert "Renamed in Claude Desktop" in names


def test_a_deleted_session_disappears(tmp_path: Path) -> None:
    import shutil

    store, root = _store(tmp_path, count=2)
    assert len(_rows(store)) == 2
    session_dir = next(root.rglob("audit.jsonl")).parent
    shutil.rmtree(session_dir)

    assert len(_rows(store)) == 1


def test_cached_rows_equal_rows_from_a_full_read(tmp_path: Path) -> None:
    """The summary that the cache keeps must give the same row as the full
    conversation: counts, branch flag, archived flag, names, dates."""
    store, root = _store(tmp_path, count=3)
    cached = _rows(store)
    cached_again = _rows(store)  # from the cache this time

    full = {}
    for session_dir in cowork_reader.iter_cowork_session_dirs(root, None):
        data = cowork_reader.read_cowork_conversation(session_dir)
        row = store._make_summary(data).model_dump()
        full[row["uuid"]] = row

    assert cached == full
    assert cached_again == full


def test_search_and_export_still_get_full_messages(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, count=2)
    _rows(store)  # fill the cache first

    raw = [c for c in store.get_all_conversations_raw(source="CLAUDE_COWORK")]

    assert len(raw) == 2
    assert all(c.get("chat_messages") for c in raw), "full_content must keep the messages"
