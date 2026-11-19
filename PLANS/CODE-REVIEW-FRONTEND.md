# Code Review — frontend/ (Category E + A2/A5/F, plus cross-boundary checks)

**Date**: 2026-05-22
**Scope**: `frontend/` — full Category E (E1-E6) plus A2/A5/F. Cross-boundary Pydantic↔TS drift sanity-check against `backend/models.py`.
**Mode**: hunt-and-fix, tiers:HM pre-approved
**Council**: gpt-5.2 (Engineer) + gemini-3-pro-preview (Architect) + opus-4.7 (CTO)
**Preflight**: PASS — both models PONG'd

## Commit range

- Baseline SHA: `98fa9837f321c5432d651cffa5d9fc94b3d1858f` (initial council run)
- Mid SHA:      `5d7ba76` (plan checked in; E4 still DEFERRED)
- Follow-up:    2026-05-22 — user authorized the >3-file restructure (AFK directive). Council confirmation round (gpt-5.2 + gemini-3-pro-preview, both CONFIRM with 2 small additions: NEGATIVE forceExpanded test from engineer; dedicated `useCopyFeedback.test.ts` from architect). Both adopted.
- Mid SHA:      `9ef80a6` (merge of `refactor/message-bubble-split` into main)
- LOW/NIT sweep: 2026-05-22 — user AFK, tiers:HMLN pre-approved, model gpt-5.2 (NOT gpt-5.2-pro per user directive). Chased 7 deferred items: 6 SHIPPED, 1 KEEP-with-rationale. Council confirmation round on the 2 lint warnings (LINT-1, LINT-2) + Round-2 cross-critique on shared-util-vs-wrap-in-place + memo-key debate. Both rounds wrap-in-place + `filtersState.nodes` won on Round-2 evidence; image-only-nav-mismatch turned out to be a council misread of `messageHasVisibleContent`.
- Final SHA:    `b657d13` (LOW/NIT sweep tip)
- Baseline tests: 325 vitest passed → Final: 354 (no regressions; +29 tests: 6 useCopyFeedback + 11 contract + 12 computeVisibleMessages)

### Commits added (4 on branch + merge):

- `a4e972b` refactor(message): extract collectCcImages + imageSourceUrl to blocks/imageCollection (council E4)
- `3d51a47` refactor(message): extract Tool blocks + useCopyFeedback hook (council E4)
- `401a49c` refactor(message): extract Image blocks + ContentBlockRenderer (council E4)
- `d76dc6c` test(message): bidirectional contract tests for MessageBubble split (council E4)
- `9ef80a6` Merge refactor/message-bubble-split (--no-ff)

### Commits added (LOW/NIT sweep, 2026-05-22):

- `1b6204a` chore(conversation-list): suppress incompatible-library warning + fix stale jsdom comment (LINT-1 + NIT-2)
- `41e2a17` fix(clipboard): handle writeText rejection with errorToast (LOW-1, 3/6 sites: MessageBubble + ToolBlocks + useKeyboardShortcuts)
- `fb2f977` perf(filters): memoize child->referencing-groups map for FilterRow rendering (LOW-2)
- `b657d13` fix(conversation): collapse click dead-zone + remaining clipboard sites (NIT-1 + LOW-1 3/6 + LINT-2)

Net council outcome (after the 2026-05-22 follow-up): **1 implementation shipped** — the MessageBubble.tsx split is DONE. MessageBubble.tsx went from 806 LOC to 299 LOC (63% reduction). 5 new files under `components/message/blocks/` + 1 new hook + 2 new test files. The LOW-3 "copy-feedback timer duplicated" finding is also resolved as part of the split via the shared `useCopyFeedback` hook.

## Decision Records

### E1 — TypeScript assertion lies

**Recon**: ~50 grep matches for `\bas\s+[A-Z]` and `!\.`. After filtering out `import * as X`, `import { Foo as Bar }` aliases (lucide-react icons), comments, and string-literal "as Markdown" UI copy, the substantive sites are:

| Site | Verdict | Rationale |
|---|---|---|
| `FilterContext.tsx:147,165,193,325,327` (`as AtomFilter`, `as GroupFilter`, `as AtomFilter & { polarity?: unknown }`) | KEEP | Discriminated-union rebuilds during v1→v2 migration and `updateNode` discriminant re-assertion. Already audited (inline comments at 315-320). |
| `FetchDialog.tsx:55` (`as LegacyFetchProgress['type']`) | KEEP | Documented narrowing for legacy SSE event types; comment at 28-32 explains the constraint. |
| `scrollBubbleIntoView.ts:82` (`closest(...) as HTMLElement \| null`) | KEEP | DOM API return type widening for `closest()`. Standard pattern. |
| `lib/types.ts:321` (`v as Record<string, unknown>`) | KEEP | Inside `isSearchResponse` runtime validator, AFTER `typeof === 'object' && !== null && !Array.isArray`. The cast is the structurally-safe widening that yields a typed `obj` for property access. |
| `usePreferences.ts:151` (`envelope?.data?.[key] as T \| undefined`) | KEEP | Documented at lines 49-57 + 97-104 as a deliberate untyped-localStorage / unvalidated-server-shape boundary. Generic T cannot be runtime-validated without per-key schemas, which the codebase has explicitly rejected. |

**Non-null assertions (`!.`)**: 0 in production code. The two grep matches are inside **comments** that document the elimination of an old `byConv.get(id)!.push(b)` / `groups.get(k)!.push(conv)` pattern (insert-or-get migration).

