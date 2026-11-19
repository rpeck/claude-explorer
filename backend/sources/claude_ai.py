"""Claude Desktop and claude.ai chats: the JSON files that the fetcher saves."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import SourceDescriptor, SyncContext, SyncReport, WatchRoot

if TYPE_CHECKING:
    from backend.store import ConversationStore

DESCRIPTOR = SourceDescriptor(
    id="CLAUDE_AI",
    label="Claude Desktop",
    short_label="Desktop",
    chronological=False,
    has_projects=False,
    has_archive_state=False,
    has_organizations=True,
    refetchable=True,
    messages_change_in_place=False,
)


class ClaudeAiAdapter:
    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    def descriptor(self) -> SourceDescriptor:
        return DESCRIPTOR

    def watch_roots(self) -> list[WatchRoot]:
        # The fetch routes reindex after each fetch; nothing watches the data dir.
        return []

    def discover(self) -> list[Path]:
        return self._store._get_conversation_files()

    def list_rows(self, *, full_content: bool, include_phantom: bool) -> list[dict[str, Any]]:
        return self._store._desktop_rows()

    def find(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        return self._store._find_desktop(uuid)

    def load_for_index(self, path: Path) -> dict[str, Any] | None:
        return self._store._load_conversation(path)

    def archive_sync(self, ctx: SyncContext) -> SyncReport | None:
        return None

    def branch_flag(self, data: dict[str, Any], chat_messages: list[dict[str, Any]]) -> bool:
        return self._store._computed_branch_flag(data, chat_messages)
