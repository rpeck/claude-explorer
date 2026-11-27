"""T1 (issue #93): the ticket view, backend.

The contract (orchestrator, 2026-11-27):

* A ticket key is "owner/repo#N" (owner/repo in lower case), or "#N" when the
  repo is unknown.
* Reference kinds: "title", "message", "branch", "pr_link".
* Text refs come from the title and from the text of human and assistant
  messages only: never from tool calls, tool results, or thinking.
  "owner/repo#N", a GitHub issue or pull URL, and a bare "#N" (with a word
  boundary).
* A bare "#N" in message text counts only when the session repo is known. A
  bare "#N" in a title or a branch always counts (else the "#N" ticket). An
  unresolved text ref stays in the index and becomes a ticket at query time
  when a later pr-link makes the repo known.
* The repo of a bare "#N" (and of a branch ref), in this order:
  (a) the Codex ``session_meta.git.repository_url`` when it is on github.com;
  (b) the ``prRepository`` of the session's ``pr-link`` records, when there is
  exactly one; (c) at query time, the repo that (a) or (b) gives for the other
  sessions with the same ``project_path``, when exactly one is known; else
  unknown. Nothing outside the transcripts is read.
* A branch ref: a path segment of ``git_branch`` that starts with digits and
  then "-" or the end.
* A ``pr-link`` record is a "pr_link" ref to prRepository#prNumber, and a PR
  entry on the ticket page of every ticket of its session.
* The index table ``ticket_refs`` (repo, number, conv_uuid, message_uuid, kind),
  filled by the build and the drift pass. ``repo`` is the repo written in the
  reference: NULL for a bare "#N" and for a branch, which resolve at query time.
* ``entrypoint`` and ``tickets`` on every summary and list item.
* ``GET /api/tickets/{owner}/{repo}/{number}`` ("_"/"_" for the unknown repo).
* Linear and Jira (config.json ``ticket_keys``): "<KEY>-<N>" in text counts
  only for a listed key (case-sensitive); a branch segment with "<key>-<n>" in
  any case counts for a listed key; Linear and Jira URLs always count. Keys
  are "ENG-123". ``GET /api/tickets/key/{key}``; TicketDetail has ``system``
  and ``url``. A Graphite PR URL is a GitHub ref.
* The ticket page lists every message that names the ticket, with its uuid.

Every value here is synthetic. The record shapes follow Claude Code 2.1.331
(``pr-link`` as in issue #93) and ``backend/tests/codex_fake.py``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

T0 = datetime(2026, 11, 20, 9, 0, 0, tzinfo=UTC)
ALPHA = "fixture-org/alpha"
BETA = "fixture-org/beta"
OTHER_MACHINE = "0e000000-0000-4000-8000-0000000000e0"
ORG = "0f000000-0000-4000-8000-0000000000f0"
ALPHA_PATH = "/home/fixture/alpha"
MIXED_PATH = "/home/fixture/mixed"
LONE_PATH = "/home/fixture/nowhere"

S1 = "a9300000-0000-4000-8000-000000000001"  # cli; every kind; a pr-link to alpha
S2 = "a9300000-0000-4000-8000-000000000002"  # claude-desktop; a path with no repo; bare #84
S3 = "a9300000-0000-4000-8000-000000000003"  # sdk-cli; a pr-link to beta gives the repo
S4 = "a9300000-0000-4000-8000-000000000004"  # unknown launcher; refs only in tools and thinking
S5 = "a9300000-0000-4000-8000-000000000005"  # the archive copy of another machine
S6 = "a9300000-0000-4000-8000-000000000006"  # written after the build (drift pass)
S7 = "a9300000-0000-4000-8000-000000000007"  # no entrypoint; S1's path gives the repo
S8 = "a9300000-0000-4000-8000-000000000008"  # mixed path; a pr-link to alpha
S9 = "a9300000-0000-4000-8000-000000000009"  # mixed path; a pr-link to beta
S10 = "a9300000-0000-4000-8000-000000000010"  # mixed path; no pr-link; bare #51
S11 = "a9300000-0000-4000-8000-000000000011"  # bare #84 in the title and the branch; no repo
L1 = "a9300000-0000-4000-8000-000000000021"  # Linear and Jira keys in text and in the branch
L2 = "a9300000-0000-4000-8000-000000000022"  # Linear, Jira, and Graphite URLs
CODEX = "019a9300-0000-7000-8000-0000000000c1"  # Codex; repository_url on beta
DESK = "d9300000-0000-4000-8000-0000000000d1"  # Desktop; the ref is in the title

TITLES = {
    S1: "Work on fixture-org/alpha#84",
    S2: "A session without a remote",
    S3: "A follow-up session",
    S4: "Tools and thinking only",
    S5: "The laptop session",
    S6: "A late session",
    S7: "A second session in the alpha folder",
    S8: "Mixed folder, alpha",
    S9: "Mixed folder, beta",
    S10: "Mixed folder, no PR",
    S11: "Fix #84 quickly",
    L1: "Linear and Jira keys",
    L2: "Ticket links",
}
#: config.json ``ticket_keys`` of the default corpus.
TICKET_KEYS = {"linear": ["ENG"], "jira": ["PROJ"]}
L1_PROMPT = (
    "Work on ENG-123 and PROJ-7.\n"
    "Not UTF-8, ISO-8601, GPT-5, SHA-256, XENG-123, ENG-0, ENG-12a, or eng-124."
)
LINEAR_URL = "https://linear.app/fixture-ws/issue/OPS-9/some-slug"
JIRA_URL = "https://fixture.atlassian.net/browse/DATA-4"
GRAPHITE_URL = "https://app.graphite.dev/github/pr/fixture-org/alpha/186/some-title"
S1_PROMPT = "Please fix #84 now.\nThe log is long."
S1_URL_TEXT = "See https://github.com/fixture-org/beta/issues/84 too."
S1_BRANCH = "fix/84-search-tools"
S1_PR_URL = "https://github.com/fixture-org/alpha/pull/85"
S3_PR_URL = "https://github.com/fixture-org/beta/pull/7"


def mu(session: str, n: int) -> str:
    return f"{session[:8]}-{session[-4:]}-4000-8000-{n:012d}"


@dataclass
class Session:
    """One Claude Code transcript. Each record's parent is the record before it."""

    sid: str
    cwd: str
    branch: str = ""
    entrypoint: str | None = "cli"
    lines: list[dict[str, Any]] = field(default_factory=list)
    n: int = 0
    last: str | None = None
    offset: int = 0

    def __post_init__(self) -> None:
        self.lines.append(
            {"type": "custom-title", "customTitle": TITLES[self.sid], "sessionId": self.sid}
        )

    def _when(self) -> str:
        return (
            (T0 + timedelta(minutes=self.offset, seconds=self.n))
            .isoformat()
            .replace("+00:00", "Z")
        )

    def _base(self, type_: str, **kw: Any) -> dict[str, Any]:
        self.n += 1
        uuid = mu(self.sid, self.n)
        rec: dict[str, Any] = {
            "parentUuid": self.last,
            "isSidechain": False,
            "type": type_,
            "uuid": uuid,
            "timestamp": self._when(),
            "userType": "external",
            "cwd": self.cwd,
            "sessionId": self.sid,
            "version": "2.1.331",
            "gitBranch": self.branch,
            **kw,
        }
        if self.entrypoint is not None:
            rec["entrypoint"] = self.entrypoint
        self.lines.append(rec)
        self.last = uuid
        return rec

    def prompt(self, text: str) -> str:
        return self._base("user", message={"role": "user", "content": text})["uuid"]

    def _assistant(self, content: list[dict[str, Any]]) -> str:
        return self._base(
            "assistant",
            message={
                "id": f"msg_{self.sid[:8]}_{self.n + 1}",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "type": "message",
                "content": content,
            },
        )["uuid"]

    def text(self, text: str) -> str:
        return self._assistant([{"type": "text", "text": text}])

    def thinking(self, text: str) -> str:
        return self._assistant(
            [
                {"type": "thinking", "thinking": text, "signature": "c3ludGhldGlj"},
                {"type": "text", "text": "Thought about it."},
            ]
        )

    def tool(self, tool_id: str, tool_input: dict[str, Any], output: str) -> str:
        call = self._assistant(
            [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": tool_input}]
        )
        self._base(
            "user",
            message={
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tool_id, "content": output}
                ],
            },
            toolUseResult={"stdout": output, "stderr": ""},
            sourceToolAssistantUUID=call,
        )
        return call

    def pr_link(self, number: int, repo: str, url: str) -> None:
        self.n += 1
        self.lines.append(
            {
                "type": "pr-link",
                "sessionId": self.sid,
                "prNumber": number,
                "prRepository": repo,
                "prUrl": url,
                "timestamp": self._when(),
            }
        )

    def write(self, projects: Path) -> Path:
        folder = "-" + self.cwd.strip("/").replace("/", "-")
        path = projects / folder / f"{self.sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in self.lines), encoding="utf-8")
        return path


