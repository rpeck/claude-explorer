"""N6: the Codex shape report (``claude-explorer codex-report``) and the
``codex-verification`` issue template.

PLANS/2026.11.18-codex-source.md §9 and §5 (N6). The report runs on work
machines, so its output holds counts and fixed names only: no message text,
no path, no title, and no id. It is read-only: it writes nothing anywhere.
The ``archive`` part is a dry run of the copier (§4.2) against the archive.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from backend.tests import codex_fake as cf
from backend.tests._platform_home import patch_home

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / ".github" / "ISSUE_TEMPLATE" / "codex-verification.yml"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")
CHECKLIST = ("source_filter", "project_grouping", "search_jump", "detail_controls", "export", "mcp_tools")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A fake user home, Codex home, data_dir, and temp folder, all under tmp_path."""
    from backend import config

    user_home = patch_home(monkeypatch, tmp_path / "user")
    user_home.mkdir()
    codex = tmp_path / "codex-home"
    data = tmp_path / "data"
    data.mkdir()
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    config.get_settings.cache_clear()
    yield SimpleNamespace(root=tmp_path, user=user_home, codex=codex, data=data, tmp=tmp)
    config.get_settings.cache_clear()


@pytest.fixture
def corpus(env: SimpleNamespace) -> SimpleNamespace:
    cf.build_codex_home(env.codex)
    return env


def build(env: SimpleNamespace) -> dict[str, Any]:
    from cli.codex_report import build_report

    return build_report(env.codex, env.data)


def run_cli(*args: str) -> Any:
    from cli.main import main

    return CliRunner().invoke(main, ["codex-report", *args], catch_exceptions=False)


def nonzero(counts: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in counts.items() if v}


def home_rollouts(home: Path) -> dict[str, Path]:
    """stem -> the file that the copier reads (the plain file wins over .zst)."""
    out: dict[str, Path] = {}
    for folder in (home / "sessions", home / "archived_sessions"):
        for path in sorted(folder.rglob("rollout-*")):
            name = path.name
            stem = name.removesuffix(".zst").removesuffix(".jsonl")
            if stem not in out or name.endswith(".jsonl"):
                out[stem] = path
    return out


def decoded(path: Path) -> bytes:
    from backend.codex.zstd import decompress

    data = path.read_bytes()
    return decompress(data) if path.suffix == ".zst" else data


def records(home: Path) -> list[dict[str, Any]]:
    """Every parsable record of every rollout (one file per stem)."""
    out = []
    for path in home_rollouts(home).values():
        for line in decoded(path).splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("type"), str):
                out.append(obj)
    return out


def archive_dir(env: SimpleNamespace) -> Path:
    from backend.archive_sync import home_key

    return env.data / "codex" / home_key(env.codex) / "rollouts"


def write_archive(env: SimpleNamespace) -> Path:
    """An up-to-date archive copy of each home rollout (§4.2 layout)."""
    dest = archive_dir(env)
    dest.mkdir(parents=True)
    for stem, path in home_rollouts(env.codex).items():
        (dest / f"{stem}.jsonl").write_bytes(decoded(path))
    return dest


def snapshot(root: Path) -> dict[str, Any]:
    """Every folder and file under root, with size, mtime, and content hash."""
    out: dict[str, Any] = {}
    for dirpath, dirs, files in os.walk(root):
        for d in dirs:
            out[os.path.relpath(os.path.join(dirpath, d), root)] = "dir"
        for f in files:
            p = Path(dirpath) / f
            st = p.stat()
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


T0 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def extra_rollout(
    env: SimpleNamespace, tid: str, lines: list[Any], *, when: datetime = T0, zst: bool = False
) -> Path:
    return cf.write_rollout(cf.rollout_path(env.codex, when, tid, zst=zst), lines)


