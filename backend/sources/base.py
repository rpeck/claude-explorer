"""The source adapter protocol and the source descriptor.

PLANS/2026.11.18-codex-source.md §4.1 and
PLANS/reviews/2026.11.18-codex-source/council_n1_adapter.md. An adapter
is bound to one ``ConversationStore``, which owns the roots and the caches.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .ids import SourceId

# ``backend.archive_sync.SyncContext`` and ``SyncReport``. Not imported, also
# not under TYPE_CHECKING: the MCPB closure analyzer reads that block, and
# archive_sync pulls in portalocker, which the bundle gets only in N7.
SyncContext = Any
SyncReport = Any


@dataclass(frozen=True)
class SourceDescriptor:
    """What the UI, search, and MCP need to know about a source.

    The descriptor has exactly these fields; it is the JSON contract of the
    frontend. Backend-only rules are adapter methods.
    """

    id: SourceId
    label: str
    short_label: str
    #: No branch tree; messages are in file order.
    chronological: bool
    #: Project grouping and the project and path filters apply.
    has_projects: bool
    #: The Archived toggle applies.
    has_archive_state: bool
    #: The workspace filter applies.
    has_organizations: bool
    #: The Re-download button applies.
    refetchable: bool
    #: A message can change without a change in the count, so the MCP
    #: outline cache rebuilds in full.
    messages_change_in_place: bool


@dataclass(frozen=True)
class WatchRoot:
    """A folder that the watcher observes for one source."""

    path: Path
    patterns: tuple[str, ...]
    #: The watcher may create a missing root. A root in another tool's
    #: home (Codex) uses ``False``.
    create: bool


class SourceAdapter(Protocol):
    def descriptor(self) -> SourceDescriptor: ...

    def watch_roots(self) -> list[WatchRoot]: ...

    def discover(self) -> Iterable[Path]:
        """The conversation files, which the index stores as ``indexed_files.path``."""
        ...

    def list_rows(self, *, full_content: bool, include_phantom: bool) -> list[dict[str, Any]]:
        """Every conversation of the source. ``full_content`` adds the
        messages (search, export); else only the list-row fields."""
        ...

    def find(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        """The conversation for the viewer, export, and MCP. May use caches."""
        ...

    def load_for_index(self, path: Path) -> dict[str, Any] | None:
        """One conversation for the search index; ``path`` comes from ``discover()``."""
        ...

    def archive_sync(self, ctx: SyncContext) -> SyncReport | None:
        """``None`` for a source that copies nothing yet."""
        ...

    def branch_flag(self, data: dict[str, Any], chat_messages: list[dict[str, Any]]) -> bool:
        """The ``has_branches`` value of a list row."""
        ...
