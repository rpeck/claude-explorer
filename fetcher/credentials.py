"""Credentials I/O for the multi-org fetcher.

This module is the **sole** reader/writer of ``credentials.json``. No other
module in the project should touch the file directly. A grep audit (in C5)
enforces this invariant.

Schema versioning
-----------------

The on-disk shape is ``CredentialsV2``:

.. code-block:: python

    {
        "schema_version": 2,
        "session_key": str,
        "cf_bm": str | None,
        "cf_clearance": str | None,
        "captured_at": str,           # ISO8601
        "orgs": [{"uuid": str, "name": str | None,
                  "capabilities": list[str], "seen_in_response": bool}, ...],
        "primary_org_id": str,
        "legacy_migration_target": str | None,
        "org_id": str,                # legacy mirror = primary_org_id
    }

Legacy v1 files (``{session_key, org_id, cf_bm, cf_clearance, captured_at}``)
are upgraded to v2 *in memory* by :func:`load_credentials`; the on-disk file is
not rewritten until the next legitimate :func:`save_credentials` call.

Atomicity
---------

* All writes go through :func:`save_credentials`, which uses a portalocker file
  lock (``credentials.json.lock``) to serialize concurrent writers across
  processes (NEW-P0-A).
* The live ``credentials.json`` is updated atomically via ``os.replace`` —
  readers (which do **not** acquire the lock) never observe a torn file.
* The ``.bak`` file is updated via ``shutil.copyfile`` + ``os.replace`` so the
  live file never momentarily disappears (a UI ``/api/orgs`` reader landing in
  a microsecond gap would otherwise falsely report "not authenticated").
* A ``.bak.prev`` file is rotated in front of ``.bak`` for crash recovery; it
  is unlinked after each successful save so exactly one backup ever survives.

References
----------

PLANS/cowork-multi-org.md — see "Atomic credentials writes" and the C1 row of
the implementation sequence.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal, TypedDict

import portalocker

log = logging.getLogger(__name__)


# Canonically defined in fetcher.paths; re-exported so backend imports
# (`from fetcher.credentials import DEFAULT_CREDENTIALS_PATH`) and
# tests patching `fetcher.credentials.DEFAULT_CREDENTIALS_PATH` continue
# to work. See backend/tests/conftest.py for the multi-site patch fixture.
from fetcher.paths import DEFAULT_CREDENTIALS_PATH  # noqa: E402
from fetcher.atomic import atomic_replace

# Public API surface. Kept explicit so that Pyright/Mypy treat the
# re-exported ``DEFAULT_CREDENTIALS_PATH`` as intentional public API,
# and so accidental ``from fetcher.credentials import *`` consumers
# (none today) cannot reach internal helpers (`_unlocked_save`,
# `_acquire_lock`, `_validate`, `_upgrade_v1_in_memory`).
__all__ = [
    # Re-exported constants
    "DEFAULT_CREDENTIALS_PATH",
    "LOCK_TIMEOUT_SECONDS",
    # Types
    "CredentialsV2",
    "OrgRef",
    # Exceptions
    "CredentialsCorruptError",
    "LockContentionError",
    # Pure functions
    "resolve_primary_org_id",
    # Reads
    "load_credentials",
    # Writes (all serialize on the same portalocker file lock)
    "save_credentials",
    "merge_orgs_and_save",
    "update_primary_org_and_save",
    "wipe_credentials",
]

#: Default seconds to wait for the file lock before raising.
LOCK_TIMEOUT_SECONDS = 10.0


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class OrgRef(TypedDict):
    """One organization the fetcher has seen.

    ``seen_in_response`` is True only when the org appeared in a real
    ``/api/organizations`` response; False when synthesized from a URL path
    (mitm fallback) or from a v1 ``org_id`` upgrade.
    """

    uuid: str
    name: str | None
    capabilities: list[str]
    seen_in_response: bool


class CredentialsV2(TypedDict):
    """The canonical on-disk credentials shape (schema_version=2)."""

    schema_version: Literal[2]
    session_key: str
    cf_bm: str | None
    cf_clearance: str | None
    captured_at: str
    orgs: list[OrgRef]
    primary_org_id: str
    legacy_migration_target: str | None
    # Legacy mirror of primary_org_id. Retained one minor version for any
    # external readers that still parse the v1 shape; remove in the version
    # after.
    org_id: str


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LockContentionError(Exception):
    """Raised when ``portalocker.Lock`` times out waiting on the creds lock."""


class CredentialsCorruptError(Exception):
    """Raised when credentials.json fails to parse or fails validation."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(creds: object) -> None:
    """Strict shape check for a CredentialsV2 dict.

    Runs *before* any disk I/O so a malformed payload never produces a partial
    write or rotates ``.bak``.

    Raises :class:`CredentialsCorruptError` on any violation.
    """

    if not isinstance(creds, dict):
        raise CredentialsCorruptError(f"creds must be a dict, got {type(creds).__name__}")

    if creds.get("schema_version") != 2:
        raise CredentialsCorruptError(
            f"schema_version must be 2, got {creds.get('schema_version')!r}"
        )

    session_key = creds.get("session_key")
    if not isinstance(session_key, str) or not session_key:
        raise CredentialsCorruptError("session_key must be a non-empty str")

    orgs = creds.get("orgs")
    if not isinstance(orgs, list) or len(orgs) == 0:
        raise CredentialsCorruptError("orgs must be a non-empty list")

    for i, org in enumerate(orgs):
        if not isinstance(org, dict):
            raise CredentialsCorruptError(f"orgs[{i}] must be a dict")
        uuid = org.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise CredentialsCorruptError(f"orgs[{i}].uuid must be a non-empty str")

    primary = creds.get("primary_org_id")
    if not isinstance(primary, str) or not primary:
        raise CredentialsCorruptError("primary_org_id must be a non-empty str")

    org_uuids = {o["uuid"] for o in orgs}
    if primary not in org_uuids:
        raise CredentialsCorruptError(
            f"primary_org_id {primary!r} not in orgs ({sorted(org_uuids)})"
        )


