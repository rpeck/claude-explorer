"""N8: Codex is the fourth source in the list, the viewer, search, and export.

Plan §4.1 (the Codex descriptor), §4.2 (the archive layout and the Codex
steps of a copy pass), §4.4 (the projection version), §4.5 (the output
fields), and §5 (the N8 privacy tests). Every test builds a synthetic Codex
home with ``backend.tests.codex_fake``; nothing reads a real ``~/.codex``
(D10). ``Path.home()`` points at a temp folder, and the Codex home is
``~/.codex`` inside it, so ``source_home`` is ``~/.codex``.

Contract (orchestrator, 2026-11-19):

- ``backend.sources.codex``: ``DESCRIPTOR``, ``CodexAdapter(store)``,
  ``CODEX_PROJECTION_VERSION``. The registry order ends with ``CODEX``.
- The archive is ``store.data_dir / "codex" / <home_key>`` (§4.2), with
  ``home_key = backend.archive_sync.home_key(codex_home)``.
- ``discover()`` gives one ``threads/<thread_id>.json`` per thread id.
- ``source_home`` is ``~/<rest>`` for a home under ``Path.home()``.
- The search index reads ``backend.sources.codex.CODEX_PROJECTION_VERSION``
  when it opens; a changed value makes the Codex rows stale.
- ``GET /api/config`` has ``cloud_sync_gate``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.tests import codex_fake as fake
from backend.tests._platform_home import patch_home

DESKTOP_UUID = "2b3c4d5e-6f70-4812-9a3b-4c5d6e7f8091"

#: Extra threads that some tests add to the default corpus.
THREAD_COMPACTED = "019a9000-0000-7000-8000-0000000000c1"
THREAD_ASSISTANT_ONLY = "019a9000-0000-7000-8000-0000000000c2"
THREAD_NO_TURNS = "019a9000-0000-7000-8000-0000000000c3"

THREAD_JSON_KEYS = {
    "version", "thread_id", "home_key", "source_name", "title", "cwd", "model",
    "archived", "history_mode", "thread_source", "parent_thread_id",
    "imported_from", "rollouts", "current_stem", "current_ambiguous",
    "updated_at_ms",
}


def _at(day: str, seconds: int = 0) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(seconds=seconds)


def _real(path: str | Path) -> str:
    return os.path.realpath(path)


@dataclass
class CodexEnv:
    home: Path
    codex: Path
    data_dir: Path
    claude_dir: Path

    def store(self) -> Any:
        from backend.store import ConversationStore

        return ConversationStore(data_dir=self.data_dir, claude_dir=self.claude_dir)

    def use_home(self, monkeypatch: pytest.MonkeyPatch, codex: Path) -> None:
        from backend import config

        monkeypatch.setenv("CODEX_HOME", str(codex))
        self.codex = codex
        config.get_settings.cache_clear()

    def sync(self) -> Any:
        """One Codex copy pass through the adapter, as every caller makes it."""
        from backend import config
        from backend.archive_sync import SyncContext
        from backend.sources.registry import get_adapter

        config.get_settings.cache_clear()
        ctx = SyncContext(settings=config.get_settings(), reason="cli", lock_timeout_s=10)
        return get_adapter("CODEX", self.store()).archive_sync(ctx)

    @property
    def archive(self) -> Path:
        from backend.archive_sync import home_key

        return self.data_dir / "codex" / home_key(self.codex)

    def thread_json(self, thread_id: str) -> dict[str, Any]:
        return json.loads((self.archive / "threads" / f"{thread_id}.json").read_bytes())

    def rows(self, **kwargs: Any) -> dict[str, Any]:
        rows = self.store().list_conversations(source="CODEX", **kwargs)
        return {r.uuid: r for r in rows}


@pytest.fixture
def cx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CodexEnv]:
    from backend import config
    from backend import search_index as si
    from backend.store import invalidate_detail_dict_cache

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    codex = fake.build_codex_home(home / ".codex")
    data_dir = tmp_path / "explorer" / "conversations"
    data_dir.mkdir(parents=True)
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setattr(si, "default_index_path", lambda: tmp_path / "explorer" / "search-index.sqlite")
    si.reset_search_index_for_tests()
    config.get_settings.cache_clear()
    invalidate_detail_dict_cache()
    try:
        yield CodexEnv(home=home, codex=codex, data_dir=data_dir, claude_dir=claude_dir)
    finally:
        invalidate_detail_dict_cache()
        si.reset_search_index_for_tests()
        config.get_settings.cache_clear()


@pytest.fixture
def api() -> TestClient:
    from backend.main import app

    # No ``with``: the lifespan (watcher, index build) stays off.
    return TestClient(app)


def _rollout(codex: Path, thread_id: str) -> Path:
    """The one rollout of a default-corpus thread (plain or ``.zst``)."""
    hits = sorted(p for p in codex.rglob(f"rollout-*-{thread_id}.jsonl*"))
    assert len(hits) == 1, hits
    return hits[0]


def _projection(path: Path, thread_id: str) -> dict[str, Any]:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import read_rollout

    read = read_rollout(path)
    return project_rollout(read.records, thread_id=thread_id, damaged_lines=read.damaged_lines)


def _snapshot(root: Path) -> dict[str, Any]:
    """Name, size, mtime, and content hash of every file and folder under ``root``."""
    out: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        st = path.lstat()
        if path.is_dir():
            out[rel] = ("dir", st.st_mtime_ns)
        else:
            out[rel] = (st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def _add_extra_threads(codex: Path) -> None:
    """A compacted thread, an assistant-only thread, a thread with no turns,
    and a second, newer rollout of THREAD_IMPORTED (it has no state_5 row)."""
    t0 = _at("2026-11-14T10:00:00")
    s = lambda n: t0 + timedelta(seconds=n)
    cwd = "/home/fixture/proj-epsilon"
    fake.write_rollout(
        fake.rollout_path(codex, t0, THREAD_COMPACTED),
        [
            fake.session_meta(t0, THREAD_COMPACTED, cwd=cwd, history_mode="paginated"),
            fake.turn_context(s(1), "turn-c1", cwd=cwd, model="gpt-5.5-codex"),
            fake.item_completed(s(2), THREAD_COMPACTED, "turn-c1", fake.user_message_item("item-c1-user", "Start the long task.")),
            fake.rollout_line(s(3), "compacted", {"message": "Earlier turns, summarized.", "replacement_history": []}),
            fake.item_completed(s(4), THREAD_COMPACTED, "turn-c1", fake.agent_message_item("item-c1-reply", "Continuing after the compaction.")),
        ],
    )
    t1 = _at("2026-11-14T11:00:00")
    fake.write_rollout(
        fake.rollout_path(codex, t1, THREAD_ASSISTANT_ONLY),
        [
            fake.session_meta(t1, THREAD_ASSISTANT_ONLY, cwd=cwd, history_mode="paginated"),
            fake.turn_context(t1 + timedelta(seconds=1), "turn-c2", cwd=cwd, model="gpt-5.5-codex"),
            fake.item_completed(t1 + timedelta(seconds=2), THREAD_ASSISTANT_ONLY, "turn-c2",
                                fake.agent_message_item("item-c2-reply", "Only the assistant spoke here.")),
        ],
    )
    t2 = _at("2026-11-14T12:00:00")
    fake.write_rollout(
        fake.rollout_path(codex, t2, THREAD_NO_TURNS),
        [fake.session_meta(t2, THREAD_NO_TURNS, cwd=cwd, history_mode="paginated")],
    )
    t3 = _at("2026-11-13T09:00:00")
    fake.write_rollout(
        fake.rollout_path(codex, t3, fake.THREAD_IMPORTED),
        [
            fake.session_meta(t3, fake.THREAD_IMPORTED, cwd="/home/fixture/proj-delta", history_mode="paginated"),
            fake.turn_context(t3 + timedelta(seconds=1), "turn-f2", cwd="/home/fixture/proj-delta", model="gpt-5.5-codex"),
            fake.item_completed(t3 + timedelta(seconds=2), fake.THREAD_IMPORTED, "turn-f2",
                                fake.user_message_item("item-f2-user", "Resume the migration at step 5.")),
            fake.item_completed(t3 + timedelta(seconds=3), fake.THREAD_IMPORTED, "turn-f2",
                                fake.agent_message_item("item-f2-reply", "Step 5 is done.")),
        ],
    )


# ---------------------------------------------------------------------------
# The descriptor, the source id, and the registry (§4.1)
# ---------------------------------------------------------------------------


def test_codex_descriptor_has_the_plan_values() -> None:
    from backend.sources.codex import DESCRIPTOR

    assert DESCRIPTOR.id == "CODEX"
    assert DESCRIPTOR.label == "Codex"
    assert DESCRIPTOR.short_label == "Codex"
    assert DESCRIPTOR.chronological is True
    assert DESCRIPTOR.has_projects is True
    assert DESCRIPTOR.has_archive_state is True
    assert DESCRIPTOR.has_organizations is False
    assert DESCRIPTOR.refetchable is False
    assert DESCRIPTOR.messages_change_in_place is True


def test_codex_is_the_last_source_in_the_registry() -> None:
    from backend.sources.registry import get_descriptors, reset_for_tests

    reset_for_tests()
    assert [d.id for d in get_descriptors()] == ["CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK", "CODEX"]


def test_source_id_and_source_filter_include_codex() -> None:
    from typing import get_args

    from backend.sources.ids import SourceFilter, SourceId
    from backend.sources.registry import get_descriptors, reset_for_tests

    reset_for_tests()
    assert "CODEX" in get_args(SourceId)
    assert "CODEX" in get_args(SourceFilter)
    assert set(get_args(SourceId)) == {d.id for d in get_descriptors()}


def test_the_generated_sources_json_has_the_codex_entry_last() -> None:
    from backend.sources.codex import DESCRIPTOR

    root = Path(__file__).resolve().parents[2]
    entries = json.loads((root / "frontend" / "src" / "lib" / "sources.generated.json").read_text("utf-8"))
    assert entries[-1] == {
        "id": "CODEX",
        "label": "Codex",
        "short_label": "Codex",
        "chronological": DESCRIPTOR.chronological,
        "has_projects": True,
        "has_archive_state": True,
        "has_organizations": False,
        "refetchable": False,
        "messages_change_in_place": True,
        "assistant_label": "Codex",
    }


def test_every_descriptor_has_an_assistant_label_and_the_json_matches() -> None:
    from backend.sources.registry import get_descriptors, reset_for_tests

    reset_for_tests()
    labels = {d.id: d.assistant_label for d in get_descriptors()}
    assert labels == {"CLAUDE_AI": "Claude", "CLAUDE_CODE": "Claude", "CLAUDE_COWORK": "Claude", "CODEX": "Codex"}
    root = Path(__file__).resolve().parents[2]
    entries = json.loads((root / "frontend" / "src" / "lib" / "sources.generated.json").read_text("utf-8"))
    assert {e["id"]: e["assistant_label"] for e in entries} == labels


def test_codex_watch_roots_are_in_the_codex_home_and_never_created(cx: CodexEnv) -> None:
    from backend.sources.registry import get_adapter

    roots = get_adapter("CODEX", cx.store()).watch_roots()
    assert roots
    patterns: set[str] = set()
    for root in roots:
        assert root.create is False
        assert _real(root.path) == _real(cx.codex) or _real(root.path).startswith(_real(cx.codex) + os.sep)
        patterns.update(root.patterns)
    assert {"*.jsonl", "*.jsonl.zst", "session_index.jsonl", "state_5.sqlite*"} <= patterns


def test_the_codex_branch_flag_is_false(cx: CodexEnv) -> None:
    from backend.sources.registry import get_adapter

    adapter = get_adapter("CODEX", cx.store())
    msgs = [{"uuid": "a", "parent_message_uuid": None}, {"uuid": "b", "parent_message_uuid": "a"},
            {"uuid": "c", "parent_message_uuid": "a"}]
    assert adapter.branch_flag({"source": "CODEX"}, msgs) is False


# ---------------------------------------------------------------------------
# The archive layout and the copy pass (§4.2)
# ---------------------------------------------------------------------------


def test_sync_writes_home_json_with_the_absolute_home_path(cx: CodexEnv) -> None:
    report = cx.sync()

    assert report.skipped_reason is None
    assert report.wrote_any is True
    home_json = json.loads((cx.archive / "home.json").read_bytes())
    assert home_json["version"] == 1
    assert os.path.isabs(home_json["home_path"])
    assert _real(home_json["home_path"]) == _real(cx.codex)
    assert isinstance(home_json["first_seen_ms"], int)


def test_a_second_pass_keeps_first_seen_ms(cx: CodexEnv) -> None:
    cx.sync()
    first = json.loads((cx.archive / "home.json").read_bytes())["first_seen_ms"]
    cx.sync()
    assert json.loads((cx.archive / "home.json").read_bytes())["first_seen_ms"] == first


def test_sync_copies_every_rollout_decoded_under_its_stem(cx: CodexEnv) -> None:
    from backend.codex.zstd import decompress

    cx.sync()
    for tid in fake.ALL_THREADS:
        src = _rollout(cx.codex, tid)
        stem = src.name.split(".jsonl")[0]
        copy = cx.archive / "rollouts" / f"{stem}.jsonl"
        raw = src.read_bytes()
        assert copy.read_bytes() == (decompress(raw) if src.suffix == ".zst" else raw), tid


def test_meta_json_records_the_thread_id_and_the_source(cx: CodexEnv) -> None:
    cx.sync()
    src = _rollout(cx.codex, fake.THREAD_LEGACY)
    stem = src.name.split(".jsonl")[0]
    meta = json.loads((cx.archive / "rollouts" / f"{stem}.meta.json").read_bytes())
    assert meta["version"] == 1
    assert meta["stem"] == stem
    assert meta["thread_id"] == fake.THREAD_LEGACY
    assert _real(meta["source_path"]) == _real(src)
    assert meta["copied_bytes"] == (cx.archive / "rollouts" / f"{stem}.jsonl").stat().st_size


def test_the_plain_file_wins_when_both_forms_exist(cx: CodexEnv) -> None:
    from backend.codex.zstd import decompress

    zst = _rollout(cx.codex, fake.THREAD_LEGACY)
    plain = zst.with_suffix("")  # .jsonl
    extra = fake.encode_lines([fake.response_item(_at("2026-10-20T14:40:00"), fake.message("assistant", fake.output_text("A resumed line.")))])
    plain.write_bytes(decompress(zst.read_bytes()) + extra)

    cx.sync()
    stem = plain.name[: -len(".jsonl")]
    meta = json.loads((cx.archive / "rollouts" / f"{stem}.meta.json").read_bytes())
    assert _real(meta["source_path"]) == _real(plain)
    assert (cx.archive / "rollouts" / f"{stem}.jsonl").read_bytes() == plain.read_bytes()


def test_each_thread_json_has_the_plan_schema(cx: CodexEnv) -> None:
    from backend.archive_sync import home_key

    cx.sync()
    for tid in fake.ALL_THREADS:
        doc = cx.thread_json(tid)
        assert THREAD_JSON_KEYS <= set(doc), (tid, sorted(THREAD_JSON_KEYS - set(doc)))
        assert doc["version"] == 1
        assert doc["thread_id"] == tid
        assert doc["home_key"] == home_key(cx.codex)


def test_thread_json_rollouts_carry_copied_bytes(cx: CodexEnv) -> None:
    cx.sync()
    doc = cx.thread_json(fake.THREAD_PAGINATED)
    assert len(doc["rollouts"]) == 1
    entry = doc["rollouts"][0]
    assert entry["stem"] == doc["current_stem"]
    assert entry["copied_bytes"] == (cx.archive / "rollouts" / f"{entry['stem']}.jsonl").stat().st_size
    assert isinstance(entry["session_meta_ts_ms"], int)


@pytest.mark.parametrize("tid", [fake.THREAD_PAGINATED, fake.THREAD_LEGACY])
def test_thread_json_model_comes_from_the_projection_of_the_current_rollout(cx: CodexEnv, tid: str) -> None:
    cx.sync()
    expected = _projection(_rollout(cx.codex, tid), tid)["model"]
    assert expected
    assert cx.thread_json(tid)["model"] == expected


def test_thread_json_metadata_follows_the_n5_rules(cx: CodexEnv) -> None:
    cx.sync()
    pag = cx.thread_json(fake.THREAD_PAGINATED)
    assert pag["source_name"] == "Alpha parser refactor"
    assert pag["title"] == "Alpha parser refactor"
    assert pag["cwd"] == "/home/fixture/proj-alpha"
    assert pag["archived"] is False
    assert cx.thread_json(fake.THREAD_ARCHIVED)["archived"] is True
    assert cx.thread_json(fake.THREAD_SUBAGENT)["parent_thread_id"] == fake.THREAD_PAGINATED
    assert cx.thread_json(fake.THREAD_IMPORTED)["imported_from"] == fake.IMPORTED_SOURCE_PATH


def test_a_pass_with_no_change_writes_nothing(cx: CodexEnv) -> None:
    cx.sync()
    threads = sorted((cx.archive / "threads").glob("*.json"))
    before = {p.name: p.stat().st_mtime_ns for p in threads}

    report = cx.sync()

    assert report.wrote_any is False
    assert {p.name: p.stat().st_mtime_ns for p in threads} == before


def test_a_rename_in_the_session_index_reaches_the_archive_without_a_rollout_change(cx: CodexEnv) -> None:
    cx.sync()
    with open(cx.codex / "session_index.jsonl", "ab") as fh:
        fh.write(fake.encode_lines([{"id": fake.THREAD_PAGINATED, "thread_name": "Alpha renamed",
                                     "updated_at": fake.ts(_at("2026-11-11T08:00:00"))}]))
    cx.sync()

    assert cx.thread_json(fake.THREAD_PAGINATED)["source_name"] == "Alpha renamed"
    row = cx.rows()[fake.THREAD_PAGINATED]
    assert row.name == "Alpha renamed"
    assert row.source_name == "Alpha renamed"


def test_archiving_a_rollout_in_codex_reaches_the_archive(cx: CodexEnv) -> None:
    # THREAD_IMPORTED has no state_5 row, so the folder decides `archived`.
    cx.sync()
    src = _rollout(cx.codex, fake.THREAD_IMPORTED)
    (cx.codex / "archived_sessions").mkdir(exist_ok=True)
    shutil.move(src, cx.codex / "archived_sessions" / src.name)
    cx.sync()

    assert cx.thread_json(fake.THREAD_IMPORTED)["archived"] is True
    assert fake.THREAD_IMPORTED not in cx.rows()
    assert cx.rows(show_archived=True)[fake.THREAD_IMPORTED].is_archived is True


def test_a_rollout_that_codex_deletes_stays_in_the_archive_and_the_list(cx: CodexEnv) -> None:
    cx.sync()
    _rollout(cx.codex, fake.THREAD_LEGACY).unlink()
    cx.sync()

    assert (cx.archive / "threads" / f"{fake.THREAD_LEGACY}.json").is_file()
    assert fake.THREAD_LEGACY in cx.rows()


def test_readers_read_only_the_archive_after_the_codex_home_is_gone(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    shutil.rmtree(cx.codex)

    assert set(cx.rows(show_archived=True)) == set(fake.ALL_THREADS)
    r = api.get(f"/api/conversations/{fake.THREAD_PAGINATED}")
    assert r.status_code == 200, r.text
    assert r.json()["messages"]


def test_run_archive_syncs_copies_codex(cx: CodexEnv) -> None:
    from backend.write_gate import run_archive_syncs

    reports = run_archive_syncs(cx.store(), "cli", 10)

    assert any(r.wrote_any for r in reports)
    assert (cx.archive / "threads" / f"{fake.THREAD_PAGINATED}.json").is_file()


def test_discover_gives_one_thread_file_per_thread_id(cx: CodexEnv) -> None:
    from backend.sources.registry import get_adapter

    cx.sync()
    paths = list(get_adapter("CODEX", cx.store()).discover())
    assert sorted(p.name for p in paths) == sorted(f"{t}.json" for t in fake.ALL_THREADS)
    for p in paths:
        assert p.parent.name == "threads"
        assert _real(p).startswith(_real(cx.data_dir / "codex") + os.sep)


def test_the_newest_copy_of_a_thread_wins_across_homes(cx: CodexEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.sources.registry import get_adapter

    cx.sync()
    home_a = cx.codex
    home_b = cx.home / "codex-b"
    shutil.copytree(home_a, home_b)
    (home_b / "state_5.sqlite").unlink()
    newer = _at("2026-11-20T09:00:00")
    pag_b = _rollout(home_b, fake.THREAD_PAGINATED)
    fake.write_state_db(home_b, [
        fake.state_row(fake.THREAD_PAGINATED, pag_b, started=_at("2026-11-10T09:00:00"), updated=newer,
                       cwd="/home/fixture/proj-alpha", title="Copy B", model="gpt-5.6-codex",
                       history_mode="paginated", name="Copy B"),
    ])
    fake.write_session_index(home_b, [(fake.THREAD_PAGINATED, "Copy B", newer)])
    cx.use_home(monkeypatch, home_b)
    cx.sync()

    rows = [r for r in cx.store().list_conversations(source="CODEX", show_archived=True)
            if r.uuid == fake.THREAD_PAGINATED]
    assert len(rows) == 1
    assert rows[0].name == "Copy B"
    assert rows[0].source_home == "~/codex-b"
    paths = list(get_adapter("CODEX", cx.store()).discover())
    assert len(paths) == len({p.name for p in paths})


# ---------------------------------------------------------------------------
# Privacy and a missing home (§5, AC6)
# ---------------------------------------------------------------------------


def test_a_full_sync_and_every_reader_change_no_file_in_the_codex_home(cx: CodexEnv, api: TestClient) -> None:
    from backend.search import search_conversations
    from backend.write_gate import run_archive_syncs

    before = _snapshot(cx.codex)
    run_archive_syncs(cx.store(), "cli", 10)
    cx.sync()
    cx.rows(show_archived=True)
    for tid in fake.ALL_THREADS:
        assert api.get(f"/api/conversations/{tid}").status_code == 200
    api.get(f"/api/conversations/{fake.THREAD_LEGACY}/export/markdown")
    search_conversations(cx.store(), "semicolon")

    assert _snapshot(cx.codex) == before
    assert not list(cx.codex.glob("*-wal")) and not list(cx.codex.glob("*-shm"))


def test_a_missing_codex_home_is_harmless_and_is_not_created(cx: CodexEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.write_gate import run_archive_syncs

    missing = cx.home / "no-codex-here"
    cx.use_home(monkeypatch, missing)

    report = cx.sync()
    run_archive_syncs(cx.store(), "watcher", 0)

    assert report.skipped_reason == "no_home"
    assert report.wrote_any is False
    assert not missing.exists()
    assert cx.rows(show_archived=True) == {}


def test_a_thread_json_with_a_newer_version_is_skipped_with_a_warning(
    cx: CodexEnv, caplog: pytest.LogCaptureFixture
) -> None:
    cx.sync()
    path = cx.archive / "threads" / f"{fake.THREAD_LEGACY}.json"
    doc = json.loads(path.read_bytes())
    doc["version"] = 2
    path.write_text(json.dumps(doc), encoding="utf-8")

    with caplog.at_level("WARNING"):
        rows = cx.rows()

    assert fake.THREAD_LEGACY not in rows
    assert fake.THREAD_PAGINATED in rows
    assert any(rec.levelname == "WARNING" for rec in caplog.records)


# ---------------------------------------------------------------------------
# The list (AC1, D2, §4.5)
# ---------------------------------------------------------------------------


def test_every_codex_session_with_a_turn_is_in_the_list(cx: CodexEnv) -> None:
    cx.sync()
    rows = cx.store().list_conversations(source="CODEX", show_archived=True)
    assert sorted(r.uuid for r in rows) == sorted(fake.ALL_THREADS)
    assert all(r.source == "CODEX" for r in rows)
    assert all(r.has_branches is False for r in rows)


def test_archived_codex_sessions_are_hidden_by_default(cx: CodexEnv) -> None:
    cx.sync()
    assert fake.THREAD_ARCHIVED not in cx.rows()
    row = cx.rows(show_archived=True)[fake.THREAD_ARCHIVED]
    assert row.is_archived is True


def test_the_codex_source_filter_returns_only_codex(cx: CodexEnv) -> None:
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    write_desktop_conv(cx.data_dir, build_desktop_conv(uuid=DESKTOP_UUID, name="Desktop chat", body="hello"))
    cx.sync()

    store = cx.store()
    assert {r.source for r in store.list_conversations(source="CODEX")} == {"CODEX"}
    every = store.list_conversations()
    assert DESKTOP_UUID in {r.uuid for r in every}
    assert fake.THREAD_PAGINATED in {r.uuid for r in every}


def test_list_rows_have_title_project_model_and_times(cx: CodexEnv) -> None:
    cx.sync()
    rows = cx.rows(show_archived=True)

    pag = rows[fake.THREAD_PAGINATED]
    assert pag.name == "Alpha parser refactor"
    assert pag.project_path == "/home/fixture/proj-alpha"
    assert pag.project_name == "proj-alpha"
    assert pag.model == _projection(_rollout(cx.codex, fake.THREAD_PAGINATED), fake.THREAD_PAGINATED)["model"]
    assert pag.created_at == _at("2026-11-10T09:00:00")
    assert pag.updated_at >= pag.created_at
    assert pag.message_count > 0
    assert pag.human_message_count == 2

    assert rows[fake.THREAD_LEGACY].name == "Why does the build fail? Here is the error."
    assert rows[fake.THREAD_ARCHIVED].name == "Gamma release notes"
    assert rows[fake.THREAD_IMPORTED].name == "Continue the migration from the Claude Code session."


def test_list_rows_carry_the_codex_name_and_home(cx: CodexEnv) -> None:
    cx.sync()
    rows = cx.rows(show_archived=True)

    pag = rows[fake.THREAD_PAGINATED]
    assert pag.source_name == "Alpha parser refactor"
    assert pag.source_home == "~/.codex"
    assert pag.source_home_abs is not None and _real(pag.source_home_abs) == _real(cx.codex)
    assert rows[fake.THREAD_LEGACY].source_name is None


def test_a_subagent_row_links_to_its_parent(cx: CodexEnv) -> None:
    cx.sync()
    rows = cx.rows(show_archived=True)
    assert rows[fake.THREAD_SUBAGENT].parent_uuid == fake.THREAD_PAGINATED
    assert rows[fake.THREAD_PAGINATED].parent_uuid is None


def test_other_sources_leave_the_codex_fields_empty(cx: CodexEnv) -> None:
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    write_desktop_conv(cx.data_dir, build_desktop_conv(uuid=DESKTOP_UUID, name="Desktop chat", body="hello"))
    row = next(r for r in cx.store().list_conversations() if r.uuid == DESKTOP_UUID)

    assert row.source_name is None
    assert row.source_home is None
    assert row.source_home_abs is None
    assert row.notices == []
    assert row.parent_uuid is None


def test_a_session_with_only_an_assistant_turn_is_listed_and_one_with_no_turns_is_not(cx: CodexEnv) -> None:
    _add_extra_threads(cx.codex)
    cx.sync()
    rows = cx.rows(show_archived=True)
    assert THREAD_ASSISTANT_ONLY in rows
    assert THREAD_NO_TURNS not in rows


def test_the_list_row_and_the_detail_have_the_same_message_count(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    row = cx.rows()[fake.THREAD_PAGINATED]
    detail = api.get(f"/api/conversations/{fake.THREAD_PAGINATED}").json()
    assert row.message_count == detail["message_count"] == len(detail["messages"])


# ---------------------------------------------------------------------------
# Notices (§4.5)
# ---------------------------------------------------------------------------


def test_an_imported_session_has_the_imported_notice(cx: CodexEnv) -> None:
    cx.sync()
    assert "Imported from Claude Code" in cx.rows()[fake.THREAD_IMPORTED].notices


def test_the_warnings_count_is_a_notice(cx: CodexEnv) -> None:
    cx.sync()
    warnings = _projection(_rollout(cx.codex, fake.THREAD_PAGINATED), fake.THREAD_PAGINATED)["warnings"]
    assert warnings
    notices = cx.rows()[fake.THREAD_PAGINATED].notices
    assert any(str(len(warnings)) in n and "warning" in n for n in notices), notices


def test_a_compacted_session_has_no_incomplete_notice(cx: CodexEnv) -> None:
    # Slice 2 (N10) rebuilds a compaction, so it no longer marks the session incomplete.
    _add_extra_threads(cx.codex)
    cx.sync()
    notices = cx.rows()[THREAD_COMPACTED].notices
    assert not any(n.startswith("incomplete: ") for n in notices), notices


def test_two_rollouts_without_a_state_row_give_the_ambiguous_and_older_rollout_notices(cx: CodexEnv) -> None:
    _add_extra_threads(cx.codex)
    cx.sync()

    doc = cx.thread_json(fake.THREAD_IMPORTED)
    assert len(doc["rollouts"]) == 2
    assert doc["current_ambiguous"] is True
    notices = cx.rows()[fake.THREAD_IMPORTED].notices
    assert "ambiguous current rollout" in notices
    assert any(n.startswith("1 ") and "older rollout" in n and "kept in the archive" in n for n in notices), notices


def test_a_plain_session_has_no_incomplete_or_ambiguous_notice(cx: CodexEnv) -> None:
    cx.sync()
    notices = cx.rows()[fake.THREAD_GUARDIAN].notices
    assert not any(n.startswith("incomplete: ") for n in notices)
    assert "ambiguous current rollout" not in notices


# ---------------------------------------------------------------------------
# The viewer (AC1, AC2)
# ---------------------------------------------------------------------------


def test_the_viewer_shows_a_paginated_session_in_order(cx: CodexEnv, api: TestClient) -> None:
    from backend.codex.projection import message_uuid

    cx.sync()
    r = api.get(f"/api/conversations/{fake.THREAD_PAGINATED}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "CODEX"
    assert body["has_branches"] is False
    first = body["messages"][0]
    assert first["sender"] == "human"
    assert first["uuid"] == message_uuid(fake.THREAD_PAGINATED, turn_id="turn-a1", item_id="item-a1-user")
    assert "Refactor the parser module" in json.dumps(first)


def test_the_viewer_shows_a_legacy_zst_session(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    body = api.get(f"/api/conversations/{fake.THREAD_LEGACY}").json()
    assert body["source"] == "CODEX"
    assert "Line 3 of main.c needs a semicolon." in json.dumps(body["messages"])


def test_the_detail_carries_the_codex_fields(cx: CodexEnv, api: TestClient) -> None:
    _add_extra_threads(cx.codex)
    cx.sync()
    body = api.get(f"/api/conversations/{fake.THREAD_IMPORTED}").json()

    assert body["source_name"] is None
    assert body["source_home"] == "~/.codex"
    assert _real(body["source_home_abs"]) == _real(cx.codex)
    assert "Imported from Claude Code" in body["notices"]
    assert "ambiguous current rollout" in body["notices"]
    assert body["parent_uuid"] is None
    sub = api.get(f"/api/conversations/{fake.THREAD_SUBAGENT}").json()
    assert sub["parent_uuid"] == fake.THREAD_PAGINATED


def test_the_codex_tree_is_the_empty_chronological_tree(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    body = api.get(f"/api/conversations/{fake.THREAD_PAGINATED}/tree").json()
    assert body["root_messages"] == []
    assert body["active_path"] == []


def test_the_list_route_accepts_the_codex_source(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    r = api.get("/api/conversations", params={"source": "CODEX"})
    assert r.status_code == 200, r.text
    assert {row["source"] for row in r.json()} == {"CODEX"}
    assert fake.THREAD_PAGINATED in {row["uuid"] for row in r.json()}


def test_the_detail_cache_never_returns_another_source_for_the_same_uuid(cx: CodexEnv, tmp_path: Path) -> None:
    from backend.store import ConversationStore
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    cx.sync()
    other = tmp_path / "other" / "conversations"
    desktop_path = write_desktop_conv(other, build_desktop_conv(uuid=fake.THREAD_LEGACY, name="Desktop twin", body="x"))
    desktop_store = ConversationStore(data_dir=other, claude_dir=cx.claude_dir)
    codex_store = cx.store()
    _, codex_path = codex_store._find_conversation_data(fake.THREAD_LEGACY)
    assert codex_path is not None
    for p in (desktop_path, codex_path):
        os.utime(p, (1_700_000_000, 1_700_000_000))

    assert desktop_store.get_conversation_dict(fake.THREAD_LEGACY)["source"] == "CLAUDE_AI"
    assert codex_store.get_conversation_dict(fake.THREAD_LEGACY)["source"] == "CODEX"
    assert desktop_store.get_conversation_dict(fake.THREAD_LEGACY)["source"] == "CLAUDE_AI"


# ---------------------------------------------------------------------------
# Search (AC2)
# ---------------------------------------------------------------------------


def _build_index(cx: CodexEnv, monkeypatch: pytest.MonkeyPatch, path: Path) -> Any:
    from backend import search_index as si

    idx = si.SearchIndex(path)
    si.build_full_index(cx.store(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    return idx


def test_search_finds_a_codex_message_on_the_fts_path(cx: CodexEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from backend.search import search_conversations

    cx.sync()
    idx = _build_index(cx, monkeypatch, tmp_path / "index.sqlite")
    indexed = {_real(p) for p in idx.list_indexed_paths()}
    assert _real(cx.archive / "threads" / f"{fake.THREAD_LEGACY}.json") in indexed

    resp = search_conversations(cx.store(), "semicolon", source="CODEX")
    assert fake.THREAD_LEGACY in {r.conversation_uuid for r in resp.results}


def test_search_finds_a_codex_message_on_the_linear_path(cx: CodexEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend import search_index as si
    from backend.search import search_conversations

    cx.sync()
    monkeypatch.setattr(si, "get_search_index", lambda: None)
    resp = search_conversations(cx.store(), "semicolon")
    assert fake.THREAD_LEGACY in {r.conversation_uuid for r in resp.results}


def test_load_for_index_returns_the_codex_conversation(cx: CodexEnv) -> None:
    from backend.sources.registry import get_adapter

    cx.sync()
    adapter = get_adapter("CODEX", cx.store())
    path = next(p for p in adapter.discover() if p.stem == fake.THREAD_PAGINATED)
    data = adapter.load_for_index(path)
    assert data is not None
    assert data["uuid"] == fake.THREAD_PAGINATED
    assert data["source"] == "CODEX"
    assert data["chat_messages"]


def _schema_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT version FROM schema_version").fetchone()[0]
    finally:
        conn.close()


def test_a_new_projection_version_reindexes_only_the_codex_rows(
    cx: CodexEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend import search_index as si
    from backend.sources import codex as codex_source
    from backend.sources.registry import get_adapter
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    write_desktop_conv(cx.data_dir, build_desktop_conv(uuid=DESKTOP_UUID, name="Desktop chat", body="hello"))
    cx.sync()
    path = tmp_path / "index.sqlite"
    store = cx.store()
    idx = si.SearchIndex(path)
    si.build_full_index(store, index=idx)
    idx.close()

    same = si.SearchIndex(path)
    assert si.update_drifted_files(store, index=same) == 0
    same.close()

    monkeypatch.setattr(codex_source, "CODEX_PROJECTION_VERSION", codex_source.CODEX_PROJECTION_VERSION + 1)
    bumped = si.SearchIndex(path)
    codex_files = len(list(get_adapter("CODEX", store).discover()))
    assert si.update_drifted_files(store, index=bumped) == codex_files
    assert si.update_drifted_files(store, index=bumped) == 0
    bumped.close()
    assert _schema_version(path) == si.SCHEMA_VERSION

    again = si.SearchIndex(path)
    assert si.update_drifted_files(store, index=again) == 0
    again.close()


# ---------------------------------------------------------------------------
# Export (AC2, D14)
# ---------------------------------------------------------------------------


def test_the_markdown_export_header_names_the_thread_the_codex_name_and_the_home(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    r = api.get(f"/api/conversations/{fake.THREAD_PAGINATED}/export/markdown")
    assert r.status_code == 200, r.text
    md = r.text
    assert f"**Thread ID:** {fake.THREAD_PAGINATED}" in md
    assert "**Codex name:** Alpha parser refactor" in md
    assert "**Codex home:** ~/.codex" in md
    assert "The parser lives in src/parser.py." in md
    assert str(cx.codex) not in md and _real(cx.codex) not in md


def test_only_a_codex_markdown_export_has_the_codex_header(cx: CodexEnv, api: TestClient) -> None:
    from backend.tests.builders import build_desktop_conv, write_desktop_conv

    write_desktop_conv(cx.data_dir, build_desktop_conv(uuid=DESKTOP_UUID, name="Desktop chat", body="hello"))
    cx.sync()
    desktop = api.get(f"/api/conversations/{DESKTOP_UUID}/export/markdown").text
    codex = api.get(f"/api/conversations/{fake.THREAD_GUARDIAN}/export/markdown").text
    assert "**Thread ID:**" in codex
    assert "**Thread ID:**" not in desktop
    assert "**Codex home:**" not in desktop


def test_the_markdown_export_says_none_for_a_session_without_a_codex_name(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    md = api.get(f"/api/conversations/{fake.THREAD_LEGACY}/export/markdown").text
    assert "**Codex name:** none" in md


def test_the_pdf_export_of_a_codex_session_works(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    r = api.get(f"/api/conversations/{fake.THREAD_PAGINATED}/export/pdf")
    assert r.status_code == 200, r.text[:500]
    assert r.content.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# The assistant label in exports (sibling bug: every assistant was "Claude")
# ---------------------------------------------------------------------------


def _bundle_markdown(content: bytes) -> str:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".md"))
        return zf.read(name).decode("utf-8")


def _html_senders(html: str) -> list[str]:
    import re

    return re.findall(r'<div class="message-header">\s*(.*?) <span class="timestamp">', html)


def test_a_codex_markdown_export_labels_assistant_messages_codex(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    md = api.get(f"/api/conversations/{fake.THREAD_PAGINATED}/export/markdown").text
    assert "**Codex gpt-5.6-codex:**" in md  # #52: the label names the model
    assert "**You:**" in md
    assert "**Claude:**" not in md


def test_a_codex_bundle_export_labels_assistant_messages_codex(cx: CodexEnv, api: TestClient) -> None:
    cx.sync()
    r = api.get(f"/api/conversations/{fake.THREAD_PAGINATED}/export/markdown-bundle")
    assert r.status_code == 200, r.text[:500]
    md = _bundle_markdown(r.content)
    assert "**Codex gpt-5.6-codex:**" in md  # #52: the label names the model
    assert "**You:**" in md
    assert "**Claude:**" not in md


def test_the_codex_pdf_html_labels_assistant_messages_codex(cx: CodexEnv) -> None:
    from backend.exporters.pdf import conversation_to_html

    cx.sync()
    senders = set(_html_senders(conversation_to_html(cx.store().get_conversation(fake.THREAD_PAGINATED))))
    assert senders == {"You", "Codex gpt-5.6-codex"}  # #52: the label names the model


def _claude_detail(source: str) -> Any:
    from backend.models import ConversationDetail, Message

    when = _at("2026-11-01T10:00:00")
    return ConversationDetail(
        uuid=DESKTOP_UUID, name="A Claude chat", created_at=when, updated_at=when, source=source,
        messages=[
            Message(uuid="m1", sender="human", text="Hello there.", created_at=when, updated_at=when),
            Message(uuid="m2", sender="assistant", text="Hello back.", created_at=when, updated_at=when),
        ],
    )


@pytest.mark.parametrize("source", ["CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"])
def test_claude_sources_still_label_assistant_messages_claude_and_name_the_source_label(source: str) -> None:
    from backend.exporters.bundle import create_markdown_bundle
    from backend.exporters.markdown import conversation_to_markdown
    from backend.exporters.pdf import conversation_to_html
    from backend.sources.registry import get_descriptor, reset_for_tests

    reset_for_tests()
    # The descriptor field must exist; today's exporters already say "Claude".
    assert get_descriptor(source).assistant_label == "Claude"
    detail = _claude_detail(source)
    assert "**Claude:**" in conversation_to_markdown(detail)
    assert "**Claude:**" in _bundle_markdown(create_markdown_bundle(detail))
    assert set(_html_senders(conversation_to_html(detail))) == {"You", "Claude"}


# ---------------------------------------------------------------------------
# The cloud-sync gate for the banner (§4.3, /api/config)
# ---------------------------------------------------------------------------


def test_config_cloud_sync_gate_is_null_for_a_local_data_dir(cx: CodexEnv, api: TestClient) -> None:
    body = api.get("/api/config").json()
    assert "cloud_sync_gate" in body
    assert body["cloud_sync_gate"] is None


def _cloud_data_dir(cx: CodexEnv, monkeypatch: pytest.MonkeyPatch) -> Path:
    from backend import config

    (cx.home / "Dropbox").mkdir()
    data_dir = cx.home / "Dropbox" / "explorer" / "conversations"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    config.get_settings.cache_clear()
    return data_dir


def test_config_cloud_sync_gate_names_the_provider_folder_and_data_dir(
    cx: CodexEnv, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import config
    from backend.write_gate import cloud_sync_block

    data_dir = _cloud_data_dir(cx, monkeypatch)
    provider, folder = cloud_sync_block(config.get_settings())

    gate = api.get("/api/config").json()["cloud_sync_gate"]
    assert gate == {"provider": provider, "folder": str(folder), "data_dir": str(config.get_settings().data_dir)}
    assert provider == "Dropbox"
    assert _real(gate["data_dir"]) == _real(data_dir)


def test_config_cloud_sync_gate_is_null_after_the_opt_in(
    cx: CodexEnv, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _cloud_data_dir(cx, monkeypatch)
    cfg = cx.home / ".claude-explorer" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"allow_writes_in_cloud_sync": _real(data_dir)}), encoding="utf-8")

    assert api.get("/api/config").json()["cloud_sync_gate"] is None


def test_the_codex_copy_skips_while_the_cloud_sync_gate_is_closed_and_runs_after_the_opt_in(
    cx: CodexEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.store import ConversationStore
    from backend.write_gate import run_archive_syncs

    data_dir = _cloud_data_dir(cx, monkeypatch)
    store = ConversationStore(data_dir=data_dir, claude_dir=cx.claude_dir)
    reports = run_archive_syncs(store, "cli", 10)

    assert [r.skipped_reason for r in reports] == ["cloud_sync_gate"]
    assert not (data_dir / "codex").exists()

    cfg = cx.home / ".claude-explorer" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"allow_writes_in_cloud_sync": _real(data_dir)}), encoding="utf-8")
    run_archive_syncs(store, "cli", 10)
    assert list((data_dir / "codex").glob(f"*/threads/{fake.THREAD_PAGINATED}.json"))


# ---------------------------------------------------------------------------
# Docs (the MCPB section and the package text)
# ---------------------------------------------------------------------------


def test_the_readme_says_how_to_set_the_codex_home_and_the_description_names_codex() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text("utf-8")
    # The supervised watcher has no shell environment, so config.json is the reliable place (§4.3).
    assert "CODEX_HOME" in readme
    assert "codex_home" in readme
    assert "config.json" in readme
    description = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["description"]
    assert "Codex" in description
