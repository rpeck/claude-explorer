"""Issue #96: discovery of a session's agent transcripts (membership only).

``backend.cc_subagents.discover_agents(session_file)`` lists the agent
transcripts of one Claude Code session (plan #46 §2.1, §4.1, Step 1.1):

* the current layout ``<project>/<session>/subagents/agent-<id>.jsonl``;
* the legacy layout ``<project>/agent-<id>.jsonl`` whose ``sessionId`` is
  the session.

Internal runs (``acompact-*``, ``aside_question-*``) are not listed. A file
whose real path is outside the project directory is not listed. A bad file
never fails the session. The description comes from the sibling
``agent-<id>.meta.json``.

The tree: the real recording ``fixtures/cc_code_mode/failed-refused-subagent.jsonl``
with its subagent ``subagent-run.jsonl``, and a synthetic session in the same
shape. ``write_tree`` is shared with the route, search, and MCP tests.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from backend.tests import cc_code_mode_fake as fake

pytestmark = pytest.mark.xfail(strict=True, reason="#96 subagent transcripts not implemented yet")

CC_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cc_code_mode"

# The real recording (Claude Code 2.1.331).
REAL_PROJECT = "-home-fixture-cm-sample"
REAL_SESSION = "2339aab4-744c-4556-95b8-5ee5552c1b85"
REAL_AGENT = "a47f122549f03fce6"
REAL_DESCRIPTION = "List src directory"
#: Only in the subagent's own Code Mode script (``.nothrow()``).
REAL_AGENT_CODE_WORD = "nothrow"
#: The subagent's last reply (an assistant text message).
REAL_AGENT_REPLY_UUID = "95598af8-df63-4996-b5b7-65a842f5fedc"

# The synthetic session.
PROJECT = "-home-fixture-proj-sa"
CWD = "/home/fixture/proj-sa"
SESSION = "5a6e0000-0000-4000-8000-000000000096"
OTHER_SESSION = "5a6e0000-0000-4000-8000-0000000000aa"
CUR_AGENT = "a5a0000000000096c"
CUR_DESCRIPTION = "Synthetic search agent"
LEGACY_AGENT = "5a1e9ac"
OTHER_LEGACY_AGENT = "5a2e9ac"
NO_OWNER_LEGACY_AGENT = "5a3e9ac"
BAD_META_AGENT = "abadmeta00000096"
COMPACT_RUN = "acompact-0123abcd4567ef89"
ASIDE_RUN = "aside_question-fd260f3de7d1db26"
ESCAPE_AGENT = "aescape000000096"
GARBAGE_AGENT = "agarbage00000096"

#: One word per place; no other file holds it.
WORDS = {
    "session": "sasessionword",
    "text": "saagenttextword",
    "tool": "saagenttoolword",
    "code": "saagentcodeword",
    "compact": "saagentcompactword",
    "legacy": "salegacyagentword",
    "other": "saotherword",
    "no_owner": "sanoownerword",
    "internal": "sainternalword",
    "escape": "saescapeword",
    "bad_meta": "sabadmetaword",
    #: In the synthetic session's reply and in the current agent's reply.
    "shared": "sasharedword",
}

#: Message uuids in the current-layout agent, by the word they hold.
CUR_MESSAGE = {"text": "sa-t1", "tool": "sa-j1", "code": "sa-r2", "compact": "sa-c1"}
LEGACY_MESSAGE = "sl-t1"

_TOOL_CALL = "toolu_01SATOOL0000000000000001"
_RUN = "toolu_01SARUN00000000000000002"


def _own(lines: list[dict[str, Any]], session: str | None, agent: str | None) -> list[dict[str, Any]]:
    for line in lines:
        line["cwd"] = CWD
        if session is None:
            line.pop("sessionId", None)
        else:
            line["sessionId"] = session
        if agent is not None:
            line.update(isSidechain=True, agentId=agent)
    return lines


def _tool_call(uuid: str, parent: str, call_id: str, command: str) -> dict[str, Any]:
    entry = fake.text_reply(uuid, parent, "")
    entry["message"]["content"] = [{"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}]
    return entry


def _tool_result(uuid: str, parent: str, call_id: str, output: str) -> dict[str, Any]:
    entry = fake.user(uuid, parent, "")
    entry["message"]["content"] = [{"type": "tool_result", "tool_use_id": call_id, "content": output, "is_error": False}]
    entry["toolUseResult"] = {"stdout": output, "stderr": ""}
    return entry


def _compact_summary(uuid: str, parent: str, text: str) -> dict[str, Any]:
    return {**fake.user(uuid, parent, text), "isCompactSummary": True}


def current_agent_lines() -> list[dict[str, Any]]:
    """A current-layout agent: text, a tool call, a Code Mode run, a compaction summary."""
    w = WORDS
    code = f"// {w['code']}\nawait $`ls src`.text()"
    return _own([
        fake.user("sa-u1", None, "Look at the files."),
        _tool_call("sa-a1", "sa-u1", _TOOL_CALL, "ls"),
        _tool_result("sa-j1", "sa-a1", _TOOL_CALL, f"app.py\n{w['tool']}.py"),
        fake.outer("sa-a2", "sa-j1", _RUN, code),
        fake.outer_result("sa-r2", "sa-a2", _RUN, code, result="app.py", tools=["Bash"]),
        _compact_summary("sa-c1", "sa-r2", f"Summary of the work so far ({w['compact']})."),
        fake.text_reply("sa-t1", "sa-c1", f"I looked at the files ({w['text']} {w['shared']})."),
    ], SESSION, CUR_AGENT)


def _small_agent(prefix: str, word: str, session: str | None, agent: str) -> list[dict[str, Any]]:
    return _own([
        fake.user(f"{prefix}-u1", None, "Do a small task."),
        fake.text_reply(f"{prefix}-t1", f"{prefix}-u1", f"Done ({word})."),
    ], session, agent)


def main_session_lines(session: str, word: str, reply: str = "The agents ran.") -> list[dict[str, Any]]:
    return _own([
        fake.user(f"{session[-4:]}-u1", None, f"Start the agents ({word})."),
        fake.text_reply(f"{session[-4:]}-t1", f"{session[-4:]}-u1", reply),
    ], session, None)


def _write(path: Path, lines: list[dict[str, Any]]) -> Path:
    return fake.write_lines(path, lines)


def _meta(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def write_tree(claude_dir: Path, outside_dir: Path) -> None:
    """Write the real recording and the synthetic session under ``<claude_dir>/projects``.

    ``outside_dir`` must be outside ``claude_dir``: the escape symlink points there.
    """
    projects = claude_dir / "projects"
    # The real recording, as Claude Code lays it out.
    real = projects / REAL_PROJECT
    real.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CC_FIXTURES / "failed-refused-subagent.jsonl", real / f"{REAL_SESSION}.jsonl")
    sub = real / REAL_SESSION / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CC_FIXTURES / "subagent-run.jsonl", sub / f"agent-{REAL_AGENT}.jsonl")
    _meta(sub / f"agent-{REAL_AGENT}.meta.json",
          json.dumps({"agentType": "general-purpose", "description": REAL_DESCRIPTION}))

    # The synthetic session.
    proj = projects / PROJECT
    _write(proj / f"{SESSION}.jsonl", main_session_lines(SESSION, WORDS["session"], f"The agents ran ({WORDS['shared']})."))
    _write(proj / f"{OTHER_SESSION}.jsonl", main_session_lines(OTHER_SESSION, "saothersessionword"))
    sub = proj / SESSION / "subagents"
    _write(sub / f"agent-{CUR_AGENT}.jsonl", current_agent_lines())
    _meta(sub / f"agent-{CUR_AGENT}.meta.json",
          json.dumps({"agentType": "general-purpose", "description": CUR_DESCRIPTION, "toolUseId": "toolu_x"}))
    _write(sub / f"agent-{BAD_META_AGENT}.jsonl", _small_agent("sb", WORDS["bad_meta"], SESSION, BAD_META_AGENT))
    _meta(sub / f"agent-{BAD_META_AGENT}.meta.json", '{"description": "cut off')
    _write(sub / f"agent-{COMPACT_RUN}.jsonl", _small_agent("sc", WORDS["internal"], SESSION, COMPACT_RUN))
    _write(sub / f"agent-{ASIDE_RUN}.jsonl", _small_agent("sq", WORDS["internal"], SESSION, ASIDE_RUN))
    (sub / f"agent-{GARBAGE_AGENT}.jsonl").write_text("{not json\n\x00\x01\n", encoding="utf-8")
    # A symlink to a transcript outside the project directory.
    target = _write(outside_dir / "stolen.jsonl", _small_agent("se", WORDS["escape"], SESSION, ESCAPE_AGENT))
    (sub / f"agent-{ESCAPE_AGENT}.jsonl").symlink_to(target)
    # Legacy layout: one for the session, one for another session, one without an owner.
    legacy = _small_agent("sl", WORDS["legacy"], SESSION, LEGACY_AGENT)
    _write(proj / f"agent-{LEGACY_AGENT}.jsonl", legacy)
    _write(proj / f"agent-{OTHER_LEGACY_AGENT}.jsonl",
           _small_agent("so", WORDS["other"], OTHER_SESSION, OTHER_LEGACY_AGENT))
    _write(proj / f"agent-{NO_OWNER_LEGACY_AGENT}.jsonl",
           _small_agent("sn", WORDS["no_owner"], None, NO_OWNER_LEGACY_AGENT))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Return the ``claude`` dir of a fresh tree."""
    write_tree(tmp_path / "claude", tmp_path / "outside")
    return tmp_path / "claude"