def simple_lines(tid: str, when: datetime = T0, *extra: Any) -> list[Any]:
    s = lambda n: when + timedelta(seconds=n)
    return [
        cf.session_meta(when, tid, cwd="/home/fixture/proj-x", history_mode="paginated"),
        cf.turn_context(s(1), "turn-x1", cwd="/home/fixture/proj-x", model="gpt-5.5-codex"),
        cf.item_completed(s(2), tid, "turn-x1", cf.user_message_item("item-x1-user", "Hello.")),
        cf.item_completed(s(3), tid, "turn-x1", cf.agent_message_item("item-x1-reply", "Hi.")),
        *extra,
    ]


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def test_report_version_is_1_and_the_home_is_found(corpus: SimpleNamespace) -> None:
    report = build(corpus)
    assert report["report_version"] == 1
    assert report["home_found"] is True


def test_rollouts_are_counted_by_form_and_folder(corpus: SimpleNamespace) -> None:
    assert build(corpus)["rollouts"] == {"total": 6, "plain": 4, "zst": 2, "archived": 1}


def test_threads_are_counted(corpus: SimpleNamespace) -> None:
    assert build(corpus)["threads"] == 6


def test_both_forms_of_one_stem_count_once_and_the_plain_file_wins(corpus: SimpleNamespace) -> None:
    before = build(corpus)
    plain = cf.rollout_path(corpus.codex, datetime(2026, 11, 10, 9, 0, tzinfo=UTC), cf.THREAD_PAGINATED)
    assert plain.exists()
    Path(str(plain) + ".zst").write_bytes(cf.zstd_compress(plain.read_bytes()))

    after = build(corpus)
    assert after["rollouts"] == before["rollouts"]
    assert after["sizes"] == before["sizes"]


def test_cli_versions_count_each_rollout(corpus: SimpleNamespace) -> None:
    assert build(corpus)["cli_versions"] == {cf.CLI_VERSION: 6}


def test_history_modes_count_each_thread(corpus: SimpleNamespace) -> None:
    assert nonzero(build(corpus)["history_modes"]) == {"paginated": 5, "legacy": 1}


def test_the_state_db_history_mode_wins_over_the_rollout(corpus: SimpleNamespace) -> None:
    conn = sqlite3.connect(corpus.codex / "state_5.sqlite")
    try:
        conn.execute("UPDATE threads SET history_mode = 'paginated' WHERE id = ?", (cf.THREAD_LEGACY,))
        conn.commit()
    finally:
        conn.close()
    assert nonzero(build(corpus)["history_modes"]) == {"paginated": 6}


def test_record_types_count_every_record(corpus: SimpleNamespace) -> None:
    expected = Counter(r["type"] for r in records(corpus.codex))
    assert build(corpus)["record_types"] == dict(expected)


def test_item_types_count_every_item_completed_record(corpus: SimpleNamespace) -> None:
    expected = Counter(
        r["payload"]["item"]["type"]
        for r in records(corpus.codex)
        if r["type"] == "event_msg" and r["payload"].get("type") == "item_completed"
    )
    assert expected["HologramCall"] == 1
    assert build(corpus)["item_types"] == dict(expected)


def test_unknown_item_types_hold_only_types_outside_the_19_codex_types(corpus: SimpleNamespace) -> None:
    # Plan is one of the 19 TurnItem types (§3), so it is known.
    assert nonzero(build(corpus)["unknown_item_types"]) == {"HologramCall": 1}


def test_the_default_corpus_has_no_unknown_record_type(corpus: SimpleNamespace) -> None:
    assert nonzero(build(corpus)["unknown_record_types"]) == {}


def test_an_unknown_record_type_is_listed(corpus: SimpleNamespace) -> None:
    tid = "019a9000-0000-7000-8000-000000000101"
    extra_rollout(corpus, tid, simple_lines(tid, T0, cf.rollout_line(T0, "hologram_record", {"beam": "red"})))
    report = build(corpus)
    assert nonzero(report["unknown_record_types"]) == {"hologram_record": 1}
    assert report["record_types"]["hologram_record"] == 1


