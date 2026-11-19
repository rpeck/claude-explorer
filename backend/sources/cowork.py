"""Cowork: local sessions of Claude Desktop, and remote tasks that the fetcher saves."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import SourceDescriptor, SyncContext, SyncReport, WatchRoot

if TYPE_CHECKING:
    from backend.store import ConversationStore

DESCRIPTOR = SourceDescriptor(
    id="CLAUDE_COWORK",
    label="Claude Cowork",
    short_label="Cowork",
    chronological=True,
    has_projects=False,
    has_archive_state=True,
    # Remote tasks carry the workspace of their fetch.
    has_organizations=True,
    refetchable=False,
    messages_change_in_place=False,
)


class CoworkAdapter:
    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    def descriptor(self) -> SourceDescriptor:
        return DESCRIPTOR

    def watch_roots(self) -> list[WatchRoot]:
        # The local root only: the remote archive changes through the fetch.
        return [WatchRoot(self._store.cowork_root, ("audit.jsonl",), create=True)]

    def discover(self) -> list[Path]:
        return self._store._discover_cowork()

    def list_rows(self, *, full_content: bool, include_phantom: bool) -> list[dict[str, Any]]:
        return self._store._cowork_rows(full_content)

    def find(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        return self._store._find_cowork(uuid)

    def load_for_index(self, path: Path) -> dict[str, Any] | None:
        return self._store._load_cowork_for_index(path)

    def archive_sync(self, ctx: SyncContext) -> SyncReport | None:
        return None

    def branch_flag(self, data: dict[str, Any], chat_messages: list[dict[str, Any]]) -> bool:
        # Computed as for Desktop. Cowork messages have no parent links, so a
        # row with 2 or more messages reads true; a later leaf fixes that.
        return self._store._computed_branch_flag(data, chat_messages)
