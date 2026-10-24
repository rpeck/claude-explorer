"""Tests for ``backend.parsing.parse_datetime``.

This module is the consolidated home for the ``_parse_datetime`` helper
that previously lived (in identical form) in both
``backend.claude_code_reader`` and ``backend.store``. ``backend.search``
still imports ``_parse_datetime`` from ``backend.store`` at 8 call
sites, so both modules retain an aliased re-export.

Locked-in behavior (preserved from the pre-refactor implementations):

* Empty / ``None`` / unparseable input returns
  ``datetime.now(timezone.utc)`` — NOT ``None``. This is intentional so
  downstream consumers always have a tz-aware ``datetime``.
* A trailing ``"Z"`` is rewritten to ``"+00:00"`` before
  ``datetime.fromisoformat`` is called (works identically on Python
  3.10–3.12).
* A successfully-parsed naive datetime is tagged ``timezone.utc``.
* A successfully-parsed datetime with an explicit non-UTC offset is
  returned unchanged (the offset is preserved, NOT converted to UTC).
* Only ``ValueError`` is caught — bit-for-bit parity with the prior
  implementations. Exception-widening is deferred follow-up work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend import claude_code_reader, parsing, store

_NOW_TOLERANCE = timedelta(seconds=5)


def _is_close_to_now_utc(dt: datetime) -> bool:
    """True if ``dt`` is tz-aware UTC and within _NOW_TOLERANCE of now."""
    if dt.tzinfo is None:
        return False
    now = datetime.now(UTC)
    return abs(now - dt) <= _NOW_TOLERANCE


def test_parse_datetime_none_returns_now_utc():
    """``None`` input falls back to current UTC time (NOT ``None``)."""
    result = parsing.parse_datetime(None)
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert _is_close_to_now_utc(result)


def test_parse_datetime_empty_string_returns_now_utc():
    """Empty string falls back to current UTC time."""
    result = parsing.parse_datetime("")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert _is_close_to_now_utc(result)


def test_parse_datetime_iso_with_z_suffix():
    """Z-suffixed ISO strings parse as tz-aware UTC."""
    result = parsing.parse_datetime("2025-01-15T10:30:00Z")
    assert result.year == 2025
    assert result.month == 1
    assert result.day == 15
    assert result.hour == 10
    assert result.minute == 30
    assert result.second == 0
    # Z → +00:00 → tz-aware UTC
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


def test_parse_datetime_naive_iso_gets_utc_tagged():
    """A naive ISO string (no offset) gets ``timezone.utc`` attached."""
    result = parsing.parse_datetime("2025-01-15T10:30:00")
    assert result.year == 2025
    assert result.month == 1
    assert result.day == 15
    assert result.hour == 10
    assert result.minute == 30
    assert result.tzinfo is UTC


def test_parse_datetime_explicit_non_utc_offset_preserved():
    """Non-UTC offsets are preserved (NOT silently converted to UTC).

    Locks in current behavior so a future "fix" doesn't change it.
    """
    result = parsing.parse_datetime("2025-01-15T10:30:00+04:00")
    assert result.year == 2025
    assert result.month == 1
    assert result.day == 15
    assert result.hour == 10
    assert result.minute == 30
    # Offset is preserved, not normalized to UTC.
    assert result.utcoffset() == timedelta(hours=4)


def test_parse_datetime_unparseable_returns_now_utc():
    """Garbage input does not raise; falls back to current UTC time."""
    result = parsing.parse_datetime("not a date")
    assert isinstance(result, datetime)
    assert _is_close_to_now_utc(result)


def test_parse_datetime_trailing_whitespace_falls_back():
    """Trailing whitespace defeats the .endswith('Z') swap and
    ``fromisoformat`` rejects it, so we fall back to now(UTC)."""
    result = parsing.parse_datetime("2025-01-15T10:30:00Z ")
    assert isinstance(result, datetime)
    assert _is_close_to_now_utc(result)


def test_aliases_are_same_object_as_parsing_function():
    """Both modules re-export the canonical function via aliasing.

    The ``_parse_datetime`` symbols in ``backend.claude_code_reader``
    and ``backend.store`` MUST be the same callable object as
    ``backend.parsing.parse_datetime`` — not wrappers. ``backend.search``
    imports ``_parse_datetime`` from ``backend.store`` and that path
    must continue to resolve to the single canonical implementation.
    """
    assert claude_code_reader._parse_datetime is parsing.parse_datetime
    assert store._parse_datetime is parsing.parse_datetime


# ---------------------------------------------------------------------------
# Coercion-audit hardening: widen the catch beyond ``ValueError``.
#
# Background: the original implementation caught only ``ValueError`` to
# preserve bit-for-bit parity with the pre-refactor duplicates. Module
# docstring documented this as "exception-widening is deferred follow-up
# work". The unsafe-primitive-coercion audit (council bug-class #1)
# promoted this from deferred to HIGH after empirical reproduction:
#
#   parse_datetime(12345)        → AttributeError on .endswith("Z")
#   parse_datetime(["x"])        → AttributeError on .endswith("Z")
#   parse_datetime({"a": 1})     → AttributeError on .endswith("Z")
#
# All three are reachable via a corrupt-on-disk ``created_at`` /
# ``updated_at`` field in any conversation JSON file, and the loop in
# ``store.list_conversations`` makes a single bad row 500 the entire
# sidebar (not just that one conversation). Same blast-radius profile
# as 8ab36fc's null-safety bug.
#
# The contract these tests pin: any "wrong-type" truthy input falls
# back to ``datetime.now(timezone.utc)``, identical to the existing
# "unparseable string" behavior. Documented behavior unchanged; the
# set of inputs that trigger the fallback is widened.
# ---------------------------------------------------------------------------


def test_parse_datetime_int_input_falls_back_to_now_utc():
    """Int input (corrupt JSON: ``"created_at": 12345``) must not raise.

    Before the fix, this raised ``AttributeError: 'int' object has no
    attribute 'endswith'`` because the ``"Z"``-suffix rewrite assumes
    a string. The widened catch (``ValueError``, ``AttributeError``,
    ``TypeError``) collapses every non-string-and-not-None truthy
    input into the documented now-UTC fallback.
    """
    result = parsing.parse_datetime(12345)  # type: ignore[arg-type]
    assert isinstance(result, datetime)
    assert _is_close_to_now_utc(result)


def test_parse_datetime_list_input_falls_back_to_now_utc():
    """List input (corrupt JSON: ``"created_at": ["x"]``) must not raise."""
    result = parsing.parse_datetime(["2025-01-15"])  # type: ignore[arg-type]
    assert isinstance(result, datetime)
    assert _is_close_to_now_utc(result)


def test_parse_datetime_dict_input_falls_back_to_now_utc():
    """Dict input (corrupt JSON: ``"created_at": {"a": 1}``) must not raise."""
    result = parsing.parse_datetime({"year": 2025})  # type: ignore[arg-type]
    assert isinstance(result, datetime)
    assert _is_close_to_now_utc(result)


def test_parse_datetime_float_input_falls_back_to_now_utc():
    """Float input (e.g. epoch-seconds in a corrupt file) must not raise."""
    result = parsing.parse_datetime(1736900000.0)  # type: ignore[arg-type]
    assert isinstance(result, datetime)
    assert _is_close_to_now_utc(result)


def test_parse_datetime_bool_input_falls_back_to_now_utc():
    """Bool input (``"created_at": true``) must not raise.

    NB: ``True`` is truthy, so the ``if not dt_str`` short-circuit
    doesn't catch it. ``True.endswith(...)`` raises AttributeError
    before the widening fix.
    """
    result = parsing.parse_datetime(True)  # type: ignore[arg-type]
    assert isinstance(result, datetime)
    assert _is_close_to_now_utc(result)


# ---------------------------------------------------------------------------
# Hunt #7: ``_parse_iso_opt`` — the ``None``-on-failure primitive.
#
# Background: ``backend/cc_message_transforms.py`` and
# ``backend/cc_agent_reader.py`` both build ``all_timestamps`` lists and
# call ``min()`` / ``max()`` over them to derive a conversation's
# ``created_at`` / ``updated_at``. The original code parsed each
# timestamp inline with a bare ``try / except (ValueError, TypeError):
# pass`` — bad rows were silently DROPPED from the list.
#
# A naive refactor would have replaced the inline parse with
# ``parse_datetime``, which substitutes ``now(utc)`` on failure. That
# substitution would have inflated ``max()`` and bounced any
# conversation with a single corrupt timestamp to the top of the
# sidebar's recent-list UI. The council Critic caught this; the fix
# uses ``_parse_iso_opt`` (returns ``None`` on failure) and filters
# the list, preserving the original "drop bad rows" semantics while
# also fixing the wrong-type ``AttributeError`` crash class.
#
# These tests pin the contract of the primitive.
# ---------------------------------------------------------------------------


def test_parse_iso_opt_valid_z_returns_aware_datetime():
    """Happy path: well-formed Z-suffixed input parses to tz-aware UTC."""
    result = parsing._parse_iso_opt("2025-01-15T10:30:00Z")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


def test_parse_iso_opt_naive_input_gets_utc_tagged():
    """Naive ISO input is tagged with ``timezone.utc`` (never returns naive)."""
    result = parsing._parse_iso_opt("2025-01-15T10:30:00")
    assert isinstance(result, datetime)
    assert result.tzinfo is UTC


def test_parse_iso_opt_with_microseconds():
    """Microsecond-precision input parses without loss."""
    result = parsing._parse_iso_opt("2025-01-15T10:30:00.123456Z")
    assert isinstance(result, datetime)
    assert result.microsecond == 123456


def test_parse_iso_opt_with_milliseconds():
    """3-digit fractional seconds (JS ``toISOString()``) parse correctly."""
    result = parsing._parse_iso_opt("2025-01-15T10:30:00.123Z")
    assert isinstance(result, datetime)
    assert result.microsecond == 123000


def test_parse_iso_opt_explicit_offset_preserved():
    """Non-UTC offset is preserved on success (not silently normalized)."""
    result = parsing._parse_iso_opt("2025-01-15T10:30:00+04:00")
    assert isinstance(result, datetime)
    assert result.utcoffset() == timedelta(hours=4)


@pytest.mark.parametrize(
    "bad_input",
    [
        None,
        "",
        "not a date",
        "2025-13-01T10:30:00Z",  # month=13
        "2025-01-32T10:30:00Z",  # day=32
        "2025-01-15T25:00:00Z",  # hour=25
        "2025-01-15 10:30:00 PST",  # named TZ
        "2025/01/15 10:30:00",  # wrong separators
        "Jan 15, 2025",  # locale string
        "2025-01-15T10:30:00Z ",  # trailing whitespace
        " 2025-01-15T10:30:00Z",  # leading whitespace
        "\x00\x01\x02",  # control chars
        "x" * 10_000,  # very long garbage
        "2025-01-15T10:30:00\u202b",  # RTL mark suffix
    ],
)
def test_parse_iso_opt_bad_string_returns_none(bad_input):
    """Every malformed string input collapses to ``None`` — no exception."""
    assert parsing._parse_iso_opt(bad_input) is None


@pytest.mark.parametrize(
    "bad_input",
    [
        12345,
        12345.0,
        True,
        False,  # falsy → short-circuits to None
        ["2025-01-15"],
        {"year": 2025},
        ("2025-01-15",),
        object(),
    ],
)
def test_parse_iso_opt_wrong_type_returns_none(bad_input):
    """Every non-string truthy input collapses to ``None`` — no AttributeError.

    This is the regression class that 500'd the sidebar pre-fix: a
    corrupt JSON file with ``"timestamp": 12345`` reached
    ``ts.replace("Z", "+00:00")`` and threw ``AttributeError``, which
    the bare ``except (ValueError, TypeError)`` at the call sites did
    NOT catch. Centralizing in ``_parse_iso_opt`` makes the wide
    catch the canonical behavior.
    """
    assert parsing._parse_iso_opt(bad_input) is None


def test_parse_iso_opt_does_not_substitute_now():
    """The primitive must NEVER return ``now(utc)`` — that's the wrapper's job.

    Pins the contract that aggregation call sites
    (``cc_message_transforms``, ``cc_agent_reader``) rely on:
    ``_parse_iso_opt`` returning ``None`` lets them filter bad rows
    out of ``min()`` / ``max()``. If this primitive ever started
    returning ``now(utc)`` on failure, ``max()`` would inflate and
    bounce corrupt conversations to the top of the sidebar.
    """
    assert parsing._parse_iso_opt("garbage") is None
    assert parsing._parse_iso_opt(None) is None
    assert parsing._parse_iso_opt(12345) is None  # type: ignore[arg-type]


def test_parse_datetime_delegates_to_parse_iso_opt_on_success():
    """``parse_datetime`` returns the same datetime as ``_parse_iso_opt`` on success."""
    valid = "2025-01-15T10:30:00Z"
    assert parsing.parse_datetime(valid) == parsing._parse_iso_opt(valid)


def test_parse_datetime_substitutes_now_when_parse_iso_opt_returns_none():
    """``parse_datetime`` substitutes ``now(utc)`` exactly when
    ``_parse_iso_opt`` returns ``None``. This is the documented
    wrapper contract and the only behavioral difference between the two.
    """
    assert parsing._parse_iso_opt("garbage") is None
    fallback = parsing.parse_datetime("garbage")
    assert isinstance(fallback, datetime)
    assert _is_close_to_now_utc(fallback)