def test_an_invalid_record_type_name_counts_as_invalid(corpus: SimpleNamespace) -> None:
    tid = "019a9000-0000-7000-8000-000000000102"
    bad = "bad type / with a space"
    extra_rollout(corpus, tid, simple_lines(tid, T0, cf.rollout_line(T0, bad, {})))
    report = build(corpus)
    assert report["record_types"]["invalid"] == 1
    assert bad not in json.dumps(report)


def test_an_invalid_item_type_name_counts_as_invalid(corpus: SimpleNamespace) -> None:
    tid = "019a9000-0000-7000-8000-000000000103"
    bad = "Bad Item/Type"
    extra_rollout(
        corpus, tid,
        simple_lines(tid, T0, cf.item_completed(T0, tid, "turn-x1", {"type": bad, "id": "item-x1-bad"})),
    )
    report = build(corpus)
    assert report["item_types"]["invalid"] == 1
    assert bad not in json.dumps(report)


def test_an_invalid_cli_version_counts_as_invalid(corpus: SimpleNamespace) -> None:
    tid = "019a9000-0000-7000-8000-000000000104"
    lines = simple_lines(tid)
    lines[0]["payload"]["cli_version"] = "not a version /x"
    extra_rollout(corpus, tid, lines)
    report = build(corpus)
    assert report["cli_versions"] == {cf.CLI_VERSION: 6, "invalid": 1}


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_warnings_are_grouped_by_kind_with_counts(corpus: SimpleNamespace) -> None:
    # 2 damaged lines in the paginated rollout, 1 in the legacy one;
    # Plan and HologramCall are not mapped in slice 1.
    assert nonzero(build(corpus)["warnings"]) == {"damaged_line": 3, "unsupported_item": 2}


def test_a_missing_state_db_is_one_state_db_warning(corpus: SimpleNamespace) -> None:
    (corpus.codex / "state_5.sqlite").unlink()
    report = build(corpus)
    assert report["warnings"]["state_db"] == 1
    # The rollouts still give the history mode.
    assert nonzero(report["history_modes"]) == {"paginated": 5, "legacy": 1}


def test_an_unreadable_session_index_is_a_session_index_warning(corpus: SimpleNamespace) -> None:
    index = corpus.codex / "session_index.jsonl"
    index.write_bytes(index.read_bytes() + b"{broken\n")
    assert build(corpus)["warnings"]["session_index"] == 1


def test_an_unreadable_import_list_is_an_imports_warning(corpus: SimpleNamespace) -> None:
    (corpus.codex / "external_agent_session_imports.json").write_text("[not json")
    assert build(corpus)["warnings"]["imports"] == 1


def test_a_tool_output_without_a_call_is_an_orphan_warning_without_its_call_id(corpus: SimpleNamespace) -> None:
    tid = "019a9000-0000-7000-8000-000000000105"
    call_id = "call_orphan_7c1d"
    extra_rollout(
        corpus, tid,
        [
            cf.session_meta(T0, tid, cwd="/home/fixture/proj-x", history_mode="legacy"),
            cf.response_item(T0, cf.message("user", cf.input_text("Run it."))),
            cf.response_item(T0, cf.function_call_output(call_id, "done")),
            cf.response_item(T0, cf.message("assistant", cf.output_text("Done."))),
        ],
    )
    report = build(corpus)
    assert report["warnings"]["orphan_tool_output"] == 1
    assert call_id not in json.dumps(report)


# ---------------------------------------------------------------------------
# Incomplete share
# ---------------------------------------------------------------------------


def test_the_default_corpus_has_no_incomplete_session(corpus: SimpleNamespace) -> None:
    inc = build(corpus)["incomplete"]
    assert (inc["sessions"], inc["incomplete"], inc["share"]) == (6, 0, 0.0)
    assert nonzero(inc["by_reason"]) == {}


