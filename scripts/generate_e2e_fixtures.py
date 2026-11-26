#!/usr/bin/env python3
"""Generate deterministic, synthetic e2e test fixtures.

This script writes a small set of "looks like real Claude data" files
into ``backend/tests/fixtures/`` that the Playwright e2e suite (and the
backend pytest suite) can run against, so external contributors don't
need Raymond's actual ``~/.claude-explorer/`` or ``~/.claude/``
directories to test the app.

What it produces (idempotent — re-run anytime to refresh):

  backend/tests/fixtures/desktop/by-org/<ORG_UUID>/<UUID>.json
      • Three Claude Desktop conversations:
          - "Phase 5 fixture: TLS handshakes" (long, used by jump-buttons)
          - "Phase 5 fixture: Branch tree" (has_branches=true)
          - "Phase 5 fixture: Tool calls" (tool_use + tool_result blocks)
      • Each conversation embeds a unique searchable token
        (NEEDLE_HANDSHAKE / NEEDLE_BRANCH / NEEDLE_TOOL) so the search
        spec can assert against a known result.

  backend/tests/fixtures/claude/projects/-fixture-project/<SESSION_UUID>.jsonl
      • One Claude Code session, with safe content (no real cwd, no real
        username, no real git branch).

  backend/tests/fixtures/desktop/codex/fixture000000/
      • The Codex archive of the synthetic Codex home that
        ``backend/tests/codex_fake.py`` builds. The real copier
        (``CodexAdapter.archive_sync``) writes it; the script then renames
        the home folder to the fixed key ``fixture000000`` and replaces the
        temp paths and the sync times with fixed values.

  backend/tests/fixtures/README.md
      • Describes each fixture's intent + searchable tokens so future
        contributors don't have to reverse-engineer the fixtures.

Determinism: UUIDs are derived from a fixed namespace (ASCII-only seed),
timestamps are pinned to 2026-04-01T10:00:00Z + offsets. Re-running the
script must produce byte-identical files.

Run:
    uv run python scripts/generate_e2e_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "backend" / "tests" / "fixtures"
DESKTOP_ROOT = FIXTURES_ROOT / "desktop"
CC_ROOT = FIXTURES_ROOT / "claude"

# The Codex archive (plan §4.2) under the Desktop data_dir, at a fixed key.
CODEX_FIXTURE_KEY = "fixture000000"
CODEX_FIXTURE_HOME = "/home/fixture/.codex"
CODEX_ROOT = DESKTOP_ROOT / "codex" / CODEX_FIXTURE_KEY

# Fixed test org. The UUID is derived from a deterministic namespace so
# the value is stable across re-runs and matches the by-org/<UUID>/ path.
TEST_ORG_UUID = "00000000-1111-2222-3333-444444444444"
TEST_ORG_NAME = "Fixture Workspace"

BASE_TS = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)


def _det_uuid(seed: str) -> str:
    """Derive a stable UUID-shaped string from a seed string."""
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _ts(offset_minutes: int) -> str:
    return (BASE_TS + timedelta(minutes=offset_minutes)).isoformat().replace("+00:00", "Z")


def _msg(
    *,
    uuid: str,
    sender: str,
    text: str,
    parent: str | None,
    minute_offset: int,
    content_blocks: list[dict] | None = None,
    files: list[dict] | None = None,
) -> dict:
    """Shape a chat_messages entry that matches what the Desktop API ships."""
    blocks = content_blocks if content_blocks is not None else [{"type": "text", "text": text}]
    return {
        "uuid": uuid,
        "sender": sender,
        "text": text,
        "content": blocks,
        "created_at": _ts(minute_offset),
        "updated_at": _ts(minute_offset),
        "truncated": False,
        "parent_message_uuid": parent,
        "attachments": [],
        "files": files or [],
        "files_v2": [],
    }


def _conv(
    *,
    uuid: str,
    name: str,
    messages: list[dict],
    has_branches: bool = False,
    minute_offset: int = 0,
) -> dict:
    """Shape a conversation JSON file (matches store.py's expectations)."""
    return {
        "uuid": uuid,
        "name": name,
        "summary": name,
        "model": "claude-sonnet-4-6",
        "created_at": _ts(minute_offset),
        "updated_at": _ts(minute_offset + len(messages)),
        "is_starred": False,
        "current_leaf_message_uuid": messages[-1]["uuid"] if messages else "",
        "chat_messages": messages,
        "has_branches": has_branches,
        "organization_id": TEST_ORG_UUID,
        "organization_name": TEST_ORG_NAME,
    }


def build_long_conversation() -> dict:
    """A 30-message conversation with the NEEDLE_HANDSHAKE token.

    Used by jump-buttons.spec.ts (needs a long scroll surface) and by
    search.spec.ts (NEEDLE_HANDSHAKE is searchable).
    """
    uuid = _det_uuid("conv-long")
    msgs: list[dict] = []
    parent: str | None = None
    for i in range(30):
        sender = "human" if i % 2 == 0 else "assistant"
        if i == 4:
            # Inject the unique searchable token in a human message at a
            # known index. The search test (search.spec.ts) types this
            # token verbatim and asserts ≥1 result card.
            text = (
                "Quick question about TLS handshakes. NEEDLE_HANDSHAKE - "
                "I want this token to be uniquely findable so the search "
                "test can assert against it without false positives."
            )
        elif i == 0:
            text = "Hi! Let's talk about TLS."
        else:
            text = f"Filler message #{i + 1} for the long-conversation fixture."
        m_uuid = _det_uuid(f"conv-long:msg-{i}")
        msgs.append(
            _msg(uuid=m_uuid, sender=sender, text=text, parent=parent, minute_offset=i)
        )
        parent = m_uuid
    return _conv(
        uuid=uuid,
        name="Phase 5 fixture: TLS handshakes (long)",
        messages=msgs,
    )


def build_branched_conversation() -> dict:
    """A 4-message conversation with has_branches=true and NEEDLE_BRANCH."""
    uuid = _det_uuid("conv-branched")
    m1 = _msg(
        uuid=_det_uuid("conv-branched:m1"),
        sender="human",
        text="Tell me about branching. NEEDLE_BRANCH",
        parent=None,
        minute_offset=0,
    )
    m2 = _msg(
        uuid=_det_uuid("conv-branched:m2"),
        sender="assistant",
        text="Branches happen when you regenerate from an earlier message.",
        parent=m1["uuid"],
        minute_offset=1,
    )
    m3 = _msg(
        uuid=_det_uuid("conv-branched:m3"),
        sender="human",
        text="What does the tree visualization look like?",
        parent=m2["uuid"],
        minute_offset=2,
    )
    m4 = _msg(
        uuid=_det_uuid("conv-branched:m4"),
        sender="assistant",
        text="A read-only tree where you can click any leaf to switch branches.",
        parent=m3["uuid"],
        minute_offset=3,
    )
    return _conv(
        uuid=uuid,
        name="Phase 5 fixture: Branch tree",
        messages=[m1, m2, m3, m4],
        has_branches=True,
    )


def build_tool_conversation() -> dict:
    """A 4-message conversation with tool_use + tool_result + NEEDLE_TOOL."""
    uuid = _det_uuid("conv-tools")
    m1 = _msg(
        uuid=_det_uuid("conv-tools:m1"),
        sender="human",
        text="Run a grep for me. NEEDLE_TOOL",
        parent=None,
        minute_offset=0,
    )
    m2 = _msg(
        uuid=_det_uuid("conv-tools:m2"),
        sender="assistant",
        text="Running grep.",
        parent=m1["uuid"],
        minute_offset=1,
        content_blocks=[
            {"type": "text", "text": "Running grep."},
            {
                "type": "tool_use",
                "name": "bash",
                "input": {"command": "grep -r FIXTURE_TOOL src"},
            },
            {
                "type": "tool_result",
                "content": [{"type": "text", "text": "FIXTURE_TOOL: 3 hits"}],
            },
        ],
    )
    m3 = _msg(
        uuid=_det_uuid("conv-tools:m3"),
        sender="human",
        text="Thanks!",
        parent=m2["uuid"],
        minute_offset=2,
    )
    return _conv(
        uuid=uuid,
        name="Phase 5 fixture: Tool calls",
        messages=[m1, m2, m3],
    )


def build_cc_session() -> tuple[str, list[dict]]:
    """A 4-message Claude Code session with NEEDLE_CC.

    Returns (session_uuid, list_of_jsonl_entries).
    cwd / gitBranch are deliberately fake; the project folder name is
    ``-fixture-project`` (the synthetic encoded form of /fixture/project).
    """
    session_id = _det_uuid("cc-session-1")
    cwd = "/fixture/project"
    branch = "fixture-branch"
    version = "2.0.0"
    entries = []
    parent: str | None = None
    msgs = [
        ("user", "Hi! NEEDLE_CC — fixture session for Phase 5 tests."),
        ("assistant", "Hello! Ready to help with the fixture project."),
        ("user", "What's 2+2?"),
        ("assistant", "Four."),
    ]
    for i, (role, text) in enumerate(msgs):
        u = _det_uuid(f"cc-session-1:m-{i}")
        entries.append(
            {
                "parentUuid": parent,
                "isSidechain": False,
                "userType": "external",
                "entrypoint": "cli",
                "cwd": cwd,
                "sessionId": session_id,
                "version": version,
                "gitBranch": branch,
                "type": role,
                "uuid": u,
                "timestamp": _ts(i),
                "message": (
                    {"role": "user", "content": text}
                    if role == "user"
                    else {
                        "model": "claude-sonnet-4-6",
                        "id": _det_uuid(f"cc-msg-id-{i}"),
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 10,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    }
                ),
            }
        )
        parent = u
    return session_id, entries


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def write_jsonl_atomic(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n"
    path.write_text(text, encoding="utf-8")


# Issue #90: real Code Mode recordings (scrubbed; see the READMEs in
# fixtures/cc_code_mode and fixtures/codex_code_mode), so the Playwright suite
# runs the real backend on real shapes.
CC_CODE_MODE_PROJECT = "-home-fixture-cm-sample"
CC_CODE_MODE_SESSIONS = (
    ("mcp-to-cli-pipe.jsonl", "b007d327-80c4-46ea-b737-53d84b30c624"),
    ("failed-refused-subagent.jsonl", "2339aab4-744c-4556-95b8-5ee5552c1b85"),
    ("poll-tool-settle.jsonl", "6c2110ef-d09c-4f7b-9e2e-657d6e24353d"),
)
# The subagent of the second session, in the current layout.
CC_CODE_MODE_SUBAGENT = ("subagent-run.jsonl", "2339aab4-744c-4556-95b8-5ee5552c1b85", "a47f122549f03fce6")
CODEX_CODE_MODE_ROLLOUTS = (
    ("cli-to-cli-pipe.jsonl", "01a2118c-b04a-7160-825d-2010987d5c34"),
    ("failed-wait-notify-two-cells-terminate.jsonl", "01a2118a-13d2-77e2-b458-a98bffa8713c"),
)


def copy_cc_code_mode_sessions() -> int:
    """Copy the Claude Code Code Mode recordings into the fixture tree."""
    project = CC_ROOT / "projects" / CC_CODE_MODE_PROJECT
    for name, session in CC_CODE_MODE_SESSIONS:
        write_bytes_atomic(project / f"{session}.jsonl", (FIXTURES_ROOT / "cc_code_mode" / name).read_bytes())
    name, session, agent = CC_CODE_MODE_SUBAGENT
    write_bytes_atomic(project / session / "subagents" / f"agent-{agent}.jsonl",
                       (FIXTURES_ROOT / "cc_code_mode" / name).read_bytes())
    return len(CC_CODE_MODE_SESSIONS)


# Issue #96: the meta file of the recorded subagent (Claude Code writes one
# next to each launched agent; the recording kept only the transcript).
CC_CODE_MODE_SUBAGENT_META = {"agentType": "general-purpose", "description": "List src directory"}

# Issue #96: a synthetic session with a current-layout agent and a legacy
# agent, in the shape of the recordings. One word per place (README).
SA_PROJECT = "-home-fixture-proj-sa"
SA_CWD = "/home/fixture/proj-sa"
SA_SESSION = "5a6e0000-0000-4000-8000-000000000096"
SA_AGENT = "a5a0000000000096c"
SA_LEGACY_AGENT = "5a1e9ac"
SA_WORDS = {
    "session": "zqsessiontext",
    "shared": "zqshared",
    "text": "zqagenttext",
    "tool": "zqagenttool",
    "code": "zqagentcode",
    "compact": "zqagentcompact",
    "legacy": "zqlegacyagent",
}


def build_subagent_session() -> dict[Path, list[dict]]:
    """The session, its agent (with a meta file), and its legacy agent.

    Returns {relative path under the project: jsonl entries}.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from backend.tests import cc_code_mode_fake as fake

    w = SA_WORDS
    code = f"// {w['code']}\nawait $`ls src`.text()"
    call = "toolu_01SATOOL0000000000000001"
    run = "toolu_01SARUN00000000000000002"
    tool_call = fake.text_reply("sa-a1", "sa-u1", "")
    tool_call["message"]["content"] = [{"type": "tool_use", "id": call, "name": "Bash", "input": {"command": "ls"}}]
    tool_result = fake.user("sa-j1", "sa-a1", "")
    tool_result["message"]["content"] = [
        {"type": "tool_result", "tool_use_id": call, "content": f"app.py\n{w['tool']}.py", "is_error": False}]
    tool_result["toolUseResult"] = {"stdout": f"app.py\n{w['tool']}.py", "stderr": ""}
    files = {
        Path(f"{SA_SESSION}.jsonl"): ([
            fake.user("0096-u1", None, f"Start the agents ({w['session']})."),
            fake.text_reply("0096-t1", "0096-u1", f"The agents ran ({w['shared']})."),
        ], None),
        Path(SA_SESSION) / "subagents" / f"agent-{SA_AGENT}.jsonl": ([
            fake.user("sa-u1", None, "Look at the files."),
            tool_call,
            tool_result,
            fake.outer("sa-a2", "sa-j1", run, code),
            fake.outer_result("sa-r2", "sa-a2", run, code, result="app.py", tools=["Bash"]),
            {**fake.user("sa-c1", "sa-r2", f"Summary of the work so far ({w['compact']})."), "isCompactSummary": True},
            fake.text_reply("sa-t1", "sa-c1", f"I looked at the files ({w['text']} {w['shared']})."),
        ], SA_AGENT),
        Path(f"agent-{SA_LEGACY_AGENT}.jsonl"): ([
            fake.user("sl-u1", None, "Do a small task."),
            fake.text_reply("sl-t1", "sl-u1", f"Done ({w['legacy']})."),
        ], SA_LEGACY_AGENT),
    }
    out: dict[Path, list[dict]] = {}
    n = 0
    for rel, (lines, agent) in files.items():
        for line in lines:
            n += 1
            line.update(sessionId=SA_SESSION, cwd=SA_CWD, timestamp=_ts(600 + n))
            if agent is not None:
                line.update(isSidechain=True, agentId=agent)
        out[rel] = lines
    return out


def write_subagent_fixtures() -> int:
    """Write the meta file of the recorded subagent and the synthetic session (#96)."""
    _name, session, agent = CC_CODE_MODE_SUBAGENT
    write_json_atomic(CC_ROOT / "projects" / CC_CODE_MODE_PROJECT / session / "subagents" / f"agent-{agent}.meta.json",
                      CC_CODE_MODE_SUBAGENT_META)
    project = CC_ROOT / "projects" / SA_PROJECT
    files = build_subagent_session()
    for rel, entries in files.items():
        write_jsonl_atomic(project / rel, entries)
    write_json_atomic(project / SA_SESSION / "subagents" / f"agent-{SA_AGENT}.meta.json",
                      {"agentType": "general-purpose", "description": "Synthetic search agent"})
    return len(files)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def build_codex_archive() -> int:
    """Copy the fake Codex home into ``CODEX_ROOT`` with the real copier.

    Returns the number of thread files.
    """
    sys.path.insert(0, str(REPO_ROOT))
    import orjson

    from backend.archive_sync import LOCK_FILE_NAME, SyncContext, home_key
    from backend.sources.registry import get_adapter
    from backend.store import ConversationStore
    from backend.tests.codex_fake import build_codex_home

    fixed_ms = int(BASE_TS.timestamp() * 1000)
    with tempfile.TemporaryDirectory() as tmp:
        home = build_codex_home(Path(tmp) / ".codex")
        # Issue #90: the scrubbed real code mode rollouts, as Codex stores them.
        for name, thread_id in CODEX_CODE_MODE_ROLLOUTS:
            dest = home / "sessions" / "2026" / "11" / "24" / f"rollout-2026-11-24T00-00-00-{thread_id}.jsonl"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES_ROOT / "codex_code_mode" / name, dest)
        data_dir = Path(tmp) / "data"
        ctx = SyncContext(settings=SimpleNamespace(codex_home=str(home)), reason="cli", lock_timeout_s=5)
        report = get_adapter("CODEX", ConversationStore(data_dir=data_dir)).archive_sync(ctx)
        if report is None or report.skipped_reason or not report.wrote_any:
            raise SystemExit(f"the Codex copy wrote nothing: {report}")
        archive = data_dir / "codex" / home_key(home)
        (archive / LOCK_FILE_NAME).unlink(missing_ok=True)

        # Fixed values for everything that names the temp home or the clock.
        home_json = archive / "home.json"
        data = orjson.loads(home_json.read_bytes())
        data.update(home_path=CODEX_FIXTURE_HOME, first_seen_ms=fixed_ms)
        home_json.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
        for meta_path in sorted((archive / "rollouts").glob("*.meta.json")):
            meta = json.loads(meta_path.read_bytes())
            rel = Path(meta["source_path"]).relative_to(home).as_posix()
            meta.update(
                source_path=f"{CODEX_FIXTURE_HOME}/{rel}",
                # The size of a .zst file depends on the zstd library version.
                source_size=meta["copied_bytes"],
                source_mtime_ns=fixed_ms * 1_000_000,
                last_sync_ms=fixed_ms,
            )
            meta_path.write_bytes(json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8"))
        threads = sorted((archive / "threads").glob("*.json"))
        for thread_path in threads:
            thread = orjson.loads(thread_path.read_bytes())
            thread["home_key"] = CODEX_FIXTURE_KEY
            thread_path.write_bytes(orjson.dumps(thread, option=orjson.OPT_INDENT_2) + b"\n")

        if CODEX_ROOT.exists():
            shutil.rmtree(CODEX_ROOT)
        CODEX_ROOT.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(archive), CODEX_ROOT)
    return len(threads)


def write_readme() -> None:
    readme_path = FIXTURES_ROOT / "README.md"
    body = """# E2E test fixtures

These files are auto-generated by `scripts/generate_e2e_fixtures.py`.
**Do not edit by hand** — re-run the script to refresh.

The Playwright e2e suite runs in "fixture mode" by default: the backend
points at this directory via the `CLAUDE_EXPLORER_DATA_DIR` (Desktop)
and `CLAUDE_DIR` (Claude Code) environment variables, set by
`frontend/playwright.config.ts`. This means external contributors can
clone the repo and run `npm run test:e2e` without needing Raymond's
actual conversation history on disk.

The Codex archive (`desktop/codex/`) comes from the real copier, run on
the synthetic Codex home of `backend/tests/codex_fake.py`. The script then
renames the home folder to the fixed key `fixture000000`, writes
`home_path` as `/home/fixture/.codex`, and replaces the temp paths and the
sync times with fixed values. In e2e, `CODEX_HOME` points to a missing
folder, so the copy never writes into these fixtures.

Code Mode (issue #90): the script also copies scrubbed recordings of real
sessions (synthetic prompts on synthetic files) into the tree, so the
Playwright suite runs the real backend on real shapes. Three Claude Code
sessions (and one subagent) go under `claude/projects/-home-fixture-cm-sample/`
from `cc_code_mode/`; two Codex rollouts from `codex_code_mode/` go into the
fake Codex home before the copy. `backend/tests/test_fixture_privacy.py`
checks every fixture for real data.

Subagent transcripts (issue #96): the recorded subagent gets a synthetic
`agent-a47f122549f03fce6.meta.json` ("List src directory"). A synthetic
session `5a6e0000-…0096` under `claude/projects/-home-fixture-proj-sa/` has a
current-layout agent `a5a0000000000096c` ("Synthetic search agent") and a
legacy agent `agent-5a1e9ac.jsonl` (no meta file). Each word is in one place:
`zqsessiontext` (session prompt), `zqshared` (session reply and agent reply),
`zqagenttext` (agent reply), `zqagenttool` (agent tool output), `zqagentcode`
(agent Code Mode script), `zqagentcompact` (agent compaction summary),
`zqlegacyagent` (legacy agent reply). Used by `subagent-transcripts.spec.ts`.

## Layout

```
backend/tests/fixtures/
├── desktop/
│   └── by-org/
│       └── 00000000-1111-2222-3333-444444444444/   # synthetic test org
│           ├── <uuid-of-long-conversation>.json
│           ├── <uuid-of-branched-conversation>.json
│           └── <uuid-of-tool-conversation>.json
│   └── codex/
│       └── fixture000000/                          # fixed home key
│           ├── home.json                           # home_path /home/fixture/.codex
│           ├── rollouts/<stem>.jsonl               # + <stem>.meta.json
│           └── threads/<thread_id>.json
│   └── cowork-remote/<org>/remote_<id>/audit.jsonl # remote Cowork task (hand-written, not generated)
├── cowork/local-agent-mode-sessions/<dep>/<org>/local_<uuid>/audit.jsonl  # local Cowork (hand-written)
└── claude/
    └── projects/
        └── -fixture-project/                       # synthetic encoded cwd
            └── <uuid-of-session>.jsonl
```

## Conversations

| Title | Source | Searchable token | Used by |
|---|---|---|---|
| `Phase 5 fixture: TLS handshakes (long)` | Desktop | `NEEDLE_HANDSHAKE` | jump-buttons.spec.ts (long scroll), search.spec.ts |
| `Phase 5 fixture: Branch tree` | Desktop (has_branches) | `NEEDLE_BRANCH` | conversations.spec.ts |
| `Phase 5 fixture: Tool calls` | Desktop (tool_use + tool_result) | `NEEDLE_TOOL` | search.spec.ts (tool-block search) |
| `Hi! NEEDLE_CC — fixture session ...` | Claude Code | `NEEDLE_CC` | conversations.spec.ts (CC source) |
| `E2E fixture: local Cowork session` | Cowork (local) | `NEEDLE_COWORKLOCAL` | search-all-sources.spec.ts |
| `E2E fixture: remote Cowork task` | Cowork (remote) | `NEEDLE_COWORKREMOTE` | search-all-sources.spec.ts |

## Codex threads

The thread ids and contents are the `THREAD_*` constants of
`backend/tests/codex_fake.py`.

| Thread | Title | Project | Kind |
|---|---|---|---|
| `019a7c10-…a001` | `Alpha parser refactor` (Codex name) | `/home/fixture/proj-alpha` | paginated, plain; images, a plan, an unknown item, damaged lines |
| `019a0f20-…b002` | `Why does the build fail? Here is the error.` | `/home/fixture/proj-beta` | legacy, `.zst`; parallel tool calls, encrypted reasoning |
| `0199e030-…c003` | `Gamma release notes` (Codex name) | `/home/fixture/proj-gamma` | archived |
| `019a7c40-…d004` | `Scout: callers of parse` (Codex name) | `/home/fixture/proj-alpha` | subagent of `…a001`, `.zst` |
| `019a7c50-…e005` | `Review the proposed command: rm -rf build/` | `/home/fixture/proj-alpha` | guardian review |
| `019a8060-…f006` | `Continue the migration from the Claude Code session.` | `/home/fixture/proj-delta` | imported from Claude Code |

## Refreshing

After changing the generator:

```bash
uv run python scripts/generate_e2e_fixtures.py
```

The output is deterministic — same input produces byte-identical files.
"""
    readme_path.write_text(body, encoding="utf-8")


def main() -> int:
    # Desktop conversations
    org_dir = DESKTOP_ROOT / "by-org" / TEST_ORG_UUID
    long_conv = build_long_conversation()
    branched_conv = build_branched_conversation()
    tool_conv = build_tool_conversation()
    write_json_atomic(org_dir / f"{long_conv['uuid']}.json", long_conv)
    write_json_atomic(org_dir / f"{branched_conv['uuid']}.json", branched_conv)
    write_json_atomic(org_dir / f"{tool_conv['uuid']}.json", tool_conv)

    # Claude Code session
    session_uuid, entries = build_cc_session()
    cc_path = CC_ROOT / "projects" / "-fixture-project" / f"{session_uuid}.jsonl"
    write_jsonl_atomic(cc_path, entries)

    # Claude Code Code Mode recordings (issue #90)
    cm_sessions = copy_cc_code_mode_sessions()

    # Subagent transcripts (issue #96)
    sa_files = write_subagent_fixtures()

    # Codex archive
    codex_threads = build_codex_archive()

    # README
    write_readme()

    print(f"Wrote 3 Desktop conversations under {org_dir}")
    print(f"Wrote 1 CC session at {cc_path}")
    print(f"Copied {cm_sessions} CC Code Mode recordings under {CC_ROOT / 'projects' / CC_CODE_MODE_PROJECT}")
    print(f"Wrote {sa_files} subagent fixture files under {CC_ROOT / 'projects' / SA_PROJECT}")
    print(f"Wrote {codex_threads} Codex threads under {CODEX_ROOT}")
    print(f"Wrote {FIXTURES_ROOT / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
