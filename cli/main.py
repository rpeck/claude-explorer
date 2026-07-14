"""
CLI entry point for claude-explorer.

Usage:
    claude-explorer capture [OPTIONS]  Log into Claude and capture credentials
    claude-explorer fetch [OPTIONS]    Fetch conversations from Claude Desktop API
    claude-explorer serve [OPTIONS]    Start the web server

Note: Claude Code sessions are read directly from ~/.claude/projects/
      at runtime - no import step needed.

Promoted from ``fetcher/cli.py`` to top-level ``cli/main.py`` on
2026-05-21 (council A1-CLI-LAYER). The CLI orchestrates both
``backend`` and ``fetcher`` packages; keeping it under ``fetcher/``
forced a ``fetcher -> backend`` import edge that lied about the
intended layering. The console-script entry point in pyproject.toml
is ``claude-explorer = "cli.main:main"``.
"""

import json
import subprocess
import sys
from pathlib import Path

import click


@click.group()
@click.version_option(package_name="claude-explorer", prog_name="claude-explorer")
def main() -> None:
    """Claude Explorer - Export and browse your Claude conversations."""
    pass


@main.command()
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".claude-explorer" / "conversations",
    help="Where to save JSON files",
)
@click.option(
    "--files-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".claude-explorer" / "files",
    help="Where to save downloaded files (images, PDFs)",
)
@click.option(
    "--credentials",
    type=click.Path(path_type=Path),
    default=Path.home() / ".claude-explorer" / "credentials.json",
    help="Path to credentials file",
)
@click.option("--session-key", help="Session key (overrides credentials file)")
@click.option("--org-id", help="Org ID (overrides credentials file)")
@click.option(
    "--incremental/--full-refresh",
    default=True,
    help="Skip already-saved conversations",
)
@click.option(
    "--download-files/--no-download-files",
    default=True,
    help="Download attached images/PDFs (default: yes)",
)
@click.option("--delay", type=float, default=0.3, help="Seconds between requests")
@click.option("--limit", type=int, help="Max conversations to fetch")
@click.option("--verbose", is_flag=True, help="Show detailed output")
def fetch(
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
    """Fetch all conversations from Claude Desktop.

    Requires credentials captured via the mitmproxy addon.
    Run 'claude-explorer capture' first if you haven't yet.
    """
    # Layer 2 of PLANS/2026.05.18-config-corruption-safe-mode.md:
    # refuse to write when the user's config.json didn't parse — the
    # data_dir we'd write to is the wrong-default in that case, and
    # silently building a parallel archive there is the orphaning
    # failure mode this layer was created to fix. The HTTP gate
    # surfaces this as 503; the CLI surfaces it as a clean
    # ClickException with the same recovery hint, so users see
    # identical actionable copy regardless of the entry point.
    #
    # NOTE: ``install-watcher`` is EXEMPT from this gate (its writes
    # land in ~/Library/LaunchAgents / ~/.config/systemd / a launcher
    # file outside data_dir; it IS the recovery affordance). Don't
    # add the same check to install-watcher without revisiting
    # the L2 EXEMPTION decision record.
    from backend.config import get_settings
    from backend.deps import CONFIG_CORRUPT_REFUSAL_TEMPLATE

    settings = get_settings()
    if settings.config_corrupt_reason:
        raise click.ClickException(
            CONFIG_CORRUPT_REFUSAL_TEMPLATE.format(
                reason=settings.config_corrupt_reason
            )
        )

    from fetcher.bulk_fetch import ClaudeFetcher, load_credentials

    # Resolve orgs + primary_org_id for the v2 multi-org ClaudeFetcher
    # constructor. Three input modes are supported:
    #
    #   1. ``--session-key`` AND ``--org-id`` overrides (CI / power user):
    #      synthesize a single-element orgs list with the override org as
    #      primary; skip credentials.json entirely.
    #   2. v2 credentials file with ``orgs`` array + ``primary_org_id``:
    #      forward both straight through (multi-org capture/fetch path).
    #   3. v1 (legacy) credentials file with flat ``org_id`` scalar:
    #      treat the v1 org as a single-element orgs list with that org
    #      as primary, mirroring ``fetcher.credentials._upgrade_v1_in_memory``.
    #
    # This logic is a faithful port of the working version in
    # ``fetcher.bulk_fetch.main`` (deleted by Council A-BUG-2 to remove
    # the drift hazard that caused the original cli.py crash). The
    # ``ClaudeFetcher(..., org_id=org_id, ...)`` constructor call this
    # block replaces was a stale v1 wiring that was never updated when
    # multi-org shipped, and crashed every ``claude-explorer fetch`` run
    # with ``TypeError: unexpected keyword argument 'org_id'`` (Council
    # A-BUG-1; regression pinned by
    # ``fetcher/tests/test_cli_fetch_wiring.py``).
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


@main.command()
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path.home() / ".claude-explorer" / "credentials.json",
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
    "--proxy",
    is_flag=True,
    help="Use mitmproxy method (for when you can't log in but Claude Desktop is still authenticated)",
)
@click.option(
    "--port",
    default=8080,
    help="Proxy port when using --proxy method (default: 8080)",
)
def capture(output: Path, timeout: int, proxy: bool, port: int) -> None:
    """Capture Claude session credentials.

    By default, opens a browser window where you can log into Claude normally.
    Once logged in, credentials are automatically extracted and saved.

    Use --proxy for the mitmproxy method, which captures credentials from
    Claude Desktop traffic. This is useful when you can't log in via web
    (e.g., lost access to SSO) but Claude Desktop is still authenticated.
    """
    if proxy:
        _capture_via_proxy(port)
    else:
        _capture_via_browser(output, timeout)