def test_the_incomplete_share_is_counted_by_reason(corpus: SimpleNamespace) -> None:
    t1 = "019a9000-0000-7000-8000-000000000106"
    t2 = "019a9000-0000-7000-8000-000000000107"
    extra_rollout(corpus, t1, simple_lines(t1, T0, cf.rollout_line(T0, "compacted", {"message": "", "replacement_history": []})))
    extra_rollout(corpus, t2, simple_lines(t2, T0, cf.rollout_line(T0, "hologram_record", {})))
    inc = build(corpus)["incomplete"]
    assert (inc["sessions"], inc["incomplete"]) == (8, 2)
    assert inc["share"] == pytest.approx(0.25, abs=0.001)
    assert nonzero(inc["by_reason"]) == {"compacted": 1, "unknown_record_type": 1}


def test_a_thread_with_two_rollouts_and_no_state_row_counts_once_as_current_ambiguous(corpus: SimpleNamespace) -> None:
    tid = "019a9000-0000-7000-8000-000000000108"
    extra_rollout(corpus, tid, simple_lines(tid, T0), when=T0)
    later = T0 + timedelta(hours=1)
    extra_rollout(corpus, tid, simple_lines(tid, later), when=later)
    report = build(corpus)
    assert report["threads"] == 7
    assert report["rollouts"]["total"] == 8
    assert report["incomplete"]["sessions"] == 7
    assert nonzero(report["incomplete"]["by_reason"]) == {"current_ambiguous": 1}


# ---------------------------------------------------------------------------
# Archive dry run
# ---------------------------------------------------------------------------

ARCHIVE_ZERO = {"unchanged": 0, "would_copy": 0, "would_append": 0, "diverged": 0, "decode_errors": 0, "revision_files": 0}


def test_with_no_archive_every_rollout_would_be_copied(corpus: SimpleNamespace) -> None:
    assert build(corpus)["archive"] == {**ARCHIVE_ZERO, "exists": False, "would_copy": 6}


def test_an_up_to_date_archive_is_unchanged(corpus: SimpleNamespace) -> None:
    write_archive(corpus)
    assert build(corpus)["archive"] == {**ARCHIVE_ZERO, "exists": True, "unchanged": 6}


def test_new_complete_lines_in_the_home_would_be_appended(corpus: SimpleNamespace) -> None:
    write_archive(corpus)
    path = home_rollouts(corpus.codex)[cf.rollout_stem(datetime(2026, 11, 10, 9, 0, tzinfo=UTC), cf.THREAD_PAGINATED)]
    path.write_bytes(path.read_bytes() + cf.encode_lines([cf.world_state(T0)]))
    assert build(corpus)["archive"] == {**ARCHIVE_ZERO, "exists": True, "unchanged": 5, "would_append": 1}


def test_a_last_line_without_a_newline_is_not_compared(corpus: SimpleNamespace) -> None:
    write_archive(corpus)
    path = home_rollouts(corpus.codex)[cf.rollout_stem(datetime(2026, 11, 10, 9, 0, tzinfo=UTC), cf.THREAD_PAGINATED)]
    path.write_bytes(path.read_bytes() + b'{"timestamp":"2026-11-15T10:00:00.000Z","type":"world')
    assert build(corpus)["archive"] == {**ARCHIVE_ZERO, "exists": True, "unchanged": 6}


def test_a_short_read_of_the_source_is_unchanged(corpus: SimpleNamespace) -> None:
    write_archive(corpus)
    path = home_rollouts(corpus.codex)[cf.rollout_stem(datetime(2026, 11, 10, 9, 0, tzinfo=UTC), cf.THREAD_PAGINATED)]
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:3]))
    assert build(corpus)["archive"] == {**ARCHIVE_ZERO, "exists": True, "unchanged": 6}


