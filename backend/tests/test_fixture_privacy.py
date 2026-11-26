"""No real data in a committed fixture (issue #90).

The fixture trees hold synthetic sessions and scrubbed recordings of real
sessions (``scripts/scrub_fixture.py``). This guard scans every text file in
them on every test run, so a later fixture cannot bring in a home path, an
email address, a key, an account id, a time zone, or model context.

The scanner is tested on planted bad strings first, so a scanner that finds
nothing cannot pass by accident.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TREES = (
    REPO / "backend" / "tests" / "fixtures",
    REPO / "backend" / "tests" / "goldens",  # committed output of the fixtures
    REPO / "frontend" / "src" / "test" / "fixtures",
)
# Recordings of real sessions: model context must be gone too.
RECORDINGS = ("cc_code_mode", "codex_code_mode")
BINARY = {".pyc", ".sqlite", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zst", ".zip"}

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a macOS home path", re.compile(r"/Users/")),
    ("a Windows home path", re.compile(r"[A-Za-z]:\\\\?Users\\\\?")),
    ("a home folder other than /home/fixture", re.compile(r"/home/(?!fixture\b)[A-Za-z0-9_.-]+")),
    ("a macOS private or temp path", re.compile(r"/private/(?:tmp|var)|/var/folders/")),
    ("an email address", re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.(?:com|org)\b)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("an Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}")),
    ("an account id field", re.compile(r'"(?:accountUuid|organizationUuid|creator_account_id|creator_user_id)"')),
    ("a time zone other than UTC", re.compile(r"<timezone>(?!UTC<)[^<]*</timezone>")),
)
RECORDING_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("model context", re.compile(r'"(?:systemPrompt|base_instructions|additional_tools|prompt_snapshot|skill_listing|developer_instructions)"')),
)


def leaks(text: str, *, recording: bool) -> list[str]:
    rules = RULES + (RECORDING_RULES if recording else ())
    return [name for name, pattern in rules if pattern.search(text)]


def _files() -> list[Path]:
    return [
        p for tree in TREES for p in sorted(tree.rglob("*"))
        if p.is_file() and p.suffix not in BINARY and ".sqlite" not in p.name and "__pycache__" not in p.parts
    ]


@pytest.mark.parametrize(("text", "kind"), [
    ('{"cwd": "/Users/alice/proj"}', "a macOS home path"),
    ('{"cwd": "C:\\\\Users\\\\alice"}', "a Windows home path"),
    ('{"cwd": "/home/alice/x"}', "a home folder other than /home/fixture"),
    ('{"f": "/private/tmp/claude-501/x"}', "a macOS private or temp path"),
    ('{"f": "/var/folders/7g/x"}', "a macOS private or temp path"),
    ("mail carol@corp.io", "an email address"),
    ("key sk-ant-sid01-abcdefgh", "an Anthropic key"),
    ('{"accountUuid": "x"}', "an account id field"),
    ("<timezone>America/Los_Angeles</timezone>", "a time zone other than UTC"),
])
def test_the_scanner_flags_each_kind_of_leak(text: str, kind: str) -> None:
    assert kind in leaks(text, recording=False)


def test_the_scanner_flags_model_context_in_a_recording_only() -> None:
    text = '{"attachment": {"type": "x", "systemPrompt": ["..."]}}'
    assert leaks(text, recording=True) == ["model context"]
    assert leaks(text, recording=False) == []


def test_the_scanner_lets_the_synthetic_values_pass() -> None:
    ok = '{"cwd": "/home/fixture/cm-sample", "user": "ada@example.com", "tz": "<timezone>UTC</timezone>"}'
    assert leaks(ok, recording=True) == []


def test_the_scan_covers_the_recordings() -> None:
    names = {p.name for p in _files()}
    assert {"mcp-to-cli-pipe.jsonl", "cli-to-cli-pipe.jsonl", "codeMode.ts"} <= names


def test_no_committed_fixture_holds_real_data() -> None:
    found = {}
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        recording = any(part in RECORDINGS for part in path.parts)
        if hits := leaks(text, recording=recording):
            found[str(path.relative_to(REPO))] = hits
    assert found == {}