def _capture_via_browser(output: Path, timeout: int) -> None:
    """Capture credentials by logging in via browser."""
    import asyncio

    # Check if playwright is importable (the actual browser binary check
    # happens later in `capture_credentials`). We import the symbol just
    # to exercise the import path; `find_spec` would miss installation
    # corruption that only surfaces at import time.
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        raise click.ClickException(
            "Playwright not installed. Run: uv sync && uv run playwright install chromium"
        )

    from fetcher.playwright_capture import capture_credentials
    from fetcher.credentials import save_credentials

    click.echo("=" * 60)
    click.echo("  Claude Credential Capture (Browser)")
    click.echo("=" * 60)
    click.echo()

    # Check if browsers are installed
    try:
        credentials = asyncio.run(capture_credentials(timeout=timeout))
    except Exception as e:
        if "Executable doesn't exist" in str(e) or "browserType.launch" in str(e):
            raise click.ClickException(
                "Playwright browsers not installed.\n"
                "Run: uv run playwright install chromium"
            )
        raise

    if credentials:
        save_credentials(credentials, output)

        click.echo()
        click.echo("=" * 60)
        click.echo("✅ CREDENTIALS CAPTURED SUCCESSFULLY!")
        click.echo("=" * 60)
        # Council F5: do NOT echo any prefix of the session key. The
        # Anthropic prefix "sk-ant-sid01-" is 13 chars, so even a
        # 20-char slice leaked ~7 chars of bearer-token entropy into
        # terminal scrollback, screenshots, CI logs, and shell history.
        # Saved-path + org-id remain as non-secret confirmation.
        click.echo(f"   Org ID: {credentials['org_id']}")
        click.echo(f"   Saved to: {output}")
        click.echo()
        click.echo("   You can now fetch conversations:")
        click.echo("   claude-explorer fetch")
        click.echo("=" * 60)
    else:
        click.echo()
        click.echo("❌ Failed to capture credentials.", err=True)
        raise SystemExit(1)


