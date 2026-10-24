"""Bug B (V1 polish 2026-05-14, second fix attempt) — search sort order
must match the date column the UI displays.

The user-reported bug:
  > "messages from Nov 18 appear at BOTH the top AND the bottom of the
  > results list with other dates interleaved between them"

Live API confirmation (curl on running 8765 server):
  Position 4 had ``conversation_updated_at=2026-05-14`` but sat BELOW
  position 3 with ``conversation_updated_at=2026-05-01``. Position 11
  had ``conversation_updated_at=2026-05-13`` but sat below conversations
  dated March/April 2026.

Root cause: ``backend/search.py:_sort_results`` (lines ~854-863) computes
the conversation-level sort key as ``max([m.created_at for m in
r.matching_messages if m.created_at])`` when ``sort="updated_at"``. The
UI renders ``conversation_updated_at`` in the date column for each
result card. So a conversation last-updated yesterday whose matched
messages are all from a month ago gets pushed below an older
conversation — the user sees "yesterday" listed BELOW "last month",
which they correctly call broken.

This spec pins the corrected contract: for ``sort="updated_at"`` (the
"Last Activity" UI label), order results by ``r.conversation_updated_at``
exactly. Per-message ordering WITHIN each conversation can stay as-is
(those cards are visually adjacent under the same conversation header
title, so message-time order makes sense there).

Mirrors the fixture style of ``test_search_multi_word_and.py`` for
consistency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import search_index as si
from backend.cache import clear_cache
from backend.search import search_conversations
from backend.store import ConversationStore


def _conv(
    uuid: str,
    name: str,
    *,
    body: str,
    conv_updated_at: str,
    conv_created_at: str,
    msg_created_at: str,
    source: str = "CLAUDE_AI",
    msg_uuid_suffix: str = "m1",
) -> dict:
    """Fixture conversation with INDEPENDENT control over conv- and
    message-level timestamps so we can deliberately invert their
    relationship (the bug's failure mode)."""
    return {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": conv_created_at,
        "updated_at": conv_updated_at,
        "is_starred": False,
        "current_leaf_message_uuid": f"{uuid}-{msg_uuid_suffix}",
        "project_path": None,
        "source": source,
        "chat_messages": [
            {
                "uuid": f"{uuid}-{msg_uuid_suffix}",
                "sender": "human",
                "text": body,
                "content": [{"type": "text", "text": body}],
                "created_at": msg_created_at,
                "updated_at": msg_created_at,
                "parent_message_uuid": None,
            },
        ],
    }


def _write_conv(by_org: Path, conv: dict) -> Path:
    by_org.mkdir(parents=True, exist_ok=True)
    path = by_org / f"{conv['uuid']}.json"
    path.write_text(json.dumps(conv))
    return path


@pytest.fixture
def store_with_inverted_timestamps(tmp_path, monkeypatch):
    """Three conversations whose conv-level and message-level
    timestamps DELIBERATELY disagree:

    +----------+--------------------+---------------------+
    | uuid     | conv_updated_at    | msg_created_at      |
    +----------+--------------------+---------------------+
    | newest   | 2026-05-14 (top)   | 2026-03-01 (oldest) |
    | middle   | 2026-05-07         | 2026-04-15          |
    | oldest   | 2026-05-01 (bot)   | 2026-05-13 (newest) |
    +----------+--------------------+---------------------+

    All three contain the literal token ``needle`` so a query of
    ``needle`` returns all three.

    Under the BUGGY sort (key = max(msg.created_at)), the order desc
    would be: oldest (msg=05-13) → middle (msg=04-15) → newest (msg=03-01).

    Under the CORRECT sort (key = conv_updated_at), the order desc
    matches the date column shown in the UI:
        newest (conv=05-14) → middle (conv=05-07) → oldest (conv=05-01).
    """
    by_org = tmp_path / "by-org" / "org-1"
    convs = [
        _conv(
            "newest",
            "Newest conversation",
            body="this contains a needle in old text",
            conv_updated_at="2026-05-14T22:00:00Z",
            conv_created_at="2026-03-01T10:00:00Z",
            msg_created_at="2026-03-01T10:00:00Z",  # OLDEST message
        ),
        _conv(
            "middle",
            "Middle conversation",
            body="another needle here in the middle",
            conv_updated_at="2026-05-07T15:00:00Z",
            conv_created_at="2026-04-15T08:00:00Z",
            msg_created_at="2026-04-15T08:00:00Z",
        ),
        _conv(
            "oldest",
            "Oldest conversation",
            body="needle hides in this very recent message body",
            conv_updated_at="2026-05-01T09:00:00Z",
            conv_created_at="2026-05-13T17:00:00Z",
            msg_created_at="2026-05-13T17:00:00Z",  # NEWEST message
        ),
    ]
    for c in convs:
        _write_conv(by_org, c)
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    clear_cache()
    si.reset_search_index_for_tests()
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(store, index=idx)
    monkeypatch.setattr(si, "_search_index", idx)

    yield store, idx

    idx.close()
    si.reset_search_index_for_tests()
    clear_cache()


def test_sort_updated_at_desc_uses_conversation_updated_at_not_max_msg(
    store_with_inverted_timestamps,
):
    """sort='updated_at', order='desc' → conversation_updated_at desc.

    With the bug (max-of-message-time key), order would be:
        oldest → middle → newest
    After the fix (conversation_updated_at key), order is:
        newest → middle → oldest
    """
    store, _idx = store_with_inverted_timestamps
    results = search_conversations(
        store=store,
        query="needle",
        source="all",
        context_size="snippet",
        sort="updated_at",
        sort_order="desc",
    ).results
    uuids = [r.conversation_uuid for r in results]
    assert uuids == ["newest", "middle", "oldest"], (
        f"sort=updated_at desc must order by conversation_updated_at, "
        f"got {uuids}"
    )


def test_sort_updated_at_asc_inverts(store_with_inverted_timestamps):
    """sort='updated_at', order='asc' → conversation_updated_at asc.

    Bidirectional guard: with the bug, asc would also be wrong (the
    min-of-message-time key would yield newest → middle → oldest, the
    same INVERSION of the correct asc order).
    """
    store, _idx = store_with_inverted_timestamps
    results = search_conversations(
        store=store,
        query="needle",
        source="all",
        context_size="snippet",
        sort="updated_at",
        sort_order="asc",
    ).results
    uuids = [r.conversation_uuid for r in results]
    assert uuids == ["oldest", "middle", "newest"], (
        f"sort=updated_at asc must order by conversation_updated_at asc, "
        f"got {uuids}"
    )


@pytest.fixture
def store_with_inverted_created_timestamps(tmp_path, monkeypatch):
    """Mirror of ``store_with_inverted_timestamps`` but with conv- and
    msg-level timestamps inverted SPECIFICALLY for the created_at sort.

    The buggy `_conv_time_key` for sort='created_at' returns
    ``min(matched_msg.created_at)``. To distinguish that from
    ``conv_created_at``, we deliberately make them diverge:

    +----------+--------------------+---------------------+
    | uuid     | conv_created_at    | msg_created_at      |
    +----------+--------------------+---------------------+
    | A_new    | 2026-05-14 (newest)| 2026-03-01 (oldest) |
    | B_mid    | 2026-04-01         | 2026-04-15          |
    | C_old    | 2026-03-01 (oldest)| 2026-05-13 (newest) |
    +----------+--------------------+---------------------+

    Under buggy sort by min(msg.created_at) desc:
        C_old (05-13) → B_mid (04-15) → A_new (03-01)
    Under correct sort by conv_created_at desc:
        A_new (05-14) → B_mid (04-01) → C_old (03-01)
    """
    by_org = tmp_path / "by-org" / "org-1"
    convs = [
        _conv(
            "A_new",
            "A newest by conv_created",
            body="needle in this one",
            conv_updated_at="2026-05-14T10:00:00Z",
            conv_created_at="2026-05-14T10:00:00Z",
            msg_created_at="2026-03-01T10:00:00Z",
        ),
        _conv(
            "B_mid",
            "B middle",
            body="needle in middle",
            conv_updated_at="2026-04-01T10:00:00Z",
            conv_created_at="2026-04-01T10:00:00Z",
            msg_created_at="2026-04-15T10:00:00Z",
        ),
        _conv(
            "C_old",
            "C oldest by conv_created",
            body="needle in third",
            conv_updated_at="2026-03-01T10:00:00Z",
            conv_created_at="2026-03-01T10:00:00Z",
            msg_created_at="2026-05-13T10:00:00Z",
        ),
    ]
    for c in convs:
        _write_conv(by_org, c)
    cc_dir = tmp_path / "claude-empty"
    cc_dir.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=cc_dir)

    clear_cache()
    si.reset_search_index_for_tests()
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(store, index=idx)
    monkeypatch.setattr(si, "_search_index", idx)

    yield store, idx

    idx.close()
    si.reset_search_index_for_tests()
    clear_cache()


def test_sort_created_at_desc_uses_conversation_created_at_not_min_msg(
    store_with_inverted_created_timestamps,
):
    """sort='created_at' must use conversation_created_at, not min msg.

    With the bug (min-of-msg key), order desc is C_old → B_mid → A_new
    (because C_old's msg=05-13 is newest, A_new's msg=03-01 is oldest).
    After the fix: A_new → B_mid → C_old.
    """
    store, _idx = store_with_inverted_created_timestamps
    results = search_conversations(
        store=store,
        query="needle",
        source="all",
        context_size="snippet",
        sort="created_at",
        sort_order="desc",
    ).results
    uuids = [r.conversation_uuid for r in results]
    assert uuids == ["A_new", "B_mid", "C_old"], (
        f"sort=created_at desc must order by conversation_created_at, "
        f"got {uuids}"
    )