#: The uuids that the tests name. ``build_cc_sessions`` fills it.
U: dict[str, str] = {}


def build_cc_sessions() -> dict[str, Session]:
    s1 = Session(S1, ALPHA_PATH, branch=S1_BRANCH, entrypoint="cli", offset=1)
    U["s1_prompt"] = s1.prompt(S1_PROMPT)
    U["s1_url"] = s1.text(S1_URL_TEXT)
    s1.tool("toolu_s1_log", {"command": "git log fixture-org/alpha#901"}, "Merge pull request #902")
    s1.pr_link(85, ALPHA, S1_PR_URL)
    s1.text("Opened the PR.")
    U["s1_done"] = s1.text("Done with fixture-org/alpha#84.")

    s2 = Session(S2, LONE_PATH, entrypoint="claude-desktop", offset=2)
    U["s2_prompt"] = s2.prompt("Bare #84 with no remote.")
    U["s2_noise"] = s2.text("Not refs: abc#12, #12a, #0, and a heading.\n# Heading")
    U["s2_case"] = s2.text("Also FIXTURE-ORG/Alpha#84 (#84).")

    s3 = Session(S3, "/home/fixture/elsewhere", entrypoint="sdk-cli", offset=3)
    U["s3_prompt"] = s3.prompt("Follow up on #84.")
    s3.pr_link(7, BETA, S3_PR_URL)
    s3.text("Done.")

    s4 = Session(S4, "/home/fixture/tools", entrypoint="some-new-launcher", offset=4)
    s4.prompt("Run the tool.")
    s4.tool("toolu_s4", {"command": "echo fixture-org/alpha#84"}, "fixture-org/alpha#84")
    s4.thinking("Maybe fixture-org/alpha#84 matters.")

    s7 = Session(S7, ALPHA_PATH, entrypoint=None, offset=7)
    U["s7_prompt"] = s7.prompt("Look at #77 next.")
    s7.text("Looked.")

    s8 = Session(S8, MIXED_PATH, offset=10)
    U["s8_prompt"] = s8.prompt("Bare #50 in the alpha session.")
    s8.pr_link(86, ALPHA, "https://github.com/fixture-org/alpha/pull/86")

    s9 = Session(S9, MIXED_PATH, offset=11)
    s9.prompt("Nothing to see.")
    s9.pr_link(87, BETA, "https://github.com/fixture-org/beta/pull/87")

    s10 = Session(S10, MIXED_PATH, offset=12)
    U["s10_prompt"] = s10.prompt("Bare #51 in a folder with two repos.")
    s11 = Session(S11, "/home/fixture/solo", branch="fix/84-solo", offset=13)
    s11.prompt("Go.")

    l1 = Session(L1, "/home/fixture/linear", branch="rpeck/eng-123-fix-search", offset=14)
    U["l1_prompt"] = l1.prompt(L1_PROMPT)
    U["l1_done"] = l1.text("ENG-123 is done.")

    l2 = Session(L2, "/home/fixture/links", offset=15)
    U["l2_links"] = l2.prompt(f"See {LINEAR_URL} and {JIRA_URL}.\nReview {GRAPHITE_URL}")
    return {s.sid: s for s in (s1, s2, s3, s4, s7, s8, s9, s10, s11, l1, l2)}


