"""Pydantic models for the API."""

from datetime import datetime
from typing import Literal, Any

from pydantic import BaseModel, Field


class ContentBlock(BaseModel):
    """A content block within a message.

    Anthropic content-block linking (``id`` and ``tool_use_id``) is
    preserved through the parse — the MCP ``get_messages`` tool surfaces
    these so an external caller can pair parallel tool_use calls to
    their tool_result back-references. Both fields are optional because
    legacy fixtures and non-tool blocks don't carry them.
    """

    type: str  # text | tool_use | tool_result | image
    text: str | None = None
    name: str | None = None  # for tool_use
    input: dict[str, Any] | None = None  # for tool_use
    content: list["ContentBlock"] | None = None  # for tool_result
    # for type == "image": Claude Code's inline base64 shape is
    # {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}.
    # claude.ai sometimes uses {"type": "image", "source": {"type": "url", "url": "..."}}.
    source: dict[str, Any] | None = None
    # Anthropic content-block IDs. ``id`` is set on ``tool_use`` blocks;
    # ``tool_use_id`` is the back-reference on the matching
    # ``tool_result`` block. Pair calls to results by matching these
    # — positional adjacency is NOT reliable (parallel calls return
    # out of order, some may be missing).
    id: str | None = None
    tool_use_id: str | None = None


class Message(BaseModel):
    """A single message in a conversation.

    Pydantic↔TS contract note (Task B, 2026-05-18): fields with
    Pydantic defaults below (``content``, ``attachments``, ``files``,
    ``files_v2``, ``is_command_marker``, ``is_prelude``,
    ``assistant_canned_response_consumed``) are ALWAYS emitted on
    the wire — the default is applied at construction so the JSON
    key is always present. The frontend ``Message`` interface in
    ``frontend/src/lib/types.ts`` marks several of these as optional
    (``?:``) to keep frontend mock construction friction-free; this
    is a defensive-direction lie (TS wider than runtime). Consumers
    using ``?? false`` / ``?? []`` are safe. Do NOT remove the
    Pydantic defaults to "match TS optionality" — that would change
    the wire shape and require a coordinated TS+BE update.
    """

    uuid: str
    sender: Literal["human", "assistant"]
    text: str
    content: list[ContentBlock] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    truncated: bool = False
    parent_message_uuid: str | None = None
    attachments: list[Any] = Field(default_factory=list)
    files: list[Any] = Field(default_factory=list)
    # claude.ai sometimes ships a v2 array alongside the legacy `files`
    # with overlapping entries. Renderers dedupe by file_uuid.
    files_v2: list[Any] = Field(default_factory=list)
    # CC-only flags (V1 polish, 2026-05-12). Pydantic v2 default is
    # `extra='ignore'`, so these MUST be declared on the model or the
    # backend's `_collapse_local_command_triplets` / fold / prelude-flag
    # passes silently lose their output before it reaches the frontend.
    #   * `is_command_marker`: set by the triplet collapser on the synthetic
    #     `"Session: /foo"` user message that replaces a CC slash-command
    #     triplet. Frontend can style muted.
    #   * `is_prelude`: set by `_flag_leading_prelude_markers` on each
    #     leading `is_command_marker` row. Frontend hides by default.
    #   * `assistant_canned_response_consumed`: set by
    #     `_fold_canned_assistant_responses_into_marker` when CC's literal
    #     `"No response requested."` reply was absorbed into the marker.
    #     Carried for debugging / future UI surfacing; renderers currently
    #     ignore it.
    is_command_marker: bool = False
    is_prelude: bool = False
    assistant_canned_response_consumed: bool = False
    # The slash-command name (e.g. "/coding", "/plan") for command-marker
    # rows, or None for any other message. Surfaced by the
    # `_collapse_local_command_triplets` pass; the frontend renders a
    # muted `<SlashCommandBadge command="/coding" />` above the body when
    # this is set. V1 polish round 3, 2026-05-12: paired with the args-
    # preservation change that puts the user's real prompt text into
    # `Message.text` when the command carried `<command-args>`.
    slash_command: str | None = None


