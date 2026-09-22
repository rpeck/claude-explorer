"""Tests for utils/restore-deleted-sessions-and-images.sh covering the
Cowork domain extension (Phase 7).

The script is bash, not Python — these tests invoke it via subprocess
against a synthetic Time Machine snapshot under tmp_path. They pin:

  * dry-run plans both the CC .jsonl AND the Cowork audit.jsonl +
    sidecar (regression guard: don't break CC while extending);
  * dry-run does NOT plan Cowork outputs/ artifacts (allow-list
    filter active);
  * --apply restores both CC + Cowork files into the live tree;
  * a second --apply is a no-op (files already present, never
    overwritten).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# The script under test is macOS-only: it drives `tmutil`, reads Time
# Machine snapshots, and calls os.geteuid, which does not exist on
# Windows. On Windows the subprocess call reaches WSL instead and the
# assertions compare against WSL's error text.
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS Time Machine restore script (needs tmutil + os.geteuid)",
)


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "utils"
    / "restore-deleted-sessions-and-images.sh"
)


@pytest.fixture
def tm_setup(tmp_path: Path):
    """Build a synthetic TM-disk root + a synthetic --home root.

    Layout under tmp_path:

        tm-disk/snapshot1/Data/Users/testuser/
            .claude/projects/proj1/cc-session.jsonl
            Library/Application Support/Claude/local-agent-mode-sessions/
                dep1/org1/local_XYZ.json                  (sidecar)
                dep1/org1/local_XYZ/audit.jsonl           (messages)
                dep1/org1/local_XYZ/outputs/garbage.txt   (SKIP target)

        home/  (the --home / live destination root, initially empty)
    """
    # The bash script's is_snapshot_name() pins the directory name to
    # YYYY-MM-DD-HHMMSS or YYYY-MM-DD-HHMMSS.backup. Use the modern
    # APFS shape so the test exercises the same code path as production.
    SNAPSHOT_NAME = "2026-05-25-120000.backup"
    tm_disk = tmp_path / "tm-disk"
    snap = (
        tm_disk
        / SNAPSHOT_NAME
        / "Data"
        / "Users"
        / "testuser"
    )

    # CC project session.
    cc_dir = snap / ".claude" / "projects" / "proj1"
    cc_dir.mkdir(parents=True)
    cc_file = cc_dir / "cc-session.jsonl"
    cc_file.write_text(
        '{"type":"user","uuid":"u1","sessionId":"sess","message":{"role":"user","content":"hi"}}\n'
    )

    # Cowork sidecar + audit + an outputs file we should SKIP.
    cw_org = (
        snap
        / "Library"
        / "Application Support"
        / "Claude"
        / "local-agent-mode-sessions"
        / "dep1"
        / "org1"
    )
    cw_org.mkdir(parents=True)
    (cw_org / "local_XYZ.json").write_text('{"sessionId": "local_XYZ", "title": "x"}')
    (cw_org / "local_XYZ").mkdir()
    (cw_org / "local_XYZ" / "audit.jsonl").write_text(
        '{"type":"user","uuid":"u1","session_id":"XYZ","message":{"role":"user","content":"hi"},"_audit_timestamp":"2026-05-25T10:00:00Z"}\n'
    )
    outputs_dir = cw_org / "local_XYZ" / "outputs"
    outputs_dir.mkdir()
    (outputs_dir / "garbage.txt").write_text("noise")

    # Live home (where restore writes into).
    live_home = tmp_path / "home"
    live_home.mkdir()

    return {
        "tm_disk": tm_disk,
        "live_home": live_home,
        "cc_relpath_live": Path(".claude/projects/proj1/cc-session.jsonl"),
        "cw_sidecar_relpath_live": Path(
            "Library/Application Support/Claude/local-agent-mode-sessions/"
            "dep1/org1/local_XYZ.json"
        ),
        "cw_audit_relpath_live": Path(
            "Library/Application Support/Claude/local-agent-mode-sessions/"
            "dep1/org1/local_XYZ/audit.jsonl"
        ),
        "cw_outputs_relpath_live": Path(
            "Library/Application Support/Claude/local-agent-mode-sessions/"
            "dep1/org1/local_XYZ/outputs/garbage.txt"
        ),
    }


def _run_script(*, tm_disk: Path, live_home: Path, dry_run: bool) -> subprocess.CompletedProcess:
    """Invoke the bash script under test."""
    cmd = [
        "bash",
        str(SCRIPT),
        "--tm-disk", str(tm_disk),
        "--user", "testuser",
        "--home", str(live_home),
    ]
    if dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_dry_run_plans_cc_and_cowork_files(tm_setup):
    result = _run_script(
        tm_disk=tm_setup["tm_disk"],
        live_home=tm_setup["live_home"],
        dry_run=True,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # CC .jsonl planned.
    assert "projects/proj1/cc-session.jsonl" in out
    # Cowork sidecar + audit planned.
    assert "local_XYZ.json" in out
    assert "local_XYZ/audit.jsonl" in out


def test_dry_run_skips_cowork_outputs(tm_setup):
    result = _run_script(
        tm_disk=tm_setup["tm_disk"],
        live_home=tm_setup["live_home"],
        dry_run=True,
    )
    assert result.returncode == 0, result.stderr
    # outputs/garbage.txt is the bidirectional bait — must NOT be
    # planned for restore.
    assert "garbage.txt" not in result.stdout


def test_apply_restores_cc_and_cowork(tm_setup):
    result = _run_script(
        tm_disk=tm_setup["tm_disk"],
        live_home=tm_setup["live_home"],
        dry_run=False,
    )
    assert result.returncode == 0, result.stderr

    live_home = tm_setup["live_home"]
    # All three target files exist on the live tree.
    assert (live_home / tm_setup["cc_relpath_live"]).exists()
    assert (live_home / tm_setup["cw_sidecar_relpath_live"]).exists()
    assert (live_home / tm_setup["cw_audit_relpath_live"]).exists()
    # Outputs file does NOT.
    assert not (live_home / tm_setup["cw_outputs_relpath_live"]).exists()


def test_auto_detects_tm_disk_via_tmutil_when_not_provided(tm_setup, tmp_path):
    """V1 polish (2026-05-25, user-reported UX): running the script with
    NO `--tm-disk` arg should auto-detect via ``tmutil latestbackup``.

    Stubs ``tmutil`` on PATH so the test doesn't depend on the actual
    Time Machine state of the machine running the suite. The stub
    returns a path that points INTO our synthetic snapshot tree —
    the script should walk up to the snapshot-containing directory
    and proceed normally.
    """
    # Create a tmutil stub on a fresh PATH dir.
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "tmutil"
    # The latest "backup" is the synthetic snapshot dir under tm_setup["tm_disk"].
    # Real tmutil returns a deeper path; the script's auto-detect walks up
    # dirname-by-dirname until it finds a dir that contains snapshot-named
    # subdirs. We point at the snapshot dir itself; dirname once → tm_disk
    # (the snapshot-containing dir) and the auto-detect stops there.
    snapshot_dir = tm_setup["tm_disk"] / "2026-05-25-120000.backup"
    stub.write_text(
        '#!/bin/bash\n'
        'case "$1" in\n'
        f'  latestbackup) echo "{snapshot_dir}" ;;\n'
        '  *) echo "tmutil stub: unsupported verb $1" >&2; exit 1 ;;\n'
        'esac\n'
    )
    stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}:{env['PATH']}"

    # NO --tm-disk arg — relies on auto-detect.
    cmd = [
        "bash",
        str(SCRIPT),
        "--user", "testuser",
        "--home", str(tm_setup["live_home"]),
        "--dry-run",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    assert result.returncode == 0, (
        f"auto-detect failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    # Surfaces a clear "auto-detected" note so the user knows what path was used.
    assert "auto-detect" in combined.lower() or "tmutil" in combined.lower(), (
        f"expected a note about auto-detection; got: {combined!r}"
    )
    # Same files should be planned as the explicit-arg path.
    assert "projects/proj1/cc-session.jsonl" in result.stdout
    assert "local_XYZ.json" in result.stdout
    assert "local_XYZ/audit.jsonl" in result.stdout


def test_helpful_error_when_tmutil_returns_no_backup_and_no_arg(tmp_path):
    """If `tmutil latestbackup` returns nothing (no TM destination
    mounted / configured) AND `--tm-disk` not given, the error
    message points the user at how to find the path themselves.

    Realistic failure mode: tmutil IS available on every macOS (it's
    in /usr/bin), but on a machine with no Time Machine destination
    configured (or all destinations unmounted), `latestbackup`
    silently emits nothing. The script should detect that and surface
    an actionable error instead of barreling on with an empty TM_DISK.
    """
    # Stub `tmutil` to return empty output for `latestbackup` (mimics
    # an unmounted / unconfigured destination). The stub must beat
    # /usr/bin/tmutil on PATH for the duration of the test.
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "tmutil"
    stub.write_text(
        '#!/bin/bash\n'
        'case "$1" in\n'
        '  latestbackup) exit 0 ;;\n'        # empty output, exit 0
        '  *) echo "tmutil stub: unsupported verb $1" >&2; exit 1 ;;\n'
        'esac\n'
    )
    stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}:{env['PATH']}"

    cmd = [
        "bash",
        str(SCRIPT),
        "--user", "testuser",
        "--home", str(tmp_path / "home"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    assert result.returncode != 0, (
        f"expected non-zero exit when auto-detect produced nothing; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    # The error explains auto-detect failed AND gives the user a
    # concrete way to find the path manually.
    assert "tmutil" in combined or "auto-detect" in combined, (
        f"expected error to mention auto-detect / tmutil; got {combined!r}"
    )
    assert "--tm-disk" in combined


def test_aborts_when_snapshot_dir_is_empty_and_unmountable(tmp_path):
    """V1 polish (2026-05-25, user-reported): on modern APFS TM, most
    snapshot dirs under /Volumes/.timemachine/<vol-uuid>/ are EMPTY
    mount-point stubs; macOS lazily mounts them on access via specific
    APIs. The script's prior pass silently skipped empty dirs, so on
    the user's 767-snapshot history only the 1 already-mounted
    snapshot was scanned — the dry-run reported "37 unique files"
    instead of the real 5-month total.

    User-observable contract pinned here: if a snapshot is present but
    not accessible (empty dir, mount failed), the default behavior is
    to ABORT with a clear error rather than silently produce a
    misleading "0 files found" result. (User chose this over
    silently-continue; the override flag is pinned by the next test.)
    """
    SNAPSHOT_NAME = "2026-05-25-120000.backup"
    tm_disk = tmp_path / "tm-disk"
    # Create the snapshot dir but leave it EMPTY (mimics an
    # unmounted APFS TM snapshot mount-point stub).
    (tm_disk / SNAPSHOT_NAME).mkdir(parents=True)
    live_home = tmp_path / "home"
    live_home.mkdir()

    cmd = [
        "bash",
        str(SCRIPT),
        "--tm-disk", str(tm_disk),
        "--user", "testuser",
        "--home", str(live_home),
        "--dry-run",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode != 0, (
        f"expected non-zero exit on unmountable snapshot; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "mount" in combined, (
        f"error must mention mounting; got {combined!r}"
    )
    assert "--continue-on-mount-failure" in combined or "continue-on-mount-failure" in combined, (
        f"error must point user at the override flag; got {combined!r}"
    )


def test_continue_on_mount_failure_proceeds_with_what_succeeded(tm_setup, tmp_path):
    """Bidirectional pair: with `--continue-on-mount-failure`, an
    unmountable snapshot is skipped instead of aborting the run.

    Setup: take the existing tm_setup (one populated snapshot) and add
    a SECOND empty snapshot dir. Without the flag this would abort
    (per the previous test). With the flag, the run proceeds and the
    populated snapshot's files are still planned.
    """
    EMPTY_SNAPSHOT_NAME = "2026-05-26-120000.backup"
    (tm_setup["tm_disk"] / EMPTY_SNAPSHOT_NAME).mkdir()

    cmd = [
        "bash",
        str(SCRIPT),
        "--tm-disk", str(tm_setup["tm_disk"]),
        "--user", "testuser",
        "--home", str(tm_setup["live_home"]),
        "--dry-run",
        "--continue-on-mount-failure",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0, (
        f"--continue-on-mount-failure should let the run proceed; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # The populated snapshot's files are still planned.
    assert "projects/proj1/cc-session.jsonl" in result.stdout
    assert "local_XYZ/audit.jsonl" in result.stdout


def test_exits_early_when_non_root_and_mounting_needed(tmp_path):
    """V1 polish (2026-05-25, user-reported): without sudo, the script
    MUST short-circuit immediately rather than iterate hundreds of
    snapshots trying (and failing) to mount each one.

    Failure mode this guards against: user ran the script as non-root
    against 767 APFS snapshots. The mount loop shelled out to mount(8)
    once per snapshot (~30ms each = ~23s of wasted work) before
    aborting with "766 snapshots could not be mounted." User wanted
    fail-fast.

    User-observable contract:
        - non-root + first snapshot empty (mount-stub) + no override
        - script exits non-zero BEFORE running the per-snapshot mount
          loop (verified by absence of the post-loop summary message)
        - error explains why and points at sudo as the recovery path
    """
    if os.geteuid() == 0:
        pytest.skip("requires non-root user to exercise the early-exit path")

    SNAPSHOT_NAME = "2026-05-25-120000.backup"
    tm_disk = tmp_path / "tm-disk"
    (tm_disk / SNAPSHOT_NAME).mkdir(parents=True)
    live_home = tmp_path / "home"
    live_home.mkdir()

    cmd = [
        "bash",
        str(SCRIPT),
        "--tm-disk", str(tm_disk),
        "--user", "testuser",
        "--home", str(live_home),
        "--dry-run",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode != 0, (
        f"expected non-zero exit; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stdout + result.stderr).lower()
    # MUST mention sudo (the recovery path) and root (the requirement).
    assert "sudo" in combined
    assert "root" in combined
    # MUST surface the original args verbatim so the user can copy-paste.
    assert "--tm-disk" in combined
    # MUST NOT have entered the per-snapshot mount loop's summary —
    # the abort message "N snapshot(s) could not be mounted" comes
    # AFTER iterating all snapshots, and we want to fail BEFORE that.
    assert "could not be mounted" not in combined, (
        "early-exit should fire before the per-snapshot mount loop's "
        "post-iteration summary message"
    )


def test_second_apply_is_noop(tm_setup):
    # First apply restores everything.
    first = _run_script(
        tm_disk=tm_setup["tm_disk"],
        live_home=tm_setup["live_home"],
        dry_run=False,
    )
    assert first.returncode == 0, first.stderr

    # Stat the restored audit.jsonl and snapshot mtime.
    audit = tm_setup["live_home"] / tm_setup["cw_audit_relpath_live"]
    original_mtime = audit.stat().st_mtime

    # Second apply should produce no [restore] lines for those files —
    # they exist on live, so the `[ -e ]` gate + cp -n combination
    # silently skips them.
    second = _run_script(
        tm_disk=tm_setup["tm_disk"],
        live_home=tm_setup["live_home"],
        dry_run=False,
    )
    assert second.returncode == 0, second.stderr
    # No restore log lines for the already-present files.
    assert "[restore] cowork/dep1/org1/local_XYZ/audit.jsonl" not in second.stdout
    # And the file wasn't overwritten (mtime unchanged within fs resolution).
    assert audit.stat().st_mtime == original_mtime
