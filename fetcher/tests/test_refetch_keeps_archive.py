"""A re-fetch keeps the content that the archive holds (issue #51).

The archive keeps every API session, also after Claude removes content from
it (AGENTS.md, guiding principle 2). Before this fix a re-fetch replaced the
local copy with what the server returns now, so content that the server no
longer returned was lost:

* a Desktop conversation: ``save_conversation`` wrote the server JSON over
  the local file, on every incremental re-fetch and on Full Refresh;
* a remote Cowork task: Full Refresh rewrote ``audit.jsonl`` from the events
  that the API returns now;
* a crash during a Desktop write left a truncated file.

The fix merges and never replaces. Some tests here also pass on the old code:
they pin behavior that the merge must not break.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from fetcher.tests.ccr_fake import (
    FakeSession,
    FakeSessionsApi,
    assistant_event,
    iso,
    user_event,
)

ORG = "11111111-1111-4111-8111-111111111111"
CONV = "c0ffee00-0000-4000-8000-000000000051"


def _ts(minute: int) -> str:
    return f"2026-10-01T10:{minute:02d}:00Z"


def _msg(uuid: str, minute: int, text: str, sender: str = "human", parent: str | None = None) -> dict:
    return {
        "uuid": uuid,
        "sender": sender,
        "text": text,
        "content": [{"type": "text", "text": text}],
        "created_at": _ts(minute),
        "updated_at": _ts(minute),
        "parent_message_uuid": parent,
        "attachments": [],
        "files": [],
        "files_v2": [],
        "truncated": False,
    }


def _conv(messages: list[dict], *, name: str = "NAS backup plan", updated: int = 10) -> dict:
    return {
        "uuid": CONV,
        "name": name,
        "summary": f"summary of {name}",
        "model": "claude-sonnet-4-6",
        "created_at": _ts(0),
        "updated_at": _ts(updated),
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else None,
        "is_starred": False,
        "chat_messages": messages,
    }


def _fetcher(out: Path, incremental: bool = True):
    from fetcher.bulk_fetch import ClaudeFetcher

    return ClaudeFetcher(
        session_key="sk-test-fake-key",
        orgs=[{"uuid": ORG, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True}],
        primary_org_id=ORG,
        output_dir=out,
        files_dir=out.parent / "files",
        download_files=False,
        incremental=incremental,
        delay=0.0,
    )


def _save(out: Path, conv: dict) -> None:
    # save_conversation changes its argument; keep the caller's copy clean.
    _fetcher(out).save_conversation(copy.deepcopy(conv))


def _path(out: Path) -> Path:
    return out / "by-org" / ORG / f"{CONV}.json"


def _on_disk(out: Path) -> dict:
    return json.loads(_path(out).read_text(encoding="utf-8"))


def _uuids(out: Path) -> list[str]:
    return [m["uuid"] for m in _on_disk(out)["chat_messages"]]


def _files(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


# The local copy: four messages. The server later drops the first two.
M1 = _msg("m1", 1, "first question about the RAID layout")
M2 = _msg("m2", 2, "first answer: use RAID 6", "assistant", "m1")
M3 = _msg("m3", 3, "second question about snapshots", parent="m2")
M4 = _msg("m4", 4, "second answer: daily snapshots", "assistant", "m3")
M5 = _msg("m5", 5, "third question about offsite copies", parent="m4")
M6 = _msg("m6", 6, "third answer: use a second NAS", "assistant", "m5")


# ------------------------------------------------------------ Desktop merge


def test_a_message_only_in_the_local_copy_is_kept(tmp_path: Path) -> None:
    _save(tmp_path, _conv([M1, M2, M3, M4], updated=4))

    _save(tmp_path, _conv([M3, M4, M5, M6], updated=6))

    assert set(_uuids(tmp_path)) == {"m1", "m2", "m3", "m4", "m5", "m6"}


def test_a_kept_local_message_keeps_its_fields_unchanged(tmp_path: Path) -> None:
    local_m1 = {**M1, "a_field_the_server_dropped": {"kept": True}}
    _save(tmp_path, _conv([local_m1, M2, M3, M4], updated=4))
    before = {m["uuid"]: m for m in _on_disk(tmp_path)["chat_messages"]}

    _save(tmp_path, _conv([M3, M4, M5, M6], updated=6))

    after = {m["uuid"]: m for m in _on_disk(tmp_path)["chat_messages"]}
    assert after.get("m1") == before["m1"]
    assert after.get("m2") == before["m2"]


def test_for_a_message_in_both_copies_the_server_copy_wins(tmp_path: Path) -> None:
    _save(tmp_path, _conv([M1, M2, M3, M4], updated=4))
    edited_m4 = {**M4, "text": "second answer, edited on the server", "updated_at": _ts(9)}

    _save(tmp_path, _conv([M3, edited_m4], updated=9))

    m4 = next(m for m in _on_disk(tmp_path)["chat_messages"] if m["uuid"] == "m4")
    assert m4["text"] == "second answer, edited on the server"
    assert m4["updated_at"] == _ts(9)


def test_conversation_fields_come_from_the_server(tmp_path: Path) -> None:
    _save(tmp_path, _conv([M1, M2, M3, M4], name="Old title", updated=4))

    _save(tmp_path, _conv([M3, M4, M5, M6], name="New title", updated=6))

    data = _on_disk(tmp_path)
    assert data["name"] == "New title"
    assert data["summary"] == "summary of New title"
    assert data["updated_at"] == _ts(6)
    assert data["current_leaf_message_uuid"] == "m6"


def test_merged_messages_are_ordered_by_created_at(tmp_path: Path) -> None:
    # m2 is only in the local copy, and its created_at is between the server's.
    _save(tmp_path, _conv([M1, M2, M3], updated=3))

    _save(tmp_path, _conv([M1, M3, M4], updated=4))

    assert _uuids(tmp_path) == ["m1", "m2", "m3", "m4"]


def test_messages_with_equal_created_at_keep_the_server_order(tmp_path: Path) -> None:
    a = _msg("same-a", 5, "a")
    b = _msg("same-b", 5, "b")
    _save(tmp_path, _conv([M1, b, a], updated=5))

    _save(tmp_path, _conv([M1, a, b], updated=6))

    assert _uuids(tmp_path) == ["m1", "same-a", "same-b"]


def test_a_new_conversation_is_saved_as_the_server_copy(tmp_path: Path) -> None:
    _save(tmp_path, _conv([M1, M2], updated=2))

    data = _on_disk(tmp_path)
    assert [m["uuid"] for m in data["chat_messages"]] == ["m1", "m2"]
    assert data["chat_messages"][0] == M1
    assert data["organization_id"] == ORG
    assert data["organization_name"] == "Personal"


def test_a_refetch_with_the_same_messages_adds_no_duplicates(tmp_path: Path) -> None:
    _save(tmp_path, _conv([M1, M2, M3, M4], updated=4))

    _save(tmp_path, _conv([M1, M2, M3, M4], updated=4))

    assert _uuids(tmp_path) == ["m1", "m2", "m3", "m4"]


def _without_uuid(how: str) -> dict:
    message = _msg("placeholder", 2, "an old message that has no uuid", "assistant", "m1")
    if how == "missing":
        del message["uuid"]
    else:
        message["uuid"] = None
    return message


def _count_equal(out: Path, message: dict) -> int:
    return sum(1 for m in _on_disk(out)["chat_messages"] if m == message)


@pytest.mark.parametrize("how", ["missing", "null"])
def test_a_local_message_without_a_uuid_is_kept(tmp_path: Path, how: str) -> None:
    no_uuid = _without_uuid(how)
    _save(tmp_path, _conv([M1, no_uuid, M3], updated=3))

    _save(tmp_path, _conv([M1, M3, M4], updated=4))

    assert _count_equal(tmp_path, no_uuid) == 1


@pytest.mark.parametrize("how", ["missing", "null"])
def test_a_message_without_a_uuid_that_the_server_also_holds_is_not_duplicated(
    tmp_path: Path, how: str
) -> None:
    no_uuid = _without_uuid(how)
    _save(tmp_path, _conv([M1, no_uuid, M3], updated=3))

    _save(tmp_path, _conv([M1, copy.deepcopy(no_uuid), M3, M4], updated=4))

    assert _count_equal(tmp_path, no_uuid) == 1


def test_a_merge_adds_no_file_and_deletes_no_file(tmp_path: Path) -> None:
    other = tmp_path / "by-org" / ORG / "another-conversation.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"uuid": "another-conversation", "chat_messages": []}), encoding="utf-8")
    _save(tmp_path, _conv([M1, M2, M3, M4], updated=4))
    before = _files(tmp_path)

    _save(tmp_path, _conv([M3, M4, M5, M6], updated=6))

    assert _files(tmp_path) == before


def test_a_failed_serialization_leaves_the_old_file_intact(tmp_path: Path) -> None:
    _save(tmp_path, _conv([M1, M2], updated=2))
    old_bytes = _path(tmp_path).read_bytes()
    before = _files(tmp_path)
    # json cannot serialize this value, so the write fails part of the way.
    broken = {**M3, "unserializable": object()}

    with pytest.raises(TypeError):
        _save(tmp_path, _conv([M1, M2, broken], updated=3))

    assert _path(tmp_path).read_bytes() == old_bytes
    assert _files(tmp_path) == before


def test_a_failed_replace_leaves_the_old_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save(tmp_path, _conv([M1, M2], updated=2))
    old_bytes = _path(tmp_path).read_bytes()
    before = _files(tmp_path)

    def disk_full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    # A Desktop write must install its file with a rename, never write in place.
    monkeypatch.setattr(os, "replace", disk_full)
    with pytest.raises(OSError):
        _save(tmp_path, _conv([M1, M2, M3], updated=3))
    monkeypatch.undo()

    assert _path(tmp_path).read_bytes() == old_bytes
    assert _files(tmp_path) == before


# A local file that is not valid JSON, or not a JSON object, cannot be merged.
# Its bytes go to a side file `<file name>.unreadable-<n>` before the server
# copy is written. The side file does not end in `.json`, so no reader loads it.
UNREADABLE = [b"{not json PRIVATE-TEXT-51", b"[1, 2, 3]", b"", b"\xff\xfe binary"]


def _side(out: Path, n: int) -> Path:
    return _path(out).with_name(f"{CONV}.json.unreadable-{n}")


def _write_unreadable(out: Path, content: bytes) -> None:
    _path(out).parent.mkdir(parents=True, exist_ok=True)
    _path(out).write_bytes(content)


@pytest.mark.parametrize("content", UNREADABLE)
def test_a_corrupt_local_file_does_not_stop_the_refetch(tmp_path: Path, content: bytes) -> None:
    _write_unreadable(tmp_path, content)

    _save(tmp_path, _conv([M3, M4], updated=4))

    assert _uuids(tmp_path) == ["m3", "m4"]


@pytest.mark.parametrize("content", UNREADABLE)
def test_a_corrupt_local_file_is_kept_in_a_side_file(tmp_path: Path, content: bytes) -> None:
    _write_unreadable(tmp_path, content)

    _save(tmp_path, _conv([M3, M4], updated=4))

    assert _side(tmp_path, 1).read_bytes() == content


def test_a_side_file_takes_the_next_free_number(tmp_path: Path) -> None:
    _write_unreadable(tmp_path, b"{first")
    _side(tmp_path, 1).parent.mkdir(parents=True, exist_ok=True)
    _side(tmp_path, 1).write_bytes(b"an older side file")

    _save(tmp_path, _conv([M3, M4], updated=4))

    assert _side(tmp_path, 1).read_bytes() == b"an older side file"
    assert _side(tmp_path, 2).read_bytes() == b"{first"


def test_a_refetch_of_a_valid_file_adds_no_side_file(tmp_path: Path) -> None:
    _write_unreadable(tmp_path, b"{broken")
    _save(tmp_path, _conv([M3, M4], updated=4))
    after_first = _files(tmp_path)

    _save(tmp_path, _conv([M3, M4, M5, M6], updated=6))

    assert _side(tmp_path, 1).relative_to(tmp_path) in after_first
    assert _files(tmp_path) == after_first


def test_a_corrupt_local_file_logs_one_warning_without_its_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_unreadable(tmp_path, b"{not json PRIVATE-TEXT-51")

    with caplog.at_level("WARNING"):
        _save(tmp_path, _conv([M3, M4], updated=4))

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "PRIVATE-TEXT-51" not in warnings[0].getMessage()


# ------------------------------------------------- Desktop fetch entry points


def _serve(monkeypatch: pytest.MonkeyPatch, conv: dict) -> None:
    from fetcher.bulk_fetch import ClaudeFetcher

    listing = {k: conv[k] for k in ("uuid", "name", "updated_at")}
    monkeypatch.setattr(ClaudeFetcher, "fetch_conversation_list", lambda self: [dict(listing)])
    monkeypatch.setattr(ClaudeFetcher, "fetch_conversation", lambda self, uuid: copy.deepcopy(conv))


def test_incremental_run_keeps_messages_the_server_no_longer_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save(tmp_path, _conv([M1, M2, M3, M4], updated=4))
    # The server copy is newer, so an incremental fetch downloads it again.
    _serve(monkeypatch, _conv([M3, M4, M5, M6], updated=6))

    _fetcher(tmp_path, incremental=True).run()

    assert _uuids(tmp_path) == ["m1", "m2", "m3", "m4", "m5", "m6"]


def test_full_refresh_run_all_orgs_keeps_messages_the_server_no_longer_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save(tmp_path, _conv([M1, M2, M3, M4], updated=4))
    _serve(monkeypatch, _conv([M3, M4, M5, M6], updated=6))

    _fetcher(tmp_path, incremental=False).run_all_orgs()

    assert _uuids(tmp_path) == ["m1", "m2", "m3", "m4", "m5", "m6"]


# ------------------------------------------------ remote task, Full Refresh

SID = "cse_01KeepArchive"


def _remote(out: Path, incremental: bool):
    from fetcher.remote_sessions import RemoteCoworkFetcher

    return RemoteCoworkFetcher(lambda *a, **k: None, out, incremental=incremental, delay=0)


def _audit(out: Path) -> list[dict]:
    path = out / "cowork-remote" / ORG / f"remote_{SID}" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _text(line: dict) -> str:
    content = line["message"]["content"]
    return content if isinstance(content, str) else content[0]["text"]


@pytest.fixture
def remote_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """A task archived with events 1-4; the server then drops 1-2, edits 4, and adds 5-6."""
    session = FakeSession(
        SID,
        "Weekly report",
        [
            user_event(1, "alpha question"),
            assistant_event(2, "alpha answer"),
            user_event(3, "beta question"),
            assistant_event(4, "beta answer"),
        ],
        updated_at=iso(4),
    )
    FakeSessionsApi({ORG: [session]}).install(monkeypatch)
    _remote(tmp_path, incremental=True).sync_org(ORG)
    session.events = [
        user_event(3, "beta question"),
        assistant_event(4, "beta answer, edited on the server"),
        user_event(5, "gamma question"),
        assistant_event(6, "gamma answer"),
    ]
    session.updated_at = iso(6)
    return session


def test_remote_full_refresh_keeps_events_the_server_no_longer_returns(
    tmp_path: Path, remote_task: FakeSession
) -> None:
    _remote(tmp_path, incremental=False).sync_org(ORG)

    assert {line["_sequence_num"] for line in _audit(tmp_path)} == {1, 2, 3, 4, 5, 6}


def test_remote_full_refresh_server_event_wins_for_a_sequence_number_in_both(
    tmp_path: Path, remote_task: FakeSession
) -> None:
    _remote(tmp_path, incremental=False).sync_org(ORG)

    lines = [line for line in _audit(tmp_path) if line["_sequence_num"] == 4]
    assert len(lines) == 1
    assert _text(lines[0]) == "beta answer, edited on the server"


def test_remote_full_refresh_keeps_local_lines_unchanged(
    tmp_path: Path, remote_task: FakeSession
) -> None:
    before = {line["_sequence_num"]: line for line in _audit(tmp_path)}

    _remote(tmp_path, incremental=False).sync_org(ORG)

    after = {line["_sequence_num"]: line for line in _audit(tmp_path)}
    assert after.get(1) == before[1]
    assert after.get(2) == before[2]


def test_remote_full_refresh_orders_lines_by_sequence_number_without_duplicates(
    tmp_path: Path, remote_task: FakeSession
) -> None:
    _remote(tmp_path, incremental=False).sync_org(ORG)

    assert [line["_sequence_num"] for line in _audit(tmp_path)] == [1, 2, 3, 4, 5, 6]


def test_remote_full_refresh_adds_no_file_and_deletes_no_file(
    tmp_path: Path, remote_task: FakeSession
) -> None:
    before = _files(tmp_path)

    _remote(tmp_path, incremental=False).sync_org(ORG)

    assert _files(tmp_path) == before
