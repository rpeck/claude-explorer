"""Shared incremental-fetch entry used by the `fetch` CLI command and the
scheduled-fetch run routine. Holds the org-resolution (v1/v2/override)
+ ClaudeFetcher construction + run, so both callers stay identical and
auth failures (FetchAuthError) surface the same way."""

from __future__ import annotations

from pathlib import Path

import click

from fetcher.bulk_fetch import ClaudeFetcher, load_credentials


def run_incremental_fetch(
    *,
    output_dir: Path,
    files_dir: Path,
    credentials: Path,
    session_key: str | None,
    org_id: str | None,
    incremental: bool,
    download_files: bool,
    delay: float,
    limit: int | None,
    verbose: bool,
) -> None:
    """Resolve org credentials and run a ClaudeFetcher incremental fetch.

    Three input modes are supported:

      1. ``session_key`` AND ``org_id`` overrides (CI / power user):
         synthesize a single-element orgs list with the override org as
         primary; skip credentials.json entirely.
      2. v2 credentials file with ``orgs`` array + ``primary_org_id``:
         forward both straight through (multi-org capture/fetch path).
      3. v1 (legacy) credentials file with flat ``org_id`` scalar:
         treat the v1 org as a single-element orgs list with that org
         as primary, mirroring ``fetcher.credentials._upgrade_v1_in_memory``.

    Raises ``click.ClickException`` for missing/invalid credentials.
    Propagates ``FetchAuthError`` from ``ClaudeFetcher.run`` (do NOT catch it
    here — callers handle it differently).
    """
    cf_bm: str | None = None
    cf_clearance: str | None = None
    if session_key and org_id:
        # Mode 1 — override path.
        orgs = [
            {
                "uuid": org_id,
                "name": None,
                "capabilities": [],
                "seen_in_response": False,
            }
        ]
        primary = org_id
    else:
        creds = load_credentials(credentials)
        session_key = session_key or creds.get("session_key")

        # Multi-org-aware: prefer the orgs array if present (v2 schema).
        # Fall back to the legacy scalar org_id (v1 file) so this code
        # path works during the cowork-multi-org rollout window.
        if "orgs" in creds and creds.get("orgs"):
            # Mode 2 — v2.
            orgs = list(creds["orgs"])
            primary = creds.get("primary_org_id") or orgs[0]["uuid"]
        else:
            # Mode 3 — v1 (or --org-id override on top of v1 creds).
            legacy_id = org_id or creds.get("org_id")
            if not legacy_id:
                raise click.ClickException(
                    "Missing org_id. Run `claude-explorer capture` to "
                    "refresh credentials."
                )
            orgs = [
                {
                    "uuid": legacy_id,
                    "name": None,
                    "capabilities": [],
                    "seen_in_response": False,
                }
            ]
            primary = legacy_id

        cf_bm = creds.get("cf_bm")
        cf_clearance = creds.get("cf_clearance")

    if not session_key:
        raise click.ClickException(
            "Missing session_key. Run `claude-explorer capture` first."
        )

    fetcher = ClaudeFetcher(
        session_key=session_key,
        orgs=orgs,
        primary_org_id=primary,
        output_dir=output_dir,
        files_dir=files_dir,
        delay=delay,
        incremental=incremental,
        verbose=verbose,
        download_files=download_files,
        cf_bm=cf_bm,
        cf_clearance=cf_clearance,
    )
    fetcher.run(limit=limit)
