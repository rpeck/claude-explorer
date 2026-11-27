"""A2 (issue #92): the export rules and the preview.

Nothing leaves a machine by default. ``~/.claude-explorer/export-rules.json``
(never in ``data_dir``) sets the mode (off, export, receive-only), the allow
rules (source and project glob), the deny globs (they always win), and a
date floor. The preview lists exactly what a bundle holds.

Contract: ``backend/tests/_machines_env.py``. Synthetic sessions only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests._machines_env import (
    World,
    bundle_manifest,
    bundle_members,
    make_world,
    member_of,
    project_folder,
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with make_world(tmp_path, monkeypatch) as w:
        yield w


S_PERSONAL = "a2a2a2a2-0000-4000-8000-000000000001"
S_WORK = "a2a2a2a2-0000-4000-8000-000000000002"
S_OLD = "a2a2a2a2-0000-4000-8000-000000000003"
COWORK_SID = "a2c0a2c0-2222-3333-4444-555566667777"
THREAD = "019b0a20-0000-7000-8000-0000000000a2"


def _paths(preview: Any) -> set[str]:
    return {f.path for f in preview.files}


def _has(paths: set[str], suffix: str) -> bool:
    return any(p.endswith(suffix) for p in paths)


def _corpus(world: World) -> None:
    b = world.b
    b.cc_session(S_PERSONAL, "personalword", cwd=b.cwd("Source", "personal", "app"))
    b.cc_session(S_WORK, "workword", cwd=b.cwd("Source", "work", "api"))
    b.cowork_session(COWORK_SID, "coworkword")
    b.codex_thread(THREAD, "codexword")
    b.sync()


def _cc_suffix(world: World, session: str, *parts: str) -> str:
    return f"{project_folder(world.b.cwd(*parts))}/{session}.jsonl"


# ---------------------------------------------------------------------------
# The rules file
# ---------------------------------------------------------------------------


def test_rules_default_to_off_when_the_file_is_missing(world: World) -> None:
    from backend import machines

    world.b.activate()
    rules = machines.load_export_rules()

    assert rules.mode == "off"
    assert rules.allow == []
    assert rules.deny == []
    assert rules.since is None


def test_a_rules_file_that_does_not_parse_gives_the_defaults(world: World) -> None:
    from backend import machines

    path = world.b.explorer_home / "export-rules.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    world.b.activate()

    assert machines.load_export_rules().mode == "off"


def test_rules_are_saved_in_the_explorer_home_not_in_data_dir(world: World) -> None:
    from backend import machines

    world.b.activate()
    rules = machines.ExportRules(
        mode="export",
        allow=[machines.AllowRule(source="codex", project="~/Source/**")],
        deny=["~/Source/work/**"],
        since="2026-01-01",
    )
    machines.save_export_rules(rules)

    saved = json.loads(
        (world.b.explorer_home / "export-rules.json").read_text(encoding="utf-8")
    )
    assert saved["mode"] == "export"
    assert saved["deny"] == ["~/Source/work/**"]
    assert not list(world.b.data_dir.rglob("export-rules.json"))
    again = machines.load_export_rules()
    assert again.mode == "export"
    assert again.allow[0].source == "codex"
    assert again.allow[0].project == "~/Source/**"
    assert again.since == "2026-01-01"


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def test_mode_off_previews_nothing(world: World) -> None:
    _corpus(world)
    world.b.write_rules(mode="off", allow=[{}])

    preview = world.b.preview()

    assert preview.file_count == 0
    assert preview.files == []
    assert preview.total_bytes == 0


@pytest.mark.parametrize("mode", ["off", "receive-only"])
def test_export_is_refused_unless_the_mode_is_export(world: World, mode: str) -> None:
    from backend import machines

    _corpus(world)
    world.b.write_rules(mode=mode, allow=[{}])
    dest = world.tmp / "out.cebundle"

    with pytest.raises(machines.BundleError) as err:
        world.b.export(dest)

    assert err.value.reason == "export_off"
    assert not dest.exists()


def test_default_rules_refuse_export(world: World) -> None:
    from backend import machines

    _corpus(world)

    with pytest.raises(machines.BundleError) as err:
        world.b.export(world.tmp / "out.cebundle")

    assert err.value.reason == "export_off"


# ---------------------------------------------------------------------------
# Allow, deny, since
# ---------------------------------------------------------------------------


def test_allow_by_source_picks_only_that_source(world: World) -> None:
    _corpus(world)
    world.b.write_rules(mode="export", allow=[{"source": "claude-code"}])

    preview = world.b.preview()

    assert {f.source for f in preview.files} == {"claude-code"}
    paths = _paths(preview)
    assert _has(paths, _cc_suffix(world, S_PERSONAL, "Source", "personal", "app"))
    assert _has(paths, _cc_suffix(world, S_WORK, "Source", "work", "api"))


def test_allow_by_project_glob_expands_the_tilde(world: World) -> None:
    _corpus(world)
    world.b.write_rules(mode="export", allow=[{"project": "~/Source/personal/**"}])

    paths = _paths(world.b.preview())

    assert _has(paths, _cc_suffix(world, S_PERSONAL, "Source", "personal", "app"))
    assert not _has(paths, _cc_suffix(world, S_WORK, "Source", "work", "api"))


def test_an_allow_rule_matches_only_when_source_and_project_both_match(
    world: World,
) -> None:
    _corpus(world)
    world.b.write_rules(
        mode="export",
        allow=[{"source": "claude-code", "project": "~/Source/personal/**"}],
    )

    preview = world.b.preview()

    assert {f.source for f in preview.files} == {"claude-code"}
    paths = _paths(preview)
    assert _has(paths, _cc_suffix(world, S_PERSONAL, "Source", "personal", "app"))
    assert not _has(paths, _cc_suffix(world, S_WORK, "Source", "work", "api"))


def test_a_deny_glob_wins_over_every_allow_rule(world: World) -> None:
    _corpus(world)
    world.b.write_rules(
        mode="export",
        allow=[{}, {"source": "claude-code", "project": "~/Source/work/**"}],
        deny=["~/Source/work/**"],
    )

    paths = _paths(world.b.preview())

    assert _has(paths, _cc_suffix(world, S_PERSONAL, "Source", "personal", "app"))
    assert not _has(paths, _cc_suffix(world, S_WORK, "Source", "work", "api"))


def test_since_drops_a_session_whose_last_record_is_older(world: World) -> None:
    b = world.b
    b.cc_session(S_PERSONAL, "newword", ts="2026-11-20T10:00:00Z")
    b.cc_session(S_OLD, "oldword", ts="2025-12-01T10:00:00Z")
    b.sync()
    b.write_rules(mode="export", allow=[{}], since="2026-01-01")

    paths = _paths(b.preview())

    assert _has(paths, f"/{S_PERSONAL}.jsonl")
    assert not _has(paths, f"/{S_OLD}.jsonl")


# ---------------------------------------------------------------------------
# What one session carries
# ---------------------------------------------------------------------------


def test_a_claude_code_session_goes_with_its_agent_files_and_meta(world: World) -> None:
    b = world.b
    cwd = b.cwd("Source", "personal", "app")
    b.cc_session(S_PERSONAL, "mainword", cwd=cwd)
    b.cc_agent(S_PERSONAL, "a2agent01", "agentword", cwd=cwd)
    b.sync()
    b.export_all()

    paths = _paths(b.preview())

    folder = project_folder(cwd)
    assert _has(paths, f"{folder}/{S_PERSONAL}.jsonl")
    assert _has(paths, f"{folder}/{S_PERSONAL}/subagents/agent-a2agent01.jsonl")
    assert _has(paths, f"{folder}/{S_PERSONAL}/subagents/agent-a2agent01.meta.json")


def test_a_cowork_session_goes_with_its_sidecar(world: World) -> None:
    _corpus(world)
    world.b.write_rules(mode="export", allow=[{"source": "cowork"}])

    preview = world.b.preview()

    assert {f.source for f in preview.files} == {"cowork"}
    paths = _paths(preview)
    assert _has(paths, f"local_{COWORK_SID}/audit.jsonl")
    assert _has(paths, f"local_{COWORK_SID}.json")


def test_a_codex_thread_goes_with_its_thread_file_and_rollout(world: World) -> None:
    _corpus(world)
    world.b.write_rules(mode="export", allow=[{"source": "codex"}])

    preview = world.b.preview()

    assert {f.source for f in preview.files} == {"codex"}
    paths = _paths(preview)
    assert all(p.startswith("codex/") for p in paths), paths
    assert _has(paths, f"threads/{THREAD}.json")
    assert any(
        "/rollouts/" in p and THREAD in p and p.endswith(".jsonl") for p in paths
    ), paths


def test_the_preview_never_holds_copier_metadata_or_locks(world: World) -> None:
    _corpus(world)
    world.b.export_all()

    paths = _paths(world.b.preview())

    assert paths
    assert not [p for p in paths if "/sync/" in f"/{p}" or p.endswith(".sync.lock")]
    # The copier's own meta files hold absolute source paths; Claude Code's agent meta stays.
    assert not [
        p for p in paths if p.endswith(".meta.json") and "/subagents/agent-" not in p
    ]


def test_preview_entries_name_their_source_project_and_size(world: World) -> None:
    _corpus(world)
    world.b.write_rules(
        mode="export",
        allow=[{"source": "claude-code", "project": "~/Source/personal/**"}],
    )

    preview = world.b.preview()

    [entry] = [f for f in preview.files if f.path.endswith(f"/{S_PERSONAL}.jsonl")]
    assert entry.source == "claude-code"
    assert entry.project == world.b.cwd("Source", "personal", "app")
    assert entry.bytes == world.b.cc_file(S_PERSONAL, entry.project).stat().st_size
    assert preview.file_count == len(preview.files)
    assert preview.total_bytes == sum(f.bytes for f in preview.files)


def test_the_preview_equals_the_bundle(world: World) -> None:
    _corpus(world)
    world.b.export_all(deny=["~/Source/work/**"])

    preview = world.b.preview()
    dest = world.tmp / "b.cebundle"
    world.b.export(dest)

    prefix, manifest = bundle_manifest(dest)
    members = bundle_members(dest)
    assert {(f.path, f.bytes) for f in preview.files} == {
        (f["path"], f["bytes"]) for f in manifest["files"]
    }
    for f in manifest["files"]:
        assert len(members[member_of(prefix, f["path"])]) == f["bytes"]
