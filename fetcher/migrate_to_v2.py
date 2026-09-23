"""One-shot migration from flat-layout to per-org subdirectory layout.

Pre-cowork-multi-org users have a data_dir like::

    ~/.claude-explorer/conversations/
    ├── _index.json
    ├── 02971706-ff28-...json
    ├── 0a5e919f-6d03-...json
    └── ...

After migration::

    ~/.claude-explorer/conversations/
    ├── _index.json                     (untouched)
    ├── by-org/
    │   ├── .migrated_v2                (sentinel — migration is done)
    │   ├── .migration_log.json         (audit trail)
    │   ├── ae24ae66-.../
    │   │   ├── 02971706-...json        (organization_id injected)
    │   │   └── 0a5e919f-...json
    │   └── _claude_code/
    │       └── <claude code sessions>

The migration is **idempotent**: it acquires ``data_dir/.fetch.lock`` (so it
can't race with a CLI fetch), enumerates UUID-shaped top-level JSONs, and
moves each into the correct ``by-org/<bucket>/`` subdir based on a multi-signal
source classifier.

Routing rules (per ``PLANS/cowork-multi-org.md``):

* ``source == "CLAUDE_CODE"`` (or structural detection matches Claude Code)
  → ``by-org/_claude_code/<uuid>.json`` (no content mutation; tenant label
  is irrelevant for source/tenant orthogonality).
* ``source == "CLAUDE_AI"`` AND credentials' ``legacy_migration_target`` is
  set → ``by-org/<legacy_migration_target>/<uuid>.json`` with
  ``organization_id`` + ``organization_name`` injected (NEW2-P0-β).
* ``source == "CLAUDE_AI"`` AND ``legacy_migration_target`` is None →
  ``by-org/_unknown_source/<uuid>.json``, no content mutation.
* No ``source`` field at all + can't structurally identify → fallback to the
  ``CLAUDE_AI`` branch above (most legacy data is from claude.ai).

Files that are skipped (left in place):

* ``_index.json`` and any other non-UUID-named top-level file (NEW-P0-I).
* Files already under ``by-org/**``.

Per-file content-mutation guard (NEW-P0-D): if the on-disk JSON already has a
non-null ``organization_id``, the file is **only relocated**; the
``organization_name`` is never overwritten with one from creds (the file's
stored name wins).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import portalocker

from fetcher.credentials import (
    CredentialsCorruptError,
    DEFAULT_CREDENTIALS_PATH,
    LockContentionError,
    load_credentials,
)


log = logging.getLogger(__name__)


# Canonically defined in fetcher.paths (Council A5-PATHS); re-export
# preserves backward-compat for any external caller.
from fetcher.paths import DEFAULT_DATA_DIR  # noqa: E402
from fetcher.atomic import atomic_replace

MIGRATION_SENTINEL = "by-org/.migrated_v2"
MIGRATION_LOG = "by-org/.migration_log.json"
FETCH_LOCK = ".fetch.lock"

UUID_FILENAME_RE = re.compile(r"^[0-9a-f-]{36}\.json$", re.IGNORECASE)

DEFAULT_LOCK_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Source classifier
# ---------------------------------------------------------------------------


def _classify_source(data: dict) -> str:
    """Return 'CLAUDE_CODE', 'CLAUDE_AI', or 'UNKNOWN'.

    NEW-P1-E multi-signal classifier:
      * Explicit `source` field wins.
      * Structural detection mirrors backend/store.py's logic for pre-source
        exports (CLAUDE_CODE conversations have project_path / git_branch /
        cwd-style fields; CLAUDE_AI conversations have summary + chat_messages
        with sender field).
      * If no signal matches: UNKNOWN.
    """
    explicit = data.get("source")
    if explicit == "CLAUDE_CODE":
        return "CLAUDE_CODE"
    if explicit == "CLAUDE_AI":
        return "CLAUDE_AI"

    # No explicit source field. Structural detection.
    if data.get("project_path") or data.get("git_branch") or data.get("cwd"):
        return "CLAUDE_CODE"

    # Look at chat_messages for claude.ai shape (sender field is distinctive).
    chat_messages = data.get("chat_messages")
    if isinstance(chat_messages, list) and chat_messages:
        first = chat_messages[0]
        if isinstance(first, dict) and "sender" in first:
            return "CLAUDE_AI"

    # Has a `summary` field is suggestive of claude.ai but not definitive.
    if "summary" in data and "name" in data:
        return "CLAUDE_AI"

    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def migrate_to_v2(
    data_dir: Path = DEFAULT_DATA_DIR,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT,
    lock_command: str = "migrate",
) -> None:
    """Migrate flat-layout legacy data into the per-org subdir layout.

    Args:
        data_dir: Root of the conversations directory.
        credentials_path: Path to the v2 credentials file.
        on_progress: Optional callable invoked as ``on_progress(moved, total)``
            every 50 files (for SSE progress streaming) and at the end.
        timeout_seconds: Max wait for the ``.fetch.lock``. Raises
            :class:`LockContentionError` on timeout — server lifespan should
            catch this, log "migration deferred", and start the server anyway
            (NEW3-P0-B); a background task can retry later.
        lock_command: Diagnostic label written into the lock metadata so
            ``unlock-fetch`` can identify the holder. Use ``"migrate"`` for
            CLI, ``"lifespan_migrate"`` for server startup, etc.

    Raises:
        LockContentionError: ``data_dir/.fetch.lock`` is held by another
            process and could not be acquired in ``timeout_seconds``.
    """
    if not data_dir.exists():
        # Nothing to migrate. Still create the sentinel so future runs are
        # cheap no-ops.
        sentinel_path = data_dir / MIGRATION_SENTINEL
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path.touch()
        if on_progress:
            on_progress(0, 0)
        return

    sentinel_path = data_dir / MIGRATION_SENTINEL
    if sentinel_path.exists():
        # Already done.
        if on_progress:
            on_progress(0, 0)
        return

    lock_path = data_dir / FETCH_LOCK
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        lock = portalocker.Lock(
            str(lock_path),
            mode="a+",
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            timeout=timeout_seconds,
            fail_when_locked=False,
        )
        lock.acquire()
    except portalocker.exceptions.LockException as e:
        raise LockContentionError(
            f"Could not acquire {lock_path} within {timeout_seconds}s"
        ) from e

    try:
        # Write lock metadata so unlock-fetch can identify the holder.
        try:
            metadata = {
                "pid": os.getpid(),
                "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "command": lock_command,
            }
            lock_path.write_text(json.dumps(metadata, indent=2))
        except OSError:
            # Lock metadata is diagnostic-only; failing to write it must not
            # abort migration.
            pass

        _do_migrate(
            data_dir=data_dir,
            credentials_path=credentials_path,
            on_progress=on_progress,
        )
    finally:
        lock.release()


def _do_migrate(
    *,
    data_dir: Path,
    credentials_path: Path,
    on_progress: Callable[[int, int], None] | None,
) -> None:
    """The actual migration logic; called inside the lock."""
    # Load credentials. Tolerate missing/corrupt — those map to UNKNOWN
    # routing for legacy untagged Claude.ai files (no migration target known).
    legacy_target: str | None = None
    org_names: dict[str, str | None] = {}
    try:
        creds = load_credentials(credentials_path)
        legacy_target = creds.get("legacy_migration_target")
        for org in creds.get("orgs", []):
            org_names[org["uuid"]] = org.get("name")
    except (FileNotFoundError, CredentialsCorruptError) as e:
        log.warning(
            "migrate_to_v2: cannot load credentials (%s); legacy untagged"
            " files will route to _unknown_source/",
            e,
        )

    # Enumerate top-level UUID-named JSONs.
    candidates = sorted(
        p for p in data_dir.glob("*.json")
        if UUID_FILENAME_RE.match(p.name)
    )
    total = len(candidates)
    moves: list[dict] = []
    errors: list[dict] = []
    moved_count = 0

    for path in candidates:
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            errors.append({"path": str(path), "error": f"read failed: {e}"})
            log.warning("migrate_to_v2: cannot read %s: %s", path, e)
            continue

        if not isinstance(data, dict):
            errors.append({"path": str(path), "error": "root is not a dict"})
            continue

        existing_org = data.get("organization_id")
        source = _classify_source(data)

        # Decide the bucket.
        bucket: str
        if source == "CLAUDE_CODE":
            bucket = "_claude_code"
        elif existing_org:
            # File already tagged — relocate but don't re-tag (NEW-P0-D).
            bucket = existing_org
        elif source == "CLAUDE_AI":
            if legacy_target:
                bucket = legacy_target
            else:
                bucket = "_unknown_source"
        else:
            # UNKNOWN: route to quarantine, don't mutate content.
            bucket = "_unknown_source"

        target_dir = data_dir / "by-org" / bucket
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / path.name

        # Per-file content mutation guard (NEW-P0-D): only inject org
        # metadata when (a) file is going under a real org bucket
        # (not _claude_code or _unknown_source), and (b) the file does
        # not already carry organization_id.
        is_real_org = bucket not in ("_claude_code", "_unknown_source")
        mutated = False
        if is_real_org and not existing_org:
            data["organization_id"] = bucket
            data["organization_name"] = org_names.get(bucket)
            mutated = True

        try:
            if mutated:
                # Atomic write of the mutated file at the target location,
                # then unlink the source.
                tmp = target_path.with_suffix(".json.tmp")
                with open(tmp, "w") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp, target_path)
                path.unlink()
            else:
                # Pure relocation. shutil.move handles cross-fs fallback.
                shutil.move(str(path), str(target_path))
        except OSError as e:
            errors.append({"path": str(path), "error": f"move failed: {e}"})
            log.warning("migrate_to_v2: cannot move %s -> %s: %s", path, target_path, e)
            continue

        moves.append({"uuid": path.stem, "bucket": bucket, "source": source, "mutated": mutated})
        moved_count += 1
        if on_progress and (moved_count % 50 == 0):
            on_progress(moved_count, total)

    # Final progress tick.
    if on_progress:
        on_progress(moved_count, total)

    # Write migration log under by-org/.
    log_path = data_dir / MIGRATION_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_data = {
        "migrated_at": datetime.now(timezone.utc).isoformat(),
        "moves": moves,
        "errors": errors,
        "total_candidates": total,
        "total_moved": moved_count,
    }
    try:
        log_path.write_text(json.dumps(log_data, indent=2))
    except OSError as e:
        log.warning("migrate_to_v2: could not write migration log: %s", e)

    # Sentinel discipline (NEW3-P1-A adjacent): touch the sentinel only if
    # all candidates were processed without read/move errors. If errors are
    # present, leave the sentinel absent so a subsequent retry sees the
    # remaining files.
    if not errors:
        sentinel_path = data_dir / MIGRATION_SENTINEL
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path.touch()
        log.info(
            "migrate_to_v2: complete. moved=%d, total=%d, sentinel=%s",
            moved_count, total, sentinel_path,
        )
    else:
        log.warning(
            "migrate_to_v2: completed with %d errors; sentinel NOT touched. "
            "See %s for details.",
            len(errors), log_path,
        )