# ---------------------------------------------------------------------------
# Primary-org resolution (canonical)
# ---------------------------------------------------------------------------


def resolve_primary_org_id(
    orgs: list[OrgRef],
    prior_primary: str | None = None,
) -> str:
    """Deterministic primary-org selection (cowork-multi-org "Primary org selection").

    Resolution order (from spec):

      1. ``prior_primary`` if it still refers to an org in ``orgs``.
      2. The first org with ``"chat"`` in capabilities, lex-sorted by uuid.
      3. Lex-sort by uuid (deterministic; never index-based).

    Canonical home: ``fetcher.credentials``. All three capture/fetch paths
    delegate here so the algorithm cannot drift (council D1, 2026-05-21):

      * ``fetcher.playwright_capture._build_credentials``
      * ``fetcher.mitmproxy_addon.ClaudeCredentialCapture._maybe_persist``
      * ``fetcher.bulk_fetch.ClaudeFetcher._pick_new_primary``

    Step 3 of the spec ("most conversations on disk") is deferred; the only
    consumer of primary selection today is the single-org fetch path which
    uses whatever uuid we pick.

    Raises:
        ValueError: when ``orgs`` is empty. ``prior_primary`` alone cannot
            synthesize a valid primary — ``orgs`` is the source of truth for
            which uuids are real.
    """
    if not orgs:
        raise ValueError("orgs must be non-empty for primary resolution")

    org_uuids = {o["uuid"] for o in orgs}
    if prior_primary and prior_primary in org_uuids:
        return prior_primary

    chat_capable = [o["uuid"] for o in orgs if "chat" in (o.get("capabilities") or [])]
    if chat_capable:
        return sorted(chat_capable)[0]

    return sorted(o["uuid"] for o in orgs)[0]


# ---------------------------------------------------------------------------
# v1 -> v2 in-memory upgrade
# ---------------------------------------------------------------------------


def _upgrade_v1_in_memory(raw: dict) -> CredentialsV2:
    """Synthesize a CredentialsV2 from a v1 dict.

    Crucially, ``legacy_migration_target = old["org_id"]`` so the migration
    script (lifespan or CLI) routes pre-multi-org untagged JSONs to the
    original v1 org regardless of any later heuristic re-pick of
    ``primary_org_id`` (NEW3-P0-C, NEW2-P0-β).
    """

    org_id = raw.get("org_id")
    if not isinstance(org_id, str) or not org_id:
        raise CredentialsCorruptError(
            "v1 credentials missing org_id; cannot upgrade to v2"
        )

    org_ref: OrgRef = {
        "uuid": org_id,
        "name": None,
        "capabilities": [],
        "seen_in_response": False,
    }
    upgraded: CredentialsV2 = {
        "schema_version": 2,
        "session_key": raw.get("session_key", "") or "",
        "cf_bm": raw.get("cf_bm"),
        "cf_clearance": raw.get("cf_clearance"),
        "captured_at": raw.get("captured_at", ""),
        "orgs": [org_ref],
        "primary_org_id": org_id,
        "legacy_migration_target": org_id,
        "org_id": org_id,
    }
    return upgraded