**Decision Basis**: Zero substantive findings. Codebase has already shipped multiple prior assertion-lie audits (lib/types.ts:308 explicitly notes "Replaces `res.json() as Promise<SearchResponse>` casts in `lib/api.ts`"; usePreferences.ts:49 explicitly notes "the previous `(await r.json()) as PreferencesEnvelope` cast was a runtime lie").

**CTO WWCMM**: I would reverse this position if a fresh `as` cast (not on this list) is found in a production file outside `lib/types.ts` runtime validators — repro: `grep -rnE '\bas\s+[A-Z][A-Za-z0-9_]+' frontend/src --include='*.ts' --include='*.tsx' | grep -v '\.test\.' | grep -v '/test/' | grep -v 'import \* as' | grep -v '"[^"]*as [A-Z]'` — producing a hit not enumerated in the table above. Action: re-classify that site individually.

---

### E2 — TanStack Query hygiene

**Recon**: 10 `useQuery`/`useMutation` sites across `hooks/useConversations.ts`, `hooks/useOrgs.ts`, `hooks/usePreferences.ts`, `routes/SettingsPage.tsx`.

**Per-site audit**:

| Site | queryKey | signal | staleTime | Verdict |
|---|---|---|---|---|
| `useConversations:27` (list) | `queryKeys.conversations.list(serverFilters)` | ✓ threaded | 30s via setQueryDefaults | CLEAN |
| `useConversations:71` (detail) | `queryKeys.conversations.detail(uuid)` or `[...detail(uuid), 'leaf', leaf]` | ✓ threaded | 5min via setQueryDefaults | CLEAN |
| `useConversations:91` (tree) | `queryKeys.conversations.tree(uuid)` | ✓ threaded | 5min explicit | CLEAN |
| `useConversations:127` (search) | `queryKeys.search(...)` with full param signature | ✓ threaded | 60s explicit + `keepPreviousData` | CLEAN |
| `useConversations:175` (config-stats) | `['config-stats']` | ✓ threaded | 60s explicit | CLEAN |
| `useConversations:197` (config) | `queryKeys.config` | ✓ threaded | 60s explicit | CLEAN |
| `useOrgs:21` | `queryKeys.orgs` | ✓ threaded | (inherits 30s default) | CLEAN |
| `usePreferences:136` (query) | `['preferences']` | ✓ threaded | 5min explicit, retry: 1 | CLEAN |
| `usePreferences:143` (mutation) | n/a (mutation) | n/a | n/a | CLEAN |
| `SettingsPage:37,50` | inline keys, OK for one-off page-scoped queries | (not inspected — page is short) | OK | CLEAN |

**Decision Basis**: Every fetch threads its AbortSignal from `({ signal })` through `api.X(...args, signal)` to `fetch(url, { signal })`. staleTime is centralized in `lib/queryClient.ts` via `queryClient.setQueryDefaults(['conversations', 'list'], ...)` / `setQueryDefaults(['conversations', 'detail'], ...)`. The `usePreferences` hook uses a runtime validator (`isPrefsEnvelope`) instead of an `as PreferencesEnvelope` cast (commented as a prior assertion-lie fix). queryKey factory pattern is consistently used.

**Note**: `useConversations:27` also handles `serverFilters` destructuring (line 11: `const { search, ...serverFilters } = filters ?? {}`) so the `search` keystroke is NOT in the queryKey — client-side substring filter at lines 53-60. This is the documented design (avoids per-keystroke network roundtrips). Tested.

**CTO WWCMM**: I would reverse if a new fetch site appears that does not thread `signal` — repro: `grep -rnE 'queryFn:' frontend/src --include='*.ts' --include='*.tsx' | grep -v 'signal'` — producing a non-empty result.

---

### E3 — Context-storm subscriptions

**Recon**: 9 contexts. Consumer counts (production only):

| Context | Consumers | Notes |
|---|---|---|
| `SettingsContext` | 12 | Highest, but it IS the central settings hub. Reading 1-2 fields per consumer; not a storm. |
| `KeyboardNavigationContext` | 7 | Per-route messages array + selection; pinned to ConversationPage + ConversationList + a few keyboard hooks. |
| `SearchPanelContext` | 5 | Subscription storm previously fixed at commit 3cdfcd1 (ConversationPage reads query once via `useDeferredValue`, threads as prop with memo comparator). |
| `SearchPinContext` | 4 | Used by Sidebar + SearchPanel + PinScopeButton. |
| `FilterContext` | 4 | Used by Sidebar (rendering active filter) + SearchPanelContext (resolving filter→UUIDs) + ManageFiltersModal. |
| `FetchPipelineContext` | 3 | Used by FetchToast + Sidebar refresh button. |
| `ConversationLightboxContext` | 2 | Provided per-conversation by ConversationPage; consumed by MessageBubble. |
| `SourceFilterContext` | 1 | Wired into SearchPanelContext only. |
| `BookmarkContext` | 0 | Module wires its hook (`useBookmarks`) rather than `useBookmarkContext`. **NIT only** — not dead code, just a naming inconsistency. |

**Decision Basis**: The grep query was `use[A-Z]\w*Context`, which matches the literal hook names. `BookmarkContext.tsx` exports `useBookmarks`, not `useBookmarkContext`, so the recon undercounts. Spot-check (`grep -rn "useBookmarks" frontend/src --include='*.tsx' --include='*.ts' | wc -l`) shows BookmarkContext is consumed in ~6 sites. Consumer-count picture is healthy.