def laptop_session() -> Session:
    s5 = Session(S5, "/home/fixture/laptop", entrypoint="cli", offset=5)
    U["s5_prompt"] = s5.prompt("fixture-org/alpha#84 from the laptop.")
    s5.text("Laptop reply.")
    return s5


def desktop_conversation() -> dict[str, Any]:
    when = (T0 + timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
    return {
        "uuid": DESK,
        "name": "Ticket fixture-org/alpha#84 in Desktop",
        "summary": "",
        "model": "claude-sonnet-5-5",
        "created_at": when,
        "updated_at": when,
        "is_starred": False,
        "current_leaf_message_uuid": f"{DESK}-m1",
        "source": "CLAUDE_AI",
        "organization_id": ORG,
        "chat_messages": [
            {
                "uuid": f"{DESK}-m1",
                "sender": "human",
                "text": "Step #2 of the list.",
                "content": [{"type": "text", "text": "Step #2 of the list."}],
                "created_at": when,
                "updated_at": when,
                "parent_message_uuid": None,
            }
        ],
    }


def codex_lines() -> list[dict[str, Any] | str]:
    from backend.tests import codex_fake as fake

    return fake._simple_paginated_lines(
        T0 + timedelta(minutes=8),
        CODEX,
        "/home/fixture/codex-beta",
        "Fix #84 in Codex.",
        "Codex looked at #84.",
        git={
            "commit_hash": "0" * 40,
            "branch": "main",
            "repository_url": "https://github.com/fixture-org/beta.git",
        },
    )


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


@dataclass
class Corpus:
    root: Path
    data_dir: Path
    projects: Path
    index: Any

    def store(self) -> Any:
        from backend.store import ConversationStore

        return ConversationStore()

    def summaries(self) -> dict[str, Any]:
        from backend.store import invalidate_detail_dict_cache

        invalidate_detail_dict_cache()
        return {c.uuid: c for c in self.store().list_conversations()}

    def drift(self) -> None:
        from backend import search_index as si

        si.update_drifted_files(self.store(), index=self.index)


def _reset_caches() -> None:
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import invalidate_detail_dict_cache

    si.reset_search_index_for_tests()
    invalidate_detail_dict_cache()
    clear_cache()
    config.get_settings.cache_clear()


@contextmanager
def use_ticket_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ticket_keys: dict[str, list[str]] | None = TICKET_KEYS,
) -> Iterator[Corpus]:
    """Write the sessions of every source, and index them as the process singleton.

    ``ticket_keys`` goes into config.json (None: no key). The MCP tests use it too.
    """
    from backend import config
    from backend import search_index as si
    from backend.archive_sync import SyncContext
    from backend.sources.codex import clear_codex_caches
    from backend.sources.registry import get_adapter
    from backend.store import ConversationStore
    from backend.tests import codex_fake as fake
    from backend.tests._platform_home import patch_home

    root = tmp_path / "corpus"
    home = patch_home(monkeypatch, root / "home")
    home.mkdir(parents=True)
    if ticket_keys is not None:
        cfg = home / ".claude-explorer" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"ticket_keys": ticket_keys}), encoding="utf-8")
    claude = root / "claude"
    projects = claude / "projects"
    for s in build_cc_sessions().values():
        s.write(projects)
    data_dir = root / "explorer" / "conversations"
    laptop_session().write(data_dir / "machines" / OTHER_MACHINE / "claude-code" / "projects")
    desk = data_dir / "by-org" / ORG / f"{DESK}.json"
    desk.parent.mkdir(parents=True)
    desk.write_text(json.dumps(desktop_conversation()), encoding="utf-8")

    codex_home = fake.build_codex_home(home / ".codex")
    fake.write_rollout(
        fake.rollout_path(codex_home, T0 + timedelta(minutes=8), CODEX), codex_lines()
    )

    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(root / "desktop-app-missing"))
    monkeypatch.setattr(si, "default_index_path", lambda: root / "explorer" / "search-index.sqlite")
    _reset_caches()
    clear_codex_caches()
    get_adapter("CODEX", ConversationStore()).archive_sync(
        SyncContext(settings=config.get_settings(), reason="cli", lock_timeout_s=10)
    )
    idx = si.SearchIndex(root / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    try:
        yield Corpus(root, data_dir, projects, idx)
    finally:
        idx.close()
        clear_codex_caches()
        _reset_caches()


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Corpus]:
    with use_ticket_corpus(tmp_path, monkeypatch) as c:
        yield c


