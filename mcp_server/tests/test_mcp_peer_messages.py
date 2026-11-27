"""L1 (issue #91, part 1): the MCP ``get_messages`` tool and peer links.

The contract (orchestrator, 2026-11-27):

* Each message with a ``peer_ref`` gets ``"peer_ref"``: the PeerRef dict,
  ``msg_id`` included, in text mode and in structured mode.
* In text mode the text of a received row is the body; the label is
  ``"Message from @<name>"``.
* A ``tool_use`` block dict gets ``"peer_ref"`` when ``include_tool_calls`` is on.
* A message or block without a link has no ``peer_ref`` key.
* An agent can follow a link: ``get_messages(conversation_id=conversation_uuid,
  message_uuids=[message_uuid])``.
* Still five tools (pinned by ``test_server_metadata.py``).

The sessions are ``backend/tests/test_peer_messages.build_sessions``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests.test_peer_messages import (
    M_AB,
    MID,
    NAMES,
    PEER_REF_KEYS,
    PING,
    A,
    B,
    D,
    U,
    use_corpus,
)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from mcp_server import server

    with use_corpus(tmp_path, monkeypatch):
        server.reset_archive_sync_for_tests()
        try:
            yield
        finally:
            server.reset_archive_sync_for_tests()


def _get(conversation_id: str, uuids: list[str], **kw: Any) -> list[dict[str, Any]]:
    from mcp_server import server

    fn = server.get_messages
    return getattr(fn, "fn", fn)(
        conversation_id=conversation_id, message_uuids=uuids, **kw
    )


def _one(conversation_id: str, uuid: str, **kw: Any) -> dict[str, Any]:
    (msg,) = _get(conversation_id, [uuid], **kw)
    return msg


def test_text_mode_gives_the_body_and_the_peer_ref(corpus: None) -> None:
    msg = _one(B, U["b_ping_row"])
    assert msg["text"] == PING
    assert set(msg["peer_ref"]) == PEER_REF_KEYS
    assert msg["peer_ref"]["msg_id"] == M_AB
    assert msg["peer_ref"]["kind"] == "received"


def test_a_received_row_has_the_message_from_label(corpus: None) -> None:
    assert _one(B, U["b_ping_row"])["label"] == f"Message from @{NAMES[A]}"


def test_a_mid_turn_row_has_its_body_and_label(corpus: None) -> None:
    msg = _one(D, U["d_mid_row"])
    assert (msg["text"], msg["label"]) == (MID, "Message from @fixture-peer-c")


def test_structured_mode_keeps_the_peer_ref_of_a_row(corpus: None) -> None:
    msg = _one(B, U["b_ping_row"], include_tool_calls=True)
    assert msg["peer_ref"]["conversation_uuid"] == A


def test_a_send_block_has_its_peer_ref_with_tool_calls_on(corpus: None) -> None:
    msg = _one(A, U["a_send"], include_tool_calls=True)
    (block,) = [b for b in msg["content"] if b["type"] == "tool_use"]
    assert block["name"] == "SendMessage"
    ref = block["peer_ref"]
    assert set(ref) == PEER_REF_KEYS
    assert (
        ref["kind"],
        ref["msg_id"],
        ref["conversation_uuid"],
        ref["message_uuid"],
    ) == ("sent", M_AB, B, U["b_ping_row"])


def test_an_agent_can_follow_a_received_row_to_the_send(corpus: None) -> None:
    ref = _one(B, U["b_ping_row"])["peer_ref"]
    msg = _one(ref["conversation_uuid"], ref["message_uuid"], include_tool_calls=True)
    assert [b.get("id") for b in msg["content"] if b["type"] == "tool_use"] == [
        "toolu_a_send"
    ]


def test_an_agent_can_follow_a_send_to_the_received_row(corpus: None) -> None:
    msg = _one(A, U["a_send"], include_tool_calls=True)
    (block,) = [b for b in msg["content"] if b["type"] == "tool_use"]
    ref = block["peer_ref"]
    assert _one(ref["conversation_uuid"], ref["message_uuid"])["text"] == PING


def test_an_idle_notice_has_its_peer_ref(corpus: None) -> None:
    ref = _one(A, U["a_idle"])["peer_ref"]
    assert (ref["kind"], ref["notify_message_uuid"], ref["conversation_uuid"]) == (
        "idle_notice",
        U["a_send"],
        B,
    )


def test_messages_without_a_link_have_no_peer_ref_key(corpus: None) -> None:
    msgs = _get(A, [U["a_prompt"], U["a_send"]], include_tool_calls=True)
    assert "peer_ref" not in msgs[0]
    assert "peer_ref" not in msgs[1]
    assert any("peer_ref" in b for b in msgs[1]["content"])
