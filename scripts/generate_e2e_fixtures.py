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
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "backend" / "tests" / "fixtures"
DESKTOP_ROOT = FIXTURES_ROOT / "desktop"
CC_ROOT = FIXTURES_ROOT / "claude"

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

## Layout

```
backend/tests/fixtures/
├── desktop/
│   └── by-org/
│       └── 00000000-1111-2222-3333-444444444444/   # synthetic test org
│           ├── <uuid-of-long-conversation>.json
│           ├── <uuid-of-branched-conversation>.json
│           └── <uuid-of-tool-conversation>.json
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

    # README
    write_readme()

    print(f"Wrote 3 Desktop conversations under {org_dir}")
    print(f"Wrote 1 CC session at {cc_path}")
    print(f"Wrote {FIXTURES_ROOT / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