def test_a_source_that_is_not_a_prefix_of_the_copy_or_its_extension_is_diverged(corpus: SimpleNamespace) -> None:
    write_archive(corpus)
    path = home_rollouts(corpus.codex)[cf.rollout_stem(datetime(2026, 10, 5, 8, 15, tzinfo=UTC), cf.THREAD_ARCHIVED)]
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join([lines[0], *lines[2:], lines[1]]))
    assert build(corpus)["archive"] == {**ARCHIVE_ZERO, "exists": True, "unchanged": 5, "diverged": 1}


def test_a_source_equal_to_the_newest_revision_is_unchanged(corpus: SimpleNamespace) -> None:
    """The copier's rule (§4.2 step 2.6): a divergence equal to the newest revision makes no new one."""
    dest = write_archive(corpus)
    stem = cf.rollout_stem(datetime(2026, 10, 5, 8, 15, tzinfo=UTC), cf.THREAD_ARCHIVED)
    path = home_rollouts(corpus.codex)[stem]
    lines = path.read_bytes().splitlines(keepends=True)
    earlier = b"".join([lines[0], *lines[2:], lines[1]])
    (dest / f"{stem}.rev-1.jsonl").write_bytes(earlier)
    path.write_bytes(earlier)
    assert build(corpus)["archive"] == {**ARCHIVE_ZERO, "exists": True, "unchanged": 6, "revision_files": 1}


def test_a_rollout_that_cannot_be_decoded_is_a_decode_error(corpus: SimpleNamespace) -> None:
    write_archive(corpus)
    path = home_rollouts(corpus.codex)[cf.rollout_stem(datetime(2026, 10, 20, 14, 30, tzinfo=UTC), cf.THREAD_LEGACY)]
    assert path.suffix == ".zst"
    path.write_bytes(b"\x28\xb5\x2f\xfd this is not a zstd frame")
    report = build(corpus)
    assert report["archive"] == {**ARCHIVE_ZERO, "exists": True, "unchanged": 5, "decode_errors": 1}
    assert report["rollouts"]["total"] == 6


def test_existing_revision_files_are_counted(corpus: SimpleNamespace) -> None:
    dest = write_archive(corpus)
    stem = cf.rollout_stem(datetime(2026, 10, 5, 8, 15, tzinfo=UTC), cf.THREAD_ARCHIVED)
    (dest / f"{stem}.rev-1.jsonl").write_bytes(b"{}\n")
    (dest / f"{stem}.rev-2.jsonl").write_bytes(b"{}\n")
    assert build(corpus)["archive"]["revision_files"] == 2


# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------


def test_sizes_are_the_stored_and_the_decoded_bytes(corpus: SimpleNamespace) -> None:
    files = home_rollouts(corpus.codex).values()
    assert build(corpus)["sizes"] == {
        "stored_bytes": sum(p.stat().st_size for p in files),
        "decoded_bytes": sum(len(decoded(p)) for p in files),
    }


# ---------------------------------------------------------------------------
# A missing home, and read-only
# ---------------------------------------------------------------------------


def test_a_missing_home_gives_a_zero_report_and_creates_nothing(env: SimpleNamespace) -> None:
    before = snapshot(env.root)
    report = build(env)
    assert report["home_found"] is False
    assert report["threads"] == 0
    assert report["rollouts"] == {"total": 0, "plain": 0, "zst": 0, "archived": 0}
    assert snapshot(env.root) == before
    assert not env.codex.exists()