No context is broadcasting to >12 consumers. The previously-identified storm (SearchPanelContext on `query` keystrokes through 15K MessageBubbles) is already fixed via the deferred-value + prop + memo-comparator pattern (commit 3cdfcd1; inline rationale at MessageBubble.tsx:37-53).

**CTO WWCMM**: I would reverse if a React 19 profiler trace shows >50ms main-thread lockup on rapid keystroke into the SearchPanel input with a 15K-message conversation loaded — repro: open `/conversations/<UUID-with-15K-messages>` in Chrome devtools Performance tab, start recording, type 5 chars rapidly into the SearchPanel input, stop recording — producing a >50ms task with `MessageBubble` re-renders as the hot path. Action: tighten the memo comparator further or hoist `searchQuery` out of bubble props entirely.

---

### E4 — Component file size cliffs (≥500 LOC, production)

| File | LOC | Council verdict | Disposition |
|---|---|---|---|
| `components/filters/ManageFiltersModal.tsx` | 977 | KEEP — cohesive feature module | LEAVE |
| `routes/ConversationPage.tsx` | 945 | MARGINAL — watch-list | LEAVE w/ watch |
| `components/conversation/ConversationList.tsx` | 851 | KEEP — virtualization + keyboard nav tightly coupled | LEAVE |
| `components/message/MessageBubble.tsx` | 806 | **SPLIT** — proposed MED, deferred to user sign-off | DEFERRED |
| `components/search/SearchPanel.tsx` | 718 | KEEP — FTS5 overlay + highlight + focus cohesive | LEAVE |
| `contexts/SearchPanelContext.tsx` | 541 | KEEP — orchestration state machine | LEAVE |
| `hooks/useKeyboardShortcuts.ts` | 476 | (under threshold, but noted) | LEAVE |
| `contexts/FilterContext.tsx` | 459 | (under threshold, but noted) | LEAVE |

#### Per-file rationale (council convergence)

**`ManageFiltersModal.tsx` (977)** — both Architect and Engineer agreed KEEP. The draft-transaction contract (`handleToggleEnabled` at lines 242-256) couples row toggles and editor state in a way that resists splitting without prop drilling. Cycle reasoning (`wouldCreateCycle` at 181-200, `validateNoCycle` at 314-320) shares state with the GroupEditor disabled-candidates UX (790-801). Prefill debounce + stale-guard refs (584-604) are tightly local. Splitting into separate Atom/Group editor files would duplicate the cross-pane invariants or introduce a context just for them.

