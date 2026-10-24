"""mitmproxy addon to capture Claude Desktop session credentials.

This addon now accumulates **all** orgs it sees rather than latching onto the
first one (Council P0-2). Two paths feed the org list:

1. **Request hook** — extracts org UUIDs from URL paths like
   ``/api/organizations/<uuid>/chat_conversations``. These are tagged
   ``seen_in_response=False`` since URL extraction can't see the real org name.

2. **Response hook** — when ``/api/organizations`` itself comes back, decodes
   the body (handling gzip/brotli via ``flow.response.get_text()`` per
   Council P0-4) and feeds the full ``[{uuid, name, capabilities}]`` array
   into :func:`fetcher.credentials.merge_orgs_and_save` with
   ``seen_in_response=True``. The merge prefers ``seen_in_response=True``
   entries so URL-only fallbacks never overwrite real names.

All persistence routes through :mod:`fetcher.credentials` — this module never
touches ``credentials.json`` directly.

Usage::

    mitmproxy -s fetcher/mitmproxy_addon.py --listen-port 8080

Then launch Claude Desktop through the proxy::

    macOS:   open -a "Claude" --args --proxy-server="127.0.0.1:8080" \\
                                     --ignore-certificate-errors
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from mitmproxy import http

from fetcher.credentials import (
    DEFAULT_CREDENTIALS_PATH,
    CredentialsCorruptError,
    LockContentionError,
    OrgRef,
    merge_orgs_and_save,
    resolve_primary_org_id,
    save_credentials,
)

log = logging.getLogger(__name__)


# Cookie patterns. Cookies can be separated by "; " or ", " depending on the
# client.
SESSION_KEY_PATTERN = re.compile(r"sessionKey=([^;,]+)")
CF_BM_PATTERN = re.compile(r"__cf_bm=([^;,]+)")
CF_CLEARANCE_PATTERN = re.compile(r"cf_clearance=([^;,]+)")

# URL path: /api/organizations/<uuid>/...
ORG_ID_FROM_PATH_PATTERN = re.compile(r"/api/organizations/([a-f0-9-]{36})/")

# URL match for the bare /api/organizations endpoint (with optional version
# segment). Matches: /api/organizations, /api/organizations?..., /api/v1/organizations
# Does NOT match: /api/organizations/<uuid>/anything, /api/organization (singular).
ORGANIZATIONS_ENDPOINT_PATTERN = re.compile(
    r"^https?://[^/]+/api/(?:v\d+/)?organizations(?:\?.*)?$"
)


def _is_organizations_endpoint(url: str) -> bool:
    """True if URL is the bare /api/organizations (or versioned variant)."""
    return ORGANIZATIONS_ENDPOINT_PATTERN.match(url) is not None


class ClaudeCredentialCapture:
    """mitmproxy addon that captures Claude Desktop session credentials."""

    def __init__(self) -> None:
        self.credentials_path: Path = DEFAULT_CREDENTIALS_PATH
        self.session_key: str | None = None
        self.cf_bm: str | None = None
        self.cf_clearance: str | None = None
        # UUID-keyed accumulator. Replaces the legacy scalar self.org_id.
        self.orgs: dict[str, OrgRef] = {}
        self._success_printed = False

    # ---------------------------------------------------------------- request

    def request(self, flow: http.HTTPFlow) -> None:
        """Intercept requests; extract cookies + URL-derived orgs.

        No early-exit after first capture — multi-org URLs accumulate over
        the session.
        """
        if not self._is_claude_request(flow.request.host):
            return

        cookie_header = flow.request.headers.get("cookie", "")

        session_key = self._extract_pattern(SESSION_KEY_PATTERN, cookie_header)
        if session_key:
            self.session_key = session_key

        cf_bm = self._extract_pattern(CF_BM_PATTERN, cookie_header)
        if cf_bm:
            self.cf_bm = cf_bm

        cf_clearance = self._extract_pattern(CF_CLEARANCE_PATTERN, cookie_header)
        if cf_clearance:
            self.cf_clearance = cf_clearance

        # URL-derived org id (seen_in_response=False; may be overwritten by
        # the response hook later).
        org_id = self._extract_org_id_from_path(flow.request.path)
        if org_id and org_id not in self.orgs:
            self.orgs[org_id] = {
                "uuid": org_id,
                "name": None,
                "capabilities": [],
                "seen_in_response": False,
            }

        # Try to persist now if we have enough state.
        self._maybe_persist()

    # --------------------------------------------------------------- response

    def response(self, flow: http.HTTPFlow) -> None:
        """If this is /api/organizations, extract the full org list."""
        if not self._is_claude_request(flow.request.host):
            return

        url = flow.request.pretty_url
        if not _is_organizations_endpoint(url):
            return

        if flow.response is None:
            return

        try:
            # get_text() handles gzip/brotli/etc transparently per mitmproxy
            # docs. This is the P0-4 fix.
            body = flow.response.get_text()
            if not body:
                return
            data = json.loads(body)
        except Exception as e:
            log.warning(
                "organizations response decode failed "
                "(content-encoding=%r, content-type=%r, content-length=%r): %s",
                flow.response.headers.get("content-encoding"),
                flow.response.headers.get("content-type"),
                flow.response.headers.get("content-length"),
                e,
            )
            return

        if not isinstance(data, list):
            return  # not the list endpoint we expected

        new_orgs: list[OrgRef] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            uuid = entry.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                continue
            new_orgs.append({
                "uuid": uuid,
                "name": entry.get("name"),
                "capabilities": entry.get("capabilities", []) or [],
                "seen_in_response": True,
            })

        if not new_orgs:
            return

        # Update local accumulator (response data wins over URL-derived).
        for org in new_orgs:
            self.orgs[org["uuid"]] = org

        # Persist via the appropriate path.
        self._maybe_persist(force_orgs=new_orgs)

    # ------------------------------------------------------------- internals

    def _is_claude_request(self, host: str) -> bool:
        """Check if request is to Claude's API."""
        return host in ("claude.ai", "api.claude.ai", "www.claude.ai")

    def _extract_pattern(self, pattern: re.Pattern, text: str) -> str | None:
        """Extract a value using a regex pattern."""
        match = pattern.search(text)
        if match:
            return match.group(1)
        return None

    def _extract_org_id_from_path(self, path: str) -> str | None:
        """Extract organization ID from API path of form /api/organizations/<uuid>/..."""
        match = ORG_ID_FROM_PATH_PATTERN.search(path)
        if match:
            return match.group(1)
        return None

    def _maybe_persist(self, *, force_orgs: list[OrgRef] | None = None) -> None:
        """Bootstrap or merge into credentials.json.

        * If no creds file exists yet AND we have at least session_key + 1 org,
          write an initial CredentialsV2 via :func:`save_credentials`.
        * If creds file already exists, call :func:`merge_orgs_and_save` so we
          don't overwrite anything else (including a manually-pinned
          ``primary_org_id``).
        """
        if not self.session_key or not self.orgs:
            return

        # File-existence check is racy across processes — but
        # save_credentials and merge_orgs_and_save both serialize on the same
        # portalocker lock so the race is benign.
        creds_path = self.credentials_path

        if creds_path.exists():
            new_orgs = force_orgs if force_orgs is not None else list(self.orgs.values())
            try:
                merge_orgs_and_save(new_orgs, creds_path)
            except (FileNotFoundError, CredentialsCorruptError, LockContentionError) as e:
                log.warning("merge_orgs_and_save failed: %s", e)
            return

        # Bootstrap: build initial v2 record.
        # No prior_primary because this is the bootstrap path (no prior file).
        primary = resolve_primary_org_id(list(self.orgs.values()))
        creds = {
            "schema_version": 2,
            "session_key": self.session_key,
            "cf_bm": self.cf_bm,
            "cf_clearance": self.cf_clearance,
            "captured_at": datetime.now(UTC).isoformat(),
            "orgs": list(self.orgs.values()),
            "primary_org_id": primary,
            # Fresh-install bootstrap: legacy_migration_target = primary
            # (definitionally correct since this IS the original org for any
            # untagged data that may appear).
            "legacy_migration_target": primary,
            "org_id": primary,
        }
        try:
            save_credentials(creds, creds_path)  # type: ignore[arg-type]
        except (CredentialsCorruptError, LockContentionError) as e:
            log.warning("save_credentials failed: %s", e)
            return

        if not self._success_printed:
            self._print_success()
            self._success_printed = True

    def _print_success(self) -> None:
        """Print success message with next steps.

        Banner intentionally does NOT echo any portion of ``session_key`` —
        Anthropic keys begin with the fixed prefix ``sk-ant-sid01-`` (13
        chars), so any slice past the prefix leaks bearer-token entropy
        into terminal scrollback, screen recordings, and CI logs. Mirrors
        the F5 redaction in ``cli/main.py`` and
        ``fetcher/playwright_capture.py``.
        """
        log.info("=" * 60)
        log.info("CREDENTIALS CAPTURED SUCCESSFULLY")
        log.info("=" * 60)
        log.info("   Session key: *** [REDACTED]")
        log.info("   Orgs seen so far: %d", len(self.orgs))
        log.info("   Saved to: %s", self.credentials_path)
        log.info("")
        log.info(
            "   You can quit mitmproxy (press 'q') after Claude Desktop has"
            " loaded a few conversations so we capture the full org list."
        )
        log.info("   Then run: uv run claude-explorer fetch")
        log.info("=" * 60)


# mitmproxy entry point
addons = [ClaudeCredentialCapture()]
