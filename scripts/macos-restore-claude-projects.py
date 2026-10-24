#!/usr/bin/env python3
"""
Restore deleted ~/.claude/projects/ subdirectories from macOS Time Machine.

Why this exists
---------------
Claude Code automatically deletes session files in ~/.claude/projects/ that are
older than `cleanupPeriodDays` (default: 30). When a project subdirectory becomes
empty as a result, it is removed too. Many users have lost months of conversation
history this way without realizing the cleanup was happening.

What this script does
---------------------
1. Walks every Time Machine backup snapshot for the current user.
2. Collects the union of every ~/.claude/projects/<name>/ subdirectory ever
   seen across those backups.
3. Diffs against the user's current ~/.claude/projects/ to identify dirs
   that disappeared.
4. For each missing dir, finds the NEWEST backup that still contains it and
   copies that version to a recovery staging directory.
5. Optionally moves the recovered dirs into ~/.claude/projects/ — never
   overwriting any directory that already exists locally.

Requirements
------------
- macOS with an attached/mounted Time Machine destination.
- Terminal (or whatever shell you run this from) must have **Full Disk Access**:
    System Settings -> Privacy & Security -> Full Disk Access -> add Terminal.
  Without FDA, macOS will return "Operation not permitted" when reading TM
  snapshot directories. The script detects this and tells you what to do.
- `sudo` to read snapshot directories. Run with `sudo python3 ...`.

Usage
-----
    # Restore everything missing (default — walks all backups):
    sudo python3 scripts/macos-restore-claude-projects.py

    # Limit how far back we look:
    sudo python3 scripts/macos-restore-claude-projects.py --since 2026-01-01
    sudo python3 scripts/macos-restore-claude-projects.py --days 60

    # Preview without copying:
    sudo python3 scripts/macos-restore-claude-projects.py --dry-run

    # Recover, then auto-move into ~/.claude/projects/ (skips any that
    # already exist locally — never clobbers):
    sudo python3 scripts/macos-restore-claude-projects.py --apply

    # Restore for a different user:
    sudo python3 scripts/macos-restore-claude-projects.py --user alice
"""

from __future__ import annotations

import argparse
import os
import pwd
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

TM_ROOT_PARENT = Path("/Volumes/.timemachine")
DEFAULT_RECOVERY_SUBDIR = ".claude/projects-recovered"
BACKUP_NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})\.backup$")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Restore deleted ~/.claude/projects/ subdirs from Time Machine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    cutoff = p.add_mutually_exclusive_group()
    cutoff.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only scan backups taken on or after this date.",
    )
    cutoff.add_argument(
        "--days",
        type=int,
        metavar="N",
        help="Only scan backups from the last N days.",
    )
    p.add_argument(
        "--user",
        default=None,
        help="Username whose ~/.claude to restore (default: $SUDO_USER or current user).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be recovered, but don't copy anything.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="After copying to projects-recovered/, move missing dirs back into "
             "~/.claude/projects/ (never overwrites existing dirs).",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_user(arg: str | None) -> tuple[str, Path]:
    """Resolve target user and home dir.

    When run via sudo, $SUDO_USER points at the original (non-root) user.
    """
    if arg:
        username = arg
    elif os.environ.get("SUDO_USER"):
        username = os.environ["SUDO_USER"]
    else:
        username = pwd.getpwuid(os.getuid()).pw_name
    pw = pwd.getpwnam(username)
    return username, Path(pw.pw_dir)


def find_tm_root() -> Path | None:
    """Find the Time Machine UUID directory under /Volumes/.timemachine/."""
    if not TM_ROOT_PARENT.is_dir():
        return None
    try:
        for p in TM_ROOT_PARENT.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                return p
    except (OSError, PermissionError):
        return None
    return None


def list_backups(tm_root: Path, since: datetime | None) -> list[tuple[datetime, Path]]:
    """Return list of (timestamp, path) for each backup, newest first.

    Tries the modern APFS layout: <tm_root>/YYYY-MM-DD-HHMMSS.backup/
    """
    out: list[tuple[datetime, Path]] = []
    try:
        entries = list(tm_root.iterdir())
    except (OSError, PermissionError) as e:
        print(f"ERROR: cannot list {tm_root}: {e}", file=sys.stderr)
        print("Hint: grant Terminal Full Disk Access in System Settings.", file=sys.stderr)
        sys.exit(1)
    for entry in entries:
        m = BACKUP_NAME_RE.match(entry.name)
        if not m:
            continue
        try:
            ts = datetime(*[int(x) for x in m.groups()])
        except ValueError:
            continue
        if since and ts < since:
            continue
        out.append((ts, entry))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def find_projects_in_backup(backup_path: Path, user: str) -> Path | None:
    """Locate the user's ~/.claude/projects/ within a TM backup snapshot.

    Modern APFS Time Machine layout (as of macOS 14/15):
        <backup>/<backup>.backup/Data/Users/<user>/.claude/projects/
    Older layouts checked as fallbacks for compatibility.
    """
    nested_inner = backup_path.name  # e.g. 2026-04-29-155242.backup
    candidates = [
        backup_path / nested_inner / "Data" / "Users" / user / ".claude" / "projects",
        backup_path / "Macintosh HD - Data" / "Users" / user / ".claude" / "projects",
        backup_path / "Macintosh HD" / "Users" / user / ".claude" / "projects",
        backup_path / "Data" / "Users" / user / ".claude" / "projects",
        backup_path / "Users" / user / ".claude" / "projects",
    ]
    for c in candidates:
        try:
            if c.is_dir():
                return c
        except (OSError, PermissionError):
            continue
    # Last resort: try any *.backup subdir + Data/...
    try:
        for v in backup_path.iterdir():
            if v.name.endswith(".backup"):
                p = v / "Data" / "Users" / user / ".claude" / "projects"
                try:
                    if p.is_dir():
                        return p
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError):
        pass
    return None


