"""L7: the six fixes of the pre-release security review (before 1.2.0).

Every test builds its data under ``tmp_path`` (``backend.tests.codex_fake``
for a Codex home); nothing reads a real home folder.

Contract (orchestrator, 2026-11-19):

1. The MCP ``list_sessions`` cloud-sync notice names the provider, and the
   folder only as ``~/<rel>``; a folder outside home gives no absolute path.
   ``cloud_sync_message`` (the web page and the CLI) keeps the full folder.
2. ``backend.sources.codex._display_home`` gives ``~``, ``~/<rel>``, or
   ``…/<last folder name>``, and folds case only on macOS and Windows.
3. ``ClaudeFetcher.save_conversation`` skips an unsafe conversation or org
   id with one WARNING: no write, no read, no side file.
4. ``backend.archive_sync.MAX_DECODED_BYTES`` (1 GiB) and ``MAX_LINE_BYTES``
   (64 MiB): over a cap, ``sync_stream`` and the Codex report count a
   decode error and write nothing.
5. ``state_db`` opens its private copy as ``file:<copy>?mode=ro`` with
   ``uri=True``, and runs ``PRAGMA trusted_schema=OFF`` first.
6. ``unknown record type: <type>`` names the type only when it matches
   ``[A-Za-z][A-Za-z0-9_]{0,63}``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.tests import codex_fake as fake
from backend.tests._platform_home import patch_home

ELLIPSIS = "…"
CASE_INSENSITIVE = sys.platform in ("darwin", "win32")


def _absolute_forms(*paths: Path) -> set[str]:
    out: set[str] = set()
    for p in paths:
        out |= {str(p), os.path.realpath(p), p.as_posix(), Path(os.path.realpath(p)).as_posix()}
    return out


def _tool(name: str, **kwargs: Any) -> Any:
    from mcp_server import server

    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


@pytest.fixture
def mcp_reset(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from backend import config
    from mcp_server import server

    monkeypatch.setenv("CLAUDE_EXPLORER_SEARCH_DRIFT_DEBOUNCE_SEC", "30")
    server._settings = None
    server._store = None
    server.reset_archive_sync_for_tests()
    config.get_settings.cache_clear()
    try:
        yield
    finally:
        server.reset_archive_sync_for_tests()
        server._settings = None
        server._store = None
        config.get_settings.cache_clear()


def _cloud_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data_dir_of: Callable[[Path], Path]
) -> SimpleNamespace:
    from backend import config

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    data_dir = data_dir_of(home)
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    config.get_settings.cache_clear()
    return SimpleNamespace(home=home, data_dir=data_dir)


# ---------------------------------------------------------------------------
# 1. The MCP cloud-sync notice holds no absolute path
# ---------------------------------------------------------------------------


def test_mcp_cloud_sync_notice_names_a_home_folder_as_tilde_and_the_web_keeps_the_full_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mcp_reset: None
) -> None:
    from fastapi import HTTPException

    from backend.routers.write_consent import refuse_if_cloud_sync_gate

    def dropbox(home: Path) -> Path:
        (home / "Dropbox").mkdir()
        return home / "Dropbox" / "explorer" / "conversations"

    env = _cloud_env(tmp_path, monkeypatch, dropbox)
    notices = _tool("list_sessions").get("notices", [])

    assert notices, "a closed gate must give a notice"
    text = "\n".join(notices)
    assert "Dropbox" in text
    assert "~/Dropbox/explorer/conversations" in text
    for form in _absolute_forms(env.home, env.data_dir, tmp_path):
        assert form not in text, f"absolute path {form!r} in the MCP notice: {text!r}"

    with pytest.raises(HTTPException) as exc:
        refuse_if_cloud_sync_gate()
    assert os.path.realpath(env.data_dir) in str(exc.value.detail)


def test_mcp_cloud_sync_notice_for_a_folder_outside_home_holds_no_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mcp_reset: None
) -> None:
    onedrive = tmp_path / "outside" / "OneDrive"
    onedrive.mkdir(parents=True)
    monkeypatch.setenv("OneDrive", str(onedrive))
    _cloud_env(tmp_path, monkeypatch, lambda _home: onedrive / "explorer" / "conversations")

    notices = _tool("list_sessions").get("notices", [])

    assert notices, "a closed gate must give a notice"
    text = "\n".join(notices)
    assert "OneDrive" in text
    for form in _absolute_forms(onedrive, tmp_path):
        assert form not in text, f"absolute path {form!r} in the MCP notice: {text!r}"


def test_cloud_sync_message_for_the_web_and_the_cli_keeps_the_full_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mcp_reset: None
) -> None:
    from backend.write_gate import cloud_sync_message

    onedrive = tmp_path / "outside" / "OneDrive"
    onedrive.mkdir(parents=True)
    monkeypatch.setenv("OneDrive", str(onedrive))
    env = _cloud_env(tmp_path, monkeypatch, lambda _home: onedrive / "explorer" / "conversations")
    folder = Path(os.path.realpath(env.data_dir))

    # Fails while the MCP notice is still the full message; then pins that
    # the full message stays for the web page and the CLI.
    assert not any(str(folder) in n for n in _tool("list_sessions").get("notices", []))
    message = cloud_sync_message(("OneDrive", folder))
    assert str(folder) in message
    assert "OneDrive" in message


# ---------------------------------------------------------------------------
# 2. _display_home never returns an absolute path
# ---------------------------------------------------------------------------


@pytest.fixture
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = patch_home(monkeypatch, tmp_path / "alice")
    (home / ".codex").mkdir(parents=True)
    return home


def _display(path: Path | str) -> str:
    from backend.sources.codex import _display_home

    return _display_home(str(path))


def test_display_home_of_a_folder_outside_home_is_the_last_folder_name(
    tmp_path: Path, user_home: Path
) -> None:
    outside = tmp_path / "Volumes" / "Work" / "alice" / ".codex"
    outside.mkdir(parents=True)

    assert _display(user_home / ".codex") == "~/.codex"
    assert _display(user_home) == "~"
    assert _display(outside) == f"{ELLIPSIS}/.codex"


def test_display_home_of_a_missing_folder_outside_home_is_the_last_folder_name(
    tmp_path: Path, user_home: Path
) -> None:
    assert _display(tmp_path / "gone" / "codex-home") == f"{ELLIPSIS}/codex-home"


def test_display_home_of_a_sibling_with_the_home_name_as_prefix_is_not_under_home(
    tmp_path: Path, user_home: Path
) -> None:
    sibling = tmp_path / "alice2" / ".codex"
    sibling.mkdir(parents=True)

    assert _display(sibling) == f"{ELLIPSIS}/.codex"


def test_display_home_folds_case_only_on_case_insensitive_platforms(
    tmp_path: Path, user_home: Path
) -> None:
    other_case = tmp_path / "ALICE" / ".codex"

    expected = "~/.codex" if CASE_INSENSITIVE else f"{ELLIPSIS}/.codex"
    assert _display(other_case) == expected


def test_display_home_never_holds_an_absolute_path(tmp_path: Path, user_home: Path) -> None:
    paths = [
        tmp_path / "Volumes" / "Work" / "alice" / ".codex",
        tmp_path / "alice2" / ".codex",
        tmp_path / "ALICE" / ".codex",
        tmp_path / "gone" / "x",
    ]
    for p in paths:
        shown = _display(p)
        assert shown.startswith(("~", ELLIPSIS)), shown
        for form in _absolute_forms(tmp_path):
            assert form not in shown, shown


def test_mcp_shows_a_codex_home_outside_home_as_the_last_folder_name_and_no_absolute_path(
    isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mcp_reset: None
) -> None:
    from backend import config

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    codex = fake.build_codex_home(tmp_path / "elsewhere" / ".codex")
    monkeypatch.setenv("CODEX_HOME", str(codex))
    config.get_settings.cache_clear()

    listed = _tool("list_sessions", source="codex", limit=100)
    outputs = [listed, _tool("list_projects", source="codex")]
    for tid in fake.ALL_THREADS:
        outputs.append(_tool("get_session_outline", conversation_id=tid))
        outputs.append(_tool("export_session", conversation_id=tid))

    assert listed["sessions"], "the Codex sessions must list"
    assert {s.get("source_home") for s in listed["sessions"]} == {f"{ELLIPSIS}/.codex"}
    text = json.dumps(outputs, default=str)
    for form in _absolute_forms(codex, tmp_path):
        assert form not in text, f"absolute path {form!r} in an MCP output"


# ---------------------------------------------------------------------------
# 3. bulk_fetch refuses unsafe ids
# ---------------------------------------------------------------------------

SAFE_ORG = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
SAFE_CONV = "11111111-2222-3333-4444-555555555555"
UNSAFE_IDS = [
    "../escape",
    "../../etc/passwd",
    "sub/dir",
    "back\\slash",
    "..",
    ".",
    "",
    "x" * 129,
    "has space",
    "nul\x00byte",
    "café",
    None,
]


def _fetcher(tmp_path: Path, org: str = SAFE_ORG) -> Any:
    from fetcher.bulk_fetch import ClaudeFetcher

    return ClaudeFetcher(
        session_key="sk-ant-sid01-fake-test-key",
        orgs=[{"uuid": org, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True}],
        primary_org_id=org,
        output_dir=tmp_path / "out" / "conversations",
        files_dir=tmp_path / "out" / "files",
        download_files=True,
        delay=0.0,
    )


def _conversation(uuid: object) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "name": "Test",
        "chat_messages": [
            {
                "uuid": "m1",
                "files": [
                    {
                        "uuid": "f1",
                        "file_name": "a.png",
                        "thumbnail_url": "https://example.com/t.png",
                        "preview_url": "https://example.com/p.png",
                    }
                ],
            }
        ],
    }


def _tree(root: Path) -> set[str]:
    return {str(p) for p in root.rglob("*")} if root.exists() else set()


def _save_and_check(
    tmp_path: Path, fetcher: Any, conv: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from fetcher import bulk_fetch

    reads: list[Any] = []
    downloads: list[Any] = []
    monkeypatch.setattr(bulk_fetch, "_read_local_conversation", lambda p: reads.append(p))
    monkeypatch.setattr(fetcher, "_download_file", lambda url, dest: downloads.append(dest) or (False, dest))
    before = _tree(tmp_path / "out")

    with caplog.at_level(logging.WARNING, logger="fetcher.bulk_fetch"):
        assert fetcher.save_conversation(conv) is None

    assert _tree(tmp_path / "out") == before, "an unsafe id must create no file or folder"
    assert reads == [], "an unsafe id must read no local file"
    assert downloads == [], "an unsafe id must download no side file"
    warnings = [r for r in caplog.records if r.name == "fetcher.bulk_fetch" and r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]


@pytest.mark.parametrize("uuid", UNSAFE_IDS, ids=repr)
def test_save_conversation_skips_an_unsafe_conversation_uuid(
    uuid: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _save_and_check(tmp_path, _fetcher(tmp_path), _conversation(uuid), monkeypatch, caplog)


@pytest.mark.parametrize("org", [o for o in UNSAFE_IDS if isinstance(o, str) and o], ids=repr)
def test_save_conversation_skips_an_unsafe_org_id(
    org: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _save_and_check(tmp_path, _fetcher(tmp_path, org), _conversation(SAFE_CONV), monkeypatch, caplog)


# ---------------------------------------------------------------------------
# 4. Decode caps
# ---------------------------------------------------------------------------

STEM = "rollout-2026-11-10T09-00-00-019a7c10-0000-7000-8000-00000000a001"
BIG = 1 << 40


def _caps(monkeypatch: pytest.MonkeyPatch, *, total: int = BIG, line: int = BIG) -> None:
    # Both import styles of the caps in cli.codex_report see the test values.
    import cli.codex_report as report_mod
    from backend import archive_sync

    for mod in (archive_sync, report_mod):
        monkeypatch.setattr(mod, "MAX_DECODED_BYTES", total, raising=False)
        monkeypatch.setattr(mod, "MAX_LINE_BYTES", line, raising=False)


def _chunks(data: bytes, size: int = 7) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


class Archive:
    def __init__(self, tmp_path: Path) -> None:
        self.dest = tmp_path / "archive"
        self.source = tmp_path / "source" / f"{STEM}.jsonl"
        self.source.parent.mkdir(parents=True)

    @property
    def copy(self) -> Path:
        return self.dest / f"{STEM}.jsonl"

    @property
    def meta(self) -> Path:
        return self.dest / f"{STEM}.meta.json"

    def files(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted(self.dest.glob("*"))} if self.dest.exists() else {}

    def sync(self, data: bytes | None = None, decode: Callable[[Path], Iterable[bytes]] | None = None) -> Any:
        from backend.archive_sync import SyncReport, sync_stream

        if data is not None:
            # A new size, so the unchanged-stat check never skips the read.
            self.source.write_bytes(data)
            decode = decode or (lambda _p: iter(_chunks(data)))
        elif not self.source.exists():
            self.source.write_bytes(b"x")
        assert decode is not None
        report = SyncReport()
        sync_stream(report, dest_dir=self.dest, stem=STEM, source_path=self.source, decode=decode)
        return report


class Endless:
    """A decoder that would never end; it stops after ``limit`` chunks and records the overrun."""

    def __init__(self, chunk: bytes, limit: int = 20_000) -> None:
        self.chunk, self.limit, self.overrun = chunk, limit, False

    def __call__(self, _path: Path) -> Iterator[bytes]:
        for _ in range(self.limit):
            yield self.chunk
        self.overrun = True


def _assert_decode_error(report: Any) -> None:
    assert report.counts["decode_errors"] == 1, report.counts
    assert len(report.warnings) == 1, report.warnings
    assert not report.wrote_any


def test_the_decode_caps_are_1_gib_and_64_mib() -> None:
    from backend import archive_sync

    assert archive_sync.MAX_DECODED_BYTES == 1 << 30
    assert archive_sync.MAX_LINE_BYTES == 64 << 20
    import cli.codex_report as report_mod

    for name, value in (("MAX_DECODED_BYTES", 1 << 30), ("MAX_LINE_BYTES", 64 << 20)):
        assert getattr(report_mod, name, value) == value


def test_a_new_stream_over_the_total_cap_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _caps(monkeypatch, total=50)
    a = Archive(tmp_path)

    report = a.sync(b"line\n" * 20)

    _assert_decode_error(report)
    assert a.files() == {}


def test_a_grown_stream_over_the_total_cap_keeps_the_copy_and_the_next_pass_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = Archive(tmp_path)
    first = b"a\nb\n"
    assert a.sync(first).counts["copied"] == 1
    before = a.files()

    _caps(monkeypatch, total=20)
    grown = first + b"c\n" * 20
    report = a.sync(grown)

    _assert_decode_error(report)
    assert a.files() == before, "no partial write and no revision"

    _caps(monkeypatch)
    report = a.sync(grown + b"d\n")
    assert report.counts["decode_errors"] == 0
    assert report.counts["appended"] == 1
    assert a.copy.read_bytes() == grown + b"d\n"


def test_a_diverged_stream_over_the_total_cap_makes_no_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = Archive(tmp_path)
    assert a.sync(b"a\nb\n").counts["copied"] == 1
    before = a.files()

    _caps(monkeypatch, total=20)
    report = a.sync(b"z\n" * 30)

    _assert_decode_error(report)
    assert a.files() == before
    assert not list(a.dest.glob("*.rev-*"))


def test_an_endless_stream_stops_at_the_total_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _caps(monkeypatch, total=1000)
    a = Archive(tmp_path)
    decoder = Endless(b"line\n")

    report = a.sync(decode=decoder)

    assert not decoder.overrun, "the copy must stop reading at the cap"
    _assert_decode_error(report)
    assert a.files() == {}


def test_a_line_over_the_line_cap_in_one_chunk_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _caps(monkeypatch, line=10)
    a = Archive(tmp_path)
    data = b"short\n" + b"y" * 50 + b"\nok\n"

    report = a.sync(data, decode=lambda _p: iter([data]))

    _assert_decode_error(report)
    assert a.files() == {}


def test_a_line_over_the_line_cap_across_chunks_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _caps(monkeypatch, line=10)
    a = Archive(tmp_path)

    report = a.sync(b"ok\n" + b"y" * 40 + b"\n", decode=lambda _p: iter([b"ok\n"] + [b"y" * 4] * 10 + [b"\n"]))

    _assert_decode_error(report)
    assert a.files() == {}


def test_an_unterminated_last_line_over_the_line_cap_is_a_decode_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _caps(monkeypatch, line=10)
    a = Archive(tmp_path)

    report = a.sync(b"ok\n" + b"y" * 50)

    _assert_decode_error(report)
    assert a.files() == {}


def test_an_endless_line_stops_at_the_line_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _caps(monkeypatch, line=1000)
    a = Archive(tmp_path)
    decoder = Endless(b"yyyyyyyy")

    report = a.sync(decode=decoder)

    assert not decoder.overrun, "the copy must stop reading at the line cap"
    _assert_decode_error(report)


def test_a_stream_under_both_caps_copies_after_a_capped_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = Archive(tmp_path)
    data = b"abc\n" * 10

    _caps(monkeypatch, total=20, line=100)
    _assert_decode_error(a.sync(data))

    _caps(monkeypatch, total=100, line=5)
    report = a.sync(data + b"d\n")
    assert report.counts["decode_errors"] == 0
    assert report.counts["copied"] == 1
    assert a.copy.read_bytes() == data + b"d\n"


@pytest.fixture
def report_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from backend import config

    patch_home(monkeypatch, tmp_path / "user").mkdir()
    codex = fake.build_codex_home(tmp_path / "codex-home")
    data = tmp_path / "data"
    data.mkdir()
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    config.get_settings.cache_clear()
    yield SimpleNamespace(codex=codex, data=data)
    config.get_settings.cache_clear()


def _report(env: SimpleNamespace) -> dict[str, Any]:
    from cli.codex_report import build_report

    return build_report(env.codex, env.data)


@pytest.mark.parametrize("cap", ["total", "line"])
def test_the_codex_report_counts_a_rollout_over_a_cap_as_a_decode_error(
    cap: str, report_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _caps(monkeypatch)
    baseline = _report(report_env)
    assert baseline["archive"]["decode_errors"] == 0
    assert baseline["sizes"]["decoded_bytes"] > 0

    _caps(monkeypatch, **{cap: 1})
    capped = _report(report_env)

    total = capped["rollouts"]["total"]
    assert total == baseline["rollouts"]["total"] > 0
    assert capped["archive"]["decode_errors"] == total
    assert capped["sizes"]["decoded_bytes"] == 0


# ---------------------------------------------------------------------------
# 5. The state_5 copy opens read-only, without a trusted schema
# ---------------------------------------------------------------------------

_TRUSTED_OFF = re.compile(r"pragma\s+(main\.)?trusted_schema\s*(=\s*|\(\s*)(off|0|false|no)\b")
_RO_URI = re.compile(r"^file:.*state_5\.sqlite\?(.*&)?mode=ro(&.*)?$")


def test_state_db_opens_the_private_copy_read_only_with_trusted_schema_off_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.codex import state_db

    monkeypatch.setattr(state_db, "DEADLINE_S", 5.0)
    home = fake.build_codex_home(tmp_path / "home")
    calls: list[tuple[tuple[Any, ...], dict[str, Any], list[str]]] = []
    real_connect = sqlite3.connect

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        sql: list[str] = []
        conn.set_trace_callback(sql.append)
        calls.append((args, kwargs, sql))
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced)
    result = state_db.read_threads(home)

    assert result.rows, result.warnings
    assert calls, "read_threads must open the copy with sqlite3.connect"
    for args, kwargs, sql in calls:
        database = str(args[0] if args else kwargs.get("database"))
        assert _RO_URI.match(database), f"open the copy as file:<copy>?mode=ro, got {database!r}"
        assert not database[len("file:"):].startswith(str(home)), "never open the home file"
        assert kwargs.get("uri") is True, kwargs
        stmts = [s.strip().lower() for s in sql if s.strip()]
        assert stmts and _TRUSTED_OFF.match(stmts[0]), f"PRAGMA trusted_schema=OFF must come first: {stmts!r}"


# ---------------------------------------------------------------------------
# 6. A raw record type never reaches a notice
# ---------------------------------------------------------------------------


def _reasons(*types: str) -> list[str]:
    from backend.codex.projection import project_rollout
    from backend.codex.rollout import RolloutRecord

    records = [RolloutRecord(line_no=i + 1, timestamp=None, type=t, payload={}) for i, t in enumerate(types)]
    return project_rollout(records, thread_id="t")["incomplete_reasons"]


@pytest.mark.parametrize(
    "raw",
    [
        "a" * 65,
        "9starts_with_digit",
        "_underscore_first",
        "has space",
        "dash-ed",
        "dot.ted",
        "../../etc/passwd",
        "<script>alert(1)</script>",
        "line\nbreak",
        "café",
        "",
    ],
    ids=repr,
)
def test_an_unsafe_record_type_gives_a_reason_without_the_type(raw: str) -> None:
    reasons = _reasons(raw)

    assert "unknown record type" in reasons, reasons
    assert not any(r.startswith("unknown record type:") for r in reasons), reasons


def test_a_safe_record_type_is_named_and_an_unsafe_one_is_not(tmp_path: Path) -> None:
    longest = "A" + "b" * 63
    reasons = _reasons("future_kind", longest, "bad type!")

    assert "unknown record type: future_kind" in reasons
    assert f"unknown record type: {longest}" in reasons
    assert "unknown record type" in reasons
    assert not any("bad type!" in r for r in reasons), reasons
