"""Tests for the ``~/.claude-exporter/`` -> ``~/.claude-explorer/`` rename
migration in :func:`backend.config.migrate_legacy_data_dir`.

The migration is invoked from the FastAPI lifespan handler at startup,
BEFORE :func:`backend.config.get_settings` caches. The three scenarios
the user can be in:

1. **Fresh install**: neither directory exists. No-op; the canonical
   path is created lazily by the conversations dir's ``mkdir`` callers.
2. **Legacy-only**: ``~/.claude-exporter/`` exists, ``~/.claude-explorer/``
   does not. Rename via :func:`shutil.move` (atomic on same filesystem).
3. **Both exist**: warn and prefer the canonical one. The legacy dir is
   left untouched so the user can inspect / merge / delete manually.

These tests use a monkeypatched ``HOME`` so they NEVER touch the
developer's real home directory. TESTING.md \u00a75.1 isolation rule.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.tests._platform_home import patch_home

from backend import config


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``Path.home()`` at ``tmp_path`` so the migration runs against
    fixture-only directories. Clears the ``@lru_cache``d settings before
    and after so neither side leaks across tests.

    NEVER returns the real home — that protects the developer's actual
    ``~/.claude-exporter/`` data during ``uv run pytest``.
    """
    patch_home(monkeypatch, tmp_path)
    # On some platforms Path.home() also reads pwd.getpwuid; the HOME env
    # var override is the contract Python's Path.home() promises.
    monkeypatch.delenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", raising=False)
    monkeypatch.delenv("CLAUDE_EXPORTER_SKIP_DATA_DIR_MIGRATION", raising=False)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def test_fresh_install_does_nothing(fake_home: Path) -> None:
    """Neither dir exists: migration is a no-op and creates nothing."""
    legacy = fake_home / ".claude-exporter"
    new = fake_home / ".claude-explorer"
    assert not legacy.exists()
    assert not new.exists()

    config.migrate_legacy_data_dir()

    # Migration must not eagerly create the canonical dir — the various
    # callers (settings, fetcher, etc.) own dir creation via mkdir.
    assert not legacy.exists()
    assert not new.exists()


def test_legacy_only_is_renamed(fake_home: Path) -> None:
    """Only ``~/.claude-exporter/`` exists: it gets renamed to the new path,
    preserving every file and subdirectory verbatim."""
    legacy = fake_home / ".claude-exporter"
    new = fake_home / ".claude-explorer"

    # Build a realistic legacy layout: top-level files + nested
    # conversations dir + a deeper attachments dir.
    legacy.mkdir()
    (legacy / "credentials.json").write_text('{"session_key": "test"}')
    (legacy / "preferences.json").write_text('{"version": 1, "data": {}}')
    (legacy / "search-index.sqlite").write_bytes(b"SQLite format 3\x00")
    conv_dir = legacy / "conversations"
    conv_dir.mkdir()
    (conv_dir / "abc-123.json").write_text('{"uuid": "abc-123"}')
    files_dir = legacy / "files" / "conv-uuid" / "file-uuid"
    files_dir.mkdir(parents=True)
    (files_dir / "original.png").write_bytes(b"PNG_BYTES")

    config.migrate_legacy_data_dir()

    assert not legacy.exists(), "legacy dir should be gone after rename"
    assert new.exists(), "canonical dir must now exist"
    # Every file at every depth must have moved.
    assert (new / "credentials.json").read_text() == '{"session_key": "test"}'
    assert (new / "preferences.json").read_text() == '{"version": 1, "data": {}}'
    assert (new / "search-index.sqlite").read_bytes() == b"SQLite format 3\x00"
    assert (new / "conversations" / "abc-123.json").read_text() == '{"uuid": "abc-123"}'
    assert (
        new / "files" / "conv-uuid" / "file-uuid" / "original.png"
    ).read_bytes() == b"PNG_BYTES"


def test_canonical_only_is_left_alone(fake_home: Path) -> None:
    """Only ``~/.claude-explorer/`` exists: migration is a no-op."""
    new = fake_home / ".claude-explorer"
    legacy = fake_home / ".claude-exporter"
    new.mkdir()
    (new / "preferences.json").write_text('{"version": 1, "data": {}}')

    config.migrate_legacy_data_dir()

    assert not legacy.exists()
    assert new.exists()
    assert (new / "preferences.json").read_text() == '{"version": 1, "data": {}}'