def list_subdirs(path: Path) -> set[str]:
    """Return set of subdir names in path (empty set on error)."""
    try:
        return {p.name for p in path.iterdir() if p.is_dir()}
    except (OSError, PermissionError):
        return set()


def chown_recursive(path: Path, username: str) -> None:
    """Restore ownership of recovered files to the target user."""
    try:
        subprocess.run(
            ["chown", "-R", f"{username}:staff", str(path)],
            check=False, capture_output=True,
        )
    except Exception:  # noqa: BLE001, S110 — best-effort cleanup
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    # Compute cutoff
    since: datetime | None = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got: {args.since}", file=sys.stderr)
            return 2
    elif args.days:
        if args.days <= 0:
            print("ERROR: --days must be positive.", file=sys.stderr)
            return 2
        since = datetime.now() - timedelta(days=args.days)

    if os.geteuid() != 0:
        print("WARNING: not running as root.")
        print("Time Machine snapshots typically require sudo. If reads fail with")
        print("'Operation not permitted', re-run with: sudo python3 ...")
        print()

    username, home = resolve_user(args.user)
    current_projects = home / ".claude" / "projects"
    recovery_dir = home / DEFAULT_RECOVERY_SUBDIR

    print(f"Target user:        {username} (home={home})")
    print(f"Live projects dir:  {current_projects}")
    print(f"Recovery dir:       {recovery_dir}")
    if since:
        print(f"Backup cutoff:      {since.isoformat(sep=' ')} (newer-only)")
    else:
        print("Backup cutoff:      none (scanning all backups)")
    print()

    # Find TM root
    print("===> Locating Time Machine backup root...")
    tm_root = find_tm_root()
    if tm_root is None:
        print("ERROR: No Time Machine backup mounted.", file=sys.stderr)
        print("Connect/mount your TM destination and try again.", file=sys.stderr)
        return 1
    print(f"     {tm_root}")
    print()

    # List backups
    print("===> Listing backups...")
    backups = list_backups(tm_root, since)
    if not backups:
        print("ERROR: No backups found in scan window.", file=sys.stderr)
        return 1
    print(f"     {len(backups)} backups (newest: {backups[0][0]}, oldest: {backups[-1][0]})")
    print()

    # Snapshot current projects
    current = list_subdirs(current_projects)
    print(f"===> Current ~/.claude/projects/: {len(current)} subdirs")
    print()

    # Walk newest-to-oldest, recording first-seen-at for every subdir name
    print("===> Scanning backups (newest -> oldest)...")
    seen: dict[str, tuple[datetime, Path]] = {}
    inaccessible = 0
    for ts, bk in backups:
        proj = find_projects_in_backup(bk, username)
        if proj is None:
            inaccessible += 1
            continue
        try:
            for sub in proj.iterdir():
                if sub.is_dir() and sub.name not in seen:
                    seen[sub.name] = (ts, sub)
        except (OSError, PermissionError):
            inaccessible += 1
    print(f"     Unique project names ever seen: {len(seen)}")
    if inaccessible:
        print(f"     Backups with no readable projects/: {inaccessible}")
    print()

    # Identify missing
    missing = sorted(name for name in seen if name not in current)
    print(f"===> MISSING projects: {len(missing)}")
    if not missing:
        print("     (nothing to recover — all backed-up projects are still present)")
        return 0
    for name in missing:
        ts, src = seen[name]
        print(f"     {name}")
        print(f"         latest backup: {ts.isoformat(sep=' ')}")

    if args.dry_run:
        print()
        print("Dry run — not copying.")
        return 0

    # Copy recovered dirs to staging
    recovery_dir.mkdir(parents=True, exist_ok=True)
    print()
    print(f"===> Copying to {recovery_dir}/")
    copied: list[str] = []
    for name in missing:
        ts, src = seen[name]
        dst = recovery_dir / name
        if dst.exists():
            print(f"     skip (already in recovery dir): {name}")
            continue
        print(f"     copy: {name}")
        try:
            shutil.copytree(src, dst, symlinks=True)
            copied.append(name)
        except Exception as e:  # noqa: BLE001 — report the error and continue with the next item
            print(f"         ERROR: {e}", file=sys.stderr)

    chown_recursive(recovery_dir, username)
    print()
    print(f"Recovered {len(copied)} dir(s) to {recovery_dir}/")

    # Optionally apply: move EVERY dir in recovery_dir into live projects/,
    # skipping any that already exist there. We use the recovery dir as the
    # source of truth (not just `copied` from this run) so that --apply also
    # works when the staging dir was populated by a prior invocation.
    if args.apply:
        print()
        print(f"===> Applying: moving recovered dirs into {current_projects}/ (skip if exists)")
        applied = 0
        skipped = 0
        try:
            staged = sorted(p for p in recovery_dir.iterdir() if p.is_dir())
        except (OSError, PermissionError) as e:
            print(f"     ERROR reading {recovery_dir}: {e}", file=sys.stderr)
            staged = []
        for src in staged:
            name = src.name
            dst = current_projects / name
            if dst.exists():
                print(f"     skip (already exists in live projects/): {name}")
                skipped += 1
                continue
            try:
                shutil.move(str(src), str(dst))
                applied += 1
                print(f"     move: {name}")
            except Exception as e:  # noqa: BLE001 — report the error and continue with the next item
                print(f"         ERROR: {e}", file=sys.stderr)
        chown_recursive(current_projects, username)
        print()
        print(f"Applied {applied}, skipped {skipped}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