def _discover(session_file: Path) -> list[Any]:
    from backend.cc_subagents import discover_agents

    return discover_agents(session_file)


def _ids(agents: list[Any]) -> list[str]:
    return [a.agent_id for a in agents]


def _session(tree: Path) -> Path:
    return tree / "projects" / PROJECT / f"{SESSION}.jsonl"


def _by_id(tree: Path) -> dict[str, Any]:
    return {a.agent_id: a for a in _discover(_session(tree))}


# ---------------------------------------------------------------------------
# The real recording
# ---------------------------------------------------------------------------


def test_the_real_recording_lists_its_one_subagent(tree: Path) -> None:
    (agent,) = _discover(tree / "projects" / REAL_PROJECT / f"{REAL_SESSION}.jsonl")
    assert agent.agent_id == REAL_AGENT
    assert agent.layout == "current"
    assert agent.description == REAL_DESCRIPTION
    want = tree / "projects" / REAL_PROJECT / REAL_SESSION / "subagents" / f"agent-{REAL_AGENT}.jsonl"
    assert Path(agent.path).resolve() == want.resolve()


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


def test_the_current_and_the_legacy_layout_are_both_members(tree: Path) -> None:
    agents = _by_id(tree)
    assert agents[CUR_AGENT].layout == "current"
    assert agents[LEGACY_AGENT].layout == "legacy"