def test_both_exist_prefers_new_and_warns(
    fake_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When both exist, prefer the canonical dir and emit a WARNING.

    The legacy dir is left in place so the user can inspect / merge /
    delete it manually — we never silently destroy user data.
    """
    legacy = fake_home / ".claude-exporter"
    new = fake_home / ".claude-explorer"
    legacy.mkdir()
    new.mkdir()
    (legacy / "credentials.json").write_text("LEGACY")
    (new / "credentials.json").write_text("CANONICAL")

    caplog.set_level(logging.WARNING, logger="backend.config")
    config.migrate_legacy_data_dir()

    # Both dirs still exist; canonical content untouched.
    assert legacy.exists()
    assert new.exists()
    assert (legacy / "credentials.json").read_text() == "LEGACY"
    assert (new / "credentials.json").read_text() == "CANONICAL"
    # Warning must name the legacy dir so the user can find it.
    assert any(
        ".claude-exporter" in record.message and "manual inspection" in record.message
        for record in caplog.records
    ), f"expected a warning naming the legacy dir; got: {[r.message for r in caplog.records]}"


def test_skip_migration_via_canonical_env(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION=1`` skips the rename."""
    legacy = fake_home / ".claude-exporter"
    legacy.mkdir()
    (legacy / "marker").write_text("legacy")

    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", "1")
    config.migrate_legacy_data_dir()

    assert legacy.exists()
    assert not (fake_home / ".claude-explorer").exists()


def test_skip_migration_via_legacy_env(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CLAUDE_EXPORTER_SKIP_DATA_DIR_MIGRATION=1`` still skips (one-release fallback)."""
    legacy = fake_home / ".claude-exporter"
    legacy.mkdir()
    (legacy / "marker").write_text("legacy")

    monkeypatch.setenv("CLAUDE_EXPORTER_SKIP_DATA_DIR_MIGRATION", "1")
    config.migrate_legacy_data_dir()

    assert legacy.exists()
    assert not (fake_home / ".claude-explorer").exists()


def test_migration_is_idempotent(fake_home: Path) -> None:
    """Running migration twice in a row is safe (the second call is a no-op)."""
    legacy = fake_home / ".claude-exporter"
    new = fake_home / ".claude-explorer"
    legacy.mkdir()
    (legacy / "preferences.json").write_text('{"version": 1, "data": {}}')

    config.migrate_legacy_data_dir()
    config.migrate_legacy_data_dir()  # second call must not error

    assert not legacy.exists()
    assert new.exists()
    assert (new / "preferences.json").read_text() == '{"version": 1, "data": {}}'


def test_settings_picks_up_canonical_after_migration(fake_home: Path) -> None:
    """After a legacy-only migration, :func:`Settings.load` defaults to the
    canonical conversations dir, NOT the legacy one (which no longer exists)."""
    legacy = fake_home / ".claude-exporter"
    (legacy / "conversations").mkdir(parents=True)

    config.migrate_legacy_data_dir()
    config.get_settings.cache_clear()  # defensive — fake_home cleared too
    settings = config.Settings.load()

    expected = fake_home / ".claude-explorer" / "conversations"
    assert settings.data_dir == expected


def test_settings_fallback_to_legacy_when_migration_skipped(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If migration was skipped (e.g., the user manually invoked Settings.load
    before the lifespan migrator ran), the legacy dir is used as a fallback
    so the user never sees an empty default."""
    legacy = fake_home / ".claude-exporter"
    (legacy / "conversations").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", "1")

    config.migrate_legacy_data_dir()  # no-op
    config.get_settings.cache_clear()
    settings = config.Settings.load()

    assert settings.data_dir == legacy / "conversations"


def test_read_env_prefers_canonical(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``read_env`` returns the canonical value when both are set."""
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", "/canonical/path")
    monkeypatch.setenv("CLAUDE_EXPORTER_DATA_DIR", "/legacy/path")
    val = config.read_env("CLAUDE_EXPLORER_DATA_DIR", "CLAUDE_EXPORTER_DATA_DIR")
    assert val == "/canonical/path"


def test_read_env_falls_back_to_legacy(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When only the legacy var is set, ``read_env`` returns its value and
    logs a deprecation warning ONCE."""
    monkeypatch.delenv("CLAUDE_EXPLORER_DATA_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_EXPORTER_DATA_DIR", "/legacy/path")
    # Reset the one-shot warning state so this test stands on its own.
    config._warned_legacy_env.clear()

    caplog.set_level(logging.WARNING, logger="backend.config")
    val = config.read_env("CLAUDE_EXPLORER_DATA_DIR", "CLAUDE_EXPORTER_DATA_DIR")
    assert val == "/legacy/path"
    deprecation_records = [
        r for r in caplog.records if "deprecated" in r.message
    ]
    assert len(deprecation_records) == 1, (
        f"expected exactly one deprecation warning; got: {[r.message for r in caplog.records]}"
    )

    # Calling again must not re-warn.
    caplog.clear()
    config.read_env("CLAUDE_EXPLORER_DATA_DIR", "CLAUDE_EXPORTER_DATA_DIR")
    deprecation_records_2 = [
        r for r in caplog.records if "deprecated" in r.message
    ]
    assert len(deprecation_records_2) == 0


def test_read_env_returns_none_when_unset(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither var is set, ``read_env`` returns None."""
    monkeypatch.delenv("CLAUDE_EXPLORER_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_EXPORTER_DATA_DIR", raising=False)
    val = config.read_env("CLAUDE_EXPLORER_DATA_DIR", "CLAUDE_EXPORTER_DATA_DIR")
    assert val is None