class SubagentSummary(BaseModel):
    """Summary of a subagent conversation."""

    uuid: str
    agent_id: str
    name: str
    model: str = ""
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationListItem(BaseModel):
    """Slim per-row payload for the sidebar list.

    Returned by ``/api/conversations``. Strips ``summary``,
    ``human_message_count``, and ``git_branch`` from the full
    ``ConversationSummary`` shape — those three fields stay on
    ``ConversationSummary`` (and therefore ``ConversationDetail``) for
    the MCP server, the per-conversation detail endpoint, and the
    server-side ``?search=`` matcher that runs on the full shape
    BEFORE this projection.

    Invariant: this model MUST be a strict subset of
    ``ConversationSummary``. The router builds instances via
    ``model_validate(..., from_attributes=True)`` against an existing
    ``ConversationSummary``, so a field added here that does not exist
    on the source model would silently fall back to its default value
    on every row. Enforced by
    ``test_conversation_list_item_split.test_list_item_is_strict_subset_of_summary``.
    """

    uuid: str
    name: str
    model: str = ""
    created_at: datetime
    updated_at: datetime
    is_starred: bool = False
    message_count: int = 0
    has_branches: bool = False
    source: Literal["CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "CLAUDE_AI"
    project_path: str | None = None  # For Claude Code sessions
    project_name: str | None = None  # Short name extracted from project_path
    # Multi-org metadata (cowork-multi-org C3). Null for legacy untagged
    # JSONs that haven't been re-fetched yet.
    organization_id: str | None = None
    organization_name: str | None = None
    subagents: list[SubagentSummary] = Field(default_factory=list)

    def model_post_init(self, context: Any, /) -> None:
        """Compute project_name from project_path after initialization.

        Kept in sync with ``ConversationSummary.model_post_init`` — the
        router projects from a fully-initialized ``ConversationSummary``,
        which has already populated ``project_name`` if applicable, so
        in the normal flow this is a no-op. The fallback is here so the
        invariant holds when callers construct ``ConversationListItem``
        directly (tests, future code paths).
        """
        if self.project_path and not self.project_name:
            path = self.project_path.rstrip("/")
            self.project_name = path.split("/")[-1] if "/" in path else path


class ConversationSummary(BaseModel):
    """Summary of a conversation for list views."""

    uuid: str
    name: str
    # `summary` is Desktop-only auto-generated text and intentionally NOT
    # surfaced in the sidebar UI. It is STRIPPED from the
    # `/api/conversations` wire format by the `ConversationListItem`
    # projection in the router (see backend/routers/conversations.py),
    # but stays on this base model because two public contracts read it:
    #   * MCP `export_session` threads it through to the sliced
    #     ConversationDetail copy (mcp_server/server.py line ~633), and
    #     past iterations of mcp_server/SPEC.md called this out as
    #     schema-stable;
    #   * `/api/conversations?search=` matches against it server-side
    #     (backend/store.py:list_conversations, see "summary_match").
    # See PLANS/SPLIT_CONVERSATION_SCHEMA.md for the audit + split.
    summary: str = ""
    model: str = ""
    created_at: datetime
    updated_at: datetime
    is_starred: bool = False
    message_count: int = 0
    # `human_message_count` is consumed by the MCP server's
    # `list_sessions` tool output (mcp_server/SPEC.md, schema-stable
    # public contract). Stripped from the sidebar wire format by the
    # `ConversationListItem` projection but kept on this model so the
    # MCP path keeps working.
    human_message_count: int = 0
    has_branches: bool = False
    source: Literal["CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK"] = "CLAUDE_AI"
    project_path: str | None = None  # For Claude Code sessions
    project_name: str | None = None  # Short name extracted from project_path
    # `git_branch` is read by the conversation-detail page (see
    # `frontend/src/routes/ConversationPage.tsx` "Details" disclosure).
    # `ConversationDetail extends ConversationSummary`, so this field
    # MUST stay on the base model — splitting it out would require
    # rewriting the detail-page render path. Stripped from the sidebar
    # list wire format by the `ConversationListItem` projection.
    git_branch: str | None = None  # For Claude Code sessions
    # Multi-org metadata (cowork-multi-org C3). Null for legacy untagged
    # JSONs that haven't been re-fetched yet — UI surfaces these under the
    # "Untagged (re-fetch to assign workspace)" group.
    organization_id: str | None = None
    organization_name: str | None = None
    subagents: list[SubagentSummary] = Field(default_factory=list)  # Nested agent conversations
    # D8 (Cowork, 2026-05-25): Cowork sidecar.isArchived. False for
    # Desktop + Claude Code; never user-actionable from the explorer
    # UI (read-only mirror of Desktop's archive state). The store
    # default-hides archived sessions from the sidebar; clients pass
    # ``?show_archived=true`` to override.
    is_archived: bool = False

    def model_post_init(self, context: Any, /) -> None:
        """Compute project_name from project_path after initialization."""
        if self.project_path and not self.project_name:
            # Extract just the folder name from the full path
            # e.g., /Users/you/Source/my-project -> my-project
            path = self.project_path.rstrip("/")
            self.project_name = path.split("/")[-1] if "/" in path else path


class CompactMarker(BaseModel):
    """A /compact event extracted from a Claude Code conversation."""

    message_uuid: str
    summary_text: str
    timestamp: str
    kind: Literal["auto", "manual"]
    user_prompt: str | None = None


class ConversationDetail(ConversationSummary):
    """Full conversation detail including messages."""

    messages: list[Message] = Field(default_factory=list)
    current_leaf_message_uuid: str = ""
    file_path: str | None = None  # Path to the source file (JSON or JSONL)
    compact_markers: list[CompactMarker] = Field(default_factory=list)
    # CC-only count of leading `is_prelude` messages. Frontend uses this to
    # decide whether to render the "Session prelude: N earlier /exit runs"
    # affordance above the message stream. 0 for Desktop conversations and
    # any CC conversation whose first message is not a slash-command marker.
    prelude_hidden_count: int = 0
    # D9 (Cowork, 2026-05-25): sidecar.error, a plain string the
    # Cowork harness writes when a session ended unexpectedly. None
    # when the session ended cleanly. Detail view renders an alert
    # banner above the message stream when non-null. None for
    # Desktop + Claude Code (those don't surface a session-fault
    # field).
    error: str | None = None
    # D10 (Cowork, 2026-05-25): sidecar.cwd, the VM sandbox path
    # (e.g. /sessions/<vm>) the Cowork agent ran under. Rendered as
    # plain text labeled "Sandbox path" in the detail view. Distinct
    # from project_path (which is also set to cwd on Cowork for
    # CC-parity, but the Cowork detail view prefers the labeled
    # sandbox_path render).
    sandbox_path: str | None = None


class MessageNode(BaseModel):
    """A node in the message tree."""

    message: Message
    children: list["MessageNode"] = Field(default_factory=list)


class ConversationTree(BaseModel):
    """Full message tree with all branches."""

    uuid: str
    root_messages: list[MessageNode] = Field(default_factory=list)
    active_path: list[str] = Field(default_factory=list)


class SnippetFragment(BaseModel):
    """One contiguous piece of a search-result snippet.

    Wire-format addition (PLANS/PERFORMANCE_PHASE_2.md §Workstream A):
    the FTS5 fast path returns a list of these so the frontend can
    render highlights without parsing inline HTML and without a
    DOMPurify-style sanitizer dependency. Each fragment is either
    plain text (``mark=False``) or a highlighted match
    (``mark=True``); concatenating ``fragment.text`` in order
    reconstructs the rendered snippet.

    Invariants the producer (FTS5 snippet() parser) maintains:
      * Non-empty ``text`` per fragment.
      * Adjacent fragments alternate marked / unmarked (a
        consumer can rely on no two consecutive marks).
      * The concatenation equals the parent ``MessageSnippet.snippet``
        for any snippet produced by the fast path.
    """

    text: str
    mark: bool


class MessageSnippet(BaseModel):
    """A snippet from a message matching a search.

    Legacy fields ``snippet``, ``match_start``, ``match_end`` stay
    populated on every code path so consumers that haven't switched
    to ``fragments`` keep working (MCP, older frontends, the JSON
    API contract). The new optional ``fragments`` field is populated
    by the FTS5 fast path (``context_size="snippet"``) and is None
    on the linear-scan fallback and on ``context_size="full"``
    responses.
    """

    message_uuid: str
    sender: str
    snippet: str
    match_start: int
    match_end: int
    created_at: datetime | None = None
    # Optional structured highlight fragments (Phase-2 A). When
    # populated, ``"".join(f.text for f in fragments) == snippet``.
    # When None, the legacy ``snippet`` + ``match_start`` + ``match_end``
    # triple is the authoritative highlight signal.
    fragments: list[SnippetFragment] | None = None


class SearchResult(BaseModel):
    """Search result with matching messages."""

    conversation_uuid: str
    conversation_name: str
    conversation_updated_at: datetime
    conversation_created_at: datetime
    project_name: str | None = None
    matching_messages: list[MessageSnippet] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """Wrapped /api/search response with total-match disclosure.

    Wire-format change (PLANS/SEARCH_TOOL_AWARENESS_AND_LIMIT_DISCLOSURE.md
    §B): /api/search now returns this envelope instead of a bare
    ``list[SearchResult]``. The new fields tell the UI (and MCP
    consumers) when the route-level LIMIT clipped the result set so
    they can render "Showing first N of M" instead of silently
    truncating.

    Field semantics:
      * ``results``: the per-conversation rollup. Same shape as the
        legacy bare-list response — clients that want to ignore
        truncation can read ``response.results`` and proceed.
      * ``total_messages_matched``: the exact ``COUNT(*)`` from the
        FTS5 MATCH under the same WHERE clauses as the snippet
        query. Message-level, not conversation-level: FTS5's COUNT
        is naturally at the row (message) level, and "Showing 1,000
        of 12,400 matches" is more honest than "Showing 47 of 200
        conversations" when the difference is what got truncated.
      * ``returned_messages``: the actual number of MessageSnippet
        rows in ``results``, capped at the route-level LIMIT (1000
        for HTTP, 5000 for MCP).
      * ``truncated``: derived as ``returned_messages <
        total_messages_matched``. Carried explicitly so callers don't
        have to recompute.
    """

    results: list[SearchResult] = Field(default_factory=list)
    total_messages_matched: int = 0
    returned_messages: int = 0
    truncated: bool = False


class Org(BaseModel):
    """A workspace (org) the user has access to.

    Wire shape mirrored by the frontend `Org` interface in
    `frontend/src/lib/types.ts`. `name` is nullable because the
    upstream credentials JSON may omit it for legacy entries (see
    `backend/routers/orgs.py:get_orgs`).
    """

    org_id: str
    name: str | None = None
    is_primary: bool


class OrgsResponse(BaseModel):
    """Wrapped `/api/orgs` response.

    Three-state contract documented at
    `backend/routers/orgs.py` module docstring. Pre-Task-B the
    router returned a raw `dict`, leaving the wire shape undocumented
    in the OpenAPI schema; the frontend `OrgsResponse` interface in
    `frontend/src/lib/types.ts` was the only spec. This Pydantic
    model makes BE the single source of truth so the next field
    addition lands in OpenAPI automatically.
    """

    authenticated: bool
    orgs: list[Org] = Field(default_factory=list)


class AccountResponse(BaseModel):
    """``/api/account``: the Anthropic account the credentials belong to.

    The sidebar footer shows it, as claude.ai and Claude Desktop do.

    * ``authenticated=False``: no credentials file; every other field is
      ``None``.
    * ``authenticated=True`` with identity fields set: the lookup succeeded.
    * ``authenticated=True`` with ``error`` set: credentials exist but the
      identity lookup failed (expired session, offline). ``org_name`` still
      comes from the credentials file.

    Mirrored by ``AccountResponse`` in ``frontend/src/lib/types.ts``.
    """

    authenticated: bool
    account_uuid: str | None = None
    email: str | None = None
    full_name: str | None = None
    display_name: str | None = None
    org_name: str | None = None
    error: str | None = None


class AppConfig(BaseModel):
    """Application configuration for the frontend.

    Lightweight: returned by `/api/config`, polled on every page load.
    Anything that requires walking the conversation directory belongs
    on `AppConfigStats` (served by `/api/config/stats`).
    """

    data_dir: str
    # Layer 3 of PLANS/2026.05.18-config-corruption-safe-mode.md.
    #
    # Mirrors ``Settings.config_corrupt_reason`` onto the wire so the
    # frontend banner can render the failure reason verbatim. ``None``
    # when ``config.json`` parsed cleanly (or was absent — fresh
    # install isn't corruption).
    #
    # Always present in the response (Pydantic emits the key even
    # when the value is None) so the frontend's optional-chaining
    # read works without a key-existence check. External API
    # consumers that ignore unknown fields are unaffected.
    config_corrupt_reason: str | None = None


class AppConfigStats(AppConfig):
    """`AppConfig` plus stats that require disk I/O.

    Returned by `/api/config/stats`. Slow on cold cache (~2.5s for ~600
    conversations); call only from screens where the user is willing to
    wait (Settings).
    """

    conversation_count: int


# ---------------------------------------------------------------------------
# Fetch pipeline contracts (council D1 — moved here from
# backend/routers/fetch.py so models.py is the single source of truth for
# wire shapes mirrored by frontend/src/lib/types.ts and api.ts).
# ---------------------------------------------------------------------------


class FetchStatus(BaseModel):
    """Status response for fetch operations.

    Returned by ``GET /api/fetch/status``. Frontend ``getFetchStatus``
    in ``lib/api.ts`` re-declares this shape inline; this model is
    the wire-contract source of truth.
    """

    has_credentials: bool
    credentials_path: str
    output_dir: str
    existing_count: int
    credentials_age_days: int | None = None


class FetchProgress(BaseModel):
    """Progress update during fetch.

    The ``type`` field is a closed ``Literal`` union mirroring the
    frontend ``FetchProgress.type`` in
    ``frontend/src/components/fetch/FetchToast.tsx``. Pre-Task-B
    (2026-05-18) the field was bare ``str``; the frontend had a
    narrow union but the backend would have happily emitted an
    undocumented variant. Tightening locks the contract on both ends.

    Note: SSE events are constructed as raw dicts in
    ``_build_error_event`` and similar helpers, not via this model
    (the dicts carry ``kind`` and ``retryable`` fields that aren't
    part of this base contract). Do NOT pipe SSE dicts through
    ``FetchProgress.model_dump()`` — that would strip the extra
    fields and break the frontend. The model is the contract spec;
    the SSE dict is the wire payload.
    """

    type: Literal[
        "start",
        "progress",
        "complete",
        "error",
        # Build-9: combined capture+fetch pipeline events.
        "capture_start",
        "capture_waiting_login",
        "capture_done",
    ]
    message: str
    current: int = 0
    total: int = 0
    conversation_name: str | None = None


class ForceRefetchResponse(BaseModel):
    """Response shape for ``POST /api/fetch/conversation/{uuid}``.

    Council code-review B6+D1 (2026-05-21): pre-council the route
    returned a bare ``dict`` with no ``response_model=``, so OpenAPI
    documented the response as ``{}``. The Pydantic model surfaces
    the wire contract in /docs and pins it for the FE
    ``forceRefetchConversation`` caller.
    """

    uuid: str
    status: Literal["refetched"]
    name: str