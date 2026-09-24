"""Tests for the canonical primary-org resolver in ``fetcher.credentials``.

Council D1 finding: ``fetcher/playwright_capture.py`` and
``fetcher/mitmproxy_addon.py`` previously duplicated the same
"chat-capable else lex-sort by uuid" selection algorithm. The mitm path
additionally diverged by not honoring ``prior_primary``, which is a real
correctness gap: a user with a manually-pinned ``primary_org_id`` would
have it silently re-picked by mitm during recapture.

Resolution: extract a single ``resolve_primary_org_id(orgs, prior_primary)``
into ``fetcher/credentials.py`` (canonical typing module already) and
delegate from all three sites:

  * ``playwright_capture._build_credentials``
  * ``mitmproxy_addon.ClaudeCredentialCapture._maybe_persist`` (bootstrap)
  * ``bulk_fetch.ClaudeFetcher._pick_new_primary`` (post-demote re-pick)

Tests below pin the contract bidirectionally:

  * Positive paths for each of the three resolution steps.
  * Negative path: empty orgs raises ``ValueError``.
  * Boundary: invalid ``prior_primary`` (not in orgs) falls through to
    step 2 rather than silently returning it.
"""

from __future__ import annotations

import pytest

from fetcher.credentials import OrgRef, resolve_primary_org_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _org(uuid: str, capabilities: list[str] | None = None, seen: bool = True) -> OrgRef:
    return {
        "uuid": uuid,
        "name": None,
        "capabilities": list(capabilities or []),
        "seen_in_response": seen,
    }


# ---------------------------------------------------------------------------
# Step 1: prior_primary inheritance
# ---------------------------------------------------------------------------


def test_prior_primary_honored_when_present_in_orgs() -> None:
    """If prior_primary is still in the orgs list, it wins outright."""
    orgs = [
        _org("aaa", capabilities=["chat"]),
        _org("bbb", capabilities=["chat"]),
    ]
    # Without prior: chat-capable lex sort -> "aaa".
    # With prior "bbb": inheritance wins -> "bbb".
    assert resolve_primary_org_id(orgs, prior_primary="bbb") == "bbb"


def test_prior_primary_ignored_when_not_in_orgs() -> None:
    """Stale prior_primary (org gone) must fall through to step 2."""
    orgs = [_org("aaa", capabilities=["chat"]), _org("bbb")]
    # prior "ccc" no longer in orgs; chat-capable lex -> "aaa".
    assert resolve_primary_org_id(orgs, prior_primary="ccc") == "aaa"


def test_prior_primary_none_falls_through_to_chat_capable() -> None:
    """prior_primary=None (fresh capture) must not short-circuit."""
    orgs = [_org("zzz"), _org("aaa", capabilities=["chat"])]
    assert resolve_primary_org_id(orgs, prior_primary=None) == "aaa"


# ---------------------------------------------------------------------------
# Step 2: chat-capable lex-sort
# ---------------------------------------------------------------------------


def test_chat_capable_lex_sort_picks_lexicographically_first() -> None:
    orgs = [
        _org("zzz", capabilities=["chat"]),
        _org("aaa", capabilities=["chat"]),
        _org("mmm", capabilities=["chat"]),
    ]
    assert resolve_primary_org_id(orgs) == "aaa"


def test_chat_capable_skips_non_chat_orgs() -> None:
    """Non-chat orgs must NOT win even if lex-smaller."""
    orgs = [
        _org("aaa", capabilities=[]),               # no chat
        _org("bbb", capabilities=["chat"]),         # chat
        _org("ccc", capabilities=["chat"]),         # chat
    ]
    # "aaa" lex-smaller but skipped; chat-capable "bbb" wins.
    assert resolve_primary_org_id(orgs) == "bbb"


# ---------------------------------------------------------------------------
# Step 3: lex-sort by uuid (no chat-capable orgs)
# ---------------------------------------------------------------------------


def test_no_chat_capable_falls_back_to_uuid_lex_sort() -> None:
    orgs = [
        _org("zzz", capabilities=[]),
        _org("aaa", capabilities=[]),
        _org("mmm"),  # capabilities default [] (no chat)
    ]
    assert resolve_primary_org_id(orgs) == "aaa"


def test_lex_sort_handles_missing_capabilities_field() -> None:
    """Orgs whose ``capabilities`` key isn't a list (or is None) must not
    short-circuit step 2 incorrectly. Mirrors the existing
    ``"chat" in (o.get("capabilities") or [])`` defensive coalesce."""
    weird = _org("aaa")
    weird["capabilities"] = None  # type: ignore[typeddict-item]
    orgs = [weird, _org("bbb")]
    # Neither has chat capability; lex sort -> "aaa".
    assert resolve_primary_org_id(orgs) == "aaa"


# ---------------------------------------------------------------------------
# Boundary: empty orgs
# ---------------------------------------------------------------------------


def test_empty_orgs_raises_value_error() -> None:
    """Empty orgs is a programming bug — refuse to invent a primary."""
    with pytest.raises(ValueError, match="non-empty"):
        resolve_primary_org_id([])


def test_empty_orgs_raises_even_with_prior_primary() -> None:
    """prior_primary alone cannot synthesize a valid primary — orgs is the
    source of truth for *which uuids are real*."""
    with pytest.raises(ValueError, match="non-empty"):
        resolve_primary_org_id([], prior_primary="aaa")


# ---------------------------------------------------------------------------
# Call-site delegation: the three legacy helpers must now delegate.
#
# These are structural assertions, not behavioral. They prevent regression
# back into the "three local copies" pattern that D1 ships against.
# ---------------------------------------------------------------------------


def test_playwright_capture_uses_canonical_resolver() -> None:
    """playwright_capture must not redefine its own primary-selection helper."""
    import fetcher.playwright_capture as pc

    assert not hasattr(pc, "_resolve_primary_org_id"), (
        "playwright_capture._resolve_primary_org_id was removed in D1; "
        "use fetcher.credentials.resolve_primary_org_id instead."
    )


def test_mitmproxy_addon_uses_canonical_resolver() -> None:
    """mitmproxy_addon.ClaudeCredentialCapture must not redefine _pick_primary."""
    from fetcher.mitmproxy_addon import ClaudeCredentialCapture

    assert not hasattr(ClaudeCredentialCapture, "_pick_primary"), (
        "ClaudeCredentialCapture._pick_primary was removed in D1; "
        "use fetcher.credentials.resolve_primary_org_id instead."
    )
