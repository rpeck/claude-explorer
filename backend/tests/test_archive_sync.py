"""N3: the generic archive copier, ArchiveLock, and atomic_write_bytes.

Plan: PLANS/2026.11.18-codex-source.md, §4.2 (copy pass, write rules) and
§4.3 (ArchiveLock). The fake source here is a folder of JSONL files in two
forms: plain ``.jsonl`` and gzip ``.jsonl.gz`` (gzip stands in for zstd, so
the test needs no Codex code). The tests run on macOS and on the Windows VM
(§6.2).
"""

from __future__ import annotations

import contextlib
import gzip
import json
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

import pytest

STEM = "rollout-2026-11-18T10-00-00-0199a7b2-1111-7222-8333-444455556666"


# --------------------------------------------------------------------------
# Fake source helpers


def decode(path: Path):
    """Decode a fake rollout as a stream of chunks, in either form."""
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rb") as fh:
        while chunk := fh.read(7):
            yield chunk


def failing_decode(good_prefix: bytes):
    """A decoder that yields some bytes, then fails like a damaged frame."""

    def _decode(path: Path):
        yield good_prefix
        raise ValueError("damaged frame")

    return _decode


def write_plain(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def write_gz(path: Path, data: bytes) -> Path:
    path.write_bytes(gzip.compress(data))
    return path


def bump_mtime(path: Path) -> None:
    """Change the mtime, so the meta-file shortcut (step 2.1) cannot skip."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def new_report():
    from backend.archive_sync import SyncReport

    return SyncReport()


def sync(dest: Path, source: Path, decoder=decode, stem: str = STEM, **kw):
    from backend.archive_sync import sync_stream

    report = new_report()
    sync_stream(
        report, dest_dir=dest, stem=stem, source_path=source, decode=decoder, **kw
    )
    return report


def copy_of(dest: Path, stem: str = STEM) -> bytes:
    return (dest / f"{stem}.jsonl").read_bytes()


def meta_of(dest: Path, stem: str = STEM) -> dict:
    return json.loads((dest / f"{stem}.meta.json").read_text(encoding="utf-8"))


def revisions(dest: Path, stem: str = STEM) -> list[Path]:
    return sorted(dest.glob(f"{stem}.rev-*.jsonl"))


@pytest.fixture
def src(tmp_path: Path) -> Path:
    d = tmp_path / "source-home" / "sessions"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    d = tmp_path / "archive" / "rollouts"
    d.mkdir(parents=True)
    return d


L1 = b'{"n":1}\n'
L2 = b'{"n":2}\n'
L3 = b'{"n":3}\n'


# --------------------------------------------------------------------------
# sync_stream: the copy cases of §4.2 step 2


def test_first_sync_copies_decoded_bytes(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    report = sync(dest, source)
    assert copy_of(dest) == L1 + L2
    assert report.counts.get("copied", 0) == 1
    assert report.wrote_any is True


def test_compressed_form_is_stored_decoded(src, dest):
    source = write_gz(src / f"{STEM}.jsonl.gz", L1 + L2)
    sync(dest, source)
    assert copy_of(dest) == L1 + L2


def test_meta_file_has_generic_fields(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    before_ms = int(time.time() * 1000)
    sync(dest, source)
    meta = meta_of(dest)
    st = source.stat()
    assert meta["version"] == 1
    assert meta["stem"] == STEM
    assert meta["copied_bytes"] == len(L1 + L2)
    assert meta["source_path"] == str(source)
    assert meta["source_size"] == st.st_size
    assert meta["source_mtime_ns"] == st.st_mtime_ns
    assert before_ms - 5_000 <= meta["last_sync_ms"] <= int(time.time() * 1000) + 5_000


def test_meta_has_no_thread_id_without_meta_extra(src, dest):
    sync(dest, write_plain(src / f"{STEM}.jsonl", L1))
    assert "thread_id" not in meta_of(dest)


def test_meta_extra_is_merged_into_meta_file(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source, meta_extra={"thread_id": "0199a7b2-1111-7222-8333-444455556666"})
    meta = meta_of(dest)
    assert meta["thread_id"] == "0199a7b2-1111-7222-8333-444455556666"
    assert meta["copied_bytes"] == len(L1)
    assert meta["version"] == 1


def test_unchanged_source_is_skipped_without_decode(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    calls: list[Path] = []

    def spy(path):
        calls.append(path)
        yield from decode(path)

    report = sync(dest, source, decoder=spy)
    assert calls == []
    assert report.counts.get("unchanged", 0) == 1
    assert report.wrote_any is False


def test_grown_source_is_appended(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    write_plain(source, L1 + L2 + L3)
    report = sync(dest, source)
    assert copy_of(dest) == L1 + L2 + L3
    assert report.counts.get("appended", 0) == 1
    assert report.counts.get("revisions", 0) == 0
    assert revisions(dest) == []
    assert meta_of(dest)["copied_bytes"] == len(L1 + L2 + L3)


def test_append_fsyncs_the_copy(src, dest, monkeypatch):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    write_plain(source, L1 + L2)
    real_fsync = os.fsync
    synced: set[tuple[int, int]] = set()

    def recording(fd):
        st = os.fstat(fd)
        synced.add((st.st_dev, st.st_ino))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording)
    sync(dest, source)
    st = (dest / f"{STEM}.jsonl").stat()
    assert (st.st_dev, st.st_ino) in synced


def test_append_keeps_existing_file_identity(src, dest):
    """D12: rollouts grow by append, never by a whole-file replace."""
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    ino = (dest / f"{STEM}.jsonl").stat().st_ino
    write_plain(source, L1 + L2)
    sync(dest, source)
    if os.name != "nt":  # st_ino is not stable enough on every Windows FS
        assert (dest / f"{STEM}.jsonl").stat().st_ino == ino


def test_last_line_without_newline_is_not_copied(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + b'{"n":2')
    sync(dest, source)
    assert copy_of(dest) == L1
    assert meta_of(dest)["copied_bytes"] == len(L1)


def test_partial_last_line_is_copied_once_it_ends(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + b'{"n":2')
    sync(dest, source)
    write_plain(source, L1 + L2)
    report = sync(dest, source)
    assert copy_of(dest) == L1 + L2
    assert report.counts.get("appended", 0) == 1


def test_short_read_changes_nothing(src, dest):
    """Step 2.4: a decoded source that is a prefix of the copy is a short read."""
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2 + L3)
    sync(dest, source)
    write_plain(source, L1)
    bump_mtime(source)
    report = sync(dest, source)
    assert copy_of(dest) == L1 + L2 + L3
    assert revisions(dest) == []
    assert report.counts.get("revisions", 0) == 0
    assert report.counts.get("unchanged", 0) == 1
    assert report.wrote_any is False


def test_decode_error_keeps_archive_and_warns(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    write_plain(source, L1 + L2)
    report = sync(dest, source, decoder=failing_decode(L1 + L2))
    assert copy_of(dest) == L1
    assert report.counts.get("decode_errors", 0) == 1
    assert report.warnings
    assert meta_of(dest)["copied_bytes"] == len(L1)


def test_decode_error_never_makes_a_revision(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    sync(dest, source)
    bump_mtime(source)
    report = sync(dest, source, decoder=failing_decode(b'{"other":true}\n'))
    assert revisions(dest) == []
    assert copy_of(dest) == L1 + L2
    assert report.counts.get("revisions", 0) == 0


def test_decode_error_on_first_sync_creates_no_copy(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    report = sync(dest, source, decoder=failing_decode(L1))
    assert not (dest / f"{STEM}.jsonl").exists()
    assert report.counts.get("decode_errors", 0) == 1


def test_decode_error_is_retried_on_next_pass(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    write_plain(source, L1 + L2)
    sync(dest, source, decoder=failing_decode(L1 + L2))
    report = sync(dest, source)
    assert copy_of(dest) == L1 + L2
    assert report.counts.get("appended", 0) == 1


def test_divergence_makes_revision_with_old_bytes(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    sync(dest, source)
    write_plain(source, L1 + L3)
    bump_mtime(source)
    report = sync(dest, source)
    revs = revisions(dest)
    assert [p.name for p in revs] == [f"{STEM}.rev-1.jsonl"]
    assert revs[0].read_bytes() == L1 + L2
    assert copy_of(dest) == L1 + L3
    assert report.counts.get("revisions", 0) == 1
    assert report.warnings
    assert report.wrote_any is True


def test_divergence_logs_warning(src, dest, caplog):
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    sync(dest, source)
    write_plain(source, L3)
    bump_mtime(source)
    with caplog.at_level("WARNING"):
        sync(dest, source)
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_revision_uses_next_free_number(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    (dest / f"{STEM}.rev-1.jsonl").write_bytes(b'{"older":true}\n')
    write_plain(source, L2)
    bump_mtime(source)
    sync(dest, source)
    assert (dest / f"{STEM}.rev-1.jsonl").read_bytes() == b'{"older":true}\n'
    assert (dest / f"{STEM}.rev-2.jsonl").read_bytes() == L1
    assert copy_of(dest) == L2


def test_second_divergence_makes_second_revision(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    for body in (L2, L3):
        write_plain(source, body)
        bump_mtime(source)
        sync(dest, source)
    assert [p.read_bytes() for p in revisions(dest)] == [L1, L2]
    assert copy_of(dest) == L3


def test_resync_after_divergence_makes_no_new_revision(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    sync(dest, source)
    write_plain(source, L3)
    bump_mtime(source)
    sync(dest, source)
    bump_mtime(source)
    report = sync(dest, source)
    assert len(revisions(dest)) == 1
    assert report.counts.get("revisions", 0) == 0


def test_form_change_with_same_content_is_unchanged(src, dest):
    """§3: Codex compresses or decompresses a rollout; only the path changes."""
    plain = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    sync(dest, plain)
    gz = write_gz(src / f"{STEM}.jsonl.gz", L1 + L2)
    plain.unlink()
    report = sync(dest, gz)
    assert copy_of(dest) == L1 + L2
    assert revisions(dest) == []
    assert report.counts.get("unchanged", 0) == 1
    assert report.counts.get("revisions", 0) == 0
    assert meta_of(dest)["source_path"] == str(gz)


def test_form_change_then_growth_appends(src, dest):
    plain = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, plain)
    gz = write_gz(src / f"{STEM}.jsonl.gz", L1 + L2)
    plain.unlink()
    report = sync(dest, gz)
    assert copy_of(dest) == L1 + L2
    assert report.counts.get("appended", 0) == 1
    assert revisions(dest) == []


def test_crash_after_append_before_meta_is_repaired(src, dest):
    """A lost meta write needs no repair code: the next pass fixes it."""
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    old_meta = (dest / f"{STEM}.meta.json").read_bytes()
    write_plain(source, L1 + L2)
    sync(dest, source)
    (dest / f"{STEM}.meta.json").write_bytes(old_meta)  # the meta write "was lost"
    report = sync(dest, source)
    assert copy_of(dest) == L1 + L2
    assert revisions(dest) == []
    assert report.counts.get("revisions", 0) == 0
    assert meta_of(dest)["copied_bytes"] == len(L1 + L2)


def test_crash_with_meta_missing_is_repaired(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    sync(dest, source)
    (dest / f"{STEM}.meta.json").unlink()
    sync(dest, source)
    assert copy_of(dest) == L1 + L2
    assert revisions(dest) == []
    assert meta_of(dest)["copied_bytes"] == len(L1 + L2)


def test_failed_meta_replace_then_next_pass_repairs(src, dest, monkeypatch):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    write_plain(source, L1 + L2)
    real_replace = os.replace

    def failing_replace(a, b):
        if str(b).endswith(".meta.json"):
            raise OSError("simulated crash in the meta write")
        return real_replace(a, b)

    monkeypatch.setattr(os, "replace", failing_replace)
    with contextlib.suppress(OSError):
        sync(dest, source)
    monkeypatch.setattr(os, "replace", real_replace)
    assert meta_of(dest)["copied_bytes"] == len(L1)  # the old meta survived whole
    sync(dest, source)
    assert copy_of(dest) == L1 + L2
    assert revisions(dest) == []
    assert meta_of(dest)["copied_bytes"] == len(L1 + L2)


def test_no_temp_files_left_after_sync(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    write_plain(source, L1 + L2)
    sync(dest, source)
    names = sorted(p.name for p in dest.iterdir())
    assert names == [f"{STEM}.jsonl", f"{STEM}.meta.json"]


def test_sync_never_changes_the_source(src, dest):
    source = write_plain(src / f"{STEM}.jsonl", L1 + L2)
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    sync(dest, source)
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert sorted(p.name for p in src.iterdir()) == [f"{STEM}.jsonl"]


@pytest.mark.parametrize(
    "bad_stem",
    ["", ".", "..", "../evil", "a/b", "a\\b", "/abs", "C:evil", "x\x00y", ".hidden"],
)
def test_invalid_stem_raises_and_writes_nothing(src, dest, bad_stem):
    source = write_plain(src / "ok.jsonl", L1)
    with pytest.raises(ValueError):
        sync(dest, source, stem=bad_stem)
    assert list(dest.iterdir()) == []
    assert list(dest.parent.parent.rglob("evil*")) == []


def test_simple_stem_is_accepted(src, dest):
    source = write_plain(src / "fake-1.jsonl", L1)
    sync(dest, source, stem="fake-1")
    assert copy_of(dest, "fake-1") == L1


# --------------------------------------------------------------------------
# run_locked_pass: step 1 and the lock outcomes


def ctx(timeout: float = 0.0):
    from backend.archive_sync import SyncContext

    return SyncContext(settings=None, reason="cli", lock_timeout_s=timeout)


def test_missing_home_skips_and_creates_nothing(tmp_path):
    from backend.archive_sync import run_locked_pass

    home = tmp_path / "no-such-home"
    archive = tmp_path / "data" / "codex" / "abc"
    called = []
    report = run_locked_pass(
        ctx(), source_home=home, archive_dir=archive, body=called.append
    )
    assert report.skipped_reason == "no_home"
    assert called == []
    assert not home.exists()
    assert not (tmp_path / "data").exists()
    assert report.wrote_any is False


def test_pass_runs_body_under_lock(tmp_path):
    from backend.archive_sync import ArchiveLock, run_locked_pass

    home = tmp_path / "home"
    home.mkdir()
    archive = tmp_path / "archive"
    seen = []

    def body(report):
        probe = ArchiveLock(archive / ".sync.lock")
        seen.append(probe.acquire(0))
        report.counts["copied"] = report.counts.get("copied", 0) + 1

    report = run_locked_pass(ctx(), source_home=home, archive_dir=archive, body=body)
    assert seen == [False]
    assert report.skipped_reason is None
    assert report.counts.get("copied", 0) == 1
    assert (archive / ".sync.lock").exists()


def test_pass_releases_lock_after_body(tmp_path):
    from backend.archive_sync import ArchiveLock, run_locked_pass

    home = tmp_path / "home"
    home.mkdir()
    archive = tmp_path / "archive"
    run_locked_pass(ctx(), source_home=home, archive_dir=archive, body=lambda r: None)
    probe = ArchiveLock(archive / ".sync.lock")
    assert probe.acquire(0) is True
    probe.release()


def test_pass_releases_lock_when_body_raises(tmp_path):
    from backend.archive_sync import ArchiveLock, run_locked_pass

    home = tmp_path / "home"
    home.mkdir()
    archive = tmp_path / "archive"

    def boom(report):
        raise RuntimeError("body failed")

    with contextlib.suppress(RuntimeError):
        run_locked_pass(ctx(), source_home=home, archive_dir=archive, body=boom)
    probe = ArchiveLock(archive / ".sync.lock")
    assert probe.acquire(0) is True
    probe.release()


def _held_by_other_thread(lock_path: Path):
    """Hold the lock in a second thread; return (release_event, thread)."""
    from backend.archive_sync import ArchiveLock

    got = threading.Event()
    done = threading.Event()

    def holder():
        lk = ArchiveLock(lock_path)
        assert lk.acquire(5)
        got.set()
        done.wait(10)
        lk.release()

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert got.wait(5)
    return done, t


def test_pass_busy_when_no_wait(tmp_path):
    from backend.archive_sync import run_locked_pass

    home = tmp_path / "home"
    home.mkdir()
    archive = tmp_path / "archive"
    archive.mkdir()
    done, t = _held_by_other_thread(archive / ".sync.lock")
    try:
        called = []
        report = run_locked_pass(
            ctx(0), source_home=home, archive_dir=archive, body=called.append
        )
        assert report.skipped_reason == "busy"
        assert called == []
    finally:
        done.set()
        t.join(5)


def test_pass_timeout_when_wait_expires(tmp_path):
    from backend.archive_sync import run_locked_pass

    home = tmp_path / "home"
    home.mkdir()
    archive = tmp_path / "archive"
    archive.mkdir()
    done, t = _held_by_other_thread(archive / ".sync.lock")
    try:
        called = []
        start = time.monotonic()
        report = run_locked_pass(
            ctx(0.3), source_home=home, archive_dir=archive, body=called.append
        )
        assert time.monotonic() - start >= 0.25
        assert report.skipped_reason == "timeout"
        assert called == []
    finally:
        done.set()
        t.join(5)


# --------------------------------------------------------------------------
# home_key


def test_home_key_is_12_hex_of_sha256_of_realpath(tmp_path):
    import hashlib

    from backend.archive_sync import home_key

    home = tmp_path / "codex-home"
    home.mkdir()
    real = os.path.realpath(home)
    if sys.platform in ("darwin", "win32"):
        real = real.casefold()
    assert home_key(home) == hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]


def test_home_key_follows_symlink(tmp_path):
    from backend.archive_sync import home_key

    home = tmp_path / "codex-home"
    home.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(home, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need extra rights on this Windows host")
    assert home_key(link) == home_key(home)


def test_home_key_folds_case_on_macos_and_windows(tmp_path):
    from backend.archive_sync import home_key

    if sys.platform not in ("darwin", "win32"):
        pytest.skip("case folding applies only on macOS and Windows")
    home = tmp_path / "CodexHome"
    home.mkdir()
    assert home_key(tmp_path / "codexhome") == home_key(home)


def test_home_key_differs_for_different_homes(tmp_path):
    from backend.archive_sync import home_key

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    key = home_key(tmp_path / "a")
    assert len(key) == 12 and all(c in "0123456789abcdef" for c in key)
    assert key != home_key(tmp_path / "b")


# --------------------------------------------------------------------------
# ArchiveLock in one process


def _try_in_thread(lock_path: Path, timeout: float) -> bool:
    from backend.archive_sync import ArchiveLock

    out: list[bool] = []

    def run():
        lk = ArchiveLock(lock_path)
        ok = lk.acquire(timeout)
        out.append(ok)
        if ok:
            lk.release()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout + 10)
    return out[0]


def test_lock_excludes_another_instance_in_another_thread(tmp_path):
    from backend.archive_sync import ArchiveLock

    path = tmp_path / ".sync.lock"
    a = ArchiveLock(path)
    assert a.acquire(0) is True
    try:
        assert _try_in_thread(path, 0) is False
    finally:
        a.release()
    assert _try_in_thread(path, 0) is True


def test_lock_excludes_another_instance_in_same_thread(tmp_path):
    """A shared handle would not block (council report); a fresh one must."""
    from backend.archive_sync import ArchiveLock

    path = tmp_path / ".sync.lock"
    a = ArchiveLock(path)
    b = ArchiveLock(path)
    assert a.acquire(0) is True
    try:
        assert b.acquire(0) is False
    finally:
        a.release()
    assert b.acquire(0) is True
    b.release()


def test_lock_wait_times_out(tmp_path):
    from backend.archive_sync import ArchiveLock

    path = tmp_path / ".sync.lock"
    a = ArchiveLock(path)
    assert a.acquire(0)
    try:
        start = time.monotonic()
        assert _try_in_thread(path, 0.3) is False
        elapsed = time.monotonic() - start
        assert 0.25 <= elapsed < 5
    finally:
        a.release()


def test_lock_wait_succeeds_when_released(tmp_path):
    from backend.archive_sync import ArchiveLock

    path = tmp_path / ".sync.lock"
    a = ArchiveLock(path)
    assert a.acquire(0)
    timer = threading.Timer(0.2, a.release)
    timer.start()
    try:
        assert _try_in_thread(path, 5) is True
    finally:
        timer.join(5)


def test_lock_for_other_path_does_not_block(tmp_path):
    from backend.archive_sync import ArchiveLock

    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    a = ArchiveLock(tmp_path / "one" / ".sync.lock")
    assert a.acquire(0)
    try:
        assert _try_in_thread(tmp_path / "two" / ".sync.lock", 0) is True
    finally:
        a.release()


def test_lock_writes_no_holder_metadata(tmp_path):
    from backend.archive_sync import ArchiveLock

    path = tmp_path / ".sync.lock"
    a = ArchiveLock(path)
    assert a.acquire(0)
    try:
        assert path.stat().st_size == 0
    finally:
        a.release()
    assert path.stat().st_size == 0


def test_two_threads_serialize_sync_passes(src, dest, tmp_path):
    """Two threads, 50 passes each: every appended line reaches the copy once."""
    from backend.archive_sync import ArchiveLock, sync_stream

    source = write_plain(src / f"{STEM}.jsonl", b"")
    lock_path = tmp_path / ".sync.lock"
    errors: list[BaseException] = []
    reports = []

    def worker(name: str):
        try:
            for i in range(50):
                lk = ArchiveLock(lock_path)
                assert lk.acquire(30)
                try:
                    with open(source, "ab") as fh:
                        fh.write(f'{{"w":"{name}","i":{i}}}\n'.encode())
                    r = new_report()
                    sync_stream(
                        r, dest_dir=dest, stem=STEM, source_path=source, decode=decode
                    )
                    reports.append(r)
                finally:
                    lk.release()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not errors, errors
    assert copy_of(dest) == source.read_bytes()
    assert len(copy_of(dest).splitlines()) == 100
    assert revisions(dest) == []
    assert sum(r.counts.get("revisions", 0) for r in reports) == 0


def test_reader_against_copier_sees_only_prefixes(src, dest):
    """§8: a reader that opens the copy during appends never fails, never sees junk."""
    source = write_plain(src / f"{STEM}.jsonl", L1)
    sync(dest, source)
    stop = threading.Event()
    reads: list[bytes] = []
    errors: list[BaseException] = []

    def reader():
        while not stop.is_set():
            try:
                with open(dest / f"{STEM}.jsonl", "rb") as fh:
                    reads.append(fh.read())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for i in range(200):
            with open(source, "ab") as fh:
                fh.write(f'{{"i":{i}}}\n'.encode())
            sync(dest, source)
    finally:
        stop.set()
        t.join(10)
    final = copy_of(dest)
    assert not errors, errors
    assert final == source.read_bytes()
    assert reads
    assert all(final.startswith(r) for r in reads)
    assert revisions(dest) == []


# --------------------------------------------------------------------------
# ArchiveLock across processes (spawn, so it matches Windows)


def _proc_sync_worker(name, lock_path, source, dest, n, err_path):
    """Child: n locked passes; each appends one line, then syncs."""
    try:
        from backend.archive_sync import ArchiveLock, SyncReport, sync_stream

        for i in range(n):
            lk = ArchiveLock(Path(lock_path))
            if not lk.acquire(60):
                raise RuntimeError("lock timeout")
            try:
                with open(source, "ab") as fh:
                    fh.write(f'{{"p":"{name}","i":{i}}}\n'.encode())
                r = SyncReport()
                sync_stream(
                    r,
                    dest_dir=Path(dest),
                    stem=STEM,
                    source_path=Path(source),
                    decode=decode,
                )
                if r.counts.get("revisions", 0) or r.counts.get("decode_errors", 0):
                    raise RuntimeError(f"bad pass: {r}")
            finally:
                lk.release()
    except BaseException as exc:
        Path(err_path).write_text(repr(exc), encoding="utf-8")
        raise


def _proc_hold_lock(lock_path, ready_path, release_path):
    from backend.archive_sync import ArchiveLock

    lk = ArchiveLock(Path(lock_path))
    if not lk.acquire(30):
        return
    Path(ready_path).write_text("held", encoding="utf-8")
    deadline = time.monotonic() + 60
    while not Path(release_path).exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    lk.release()


def _wait_for(path: Path, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} never appeared")
        time.sleep(0.05)


def test_four_processes_serialize_sync_passes(src, dest, tmp_path):
    source = write_plain(src / f"{STEM}.jsonl", b"")
    lock_path = tmp_path / ".sync.lock"
    mp = multiprocessing.get_context("spawn")
    procs = []
    for k in range(4):
        p = mp.Process(
            target=_proc_sync_worker,
            args=(
                f"p{k}",
                str(lock_path),
                str(source),
                str(dest),
                50,
                str(tmp_path / f"err-{k}.txt"),
            ),
        )
        p.start()
        procs.append(p)
    for p in procs:
        p.join(300)
    errs = [f.read_text(encoding="utf-8") for f in tmp_path.glob("err-*.txt")]
    assert not errs, errs
    assert [p.exitcode for p in procs] == [0, 0, 0, 0]
    final = copy_of(dest)
    assert final == source.read_bytes()
    assert len(final.splitlines()) == 200
    assert revisions(dest) == []
    assert meta_of(dest)["copied_bytes"] == len(final)


def test_lock_held_by_other_process_blocks(tmp_path):
    from backend.archive_sync import ArchiveLock

    lock_path = tmp_path / ".sync.lock"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    p = multiprocessing.get_context("spawn").Process(
        target=_proc_hold_lock, args=(str(lock_path), str(ready), str(release))
    )
    p.start()
    try:
        _wait_for(ready)
        lk = ArchiveLock(lock_path)
        assert lk.acquire(0) is False
        release.write_text("go", encoding="utf-8")
        assert lk.acquire(30) is True
        lk.release()
    finally:
        release.write_text("go", encoding="utf-8")
        p.join(30)


def test_lock_is_freed_when_holder_process_dies(tmp_path):
    from backend.archive_sync import ArchiveLock

    lock_path = tmp_path / ".sync.lock"
    ready = tmp_path / "ready"
    p = multiprocessing.get_context("spawn").Process(
        target=_proc_hold_lock,
        args=(str(lock_path), str(ready), str(tmp_path / "never")),
    )
    p.start()
    try:
        _wait_for(ready)
        lk = ArchiveLock(lock_path)
        assert lk.acquire(0) is False
        p.kill()
        p.join(30)
        assert lk.acquire(10) is True
        lk.release()
    finally:
        if p.is_alive():
            p.kill()
            p.join(30)


# --------------------------------------------------------------------------
# fetcher.atomic.atomic_write_bytes


def test_atomic_write_bytes_writes_new_file(tmp_path):
    from fetcher.atomic import atomic_write_bytes

    target = tmp_path / "x.json"
    atomic_write_bytes(target, b'{"a":1}')
    assert target.read_bytes() == b'{"a":1}'
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_atomic_write_bytes_replaces_existing_file(tmp_path):
    from fetcher.atomic import atomic_write_bytes

    target = tmp_path / "x.json"
    target.write_bytes(b"old")
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_atomic_write_bytes_fsyncs(tmp_path, monkeypatch):
    from fetcher.atomic import atomic_write_bytes

    real_fsync = os.fsync
    count = [0]

    def counting(fd):
        count[0] += 1
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting)
    atomic_write_bytes(tmp_path / "x.json", b"data")
    assert count[0] >= 1


def test_atomic_write_bytes_uses_temp_in_same_folder(tmp_path, monkeypatch):
    from fetcher.atomic import atomic_write_bytes

    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(a, b):
        seen.append((str(a), str(b)))
        return real_replace(a, b)

    monkeypatch.setattr(os, "replace", spy)
    target = tmp_path / "x.json"
    atomic_write_bytes(target, b"data")
    assert len(seen) == 1
    tmp, dst = seen[0]
    assert Path(dst) == target
    assert Path(tmp).parent == target.parent
    assert Path(tmp) != target


def test_atomic_write_bytes_retries_windows_sharing_violation(tmp_path, monkeypatch):
    from fetcher import atomic

    real_replace = os.replace
    calls = [0]

    def flaky(a, b):
        calls[0] += 1
        if calls[0] == 1:
            err = PermissionError("sharing violation")
            err.winerror = 32  # type: ignore[attr-defined]
            raise err
        return real_replace(a, b)

    monkeypatch.setattr(os, "replace", flaky)
    target = tmp_path / "x.json"
    atomic.atomic_write_bytes(target, b"data")
    assert target.read_bytes() == b"data"
    assert calls[0] == 2


def test_atomic_write_bytes_failure_keeps_old_file_and_no_temp(tmp_path, monkeypatch):
    from fetcher.atomic import atomic_write_bytes

    target = tmp_path / "x.json"
    target.write_bytes(b"old")

    def broken(a, b):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_atomic_write_bytes_concurrent_writers_do_not_collide(tmp_path):
    """Unique temp names: two threads writing the same file never mix bytes."""
    from fetcher.atomic import atomic_write_bytes

    target = tmp_path / "x.json"
    payloads = [bytes([65 + k]) * 50_000 for k in range(2)]
    errors: list[BaseException] = []

    def writer(data: bytes):
        try:
            for _ in range(30):
                atomic_write_bytes(target, data)
        except PermissionError:
            pass  # Windows: a replace can lose to a concurrent replace
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    assert target.read_bytes() in payloads
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


# --------------------------------------------------------------------------
# Sibling writers that N3 moves onto atomic_write_bytes


def test_remote_sessions_write_helper_fsyncs(tmp_path, monkeypatch):
    from fetcher import remote_sessions

    real_fsync = os.fsync
    count = [0]

    def counting(fd):
        count[0] += 1
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting)
    target = tmp_path / "audit.jsonl"
    remote_sessions._atomic_write_text(target, "line\n")
    assert target.read_text(encoding="utf-8") == "line\n"
    assert count[0] >= 1
    assert [p.name for p in tmp_path.iterdir()] == ["audit.jsonl"]


@pytest.fixture
def cc_env(tmp_path, monkeypatch):
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import config as cfg

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    yield {"claude_dir": claude_dir, "data_dir": data_dir}
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def _cc_image(claude_dir: Path) -> Path:
    d = claude_dir / "image-cache" / "sess-1"
    d.mkdir(parents=True)
    p = d / "1.png"
    p.write_bytes(b"\x89PNG fake image bytes")
    return p


def test_cc_image_copy_is_atomic(cc_env, monkeypatch):
    """A failed replace leaves no partial image at the cache path."""
    from backend import cc_image_cache

    img = _cc_image(cc_env["claude_dir"])

    def broken(a, b):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", broken)
    with contextlib.suppress(OSError):
        cc_image_cache.copy_marker_image_to_cache(str(img), "conv-1")
    conv_dir = cc_image_cache.cache_dir() / "conv-1"
    leftovers = list(conv_dir.iterdir()) if conv_dir.exists() else []
    assert leftovers == []
