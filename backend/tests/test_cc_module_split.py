"""Tests for the four-module split of ``backend.claude_code_reader``.

The historical 1540-line ``backend/claude_code_reader.py`` was split into:

  * ``backend.cc_jsonl_io`` — raw JSONL streaming reads + path discovery.
  * ``backend.cc_message_transforms`` — message-pipeline transforms
    (triplet collapse, canned-response fold, prelude flag, streaming-
    chunk merge, text/title helpers).
  * ``backend.cc_image_markers`` — compact-marker extraction.
  * ``backend.cc_agent_reader`` — agent-session discovery + metadata.

``backend.claude_code_reader`` is now a thin facade that re-exports the
public symbols (and a few private ones consumed by tests / monkeypatch
seams) at the same names they had pre-split, so every existing caller
keeps working unchanged.

These tests are intentionally LOW-LEVEL: they assert the module split
itself — not the runtime behavior of any single function. Behavior tests
live in the dedicated ``test_*`` files for each domain (compact markers,
canned-response fold, local-command triplets, claude-code titles, etc.)
and continue to import from ``backend.claude_code_reader`` to exercise
the facade re-exports in passing.
"""

from __future__ import annotations


def test_four_new_modules_importable() -> None:
    """Each of the four extracted submodules must be importable on its own."""
    import backend.cc_agent_reader
    import backend.cc_image_markers
    import backend.cc_jsonl_io
    import backend.cc_message_transforms  # noqa: F401


def test_facade_reexports_public_symbols() -> None:
    """Every name external callers grep for must still resolve through
    ``backend.claude_code_reader``.

    Sourced from ``grep -rn 'from backend.claude_code_reader' backend/ tests/``
    at the time of the refactor. If a future caller imports a NEW symbol
    from the facade, add it here so the next refactor catches the binding.
    """
    from backend.claude_code_reader import (  # noqa: F401
        # Private symbols that production code or tests reach into.
        _LOCAL_CMD_ARGS_RE,
        _PROCESS_POOL_THRESHOLD,
        # Constants
        DEFAULT_CLAUDE_DIR,
        LOGIC_VERSION,
        _flag_leading_prelude_markers,
        _fold_canned_assistant_responses_into_marker,
        _load_conversation_cached,
        # Re-exported helper from backend.parsing (test_parsing.py asserts
        # identity with `parsing.parse_datetime`).
        _parse_datetime,
        _read_summaries_parallel,
        build_agent_index_with_summaries,
        # Public API
        discover_jsonl_files,
        extract_compact_markers,
        list_claude_code_conversations,
        parse_jsonl_file,
        read_agent_summary,
        read_agent_summary_fast,
        read_claude_code_conversation,
        read_conversation_summary_fast,
    )


def test_logic_version_shape_unchanged() -> None:
    """``LOGIC_VERSION`` is a 16-hex-char sha256 prefix and stays a str.

    The value is allowed to change across refactors (any whitespace
    change inside ``read_conversation_summary_fast`` perturbs the
    hash), but the SHAPE is part of the contract used by
    :meth:`backend.summary_cache.SummaryCache.clear_on_logic_mismatch`.
    """
    from backend.claude_code_reader import LOGIC_VERSION

    assert isinstance(LOGIC_VERSION, str)
    assert len(LOGIC_VERSION) == 16
    assert all(c in "0123456789abcdef" for c in LOGIC_VERSION)


def test_parse_datetime_identity_through_facade() -> None:
    """``claude_code_reader._parse_datetime`` is the SAME object as
    ``backend.parsing.parse_datetime`` (test_parsing.py asserts this too,
    but we re-assert here so the binding is part of the split contract).
    """
    from backend import claude_code_reader, parsing

    assert claude_code_reader._parse_datetime is parsing.parse_datetime


def test_read_summaries_parallel_lives_on_facade() -> None:
    """``_read_summaries_parallel`` MUST be patchable at
    ``backend.claude_code_reader._read_summaries_parallel`` because
    ``test_lifespan_cold_start.py`` patches it there. Equivalently:
    the orchestration in ``list_claude_code_conversations`` resolves the
    name from the facade's namespace at call time.
    """
    from backend import claude_code_reader

    # The attribute exists on the facade module object (not just
    # importable through it). This is the property mock.patch needs.
    assert hasattr(claude_code_reader, "_read_summaries_parallel")
    assert callable(claude_code_reader._read_summaries_parallel)


def test_process_pool_threshold_monkeypatchable_on_facade() -> None:
    """``test_summary_cache.py`` does
    ``monkeypatch.setattr(ccr, "_PROCESS_POOL_THRESHOLD", 3)``;
    the constant must live on the facade module so the assignment
    rebinds the name the caller resolves.
    """
    from backend import claude_code_reader

    assert hasattr(claude_code_reader, "_PROCESS_POOL_THRESHOLD")
    assert isinstance(claude_code_reader._PROCESS_POOL_THRESHOLD, int)
