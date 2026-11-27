"""Two or more synthetic machines for the A2 tests (issue #92).

Each :class:`Machine` has its own home folder (so its own
``~/.claude-explorer/machine.json``), its own ``data_dir``, Claude Code
folder, Cowork root, and Codex home. :meth:`Machine.activate` points the
process at one machine: the environment, the search index path, and every
cache. A test that switches machines calls ``activate`` first.

Contract (orchestrator, 2026-11-27), the names that every A2 test uses:

- ``backend.machines``: ``ExportRules``, ``AllowRule``, ``load_export_rules``,
  ``save_export_rules``, ``preview_export``, ``export_bundle``, ``import_bundle``,
  ``BundleError`` (``.reason``), ``set_machine_label``, ``list_machines``,
  ``sync_shared_folder``, ``remove_own_shared_folder``.
- Files in ``~/.claude-explorer/``: ``export-rules.json``, ``export-state.json``,
  ``machine-labels.json`` (local names of other machines), ``config.json``
  (``shared_archive_dir``, ``allow_writes_in_shared_archive``).
- A foreign machine's copy is ``<data_dir>/machines/<id>/``; this machine's
  Codex copy stays in ``<data_dir>/codex/<home_key>/``.

Every value here is synthetic. Nothing reads a real home folder.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests._platform_home import patch_home

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_COWORK = (
    Path(__file__).parent / "fixtures" / "cowork" / "d_deployment1" / "o_org1"
)
FIXTURE_SID = "aaaa1111-2222-3333-4444-555566667777"

COWORK_DEP = "d_a2dep"
COWORK_ORG = "o_a2org"


def project_folder(cwd: str) -> str:
    """Claude Code's project folder name for a cwd."""
    return cwd.replace("\\", "-").replace("/", "-").replace(":", "-").replace(".", "-")


def cc_entries(
    session: str, word: str, *, cwd: str, ts: str, prefix: str = ""
) -> list[dict[str, Any]]:
    from backend.tests.builders import build_cc_assistant_entry, build_cc_user_entry

    later = (
        (datetime.fromisoformat(ts) + timedelta(seconds=5))
        .isoformat()
        .replace("+00:00", "Z")
    )
    return [
        build_cc_user_entry(
            uuid=f"{prefix}u1-{session[-4:]}",
            text=f"please {word}",
            session_id=session,
            cwd=cwd,
            timestamp=ts,
        ),
        build_cc_assistant_entry(
            uuid=f"{prefix}a1-{session[-4:]}",
            msg_id=f"{prefix}m1-{session[-4:]}",
            text=f"done {word}",
            session_id=session,
            parent_uuid=f"{prefix}u1-{session[-4:]}",
            timestamp=later,
        ),
    ]


def line(entry: dict[str, Any]) -> bytes:
    return (json.dumps(entry) + "\n").encode("utf-8")


