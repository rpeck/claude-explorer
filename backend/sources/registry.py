"""The source registry: a descriptor and an adapter factory per source.

The order is behavior: the store lists the sources, and asks them for a
uuid, in registry order (CLAUDE_AI, CLAUDE_CODE, CLAUDE_COWORK, CODEX).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeAlias

from . import claude_ai, claude_code, codex, cowork
from .base import SourceAdapter, SourceDescriptor
from .ids import SourceId

if TYPE_CHECKING:
    from backend.store import ConversationStore

AdapterFactory: TypeAlias = Callable[["ConversationStore"], SourceAdapter]

_BUILT_INS: tuple[tuple[SourceDescriptor, AdapterFactory], ...] = (
    (claude_ai.DESCRIPTOR, claude_ai.ClaudeAiAdapter),
    (claude_code.DESCRIPTOR, claude_code.ClaudeCodeAdapter),
    (cowork.DESCRIPTOR, cowork.CoworkAdapter),
    (codex.DESCRIPTOR, codex.CodexAdapter),
)
_registry: dict[str, tuple[SourceDescriptor, AdapterFactory]] = {}


def register_adapter(descriptor: SourceDescriptor, factory: AdapterFactory) -> None:
    """Add a source, or replace the one with the same id. ``factory`` takes the store."""
    _registry[descriptor.id] = (descriptor, factory)


def reset_for_tests() -> None:
    """Restore the full, ordered built-in registry."""
    _registry.clear()
    for descriptor, factory in _BUILT_INS:
        register_adapter(descriptor, factory)


def get_descriptors() -> tuple[SourceDescriptor, ...]:
    return tuple(descriptor for descriptor, _ in _registry.values())


def get_descriptor(source_id: str) -> SourceDescriptor | None:
    entry = _registry.get(source_id)
    return entry[0] if entry is not None else None


def get_adapters(store: ConversationStore) -> tuple[SourceAdapter, ...]:
    """New adapters bound to ``store``, in registry order."""
    return tuple(factory(store) for _, factory in _registry.values())


def get_adapter(source_id: SourceId, store: ConversationStore) -> SourceAdapter:
    """The adapter of ``source_id``. Raises ``KeyError`` for an unknown id."""
    return _registry[source_id][1](store)


def get_adapter_or_none(source_id: str, store: ConversationStore) -> SourceAdapter | None:
    entry = _registry.get(source_id)
    return entry[1](store) if entry is not None else None


reset_for_tests()