@pytest.fixture
def corpus_no_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Corpus]:
    with use_ticket_corpus(tmp_path, monkeypatch, ticket_keys=None) as c:
        yield c


def _client() -> Any:
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.store import invalidate_detail_dict_cache

    invalidate_detail_dict_cache()
    # No ``with``: the lifespan (watcher, index build) stays off.
    return TestClient(app)


def ticket(owner: str, repo: str, number: int | str) -> dict[str, Any]:
    r = _client().get(f"/api/tickets/{owner}/{repo}/{number}")
    assert r.status_code == 200, r.text
    return r.json()


def session_uuids(body: dict[str, Any]) -> list[str]:
    return [s["conversation_uuid"] for s in body["sessions"]]


def entry(body: dict[str, Any], uuid: str) -> dict[str, Any]:
    (e,) = [s for s in body["sessions"] if s["conversation_uuid"] == uuid]
    return e


def refs_rows(corpus: Corpus, conv_uuid: str) -> set[tuple[Any, ...]]:
    con = sqlite3.connect(corpus.root / "index.sqlite")
    try:
        return set(
            con.execute(
                "SELECT repo, number, message_uuid, kind FROM ticket_refs"
                " WHERE conv_uuid = ? AND system = 'github'",
                (conv_uuid,),
            )
        )
    finally:
        con.close()