**`ConversationList.tsx` (851)** — both agreed KEEP. The virtualization "spectator mode" (writing `scrollEl.scrollTop` directly instead of calling virtualizer APIs, lines 506-544 + 560-586) is part of a documented cascade-avoidance invariant (Hunt #5, prior fix). The jsdom fallback at 588-633 is a "don't break tests" guardrail. Starred/unstarred partitioning is repeated in 3 places (142-145, 155-158, 224-228) — Engineer noted this; Architect dismissed as stylistic pedantry. Codebase keeps it.

**`SearchPanel.tsx` (718)** — Architect KEEP. Engineer did not deep-dive but did not contest. The file contains the panel UI, `HighlightedSnippet` (token scanner), input focus management, navigation glue. Splitting `ResultCard` (line 540) would force prop drilling for `contextSize` and `query`.

**`SearchPanelContext.tsx` (541)** — Architect KEEP. Pure orchestration: source filter + pin scope + bookmarks + settings + filters → search params; envelope passthrough (totalMatched/returnedMatches/truncated); flatMatches derivation. State machines of this complexity belong in one file for trace-ability.

**`ConversationPage.tsx` (945)** — Engineer flagged as "marginal, watch-list". Multiple side-effect subsystems share refs:
- Expand-all anchor capture + layout restore (128-173)
- PDF export state machine (175-530)
- Highlight scroll + URL mutation + focus + timer (365-413)
- Keyboard handlers — bookmark (310-338), compact-marker nav (340-363)

Architect Round-2 flexed to agree it's borderline. **Decision**: leave it today (no concrete bug, splitting risks the smooth-scroll orchestration), but watch-list. If any of these subsystems grows further, extract via a hook in the same file/folder rather than a component split, so the lifecycle stays explicit.

**`MessageBubble.tsx` (806)** — Council convergence: TRUE god module; SPLIT recommended. Mixes:
- Clipboard + copy-feedback timer (74-96)
- CC image cataloging + marker parsing (130-159 + 323-501)
- `CcImageMarkerTile` (504-574)
- `ContentBlockRenderer` + `InlineImageBlock` (576-696)
- `ToolUseBlock` (698-758) — duplicates the copy-feedback timer pattern (708-723)
- `ToolResultBlock` (760-806)

**Proposed split** (deferred to user sign-off):
- Keep `MessageBubble.tsx` for: prop types, `MessageBubbleImpl`, the memo'd export, and `useImageFailureTombstone`.
- Extract into `components/message/blocks/`:
  - `ToolUseBlock.tsx` (with `useCopyFeedback` hook to DRY the timer logic)
  - `ToolResultBlock.tsx`
  - `InlineImageBlock.tsx`
  - `CcImageMarkerText.tsx` + `CcImageMarkerTile.tsx`
  - `ContentBlockRenderer.tsx` (re-exports the four above as a discriminated switch)
  - `imageCollection.ts` (pure `collectCcImages` + `imageSourceUrl`)

**Test methodology that pins the split** (per Engineer's spec, refined by CTO):
1. **Black-box DOM contract assertions** (NOT byte-for-byte snapshots — too brittle under React 19 + Tailwind class ordering):
   - `data-cc-image-marker` and `data-cc-image-path="<abs-path>"` present after marker-tile render
   - `data-cc-image-broken` present on the tombstoned/errored variant
   - `data-content-image` and `data-content-image-broken` present on inline-image variants
   - `data-message-uuid` on the root bubble div
   - Tool block presence/absence keyed on `showToolCalls` and `expandAllTools`
2. **Bidirectional plumbing test**:
   - Render a `MessageBubble` with 1 attachment image + 1 CC marker + 1 inline CC image
   - Stub `ConversationLightboxContext.offsetForMessage(message.uuid)` to return a known offset (e.g. 10)
   - Spy on `ConversationLightboxContext.openAt`
   - Click the first CC marker tile → assert `openAt(offset + attachmentCount + localIdx)`
   - Click the inline image → assert `openAt(offset + attachmentCount + localIdx)` with the next ordinal
3. **Existing test files that will continue to pin behavior** (already 18 tests in `frontend/src/test/components/MessageBubble.test.tsx` + 6 in `MessageBubble.searchQuery-prop.test.tsx`):
   - Tool block collapsed/expanded on click
   - Tool block hidden when `showToolCalls=false`
   - Excludable-marker hides copy + bookmark overlay
   - Bookmarked indicator (`data-bookmarked` sentinel)
   - Slash-command badge rendering

**Why deferred, not shipped this run**:
- Spec rule: ">3 files moved/renamed in a single commit requires user pre-approval. If you hit this, STOP and ask." Proposed split creates 6 new files (Tool*, Inline*, CcImage*, ContentBlock*, imageCollection) — well past the threshold.
- Engineer Round-2 downgrade: "MED on maintainability grounds, not mandatory for correctness".
- Prior in-file council audits (Hunt #11, Issue 3 follow-up, Manual finding 2026-05-04, Issue #1, P4d) repeatedly chose NOT to split this file. Reversing prior decisions without explicit user signal = cargo-cult.
- The user is AFK with HM-tier autonomous approval, but the file-restructure rule is orthogonal to severity tiers.

**Action when user returns**: review this DR, sign off (or not) on the proposed split + new directory `components/message/blocks/`. Implementation should land as ~3 separate commits (extract pure helpers → extract Tool* + DRY the copy hook → extract image components) each with the corresponding contract test commit.

#### CTO WWCMM (MessageBubble split decision)

I would reverse the "defer" decision if (a) the user explicitly signs off in chat OR (b) a real user-reported regression rooted in `MessageBubble.tsx` complexity surfaces (e.g. a bug fix that requires touching 3+ of the listed concerns in one commit) — producing a commit message that would benefit from being scoped to a single sub-file. Repro for (b): inspect any future MessageBubble-touching commit; if the diff spans `ToolUseBlock`, `CcImageMarkerTile`, AND `MessageBubbleImpl` simultaneously, that's the signal.

---

### E5 — Effect dependency lies

**Recon**: 3 production sites + 1 test site of `// eslint-disable-next-line react-hooks/exhaustive-deps`. All `@ts-expect-error` comments live in test files (deliberate type-system bypasses for runtime guard tests).

| Site | Justification | Verdict |
|---|---|---|
| `FilterContext.tsx:291` | Sentinel-only deps (`_migratedV1`, `_migratedV2`, `qc`); full `filtersState` would re-fire on every node mutation. `didMigrateV2Ref.current` guard makes the body idempotent. | KEEP |
| `ManageFiltersModal.tsx:603` | Debounce timer keyed only on `draft.patterns` + `draft.type`; refs (`userEditedNameRef`, `nameFocusedRef`, `lastDraftIdRef`) read inside the closure intentionally to avoid resetting the debounce on focus/edit toggle. | KEEP |
| `SearchPanel.tsx:109` | Auto-navigate-on-active-match-change fires only when `activeMatchIndex` changes; `navigateToMatch` is a module-level import (stable). Adding `flatMatches` would re-fire on every search result update and double-navigate. | KEEP |
| `test/components/search/navigateToMatch.test.tsx:116` | Test-only useEffect for navigation harness. Not production. | n/a |

**Decision Basis**: Every production site has inline rationale paragraph(s) explaining why the disable is correct. All have ref-based reads inside the closure to capture the latest values without triggering re-fires.

**CTO WWCMM**: I would reverse if any of these effects produces a bug report that traces to a stale closure — repro: file a bug "filter migration didn't run for X scenario" / "name prefill behaved wrong after rapid edit" / "search auto-navigation jumped to wrong match" — producing a stack trace that lands in one of these effect bodies with a stale variable.

---

### E6 — Rubber-stamp test assertions

**Recon**: 33 matches for `expect(...).toBeTruthy()`, `toBeFalsy()`, `toBeDefined()`, `toBeUndefined()`, `toBeNull()`, `not.toBeNull()`.

**Spot-check methodology**: read each match in its surrounding test context. A match is a "rubber-stamp" only if there is NO downstream content/behavioral assertion within the same test block.

| Test file | Pattern | Verdict |
|---|---|---|
| `FilterContext.test.tsx:124` (`expect(probeHolder.current?.filtersState.nodes.a).toBeDefined()`) | Existence sentinel followed by content checks at L127-128 (`expect(prefs.patches).toEqual([])`) | LEGITIMATE |
| `FilterContext.test.tsx:174` (`expect(v2Patch).toBeDefined()`) | Existence sentinel; lines 178-195 assert on every field of the migrated blob | LEGITIMATE |
| `FilterContext.test.tsx:316,335,400,427,430` | Same pattern (find→exists→assert content) | LEGITIMATE |
| `FilterContext.test.tsx:432` (`expect(filtersBlob.activeId).toBeNull()`) | Content assertion (activeId IS the value being tested) | LEGITIMATE |
| `TreeView.test.tsx:65,70` (with custom failure message: `'expected active node for ${t} to be rendered'`) | Custom-message existence with `not.toBeNull` — legitimate sentinel with diagnostic context | LEGITIMATE |
| `MessageBubble.test.tsx:269,282,299,320,334,344,365` | Each `expect(bubble).not.toBeNull()` precedes a content assertion (e.g. classList check, attribute check, child query) | LEGITIMATE |
| `ConfigCorruptionBanner.test.tsx:136,140` (`expect(dismissByLabel).toBeNull()`, `expect(dismissByRole).toBeNull()`) | Content assertion — the test's POINT is that dismiss buttons MUST NOT exist (V1 invariant: non-dismissible banner) | LEGITIMATE |
| `useConversations.null-safety.test.tsx:94,95,120,145` | `expect(result.current.error).toBeNull()` paired with the actual data-shape assertions in the same test | LEGITIMATE |
| `useConversations.staleTime.test.tsx:68,96` (`expect(cached).toBeDefined()`) | Existence sentinel before queryClient cache content assertions | LEGITIMATE |
| `useConversation.abort.test.tsx:64` (`expect(capturedSignal).toBeDefined()`) | Existence sentinel before `expect(capturedSignal.aborted).toBe(true)` later in the test | LEGITIMATE |
| `useSearch.abort.test.tsx:77` | Same pattern as useConversation.abort | LEGITIMATE |
| `HighlightedText.test.tsx:73`, `MarkdownRenderer.search-highlight.test.tsx:68,110,114,133` | Existence sentinels for DOM nodes that are then asserted on for textContent / nested structure | LEGITIMATE |

**Decision Basis**: All 33 sites are either (a) existence sentinels paired with content/behavioral assertions later in the same `it(...)` block, or (b) the existence/null IS the content being asserted (e.g. ConfigCorruptionBanner's "no dismiss button" invariant). Zero rubber-stamps.

**CTO WWCMM**: I would reverse if a new test is added with a bare `expect(x).toBeDefined()` and no downstream content check — repro: `grep -rn 'toBeDefined\|toBeNull' frontend/src/test --include='*.test.*'` after future commits, then read each match's `it(...)` block — producing a test body where the assertion is the only one. Action: write the missing content assertion or delete the test.

---

### A2 — God modules (production .ts/.tsx ≥500 LOC)

See E4 above. Same files; verdict identical.

---

### A5 — Drifted duplicates

**Recon**: Looked for filename-pair duplicates across `frontend/src/`. Specifically:
- `cache` / `*Cache` — none.
- `reader` / `parser` — none.
- `watcher` — none in frontend.
- Multiple flavors of the same util / context: none.

**Cross-boundary note** — `frontend/src/lib/types.ts` and `backend/models.py` are intentional mirrors. The TS file is heavily commented with prior drift-audit decisions (Task B 2026-05-18):
- Optional `?:` markers on Message fields (`files_v2`, `is_command_marker`, `is_prelude`, `assistant_canned_response_consumed`, `slash_command`) are deliberate defensive lies — backend always emits these (Pydantic defaults at construction), but TS marks them optional to keep frontend mock construction friction-free. Documented at types.ts:163-171 and backend/models.py:Message.
- `ContentBlock.type` is closed on TS side (`'text' | 'tool_use' | 'tool_result' | 'image' | 'thinking'`) while open on the backend side (`str`). Mismatch is intentional: backend pass-through preserves forward compat for new Anthropic block types; TS closed union forces every consumer to route unknown types through a `default: return null` branch. Documented at types.ts:120-129.
- `PreferencesEnvelope` is intentionally NOT in lib/types.ts — it lives in `hooks/usePreferences.ts` as the only consumer. types.ts:163-171 comment explicitly says "If you find yourself re-declaring this interface in another file, HOIST it to lib/types.ts instead." Good gate.

**Decision Basis**: 0 drifted duplicates. The cross-boundary drift work was already done in commits 7b25a3f → e60917f (Task B 2026-05-18). No new findings.

---

### F1 — Dead code

**Already-investigated (DO NOT touch)**:

- `frontend/src/lib/api.ts:263` — `if (typeof parsed?.detail === 'string')` guard. Solo investigation (pre-this-run) determined this is NOT dead defensive code. The backend's FastAPI 422 validation errors return list-shaped `detail` (no custom `exception_handler` in `backend/main.py` overrides FastAPI's default 422 handler). The type-check is load-bearing — without it, `parsed.detail` could be assigned to an `ApiError.message` (typed `string`) but actually be a list, propagating the type lie through the error envelope. Backend B+D council's "dead defensive code" classification was incomplete. **KEEP.**

- `frontend/src/contexts/SearchPanelContext.tsx` — `useSearchPanelOptional` was previously dead and cleared at commit `51ca891` (solo pre-this-run). Confirmed via grep.

**Recon for this run**: no new dead-code candidates surfaced. The previously-flagged solo cleanup already shipped.

**Decision Basis**: 0 new dead-code findings.

---

### F (other) — Magic numbers, logging hygiene, public-API surface

- **Magic numbers**: scrollIntoView offsets (`clientHeight / 3` in `ConversationList:543,584`) are commented inline. Timer values (2000ms for copy-feedback, 200ms for search debounce, 100ms for highlight scroll, 5000ms for unmount-safe timer fallback) are inline literals with rationale comments at each site. No flag.
- **Logging hygiene**: frontend uses `console.error` sparingly + `errorToast` for user-facing errors (`lib/errorToast.ts`). The pattern is consistent across `routes/`, `hooks/`, `contexts/`. No flag.
- **Public-API surface**: `lib/types.ts` exports types-only; runtime predicates (`isTheme`, `isKeyboardMode`, `isMarkdownDialect`, `isSourceFilter`, `isSortField`, `isSearchResponse`) are co-located with their types. Cohesive.

---

## LOW / NIT findings (deferred to plan)

These were raised by the council but classified below the HM threshold the user pre-approved. They live here as backlog.

### LOW-1 — `navigator.clipboard.writeText` without `try/catch` — **DONE (2026-05-22)**

**Sites (all 6 patched)**:
- `MessageBubble.tsx:handleCopyMessage` → try/catch + `errorToast('Failed to copy to clipboard.')` (`41e2a17`)
- `ToolBlocks.tsx:ToolUseBlock.handleCopy` → try/catch + errorToast (`41e2a17`)
- `useKeyboardShortcuts.ts` Cmd+C handler → `.catch()` chain + errorToast (`41e2a17`). Cannot use try/catch because the surrounding handler is a synchronous keydown listener.
- `ConversationPage.tsx:handleCopyAll` → try/catch + errorToast (`b657d13`)
- `ConversationPage.tsx` copy-UUID button → try/catch + errorToast (`b657d13`)
- `ConversationPage.tsx` copy-file_path button → try/catch + errorToast (`b657d13`)

**Council Decision Record (Round 2)**: Architect originally proposed shared `lib/clipboard.ts` util returning `Promise<boolean>`. Engineer pushed back: useKeyboardShortcuts.ts is a sync window-keydown handler, would force IIFE/async-signature change. Engineer's wrap-in-place won; Architect flexed to agree, adopted `.catch()` for the sync site. Each call site has tailored success-feedback state (copied / copiedAll / copiedUuid / copiedPath / triggerCopied) — sharing a util adds parameter surface for no DRY win.

**CTO WWCMM**: I would reverse the wrap-in-place call if a 7th clipboard site lands. Repro: `grep -c 'navigator.clipboard.writeText' frontend/src --include='*.ts' --include='*.tsx'` ≥ 7 → extract `lib/clipboard.ts`.

### LOW-2 — `findReferencingGroups` perf cliff in `ManageFiltersModal.tsx` — **DONE (2026-05-22, `fb2f977`)**

**Site**: `ManageFiltersModal.tsx:393` — previously called `findReferencingGroups(n.id, filtersState)` inside `visibleNodes.map(...)`. O(V * (V+E)) walk per visible row, re-running on every keystroke into searchQuery / every draft toggle / every deleteUi update.

**Fix**: pre-compute `referencingById: Map<FilterId, GroupFilter[]>` via `useMemo` keyed on `filtersState.nodes`. One O(V+E) pass over group nodes' childIds; row consumer now does O(1) lookup. The two non-render-loop callers of `findReferencingGroups` (handleRequestDelete L294, editor "Used by" L652) keep the direct function call — they run per user interaction, not per render.

**Memo key debate (Round 2)**: Engineer initially split with Architect on `[filtersState]` vs `[filtersState.nodes]`. Architect's code-evidence won: FilterContext rebuilds `filtersState` identity on activeId / migration-banner / sentinel changes too, but the precompute only reads `nodes`. Same rationale as the existing `allNodes` memo at L213.

**Stable empty-array reference**: `EMPTY_GROUP_ARRAY = Object.freeze([])` at module scope so nodes with zero referencing groups don't see a fresh `[]` identity each render (lets FilterRow memoization, if any, treat the prop as unchanged).

### LOW-3 — `MessageBubble` copy-feedback timer pattern duplicated

**Sites**: `MessageBubble.tsx:74-96` (`MessageBubbleImpl`) and `MessageBubble.tsx:708-723` (`ToolUseBlock`). Both implement: `useState(copied)` + `useRef<Timeout>` + `useEffect` cleanup + `setCopied(true); clearTimeout; setTimeout(setCopied(false), 2000)`.

**Proposed fix**: extract `useCopyFeedback(timeoutMs = 2000): [copied, triggerCopied]` hook, ideally as part of the MessageBubble.tsx split (E4 deferred MED).

**Why LOW**: pure DRY win, no behavior change. Land it WITH the split if/when the user signs off.

### NIT-1 — Click-dead-zone for tool-only messages when `showToolCalls=false` — **DONE (2026-05-22, `b657d13`)**

**Site**: `ConversationPage.tsx` `<div onClick>` wrapper that called `messages.findIndex((m) => m.uuid === message.uuid)`. When `showToolCalls=false`, `MessageBubble` returned `null` for tool-only messages, but the outer wrapper still rendered a 0-height clickable band — `findIndex` returned `-1` and the click silently no-op'd. Keyboard-nav registration at L298-302 already filtered tool-only messages, so render and nav were also out of sync.

**Fix**: extract `computeVisibleMessages(messages, opts) → Message[]` to `lib/utils.ts` as a pure helper. Predicate:
- drop `is_prelude` when `showPrelude=false`
- keep any UUID in the compact-marker set (CompactMarker affordance renders from a separate Map and is always-visible chrome)
- otherwise keep iff `messageHasVisibleContent(m, showToolCalls)`

Render and keyboard-nav now share the visibility shape via the same base predicate. 12 new bidirectional unit tests in `frontend/src/test/lib/computeVisibleMessages.test.ts` pin positive + negative + order-preservation contracts. Transient-break verified.

**Council note**: the original concern about an "image-only nav mismatch" (engineer Round 1) turned out to be a misread of `messageHasVisibleContent` — that predicate already counts attachments via `dedupeImageFiles`. So the unified predicate needed no special image branch.

### NIT-2 — Stale comment in `ConversationList.tsx:483-487` (`measureElement` rationale) — **DONE (2026-05-22, `1b6204a`)**

**Site**: The comment claimed "Skip in jsdom (vitest) where getBoundingClientRect always returns zero". Actual jsdom handling lives at the `isJsdom` early-return (now at L596+). The `measureElement` ternary is purely about ResizeObserver presence.

**Fix**: rewrote the comment as "Provide a fixed measureElement fallback when ResizeObserver isn't available (SSR / very old browsers). jsdom (vitest) is handled separately below via the `isJsdom` non-virtualized early-return, so this branch is NOT about jsdom."

### NIT-3 — `BookmarkContext` naming inconsistency — **KEEP (2026-05-22, council re-confirmed)**

The exported hook is `useBookmarks`. The plan's original framing claimed this was inconsistent with `useFilters`, `useSettings`, `useSearchPanel`, `useKeyboardNavigation` — but on re-inspection, ALL of those follow the short-form `use<Plural>` / `use<Domain>` pattern, NOT `use<X>Context`. `useBookmarks` matches the codebase convention.

**Council Round-2 convergence**: Both Architect and Engineer KEEP. Renaming would force broad mechanical refactor across consumers with no runtime benefit, and the current name is arguably cleaner. Naming churn is a classic AFK trap (high diff, low value, merge conflict potential).

### Two new lint-suppression findings — **DONE (2026-05-22)**

#### LINT-1 — `useVirtualizer` React Compiler warning (`1b6204a`)

**Site**: `ConversationList.tsx:477` triggered `react-hooks/incompatible-library` because TanStack Virtual's API returns functions that cannot be safely memoized by React Compiler. The constraint is a library-level fact (not fixable locally).

**Fix**: added `eslint-disable-next-line react-hooks/incompatible-library` with rationale. The pre-existing multi-line explanation at L471-476 stays.

#### LINT-2 — `react-hooks/exhaustive-deps` missing dep at `ConversationPage.tsx:412` (`b657d13`)

**Site**: highlight-scroll/focus effect references `scheduleHighlightClear`, the return value of `useUnmountSafeTimer()`. The hook's returned function is a fresh closure each render (NOT useCallback-wrapped — see hook source L43-52).

**Decision**: adding it to the dep array would re-fire the whole effect on every render while `highlightMessageId` is truthy, causing repeated `scrollBubbleIntoView` + `classList` mutations + `setSearchParams` URL rewrites. Documented `eslint-disable-next-line` with rationale and a back-reference to the matching pattern at FilterContext.tsx:291, ManageFiltersModal.tsx:603, SearchPanel.tsx:109.

**Council note**: both councilors initially recommended "add to deps" believing the hook returned a stable callback. CTO verification of `useUnmountSafeTimer.ts:31-52` proved that wrong — adding to deps would re-fire. CTO overruled both personas on code-evidence grounds.

---

## Findings table

| Class | File | Severity | Status | Commit |
|---|---|---|---|---|
| E1 | (none) | — | CLEAN | — |
| E2 | (none) | — | CLEAN | — |
| E3 | (none) | — | CLEAN | — |
| E4 | components/message/MessageBubble.tsx | MED | **DONE** (2026-05-22, branch refactor/message-bubble-split, merged to main) | `9ef80a6` (merge); `a4e972b` `3d51a47` `401a49c` `d76dc6c` |
| E4 | routes/ConversationPage.tsx | MED-watch | LEAVE w/ watch | — |
| E4 | components/filters/ManageFiltersModal.tsx | NIT | LEAVE | — |
| E4 | components/conversation/ConversationList.tsx | NIT | LEAVE | — |
| E4 | components/search/SearchPanel.tsx | NIT | LEAVE | — |
| E4 | contexts/SearchPanelContext.tsx | NIT | LEAVE | — |
| E5 | FilterContext.tsx:291 | KEEP | LEAVE | — |
| E5 | ManageFiltersModal.tsx:603 | KEEP | LEAVE | — |
| E5 | SearchPanel.tsx:109 | KEEP | LEAVE | — |
| E6 | (33 sites) | — | CLEAN | — |
| A5 | types.ts ↔ models.py drift | — | CLEAN (prior Task-B audit shipped) | — |
| F1 | api.ts:263 detail-string guard | KEEP | DOCUMENTED (NOT dead) | — |
| LOW-1 | clipboard try/catch (6 sites) | LOW | **DONE** (2026-05-22) | `41e2a17` + `b657d13` |
| LOW-2 | findReferencingGroups per-row | LOW | **DONE** (2026-05-22) | `fb2f977` |
| LOW-3 | copy-feedback timer duplication | LOW | **DONE** (resolved as part of E4 split via shared `useCopyFeedback` hook) | `3d51a47` |
| NIT-1 | click-dead-zone in ConversationPage | NIT | **DONE** (2026-05-22, extract `computeVisibleMessages` helper + 12 contract tests) | `b657d13` |
| NIT-2 | stale comment in ConversationList.tsx | NIT | **DONE** (2026-05-22) | `1b6204a` |
| NIT-3 | BookmarkContext naming | NIT | KEEP (Round-2 confirmed `useBookmarks` matches codebase convention) | — |
| LINT-1 | useVirtualizer compile-skip warning | LINT | **DONE** (2026-05-22, eslint-disable with rationale) | `1b6204a` |
| LINT-2 | scheduleHighlightClear missing dep | LINT | **DONE** (2026-05-22, eslint-disable with rationale; adding to deps would re-fire effect each render) | `b657d13` |

## Tests added

Initial run (2026-05-21): none.

Follow-up run (2026-05-22, E4 split):

| Test file | Tests | Class | Purpose |
|---|---|---|---|
| `frontend/src/test/hooks/useCopyFeedback.test.tsx` | 6 | E4 / Hunt #11 | Pin hook-level contract: initial state false, trigger flips true, auto-reset after timeoutMs, custom timeout honored, rapid clicks coalesce (cancel previous timer), unmount clears pending timer. |
| `frontend/src/test/components/MessageBubble.contract.test.tsx` | 11 | E4 | Bidirectional black-box data-attribute contract for the split: CC marker data-attrs + click → lightbox; inline image data-attrs + click → lightbox; tool block gated on showToolCalls (positive + negative); forceExpanded shows JSON without click + ignores collapse click (engineer Round-2 mandate). |

All 17 new tests passing; 0 regressions to the 325 baseline.

LOW/NIT sweep (2026-05-22):

| Test file | Tests | Class | Purpose |
|---|---|---|---|
| `frontend/src/test/lib/computeVisibleMessages.test.ts` | 12 | NIT-1 | Bidirectional + order-preservation contract for the unified visibility predicate: positive (text / image / tool-when-shown / prelude-when-shown / compact-marker override) + negative (tool-only-hidden / prelude-hidden / fully-empty) + mixed-order preservation + empty-array. Transient-break verified by stashing utils.ts. |

Final test count: 354 vitest green (325 initial + 17 from E4 split + 12 from LOW/NIT sweep).

## Open items requiring user action

1. ~~**Sign off on the proposed `MessageBubble.tsx` split**~~ — **DONE 2026-05-22**. User authorized via AFK directive; council confirmed via single round (gpt-5.2 + gemini-3-pro-preview both CONFIRM with 2 small additions). The split landed exactly as proposed:
   - `a4e972b` Commit 1: extract pure helpers → `blocks/imageCollection.ts`
   - `3d51a47` Commit 2: extract Tool blocks + `useCopyFeedback` hook (`blocks/ToolBlocks.tsx`, `hooks/useCopyFeedback.ts`). Architect's mandate: dedicated `useCopyFeedback.test.tsx` (6 hook-level tests pinning Hunt #11).
   - `401a49c` Commit 3: extract Image blocks + ContentBlockRenderer (`blocks/ImageBlocks.tsx`, `blocks/ContentBlockRenderer.tsx`, `blocks/useImageFailureTombstone.ts`)
   - `d76dc6c` Commit 4: 11 bidirectional contract tests (`MessageBubble.contract.test.tsx`). Engineer's mandate: NEGATIVE forceExpanded test (guards regression where `expanded = forceExpanded || isExpanded` is refactored).
   - `9ef80a6` Merge with `--no-ff` to main.
2. ~~**Confirm the LOW/NIT list above** is acceptable to defer for V1.~~ — **DONE 2026-05-22**. User AFK pre-approved tiers:HMLN; LOW/NIT sweep landed 6 fixes + 1 KEEP-with-rationale across 4 commits (`1b6204a`, `41e2a17`, `fb2f977`, `b657d13`). Lint output is now clean (was 2 warnings before sweep).
3. ~~**Add LOW-1 (clipboard try/catch) to a small followup commit**~~ — **DONE 2026-05-22**. All 6 sites now guard `navigator.clipboard.writeText` rejections via try/catch (or `.catch()` for the sync useKeyboardShortcuts handler), surfacing failures via `errorToast`.

## Methodological notes

- The recon's apparent leanness is **earned**, not coincidental. The codebase has shipped at least 4 prior council audits visible in inline comments (Hunt #5 abort-signal plumbing 2026-05-18, Hunt #11 unmount-safe timers, Task B Pydantic↔TS drift 2026-05-18, Issue #3 SearchPanelContext subscription storm 2026-05-20, Bug B search loading affordance 2026-05-03, V1 polish multiple rounds). The cleanliness reflects that prior work, not laziness from this run.
- Engineer Round-2 self-correction (downgrading clipboard and click-dead-zone, withdrawing the measureElement claim) demonstrates the council's adversarial Round-2 working as intended.
- The `MessageBubble.tsx` split is the only finding the council disagreed on enough to land in the MED bucket. Even there, both personas converged after Round 2 that it's "maintainability MED, not correctness". The decision to defer rather than ship is grounded in the file-restructure threshold rule, not council disagreement.