def _capture_via_proxy(port: int) -> None:
    """Capture credentials via mitmproxy (for Claude Desktop)."""
    addon_path = Path(__file__).parent / "mitmproxy_addon.py"

    click.echo("=" * 60)
    click.echo("  Claude Credential Capture (Proxy)")
    click.echo("=" * 60)
    click.echo()
    click.echo("This method intercepts Claude Desktop traffic to capture")
    click.echo("credentials. Useful when you can't log in via web but")
    click.echo("Claude Desktop is still authenticated.")
    click.echo()
    click.echo(f"Proxy listening on port {port}")
    click.echo()
    click.echo("In another terminal, launch Claude Desktop through the proxy:")
    click.echo()
    click.echo(f'  open -a "Claude" --args --proxy-server="127.0.0.1:{port}" --ignore-certificate-errors')
    click.echo()
    click.echo("Use Claude Desktop normally. Credentials will be captured automatically.")
    click.echo("Press 'q' to quit mitmproxy when done.")
    click.echo()

    try:
        subprocess.run(
            ["mitmproxy", "-s", str(addon_path), "--listen-port", str(port)],
            check=True,
        )
    except FileNotFoundError:
        # mitmproxy is a conditional dependency (PEP 508 marker in
        # pyproject.toml skips it on Windows ARM64 — see V2 plan at
        # PLANS/2026.06.12-V2-cookie-storage-read.md). When the binary
        # is absent, point users at the default browser-based capture,
        # which doesn't need mitmproxy.
        import platform
        import sys

        is_win_arm64 = sys.platform == "win32" and platform.machine() == "ARM64"
        if is_win_arm64:
            raise click.ClickException(
                "mitmproxy is not installed on this platform.\n"
                "\n"
                "Windows ARM64 has no prebuilt mitmproxy wheels available\n"
                "(see PLANS/2026.06.12-V2-cookie-storage-read.md).\n"
                "\n"
                "Use the default browser-based capture instead:\n"
                "    claude-explorer capture\n"
                "\n"
                "(Omit the --proxy flag; the browser flow works on every\n"
                "platform without mitmproxy.)\n"
                "\n"
                "If you specifically need the proxy method on Windows ARM64,\n"
                "install Visual Studio Build Tools + Rust toolchain and run:\n"
                "    pipx inject claude-explorer mitmproxy"
            )
        raise click.ClickException(
            "mitmproxy not found.\n"
            "\n"
            "Install with: pipx inject claude-explorer mitmproxy\n"
            "(Or use the default browser-based capture: omit --proxy.)"
        )
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"mitmproxy exited with error: {e}")


@main.command()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=Path.home() / ".claude-explorer" / "conversations",
    help="Conversations directory to migrate",
)
@click.option(
    "--credentials",
    type=click.Path(path_type=Path),
    default=Path.home() / ".claude-explorer" / "credentials.json",
    help="Path to credentials file (provides legacy_migration_target)",
)
def migrate(data_dir: Path, credentials: Path) -> None:
    """Run the v1 -> v2 per-org subdir migration explicitly.

    Useful for users with large data dirs who want to run the migration
    offline rather than blocking the SSE fetch or server startup.
    """
    from fetcher.migrate_to_v2 import migrate_to_v2

    click.echo(f"Migrating {data_dir}...")

    def _progress(moved: int, total: int) -> None:
        if total:
            click.echo(f"  {moved}/{total} files migrated")

    try:
        migrate_to_v2(
            data_dir=data_dir,
            credentials_path=credentials,
            on_progress=_progress,
            lock_command="cli_migrate",
        )
        click.echo("Migration complete.")
    except Exception as e:
        raise click.ClickException(f"Migration failed: {e}") from e


@main.command()
def mcp() -> None:
    """Start the MCP server (stdio transport).

    Exposes conversation sessions as MCP tools for use with
    Claude Desktop, Claude Code, or any MCP-compatible client.

    Configure in claude_desktop_config.json or .claude.json.
    """
    from mcp_server.server import main as mcp_main

    mcp_main()


