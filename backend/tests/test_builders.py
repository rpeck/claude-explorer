"""Contract tests for the shared test-fixture builders.

These tests pin the contract for ``backend.tests.builders`` — the
deduplicated home for the ``_conv()`` / ``_write_conv()`` / ``_user()``
/ ``_assistant()`` / ``_write_jsonl()`` helpers that 14+ search-test
files used to define inline.

The contract is "the builder output round-trips through production
parsers." We deliberately validate by **writing to disk and re-parsing
via the production code path** (``ConversationStore`` for Desktop,
``read_conversation_summary_fast`` for Claude Code JSONL). That turns
this file into a guardrail: if anyone ever skews the builder shape
away from what production accepts, the next ``pytest`` run catches it.

Council decision (LLM Council coding workflow, 2026-05-18):
  * Lives at ``backend/tests/builders.py`` (peer to ``conftest.py``),
    NOT ``backend/tests/fixtures/builders.py``. The ``fixtures/`` dir
    is reserved for STATIC JSON/JSONL e2e payloads driven by
    ``scripts/generate_e2e_fixtures.py`` (see ``fixtures/README.md``:
    "Do not edit by hand"). Putting a Python module there dilutes
    that convention.
  * Builders return raw ``dict``, not Pydantic objects, so callers
    can mutate before validation and so we exercise the on-disk
    parsing path end-to-end.
  * ``NEEDLE_*`` tokens are exported as module constants AND used
    as default-arg values, so callers either pass them explicitly
    or get a stable grep-able default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.claude_code_reader import read_conversation_summary_fast
from backend.models import ConversationSummary
from backend.store import ConversationStore
from backend.tests import builders as B

# ---------------------------------------------------------------------------
# Desktop conversation builder
# ---------------------------------------------------------------------------


def test_build_desktop_conv_returns_dict_with_required_keys() -> None:
    """Direct shape check — fast, no I/O.

    Pins the keys downstream parsers (``store._make_summary``) expect:
    ``uuid``, ``name``, ``created_at``, ``updated_at``, ``chat_messages``,
    ``source``. If a future refactor renames any of these on the
    Desktop wire format, the production reader breaks first; this test
    catches the drift in the builder.
    """
    conv = B.build_desktop_conv(uuid="conv-1", name="Hello", body="some body text")

    assert isinstance(conv, dict)
    for key in (
        "uuid",
        "name",
        "summary",
        "model",
        "created_at",
        "updated_at",
        "is_starred",
        "current_leaf_message_uuid",
        "project_path",
        "source",
        "chat_messages",
    ):
        assert key in conv, f"missing required key {key!r}"

    assert conv["uuid"] == "conv-1"
    assert conv["name"] == "Hello"
    assert conv["source"] == "CLAUDE_AI"
    assert isinstance(conv["chat_messages"], list)
    assert len(conv["chat_messages"]) == 1
    assert conv["chat_messages"][0]["text"] == "some body text"


def test_build_desktop_conv_isolation_no_shared_mutable_state() -> None:
    """Two calls must return INDEPENDENT dicts.

    Catches the classic mutable-default-argument bug (``def f(x=[])``)
    AND any "return cached blueprint, callers tweak it" pattern that
    would leak state across tests under ``pytest-xdist``.
    """
    a = B.build_desktop_conv(uuid="a", name="A", body="body-a")
    b = B.build_desktop_conv(uuid="b", name="B", body="body-b")

    # Mutate a; b must stay clean.
    a["chat_messages"].append({"sentinel": True})
    a["name"] = "MUTATED"

    assert "sentinel" not in (b["chat_messages"][0] if b["chat_messages"] else {})
    assert b["name"] == "B"
    # And the nested message dict itself must not be aliased.
    assert a["chat_messages"][0] is not b["chat_messages"][0]


def test_build_desktop_conv_accepts_explicit_messages_list() -> None:
    """The multi-message variant some tests need (e.g.
    ``test_search_response_envelope.py`` uses a list-of-messages
    signature). Builder must accept either ``body=...`` (single-message
    sugar) or ``messages=[...]`` (full control)."""
    msgs = [
        B.build_message(uuid="m-1", text="first"),
        B.build_message(uuid="m-2", text="second", sender="assistant"),
    ]
    conv = B.build_desktop_conv(uuid="c", name="multi", messages=msgs)

    assert len(conv["chat_messages"]) == 2
    assert conv["chat_messages"][0]["text"] == "first"
    assert conv["chat_messages"][1]["sender"] == "assistant"
    # current_leaf_message_uuid tracks the last message.
    assert conv["current_leaf_message_uuid"] == "m-2"


def test_build_desktop_conv_body_xor_messages_is_enforced() -> None:
    """Passing both ``body=`` and ``messages=`` is a programmer error —
    we'd silently drop one. Fail loudly instead."""
    with pytest.raises(ValueError, match="body.*messages"):
        B.build_desktop_conv(
            uuid="c",
            name="x",
            body="ignored",
            messages=[B.build_message(uuid="m-1", text="kept")],
        )


def test_build_desktop_conv_passes_through_conversation_store(tmp_path: Path) -> None:
    """Contract test: builder output, after ``write_desktop_conv`` to
    disk, must be readable by the production ``ConversationStore``
    and produce a valid ``ConversationSummary``.

    This is the load-bearing test. If the wire-format expectations
    drift, this catches it.
    """
    data_dir = tmp_path / "data"
    by_org = data_dir / "by-org" / "org-1"

    conv = B.build_desktop_conv(
        uuid="conv-roundtrip",
        name="Round-trip check",
        body=f"hello {B.NEEDLE_HANDSHAKE} world",
    )
    written = B.write_desktop_conv(by_org, conv)
    assert written.exists()

    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=data_dir, claude_dir=cc_dir)

    summaries = store.list_conversations()
    assert len(summaries) == 1
    s = summaries[0]
    assert isinstance(s, ConversationSummary)
    assert s.uuid == "conv-roundtrip"
    assert s.name == "Round-trip check"
    assert s.source == "CLAUDE_AI"