def _no_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every module that looks up the index gets None (as with the cloud-sync gate)."""
    import sys

    from backend import search_index as si

    monkeypatch.setattr(si, "_search_index", None)
    for mod in list(sys.modules.values()):
        name = getattr(mod, "__name__", "")
        if name.startswith(("backend", "mcp_server")) and hasattr(mod, "get_search_index"):
            monkeypatch.setattr(mod, "get_search_index", lambda: None)


# ---------------------------------------------------------------------------
# The pure functions (backend/tickets.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "default_repo", "expected"),
    [
        ("Fix fixture-org/alpha#84 now", None, [(ALPHA, 84)]),
        ("Fix Fixture-Org/Alpha#84", None, [(ALPHA, 84)]),
        ("See https://github.com/fixture-org/beta/issues/84.", None, [(BETA, 84)]),
        ("See https://github.com/fixture-org/beta/pull/85", None, [(BETA, 85)]),
        ("Bare #84 here", None, [(None, 84)]),
        ("Bare #84 here", ALPHA, [(ALPHA, 84)]),
        ("(#84)", None, [(None, 84)]),
        ("PR #84, then #85.", None, [(None, 84), (None, 85)]),
        ("#84", None, [(None, 84)]),
        ("abc#84", None, []),
        ("#84a", None, []),
        ("#0", None, []),
        ("# Heading", None, []),
        ("no refs at all", ALPHA, []),
        ("fixture-org/alpha#84 and #84", BETA, [(ALPHA, 84), (BETA, 84)]),
        (f"Review {GRAPHITE_URL}", None, [(ALPHA, 186)]),
        ("https://app.graphite.dev/github/pr/fixture-org/beta/9", None, [(BETA, 9)]),
        ("ENG-123 is a Linear key, not a GitHub ref", None, []),
    ],
)
def test_extract_text_refs(
    text: str, default_repo: str | None, expected: list[tuple[str | None, int]]
) -> None:
    from backend.tickets import extract_text_refs

    assert sorted(extract_text_refs(text, default_repo), key=repr) == sorted(expected, key=repr)


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("fix/84-search-tools", 84),
        ("fix/84", 84),
        ("84-short", 84),
        ("feat/ticket-view", None),
        ("main", None),
        ("release-1.2.0", None),
        ("", None),
    ],
)
def test_branch_number(branch: str, expected: int | None) -> None:
    from backend.tickets import branch_number

    assert branch_number(branch) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("fixture-org/alpha#84", (ALPHA, 84)),
        ("FIXTURE-ORG/ALPHA#84", (ALPHA, 84)),
        ("#84", (None, 84)),
        ("https://github.com/fixture-org/alpha/issues/84", (ALPHA, 84)),
        ("https://github.com/fixture-org/alpha/pull/84", (ALPHA, 84)),
        ("alpha-84", None),
        ("fixture-org/alpha#0", None),
        ("fixture-org/alpha#x", None),
        ("", None),
    ],
)
def test_parse_ticket_key(value: str, expected: tuple[str | None, int] | None) -> None:
    from backend.tickets import parse_ticket_key

    assert parse_ticket_key(value) == expected


def test_ticket_key_formats_known_and_unknown_repos() -> None:
    from backend.tickets import ticket_key

    assert ticket_key(ALPHA, 84) == "fixture-org/alpha#84"
    assert ticket_key("Fixture-Org/Alpha", 84) == "fixture-org/alpha#84"
    assert ticket_key(None, 84) == "#84"


# ---------------------------------------------------------------------------
# The ticket_refs table
# ---------------------------------------------------------------------------


def test_the_build_records_every_kind_of_ref(corpus: Corpus) -> None:
    assert refs_rows(corpus, S1) == {
        (ALPHA, 84, None, "title"),
        (None, 84, U["s1_prompt"], "message"),
        (BETA, 84, U["s1_url"], "message"),
        (ALPHA, 84, U["s1_done"], "message"),
        (None, 84, None, "branch"),
        (ALPHA, 85, None, "pr_link"),
    }


def test_text_that_only_looks_like_a_ref_is_not_a_ref(corpus: Corpus) -> None:
    assert refs_rows(corpus, S2) == {
        (None, 84, U["s2_prompt"], "message"),
        (ALPHA, 84, U["s2_case"], "message"),
        (None, 84, U["s2_case"], "message"),
    }


def test_refs_in_tool_calls_tool_results_and_thinking_do_not_count(corpus: Corpus) -> None:
    assert refs_rows(corpus, S4) == set()
    assert not [r for r in refs_rows(corpus, S1) if r[1] in (901, 902)]


def test_codex_messages_are_indexed(corpus: Corpus) -> None:
    rows = refs_rows(corpus, CODEX)
    # The title is the first user message, so it is a title ref too.
    assert {(r[0], r[1], r[3]) for r in rows} == {(None, 84, "message"), (None, 84, "title")}
    assert len([r for r in rows if r[3] == "message"]) == 2  # the user message and the reply


def test_a_desktop_title_is_a_ref(corpus: Corpus) -> None:
    # The bare "#2" stays in the index, unresolved: a Desktop chat has no repo.
    assert refs_rows(corpus, DESK) == {(ALPHA, 84, None, "title"), (None, 2, f"{DESK}-m1", "message")}


def test_the_archive_copy_of_another_machine_is_indexed(corpus: Corpus) -> None:
    assert refs_rows(corpus, S5) == {(ALPHA, 84, U["s5_prompt"], "message")}


def test_the_drift_pass_adds_the_refs_of_a_new_session(corpus: Corpus) -> None:
    s6 = Session(S6, "/home/fixture/late", offset=9)
    prompt = s6.prompt("Late work on fixture-org/alpha#84.")
    s6.write(corpus.projects)
    corpus.drift()
    assert refs_rows(corpus, S6) == {(ALPHA, 84, prompt, "message")}
    assert S6 in session_uuids(ticket("fixture-org", "alpha", 84))


def test_the_drift_pass_replaces_the_refs_of_a_changed_session(corpus: Corpus) -> None:
    s2 = Session(S2, LONE_PATH, entrypoint="claude-desktop", offset=2)
    prompt = s2.prompt("Now about fixture-org/beta#12 only.")
    path = s2.write(corpus.projects)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    corpus.drift()
    assert refs_rows(corpus, S2) == {(BETA, 12, prompt, "message")}
    assert S2 not in session_uuids(ticket("fixture-org", "alpha", 84))


# ---------------------------------------------------------------------------
# The repo of a bare ref
# ---------------------------------------------------------------------------


def test_a_bare_ref_takes_the_repo_of_the_sessions_pr_link(corpus: Corpus) -> None:
    assert S3 in session_uuids(ticket("fixture-org", "beta", 84))
    assert S1 in session_uuids(ticket("fixture-org", "alpha", 84))


def test_a_branch_takes_the_repo_of_the_session(corpus: Corpus) -> None:
    assert "branch" in entry(ticket("fixture-org", "alpha", 84), S1)["kinds"]


def test_a_bare_ref_takes_the_codex_repository_url(corpus: Corpus) -> None:
    assert CODEX in session_uuids(ticket("fixture-org", "beta", 84))


def test_a_bare_ref_takes_the_repo_of_another_session_in_the_same_folder(
    corpus: Corpus,
) -> None:
    assert session_uuids(ticket("fixture-org", "alpha", 77)) == [S7]
    assert session_uuids(ticket("_", "_", 77)) == []


def test_the_sessions_own_pr_link_wins_over_the_folder(corpus: Corpus) -> None:
    assert session_uuids(ticket("fixture-org", "alpha", 50)) == [S8]


def test_a_folder_with_two_repos_gives_no_repo(corpus: Corpus) -> None:
    assert session_uuids(ticket("fixture-org", "alpha", 51)) == []
    assert session_uuids(ticket("fixture-org", "beta", 51)) == []
    assert session_uuids(ticket("_", "_", 51)) == []
    assert corpus.summaries()[S10].tickets == []


def test_an_unresolved_bare_ref_in_text_is_not_a_ticket(corpus: Corpus) -> None:
    assert "#84" not in corpus.summaries()[S2].tickets
    assert S2 not in session_uuids(ticket("_", "_", 84))
    assert S2 not in session_uuids(ticket("fixture-org", "beta", 84))


def test_a_bare_ref_in_desktop_text_is_not_a_ticket(corpus: Corpus) -> None:
    assert corpus.summaries()[DESK].tickets == ["fixture-org/alpha#84"]
    assert session_uuids(ticket("_", "_", 2)) == []


def test_a_bare_ref_in_a_title_or_branch_without_a_repo_is_the_unknown_ticket(
    corpus: Corpus,
) -> None:
    body = ticket("_", "_", 84)
    assert session_uuids(body) == [S11]
    assert entry(body, S11)["kinds"] == ["branch", "title"]
    assert corpus.summaries()[S11].tickets == ["#84"]


def test_a_later_pr_link_resolves_older_sessions_without_a_reindex(corpus: Corpus) -> None:
    assert S2 not in session_uuids(ticket("fixture-org", "beta", 84))
    assert "fixture-org/beta#84" not in corpus.summaries()[S2].tickets
    before = refs_rows(corpus, S2)
    late = Session(S6, LONE_PATH, offset=9)
    late.prompt("Open the PR.")
    late.pr_link(90, BETA, "https://github.com/fixture-org/beta/pull/90")
    late.write(corpus.projects)
    corpus.drift()
    assert refs_rows(corpus, S2) == before  # S2 was not indexed again
    assert S2 in session_uuids(ticket("fixture-org", "beta", 84))
    assert S2 not in session_uuids(ticket("_", "_", 84))
    assert "fixture-org/beta#84" in corpus.summaries()[S2].tickets


# ---------------------------------------------------------------------------
# entrypoint and tickets on the summaries
# ---------------------------------------------------------------------------


def test_entrypoint_comes_from_the_records_and_unknown_values_stay(corpus: Corpus) -> None:
    s = corpus.summaries()
    assert {u: s[u].entrypoint for u in (S1, S2, S3, S4, S7)} == {
        S1: "cli",
        S2: "claude-desktop",
        S3: "sdk-cli",
        S4: "some-new-launcher",
        S7: None,
    }


def test_codex_entrypoint_is_the_originator(corpus: Corpus) -> None:
    assert corpus.summaries()[CODEX].entrypoint == "codex_cli_rs"


def test_a_desktop_conversation_has_no_entrypoint(corpus: Corpus) -> None:
    assert corpus.summaries()[DESK].entrypoint is None


def test_summaries_carry_their_ticket_keys_sorted_and_unique(corpus: Corpus) -> None:
    s = corpus.summaries()
    assert s[S1].tickets == [
        "fixture-org/alpha#84",
        "fixture-org/alpha#85",
        "fixture-org/beta#84",
    ]
    assert s[S2].tickets == ["fixture-org/alpha#84"]
    assert s[S7].tickets == ["fixture-org/alpha#77"]
    assert s[S4].tickets == []


def _list_rows() -> list[dict[str, Any]]:
    r = _client().get("/api/conversations")
    assert r.status_code == 200, r.text
    rows = r.json()
    return rows["conversations"] if isinstance(rows, dict) else rows


def test_the_list_route_carries_entrypoint_and_tickets(corpus: Corpus) -> None:
    (row,) = [c for c in _list_rows() if c["uuid"] == S3]
    assert (row["entrypoint"], row["tickets"]) == (
        "sdk-cli",
        ["fixture-org/beta#7", "fixture-org/beta#84"],
    )


def test_the_detail_route_carries_entrypoint_and_tickets(corpus: Corpus) -> None:
    r = _client().get(f"/api/conversations/{S3}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["entrypoint"], body["tickets"]) == (
        "sdk-cli",
        ["fixture-org/beta#7", "fixture-org/beta#84"],
    )


def test_tickets_are_empty_when_there_is_no_index(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_index(monkeypatch)
    s = corpus.summaries()
    assert s[S1].tickets == []
    assert s[S1].entrypoint == "cli"


# ---------------------------------------------------------------------------
# GET /api/tickets/{owner}/{repo}/{number}
# ---------------------------------------------------------------------------


def test_the_ticket_page_lists_every_session_across_sources_and_machines(
    corpus: Corpus,
) -> None:
    body = ticket("fixture-org", "alpha", 84)
    assert (body["key"], body["repo"], body["number"]) == ("fixture-org/alpha#84", ALPHA, 84)
    assert set(session_uuids(body)) == {S1, S2, S5, DESK}


def test_the_sessions_are_newest_first(corpus: Corpus) -> None:
    assert session_uuids(ticket("fixture-org", "alpha", 84)) == [DESK, S5, S2, S1]


def test_a_session_entry_has_exactly_the_contract_fields(corpus: Corpus) -> None:
    e = entry(ticket("fixture-org", "alpha", 84), S1)
    assert set(e) == {
        "conversation_uuid",
        "name",
        "source",
        "project_path",
        "entrypoint",
        "machine_id",
        "machine_label",
        "state",
        "created_at",
        "updated_at",
        "kinds",
        "refs",
    }
    assert (e["name"], e["source"], e["entrypoint"], e["project_path"]) == (
        TITLES[S1],
        "CLAUDE_CODE",
        "cli",
        ALPHA_PATH,
    )


def test_a_session_entry_lists_its_kinds_sorted(corpus: Corpus) -> None:
    assert entry(ticket("fixture-org", "alpha", 84), S1)["kinds"] == [
        "branch",
        "message",
        "title",
    ]


def test_a_message_ref_links_to_its_message_with_a_snippet(corpus: Corpus) -> None:
    refs = entry(ticket("fixture-org", "alpha", 84), S1)["refs"]
    msg = next(r for r in refs if r["kind"] == "message")
    assert msg["message_uuid"] == U["s1_prompt"]
    assert "#84" in msg["snippet"]
    assert set(msg) == {"kind", "message_uuid", "snippet"}


def test_every_message_that_names_the_ticket_is_listed_in_order(corpus: Corpus) -> None:
    refs = entry(ticket("fixture-org", "alpha", 84), S1)["refs"]
    assert [r["message_uuid"] for r in refs if r["kind"] == "message"] == [
        U["s1_prompt"],
        U["s1_done"],
    ]


def test_the_ticket_detail_has_exactly_the_contract_fields(corpus: Corpus) -> None:
    body = ticket("fixture-org", "alpha", 84)
    assert set(body) == {"key", "repo", "number", "system", "url", "sessions", "prs"}
    assert (body["system"], body["url"]) == (
        "github",
        "https://github.com/fixture-org/alpha/issues/84",
    )
    unknown = ticket("_", "_", 84)
    assert (unknown["system"], unknown["url"]) == ("github", None)


def test_title_and_branch_refs_have_no_message(corpus: Corpus) -> None:
    refs = entry(ticket("fixture-org", "alpha", 84), S1)["refs"]
    by_kind = {r["kind"]: r for r in refs if r["kind"] in ("title", "branch")}
    assert by_kind["title"]["message_uuid"] is None
    assert by_kind["title"]["snippet"] == TITLES[S1]
    assert by_kind["branch"]["message_uuid"] is None
    assert by_kind["branch"]["snippet"] == S1_BRANCH


def test_one_message_with_two_refs_to_one_ticket_is_one_ref(corpus: Corpus) -> None:
    refs = entry(ticket("fixture-org", "alpha", 84), S2)["refs"]
    assert [r["message_uuid"] for r in refs] == [U["s2_case"]]


def test_the_ticket_page_lists_the_pr_links_of_its_sessions(corpus: Corpus) -> None:
    (pr,) = ticket("fixture-org", "alpha", 84)["prs"]
    assert set(pr) == {"number", "repository", "url", "conversation_uuid", "timestamp"}
    assert (pr["number"], pr["repository"], pr["url"], pr["conversation_uuid"]) == (
        85,
        ALPHA,
        S1_PR_URL,
        S1,
    )


def test_two_repos_with_the_same_number_are_two_tickets(corpus: Corpus) -> None:
    beta = ticket("fixture-org", "beta", 84)
    assert set(session_uuids(beta)) == {S1, S3, CODEX}
    assert [p["url"] for p in beta["prs"]] == [S1_PR_URL, S3_PR_URL]  # by timestamp


def test_the_unknown_repo_ticket_has_only_unresolved_refs(corpus: Corpus) -> None:
    body = ticket("_", "_", 84)
    assert (body["key"], body["repo"], body["number"]) == ("#84", None, 84)
    assert session_uuids(body) == [S11]


def test_a_pr_link_is_a_ticket_of_its_own(corpus: Corpus) -> None:
    body = ticket("fixture-org", "alpha", 85)
    assert session_uuids(body) == [S1]
    assert entry(body, S1)["kinds"] == ["pr_link"]


def test_the_route_ignores_the_case_of_owner_and_repo(corpus: Corpus) -> None:
    assert set(session_uuids(ticket("Fixture-Org", "ALPHA", 84))) == {S1, S2, S5, DESK}


def test_an_unknown_ticket_is_empty(corpus: Corpus) -> None:
    body = ticket("fixture-org", "alpha", 9999)
    assert (body["sessions"], body["prs"]) == ([], [])


@pytest.mark.parametrize("number", ["0", "-1", "abc"])
def test_a_bad_number_is_400(corpus: Corpus, number: str) -> None:
    r = _client().get(f"/api/tickets/fixture-org/alpha/{number}")
    assert r.status_code == 400, r.text


@pytest.mark.xfail(
    strict=True, reason="needs A2 machine_id (#92); remove when feat/multi-machine merges"
)
def test_the_session_from_another_machine_names_its_machine(corpus: Corpus) -> None:
    assert entry(ticket("fixture-org", "alpha", 84), S5)["machine_id"] == OTHER_MACHINE


def test_the_route_is_503_without_an_index(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_index(monkeypatch)
    r = _client().get("/api/tickets/fixture-org/alpha/84")
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "ticket index not ready"


def test_the_route_is_503_while_the_index_builds(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(type(corpus.index), "is_ready", lambda self: False)
    r = _client().get("/api/tickets/fixture-org/alpha/84")
    assert r.status_code == 503, r.text


# ---------------------------------------------------------------------------
# Linear and Jira keys (config.json ticket_keys), and Graphite
# ---------------------------------------------------------------------------


def key_ticket(key: str) -> dict[str, Any]:
    r = _client().get(f"/api/tickets/key/{key}")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_listed_linear_key_in_text_and_branch_is_a_ticket(corpus: Corpus) -> None:
    body = key_ticket("ENG-123")
    assert (body["key"], body["repo"], body["number"], body["system"]) == ("ENG-123", None, 123, "linear")
    assert session_uuids(body) == [L1]
    assert entry(body, L1)["kinds"] == ["branch", "message"]


def test_a_linear_ticket_lists_every_message_that_names_it(corpus: Corpus) -> None:
    refs = entry(key_ticket("ENG-123"), L1)["refs"]
    assert [r["message_uuid"] for r in refs if r["kind"] == "message"] == [U["l1_prompt"], U["l1_done"]]
    (branch,) = [r for r in refs if r["kind"] == "branch"]
    assert (branch["message_uuid"], branch["snippet"]) == (None, "rpeck/eng-123-fix-search")


def test_a_linear_key_without_a_seen_url_links_to_linear(corpus: Corpus) -> None:
    assert key_ticket("ENG-123")["url"] == "https://linear.app/issue/ENG-123"


def test_a_listed_jira_key_is_a_ticket_without_a_url(corpus: Corpus) -> None:
    body = key_ticket("PROJ-7")
    assert (body["key"], body["system"], body["url"]) == ("PROJ-7", "jira", None)
    assert session_uuids(body) == [L1]


def test_only_listed_keys_count_in_text(corpus: Corpus) -> None:
    # UTF-8, ISO-8601, GPT-5, SHA-256, XENG-123, ENG-0, ENG-12a, eng-124.
    assert corpus.summaries()[L1].tickets == ["ENG-123", "PROJ-7"]


def test_a_linear_url_counts_even_when_its_key_is_not_listed(corpus: Corpus) -> None:
    body = key_ticket("OPS-9")
    assert (body["system"], session_uuids(body)) == ("linear", [L2])
    assert body["url"].startswith("https://linear.app/fixture-ws/issue/OPS-9")
    (ref,) = entry(body, L2)["refs"]
    assert (ref["kind"], ref["message_uuid"]) == ("message", U["l2_links"])


def test_a_jira_url_counts_and_gives_the_url(corpus: Corpus) -> None:
    body = key_ticket("DATA-4")
    assert (body["system"], body["url"], session_uuids(body)) == ("jira", JIRA_URL, [L2])


def test_a_graphite_url_is_a_github_ref(corpus: Corpus) -> None:
    assert session_uuids(ticket("fixture-org", "alpha", 186)) == [L2]
    assert refs_rows(corpus, L2) == {(ALPHA, 186, U["l2_links"], "message")}


def test_summaries_sort_keys_with_github_tickets(corpus: Corpus) -> None:
    assert corpus.summaries()[L2].tickets == ["DATA-4", "OPS-9", "fixture-org/alpha#186"]


def test_the_list_route_carries_linear_keys(corpus: Corpus) -> None:
    (row,) = [c for c in _list_rows() if c["uuid"] == L1]
    assert row["tickets"] == ["ENG-123", "PROJ-7"]


def test_without_ticket_keys_only_urls_count(corpus_no_keys: Corpus) -> None:
    s = corpus_no_keys.summaries()
    assert s[L1].tickets == []
    assert s[L2].tickets == ["DATA-4", "OPS-9", "fixture-org/alpha#186"]
    r = _client().get("/api/tickets/key/ENG-123")
    assert r.status_code == 200, r.text
    assert r.json()["sessions"] == []


@pytest.mark.parametrize("key", ["ENG-0", "eng-123", "ENG123", "ENG-x"])
def test_a_bad_key_is_400(corpus: Corpus, key: str) -> None:
    r = _client().get(f"/api/tickets/key/{key}")
    assert r.status_code == 400, r.text


def test_the_key_route_is_503_without_an_index(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_index(monkeypatch)
    r = _client().get("/api/tickets/key/ENG-123")
    assert r.status_code == 503, r.text