# ---------------------------------------------------------------------------
# Public reads
# ---------------------------------------------------------------------------


def load_credentials(path: Path = DEFAULT_CREDENTIALS_PATH) -> CredentialsV2:
    """Load credentials, upgrading v1 in memory if needed.

    Does **not** acquire the file lock. Reader-side atomicity is provided by
    ``os.replace`` in :func:`save_credentials` — readers either see the old
    full file or the new full file, never a torn one.

    Raises:
        FileNotFoundError: when ``path`` does not exist.
        CredentialsCorruptError: when JSON parse or schema validation fails.
    """

    if not path.exists():
        raise FileNotFoundError(f"credentials file not found: {path}")

    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise CredentialsCorruptError(f"failed to parse {path}: {e}") from e

    if not isinstance(raw, dict):
        raise CredentialsCorruptError(
            f"{path} root must be a JSON object, got {type(raw).__name__}"
        )

    if raw.get("schema_version") == 2:
        # Already v2 — but validate before returning so a hand-edited corrupt
        # v2 file fails at load time, not three layers deep in a fetch loop.
        _validate(raw)
        return raw  # type: ignore[return-value]

    # v1 (no schema_version, or schema_version==1) — upgrade in memory.
    upgraded = _upgrade_v1_in_memory(raw)
    _validate(upgraded)
    return upgraded


# ---------------------------------------------------------------------------
# Internal save (no lock)
# ---------------------------------------------------------------------------


def harden_path_permissions(path: Path, *, directory: bool = False) -> None:
    """Restrict a file or directory to the current user.

    POSIX honours mode bits, so ``chmod`` is enough there.

    Windows ignores mode bits completely. NTFS uses access control lists, so
    a ``chmod`` leaves a credentials file readable by every account the
    inherited ACL allows. On Windows this drops inheritance and grants the
    current user alone.

    Both paths are best effort. A failure logs a warning and the caller
    continues, because a usable credential beats a hard failure in the
    middle of a capture.
    """
    mode = 0o700 if directory else 0o600
    try:
        os.chmod(path, mode)
    except OSError as e:
        log.warning("chmod %s on %s failed: %s", oct(mode), path, e)

    if sys.platform != "win32":
        return

    user = os.environ.get("USERNAME")
    if not user:
        log.warning("cannot restrict %s: USERNAME is not set", path)
        return
    try:
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            log.warning(
                "icacls on %s failed: %s", path, (result.stdout or result.stderr).strip()
            )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("icacls on %s failed: %s", path, e)