# ---------------------------------------------------------------------------
# Claude Code JSONL builders
# ---------------------------------------------------------------------------


def test_build_cc_user_entry_shape() -> None:
    entry = B.build_cc_user_entry(
        uuid="u1", text="hello world", session_id="sess", cwd="/tmp/p"
    )
    assert entry["type"] == "user"
    assert entry["uuid"] == "u1"
    assert entry["sessionId"] == "sess"
    assert entry["cwd"] == "/tmp/p"
    assert entry["message"]["role"] == "user"
    assert entry["message"]["content"] == "hello world"


def test_build_cc_assistant_entry_shape() -> None:
    entry = B.build_cc_assistant_entry(uuid="a1", msg_id="msg_a1", text="hi there")
    assert entry["type"] == "assistant"
    assert entry["uuid"] == "a1"
    assert entry["message"]["id"] == "msg_a1"
    assert entry["message"]["role"] == "assistant"
    assert entry["message"]["content"] == [{"type": "text", "text": "hi there"}]


def test_write_cc_jsonl_round_trips_via_fast_reader(tmp_path: Path) -> None:
    """Contract test: a CC session built via the builders, written to
    disk via ``write_cc_jsonl``, must parse via the production
    ``read_conversation_summary_fast`` reader and return a non-None
    summary dict with the expected fields populated."""
    session_id = "sess-roundtrip-0001"
    jsonl_path = tmp_path / "projects" / "-tmp-p" / f"{session_id}.jsonl"

    entries = [
        B.build_cc_user_entry(
            uuid="u1",
            text=f"first user prompt {B.NEEDLE_CC}",
            session_id=session_id,
        ),
        B.build_cc_assistant_entry(
            uuid="a1", msg_id="msg_a1", text="assistant reply"
        ),
    ]
    written = B.write_cc_jsonl(jsonl_path, entries)
    assert written.exists()

    summary = read_conversation_summary_fast(jsonl_path)
    assert summary is not None
    assert summary["uuid"] == session_id  # session_id is the uuid
    # message counts populated
    assert summary["message_count"] >= 1
    assert summary["human_message_count"] >= 1


def test_build_cc_entries_are_independent() -> None:
    """Same mutable-default-arg sanity check as Desktop builder."""
    a = B.build_cc_user_entry(uuid="u1", text="x")
    b = B.build_cc_user_entry(uuid="u2", text="y")
    a["message"]["content"] = "MUTATED"
    assert b["message"]["content"] == "y"
    assert a["message"] is not b["message"]


# ---------------------------------------------------------------------------
# NEEDLE constants
# ---------------------------------------------------------------------------


def test_needle_constants_are_nonempty_unique_strings() -> None:
    """The NEEDLE tokens are the load-bearing search-query strings.
    Two collisions (e.g. NEEDLE_BRANCH == NEEDLE_TOOL) would silently
    cross-pollinate test assertions across files. Pin uniqueness."""
    needles = [
        B.NEEDLE_HANDSHAKE,
        B.NEEDLE_BRANCH,
        B.NEEDLE_TOOL,
        B.NEEDLE_CC,
        B.NEEDLE_BENCHMARK,
    ]
    for n in needles:
        assert isinstance(n, str)
        assert n, "NEEDLE constant must be non-empty"
        # Must be searchable (no whitespace-only, no leading/trailing whitespace).
        assert n == n.strip()
        assert " " not in n

    # All distinct.
    assert len(set(needles)) == len(needles), (
        "NEEDLE_* tokens must be globally unique so cross-file search "
        "assertions don't accidentally match each other's fixtures"
    )


def test_needle_constants_match_their_name() -> None:
    """The whole point of these constants is grep-ability: if I grep
    for ``NEEDLE_HANDSHAKE`` in the repo, I want hits in BOTH the test
    that asserts on it AND the fixture file that contains it. Pin the
    string value == the constant name."""
    assert B.NEEDLE_HANDSHAKE == "NEEDLE_HANDSHAKE"
    assert B.NEEDLE_BRANCH == "NEEDLE_BRANCH"
    assert B.NEEDLE_TOOL == "NEEDLE_TOOL"
    assert B.NEEDLE_CC == "NEEDLE_CC"
    # Benchmark uses a longer suffix historically; keep the existing name.
    assert "NEEDLE" in B.NEEDLE_BENCHMARK


# ---------------------------------------------------------------------------
# write_desktop_conv / write_cc_jsonl helpers
# ---------------------------------------------------------------------------


def test_write_desktop_conv_creates_parent_dirs(tmp_path: Path) -> None:
    """Builders should be drop-in usable from any test — they must
    ``mkdir(parents=True, exist_ok=True)`` so callers don't have to."""
    deep = tmp_path / "data" / "by-org" / "deep" / "nest"
    conv = B.build_desktop_conv(uuid="c", name="x", body="body")
    written = B.write_desktop_conv(deep, conv)
    assert written.parent == deep
    assert written.name == "c.json"
    assert json.loads(written.read_text())["uuid"] == "c"


def test_write_cc_jsonl_creates_parent_dirs(tmp_path: Path) -> None:
    deep = tmp_path / "claude" / "projects" / "-deep-nest" / "sess.jsonl"
    entries = [B.build_cc_user_entry(uuid="u1", text="hi", session_id="sess")]
    written = B.write_cc_jsonl(deep, entries)
    assert written.exists()
    # One line per entry, valid JSON.
    lines = written.read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["uuid"] == "u1"
