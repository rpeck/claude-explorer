"""Regression: ``backend.config.Settings.load`` must not crash on a
corrupt ``config.json``.

Bug class: uncaught specialized-parser exception (variant of catalog
class #2 — Unsafe primitive coercion extended to JSON config parsing).
Surfaced 2026-05-18 by the /code-audit broad sweep.

Failure mode without the fix: a user whose editor crashed mid-save of
``~/.claude-explorer/config.json`` (leaving truncated JSON) cannot boot
``claude-explorer serve`` — ``Settings.load()`` propagates
``json.JSONDecodeError`` out of the FastAPI lifespan, the server dies
with a cryptic stack trace, and the user has no UI to recover from.

Discipline:
  * **Bidirectional**: corrupt config → defaults; valid config → fields
    honored. Asserting only the corrupt case would pass a trivially-broken
    impl that ignores ALL config.
  * **Boundary cases**: empty file, whitespace-only file, non-dict root
    (list / scalar).
  * **Cross-corpus diversity**: canonical-corrupt-vs-legacy-valid case
    pins the council Critic's "premature break" finding — the fix must
    `continue` to the legacy path, not silently fall back to defaults.

Test isolation: each test monkeypatches HOME to a tmp_path and clears
the ``get_settings`` lru_cache before AND after to avoid pollution.
"""

import json
import logging
from pathlib import Path

import pytest

from backend.tests._platform_home import patch_home

from backend import config


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at a tmp dir and clear the Settings lru_cache.

    Settings.load() reads ``Path.home() / ".claude-explorer" / "config.json"``
    (and the legacy ``.claude-exporter`` sibling), so monkeypatching HOME
    isolates the test from the developer's real config file.
    """
    patch_home(monkeypatch, tmp_path)
    # Strip env overrides so the load path actually consults config.json.
    monkeypatch.delenv("CLAUDE_EXPLORER_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_EXPORTER_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_DIR", raising=False)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def _write_canonical_config(home: Path, contents: str) -> Path:
    """Write `contents` to ``<home>/.claude-explorer/config.json``."""
    cfg_dir = home / config.CANONICAL_HOME_DIR_NAME
    cfg_dir.mkdir(exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(contents)
    return cfg_path


def _write_legacy_config(home: Path, contents: str) -> Path:
    """Write `contents` to ``<home>/.claude-exporter/config.json``."""
    cfg_dir = home / config.LEGACY_HOME_DIR_NAME
    cfg_dir.mkdir(exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(contents)
    return cfg_path


# -- Corrupt cases -----------------------------------------------------


def test_corrupt_canonical_config_does_not_crash_boot(
    isolated_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Truncated JSON in the canonical config must not raise; should log a warning
    and fall back to defaults so the FastAPI server can still boot."""
    _write_canonical_config(isolated_home, '{"data_dir": "/foo"')  # missing }

    with caplog.at_level(logging.WARNING, logger="backend.config"):
        settings = config.Settings.load()

    assert settings.data_dir == isolated_home / ".claude-explorer" / "conversations"
    assert any(
        "config" in rec.message.lower() and "config.json" in rec.message
        for rec in caplog.records
    ), f"expected a warning about the bad config; got {[r.message for r in caplog.records]}"


def test_empty_canonical_config_does_not_crash(isolated_home: Path) -> None:
    """Empty file → json.JSONDecodeError → swallowed + default fallback."""
    _write_canonical_config(isolated_home, "")
    settings = config.Settings.load()
    assert settings.data_dir == isolated_home / ".claude-explorer" / "conversations"


def test_whitespace_only_canonical_config_does_not_crash(
    isolated_home: Path,
) -> None:
    """Whitespace-only → json.JSONDecodeError → swallowed + default fallback."""
    _write_canonical_config(isolated_home, "   \n  \t ")
    settings = config.Settings.load()
    assert settings.data_dir == isolated_home / ".claude-explorer" / "conversations"


def test_non_dict_root_canonical_config_does_not_crash(
    isolated_home: Path,
) -> None:
    """JSON root is a list — ``"data_dir" in [...]`` returns False (no crash),
    but defending against this explicitly is part of the contract — a
    future refactor that uses ``config.get("data_dir")`` would TypeError on
    a list root. Pin the safe behavior."""
    _write_canonical_config(isolated_home, '["data_dir"]')
    settings = config.Settings.load()
    assert settings.data_dir == isolated_home / ".claude-explorer" / "conversations"


# -- Bidirectional positive cases --------------------------------------


def test_valid_canonical_config_is_honored(isolated_home: Path) -> None:
    """Valid JSON with data_dir → honored. The negative side of the pair:
    without this, a "fix" that ignored ALL config would pass the corrupt
    cases above."""
    custom = isolated_home / "custom_data"
    _write_canonical_config(
        isolated_home, json.dumps({"data_dir": str(custom)})
    )
    settings = config.Settings.load()
    assert settings.data_dir == custom


def test_valid_canonical_config_with_claude_dir_is_honored(
    isolated_home: Path,
) -> None:
    """Both fields settable from config.json."""
    custom_data = isolated_home / "custom_data"
    custom_claude = isolated_home / "custom_claude"
    _write_canonical_config(
        isolated_home,
        json.dumps(
            {"data_dir": str(custom_data), "claude_dir": str(custom_claude)}
        ),
    )
    settings = config.Settings.load()
    assert settings.data_dir == custom_data
    assert settings.claude_dir == custom_claude


# -- The Critic's "premature break" pin --------------------------------


def test_corrupt_canonical_falls_through_to_valid_legacy(
    isolated_home: Path,
) -> None:
    """If the canonical config is corrupt AND the legacy config exists and
    is valid, the loader MUST consult the legacy file rather than silently
    falling back to defaults. Otherwise a user who recently upgraded loses
    their settings on a partial-write of the canonical file.

    Pins the Council Critic's "premature break" finding (2026-05-18 meta-
    audit, Decision Record §2): the post-parse break must only fire on
    SUCCESS; on parse failure we ``continue`` to the legacy candidate.
    """
    legacy_data = isolated_home / "legacy_data"
    _write_canonical_config(isolated_home, '{"data_dir": "broken')  # corrupt
    _write_legacy_config(
        isolated_home, json.dumps({"data_dir": str(legacy_data)})
    )

    settings = config.Settings.load()

    assert settings.data_dir == legacy_data, (
        "Loader must fall through to legacy config when canonical is "
        "corrupt; the bug is silently defaulting when a valid legacy "
        "config sits right next door."
    )
