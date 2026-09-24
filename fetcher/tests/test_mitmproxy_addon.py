"""Tests for the mitmproxy addon's multi-org response-hook decoding.

C2 of the cowork-multi-org plan. Covers Council P0-4 (gzip/brotli decode)
and the response-hook URL match regex.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# mitmproxy is a dependency on every platform (no marker since 2026-09-24),
# so import it plainly. A skip here once hid 17 tests on Windows ARM, where
# the old marker made the install random. A missing mitmproxy must fail.
import mitmproxy  # noqa: F401,E402

from fetcher.credentials import load_credentials
from fetcher.mitmproxy_addon import (
    ClaudeCredentialCapture,
    _is_organizations_endpoint,
)


# ---------------------------------------------------------------------------
# URL match regex (NEW2-P1-α adjacent)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://claude.ai/api/organizations", True),
        ("https://claude.ai/api/organizations?foo=bar", True),
        ("https://claude.ai/api/v1/organizations", True),
        ("https://claude.ai/api/v2/organizations?x", True),
        ("https://api.claude.ai/api/organizations", True),
        # NEGATIVE
        ("https://claude.ai/api/organizations/abc-uuid/chat_conversations", False),
        ("https://claude.ai/api/organizations/abc-uuid", False),
        ("https://claude.ai/api/organization", False),  # singular
        # Host filtering is done separately by _is_claude_request() before
        # this regex is called; the URL pattern itself is host-agnostic.
    ],
)
def test_response_hook_matches_versioned_path(url: str, expected: bool) -> None:
    assert _is_organizations_endpoint(url) is expected


# ---------------------------------------------------------------------------
# Response decode (P0-4)
# ---------------------------------------------------------------------------


def _path_from_url(url: str) -> str:
    """Strip scheme+host from URL, leaving the path (and query)."""
    # Cheap and correct enough for fixtures; matches mitmproxy's flow.request.path.
    no_scheme = url.split("://", 1)[-1]
    slash = no_scheme.find("/")
    return no_scheme[slash:] if slash != -1 else "/"


def _make_flow(url: str, body_bytes: bytes, content_type: str = "application/json", encoding: str | None = None):
    """Build a minimal mitmproxy-flow-shaped MagicMock for the addon."""
    flow = MagicMock()
    flow.request.pretty_url = url
    flow.request.host = "claude.ai"
    flow.request.headers = {"cookie": "sessionKey=sk-ant-fake; __cf_bm=bm; cf_clearance=cf"}
    flow.request.path = _path_from_url(url)
    flow.response.headers = {"content-type": content_type}
    if encoding:
        flow.response.headers["content-encoding"] = encoding
    flow.response.content = body_bytes

    # get_text() automatically decompresses, per mitmproxy docs.
    if encoding == "gzip":
        decoded = gzip.decompress(body_bytes).decode("utf-8")
    else:
        decoded = body_bytes.decode("utf-8")
    flow.response.get_text.return_value = decoded
    return flow


def test_response_hook_decodes_plain_json(tmp_path: Path) -> None:
    """Plain JSON body extracts org list and writes to credentials.json."""
    creds_path = tmp_path / "credentials.json"
    addon = ClaudeCredentialCapture()
    addon.credentials_path = creds_path

    # Send a request first to populate session_key + an initial org_id.
    addon.request(_make_flow("https://claude.ai/api/organizations/ae24ae66-4622-48e7-b4b3-1ab2c49f933d/chat_conversations", b""))

    # Now the response handler with a real /api/organizations response.
    body = json.dumps([
        {"uuid": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d", "name": "Personal", "capabilities": ["chat"]},
        {"uuid": "0c0c170b-1234-5678-90ab-cdef00000000", "name": "Cowork", "capabilities": ["chat"]},
    ]).encode("utf-8")
    addon.response(_make_flow("https://claude.ai/api/organizations", body))

    creds = load_credentials(creds_path)
    uuids = {o["uuid"] for o in creds["orgs"]}
    assert uuids == {"ae24ae66-4622-48e7-b4b3-1ab2c49f933d", "0c0c170b-1234-5678-90ab-cdef00000000"}
    # All orgs from the response are flagged seen_in_response=True
    seen = {o["uuid"] for o in creds["orgs"] if o["seen_in_response"]}
    assert "ae24ae66-4622-48e7-b4b3-1ab2c49f933d" in seen
    assert "0c0c170b-1234-5678-90ab-cdef00000000" in seen


def test_response_hook_decodes_gzip(tmp_path: Path) -> None:
    """P0-4. gzip-encoded /api/organizations body decodes correctly via get_text()."""
    creds_path = tmp_path / "credentials.json"
    addon = ClaudeCredentialCapture()
    addon.credentials_path = creds_path

    # Bootstrap session_key + org_id from a request flow first.
    addon.request(_make_flow("https://claude.ai/api/organizations/ae24ae66-4622-48e7-b4b3-1ab2c49f933d/chat_conversations", b""))

    body_json = json.dumps([
        {"uuid": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d", "name": "Personal"},
        {"uuid": "0c0c170b-1234-5678-90ab-cdef00000000", "name": "Cowork"},
    ]).encode("utf-8")
    body_gz = gzip.compress(body_json)
    addon.response(_make_flow("https://claude.ai/api/organizations", body_gz, encoding="gzip"))

    creds = load_credentials(creds_path)
    uuids = {o["uuid"] for o in creds["orgs"]}
    assert uuids == {"ae24ae66-4622-48e7-b4b3-1ab2c49f933d", "0c0c170b-1234-5678-90ab-cdef00000000"}


def test_response_hook_decode_failure_logs_and_continues(tmp_path: Path) -> None:
    """A truncated body must not crash the addon."""
    creds_path = tmp_path / "credentials.json"
    addon = ClaudeCredentialCapture()
    addon.credentials_path = creds_path
    addon.request(_make_flow("https://claude.ai/api/organizations/ae24ae66-4622-48e7-b4b3-1ab2c49f933d/chat_conversations", b""))

    # Truncated JSON
    flow = _make_flow("https://claude.ai/api/organizations", b'[{"uuid":"o')
    flow.response.get_text.return_value = '[{"uuid":"o'  # invalid JSON

    # MUST NOT raise.
    addon.response(flow)

    # Creds still exist (from bootstrap), but no new orgs added.
    creds = load_credentials(creds_path)
    # Only the URL-derived org from the request hook is present.
    assert any(o["uuid"] == "ae24ae66-4622-48e7-b4b3-1ab2c49f933d" for o in creds["orgs"])


def test_response_hook_does_not_match_chat_conversations(tmp_path: Path) -> None:
    """A response to /api/organizations/<uuid>/chat_conversations is NOT decoded
    as an org list."""
    creds_path = tmp_path / "credentials.json"
    addon = ClaudeCredentialCapture()
    addon.credentials_path = creds_path
    addon.request(_make_flow("https://claude.ai/api/organizations/ae24ae66-4622-48e7-b4b3-1ab2c49f933d/chat_conversations", b""))

    # Should not crash even though the body parses as JSON-like garbage for
    # an org-list parser.
    body = json.dumps({"chat_conversations": []}).encode("utf-8")
    addon.response(_make_flow("https://claude.ai/api/organizations/ae24ae66-4622-48e7-b4b3-1ab2c49f933d/chat_conversations", body))

    # No additional orgs got injected.
    creds = load_credentials(creds_path)
    assert len(creds["orgs"]) == 1
    assert creds["orgs"][0]["uuid"] == "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"


# ---------------------------------------------------------------------------
# Bootstrap behavior (request-hook with no creds yet)
# ---------------------------------------------------------------------------


def test_bootstrap_writes_initial_creds_from_request(tmp_path: Path) -> None:
    """First qualifying request creates credentials.json with v2 shape."""
    creds_path = tmp_path / "credentials.json"
    addon = ClaudeCredentialCapture()
    addon.credentials_path = creds_path

    addon.request(
        _make_flow("https://claude.ai/api/organizations/ae24ae66-4622-48e7-b4b3-1ab2c49f933d/chat_conversations", b"")
    )

    creds = load_credentials(creds_path)
    assert creds["schema_version"] == 2
    assert creds["session_key"] == "sk-ant-fake"
    assert creds["primary_org_id"] == "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
    # URL-derived orgs are not seen_in_response
    assert creds["orgs"][0]["seen_in_response"] is False


def test_request_hook_does_not_early_exit_after_first_org(tmp_path: Path) -> None:
    """The legacy `self.captured` early-exit is gone — multi-org URLs accumulate."""
    creds_path = tmp_path / "credentials.json"
    addon = ClaudeCredentialCapture()
    addon.credentials_path = creds_path

    addon.request(_make_flow("https://claude.ai/api/organizations/aaaaaaaa-1111-2222-3333-444444444444/chat_conversations", b""))
    addon.request(_make_flow("https://claude.ai/api/organizations/bbbbbbbb-1111-2222-3333-444444444444/chat_conversations", b""))

    creds = load_credentials(creds_path)
    uuids = {o["uuid"] for o in creds["orgs"]}
    assert uuids == {"aaaaaaaa-1111-2222-3333-444444444444", "bbbbbbbb-1111-2222-3333-444444444444"}


# ---------------------------------------------------------------------------
# F5 council finding (mitmproxy parity): _print_success previously echoed
# session_key[:20] to log handlers. Anthropic session keys begin with the
# fixed prefix ``sk-ant-sid01-`` (13 chars), so slicing 20 chars leaked 7
# chars of bearer-token entropy to terminal scrollback / CI logs / shell
# screenshots. ``fetcher/cli.py`` and ``fetcher/playwright_capture.py``
# already redact (see tests/test_capture_redaction.py); this mirrors the
# fix to the mitmproxy capture path.
# ---------------------------------------------------------------------------


_F5_FAKE_SESSION_KEY = "sk-ant-sid01-AAAAAAAAAAAAAAAAAAAA-BBBBBBBBBB-CCCC"


def _assert_no_session_key_entropy_in(records: list[str], key: str = _F5_FAKE_SESSION_KEY) -> None:
    """Bidirectional: ensure no non-prefix slice of the key leaks into log records."""
    prefix = "sk-ant-sid01-"
    entropy = key[len(prefix):]
    joined = "\n".join(records)
    for slice_len in (7, 10, 15, 20):
        sub = entropy[:slice_len]
        assert sub not in joined, (
            f"Session-key entropy substring {sub!r} (len={slice_len}) "
            f"leaked into mitmproxy log output:\n{joined}"
        )


def test_print_success_does_not_leak_session_key(tmp_path: Path, caplog) -> None:
    """mitmproxy _print_success success banner must redact the session key."""
    addon = ClaudeCredentialCapture()
    addon.credentials_path = tmp_path / "credentials.json"
    addon.session_key = _F5_FAKE_SESSION_KEY
    addon.orgs = {
        "uuid-A": {
            "uuid": "uuid-A",
            "name": "TestOrg",
            "capabilities": ["chat"],
            "seen_in_response": True,
        }
    }

    with caplog.at_level("INFO", logger="fetcher.mitmproxy_addon"):
        addon._print_success()

    _assert_no_session_key_entropy_in([rec.getMessage() for rec in caplog.records])


def test_print_success_still_emits_success_banner(tmp_path: Path, caplog) -> None:
    """Bidirectional positive: dropping the key prefix must NOT regress
    the success-confirmation banner. Operators must still see SOMETHING
    positive when capture completes (otherwise they don't know to quit
    mitmproxy)."""
    addon = ClaudeCredentialCapture()
    creds_path = tmp_path / "credentials.json"
    addon.credentials_path = creds_path
    addon.session_key = _F5_FAKE_SESSION_KEY
    addon.orgs = {
        "uuid-A": {
            "uuid": "uuid-A",
            "name": "TestOrg",
            "capabilities": ["chat"],
            "seen_in_response": True,
        }
    }

    with caplog.at_level("INFO", logger="fetcher.mitmproxy_addon"):
        addon._print_success()

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "CREDENTIALS CAPTURED SUCCESSFULLY" in joined, (
        f"Success banner missing from mitmproxy log output:\n{joined}"
    )
    assert str(creds_path) in joined, (
        f"Saved-path confirmation missing from mitmproxy log output:\n{joined}"
    )
    # Org count is non-secret operator info.
    assert "Orgs seen so far: 1" in joined, (
        f"Org-count confirmation missing from mitmproxy log output:\n{joined}"
    )


def test_print_success_handles_none_session_key(tmp_path: Path, caplog) -> None:
    """Boundary: session_key=None must not crash (defensive — _maybe_persist
    only calls _print_success when session_key is set, but be defensive)."""
    addon = ClaudeCredentialCapture()
    addon.credentials_path = tmp_path / "credentials.json"
    addon.session_key = None
    addon.orgs = {
        "uuid-A": {
            "uuid": "uuid-A",
            "name": None,
            "capabilities": [],
            "seen_in_response": False,
        }
    }

    with caplog.at_level("INFO", logger="fetcher.mitmproxy_addon"):
        addon._print_success()  # must not raise