@main.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8765, help="Port to bind to")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the web server to browse conversations.

    The server provides both the API and the web UI.
    Open http://localhost:8765 in your browser to view conversations.
    """
    import uvicorn

    # Terminal warning when the supervised CC image-cache watcher
    # isn't installed. The lifespan log inside backend/main.py also
    # logs this for supervised-job tails, but the user running
    # `claude-explorer serve` directly in their shell sees uvicorn
    # output, not the structured logs — echo to stderr so a
    # human-readable hint surfaces above the request log.
    # See PLANS/2026.05.26-watcher-install-detection.md.
    try:
        from backend.watcher_status import is_watcher_installed
        if not is_watcher_installed():
            click.echo(
                "\nWARNING: CC image-cache watcher not installed.\n"
                "  Run 'uv run claude-explorer install-watcher' to prevent\n"
                "  permanent image-cache data loss during backend downtime.\n",
                err=True,
            )
    except Exception:  # noqa: BLE001
        # Detection is best-effort; never fail `serve` over the hint.
        pass

    click.echo(f"Starting server on http://{host}:{port}")
    click.echo("Press Ctrl+C to stop.")

    try:
        uvicorn.run(
            "backend.main:app",
            host=host,
            port=port,
            reload=reload,
            # 2026-05-22: disable uvicorn's default access log because
            # backend.main's request-timing middleware emits one richer
            # line per response (method + path + status + elapsed). The
            # default line lacks elapsed and would just double-print.
            access_log=False,
        )
    except OSError as e:
        if e.errno == 48 or "address already in use" in str(e).lower():
            click.echo(
                f"\nError: port {port} is already in use.\n"
                f"Another process is bound to {host}:{port}. "
                f"Either stop it, or pick a different port with --port <N>.",
                err=True,
            )
            raise SystemExit(1) from None
        raise


@main.command("reindex-search")
@click.option(
    "--full/--drift",
    default=True,
    help="--full rebuilds from scratch (DROP+rebuild). --drift only re-indexes files whose mtime changed.",
)
def reindex_search(full: bool) -> None:
    """Manually rebuild the SQLite FTS5 search index.

    NOTE: this runs automatically in the background every time
    ``claude-explorer serve`` starts, and the watcher keeps it in sync.
    You should rarely need to invoke this CLI manually — it's a one-shot
    override for cases like:

      * the index file got corrupted (delete it and re-run);
      * you want to verify a fresh build matches your data;
      * you bumped the schema version and want to force a rebuild
        without restarting the server.

    Idempotent: re-runs are cheap because the upsert is a no-op for
    unchanged files (mtime check).
    """
    from backend.search_index import (
        build_full_index,
        get_search_index,
        update_drifted_files,
    )
    from backend.store import ConversationStore

    idx = get_search_index()
    if idx is None:
        raise click.ClickException(
            "FTS5 not available in this sqlite3 build. Search will use "
            "linear-scan fallback. Check your Python install: "
            "`python -c \"import sqlite3; "
            "sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE x USING fts5(c)')\"`"
        )

    store = ConversationStore()
    if full:
        click.echo("Wiping index and rebuilding from scratch...")
        idx.clear_all()

        def _progress(i: int, total: int) -> None:
            if i % 50 == 0 or i == total:
                click.echo(f"  [{i}/{total}] conversations indexed")

        files, msgs = build_full_index(store, index=idx, on_progress=_progress)
        click.echo("")
        click.echo(f"Done. Indexed {files} files / {msgs} messages.")
    else:
        click.echo("Drift pass: re-indexing only files whose mtime changed...")
        updated = update_drifted_files(store, index=idx)
        click.echo(f"Done. Re-indexed {updated} file(s).")


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output")
def doctor(as_json: bool) -> None:
    """Diagnose install + environment health (read-only).

    Reports pass/warn/fail per check with a fix hint. Exits non-zero if
    any check fails. Fixing stays in dedicated commands (install-watcher,
    reindex-search, mcp).
    """
    from backend.doctor import ALL_CHECKS, has_failure, render_text, run_checks, to_json

    results = run_checks(ALL_CHECKS)
    if as_json:
        click.echo(json.dumps(to_json(results), indent=2))
    else:
        click.echo(render_text(results))
    sys.exit(1 if has_failure(results) else 0)


_PLACEHOLDER_TEXT = "This block is not supported on your current device yet."


@main.command("rehydrate")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path.home() / ".claude-explorer" / "conversations",
    help="Where conversations live.",
)
@click.option(
    "--credentials",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path.home() / ".claude-explorer" / "credentials.json",
    help="Path to credentials.json.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap on number of conversations to attempt (default: all).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List candidates without re-fetching.",
)
def rehydrate(
    data_dir: Path,
    credentials: Path,
    limit: int | None,
    dry_run: bool,
) -> None:
    """Re-fetch on-disk Desktop conversations whose tool_use / tool_result
    blocks are stored as the legacy "This block is not supported on your
    current device yet." placeholder string.

    Background: claude.ai's chat_conversations API only returns
    structured tool blocks when ``?render_all_tools=true`` is set. Our
    fetcher has used that flag since 2026-03-09 (commit c94ce6f), but
    conversations fetched BEFORE that date are stuck with the legacy
    placeholder strings. This command finds them and re-fetches with the
    correct flag.

    claude.ai aggressively garbage-collects old Desktop conversations,
    so many candidates will return 404 — those are logged and skipped
    (not retried; the data is upstream-gone).

    Idempotent. Re-runs are cheap because already-rehydrated
    conversations no longer have the placeholder + empty content[]
    pattern that flags them as candidates.
    """
    import json
    import time
    from collections import defaultdict

    from fetcher.bulk_fetch import ClaudeFetcher
    from fetcher.credentials import load_credentials

    click.echo(f"Scanning {data_dir} for placeholder-affected conversations...")

    # Discover the on-disk layout (by-org/<org>/<uuid>.json or flat <uuid>.json).
    by_org_root = data_dir / "by-org"
    if by_org_root.exists():
        candidate_paths = sorted(by_org_root.glob("*/*.json"))
    else:
        candidate_paths = sorted(data_dir.glob("*.json"))

    # Identify candidates: file contains the placeholder string AND no
    # message has populated content[] (meaning the structured blocks are
    # entirely missing, not just nested-as-verbatim-file-content).
    candidates: list[dict] = []
    for path in candidate_paths:
        try:
            raw = path.read_text()
        except OSError:
            continue
        if _PLACEHOLDER_TEXT not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        has_structured_content = any(
            (msg.get("content") or [])
            for msg in (data.get("chat_messages") or [])
        )
        if has_structured_content:
            continue
        org_id = data.get("organization_id")
        if not org_id:
            org_id = path.parent.name if by_org_root.exists() else None
        if not org_id:
            click.echo(
                f"  ⚠ {path.name}: cannot determine organization_id; skipping",
                err=True,
            )
            continue
        candidates.append({
            "path": path,
            "uuid": data.get("uuid", path.stem),
            "org_id": org_id,
            "name": data.get("name", "")[:60],
        })

    if limit is not None:
        candidates = candidates[:limit]

    click.echo(f"Found {len(candidates)} candidate conversation(s).")
    if not candidates:
        return
    if dry_run:
        for c in candidates:
            click.echo(
                f"  {c['uuid']} (org {c['org_id'][:8]}) {c['name']!r}"
            )
        return

    # Group by org so we instantiate one ClaudeFetcher per org.
    by_org: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        by_org[c["org_id"]].append(c)

    creds = load_credentials(credentials)

    rescued = 0
    upstream_gone = 0
    errors = 0

    for org_id, org_candidates in by_org.items():
        # Build the orgs list for ClaudeFetcher (it requires its own
        # primary_org_id to be one of the entries).
        orgs_for_fetcher = [
            {"uuid": o.get("uuid"), "name": o.get("name")}
            for o in (creds.get("orgs") or [])
        ]
        if org_id not in {o["uuid"] for o in orgs_for_fetcher}:
            # Fallback: synthesize a one-entry list. Matches the on-disk
            # convention; the API only cares about the uuid in the URL.
            orgs_for_fetcher = [{"uuid": org_id, "name": org_id}]

        fetcher = ClaudeFetcher(
            session_key=creds["session_key"],
            orgs=orgs_for_fetcher,
            primary_org_id=org_id,
            output_dir=data_dir,
            cf_bm=creds.get("cf_bm"),
            cf_clearance=creds.get("cf_clearance"),
            verbose=False,
        )

        click.echo(
            f"Re-fetching {len(org_candidates)} candidate(s) for org "
            f"{org_id[:8]}..."
        )
        for c in org_candidates:
            try:
                full = fetcher.fetch_conversation(c["uuid"])
            except Exception as e:  # noqa: BLE001
                errors += 1
                click.echo(
                    f"  ⚠ {c['uuid']} {c['name']!r}: {e}", err=True
                )
                time.sleep(0.3)
                continue
            if full is None:
                upstream_gone += 1
                click.echo(
                    f"  ❌ {c['uuid']} {c['name']!r}: 404 (upstream-gone)"
                )
            else:
                # save_conversation downloads file attachments + injects
                # organization metadata + atomic-writes to by-org/<org>/.
                fetcher.save_conversation(full)
                rescued += 1
                click.echo(
                    f"  ✅ {c['uuid']} {c['name']!r}: rehydrated"
                )
            time.sleep(0.3)

    click.echo("")
    click.echo("Done.")
    click.echo(f"  rescued:        {rescued}")
    click.echo(f"  upstream-gone:  {upstream_gone}")
    click.echo(f"  errors:         {errors}")


@main.command("warm-cc-cache")
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Cap on number of CC sessions to walk (default: all).",
)
def warm_cc_cache(limit: int | None) -> None:
    """Walk every Claude Code session and copy referenced image-cache
    files into ~/.claude-explorer/cc-images/.

    NOTE: this runs automatically in the background every time
    ``claude-explorer serve`` starts. You should rarely need to invoke
    this CLI manually — it's a one-shot override for cases like:
      * you have a long-running launchd-backed watcher but want to
        force a re-walk right now;
      * you fixed a broken JSONL that previously errored out;
      * you're running the CLI on a machine that doesn't run the
        backend (rare).

    Idempotent: re-runs are cheap because copy_marker_image_to_cache
    skips files already in cache.
    """
    from backend.cc_image_cache import warm_all_sessions

    def _print_progress(state: dict) -> None:
        i = state["sessions_walked"]
        total = state["total_sessions"]
        click.echo(
            f"  [{i}/{total}] sessions with cached markers: "
            f"{state['sessions_with_markers']}; files cached: "
            f"{state['files_cached']}"
        )

    state = warm_all_sessions(limit=limit, progress=_print_progress)

    click.echo("")
    click.echo("Done.")
    click.echo(f"  sessions walked:         {state['sessions_walked']}")
    click.echo(f"  sessions with markers:   {state['sessions_with_markers']}")
    click.echo(f"  files cached (incl. dupes): {state['files_cached']}")
    if state["sessions_failed"]:
        click.echo(f"  sessions failed to read: {state['sessions_failed']}", err=True)


# Watcher install/uninstall machinery lives in cli.watcher (promoted
# alongside cli.main on 2026-05-21 per council A1-CLI-LAYER). The
# re-imports here let the install_watcher Click command body dispatch
# on sys.platform and call _install_macos / _install_linux /
# _install_windows by bare name. Test files import
# _build_launchd_plist and _LAUNCHD_LABEL directly from cli.watcher
# now (migrated in the same A1-CLI-LAYER commit).
from cli.watcher import (  # noqa: F401  (re-exported)
    _LAUNCHD_LABEL,
    _build_launchd_plist,
    _install_linux,
    _install_macos,
    _install_windows,
    _uninstall_linux,
    _uninstall_macos,
    _uninstall_windows,
)


def _do_watcher(python_bin: str | None, interval: float, uninstall: bool) -> "InstallResult":
    """Run the per-OS watcher install/uninstall dispatch, returning an
    InstallResult instead of raising (so `install all` can aggregate)."""
    from backend.mcp_config_install import InstallResult
    import sys as _sys

    try:
        if uninstall:
            if _sys.platform == "darwin":
                _uninstall_macos()
            elif _sys.platform.startswith("linux"):
                _uninstall_linux()
            elif _sys.platform == "win32":
                _uninstall_windows()
            else:
                raise click.ClickException(f"unsupported platform {_sys.platform!r}")
            return InstallResult("watcher", True, True, "watcher uninstalled")

        bin_ = python_bin or _sys.executable
        if _sys.platform == "darwin":
            _install_macos(bin_, interval)
        elif _sys.platform.startswith("linux"):
            _install_linux(bin_, interval)
        elif _sys.platform == "win32":
            _install_windows(bin_, interval)
        else:
            raise click.ClickException(
                f"unsupported platform {_sys.platform!r}. Supported: darwin, linux, win32."
            )
        return InstallResult("watcher", True, True, "watcher installed")
    except Exception as exc:  # noqa: BLE001 - aggregate, never crash the group
        return InstallResult("watcher", False, False, f"watcher failed: {exc}")


@main.group("install")
def install() -> None:
    """Install integrations: the CC watcher and MCP client registration."""


def _summarize_install(results: list) -> int:
    """Print an [ok]/[FAIL] line per InstallResult; return exit code (1 if any failed).

    ASCII markers (not unicode check/cross) to avoid Windows cp1252 console
    encoding errors — same convention as the `doctor` command.
    """
    failed = 0
    for r in results:
        mark = "[ok]" if r.ok else "[FAIL]"
        click.echo(f"  {mark} {r.target}: {r.detail}")
        if not r.ok:
            failed += 1
    return 1 if failed else 0


@install.command("mcp")
@click.option("--client", type=click.Choice(["all", "code", "desktop"]),
              default="all", help="Which client(s) to register with (default: all).")
@click.option("--scope", type=click.Choice(["user", "project"]),
              default="user", help="Claude Code scope (code client only; default: user).")
@click.option("--uninstall", is_flag=True,
              help="Remove the registration instead of installing.")
def install_mcp(client: str, scope: str, uninstall: bool) -> None:
    """Register (or remove) the `claude-explorer mcp` server with Claude
    Code and/or Claude Desktop."""
    import sys as _sys
    from backend.mcp_config_install import (
        install_mcp_code, install_mcp_desktop,
        uninstall_mcp_code, uninstall_mcp_desktop,
    )

    results = []
    if client in ("all", "code"):
        results.append(
            uninstall_mcp_code(scope) if uninstall else install_mcp_code(scope)
        )
    if client in ("all", "desktop"):
        results.append(
            uninstall_mcp_desktop() if uninstall else install_mcp_desktop()
        )
    _sys.exit(_summarize_install(results))


@install.command("all")
@click.option("--uninstall", is_flag=True,
              help="Remove everything instead of installing.")
def install_all(uninstall: bool) -> None:
    """Install (or uninstall) everything: the CC watcher + MCP
    registration for Claude Code and Claude Desktop (defaults only)."""
    import sys as _sys
    from backend.mcp_config_install import (
        install_mcp_code, install_mcp_desktop,
        uninstall_mcp_code, uninstall_mcp_desktop,
    )

    results = [_do_watcher(None, 600.0, uninstall)]
    if uninstall:
        results.append(uninstall_mcp_code("user"))
        results.append(uninstall_mcp_desktop())
    else:
        results.append(install_mcp_code("user"))
        results.append(install_mcp_desktop())
    _sys.exit(_summarize_install(results))


@install.command("watcher")
@click.option(
    "--python",
    "python_bin",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Python interpreter to use (default: this venv's python).",
)
@click.option(
    "--interval",
    type=float,
    default=600.0,
    help="Backstop poll interval in seconds (default: 600 = 10min). The "
    "watcher's primary path is event-driven (FSEvents on macOS, inotify "
    "on Linux, ReadDirectoryChangesW on Windows) with sub-second "
    "latency; the backstop only catches the rare event the OS drops or "
    "coalesces. Smaller values do not improve normal-case latency.",
)
@click.option(
    "--uninstall",
    is_flag=True,
    help="Remove the platform-specific watcher unit instead of installing.",
)
def install_watcher_cmd(python_bin: str | None, interval: float, uninstall: bool) -> None:
    """Install (or uninstall) a background job that runs the CC
    image-cache watcher continuously, independent of
    ``claude-explorer serve``.

    Cross-platform: dispatches to the OS-native supervisor.

      * macOS  → launchd user agent at
                 ``~/Library/LaunchAgents/com.claude-explorer.cc-watcher.plist``
      * Linux  → systemd user unit at
                 ``~/.config/systemd/user/claude-explorer-cc-watcher.service``
                 (run ``loginctl enable-linger $USER`` to keep it running
                 across logout — the install command prints a reminder)
      * Windows → Task Scheduler task ``ClaudeExplorerCCWatcher`` triggered
                  on logon, executing a launcher at
                  ``%USERPROFILE%\\.claude-explorer\\cc-watcher.py`` via
                  ``pythonw.exe`` (no console window).

    Why install this: without it the watcher only runs while the dev
    server is up. Quitting the server (or never starting it) leaves
    Claude Code free to rotate images off disk before any reader has
    cached them — permanent data loss. The supervised job runs at
    login and restarts on crash.

    Logs:
      * macOS:   ``~/Library/Logs/claude-explorer-cc-watcher.{out,err}``
      * Linux:   ``journalctl --user -u claude-explorer-cc-watcher.service``
      * Windows: stdout suppressed (pythonw.exe). For debugging, run
                 the launcher script manually in a console.
    """
    import sys as _sys
    r = _do_watcher(python_bin, interval, uninstall)
    click.echo(r.detail)
    _sys.exit(0 if r.ok else 1)


@main.command("install-watcher", hidden=True)
@click.option(
    "--python",
    "python_bin",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
)
@click.option("--interval", type=float, default=600.0)
@click.option("--uninstall", is_flag=True)
def install_watcher(python_bin: str | None, interval: float, uninstall: bool) -> None:
    """Deprecated alias for `claude-explorer install watcher`."""
    import sys as _sys
    click.echo(
        "Note: `install-watcher` is deprecated; use `claude-explorer install watcher`.",
        err=True,
    )
    r = _do_watcher(python_bin, interval, uninstall)
    click.echo(r.detail)
    _sys.exit(0 if r.ok else 1)


if __name__ == "__main__":
    main()
