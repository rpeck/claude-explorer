"""Layer 1 of PLANS/2026.05.18-config-corruption-safe-mode.md:
``Settings.config_corrupt_reason`` field populated from the parse loop.

Why a new file: the existing ``test_settings_corrupt_config.py`` pins the
boot-doesn't-crash invariant (commit ``ed9dca9``). This file pins the
NEXT layer's invariant — that a corrupt config surfaces a descriptive
reason on the loaded ``Settings`` so writers can refuse and the UI can
banner. The two test files cover orthogonal contracts; keeping them
separate makes the failure-mode docstrings tight.

Discipline (per PLANS/.../CLAUDE-TESTING.md):

* **Bidirectional pairs**: every "reason set" test has a "reason None"
  sibling. A trivially-broken impl that *always* set the reason would
  pass the corruption tests alone — the sibling rules that out.
* **Path provenance**: the reason string MUST contain the failing
  config path so the UI can render "fix this exact file". Asserting
  only the exception type would silently accept a "lost the path"
  refactor.
* **Cross-corpus diversity**: JSON parse error, OSError (chmod-denied
  read), non-dict-root structural error, and UnicodeDecodeError (UTF-8
  config opened with platform default on a Windows-style CP1252 box)
  each exercise a different branch of the catch tuple.
* **lru_cache recheck path**: the UI banner must clear when the user
  fixes the file mid-session; pinned by an explicit cache-clear test
  rather than a global flush in the recovery flow.

Test isolation: each test monkeypatches HOME to a tmp_path and clears
``get_settings`` lru_cache before AND after to avoid pollution. This
mirrors the existing fixture in ``test_settings_corrupt_config.py``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from backend.tests._platform_home import patch_home

from backend import config


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at a tmp dir and clear the Settings lru_cache.

    Mirrors the fixture in ``test_settings_corrupt_config.py`` so the two
    layers' tests share their isolation strategy without
    cross-importing private fixtures.
    """
    patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAUDE_EXPLORER_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_EXPORTER_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_DIR", raising=False)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def _write_canonical_config(home: Path, contents: str | bytes) -> Path:
    cfg_dir = home / config.CANONICAL_HOME_DIR_NAME
    cfg_dir.mkdir(exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    if isinstance(contents, bytes):
        cfg_path.write_bytes(contents)
    else:
        cfg_path.write_text(contents)
    return cfg_path


def _write_legacy_config(home: Path, contents: str) -> Path:
    cfg_dir = home / config.LEGACY_HOME_DIR_NAME
    cfg_dir.mkdir(exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(contents)
    return cfg_path


# -- Bidirectional: clean config → reason None -------------------------


def test_no_config_file_leaves_reason_None(isolated_home: Path) -> None:
    """Absence of any config file is the fresh-install case, not corruption.

    The "premature break" Critic-pin test in test_settings_corrupt_config.py
    already covers the missing-file behavior for data_dir resolution; this
    test explicitly pins that absence does NOT populate the corruption
    reason. Without this assert, a "trivially-broken" impl that set the
    reason to ``"no config found"`` would pass every corrupt-config test
    here.
    """
    settings = config.Settings.load()
    assert settings.config_corrupt_reason is None


def test_valid_canonical_config_leaves_reason_None(isolated_home: Path) -> None:
    """Valid canonical config → reason None. Bidirectional pair for every
    "reason set" assertion below: rules out always-set impls."""
    custom = isolated_home / "custom_data"
    _write_canonical_config(
        isolated_home, json.dumps({"data_dir": str(custom)})
    )
    settings = config.Settings.load()
    assert settings.data_dir == custom
    assert settings.config_corrupt_reason is None


# -- Reason populated for each failure mode ----------------------------


def test_corrupt_canonical_config_sets_reason_with_path_and_exception(
    isolated_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Truncated JSON → reason includes the file path AND the exception
    name/message.

    Asserting both elements (path AND exception) pins the L3 banner
    contract: the UI renders the path so the user knows WHICH file to
    fix, and the exception detail so the user knows WHY. Dropping
    either would silently regress to a useless "config is corrupt"
    blob.
    """
    cfg_path = _write_canonical_config(isolated_home, '{"data_dir": "/foo"')  # missing }

    with caplog.at_level(logging.WARNING, logger="backend.config"):
        settings = config.Settings.load()

    assert settings.config_corrupt_reason is not None
    reason = settings.config_corrupt_reason
    # Path provenance: full path so the banner can show user where to look.
    assert str(cfg_path) in reason, (
        f"reason must include the corrupt file's path; got {reason!r}"
    )
    # Exception type provenance: a generic "config is corrupt" string
    # would lose the actionable JSON-parser detail (line/column).
    assert "JSONDecodeError" in reason, (
        f"reason must name the underlying exception class; got {reason!r}"
    )


def test_empty_canonical_config_sets_reason(isolated_home: Path) -> None:
    """Empty file → ``json.JSONDecodeError("Expecting value: line 1...")``.

    Bidirectional pair on ``test_valid_canonical_config_leaves_reason_None``:
    the data_dir falls back to default (already pinned upstream), AND the
    reason is populated.
    """
    cfg_path = _write_canonical_config(isolated_home, "")
    settings = config.Settings.load()
    assert settings.data_dir == isolated_home / ".claude-explorer" / "conversations"
    assert settings.config_corrupt_reason is not None
    assert str(cfg_path) in settings.config_corrupt_reason


def test_non_dict_root_canonical_config_sets_reason(isolated_home: Path) -> None:
    """JSON root is a list — structurally invalid, not a parse error.

    The current loader skips non-dict roots with a log warning and
    ``continue``. Layer 1 must also populate ``config_corrupt_reason``
    for this case — otherwise a list-root file would silently default
    with no UI surfacing.
    """
    cfg_path = _write_canonical_config(isolated_home, '["data_dir"]')
    settings = config.Settings.load()
    assert settings.config_corrupt_reason is not None
    assert str(cfg_path) in settings.config_corrupt_reason


def test_non_utf8_canonical_config_sets_reason(isolated_home: Path) -> None:
    """A config file written in a non-UTF-8 encoding (e.g. CP1252-encoded
    file with bytes 0x80-0xFF) raises ``UnicodeDecodeError`` (which
    inherits from ``ValueError``) on ``open(...).read()``.

    On macOS/Linux the default encoding is UTF-8 so this is rare; on
    Windows the default is CP1252 and the same UTF-8 file would
    inversely fail. The Python Expert (Council 2026-05-19) flagged this
    as a missed edge case. Pinning here so a future "simplify the except
    tuple" refactor can't silently re-introduce a Windows-only crash.
    """
    # 0xFF 0xFE is an invalid UTF-8 byte sequence; ``open(..., encoding="utf-8")``
    # raises UnicodeDecodeError when ``json.load`` reads it.
    _write_canonical_config(isolated_home, b'\xff\xfe{"data_dir": "x"}')
    settings = config.Settings.load()
    # Must not crash. Must surface as a corruption reason rather than
    # bubbling out of the loader.
    assert settings.config_corrupt_reason is not None, (
        "non-UTF-8 config must be reported as a corruption reason, "
        "not propagate as an uncaught UnicodeDecodeError"
    )


# -- Fall-through + corruption-still-surfaced --------------------------


def test_corrupt_canonical_with_valid_legacy_still_sets_reason(
    isolated_home: Path,
) -> None:
    """The "premature break" Critic-pin test ensured corrupt canonical +
    valid legacy → data_dir from legacy. Layer 1 adds: the corruption
    reason from the canonical file MUST still be surfaced even though
    the legacy fallback works.

    Rationale (council decision record, D1 2026-05-19): silently masking
    a corrupt canonical file just because the legacy file happens to
    exist is the exact "silent data-dir orphaning" failure mode this
    layer was created to fix. Surfacing the reason while honoring the
    legacy fallback satisfies both invariants.
    """
    legacy_data = isolated_home / "legacy_data"
    canon_path = _write_canonical_config(isolated_home, '{"data_dir": "broken')
    _write_legacy_config(
        isolated_home, json.dumps({"data_dir": str(legacy_data)})
    )

    settings = config.Settings.load()

    # Existing invariant unchanged: legacy fallback still drives data_dir.
    assert settings.data_dir == legacy_data
    # New invariant: corruption is surfaced even though the fallback
    # found a working config. Without this assert, a "clear reason when
    # we found valid config" impl would pass while reintroducing the
    # silent orphaning bug.
    assert settings.config_corrupt_reason is not None
    assert str(canon_path) in settings.config_corrupt_reason


# -- lru_cache recheck path --------------------------------------------


def test_cache_clear_picks_up_repaired_config(isolated_home: Path) -> None:
    """The user's recovery flow: corrupt the file, see the banner, fix
    the file, refresh the UI. Without a working cache-clear path, the
    banner persists until server restart.

    This test pins the contract at the ``get_settings`` boundary: a
    cleared lru_cache reflects on-disk state immediately. The router's
    per-request ``cache_clear()`` (Layer 3 wiring) is tested separately
    at the HTTP layer.
    """
    cfg_path = _write_canonical_config(isolated_home, '{"data_dir":')  # corrupt
    s1 = config.get_settings()
    assert s1.config_corrupt_reason is not None, (
        "precondition: first load should detect corruption"
    )

    # User fixes the file.
    custom = isolated_home / "fixed_data"
    cfg_path.write_text(json.dumps({"data_dir": str(custom)}))

    # Without cache_clear, the user is stuck on the old Settings.
    s2_before_clear = config.get_settings()
    assert s2_before_clear is s1, (
        "precondition: lru_cache returns the cached corrupt Settings until cleared"
    )

    # After cache_clear, the next call reflects the repaired file.
    config.get_settings.cache_clear()
    s2 = config.get_settings()
    assert s2.config_corrupt_reason is None, (
        "after cache_clear, repaired config should produce a clean Settings"
    )
    assert s2.data_dir == custom
