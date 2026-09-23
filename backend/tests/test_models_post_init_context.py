"""Council code-review D3 (2026-05-21).

The ``model_post_init`` hooks on ``ConversationListItem`` and
``ConversationSummary`` use ``__context`` as the parameter name. The
double-underscore prefix triggers Python name-mangling on access from
within the class body (it becomes ``_ConversationListItem__context``)
and is anti-idiomatic for an unused parameter.

The council voted to rename to ``context`` (no leading underscore — it
matches the documented Pydantic v2 signature). This is safe IFF
Pydantic v2 passes the context positionally, NOT as a keyword argument
named ``__context``. If Pydantic ever called ``model_post_init(self,
__context=ctx)`` by name, the rename would raise ``TypeError: got an
unexpected keyword argument '__context'``.

These tests pin the falsifiable WWCMM:

  1. ``model_validate(data, context={...})`` works against
     ``ConversationSummary`` / ``ConversationListItem``. (Pydantic
     accepts the context kwarg; the dummy context dict is the actual
     contract.)
  2. The post-init STILL derives ``project_name`` from
     ``project_path`` after the rename — semantic preservation.

Per TESTING.md §5.12 (attribute-patch idiom): models.py is
NOT a heavily-patched module; the only existing test that touches
``model_post_init`` is the project_name derivation in
``test_conversation_list_item_split.py``. Safe to refactor.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models import ConversationListItem, ConversationSummary


def _row(project_path: str | None = None) -> dict:
    """Minimal dict that ``model_validate`` accepts for both shapes."""
    return {
        "uuid": "11111111-1111-1111-1111-111111111111",
        "name": "Council D3 fixture",
        "model": "claude-sonnet-4-6",
        "created_at": datetime(2026, 5, 21, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 21, tzinfo=timezone.utc),
        "is_starred": False,
        "message_count": 1,
        "has_branches": False,
        "source": "CLAUDE_CODE",
        "project_path": project_path,
    }


def test_conversation_summary_accepts_context_kwarg_in_model_validate():
    """WWCMM-1: Pydantic ``model_validate(.., context={...})`` works.

    If Pydantic ever propagated the context as a kwarg literally
    named ``__context`` to ``model_post_init``, this would raise
    ``TypeError: got an unexpected keyword argument '__context'``
    (or its mirror after the rename). The fact that this test
    passes against the renamed signature proves Pydantic passes
    context positionally — making the rename safe.
    """
    out = ConversationSummary.model_validate(
        _row("/Users/rpeck/Source/cool-project"),
        context={"trace_id": "trace-abc"},
    )
    assert out.project_name == "cool-project"


def test_conversation_list_item_accepts_context_kwarg_in_model_validate():
    """WWCMM-1 mirror for the skinny list-item shape."""
    out = ConversationListItem.model_validate(
        _row("/Users/rpeck/Source/cool-project"),
        context={"trace_id": "trace-abc"},
    )
    assert out.project_name == "cool-project"


def test_post_init_derives_project_name_from_project_path():
    """WWCMM-2 (semantic preservation): the rename must not change
    the post-init behavior — ``project_name`` is still derived from
    ``project_path``."""
    summary = ConversationSummary(**_row("/a/b/c/deep-project"))
    assert summary.project_name == "deep-project"

    item = ConversationListItem(**_row("/a/b/c/deep-project"))
    assert item.project_name == "deep-project"


def test_post_init_handles_trailing_slash():
    """Boundary: trailing slash should not produce empty project_name."""
    summary = ConversationSummary(**_row("/a/b/c/project-with-slash/"))
    assert summary.project_name == "project-with-slash"


def test_post_init_handles_no_path_separator():
    """Boundary: a project_path with no ``/`` should yield itself."""
    summary = ConversationSummary(**_row("bare-name"))
    assert summary.project_name == "bare-name"


def test_post_init_preserves_explicit_project_name():
    """Existing explicit ``project_name`` is NOT overwritten by the post-init."""
    data = _row("/Users/rpeck/Source/cool-project")
    data["project_name"] = "explicit-override"
    summary = ConversationSummary.model_validate(data)
    assert summary.project_name == "explicit-override"


def test_post_init_handles_none_project_path():
    """Boundary: ``project_path=None`` leaves ``project_name=None``."""
    summary = ConversationSummary(**_row(None))
    assert summary.project_name is None
