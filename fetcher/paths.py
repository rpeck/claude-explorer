"""Shared default paths for the fetcher package.

Extracted from ``fetcher/bulk_fetch.py``, ``fetcher/credentials.py``,
and ``fetcher/migrate_to_v2.py`` (Council A5-PATHS, 2026-05-21) to
remove the drift risk from defining the same
``Path.home() / ".claude-explorer" / ...`` literals across multiple
modules.

Each old definition site re-exports the constant from this module so:

  * ``from fetcher.credentials import DEFAULT_CREDENTIALS_PATH`` keeps
    working (backend imports rely on this).
  * Test patches at ``fetcher.bulk_fetch.DEFAULT_CREDENTIALS_PATH``,
    ``fetcher.credentials.DEFAULT_CREDENTIALS_PATH``,
    ``backend.routers.fetch.DEFAULT_CREDENTIALS_PATH``,
    ``backend.routers.orgs.DEFAULT_CREDENTIALS_PATH`` (see
    ``backend/tests/conftest.py``) continue to take effect at each
    site because Python's attribute-patch semantics work on the
    module's namespace, and each module's namespace re-binds the name
    to its own attribute via ``from .paths import ...``.

Backend ignores these defaults; backend's data-dir resolution goes
through ``backend.config.Settings.data_dir`` (which reads the
``CLAUDE_EXPLORER_DATA_DIR`` env var). This module is the fallback
default for the CLI / fetcher only.
"""

from __future__ import annotations

from pathlib import Path

#: Root dir for all user-facing state owned by claude-explorer.
DEFAULT_CONFIG_DIR: Path = Path.home() / ".claude-explorer"

#: Captured Claude Desktop session credentials. Written by
#: ``fetcher.credentials.save_credentials`` with mode 0o600.
DEFAULT_CREDENTIALS_PATH: Path = DEFAULT_CONFIG_DIR / "credentials.json"

#: Where fetched conversation JSONs land (one file per conversation,
#: optionally subdivided into ``by-org/<uuid>/`` for multi-org fetches).
DEFAULT_DATA_DIR: Path = DEFAULT_CONFIG_DIR / "conversations"

#: Where attached files (images, PDFs) downloaded during fetch land.
DEFAULT_FILES_DIR: Path = DEFAULT_CONFIG_DIR / "files"

#: Subdirectory of the conversations data dir that holds remote Cowork
#: sessions (tasks run in Anthropic's cloud), laid out as
#: ``<org_uuid>/remote_<session_id>/audit.jsonl`` plus a sibling sidecar.
#: Written by ``fetcher.remote_sessions``; read by ``backend.cowork_reader``.
REMOTE_COWORK_DIRNAME = "cowork-remote"

#: Directory-name prefix of a remote Cowork session. Local Cowork sessions
#: use ``local_``.
REMOTE_SESSION_PREFIX = "remote_"


__all__ = [
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_CREDENTIALS_PATH",
    "DEFAULT_DATA_DIR",
    "DEFAULT_FILES_DIR",
    "REMOTE_COWORK_DIRNAME",
    "REMOTE_SESSION_PREFIX",
]
