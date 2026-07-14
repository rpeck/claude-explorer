"""Scheduled-fetch run routine — invoked once per scheduler tick by the
supervised job. Incremental fetch -> reindex drift -> status write, with
an overlap lock and a re-auth notification on the ok->expired transition.
Never raises: every failure becomes a status + exit code. CLI-only; must
stay OUT of the MCPB closure."""

from __future__ import annotations

import logging
from pathlib import Path

from fetcher.http_retry import FetchAuthError
from fetcher.run_fetch import run_incremental_fetch

from .config import canonical_home_dir, get_settings
from .notify import notify
from .scheduled_fetch_status import FetchStatus, read_status, status_path, write_status

log = logging.getLogger(__name__)

_REAUTH_TITLE = "Claude Explorer"
_REAUTH_MSG = (
    "Your Claude session expired. Re-authenticate: run `claude-explorer capture` "
    "(or click Refresh in the web UI)."
)


def credentials_path() -> Path:
    return canonical_home_dir() / "credentials.json"


def _lock_path() -> Path:
    return canonical_home_dir() / "scheduled-fetch.lock"


def _acquire_lock():
    """Return a lock handle, or None if another run holds it. Uses O_CREAT|
    O_EXCL on a lockfile; the handle is the open fd.

    Known limitation: a hard-killed run (SIGKILL/OOM) leaves the lockfile
    behind, blocking future runs until manually removed. A future enhancement
    could stale-bust via PID check."""
    import os
    p = _lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        return os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None


def _release_lock(handle) -> None:
    import os
    if handle is None:
        return
    try:
        os.close(handle)
    except OSError:
        pass
    try:
        _lock_path().unlink()
    except OSError:
        pass


def _reindex_drift() -> None:
    """Best-effort search-index drift pass; isolated so its failure never
    fails the fetch."""
    try:
        from .search_index import get_search_index, update_drifted_files
        from .store import ConversationStore
        idx = get_search_index()
        if idx is not None:
            update_drifted_files(ConversationStore(), index=idx)
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduled-fetch: drift reindex failed: %s", exc)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_scheduled_fetch(*, interval_sec: int = 3600, now: str | None = None) -> int:
    ts = now or _now_iso()
    prior = read_status(status_path())

    handle = _acquire_lock()
    if handle is None:
        log.info("scheduled-fetch: previous run still in progress; skipping")
        return 0

    try:
        if not credentials_path().exists():
            _write(prior, ts, result="needs_auth", auth_expired=True,
                   interval_sec=interval_sec)
            if not prior.auth_expired:
                notify(_REAUTH_TITLE, _REAUTH_MSG)
            return 1

        settings = get_settings()
        try:
            run_incremental_fetch(
                output_dir=settings.data_dir,
                files_dir=settings.data_dir / "files",
                credentials=credentials_path(),
                session_key=None, org_id=None, incremental=True,
                download_files=True, delay=0.3, limit=None, verbose=False,
            )
        except FetchAuthError:
            _write(prior, ts, result="auth_expired", auth_expired=True,
                   interval_sec=interval_sec)
            if not prior.auth_expired:
                notify(_REAUTH_TITLE, _REAUTH_MSG)
            return 1
        except Exception as exc:  # noqa: BLE001
            _write(prior, ts, result="error", auth_expired=False,
                   interval_sec=interval_sec, error=str(exc))
            return 1

        _reindex_drift()
        write_status(FetchStatus(
            last_run_at=ts, last_success_at=ts, last_result="ok",
            auth_expired=False, fetched_count=None, error=None,
            interval_sec=interval_sec,
        ), status_path())
        return 0
    except Exception as exc:  # noqa: BLE001 - supervised job must never crash
        log.warning("scheduled-fetch: unexpected error: %s", exc)
        return 1
    finally:
        _release_lock(handle)


def _write(prior: FetchStatus, ts: str, *, result: str, auth_expired: bool,
           interval_sec: int, error: str | None = None) -> None:
    write_status(FetchStatus(
        last_run_at=ts,
        last_success_at=prior.last_success_at,   # preserve prior success time
        last_result=result, auth_expired=auth_expired,
        fetched_count=prior.fetched_count, error=error, interval_sec=interval_sec,
    ), status_path())