def test_the_command_succeeds_for_a_missing_home(env: SimpleNamespace) -> None:
    result = run_cli("--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["home_found"] is False
    assert not env.codex.exists()
    assert not (env.data / "codex").exists()


def test_the_report_writes_nothing_anywhere(corpus: SimpleNamespace) -> None:
    write_archive(corpus)
    (archive_dir(corpus).parent / ".sync.lock").write_bytes(b"")
    before = snapshot(corpus.root)

    build(corpus)
    assert run_cli().exit_code == 0
    assert run_cli("--json").exit_code == 0

    assert snapshot(corpus.root) == before
    assert list(corpus.tmp.iterdir()) == [], "no temp folder or file stays behind"
    assert not (corpus.codex / "state_5.sqlite-wal").exists()
    assert not (corpus.codex / "state_5.sqlite-shm").exists()


def test_the_report_does_not_create_the_archive(corpus: SimpleNamespace) -> None:
    assert run_cli("--json").exit_code == 0
    assert not (corpus.data / "codex").exists()


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_json_output_equals_build_report(corpus: SimpleNamespace) -> None:
    result = run_cli("--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == build(corpus)


def test_text_output_shows_the_main_numbers(corpus: SimpleNamespace) -> None:
    result = run_cli()
    assert result.exit_code == 0, result.output
    for word in (cf.CLI_VERSION, "paginated", "legacy", "HologramCall"):
        assert word in result.output
    with pytest.raises(ValueError):
        json.loads(result.output)


def test_format_report_is_the_text_output(corpus: SimpleNamespace) -> None:
    from cli.codex_report import format_report

    assert run_cli().output.strip() == format_report(build(corpus)).strip()


# ---------------------------------------------------------------------------
# Privacy (§5, N6): no message text, no path, no title, no id
# ---------------------------------------------------------------------------

M_THREADS = (
    "7e110000-0000-7000-8000-0000000000a1",
    "7e110000-0000-7000-8000-0000000000b2",
    "7e110000-0000-7000-8000-0000000000c3",
)


def build_marker_home(home: Path) -> None:
    """A Codex home whose texts, paths, titles, and ids are all marker strings (MRK...)."""
    when = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
    s = lambda n: when + timedelta(seconds=n)
    cwd = "/home/MRKPATHUSER/MRKPATHPROJ"
    git = {"commit_hash": "MRKGITSHA", "branch": "MRKBRANCH", "repository_url": "https://example.com/MRKREPO.git"}
    t_pag, t_leg, t_arc = M_THREADS

    pag = cf.write_rollout(cf.rollout_path(home, when, t_pag), [
        cf.session_meta(when, t_pag, cwd=cwd, history_mode="paginated", git=git, agent_nickname="MRKNICK"),
        cf.turn_context(s(1), "MRKTURN1", cwd=cwd, model="gpt-5.5-codex"),
        cf.response_item(s(2), cf.message("user", cf.input_text("<environment_context>MRKENVTEXT</environment_context>"))),
        cf.item_completed(s(3), t_pag, "MRKTURN1", cf.user_message_item(
            "MRKITEM1", "MRKTEXTUSER1",
            {"type": "local_image", "path": "/home/MRKPATHUSER/MRKIMAGE.png"},
        )),
        cf.item_completed(s(4), t_pag, "MRKTURN1", cf.reasoning_item("MRKITEM2", "MRKTEXTTHINK")),
        cf.item_completed(s(5), t_pag, "MRKTURN1", cf.command_item(
            "MRKITEM3", ["cat", "/home/MRKPATHUSER/MRKFILE"], cwd=cwd, output="MRKTEXTOUTPUT",
        )),
        cf.item_completed(s(6), t_pag, "MRKTURN1", {"type": "Plan", "id": "MRKITEM4", "text": "MRKTEXTPLAN"}),
        cf.item_completed(s(7), t_pag, "MRKTURN1", {"type": "HologramCall", "id": "MRKITEM5", "beam": "MRKTEXTBEAM"}),
        cf.item_completed(s(8), t_pag, "MRKTURN1", cf.agent_message_item("MRKITEM6", "MRKTEXTREPLY")),
        cf.rollout_line(s(9), "compacted", {"message": "MRKTEXTCOMPACT", "replacement_history": []}),
        '{"type":"event_msg","payload":{"text":"MRKTEXTDAMAGED"',
    ])
    leg = cf.write_rollout(cf.rollout_path(home, when + timedelta(hours=1), t_leg, zst=True), [
        cf.session_meta(when, t_leg, cwd=cwd + "/MRKPATHLEG", history_mode="legacy"),
        cf.response_item(s(1), cf.message("user", cf.input_text("MRKTEXTLEGUSER"))),
        cf.response_item(s(2), cf.function_call("MRKCALL1", "shell", {"command": ["ls", "/home/MRKPATHUSER"]})),
        cf.response_item(s(3), cf.function_call_output("MRKCALL1", "MRKTEXTCALLOUT")),
        cf.response_item(s(4), cf.function_call_output("MRKCALLORPHAN", "MRKTEXTORPHAN")),
        cf.response_item(s(5), {"type": "reasoning", "summary": [], "encrypted_content": "MRKENCRYPTED"}),
        cf.response_item(s(6), cf.message("assistant", cf.output_text("MRKTEXTLEGREPLY"))),
    ])
    arc = cf.write_rollout(cf.rollout_path(home, when + timedelta(hours=2), t_arc, archived=True), [
        cf.session_meta(when, t_arc, cwd=cwd, history_mode="paginated"),
        cf.item_completed(s(1), t_arc, "MRKTURN3", cf.user_message_item("MRKITEM7", "MRKTEXTARC")),
    ])

    cf.write_session_index(home, [(t_pag, "MRKNAMEINDEX", when), (t_leg, "MRKNAMEINDEX2", when)])
    cf.write_state_db(home, [
        cf.state_row(t_pag, pag, started=when, updated=s(60), cwd=cwd, title="MRKTITLE1", model="gpt-5.5-codex",
                     history_mode="paginated", name="MRKNAMESTATE", git=git),
        cf.state_row(t_leg, leg, started=when, updated=s(60), cwd=cwd, title="MRKTITLE2", model="gpt-5.4-codex",
                     history_mode="legacy"),
        cf.state_row(t_arc, arc, started=when, updated=s(60), cwd=cwd, title="MRKTITLE3", model="gpt-5.5-codex",
                     history_mode="paginated", archived=True),
    ])
    cf.write_imports(home, [{
        "source_path": "/home/MRKPATHUSER/.claude/projects/MRKIMPORT.jsonl",
        "content_sha256": "MRKSHA",
        "imported_thread_id": t_arc,
        "title": "MRKTITLEIMPORT",
    }])
    # Files that the report must never read (§1.2).
    (home / "history.jsonl").write_text('{"session_id":"MRKHISTID","text":"MRKTEXTHISTORY"}\n')
    (home / "log").mkdir()
    (home / "log" / "codex-tui.log").write_text("MRKTEXTLOG /home/MRKPATHUSER\n")


@pytest.fixture
def marker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Like ``env``, but every folder name is a marker too."""
    from backend import config

    root = tmp_path / "MRKROOT"
    user_home = patch_home(monkeypatch, root / "MRKUSERHOME")
    user_home.mkdir(parents=True)
    codex = user_home / ".codex"
    data = root / "MRKDATADIR"
    tmp = root / "MRKTMP"
    tmp.mkdir()
    build_marker_home(codex)
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    config.get_settings.cache_clear()
    env = SimpleNamespace(root=root, user=user_home, codex=codex, data=data, tmp=tmp)
    write_archive(env)
    yield env
    config.get_settings.cache_clear()


def assert_private(output: str, env: SimpleNamespace) -> None:
    assert "MRK" not in output.upper(), [line for line in output.splitlines() if "MRK" in line.upper()]
    assert not UUID_RE.search(output), UUID_RE.search(output)
    for path in (env.root, env.user, env.codex, env.data):
        assert str(path) not in output
        assert path.as_posix() not in output
    assert "/home/" not in output
    assert "rollout-" not in output


def test_the_marker_home_exercises_every_part_of_the_report(marker_env: SimpleNamespace) -> None:
    """Guard: the privacy test below is only strong if the report saw the markers."""
    from cli.codex_report import build_report

    report = build_report(marker_env.codex, marker_env.data)
    assert report["threads"] == 3
    assert report["rollouts"]["total"] == 3
    assert report["archive"]["unchanged"] == 3
    assert report["warnings"]["damaged_line"] >= 1
    assert report["warnings"]["orphan_tool_output"] == 1
    assert report["warnings"]["unsupported_item"] >= 1
    assert report["incomplete"]["by_reason"]["compacted"] == 1


def test_the_json_report_has_no_text_path_title_or_id(marker_env: SimpleNamespace) -> None:
    result = run_cli("--json")
    assert result.exit_code == 0, result.output
    assert_private(result.output, marker_env)


def test_the_text_report_has_no_text_path_title_or_id(marker_env: SimpleNamespace) -> None:
    result = run_cli()
    assert result.exit_code == 0, result.output
    assert_private(result.output, marker_env)


def test_the_report_logs_no_text_path_title_or_id(
    marker_env: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    """A work-machine tester can paste the terminal output, log lines included."""
    caplog.set_level("DEBUG")
    assert run_cli().exit_code == 0
    assert_private(caplog.text, marker_env)


# ---------------------------------------------------------------------------
# The codex-verification issue template (§9)
# ---------------------------------------------------------------------------


def template() -> dict[str, Any]:
    import yaml

    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def fields() -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in template()["body"] if "id" in f}


def test_the_template_is_an_issue_form_with_the_codex_verification_label() -> None:
    data = template()
    assert data["name"]
    assert data["description"]
    labels = data["labels"] if isinstance(data["labels"], list) else [data["labels"]]
    assert "codex-verification" in labels
    assert isinstance(data["body"], list)


@pytest.mark.parametrize("item", CHECKLIST)
def test_each_checklist_item_is_a_required_pass_or_fail_dropdown(item: str) -> None:
    field = fields()[item]
    assert field["type"] == "dropdown"
    assert {"Pass", "Fail"} <= set(field["attributes"]["options"])
    assert field["attributes"]["label"]
    assert field.get("validations", {}).get("required") is True


@pytest.mark.parametrize("item", CHECKLIST)
def test_each_checklist_item_has_a_short_note_field(item: str) -> None:
    field = fields()[f"{item}_note"]
    assert field["type"] in ("input", "textarea")
    assert field["attributes"]["label"]


def test_the_template_asks_for_the_shape_report_output() -> None:
    field = fields()["shape_report"]
    assert field["type"] == "textarea"
    assert "codex-report" in json.dumps(field)


def test_the_template_says_to_attach_only_screenshots_of_the_synthetic_fixture_corpus() -> None:
    text = json.dumps(template()).lower()
    assert "screenshot" in text
    assert "synthetic fixture" in text
    assert "only" in text


# L6 (issue #73): each checklist item says exactly what to check.
CHECKLIST_DESCRIPTION_WORDS = {
    "source_filter": ("Codex", "only"),
    "project_grouping": ("project folder",),
    "search_jump": ("earch", "jump", "message"),
    "detail_controls": ("Thread ID", "Codex name", "Codex home", "copy", "Show Tools", "notices"),
    "export": ("Copy as Markdown", "Markdown", "PDF", "Thread ID", "Codex name", "Codex home"),
    "mcp_tools": ("list_sessions", 'source="CODEX"', "get_session_outline", "unknown source", "error"),
}


@pytest.mark.xfail(strict=True, reason="L6 not implemented yet")
@pytest.mark.parametrize("item", CHECKLIST)
def test_each_checklist_item_has_a_description_of_what_to_check(item: str) -> None:
    description = fields()[item]["attributes"].get("description", "")
    assert description.strip(), f"{item} has no description"
    missing = [w for w in CHECKLIST_DESCRIPTION_WORDS[item] if w not in description]
    assert not missing, f"{item} description lacks {missing}: {description!r}"