def test_the_list_is_exactly_the_members_sorted_by_id(tree: Path) -> None:
    ids = _ids(_discover(_session(tree)))
    members = {CUR_AGENT, LEGACY_AGENT, BAD_META_AGENT}
    assert members <= set(ids)
    # The garbage file has an id from its name and no rows; listing it or not is the coder's choice.
    assert set(ids) - members <= {GARBAGE_AGENT}
    assert ids == sorted(ids)


def test_a_legacy_agent_of_another_session_is_not_a_member(tree: Path) -> None:
    assert OTHER_LEGACY_AGENT not in _by_id(tree)
    other = _ids(_discover(tree / "projects" / PROJECT / f"{OTHER_SESSION}.jsonl"))
    assert other == [OTHER_LEGACY_AGENT]


def test_a_legacy_agent_without_session_id_belongs_to_no_session(tree: Path) -> None:
    for session in (SESSION, OTHER_SESSION):
        assert NO_OWNER_LEGACY_AGENT not in _ids(_discover(tree / "projects" / PROJECT / f"{session}.jsonl"))


@pytest.mark.parametrize("run", [COMPACT_RUN, ASIDE_RUN])
def test_internal_runs_are_excluded(tree: Path, run: str) -> None:
    assert run not in _by_id(tree)


def test_a_symlink_that_points_outside_the_project_is_not_listed(tree: Path) -> None:
    assert ESCAPE_AGENT not in _by_id(tree)


def test_every_listed_path_is_under_the_project_directory(tree: Path) -> None:
    project = (tree / "projects" / PROJECT).resolve()
    for agent in _discover(_session(tree)):
        assert Path(agent.path).resolve().is_relative_to(project), agent


