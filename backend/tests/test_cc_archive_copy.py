"""A1: copy Claude Code and local Cowork sessions into the archive (#92 prerequisite).

The known gap of AGENTS.md "Keep all history": Claude Code deletes old session
files, and Explorer then lost them. Each Claude Code session file (main and
agent files) and each local Cowork session is now copied into the archive,
append-only, as the Codex copy is (``backend/archive_sync.py``). The live file
wins while it exists; when it is gone, the archive copy shows the same way in
the list, the viewer, search, and MCP.

Contract (orchestrator, 2026-11-27):

- The local machine identity is ``~/.claude-explorer/machine.json``
  (``canonical_home_dir()``, read at call time), never in ``data_dir``.
  ``machine_id`` is a random UUID, never the host name.
- The copy is under ``<data_dir>/machines/<machine_id>/``:
  - ``machine.json`` with ``machine_id`` and ``label``;
  - ``claude-code/projects/<project>/<uuid>.jsonl``, the agent files in
    their two layouts, and Claude Code's own ``agent-<id>.meta.json`` byte
    for byte;
  - ``cowork/<deployment>/<org>/local_<sid>/audit.jsonl`` and the sidecar
    ``local_<sid>.json``;
  - the copier's own meta and revision files in ``claude-code/sync/`` and
    ``cowork/sync/``; the locks are ``claude-code/.sync.lock`` and
    ``cowork/.sync.lock``.
- The readers read every ``machines/*/`` folder, one row per uuid, live first.
- MCP calls do not copy Claude Code or Cowork (a first copy can take
  minutes), but MCP reads the archive copies.
- An archive-only session shows no notice.

Every session here is synthetic; nothing reads a real ``~/.claude``.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import uuid as uuid_mod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from backend.tests._platform_home import patch_home

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_COWORK = Path(__file__).parent / "fixtures" / "cowork" / "d_deployment1" / "o_org1"
FIXTURE_SID = "aaaa1111-2222-3333-4444-555566667777"

PROJECT = "-tmp-a1-project"
CWD = "/tmp/a1/project"
SESSION = "a1a1a1a1-0000-4000-8000-000000000001"
SESSION_B = "a1a1a1a1-0000-4000-8000-000000000002"
OTHER_MACHINE_SESSION = "a1a1a1a1-0000-4000-8000-0000000000f1"
AGENT_CUR = "a1cur0001"
AGENT_LEG = "a1leg0001"
AGENT_DESCRIPTION = "Synthetic helper agent"
COWORK_SID = "a1c0a1c0-2222-3333-4444-555566667777"
COWORK_DEP = "d_a1dep"
COWORK_ORG = "o_a1org"
COWORK_TITLE = "A1 Cowork Title"

WORD_MAIN = "alphaarchiveword"
WORD_AGENT = "betaagentword"
WORD_LEGACY = "gammalegacyword"
WORD_COWORK = "deltacoworkword"
WORD_OTHER = "epsilonothermachine"


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


def _cc_rows(session: str, word: str, *, prefix: str = "") -> list[dict[str, Any]]:
    from backend.tests.builders import build_cc_assistant_entry, build_cc_user_entry

    return [
        build_cc_user_entry(
            uuid=f"{prefix}u1", text=f"please {word}", session_id=session, cwd=CWD,
            timestamp="2026-11-20T10:00:00Z",
        ),
        build_cc_assistant_entry(
            uuid=f"{prefix}a1", msg_id=f"{prefix}m1", text=f"done {word}", session_id=session,
            parent_uuid=f"{prefix}u1", timestamp="2026-11-20T10:00:05Z",
        ),
    ]


def _line(entry: dict[str, Any]) -> bytes:
    return (json.dumps(entry) + "\n").encode("utf-8")


def _files(*roots: Path) -> list[Path]:
    return sorted(p for root in roots for p in root.rglob("*") if p.is_file())


@dataclass
class Env:
    home: Path
    data_dir: Path
    claude_dir: Path
    cowork_root: Path

    # --- live files ---------------------------------------------------------

    @property
    def project_dir(self) -> Path:
        return self.claude_dir / "projects" / PROJECT

    @property
    def session_file(self) -> Path:
        return self.project_dir / f"{SESSION}.jsonl"

    @property
    def agent_file(self) -> Path:
        return self.project_dir / SESSION / "subagents" / f"agent-{AGENT_CUR}.jsonl"

    @property
    def agent_meta(self) -> Path:
        return self.project_dir / SESSION / "subagents" / f"agent-{AGENT_CUR}.meta.json"

    @property
    def legacy_agent_file(self) -> Path:
        return self.project_dir / f"agent-{AGENT_LEG}.jsonl"

    @property
    def cowork_org(self) -> Path:
        return self.cowork_root / COWORK_DEP / COWORK_ORG

    @property
    def cowork_dir(self) -> Path:
        return self.cowork_org / f"local_{COWORK_SID}"

    @property
    def cowork_sidecar(self) -> Path:
        return self.cowork_org / f"local_{COWORK_SID}.json"

    def write_cc(self) -> None:
        from backend.tests.builders import write_cc_jsonl

        write_cc_jsonl(self.session_file, _cc_rows(SESSION, WORD_MAIN))
        write_cc_jsonl(self.agent_file, _cc_rows(SESSION, WORD_AGENT, prefix="ag-"))
        self.agent_meta.write_text(
            json.dumps({"agentType": "general-purpose", "description": AGENT_DESCRIPTION}),
            encoding="utf-8",
        )
        write_cc_jsonl(self.legacy_agent_file, _cc_rows(SESSION, WORD_LEGACY, prefix="lg-"))

    def write_cowork(self, title: str = COWORK_TITLE) -> None:
        self.cowork_dir.mkdir(parents=True, exist_ok=True)
        audit = (FIXTURE_COWORK / f"local_{FIXTURE_SID}" / "audit.jsonl").read_text(encoding="utf-8")
        audit = audit.replace(FIXTURE_SID, COWORK_SID).replace("COWORK_FIXTURE_HELLO_XYZ", WORD_COWORK)
        (self.cowork_dir / "audit.jsonl").write_text(audit, encoding="utf-8")
        self.write_sidecar(title)

    def write_sidecar(self, title: str) -> None:
        sidecar = json.loads((FIXTURE_COWORK / f"local_{FIXTURE_SID}.json").read_text(encoding="utf-8"))
        sidecar["sessionId"] = f"local_{COWORK_SID}"
        sidecar["title"] = title
        self.cowork_sidecar.write_text(json.dumps(sidecar), encoding="utf-8")

    # --- the archive ----------------------------------------------------------

    @property
    def machines(self) -> Path:
        return self.data_dir / "machines"

    def machine_dirs(self) -> list[Path]:
        if not self.machines.is_dir():
            return []
        return sorted(p for p in self.machines.iterdir() if p.is_dir())

    def machine(self) -> Path:
        dirs = self.machine_dirs()
        assert len(dirs) == 1, f"expected one machine folder, got {[d.name for d in dirs]}"
        return dirs[0]

    def archived(self, live: Path) -> Path:
        """The archive path of a live Claude Code or Cowork file."""
        if live.is_relative_to(self.claude_dir):
            return self.machine() / "claude-code" / live.relative_to(self.claude_dir)
        return self.machine() / "cowork" / live.relative_to(self.cowork_root)

    def archive_files(self) -> dict[str, bytes]:
        if not self.machines.is_dir():
            return {}
        return {
            p.relative_to(self.machines).as_posix(): p.read_bytes()
            for p in _files(self.machines) if p.name != ".sync.lock"
        }

    # --- the app ----------------------------------------------------------------

    def store(self) -> Any:
        from backend.store import ConversationStore

        return ConversationStore()

    def sync(self, reason: str = "refresh", timeout: float = 10) -> list[Any]:
        from backend.write_gate import run_archive_syncs

        return run_archive_syncs(self.store(), reason, timeout)

    def adapter_sync(self, source_id: str, timeout: float = 10) -> Any:
        from backend import config
        from backend.archive_sync import SyncContext
        from backend.sources.registry import get_adapter

        config.get_settings.cache_clear()
        ctx = SyncContext(settings=config.get_settings(), reason="refresh", lock_timeout_s=timeout)
        return get_adapter(source_id, self.store()).archive_sync(ctx)

    def rows(self, source: str) -> dict[str, Any]:
        rows = self.store().list_conversations(source=source)
        uuids = [r.uuid for r in rows]
        assert len(uuids) == len(set(uuids)), f"duplicate rows: {uuids}"
        return {r.uuid: r for r in rows}

    def remove_live_cc(self) -> None:
        shutil.rmtree(self.project_dir)

    def remove_live_cowork(self) -> None:
        shutil.rmtree(self.cowork_dir)
        self.cowork_sidecar.unlink()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    from backend import config
    from backend import search_index as si
    from backend import summary_cache as sc
    from backend.cache import clear_cache
    from backend.store import clear_cowork_summary_cache, invalidate_detail_dict_cache

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    data_dir = tmp_path / "explorer" / "conversations"
    data_dir.mkdir(parents=True)
    claude_dir = tmp_path / "claude"
    (claude_dir / "projects").mkdir(parents=True)
    app_dir = tmp_path / "desktop"
    cowork_root = app_dir / "local-agent-mode-sessions"
    cowork_root.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(app_dir))
    index_path = tmp_path / "explorer" / "search-index.sqlite"
    monkeypatch.setattr(si, "default_index_path", lambda: index_path)
    monkeypatch.setattr(sc, "default_index_path", lambda: index_path)

    def reset() -> None:
        si.reset_search_index_for_tests()
        sc.reset_summary_cache_for_tests()
        clear_cache()
        clear_cowork_summary_cache()
        invalidate_detail_dict_cache()
        config.get_settings.cache_clear()

    reset()
    try:
        yield Env(home=home, data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root)
    finally:
        reset()


@pytest.fixture
def corpus(env: Env) -> Env:
    env.write_cc()
    env.write_cowork()
    return env


SUMMARY_FIELDS = (
    "uuid", "name", "message_count", "human_message_count", "project_path",
    "project_name", "source", "created_at", "updated_at", "git_branch", "model",
    "is_archived",
)


def _summary(row: Any) -> dict[str, Any]:
    return {f: getattr(row, f) for f in SUMMARY_FIELDS}


def _detail(store: Any, uuid: str) -> dict[str, Any]:
    detail = store.get_conversation(uuid)
    assert detail is not None, f"the viewer finds no {uuid}"
    return detail.model_dump(exclude={"file_path"})


def _texts(detail: dict[str, Any]) -> str:
    return json.dumps(detail["messages"], default=str)


def _add_cc_turn(env: Env, text: str) -> None:
    from backend.tests.builders import build_cc_user_entry

    with open(env.session_file, "ab") as fh:
        fh.write(_line(build_cc_user_entry(
            uuid="u2", text=text, session_id=SESSION, cwd=CWD,
            timestamp="2026-11-20T10:01:00Z", parent_uuid="a1",
        )))


def _add_session_b(env: Env) -> None:
    from backend.tests.builders import write_cc_jsonl

    write_cc_jsonl(env.project_dir / f"{SESSION_B}.jsonl", _cc_rows(SESSION_B, "zetaword"))


# ---------------------------------------------------------------------------
# The machine folder
# ---------------------------------------------------------------------------


def test_machine_folder_has_machine_json_with_a_random_uuid_id(corpus: Env) -> None:
    corpus.sync()

    machine = corpus.machine()
    meta = json.loads((machine / "machine.json").read_text(encoding="utf-8"))
    assert meta["machine_id"] == machine.name
    assert str(uuid_mod.UUID(machine.name)) == machine.name
    # #92 sets labels; until then a machine has none.
    assert meta["label"] is None or isinstance(meta["label"], str)
    assert machine.name != socket.gethostname()


def test_machine_identity_lives_in_the_explorer_home_not_in_data_dir(corpus: Env) -> None:
    corpus.sync()

    identity = json.loads((corpus.home / ".claude-explorer" / "machine.json").read_text(encoding="utf-8"))
    assert identity["machine_id"] == corpus.machine().name
    assert not (corpus.data_dir / "machine.json").exists()


def test_machine_folder_is_the_same_on_every_pass(corpus: Env) -> None:
    corpus.sync()
    first = corpus.machine().name
    _add_session_b(corpus)
    corpus.sync()

    assert corpus.machine().name == first
    assert (corpus.machine() / "claude-code" / "projects" / PROJECT / f"{SESSION_B}.jsonl").is_file()


def test_machine_folder_is_the_same_in_a_new_process(corpus: Env) -> None:
    script = textwrap.dedent(
        """
        from backend.store import ConversationStore
        from backend.write_gate import run_archive_syncs
        run_archive_syncs(ConversationStore(), "cli", 30)
        """
    )

    def run() -> None:
        out = subprocess.run(
            [sys.executable, "-c", script], check=False, cwd=REPO_ROOT, env=dict(os.environ),
            capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, out.stderr

    run()
    first = corpus.machine().name
    _add_session_b(corpus)
    run()

    assert corpus.machine().name == first
    assert (corpus.machine() / "claude-code" / "projects" / PROJECT / f"{SESSION_B}.jsonl").is_file()


# ---------------------------------------------------------------------------
# The first pass
# ---------------------------------------------------------------------------


def test_first_pass_copies_the_main_session_file(corpus: Env) -> None:
    corpus.sync()

    copy = corpus.archived(corpus.session_file)
    assert copy == corpus.machine() / "claude-code" / "projects" / PROJECT / f"{SESSION}.jsonl"
    assert copy.read_bytes() == corpus.session_file.read_bytes()


def test_first_pass_copies_the_current_and_the_legacy_agent_files(corpus: Env) -> None:
    corpus.sync()

    for live in (corpus.agent_file, corpus.legacy_agent_file):
        assert corpus.archived(live).read_bytes() == live.read_bytes()


def test_the_agent_meta_file_of_claude_code_is_copied_byte_for_byte(corpus: Env) -> None:
    corpus.sync()

    assert corpus.archived(corpus.agent_meta).read_bytes() == corpus.agent_meta.read_bytes()


def test_the_sync_metadata_is_not_in_the_projects_tree(corpus: Env) -> None:
    corpus.sync()

    projects = corpus.machine() / "claude-code" / "projects"
    metas = sorted(p.relative_to(projects).as_posix() for p in projects.rglob("*.meta.json"))
    assert metas == [f"{PROJECT}/{SESSION}/subagents/agent-{AGENT_CUR}.meta.json"]
    assert (corpus.machine() / "claude-code" / "sync").is_dir()


def test_first_pass_copies_a_local_cowork_session_and_its_sidecar(corpus: Env) -> None:
    corpus.sync()

    audit = corpus.cowork_dir / "audit.jsonl"
    copy = corpus.archived(audit)
    assert copy == (
        corpus.machine() / "cowork" / COWORK_DEP / COWORK_ORG / f"local_{COWORK_SID}" / "audit.jsonl"
    )
    assert copy.read_bytes() == audit.read_bytes()
    assert corpus.archived(corpus.cowork_sidecar).read_bytes() == corpus.cowork_sidecar.read_bytes()
    assert not list((corpus.machine() / "cowork" / COWORK_DEP).rglob("audit.meta.json")), (
        "the copier's meta files belong in cowork/sync/"
    )


def test_first_pass_reports_the_copied_files(corpus: Env) -> None:
    cc = corpus.adapter_sync("CLAUDE_CODE")
    cowork = corpus.adapter_sync("CLAUDE_COWORK")

    assert cc is not None and cc.wrote_any and cc.skipped_reason is None
    assert cc.counts["copied"] == 3  # the main file and two agent files
    assert cowork is not None and cowork.wrote_any and cowork.skipped_reason is None
    assert cowork.counts["copied"] >= 1


def test_the_copy_never_changes_the_live_files(corpus: Env) -> None:
    live = _files(corpus.claude_dir, corpus.cowork_root)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in live}

    corpus.sync()

    assert corpus.archived(corpus.session_file).is_file()
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in live} == before
    assert _files(corpus.claude_dir, corpus.cowork_root) == live, "the copy wrote into a live folder"


# ---------------------------------------------------------------------------
# Later passes: add and update, never remove
# ---------------------------------------------------------------------------


def test_a_second_pass_without_a_change_writes_nothing(corpus: Env) -> None:
    corpus.sync()
    before = corpus.archive_files()

    cc = corpus.adapter_sync("CLAUDE_CODE")
    cowork = corpus.adapter_sync("CLAUDE_COWORK")

    assert before, "the first pass copied nothing"
    assert not cc.wrote_any and not cowork.wrote_any
    assert {k: v for k, v in corpus.archive_files().items() if "/sync/" not in k} == {
        k: v for k, v in before.items() if "/sync/" not in k
    }


def test_a_grown_session_file_is_appended(corpus: Env) -> None:
    corpus.sync()
    _add_cc_turn(corpus, "more")
    report = corpus.adapter_sync("CLAUDE_CODE")

    assert report.counts["appended"] == 1
    assert corpus.archived(corpus.session_file).read_bytes() == corpus.session_file.read_bytes()


def test_a_grown_cowork_audit_is_appended(corpus: Env) -> None:
    corpus.sync()
    audit = corpus.cowork_dir / "audit.jsonl"
    with open(audit, "ab") as fh:
        fh.write(_line({
            "type": "user", "uuid": "u-9", "session_id": COWORK_SID, "parent_tool_use_id": None,
            "message": {"role": "user", "content": "one more turn"},
            "_audit_timestamp": "2026-05-25T10:09:00.000Z", "_audit_hmac": "00",
        }))
    corpus.sync()

    assert corpus.archived(audit).read_bytes() == audit.read_bytes()


def test_a_changed_cowork_sidecar_updates_the_archived_sidecar(corpus: Env) -> None:
    corpus.sync()
    corpus.write_sidecar("A1 Renamed Title")
    corpus.sync()

    archived = json.loads(corpus.archived(corpus.cowork_sidecar).read_text(encoding="utf-8"))
    assert archived["title"] == "A1 Renamed Title"


def test_deleted_live_files_keep_every_archive_file(corpus: Env) -> None:
    corpus.sync()
    before = corpus.archive_files()
    corpus.remove_live_cc()
    corpus.remove_live_cowork()

    corpus.sync()

    after = corpus.archive_files()
    assert before
    for name, data in before.items():
        if "/sync/" not in name:
            assert after.get(name) == data, f"{name} changed or went away"


def test_a_deleted_live_sidecar_keeps_the_archived_sidecar(corpus: Env) -> None:
    corpus.sync()
    copy = corpus.archived(corpus.cowork_sidecar)
    before = copy.read_bytes()
    corpus.cowork_sidecar.unlink()

    corpus.sync()

    assert copy.read_bytes() == before


def test_a_diverged_file_keeps_the_old_bytes_and_never_shows_a_revision(corpus: Env) -> None:
    from backend.tests.builders import write_cc_jsonl

    corpus.sync()
    old = corpus.session_file.read_bytes()
    write_cc_jsonl(corpus.session_file, _cc_rows(SESSION, "rewrittenword", prefix="rw-"))
    corpus.sync()

    sync_dir = corpus.machine() / "claude-code" / "sync"
    kept = [p for p in _files(sync_dir) if p.read_bytes() == old]
    assert kept, "the old bytes of the diverged file are not kept"
    assert list(corpus.rows("CLAUDE_CODE")) == [SESSION]

    corpus.remove_live_cc()
    assert list(corpus.rows("CLAUDE_CODE")) == [SESSION]


# ---------------------------------------------------------------------------
# Nothing to copy, no home, the lock
# ---------------------------------------------------------------------------


def test_a_pass_with_nothing_to_copy_creates_no_machines_folder(env: Env) -> None:
    env.sync()
    assert not env.machines.exists()

    env.write_cc()
    env.sync()
    assert env.archived(env.session_file).is_file()


def test_a_missing_projects_folder_gives_no_home_and_creates_nothing(env: Env) -> None:
    shutil.rmtree(env.claude_dir / "projects")

    report = env.adapter_sync("CLAUDE_CODE")

    assert report is not None and report.skipped_reason == "no_home"
    assert not env.machines.exists()


def test_a_missing_cowork_root_gives_no_home_and_creates_nothing(env: Env) -> None:
    shutil.rmtree(env.cowork_root)

    report = env.adapter_sync("CLAUDE_COWORK")

    assert report is not None and report.skipped_reason == "no_home"
    assert not env.machines.exists()


@pytest.mark.parametrize(("source_id", "folder"), [("CLAUDE_CODE", "claude-code"), ("CLAUDE_COWORK", "cowork")])
def test_a_held_lock_gives_busy_and_copies_nothing_new(corpus: Env, source_id: str, folder: str) -> None:
    from backend.archive_sync import ArchiveLock

    corpus.sync()
    before = corpus.archive_files()
    _add_session_b(corpus)
    corpus.write_sidecar("A1 Busy Title")
    lock = ArchiveLock(corpus.machine() / folder / ".sync.lock")
    assert lock.acquire(0)
    try:
        report = corpus.adapter_sync(source_id, timeout=0)
    finally:
        lock.release()

    assert report.skipped_reason == "busy"
    assert not report.wrote_any
    assert corpus.archive_files() == before


# ---------------------------------------------------------------------------
# Reading: the live file wins while it exists
# ---------------------------------------------------------------------------


def test_the_list_has_one_row_per_session_while_live_and_archive_exist(corpus: Env) -> None:
    corpus.sync()

    assert corpus.archived(corpus.session_file).is_file()
    assert list(corpus.rows("CLAUDE_CODE")) == [SESSION]
    assert list(corpus.rows("CLAUDE_COWORK")) == [COWORK_SID]


def test_the_viewer_shows_the_live_content_while_the_live_file_exists(corpus: Env) -> None:
    corpus.sync()
    _add_cc_turn(corpus, "livefreshword")

    assert "livefreshword" in _texts(_detail(corpus.store(), SESSION))
    assert b"livefreshword" not in corpus.archived(corpus.session_file).read_bytes()


def test_discover_gives_the_live_paths_while_they_exist(corpus: Env) -> None:
    from backend.sources.registry import get_adapter

    corpus.sync()
    store = corpus.store()
    cc = [Path(p) for p in get_adapter("CLAUDE_CODE", store).discover()]
    cowork = [Path(p) for p in get_adapter("CLAUDE_COWORK", store).discover()]

    assert corpus.archived(corpus.session_file).is_file()
    assert corpus.session_file in cc
    assert not [p for p in cc + cowork if p.is_relative_to(corpus.machines)]


# ---------------------------------------------------------------------------
# Reading: the archive copy shows when the live file is gone
# ---------------------------------------------------------------------------


def test_the_list_row_is_the_same_after_the_live_session_file_is_gone(corpus: Env) -> None:
    corpus.sync()
    before = _summary(corpus.rows("CLAUDE_CODE")[SESSION])
    corpus.remove_live_cc()

    rows = corpus.rows("CLAUDE_CODE")
    assert SESSION in rows
    assert _summary(rows[SESSION]) == before


def test_the_viewer_is_the_same_after_the_live_session_file_is_gone(corpus: Env) -> None:
    corpus.sync()
    before = _detail(corpus.store(), SESSION)
    corpus.remove_live_cc()

    after = _detail(corpus.store(), SESSION)
    assert after["messages"] == before["messages"]
    assert after == before
    assert after["notices"] == []


def test_the_cowork_row_and_viewer_are_the_same_after_the_live_session_is_gone(corpus: Env) -> None:
    corpus.sync()
    row = _summary(corpus.rows("CLAUDE_COWORK")[COWORK_SID])
    detail = _detail(corpus.store(), COWORK_SID)
    corpus.remove_live_cowork()

    rows = corpus.rows("CLAUDE_COWORK")
    assert COWORK_SID in rows
    assert _summary(rows[COWORK_SID]) == row
    assert rows[COWORK_SID].name == COWORK_TITLE
    assert _detail(corpus.store(), COWORK_SID) == detail


@pytest.mark.parametrize("agent_id", [AGENT_CUR, AGENT_LEG])
def test_an_agent_opens_from_the_archive_after_the_live_project_is_gone(corpus: Env, agent_id: str) -> None:
    corpus.sync()
    before = corpus.store().get_agent_conversation(SESSION, agent_id)
    assert before is not None
    corpus.remove_live_cc()

    store = corpus.store()
    assert store.find_agent(SESSION, agent_id) is not None
    after = store.get_agent_conversation(SESSION, agent_id)
    assert after is not None
    assert after.model_dump(exclude={"file_path"}) == before.model_dump(exclude={"file_path"})
    if agent_id == AGENT_CUR:
        assert after.name == AGENT_DESCRIPTION


def test_a_copy_under_another_machine_id_shows_when_no_live_file_exists(env: Env) -> None:
    from backend.tests.builders import write_cc_jsonl

    other = env.machines / str(uuid_mod.uuid4())
    other.mkdir(parents=True)
    (other / "machine.json").write_text(
        json.dumps({"machine_id": other.name, "label": "Old id"}), encoding="utf-8"
    )
    write_cc_jsonl(
        other / "claude-code" / "projects" / PROJECT / f"{OTHER_MACHINE_SESSION}.jsonl",
        _cc_rows(OTHER_MACHINE_SESSION, WORD_OTHER),
    )

    assert OTHER_MACHINE_SESSION in env.rows("CLAUDE_CODE")
    assert WORD_OTHER in _texts(_detail(env.store(), OTHER_MACHINE_SESSION))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _index(env: Env) -> Any:
    from backend import search_index as si

    idx = si.SearchIndex(env.data_dir.parent / "a1-test-index.sqlite")
    si.build_full_index(env.store(), index=idx)
    return idx


def _hits(idx: Any, word: str) -> set[str]:
    return {h["conv_uuid"] for h in idx.query(word)}


@pytest.mark.parametrize(
    ("word", "uuid", "remove"),
    [
        (WORD_MAIN, SESSION, "cc"),
        (WORD_AGENT, f"{SESSION}::agent::{AGENT_CUR}", "cc"),
        (WORD_LEGACY, f"{SESSION}::agent::{AGENT_LEG}", "cc"),
        (WORD_COWORK, COWORK_SID, "cowork"),
    ],
    ids=["main", "agent", "legacy-agent", "cowork"],
)
def test_search_still_finds_the_text_after_the_live_file_is_gone(
    corpus: Env, word: str, uuid: str, remove: str
) -> None:
    from backend import search_index as si

    corpus.sync()
    idx = _index(corpus)
    try:
        assert _hits(idx, word) == {uuid}
        if remove == "cc":
            corpus.remove_live_cc()
        else:
            corpus.remove_live_cowork()

        si.update_drifted_files(corpus.store(), index=idx)

        assert _hits(idx, word) == {uuid}
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from mcp_server import server

    monkeypatch.setattr(server, "_store", None)
    monkeypatch.setattr(server, "_settings", None)
    server.reset_archive_sync_for_tests()
    try:
        yield server
    finally:
        server.reset_archive_sync_for_tests()


def _tool(server: Any, name: str, **kwargs: Any) -> Any:
    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


def test_an_mcp_call_does_not_copy_claude_code_or_cowork(corpus: Env, mcp: Any) -> None:
    corpus.sync()
    before = corpus.archive_files()
    _add_session_b(corpus)
    corpus.write_sidecar("A1 MCP Title")

    result = _tool(mcp, "list_sessions", limit=100)

    assert before, "the refresh pass copied nothing"
    assert SESSION_B in {s["uuid"] for s in result["sessions"]}
    assert corpus.archive_files() == before


def test_the_mcp_outline_keeps_its_messages_when_only_the_mtime_changes(env: Env, mcp: Any) -> None:
    # The root cause of the empty outline, without the archive: INSERT OR REPLACE
    # on session_files cascaded into message_summaries.
    env.write_cc()
    before = _tool(mcp, "get_session_outline", conversation_id=SESSION)
    st = env.session_file.stat()
    os.utime(env.session_file, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    after = _tool(mcp, "get_session_outline", conversation_id=SESSION)

    assert len(before["messages"]) == 2
    assert after["messages"] == before["messages"]


def test_mcp_tools_show_an_archived_session_after_the_live_file_is_gone(corpus: Env, mcp: Any) -> None:
    corpus.sync()
    outline = _tool(mcp, "get_session_outline", conversation_id=SESSION)
    messages = _tool(mcp, "get_messages", conversation_id=SESSION)
    corpus.remove_live_cc()
    corpus.remove_live_cowork()
    mcp.reset_archive_sync_for_tests()

    listed = {s["uuid"] for s in _tool(mcp, "list_sessions", limit=100)["sessions"]}
    assert {SESSION, COWORK_SID} <= listed
    assert _tool(mcp, "get_session_outline", conversation_id=SESSION) == outline
    assert _tool(mcp, "get_messages", conversation_id=SESSION) == messages
    assert _tool(mcp, "get_messages", conversation_id=COWORK_SID)


# ---------------------------------------------------------------------------
# Triggers and the cloud-sync gate
# ---------------------------------------------------------------------------


def test_the_watcher_pass_copies_claude_code_and_cowork(corpus: Env) -> None:
    from backend import cc_watcher

    cc_watcher._archive_sync_pass("watcher")

    assert corpus.archived(corpus.session_file).is_file()
    assert corpus.archived(corpus.cowork_dir / "audit.jsonl").is_file()


def test_the_backstop_reason_copies_claude_code_and_cowork(corpus: Env) -> None:
    corpus.sync("backstop", 0)

    assert corpus.archived(corpus.session_file).is_file()
    assert corpus.archived(corpus.cowork_dir / "audit.jsonl").is_file()


def test_the_cloud_sync_gate_blocks_the_copy_until_the_opt_in(
    monkeypatch: pytest.MonkeyPatch, env: Env
) -> None:
    from backend import config

    (env.home / "Dropbox").mkdir()
    data_dir = env.home / "Dropbox" / "explorer" / "conversations"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    config.get_settings.cache_clear()
    env.data_dir = data_dir
    env.write_cc()
    env.write_cowork()

    reports = env.sync()
    assert [r.skipped_reason for r in reports] == ["cloud_sync_gate"]
    assert not env.machines.exists()

    cfg = env.home / ".claude-explorer" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"allow_writes_in_cloud_sync": os.path.realpath(data_dir)}), encoding="utf-8")
    config.get_settings.cache_clear()
    env.sync()

    assert env.archived(env.session_file).is_file()
    assert env.archived(env.cowork_dir / "audit.jsonl").is_file()
