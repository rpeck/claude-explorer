"""Canonical compaction-prompt text — single source of truth.

Leaf module (zero internal imports) so both readers (``cowork_reader``,
``cc_message_transforms``) and infra (``search_index``, ``search``) can
import the same constant without creating a wrong-direction dependency
between layers.

The canonical Claude compaction-prompt is what the runtime injects after
a context-overflow. Cowork's audit.jsonl does NOT carry CC's
``isCompactSummary: true`` field, so for Cowork compaction detection
text-prefix matching is the only reliable detector. The same exact
wording also appears in CC compactions (the synthetic
``isCompactSummary: true`` row's body text starts with this prefix).

Two consumers as of 2026-05-26:

  1. ``backend.cowork_reader._extract_cowork_compact_markers`` —
     detects compaction-summary turns in Cowork audit.jsonl.
  2. ``backend.search_index`` — flags conversations whose TITLE was
     fallback-derived from the compaction-summary text (when a CC
     session starts with a compaction and has no
     ``summary``/``custom-title``/``agent-name`` row, the title
     derivation in ``backend.cc_message_transforms`` falls back to
     the first 100 chars of the first non-system user message, which
     IS the compaction-summary prefix). The ``is_compaction_titled``
     column on the ``conversations`` projection drives the title-
     sweep filter when ``include_compactions=False``.

The gate is ``.lstrip().startswith(prefix)`` (anchored after leading
whitespace) — NOT a substring check, so a regular user message that
quotes the prefix mid-text does not false-positive.
"""

from __future__ import annotations

# Hard-coded so it travels with the leaf module — zero imports keeps
# this safe to import from anywhere (cowork reader, search infra, tests).
COMPACTION_TITLE_PREFIX = (
    "This session is being continued from a previous conversation that "
    "ran out of context."
)


def is_compaction_prefix_text(text: object) -> bool:
    """Return True iff ``text`` starts with the canonical compaction
    prefix after leading whitespace is stripped.

    Anchored (``.lstrip().startswith(...)``) so quoted text mid-message
    cannot false-positive. Returns False for non-string inputs (defensive
    against legacy / partial-write rows where ``text`` may be ``None``
    or a list of content blocks).
    """
    if not isinstance(text, str):
        return False
    return text.lstrip().startswith(COMPACTION_TITLE_PREFIX)