def test_the_session_subdirectory_name_decides_membership_not_the_rows(tree: Path) -> None:
    """Current layout: the directory names the owner (plan §4.1), whatever the rows say."""
    sub = tree / "projects" / PROJECT / SESSION / "subagents"
    _write(sub / "agent-arows00000000096.jsonl", _small_agent("sr", "sarowsword", OTHER_SESSION, "arows00000000096"))
    assert "arows00000000096" in _by_id(tree)


# ---------------------------------------------------------------------------
# Description and tolerant parsing
# ---------------------------------------------------------------------------


def test_the_description_comes_from_the_meta_file(tree: Path) -> None:
    assert _by_id(tree)[CUR_AGENT].description == CUR_DESCRIPTION


def test_a_legacy_agent_without_a_meta_file_has_no_description(tree: Path) -> None:
    assert _by_id(tree)[LEGACY_AGENT].description is None


def test_a_bad_meta_file_gives_no_description_and_keeps_the_agent(tree: Path) -> None:
    assert _by_id(tree)[BAD_META_AGENT].description is None


def test_a_garbage_transcript_does_not_fail_discovery(tree: Path) -> None:
    assert {CUR_AGENT, LEGACY_AGENT} <= set(_by_id(tree))


def test_a_missing_session_directory_gives_the_legacy_agents_only(tree: Path) -> None:
    shutil.rmtree(tree / "projects" / PROJECT / SESSION)
    assert _ids(_discover(_session(tree))) == [LEGACY_AGENT]


def test_a_new_agent_file_is_found_on_the_next_call(tree: Path) -> None:
    assert "anew000000000096" not in _by_id(tree)
    sub = tree / "projects" / PROJECT / SESSION / "subagents"
    _write(sub / "agent-anew000000000096.jsonl", _small_agent("sw", "sanewword", SESSION, "anew000000000096"))
    assert "anew000000000096" in _by_id(tree)


def test_a_deleted_agent_file_is_gone_on_the_next_call(tree: Path) -> None:
    assert CUR_AGENT in _by_id(tree)
    (tree / "projects" / PROJECT / SESSION / "subagents" / f"agent-{CUR_AGENT}.jsonl").unlink()
    assert CUR_AGENT not in _by_id(tree)


def test_the_agent_transcript_reads_with_the_session_reader(tree: Path) -> None:
    """The real subagent reads like a session: its Code Mode run folds (#90)."""
    from backend.claude_code_reader import read_claude_code_conversation

    agent = next(a for a in _discover(tree / "projects" / REAL_PROJECT / f"{REAL_SESSION}.jsonl"))
    conv = read_claude_code_conversation(Path(agent.path))
    assert conv is not None
    runs = [b for m in conv["chat_messages"] for b in m["content"] if b["type"] == "code_run"]
    assert len(runs) == 1 and runs[0]["code_run"]["result"] == "app.py\nutil.py"


def test_the_module_imports_no_server_code() -> None:
    """The MCP bundle may import it; it must not pull in FastAPI or watchdog (AGENTS.md canary)."""
    import subprocess
    import sys

    code = (
        "import sys, backend.cc_subagents\n"
        "bad = [m for m in ('fastapi', 'watchdog', 'weasyprint', 'mitmproxy') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, "-c", code], check=True, cwd=root)


def use_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write the tree and point the settings at it (no Desktop data, no Codex,
    no Cowork). Return the corpus root. The route, search, and MCP tests use it."""
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import invalidate_detail_dict_cache

    root = tmp_path / "corpus"
    (root / "data").mkdir(parents=True)
    write_tree(root / "claude", tmp_path / "outside")
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("CLAUDE_DIR", str(root / "claude"))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(root / "desktop-app-missing"))
    monkeypatch.setenv("CODEX_HOME", str(root / "codex-home-missing"))
    monkeypatch.setattr(si, "default_index_path", lambda: root / "search-index.sqlite")
    config.get_settings.cache_clear()
    clear_cache()
    invalidate_detail_dict_cache()
    si.reset_search_index_for_tests()
    return root


def close_tree() -> None:
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import invalidate_detail_dict_cache

    si.reset_search_index_for_tests()
    invalidate_detail_dict_cache()
    clear_cache()
    config.get_settings.cache_clear()
