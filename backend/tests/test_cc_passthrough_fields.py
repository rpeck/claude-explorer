"""V1 polish (2026-05-13, Fix 5) — plumbing-fragility canary.

`backend/store._parse_message` hand-forwards a known list of CC-only
flags from the raw dict into the Pydantic `Message` constructor. Earlier
we lost an hour debugging the `slash_command` field because that single
forward line was omitted. To prevent recurrence we centralized the field
list in the `_CC_ONLY_PASSTHROUGH_FIELDS` constant and use dict-
comprehension forwarding.

This test introspects the `Message` model and FAILS the moment anyone
adds a new field without updating the constant. That's the canary's
job: brittle on purpose so the next CC-only field can't silently fall
through the floor.

Bidirectional contract:
  * POSITIVE: every CC-only field on the Message model is in the
    passthrough constant.
  * NEGATIVE: every Desktop-compatible field on the model is NOT in
    the passthrough constant (would be a bug — Desktop fields are
    forwarded by name explicitly in _parse_message).
"""

from __future__ import annotations

from backend.models import Message
from backend.store import _CC_ONLY_DEFAULTS, _CC_ONLY_PASSTHROUGH_FIELDS

# These are the fields that should be hand-forwarded in _parse_message:
# every "CC-only" field that the Claude Code reader sets via the
# collapser / fold / prelude-flag passes. The reader populates them
# via plain dict keys on the synthesized marker dicts; the Pydantic
# Message model then reads them via these passthrough lines.
_EXPECTED_CC_ONLY_FIELDS = {
    "is_command_marker",
    "is_prelude",
    "assistant_canned_response_consumed",
    "slash_command",
}

# Desktop-compatible fields: explicitly named in _parse_message OUTSIDE
# the dict-comprehension. These are NOT in the passthrough constant by
# design — they have type coercions (datetime, ContentBlock parsing,
# nested files lists) that the dict-comprehension forwarding doesn't do.
_EXPECTED_DESKTOP_FIELDS = {
    "uuid",
    "sender",
    "text",
    "content",
    "created_at",
    "updated_at",
    "truncated",
    "parent_message_uuid",
    "attachments",
    "files",
    "files_v2",
}


def test_cc_only_passthrough_constant_matches_expected_fields() -> None:
    """The constant in store.py MUST equal the canonical CC-only field set."""
    assert set(_CC_ONLY_PASSTHROUGH_FIELDS) == _EXPECTED_CC_ONLY_FIELDS, (
        f"_CC_ONLY_PASSTHROUGH_FIELDS drifted from the expected set. "
        f"Constant: {set(_CC_ONLY_PASSTHROUGH_FIELDS)!r}. "
        f"Expected: {_EXPECTED_CC_ONLY_FIELDS!r}."
    )


def test_cc_only_defaults_covers_every_passthrough_field() -> None:
    """Every field listed in _CC_ONLY_PASSTHROUGH_FIELDS must have a
    matching entry in _CC_ONLY_DEFAULTS. A missing key would raise
    KeyError at parse time when a Desktop message (which doesn't have
    these keys) flows through the dict-comprehension."""
    assert set(_CC_ONLY_DEFAULTS.keys()) == set(_CC_ONLY_PASSTHROUGH_FIELDS), (
        f"_CC_ONLY_DEFAULTS keys must match _CC_ONLY_PASSTHROUGH_FIELDS. "
        f"Defaults: {set(_CC_ONLY_DEFAULTS.keys())!r}. "
        f"Passthrough: {set(_CC_ONLY_PASSTHROUGH_FIELDS)!r}."
    )


def test_message_model_fields_match_expected_union() -> None:
    """The Message Pydantic model MUST expose exactly the union of CC-only
    and Desktop fields. This is the canary: adding a NEW field to the
    model breaks this test, forcing the developer to decide whether the
    field is CC-only (add to the constant + defaults) or Desktop
    (add explicit forwarding line in _parse_message).

    Without this test, a new CC-only field would silently get its
    Pydantic default in the API response — exactly the plumbing-
    fragility bug Fix 5 prevents.
    """
    actual = set(Message.model_fields.keys())
    expected = _EXPECTED_CC_ONLY_FIELDS | _EXPECTED_DESKTOP_FIELDS
    assert actual == expected, (
        f"Message.model_fields drifted from the expected union. "
        f"If you added a new field, decide: CC-only (extend "
        f"_CC_ONLY_PASSTHROUGH_FIELDS + _CC_ONLY_DEFAULTS) or "
        f"Desktop (extend the explicit kwargs in _parse_message AND "
        f"_EXPECTED_DESKTOP_FIELDS here). "
        f"Got: {actual!r}. "
        f"Expected: {expected!r}. "
        f"Added: {actual - expected!r}. "
        f"Removed: {expected - actual!r}."
    )


def test_cc_passthrough_round_trip_preserves_all_flags() -> None:
    """Functional canary: a raw dict containing all four CC-only flags
    must round-trip through _parse_message and surface intact on the
    Pydantic Message instance.
    """
    from backend.store import _parse_message

    raw = {
        "uuid": "u1",
        "sender": "human",
        "text": "Session: /exit",
        "content": [{"type": "text", "text": "Session: /exit"}],
        "created_at": "2026-04-19T01:31:14Z",
        "updated_at": "2026-04-19T01:31:14Z",
        "truncated": False,
        "parent_message_uuid": None,
        "attachments": [],
        "files": [],
        "is_command_marker": True,
        "is_prelude": True,
        "assistant_canned_response_consumed": True,
        "slash_command": "/exit",
    }
    msg = _parse_message(raw)
    assert msg.is_command_marker is True
    assert msg.is_prelude is True
    assert msg.assistant_canned_response_consumed is True
    assert msg.slash_command == "/exit"


def test_cc_passthrough_round_trip_desktop_defaults() -> None:
    """Bidirectional inverse: a raw dict WITHOUT the CC-only keys (e.g.
    a Desktop message) must yield the documented defaults — never a
    KeyError. The dict-comprehension forwarding uses
    `raw.get(k, _CC_ONLY_DEFAULTS[k])`, so this test pins the default
    fallback path.
    """
    from backend.store import _parse_message

    raw = {
        "uuid": "u1",
        "sender": "human",
        "text": "Plain Desktop message.",
        "content": [{"type": "text", "text": "Plain Desktop message."}],
        "created_at": "2026-04-19T01:31:14Z",
        "updated_at": "2026-04-19T01:31:14Z",
        "truncated": False,
        "parent_message_uuid": None,
        "attachments": [],
        "files": [],
        # No is_command_marker / is_prelude / etc.
    }
    msg = _parse_message(raw)
    assert msg.is_command_marker is False
    assert msg.is_prelude is False
    assert msg.assistant_canned_response_consumed is False
    assert msg.slash_command is None
