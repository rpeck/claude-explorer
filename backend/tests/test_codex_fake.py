"""NF: the synthetic Codex home, the zstd shim, and the ``codex_home`` setting.

Plan: PLANS/2026.11.18-codex-source.md, §3, §4.3 ("The Codex home
setting"), §4.6, §5, and the NF row of §6.1.

The tests read the files that ``build_codex_home`` writes, not a Python
object, so the N4 and N5 tests find the same threads the same way. Where
the plan does not state the nesting of a record, the tests search the
payload. No test reads a real ``~/.codex``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests._platform_home import patch_home

REPO_ROOT = Path(__file__).resolve().parents[2]

# §3: the columns of ``state_5.sqlite.threads`` that Explorer reads.
STATE_5_COLUMNS = {
    "id", "rollout_path", "title", "name", "archived", "cwd", "git_sha",
    "git_branch", "git_origin_url", "model", "history_mode", "updated_at_ms",
    "thread_source", "source",
}

# §3: the 19 completed item types of ``ThreadItemPayload``.
KNOWN_ITEM_TYPES = {
    "UserMessage", "AgentMessage", "Reasoning", "CommandExecution",
    "ContextCompaction", "HookPrompt", "FunctionCallOutput", "Plan",
    "FileChange", "McpToolCall", "DynamicToolCall", "CollabAgentToolCall",
    "SubAgentActivity", "WebSearch", "ImageView", "Sleep", "ImageGeneration",
    "EnteredReviewMode", "ExitedReviewMode",
}


def _norm(name: str) -> str:
    # The serialized tag can be "UserMessage", "userMessage", or "user_message".
    return name.replace("_", "").lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def codex_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from backend.tests.codex_fake import build_codex_home

    home = tmp_path_factory.mktemp("fake") / "nested" / "codex"
    returned = build_codex_home(home)
    assert Path(returned) == home
    return home


def _rollouts(home: Path) -> list[Path]:
    files = []
    for top in ("sessions", "archived_sessions"):
        root = home / top
        if root.is_dir():
            files += [
                p for p in root.rglob("rollout-*")
                if p.name.endswith((".jsonl", ".jsonl.zst"))
            ]
    return sorted(files)


def _stem(path: Path | str) -> str:
    name = Path(path).name
    for suffix in (".jsonl.zst", ".jsonl"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _decoded(path: Path) -> bytes:
    if path.name.endswith(".zst"):
        from backend.codex.zstd import decompress

        return decompress(path.read_bytes())
    return path.read_bytes()


def _lines(path: Path) -> list[bytes]:
    """The lines that end with a newline (§4.2: a last partial line is not a line)."""
    return _decoded(path).split(b"\n")[:-1]


def _records(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in _lines(path):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _walk(obj: Any) -> Iterator[Any]:
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _dicts(obj: Any) -> Iterator[dict]:
    return (d for d in _walk(obj) if isinstance(d, dict))


def _ptype(rec: dict) -> Any:
    payload = rec.get("payload")
    return payload.get("type") if isinstance(payload, dict) else None


def _session_meta_id(path: Path) -> str:
    first = _records(path)[0]
    assert first["type"] == "session_meta", path.name
    return first["payload"]["id"]


def _item_completed(rec: dict) -> bool:
    return rec.get("type") == "event_msg" and _ptype(rec) == "item_completed"


def _item_types(rec: dict) -> set[str]:
    """Item type tags in an item_completed payload: a dict with ``id`` and ``type``."""
    return {
        d["type"] for d in _dicts(rec["payload"])
        if "id" in d and isinstance(d.get("type"), str) and d["type"] != "item_completed"
    }


def _all_records(home: Path) -> list[tuple[Path, dict]]:
    return [(p, r) for p in _rollouts(home) for r in _records(p)]


def _threads(home: Path) -> list[dict[str, Any]]:
    # immutable=1: a plain mode=ro open of a WAL database creates -wal and -shm.
    uri = (home / "state_5.sqlite").as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM threads")]
    finally:
        conn.close()


def _rows_by_stem(home: Path) -> dict[str, dict]:
    return {_stem(r["rollout_path"]): r for r in _threads(home) if r["rollout_path"]}


def _compress(data: bytes) -> bytes:
    if sys.version_info >= (3, 14):
        from compression import zstd

        return zstd.compress(data)
    import zstandard

    return zstandard.ZstdCompressor().compress(data)


# ---------------------------------------------------------------------------
# The builder: files and folders
# ---------------------------------------------------------------------------


def test_builder_creates_the_home_and_its_parents_and_returns_it(codex_home: Path) -> None:
    assert codex_home.is_dir()


def test_sessions_has_a_plain_rollout(codex_home: Path) -> None:
    assert list((codex_home / "sessions").rglob("rollout-*.jsonl"))


def test_sessions_has_a_zst_rollout(codex_home: Path) -> None:
    assert list((codex_home / "sessions").rglob("rollout-*.jsonl.zst"))


def test_archived_sessions_has_a_rollout(codex_home: Path) -> None:
    files = [p for p in _rollouts(codex_home) if "archived_sessions" in p.parts]
    assert files


def test_every_zst_rollout_decodes_to_jsonl_records(codex_home: Path) -> None:
    zst = [p for p in _rollouts(codex_home) if p.name.endswith(".zst")]
    assert zst
    for path in zst:
        assert _records(path), path.name


def test_rollout_stem_is_rollout_timestamp_and_thread_id(codex_home: Path) -> None:
    for path in _rollouts(codex_home):
        stem = _stem(path)
        assert stem.startswith("rollout-"), path.name
        assert stem.endswith("-" + _session_meta_id(path)), path.name


def test_every_rollout_starts_with_session_meta_with_id_and_cwd(codex_home: Path) -> None:
    for path in _rollouts(codex_home):
        first = _records(path)[0]
        assert first["type"] == "session_meta", path.name
        assert first["payload"]["id"], path.name
        assert first["payload"]["cwd"], path.name


def test_every_record_has_timestamp_type_and_payload(codex_home: Path) -> None:
    for path, rec in _all_records(codex_home):
        assert {"timestamp", "type", "payload"} <= rec.keys(), (path.name, rec)


# ---------------------------------------------------------------------------
# The builder: record kinds
# ---------------------------------------------------------------------------


def test_paginated_rollout_has_item_completed_for_each_slice_1_item_type(codex_home: Path) -> None:
    found = {
        _norm(t) for _, rec in _all_records(codex_home) if _item_completed(rec)
        for t in _item_types(rec)
    }
    for name in ("UserMessage", "AgentMessage", "Reasoning", "CommandExecution"):
        assert _norm(name) in found, (name, sorted(found))


def test_corpus_has_an_unknown_item_type(codex_home: Path) -> None:
    known = {_norm(t) for t in KNOWN_ITEM_TYPES}
    found = {
        t for _, rec in _all_records(codex_home) if _item_completed(rec)
        for t in _item_types(rec)
    }
    assert {t for t in found if _norm(t) not in known}, sorted(found)


def test_legacy_rollout_has_response_item_messages_and_no_item_completed(codex_home: Path) -> None:
    legacy = []
    for path in _rollouts(codex_home):
        recs = _records(path)
        if any(_item_completed(r) for r in recs):
            continue
        content_types = {
            d.get("type") for r in recs
            if r["type"] == "response_item" and _ptype(r) == "message"
            for d in _dicts(r["payload"])
        }
        if {"input_text", "output_text"} <= content_types:
            legacy.append(path)
    assert legacy


def test_turn_context_records_carry_a_model(codex_home: Path) -> None:
    models = [
        rec["payload"].get("model") for _, rec in _all_records(codex_home)
        if rec["type"] == "turn_context"
    ]
    assert models and all(models), models


def test_a_rollout_has_a_damaged_line_before_its_end(codex_home: Path) -> None:
    def damaged(line: bytes) -> bool:
        try:
            return not isinstance(json.loads(line), dict)
        except ValueError:
            return True

    hits = []
    for path in _rollouts(codex_home):
        lines = _lines(path)
        # A damaged line followed by a good record: the reader must skip it, not stop.
        hits += [path for i, ln in enumerate(lines[:-1]) if damaged(ln) and not damaged(lines[-1])]
    assert hits


def test_corpus_has_encrypted_reasoning(codex_home: Path) -> None:
    hits = [
        d for _, rec in _all_records(codex_home) for d in _dicts(rec["payload"])
        if any("encrypted" in k and isinstance(v, str) and v for k, v in d.items())
    ]
    assert hits


def test_corpus_references_an_image(codex_home: Path) -> None:
    def mentions_image(rec: dict) -> bool:
        for d in _dicts(rec["payload"]):
            for k, v in d.items():
                if "image" in k.lower() or (isinstance(v, str) and "image" in v.lower()):
                    return True
        return False

    assert [rec for _, rec in _all_records(codex_home) if mentions_image(rec)]


def test_parallel_tool_calls_have_outputs_out_of_call_order(codex_home: Path) -> None:
    """§4.4: a call matches its output by ``call_id``, never by position."""

    def parallel(path: Path) -> bool:
        calls: list[str] = []
        outputs: list[str] = []
        for rec in _records(path):
            for d in _dicts(rec["payload"]):
                cid = d.get("call_id")
                if not isinstance(cid, str):
                    continue
                kind = str(d.get("type", ""))
                (outputs if "output" in kind else calls).append(cid)
                break
        # First sight only: begin and end events can repeat a call_id.
        calls, outputs = list(dict.fromkeys(calls)), list(dict.fromkeys(outputs))
        for i, a in enumerate(calls):
            for b in calls[i + 1:]:
                if a != b and a in outputs and b in outputs and outputs.index(b) < outputs.index(a):
                    return True
        return False

    assert [p for p in _rollouts(codex_home) if parallel(p)]


def test_a_tool_call_has_json_arguments_inside_a_string(codex_home: Path) -> None:
    def decodes(v: Any) -> bool:
        try:
            return isinstance(json.loads(v), (dict, list))
        except (TypeError, ValueError):
            return False

    hits = [
        d for _, rec in _all_records(codex_home) for d in _dicts(rec["payload"])
        if "call_id" in d and isinstance(d.get("arguments"), str) and decodes(d["arguments"])
    ]
    assert hits


# ---------------------------------------------------------------------------
# The builder: state_5.sqlite, session_index.jsonl, and the imports file
# ---------------------------------------------------------------------------


def test_state_5_threads_has_every_section_3_column(codex_home: Path) -> None:
    conn = sqlite3.connect((codex_home / "state_5.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(threads)")}
    finally:
        conn.close()
    assert STATE_5_COLUMNS <= cols, sorted(STATE_5_COLUMNS - cols)


def test_builder_leaves_no_wal_or_shm_file(tmp_path: Path) -> None:
    # N5 asserts that its read-only open creates none; the builder must not leave one.
    from backend.tests.codex_fake import build_codex_home

    home = build_codex_home(tmp_path / "codex")
    assert not list(home.glob("state_5.sqlite-*"))


def test_state_5_rows_point_at_rollouts_with_the_same_thread_id(codex_home: Path) -> None:
    by_stem = {_stem(p): p for p in _rollouts(codex_home)}
    rows = _threads(codex_home)
    assert rows
    for row in rows:
        stem = _stem(row["rollout_path"])
        assert stem in by_stem, row["rollout_path"]
        assert _session_meta_id(by_stem[stem]) == row["id"]


def test_state_5_has_paginated_and_legacy_rows_that_match_the_records(codex_home: Path) -> None:
    rows = _rows_by_stem(codex_home)
    modes = {}
    for path in _rollouts(codex_home):
        row = rows.get(_stem(path))
        if row is not None:
            has_items = any(_item_completed(r) for r in _records(path))
            modes.setdefault(row["history_mode"], set()).add(has_items)
    assert modes.get("paginated") == {True}, modes
    assert modes.get("legacy") == {False}, modes


def test_archived_rollout_row_has_archived_set(codex_home: Path) -> None:
    rows = _rows_by_stem(codex_home)
    archived = [p for p in _rollouts(codex_home) if "archived_sessions" in p.parts]
    assert [p for p in archived if rows.get(_stem(p), {}).get("archived")]


def test_state_5_has_user_subagent_and_guardian_review_threads(codex_home: Path) -> None:
    sources = {row["thread_source"] for row in _threads(codex_home)}
    assert {"user", "subagent", "guardian_review"} <= sources, sources


def test_subagent_source_json_names_an_existing_parent_thread(codex_home: Path) -> None:
    rows = _threads(codex_home)
    ids = {row["id"] for row in rows}
    subagents = [row for row in rows if row["thread_source"] == "subagent"]
    assert subagents
    for row in subagents:
        parents = [
            d["parent_thread_id"] for d in _dicts(json.loads(row["source"]))
            if "parent_thread_id" in d
        ]
        assert parents and parents[0] in ids, row["source"]


def test_session_index_lines_have_id_thread_name_and_updated_at(codex_home: Path) -> None:
    ids = {_session_meta_id(p) for p in _rollouts(codex_home)}
    lines = (codex_home / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        rec = json.loads(line)
        assert {"id", "thread_name", "updated_at"} <= rec.keys(), rec
        assert rec["id"] in ids, rec


def test_imports_file_lists_an_imported_thread(codex_home: Path) -> None:
    ids = {_session_meta_id(p) for p in _rollouts(codex_home)}
    data = json.loads((codex_home / "external_agent_session_imports.json").read_text(encoding="utf-8"))
    records = [
        d for d in _dicts(data)
        if {"source_path", "content_sha256", "imported_thread_id"} <= d.keys()
    ]
    assert records
    assert all(r["imported_thread_id"] in ids for r in records), records


# ---------------------------------------------------------------------------
# backend/codex/zstd.py (§4.6)
# ---------------------------------------------------------------------------


def test_zstd_decompress_returns_the_original_bytes() -> None:
    from backend.codex.zstd import decompress

    data = b'{"timestamp":"t","type":"session_meta","payload":{}}\n' * 500
    assert decompress(_compress(data)) == data


def test_zstd_open_decompressed_streams_the_same_bytes(tmp_path: Path) -> None:
    from backend.codex.zstd import open_decompressed

    data = b"line\n" * 20_000
    path = tmp_path / "rollout-x.jsonl.zst"
    path.write_bytes(_compress(data))
    with open_decompressed(path) as stream:
        chunks = iter(lambda: stream.read(4096), b"")
        assert b"".join(chunks) == data


def test_zstd_decodes_the_builder_zst_rollouts(codex_home: Path) -> None:
    from backend.codex.zstd import decompress, open_decompressed

    for path in [p for p in _rollouts(codex_home) if p.name.endswith(".zst")]:
        with open_decompressed(path) as stream:
            assert stream.read() == decompress(path.read_bytes())


def test_zstd_module_imports_its_codec_lazily() -> None:
    # The MCPB closure analyzer does not follow function-body imports (§4.6).
    code = (
        "import sys, backend.codex.zstd; "
        "print('zstandard' in sys.modules, 'compression.zstd' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["False", "False"]


# ---------------------------------------------------------------------------
# pyproject.toml and uv.lock (§4.6)
# ---------------------------------------------------------------------------


def test_pyproject_requires_zstandard_below_python_3_14() -> None:
    from packaging.requirements import Requirement

    deps = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    reqs = [Requirement(d) for d in deps if Requirement(d).name == "zstandard"]
    assert len(reqs) == 1, deps
    req = reqs[0]
    assert req.specifier.contains("0.23") and not req.specifier.contains("0.22")
    assert req.marker is not None
    for version, wanted in (("3.11", True), ("3.13", True), ("3.14", False), ("3.15", False)):
        env = {"python_version": version, "python_full_version": version + ".0"}
        assert req.marker.evaluate(env) is wanted, (version, str(req.marker))


def test_uv_lock_has_zstandard_with_the_marker_and_wheels_for_3_11_to_3_13() -> None:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    root = next(p for p in lock["package"] if p["name"] == "claude-explorer")
    deps = [d for d in root.get("dependencies", []) if d["name"] == "zstandard"]
    assert deps, "uv.lock: claude-explorer does not depend on zstandard"
    # uv can fork the lock into one entry per Python range; the union must be < 3.14.
    from packaging.markers import Marker

    for version, wanted in (("3.11", True), ("3.12", True), ("3.13", True), ("3.14", False), ("3.15", False)):
        env = {"python_version": version, "python_full_version": version + ".0"}
        hits = [d for d in deps if "marker" in d and Marker(d["marker"]).evaluate(env)]
        assert bool(hits) is wanted, (version, deps)
    wheels = " ".join(
        w["url"] for p in lock["package"] if p["name"] == "zstandard" for w in p.get("wheels", [])
    )
    for tag in ("cp311", "cp312", "cp313"):
        assert tag in wheels, tag


# ---------------------------------------------------------------------------
# Settings.codex_home (§4.3, "The Codex home setting")
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from backend import config

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    monkeypatch.delenv("CODEX_HOME", raising=False)
    config.get_settings.cache_clear()
    yield home
    config.get_settings.cache_clear()


def _write_config(home: Path, body: dict) -> None:
    path = home / ".claude-explorer" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_codex_home_defaults_to_dot_codex_in_the_home_folder(isolated_home: Path) -> None:
    from backend.config import Settings

    assert Settings.load().codex_home == isolated_home / ".codex"


def test_codex_home_reads_config_json_when_env_is_unset(isolated_home: Path) -> None:
    from backend.config import Settings

    _write_config(isolated_home, {"codex_home": str(isolated_home / "from-config")})
    assert Settings.load().codex_home == isolated_home / "from-config"


def test_codex_home_env_var_wins_over_config_json(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.config import Settings

    _write_config(isolated_home, {"codex_home": str(isolated_home / "from-config")})
    monkeypatch.setenv("CODEX_HOME", str(isolated_home / "from-env"))
    assert Settings.load().codex_home == isolated_home / "from-env"


def test_codex_home_is_a_path(isolated_home: Path) -> None:
    from backend.config import Settings

    assert isinstance(Settings.load().codex_home, Path)


# ---------------------------------------------------------------------------
# The autouse fixtures (§5)
# ---------------------------------------------------------------------------


def _assert_isolated_codex_home(env_value: str | None, real_home: Path) -> None:
    assert env_value, "CODEX_HOME is not set"
    path = Path(env_value)
    assert path.is_dir(), path
    assert not any(path.iterdir()), path
    assert path.resolve() != (real_home / ".codex").resolve()


def test_backend_conftest_points_codex_home_at_an_empty_temp_folder() -> None:
    from backend.config import get_settings

    _assert_isolated_codex_home(os.environ.get("CODEX_HOME"), Path.home())
    get_settings.cache_clear()
    assert get_settings().codex_home == Path(os.environ["CODEX_HOME"])


PROBE = '''
import os
from pathlib import Path


def test_probe():
    from backend.config import Settings

    value = os.environ.get("CODEX_HOME")
    assert value, "CODEX_HOME is not set"
    path = Path(value)
    assert path.is_dir() and not any(path.iterdir()), path
    assert path.resolve() != (Path.home() / ".codex").resolve()
    assert Settings.load().codex_home == path
'''


def test_mcp_server_conftest_points_codex_home_at_an_empty_temp_folder(tmp_path: Path) -> None:
    # Load mcp_server/tests/conftest.py as a plugin into a probe run, with no CODEX_HOME.
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(PROBE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
    out = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-p", "mcp_server.tests.conftest",
            "-c", str(tmp_path / "pytest.ini"), "--rootdir", str(tmp_path),
            str(tmp_path / "test_probe.py"),
        ],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