def tree(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` (relative posix path -> bytes)."""
    if not root.is_dir():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def archived(root: Path) -> dict[str, bytes]:
    """:func:`tree` without the push bookkeeping (``machines/<id>/shared-push/``).

    Removal from the shared folder also drops that bookkeeping, so that the
    next push starts again. It is not archive data.
    """
    return {k: v for k, v in tree(root).items() if "/shared-push/" not in f"/{k}"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reset_caches() -> None:
    from backend import config
    from backend import search_index as si
    from backend import summary_cache as sc
    from backend.cache import clear_cache
    from backend.sources.codex import clear_codex_caches
    from backend.sources.registry import reset_for_tests
    from backend.store import clear_cowork_summary_cache, invalidate_detail_dict_cache

    si.reset_search_index_for_tests()
    sc.reset_summary_cache_for_tests()
    clear_cache()
    clear_cowork_summary_cache()
    invalidate_detail_dict_cache()
    clear_codex_caches()
    reset_for_tests()
    config.get_settings.cache_clear()


@dataclass
class Machine:
    name: str
    root: Path
    mp: pytest.MonkeyPatch
    codex_home: Path
    home: Path = field(init=False)
    data_dir: Path = field(init=False)
    claude_dir: Path = field(init=False)
    app_dir: Path = field(init=False)
    cowork_root: Path = field(init=False)
    index_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.home = self.root / "home"
        self.data_dir = self.root / "explorer" / "conversations"
        self.claude_dir = self.root / "claude"
        self.app_dir = self.root / "desktop"
        self.cowork_root = self.app_dir / "local-agent-mode-sessions"
        self.index_path = self.root / "explorer" / "search-index.sqlite"
        for p in (
            self.home,
            self.data_dir,
            self.claude_dir / "projects",
            self.cowork_root,
            self.codex_home,
        ):
            p.mkdir(parents=True, exist_ok=True)

    # --- the process ------------------------------------------------------

    def activate(self) -> Machine:
        from backend import search_index as si
        from backend import summary_cache as sc

        patch_home(self.mp, self.home)
        self.mp.setenv("CLAUDE_EXPLORER_DATA_DIR", str(self.data_dir))
        self.mp.setenv("CLAUDE_DIR", str(self.claude_dir))
        self.mp.setenv("CLAUDE_DESKTOP_APP_DIR", str(self.app_dir))
        self.mp.setenv("CODEX_HOME", str(self.codex_home))
        self.mp.setattr(si, "default_index_path", lambda: self.index_path)
        self.mp.setattr(sc, "default_index_path", lambda: self.index_path)
        reset_caches()
        return self

    def settings(self) -> Any:
        from backend import config

        self.activate()
        return config.get_settings()

    def store(self) -> Any:
        from backend.store import ConversationStore

        return ConversationStore()

    def sync(self, reason: str = "refresh", timeout: float = 10) -> list[Any]:
        """The archive copy of every source (and, with A2, the sync folder)."""
        from backend.write_gate import run_archive_syncs

        self.activate()
        return run_archive_syncs(self.store(), reason, timeout)

    # --- this machine's identity and settings ---------------------------------

    @property
    def explorer_home(self) -> Path:
        return self.home / ".claude-explorer"

    @property
    def machine_id(self) -> str:
        data = json.loads(
            (self.explorer_home / "machine.json").read_text(encoding="utf-8")
        )
        return data["machine_id"]

    def ensure_id(self) -> str:
        from backend.archive_sync import this_machine_dir

        self.activate()
        machine = this_machine_dir(self.data_dir)
        assert machine is not None
        return machine.name

    def write_rules(self, **rules: Any) -> Path:
        body = {"version": 1, **rules}
        path = self.explorer_home / "export-rules.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def export_all(self, **extra: Any) -> Path:
        """Rules that send every session of every source."""
        return self.write_rules(mode="export", allow=[{}], **extra)

    def write_config(self, **keys: Any) -> Path:
        path = self.explorer_home / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        old.update(keys)
        path.write_text(json.dumps(old), encoding="utf-8")
        return path

    def write_corrupt_config(self) -> Path:
        path = self.explorer_home / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")
        return path

    # --- the live sources --------------------------------------------------------

    def cwd(self, *parts: str) -> str:
        return str(self.home.joinpath(*parts))

    def cc_file(self, session: str, cwd: str) -> Path:
        return self.claude_dir / "projects" / project_folder(cwd) / f"{session}.jsonl"

    def cc_session(
        self,
        session: str,
        word: str,
        *,
        cwd: str | None = None,
        ts: str = "2026-11-20T10:00:00Z",
    ) -> Path:
        from backend.tests.builders import write_cc_jsonl

        cwd = cwd or self.cwd("Source", "personal", "app")
        return write_cc_jsonl(
            self.cc_file(session, cwd), cc_entries(session, word, cwd=cwd, ts=ts)
        )

    def cc_agent(
        self, session: str, agent: str, word: str, *, cwd: str
    ) -> tuple[Path, Path]:
        from backend.tests.builders import write_cc_jsonl

        folder = self.cc_file(session, cwd).with_suffix("") / "subagents"
        path = write_cc_jsonl(
            folder / f"agent-{agent}.jsonl",
            cc_entries(session, word, cwd=cwd, ts="2026-11-20T10:00:10Z", prefix="ag-"),
        )
        meta = folder / f"agent-{agent}.meta.json"
        meta.write_text(
            json.dumps(
                {"agentType": "general-purpose", "description": "Synthetic agent"}
            ),
            encoding="utf-8",
        )
        return path, meta

    def cc_add_turn(self, session: str, text: str, *, cwd: str | None = None) -> Path:
        from backend.tests.builders import build_cc_user_entry

        cwd = cwd or self.cwd("Source", "personal", "app")
        path = self.cc_file(session, cwd)
        with open(path, "ab") as fh:
            fh.write(
                line(
                    build_cc_user_entry(
                        uuid=f"u2-{session[-4:]}",
                        text=text,
                        session_id=session,
                        cwd=cwd,
                        timestamp="2026-11-20T10:01:00Z",
                        parent_uuid=f"a1-{session[-4:]}",
                    )
                )
            )
        return path

    def cowork_session(
        self, sid: str, word: str, title: str = "A2 Cowork Title"
    ) -> Path:
        org = self.cowork_root / COWORK_DEP / COWORK_ORG
        folder = org / f"local_{sid}"
        folder.mkdir(parents=True, exist_ok=True)
        audit = (FIXTURE_COWORK / f"local_{FIXTURE_SID}" / "audit.jsonl").read_text(
            encoding="utf-8"
        )
        audit = audit.replace(FIXTURE_SID, sid).replace(
            "COWORK_FIXTURE_HELLO_XYZ", word
        )
        (folder / "audit.jsonl").write_text(audit, encoding="utf-8")
        sidecar = json.loads(
            (FIXTURE_COWORK / f"local_{FIXTURE_SID}.json").read_text(encoding="utf-8")
        )
        sidecar["sessionId"] = f"local_{sid}"
        sidecar["title"] = title
        (org / f"local_{sid}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        return folder

    def codex_thread(
        self,
        thread_id: str,
        word: str,
        *,
        cwd: str = "/home/fixture/proj-a2",
        day: str = "2026-11-15",
    ) -> Path:
        from backend.tests import codex_fake as cf

        t0 = datetime.fromisoformat(f"{day}T09:00:00+00:00")
        return cf.write_rollout(
            cf.rollout_path(self.codex_home, t0, thread_id),
            cf._simple_paginated_lines(
                t0, thread_id, cwd, f"Codex asks {word}", f"Codex answers {word}"
            ),
        )

    # --- reading ---------------------------------------------------------------------

    def rows(self, source: str = "all") -> dict[str, Any]:
        self.activate()
        rows = self.store().list_conversations(source=source)
        uuids = [r.uuid for r in rows]
        assert len(uuids) == len(set(uuids)), f"duplicate rows: {uuids}"
        return {r.uuid: r for r in rows}

    def detail_text(self, uuid: str) -> str:
        self.activate()
        detail = self.store().get_conversation(uuid)
        assert detail is not None, f"the viewer finds no {uuid}"
        return json.dumps(
            detail.model_dump(exclude={"file_path"})["messages"], default=str
        )

    def hits(self, word: str) -> set[str]:
        from backend import search_index as si

        self.activate()
        idx = si.SearchIndex(self.root / "a2-test-index.sqlite")
        try:
            si.build_full_index(self.store(), index=idx)
            return {h["conv_uuid"] for h in idx.query(word)}
        finally:
            idx.close()

    def foreign(self, machine_id: str) -> Path:
        """This machine's archive copy of another machine."""
        return self.data_dir / "machines" / machine_id

    # --- bundles -------------------------------------------------------------------------

    def export(self, dest: Path, **kw: Any) -> Any:
        from backend import machines

        self.activate()
        return machines.export_bundle(dest, **kw)

    def import_(self, src: Path, **kw: Any) -> Any:
        from backend import machines

        self.activate()
        return machines.import_bundle(src, **kw)

    def preview(self, **kw: Any) -> Any:
        from backend import machines

        self.activate()
        return machines.preview_export(**kw)


def bundle_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


def bundle_manifest(path: Path) -> tuple[str, dict[str, Any]]:
    """``(machine folder prefix, manifest)`` of a plain bundle."""
    members = bundle_members(path)
    names = [
        n
        for n in members
        if n.endswith("/manifest.json")
        and n.count("/") == 2
        and n.startswith("machines/")
    ]
    assert len(names) == 1, (
        f"one machines/<id>/manifest.json expected, got {sorted(members)}"
    )
    prefix = names[0].removesuffix("manifest.json")
    return prefix, json.loads(members[names[0]])


def member_of(prefix: str, manifest_path: str) -> str:
    """The zip member name of a manifest path (relative to the machine folder)."""
    return (
        manifest_path
        if manifest_path.startswith("machines/")
        else prefix + manifest_path
    )


def rewrite_member(path: Path, name: str, data: bytes) -> None:
    """Replace one member of a zip bundle (a tampered bundle)."""
    members = bundle_members(path)
    assert name in members, f"{name} not in {sorted(members)}"
    members[name] = data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in members.items():
            zf.writestr(n, d)
    path.write_bytes(buf.getvalue())


@dataclass
class World:
    a: Machine
    b: Machine
    c: Machine
    shared: Path
    tmp: Path


@contextmanager
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """Three machines (A is the user's Mac, B the work laptop, C a third) and one shared folder.

    Each machine has its own Codex home. A test that needs one Codex home
    path on two machines sets ``codex_home`` itself.
    """
    shared = tmp_path / "shared-archive"
    shared.mkdir()
    a = Machine(
        "a", tmp_path / "machine-a", monkeypatch, tmp_path / "machine-a" / "codex"
    )
    b = Machine(
        "b", tmp_path / "machine-b", monkeypatch, tmp_path / "machine-b" / "codex"
    )
    c = Machine(
        "c", tmp_path / "machine-c", monkeypatch, tmp_path / "machine-c" / "codex"
    )
    try:
        yield World(a=a, b=b, c=c, shared=shared, tmp=tmp_path)
    finally:
        reset_caches()


def no_sqlite(root: Path) -> list[str]:
    return [
        p.as_posix()
        for p in root.rglob("*")
        if ".sqlite" in p.name or p.suffix in (".db",)
    ]


def realpath(p: Path | str) -> str:
    return os.path.realpath(p)