def _ensure_parent(path: Path) -> None:
    """Create parent dir with 0o700 perms (best-effort; Windows may ignore)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    harden_path_permissions(path.parent, directory=True)


def _unlocked_save(creds: CredentialsV2, path: Path) -> None:
    """Atomic save WITHOUT acquiring the lock.

    Intended for use by :func:`save_credentials` and
    :func:`merge_orgs_and_save`, which acquire the lock themselves.

    Sequence (see "Atomic credentials writes" in PLANS/cowork-multi-org.md;
    Step 3 diverges from spec — see module docstring "Atomicity" section):

    1. Rotate any existing ``.bak`` -> ``.bak.prev`` (preserve V0).
    2. Write the new payload to ``.tmp`` + fsync; ``chmod 0o600`` best-effort.
    3. ``shutil.copyfile`` of live -> ``.bak.tmp``, then atomic rename to
       ``.bak``. The live file remains intact throughout — concurrent unlocked
       readers never see a missing file.
    4. ``os.replace(tmp, path)`` to atomically install the new live file.
    5. Unlink ``.bak.prev`` (recovery no longer needed; exactly one ``.bak``
       survives).
    """

    _validate(creds)
    _ensure_parent(path)

    tmp = path.with_suffix(".json.tmp")
    bak = path.with_suffix(".json.bak")
    bak_tmp = path.with_suffix(".json.bak.tmp")
    prev_bak = path.with_suffix(".json.bak.prev")

    # Step 1: rotate any existing .bak -> .bak.prev so we can restore on crash.
    if bak.exists():
        atomic_replace(bak, prev_bak)

    # Step 2: write new payload to .tmp + fsync, restrict perms.
    with open(tmp, "w") as f:
        json.dump(creds, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    harden_path_permissions(tmp)

    try:
        # Step 3: refresh .bak from the current live file *without* removing it.
        # shutil.copyfile then atomic rename — readers never see live missing.
        if path.exists():
            shutil.copyfile(path, bak_tmp)
            harden_path_permissions(bak_tmp)
            atomic_replace(bak_tmp, bak)

        # Step 4: install new live file atomically.
        atomic_replace(tmp, path)
    finally:
        # Defensive: clean up any bak_tmp residue from a partial Step 3.
        if bak_tmp.exists():
            try:
                bak_tmp.unlink()
            except OSError:
                pass

    # Step 5: drop the now-redundant .bak.prev. .bak holds V_prev; that's our
    # one and only backup.
    if prev_bak.exists():
        try:
            prev_bak.unlink()
        except OSError as e:
            log.warning("unlink %s failed: %s", prev_bak, e)


# ---------------------------------------------------------------------------
# Public writes
# ---------------------------------------------------------------------------


def _acquire_lock(path: Path, timeout: float):  # type: ignore[no-untyped-def]
    """Open a portalocker exclusive file lock on ``path.with_suffix('.json.lock')``.

    Returns the context manager. Raises :class:`LockContentionError` on
    timeout. Caller is responsible for using ``with`` to release.
    """
    lock_path = path.with_suffix(".json.lock")
    _ensure_parent(path)
    try:
        # LOCK_EX | LOCK_NB makes portalocker honor the timeout (which it
        # otherwise warns is a no-op in blocking mode); fail_when_locked=False
        # plus the timeout argument means it will retry until timeout elapses.
        return portalocker.Lock(
            str(lock_path),
            mode="a+",
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            timeout=timeout,
            fail_when_locked=False,
        )
    except portalocker.exceptions.LockException as e:  # pragma: no cover
        raise LockContentionError(
            f"could not acquire {lock_path} within {timeout}s"
        ) from e


def save_credentials(
    creds: CredentialsV2,
    path: Path = DEFAULT_CREDENTIALS_PATH,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> None:
    """Atomically save ``creds`` to ``path`` under a process-wide file lock.

    Validates ``creds`` *before* touching disk. Raises
    :class:`CredentialsCorruptError` for invalid payloads;
    :class:`LockContentionError` if the lock cannot be acquired within
    ``timeout`` seconds.
    """

    _validate(creds)  # Fail fast before lock acquisition + disk I/O.
    try:
        with _acquire_lock(path, timeout):
            _unlocked_save(creds, path)
    except portalocker.exceptions.LockException as e:
        raise LockContentionError(
            f"could not acquire credentials lock within {timeout}s"
        ) from e


def merge_orgs_and_save(
    new_orgs: list[OrgRef],
    path: Path = DEFAULT_CREDENTIALS_PATH,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> CredentialsV2:
    """Union ``new_orgs`` into the existing credentials' org list and save.

    Read-merge-write is performed atomically inside the file lock so two
    concurrent callers (e.g. mitmproxy_addon + playwright_capture) cannot
    silently truncate each other's contributions (NEW-P0-A).

    Merge rule: orgs are keyed by ``uuid``. When two refs share a UUID, the
    one with ``seen_in_response=True`` wins so URL-only fallbacks don't
    overwrite real names from ``/api/organizations``. If both have the same
    ``seen_in_response`` value, the incoming ``new_orgs`` entry wins (lets
    capabilities and names refresh on a re-fetch — see NEW-P2-M).

    Raises:
        FileNotFoundError: if ``path`` does not exist. mitmproxy is an
            enricher, not a bootstrapper — it cannot synthesize a session_key.
        CredentialsCorruptError: if the on-disk creds fail validation.
        LockContentionError: if the lock cannot be acquired in ``timeout``s.
    """

    if not path.exists():
        raise FileNotFoundError(
            f"credentials file not found: {path} (run `claude-explorer capture` first)"
        )

    try:
        with _acquire_lock(path, timeout):
            current = load_credentials(path)

            # Merge by uuid.
            by_uuid: dict[str, OrgRef] = {o["uuid"]: o for o in current["orgs"]}
            for incoming in new_orgs:
                uuid = incoming["uuid"]
                existing = by_uuid.get(uuid)
                if existing is None:
                    by_uuid[uuid] = dict(incoming)  # type: ignore[assignment]
                    continue
                # Conflict resolution: prefer seen_in_response=True.
                if existing.get("seen_in_response") and not incoming.get("seen_in_response"):
                    # Keep existing — don't downgrade a real record with a URL-only one.
                    continue
                if incoming.get("seen_in_response") and not existing.get("seen_in_response"):
                    by_uuid[uuid] = dict(incoming)  # type: ignore[assignment]
                    continue
                # Same seen_in_response on both sides — incoming wins (refresh).
                by_uuid[uuid] = dict(incoming)  # type: ignore[assignment]

            current["orgs"] = list(by_uuid.values())
            _unlocked_save(current, path)
            return current
    except portalocker.exceptions.LockException as e:
        raise LockContentionError(
            f"could not acquire credentials lock within {timeout}s"
        ) from e


def update_primary_org_and_save(
    new_primary: str,
    path: Path = DEFAULT_CREDENTIALS_PATH,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> CredentialsV2:
    """Atomically update ``primary_org_id`` (and the legacy ``org_id`` mirror).

    Used by the multi-org fetcher when an auto-demote happens (NEW-P0-B):
    callers don't carry the full ``CredentialsV2`` payload, so they cannot
    use :func:`save_credentials` directly. This helper performs the
    read-modify-write under the same portalocker lock.

    Raises:
        FileNotFoundError: when ``path`` does not exist.
        ValueError: if ``new_primary`` is not in the on-disk creds' orgs list.
    """
    if not path.exists():
        raise FileNotFoundError(f"credentials file not found: {path}")

    try:
        with _acquire_lock(path, timeout):
            current = load_credentials(path)
            org_uuids = {o["uuid"] for o in current["orgs"]}
            if new_primary not in org_uuids:
                raise ValueError(
                    f"new_primary {new_primary!r} not in current orgs ({sorted(org_uuids)})"
                )
            current["primary_org_id"] = new_primary
            current["org_id"] = new_primary
            _unlocked_save(current, path)
            return current
    except portalocker.exceptions.LockException as e:
        raise LockContentionError(
            f"could not acquire credentials lock within {timeout}s"
        ) from e


def wipe_credentials(
    path: Path = DEFAULT_CREDENTIALS_PATH,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> None:
    """Remove credentials.json and ALL associated artifacts.

    Acquires the lock so we can't wipe mid-write. After successful return:

    * ``credentials.json`` removed
    * ``credentials.json.bak`` removed
    * ``credentials.json.bak.prev`` removed
    * ``credentials.json.bak.tmp`` removed
    * ``credentials.json.tmp`` removed
    * ``credentials.json.lock`` removed (so a subsequent capture starts clean)

    Idempotent: missing artifacts are not an error.
    """

    suffixes = [
        ".json",
        ".json.bak",
        ".json.bak.prev",
        ".json.bak.tmp",
        ".json.tmp",
    ]

    try:
        # Acquire the lock first so we don't wipe in the middle of a write.
        with _acquire_lock(path, timeout):
            for suffix in suffixes:
                p = path.with_suffix(suffix)
                if p.exists():
                    try:
                        p.unlink()
                    except OSError as e:
                        log.warning("unlink %s failed: %s", p, e)
    except portalocker.exceptions.LockException as e:  # pragma: no cover
        raise LockContentionError(
            f"could not acquire credentials lock within {timeout}s"
        ) from e
    except (FileNotFoundError, LockContentionError):
        # If the lock file itself doesn't exist yet (nothing was ever saved),
        # there's nothing to wipe and nothing to lock.
        pass

    # Remove the lock file last (and outside the lock context — closing the
    # context already released it).
    lock_path = path.with_suffix(".json.lock")
    if lock_path.exists():
        try:
            lock_path.unlink()
        except OSError as e:
            log.warning("unlink %s failed: %s", lock_path, e)
