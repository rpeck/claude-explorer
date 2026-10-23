"""Configuration settings for the backend.

V1 rename: the canonical user data directory is ``~/.claude-explorer/``
(matching the ``claude-explorer`` CLI command). The legacy name
``~/.claude-exporter/`` is kept readable for one release via the
:func:`migrate_legacy_data_dir` function that ``backend.main`` invokes
at FastAPI lifespan startup, BEFORE :func:`get_settings` caches.

Environment variables also follow the rename: ``CLAUDE_EXPLORER_*`` are
canonical; legacy ``CLAUDE_EXPORTER_*`` are read as fallbacks with a
single deprecation warning emitted on first read per variable.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from functools import lru_cache

import platformdirs
from pydantic import BaseModel


log = logging.getLogger(__name__)


#: Canonical data directory name. Matches the ``claude-explorer`` CLI.
CANONICAL_HOME_DIR_NAME = ".claude-explorer"
#: Legacy data directory name. Read only as a fallback during V1 migration.
LEGACY_HOME_DIR_NAME = ".claude-exporter"


def canonical_home_dir() -> Path:
    """Return ``~/.claude-explorer/`` (the V1 canonical app dir)."""
    return Path.home() / CANONICAL_HOME_DIR_NAME


def files_dir_for(data_dir: Path) -> Path:
    """Where attachments live for a given conversations data directory.

    The default layout puts ``conversations/`` and ``files/`` side by side
    under ``~/.claude-explorer/``. Any other data directory keeps its
    ``files/`` inside it. The fetchers write here and the attachments
    route reads here, so both must use this one rule.
    """
    if data_dir.name == "conversations":
        return data_dir.parent / "files"
    return data_dir / "files"


def legacy_home_dir() -> Path:
    """Return ``~/.claude-exporter/`` (the pre-V1 app dir)."""
    return Path.home() / LEGACY_HOME_DIR_NAME


# Track which legacy CLAUDE_EXPORTER_* env vars we've already warned about
# so we don't spam the log on every read. One warning per var per process.
_warned_legacy_env: set[str] = set()


def read_env(canonical_name: str, legacy_name: str | None = None) -> str | None:
    """Read an env var by its canonical name, with optional legacy fallback.

    Returns the canonical value if set; otherwise the legacy value (with a
    one-shot deprecation warning logged). Returns ``None`` if neither is
    set. Empty strings are treated the same as unset to match the previous
    ``os.environ.get`` + truthiness pattern used throughout the codebase.
    """
    val = os.environ.get(canonical_name)
    if val:
        return val
    if legacy_name is None:
        return None
    legacy_val = os.environ.get(legacy_name)
    if not legacy_val:
        return None
    if legacy_name not in _warned_legacy_env:
        _warned_legacy_env.add(legacy_name)
        log.warning(
            "Env var %s is deprecated; use %s instead. Falling back for now; "
            "support will be removed in the next release.",
            legacy_name,
            canonical_name,
        )
    return legacy_val


def migrate_legacy_data_dir() -> None:
    """One-time migration of ``~/.claude-exporter/`` -> ``~/.claude-explorer/``.

    Called once from the FastAPI lifespan handler at startup, BEFORE
    :func:`get_settings` is invoked for the first time. Skip via
    ``CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION=1`` (or the legacy
    ``CLAUDE_EXPORTER_SKIP_DATA_DIR_MIGRATION=1``) for tests.

    Cases handled:

    * Neither exists: no-op (fresh install — the data dir is created
      lazily by callers using the canonical name).
    * Only canonical exists: no-op (already migrated, or fresh V1 install).
    * Only legacy exists: rename legacy -> canonical via :func:`shutil.move`.
      ``shutil.move`` is ``os.rename`` on the same filesystem (atomic)
      and a copy+remove on cross-filesystem moves (non-atomic but
      reliable).
    * Both exist: warn and prefer the canonical. The legacy dir is left
      untouched so the user can inspect / merge / delete manually.

    Idempotent and safe to call multiple times. Failures (permission
    errors, etc.) are logged at ERROR but do NOT crash startup — the
    legacy fallback in :class:`Settings` keeps the app usable while the
    user resolves the issue.
    """
    if read_env(
        "CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION",
        "CLAUDE_EXPORTER_SKIP_DATA_DIR_MIGRATION",
    ) == "1":
        log.info("Data-dir migration skipped via env var.")
        return

    legacy = legacy_home_dir()
    new = canonical_home_dir()

    if not legacy.exists():
        # Fresh install or already-migrated — nothing to do.
        return

    if new.exists():
        log.warning(
            "Both %s and %s exist. Preferring %s; the legacy directory has "
            "been left in place for manual inspection. Once you're confident "
            "your data has been migrated, you may delete %s.",
            legacy, new, new, legacy,
        )
        return

    # Only legacy exists — perform the rename.
    try:
        shutil.move(str(legacy), str(new))
    except OSError as exc:
        log.error(
            "Failed to migrate data dir %s -> %s: %s. The app will continue "
            "to read from the legacy path via the Settings fallback, but "
            "please move it manually when possible.",
            legacy, new, exc,
        )
        return

    log.info("Migrated data dir %s -> %s", legacy, new)


class Settings(BaseModel):
    """Application settings."""

    data_dir: Path
    # Root directory for Claude Code session JSONLs. The reader walks
    # ``claude_dir / "projects" / <encoded-cwd> / <uuid>.jsonl``. Override
    # via the CLAUDE_DIR env var (set by the Playwright fixture-mode
    # runner) so contributors without ~/.claude/projects on disk can run
    # the e2e suite against committed synthetic fixtures.
    claude_dir: Path
    # Root directory for the Claude Desktop application's user data. The
    # Cowork reader walks
    # ``claude_desktop_app_dir / "local-agent-mode-sessions" / <deployment>
    # / <org> / local_<uuid>/audit.jsonl`` plus the sibling
    # ``local_<uuid>.json`` sidecar.
    #
    # Override precedence (matches ``claude_dir``):
    #   1. ``CLAUDE_DESKTOP_APP_DIR`` env var (Playwright fixture mode)
    #   2. ``config.json`` ``claude_desktop_app_dir`` key
    #   3. ``platformdirs.user_data_path("Claude")`` (cross-platform default;
    #      e.g. ``~/Library/Application Support/Claude`` on macOS,
    #      ``~/.local/share/Claude`` on Linux,
    #      ``%APPDATA%/Claude`` on Windows).
    claude_desktop_app_dir: Path
    # Layer 1 of PLANS/2026.05.18-config-corruption-safe-mode.md.
    #
    # Set to a one-line, human-readable description of WHY the config
    # parse failed when any present ``config.json`` candidate didn't load
    # cleanly. ``None`` when every present config parsed (or when no
    # config file exists — absence is not corruption).
    #
    # Wire-format note: surfaced verbatim in ``AppConfig`` so the
    # frontend banner can render the path + exception name directly. If
    # multiple candidates fail (canonical AND legacy both corrupt), they
    # are joined with `` | `` in load-order. Format per failure:
    # ``f"{path}: {type(exc).__name__}: {exc}"``.
    #
    # Invariant pinned by the L1 test slab
    # (``test_settings_corrupt_reason.py``): set even when a later
    # candidate parses cleanly. The "premature break" Critic-pin test
    # ensures ``data_dir`` still resolves from the working candidate;
    # this field surfaces the broken one to the user so the silent
    # data-dir orphaning failure mode can't recur.
    config_corrupt_reason: str | None = None

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from environment or config file.

        Defaults to ``~/.claude-explorer/conversations``. If that dir
        doesn't exist but the legacy ``~/.claude-exporter/conversations``
        does (e.g., the lifespan migration has not yet run, or the user
        invoked a CLI before the server started), we fall back to the
        legacy path so the app is never staring at an empty default
        directory while the user's real data sits next door.
        """
        env_data_dir = read_env(
            "CLAUDE_EXPLORER_DATA_DIR", "CLAUDE_EXPORTER_DATA_DIR"
        )
        env_claude_dir = os.environ.get("CLAUDE_DIR")
        env_claude_desktop_app_dir = os.environ.get("CLAUDE_DESKTOP_APP_DIR")

        # Check config file (used as fallback for fields not set via env).
        # Prefer the canonical location; fall back to the legacy location
        # for users whose lifespan migration has not yet renamed the dir.
        config_data_dir: Path | None = None
        config_claude_dir: Path | None = None
        config_claude_desktop_app_dir: Path | None = None
        # Layer 1 (2026-05-18): accumulate per-candidate parse failures.
        # Joined with `` | `` into ``config_corrupt_reason`` at the bottom
        # of ``load``. The list is empty in the happy path (no parse
        # failures), which collapses to ``None`` for the field — keeping
        # the wire-format optionality cleanly representable.
        corruption_reasons: list[str] = []
        for config_path in (
            canonical_home_dir() / "config.json",
            legacy_home_dir() / "config.json",
        ):
            if not config_path.exists():
                continue
            # Hunt-config-parse (2026-05-18 broad sweep): a corrupt
            # config.json (editor crash mid-save, truncated JSON, non-dict
            # root) MUST NOT crash boot — otherwise the user has no UI to
            # recover and is stuck deciphering a stack trace. Catch
            # JSONDecodeError + OSError (TOCTOU between exists() and open(),
            # permission denied) + TypeError (subscript on non-dict root)
            # + ValueError (catches UnicodeDecodeError on non-UTF-8 files,
            # flagged by Python Expert Council 2026-05-19 as a Windows-
            # path-default-encoding gap). We do NOT catch bare ``Exception``
            # (catalog #4).
            #
            # Council Critic 2026-05-18 §2: ``break`` on error would
            # silently default when a valid legacy config sits right next
            # door. On parse failure we ``continue`` to the next candidate
            # so the legacy fallback still works — AND (Layer 1) we
            # record the failure in ``corruption_reasons`` so the
            # corruption surfaces to the UI banner even when fallback
            # succeeds.
            try:
                # ``encoding="utf-8"`` is explicit so cross-platform
                # behavior is identical: on Windows the default would be
                # CP1252 and a valid UTF-8 config with non-ASCII bytes
                # in (e.g.) a directory path would raise
                # UnicodeDecodeError before reaching the JSON parser.
                with open(config_path, encoding="utf-8") as f:
                    parsed = json.load(f)
                if not isinstance(parsed, dict):
                    log.warning(
                        "Config file %s root is not a JSON object; ignoring.",
                        config_path,
                    )
                    corruption_reasons.append(
                        f"{config_path}: root is not a JSON object"
                    )
                    continue
                if "data_dir" in parsed:
                    config_data_dir = Path(parsed["data_dir"])
                if "claude_dir" in parsed:
                    config_claude_dir = Path(parsed["claude_dir"])
                if "claude_desktop_app_dir" in parsed:
                    config_claude_desktop_app_dir = Path(
                        parsed["claude_desktop_app_dir"]
                    )
                break
            except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
                log.warning(
                    "Failed to parse config %s: %s. "
                    "Using defaults; fix the file and restart to apply.",
                    config_path,
                    exc,
                )
                # Reason format: path-first so the banner can render the
                # actionable "fix this exact file" line; exception class
                # name preserves the JSONDecodeError "line N column N"
                # detail that's the most actionable signal for the user.
                corruption_reasons.append(
                    f"{config_path}: {type(exc).__name__}: {exc}"
                )
                continue

        # Default data dir: canonical first; legacy as last-resort
        # fallback so a startup that bypassed migration still finds data.
        default_data_dir = canonical_home_dir() / "conversations"
        legacy_data_dir = legacy_home_dir() / "conversations"
        if (
            env_data_dir is None
            and config_data_dir is None
            and not default_data_dir.exists()
            and legacy_data_dir.exists()
        ):
            chosen_default = legacy_data_dir
        else:
            chosen_default = default_data_dir

        data_dir = (
            Path(env_data_dir)
            if env_data_dir
            else config_data_dir
            if config_data_dir
            else chosen_default
        )
        claude_dir = (
            Path(env_claude_dir)
            if env_claude_dir
            else config_claude_dir
            if config_claude_dir
            else Path.home() / ".claude"
        )
        # Cowork app dir: env → config → platformdirs default.
        # ``platformdirs.user_data_path("Claude")`` resolves to the
        # platform-correct Claude Desktop user-data directory
        # (``~/Library/Application Support/Claude`` on macOS, etc.).
        claude_desktop_app_dir = (
            Path(env_claude_desktop_app_dir)
            if env_claude_desktop_app_dir
            else config_claude_desktop_app_dir
            if config_claude_desktop_app_dir
            else platformdirs.user_data_path("Claude")
        )
        return cls(
            data_dir=data_dir,
            claude_dir=claude_dir,
            claude_desktop_app_dir=claude_desktop_app_dir,
            config_corrupt_reason=(
                " | ".join(corruption_reasons) if corruption_reasons else None
            ),
        )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings.load()
