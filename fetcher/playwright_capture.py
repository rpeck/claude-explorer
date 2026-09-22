"""
Capture Claude session credentials using Playwright.

Opens a browser window for the user to log into Claude, then extracts the
session credentials automatically and writes them as a CredentialsV2 record
via :mod:`fetcher.credentials`.

Multi-org support
-----------------

Per ``PLANS/cowork-multi-org.md`` (C2): the capture path now enumerates **all**
orgs from ``/api/organizations`` rather than latching onto ``data[0]``. The
orgs go into the credentials' ``orgs`` array (each with
``seen_in_response=True``), and ``primary_org_id`` is selected by inheriting
any prior pinned value if still valid, else by a deterministic resolution
algorithm (chat-capable first, then most conversations on disk, then lex
order). ``legacy_migration_target`` is preserved across recapture so the
migration script always routes legacy untagged JSONs to the same v1 org.

Usage::

    uv run python -m fetcher.playwright_capture

Or via CLI::

    claude-explorer capture
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import click
from playwright.async_api import async_playwright, Page, BrowserContext

from fetcher.install_hints import playwright_install_hint  # noqa: F401  re-export
from fetcher.credentials import (
    CredentialsCorruptError,
    CredentialsV2,
    DEFAULT_CREDENTIALS_PATH,
    OrgRef,
    load_credentials,
    resolve_primary_org_id,
    save_credentials,
)


log = logging.getLogger(__name__)


# Claude URLs
CLAUDE_LOGIN_URL = "https://claude.ai/login"
CLAUDE_HOME_URL = "https://claude.ai"
CLAUDE_API_BASE = "https://claude.ai/api"


async def wait_for_login(page: Page) -> bool:
    """Wait for user to complete login.

    Returns True if logged in, False if window was closed.
    """
    click.echo("Waiting for you to log in...")
    click.echo("(Complete the login process in the browser window)")

    try:
        # Wait for navigation away from login page, or for a logged-in indicator
        # The home page after login will have a different URL pattern
        while True:
            await asyncio.sleep(1)

            # Check if we're on a logged-in page
            url = page.url
            if "/login" not in url and "/chat" in url or url == "https://claude.ai/":
                # Give it a moment to fully load
                await asyncio.sleep(2)
                return True

            # Check for the chat input or new chat button as indicators of login
            try:
                # Look for elements that only appear when logged in
                logged_in = await page.locator(
                    '[data-testid="composer-input"], [data-testid="new-chat-button"], .ProseMirror'
                ).first.is_visible(timeout=500)
                if logged_in:
                    return True
            except Exception:
                pass

    except Exception as e:
        click.echo(f"Error waiting for login: {e}", err=True)
        return False


async def extract_cookies(context: BrowserContext) -> dict[str, str]:
    """Extract relevant cookies from browser context."""
    cookies = await context.cookies()

    result: dict[str, str] = {}
    for cookie in cookies:
        if cookie["name"] == "sessionKey":
            result["session_key"] = cookie["value"]
        elif cookie["name"] == "__cf_bm":
            result["cf_bm"] = cookie["value"]
        elif cookie["name"] == "cf_clearance":
            result["cf_clearance"] = cookie["value"]

    return result


async def get_orgs(page: Page) -> list[OrgRef]:
    """Return all orgs from /api/organizations as OrgRef list.

    Replaces the legacy ``get_org_id`` which discarded ``data[1:]`` (Council
    P0-2). Each entry is flagged ``seen_in_response=True`` since they came from
    the authoritative API.

    Falls back to URL-derived single-org with ``seen_in_response=False`` only
    when the API call fails entirely (returns empty list in that case; caller
    decides what to do).
    """
    try:
        response = await page.request.get(f"{CLAUDE_API_BASE}/organizations")
        if response.ok:
            data = await response.json()
            if isinstance(data, list) and data:
                return [
                    {
                        "uuid": entry["uuid"],
                        "name": entry.get("name"),
                        "capabilities": entry.get("capabilities", []) or [],
                        "seen_in_response": True,
                    }
                    for entry in data
                    if isinstance(entry, dict) and entry.get("uuid")
                ]
    except Exception as e:
        click.echo(f"Warning: Could not fetch orgs via API: {e}", err=True)

    # Fallback: try to extract one org id from URL.
    try:
        url = page.url
        if "/chat/" in url:
            parts = url.split("/")
            for part in parts:
                if len(part) == 36 and "-" in part:
                    return [
                        {
                            "uuid": part,
                            "name": None,
                            "capabilities": [],
                            "seen_in_response": False,
                        }
                    ]
    except Exception as e:
        # Council C3: don't swallow silently. The visible failure mode
        # is "capture returned no orgs"; a debug breadcrumb here means
        # operators can diagnose Claude API URL-shape changes without
        # having to add instrumentation after the fact. Debug level
        # keeps the happy path quiet.
        log.debug("get_orgs URL fallback extraction failed: %s", e)

    return []


def _build_credentials(
    *,
    creds_path: Path,
    session_key: str,
    cf_bm: str | None,
    cf_clearance: str | None,
    captured_at: str,
    orgs: list[OrgRef],
) -> CredentialsV2:
    """Build a CredentialsV2 dict, inheriting prior state from ``creds_path``.

    Inherits per "Capture-path preserves user state" (NEW2-P0-β + NEW2-P0-θ):

    * ``primary_org_id`` is inherited from the prior record only if the
      referenced uuid still appears in the new ``orgs`` list. Otherwise
      resolved deterministically.
    * ``legacy_migration_target`` is **always** inherited from the prior
      record. On a fresh first capture (no prior file), it defaults to the
      newly-resolved ``primary_org_id`` since this IS the original org.

    This function is pure — it reads ``creds_path`` once but does not write.
    The caller is responsible for ``save_credentials``.
    """
    if not orgs:
        raise ValueError("Cannot build credentials with empty orgs list")

    prior_primary: str | None = None
    prior_legacy_target: str | None = None
    try:
        prior = load_credentials(creds_path)
        prior_primary = prior.get("primary_org_id")
        prior_legacy_target = prior.get("legacy_migration_target")
    except FileNotFoundError:
        pass
    except CredentialsCorruptError as e:
        # Don't blow away a corrupt file silently — log and recapture from
        # scratch. The .bak left by the prior save is the recovery path.
        log.warning("Existing credentials corrupt; recapturing from scratch: %s", e)

    primary = resolve_primary_org_id(orgs, prior_primary=prior_primary)

    # Default legacy_migration_target to current primary on FIRST EVER capture
    # (no prior file). On every subsequent recapture, inherit the prior value
    # — which itself was either set by the v1 -> v2 in-memory upgrade
    # (NEW3-P0-C) or by a previous fresh capture.
    legacy_target = prior_legacy_target if prior_legacy_target else primary

    creds: CredentialsV2 = {
        "schema_version": 2,
        "session_key": session_key,
        "cf_bm": cf_bm,
        "cf_clearance": cf_clearance,
        "captured_at": captured_at,
        "orgs": orgs,
        "primary_org_id": primary,
        "legacy_migration_target": legacy_target,
        "org_id": primary,  # legacy mirror
    }
    return creds


async def capture_credentials(
    headless: bool = False,
    timeout: int = 300,
    creds_path: Path = DEFAULT_CREDENTIALS_PATH,
) -> CredentialsV2 | None:
    """Open browser, wait for login, and capture credentials.

    Args:
        headless: Run browser in headless mode (not useful for login).
        timeout: Max seconds to wait for login.
        creds_path: Where to read prior credentials from for inheritance.
            The caller is responsible for actually persisting the returned
            dict (typically via :func:`fetcher.credentials.save_credentials`).

    Returns:
        CredentialsV2 dict or None if failed.
    """
    # Build-9: When invoked from inside a FastAPI worker, Playwright's default
    # SIGINT/SIGTERM handlers fight Uvicorn's. Disable them — the FastAPI
    # process owns shutdown signaling.
    async with async_playwright() as p:
        # Launch browser - must be headed for user to log in
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            handle_sigint=False,
            handle_sigterm=False,
            handle_sighup=False,
        )

        # Create context with realistic settings
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        page = await context.new_page()

        try:
            click.echo(f"Opening {CLAUDE_HOME_URL}...")
            await page.goto(CLAUDE_HOME_URL, wait_until="networkidle")

            # Check if already logged in
            url = page.url
            if "/login" in url:
                click.echo("\nPlease log in to your Claude account in the browser window.")
                click.echo("This window will close automatically once login is complete.\n")

                try:
                    logged_in = await asyncio.wait_for(
                        wait_for_login(page),
                        timeout=timeout,
                    )
                    if not logged_in:
                        click.echo("Login was not completed.", err=True)
                        return None
                except asyncio.TimeoutError:
                    click.echo(f"Login timed out after {timeout} seconds.", err=True)
                    return None
            else:
                click.echo("Already logged in!")

            # Extract cookies
            click.echo("Extracting session credentials...")
            cookies = await extract_cookies(context)

            session_key = cookies.get("session_key")
            if not session_key:
                click.echo("Error: Could not find sessionKey cookie.", err=True)
                return None

            # Get all orgs
            click.echo("Fetching organizations...")
            orgs = await get_orgs(page)

            if not orgs:
                click.echo(
                    "Error: Could not enumerate organizations from /api/organizations.",
                    err=True,
                )
                return None

            creds = _build_credentials(
                creds_path=creds_path,
                session_key=session_key,
                cf_bm=cookies.get("cf_bm"),
                cf_clearance=cookies.get("cf_clearance"),
                captured_at=datetime.now(timezone.utc).isoformat(),
                orgs=orgs,
            )
            return creds

        finally:
            await browser.close()


def _format_org_summary(orgs: list[OrgRef], primary: str, verbose: bool) -> str:
    """Pretty-print the org list for the success banner.

    Names are redacted by default (P2-1) since workspace names may be
    sensitive (e.g. customer/project names in Cowork tenants). Use
    ``--verbose`` to show real names.
    """
    lines = []
    for org in orgs:
        marker = " [primary]" if org["uuid"] == primary else ""
        if verbose:
            name = org.get("name") or "(name unknown)"
        else:
            name = "***" if org.get("name") else "(name unknown)"
        lines.append(f"   {org['uuid'][:8]}…  {name}{marker}")
    return "\n".join(lines)


@click.command()
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=DEFAULT_CREDENTIALS_PATH,
    help="Where to save credentials",
)
@click.option(
    "--timeout",
    "-t",
    type=int,
    default=300,
    help="Max seconds to wait for login (default: 300)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show full org names in output (default: redacted as ***)",
)
def main(output: Path, timeout: int, verbose: bool) -> None:
    """Capture Claude session credentials by logging in via browser.

    Opens a browser window where you can log into Claude normally.
    Once logged in, credentials are automatically extracted and saved.
    """
    click.echo("=" * 60)
    click.echo("  Claude Credential Capture")
    click.echo("=" * 60)
    click.echo()

    credentials = asyncio.run(capture_credentials(timeout=timeout, creds_path=output))

    if credentials:
        save_credentials(credentials, output)

        click.echo()
        click.echo("=" * 60)
        click.echo("CREDENTIALS CAPTURED SUCCESSFULLY")
        click.echo("=" * 60)
        # Council F5: do NOT echo any prefix of the session key. The
        # Anthropic prefix "sk-ant-sid01-" is 13 chars, so even a
        # 20-char slice leaked ~7 chars of bearer-token entropy into
        # terminal scrollback, screenshots, CI logs, and shell history.
        # Saved-path + org summary remain as non-secret confirmation.
        click.echo(f"   Saved to:    {output}")
        click.echo(f"   {len(credentials['orgs'])} organization(s):")
        click.echo(_format_org_summary(credentials["orgs"], credentials["primary_org_id"], verbose))
        if not verbose and any(o.get("name") for o in credentials["orgs"]):
            click.echo("   (names redacted; use --verbose to show)")
        click.echo()
        click.echo("   You can now fetch conversations:")
        click.echo("   claude-explorer fetch")
        click.echo("=" * 60)
    else:
        click.echo()
        click.echo("Failed to capture credentials.", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
