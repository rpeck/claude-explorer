# Claude Opus 5.5 — proposed behaviors to test

Black-box. Each row names a public surface. Group tags `[G:...]` mark rows that can share one fixture or be one parametrized test.

## Phase 1 — discovery and graph (`build_graph`, `GET /api/conversations/{uuid}/subagents`)

| ID | Behavior | Layer | Fixture | Exact assertion | Must NOT happen | Ref |
|---|---|---|---|---|---|---|
| O1 | Legacy agent with `sessionId` belongs to `<sessionId>.jsonl` | unit | legacy-with-sid | node `owner_session_uuid` = that stem | node under another session | §4.1 [G:layouts] |
| O2 | Legacy agent without `sessionId` is listed in `diagnostics` as unknown owner | unit | legacy-no-sid | `diagnostics.unknown_owner` holds its file; no node in any session | node invented under a session | §4.1 [G:layouts] |
| O3 | Current-layout agent with `toolUseId` links `exact` to that call | unit | current-tooluse | `link_quality=exact`, `tool_use_id` = meta value, `spawned` edge from session | prompt match used | §4.1 [G:layouts] |
| O4 | Current-layout agent without `toolUseId` links by unique prompt | unit | current-no-tooluse | `link_quality=prompt_match`, edge to the one matching call | — | §4.1 [G:layouts] |
| O5 | Two Agent calls with the same prompt give `ambiguous` with both candidates | unit | dup-prompt | `link_quality=ambiguous`, `link_candidates` = both `tool_use_id`s, no `spawned` edge | an edge to either call | R3 |
| O6 | Nested agent (`parentAgentId`) has caller = parent agent | unit | nested | `caller_kind=agent`, `caller_id` = parent id; edge parent→child | edge session→child | §4.1, R7′ |
| O7 | Nested agent link starts `partial` and becomes `complete` after caller evidence loads | unit | nested | before: `evidence=partial`; after opening the caller: `evidence=complete`, `tool_use_id` set | — | R10′ |
| O8 | Workflow run gives a `workflow` node, `launched_workflow` edge, and `member` edges | unit | workflow | `run_id` = dir name; member set = journal `started` agentIds | workflow agents as direct session children | §4.2 [G:layouts] |
| O9 | `SendMessage` to a done agent adds a `continued` edge and a second invocation | unit | resumed | 2 invocations (`spawn`, `continue`); 1 `continued` edge | a second agent node | §4.3 |
| O10 | `continued` edges never change ranks | unit | resumed+nested | node ranks equal with and without `continued` edges | — | R6 |
| O11 | Internal runs (`acompact-*`, `aside_question-*`) are `is_internal=true` and not counted | unit | internal-only | `subagent_count=0`, `internal_run_count`=N | internal run in `subagent_count` | R19′ [G:counts] |
| O12 | A session that spans both layouts joins by agent ID | unit | spans-layouts | union of both sets; no duplicate IDs | duplicate node | §8.1 [G:layouts] |
| O13 | Duplicate ID in both layouts: current wins, diagnostics entry | unit | spans-layouts-dup | one node from current layout; `diagnostics` names the ID | two nodes | §8.1 |
| O14 | Malformed meta / journal / transcript is skipped with a diagnostics entry; the rest of the graph is intact | unit | malformed | graph has all good nodes; `diagnostics.parse_failures` names each bad file | exception, 500, empty graph | R4 [G:malformed] |
| O15 | Unknown layout under `subagents/` gives a diagnostics entry | unit | unknown-layout | `diagnostics.unknown_layouts` non-empty | silent zero | R4 |
| O16 | Agent of another session never links even with an equal prompt | unit | two-sessions-same-prompt | agent stays in its own session | cross-session edge | R7′ |
| O17 | Old `Task` tool name is treated like `Agent` | unit | task-name | node linked; `subagent_ref` on the `Task` block | generic tool view for `Task` | §8.1 [G:oldshapes] |
| O18 | Each of the 5 result shapes gives the right outcome and usage | unit | five-shapes | table-driven: `completed`/`interrupted`/`launched`/`unknown`; usage fields mapped | wrong outcome | §8.1 [G:oldshapes] |
| O19 | A result without `agentId` still pairs with its call by `tool_use_id` | unit | no-agentid-unlinked | call shows outcome+usage while agent is in "Unlinked" | outcome lost | S1 |
| O20 | Notification + tool result for one invocation count once | unit | sync+notif | usage totals = one source | doubled totals | R9 |
| O21 | Notification that fires twice for one task-id: newest status per invocation | unit | double-notif | invocation outcome = later notification | earlier status shown | R9 |
| O22 | Unknown raw status is kept as text and gets the neutral color | unit+e2e | odd-status | `outcome` = raw text; chip has neutral class | success color | §8.4 |

## Phase 1 — counts, cache, list path

| ID | Behavior | Layer | Fixture | Exact assertion | Must NOT happen | Ref |
|---|---|---|---|---|---|---|
| O23 | `subagent_count` and `internal_run_count` on `/api/conversations` for every fixture | route | all | values equal the fixture manifest | — | [G:counts] |
| O24 | Warm list path opens no agent file | perf/unit | 295-agent | open-file spy: 0 opens under `subagents/` and no legacy agent file | any agent-file open | R8′ |
| O25 | Add a new run under an existing `workflows/` dir → count changes on next call | unit | workflow | count +k | stale count | R8′ [G:invalidation] |
| O26 | Create / delete / replace an agent file → count and graph change | unit | current | each step changes the result | stale result | R8′ [G:invalidation] |
| O27 | Change only a meta file / only a journal → graph changes | unit | current, workflow | the changed field shows | stale graph | R8′ [G:invalidation] |
| O28 | Missing→present `subagents/` dir is detected | unit | none→current | count from 0 to N | stays 0 | R8′ [G:invalidation] |
| O29 | Graph evidence change invalidates the enriched detail payload | route | current | `subagent_ref` changes after a meta change, session file untouched | stale ref | S2 |
| O30 | List path median and p90 within 5% of T0 baseline | perf | real corpus | measured over 10 runs | — | §5.14 |
| O31 | First list call after `LOGIC_VERSION` bump ends before the frontend request timeout | perf | 3,100-session corpus + fixture | wall time < timeout; counts present | timeout | S5 |

## Phase 1 — routes and safety

| ID | Behavior | Layer | Fixture | Exact assertion | Must NOT happen | Ref |
|---|---|---|---|---|---|---|
| O32 | Graph route returns a body that validates against `SubagentGraph` | route | each layout | parse real body; model validation passes | — | R5 [G:wire] |
| O33 | Agent detail route returns `ConversationDetail` with `uuid` = session, `agent_id` set | route | current | fields equal; messages from the agent file | slash IDs | R5, R11 [G:wire] |
| O34 | Agent's own Agent calls carry `subagent_ref` | route | nested | nested block has ref to child | — | step 1.2 |
| O35 | Bad `agent_id` → 422; unknown → 404; `../` → 404 | route | current | status codes | 200, 500 | R12, R17 [G:safety] |
| O36 | Symlinked agent file that points outside the project → 404 | route | symlink-escape | 404; outside file never read | content served | R12 [G:safety] |
| O37 | `<output-file>` value is never opened | unit | notif with path to a sentinel file | sentinel atime/opens unchanged (spy) | open of that path | R12 [G:safety] |
| O38 | `include_subagents=true` returns the old `subagents` list shape (superset) | route | legacy + current | each item has old keys; includes current-layout agents | empty list | S3 [G:compat] |
| O39 | Default list call does not build the deprecated list | perf/unit | 295-agent | spy: no graph build on default call | — | S3 |

## Phase 1 — enrichment and notifications

| ID | Behavior | Layer | Fixture | Exact assertion | Must NOT happen | Ref |
|---|---|---|---|---|---|---|
| O40 | `subagent_ref` on `Agent`/`Task`/`SendMessage`/`Workflow` blocks | route | mixed | each block has the right id and this invocation's outcome | ref on other tools | §4.6 |
| O41 | Merged messages carry `message_id` = Anthropic `message.id` | route | any | field equals raw id | — | R5 |
| O42 | Notification row parsed only if text starts with the tag AND task-id is known | unit | notif-known, notif-unknown, user-text-mentions-tag | known → `task_notification`; others raw | parse of a user's quoted XML | R12 [G:notif] |
| O43 | Malformed notification stays raw text | unit | notif-malformed | `task_notification` absent; raw text intact | exception | R12 [G:notif] |
| O44 | Enrichment does not change the cached parsed conversation | unit | any | deep-equal before/after | mutation | S2 |
| O45 | Search results and exports unchanged by enrichment | compat | any | search JSON and Markdown byte-equal before/after | — | §8.3 |

## Phase 1 — steps endpoint

| ID | Behavior | Layer | Fixture | Exact assertion | Must NOT happen | Ref |
|---|---|---|---|---|---|---|
| O46 | `is_error` true/false/null from raw rows | unit | errors-mixed | three values as in the file | null shown as false | §4.7 [G:steps] |
| O47 | `elapsed_ms` = result row time − call row time; null if a time is missing | unit | timing | exact ms; null case | — | §4.7 [G:steps] |
| O48 | Parallel calls share `parallel_group`; no-`message.id` calls never group | unit | parallel | groups as in file | null-group merge | S10 [G:steps] |
| O49 | Pages break at batch boundaries; oversized batch → one summary item | unit | oversized-batch | no batch split across pages; summary count = batch size | split batch | S16 [G:steps] |
| O50 | `aggregates` cover the whole transcript | unit | 426-call | aggregate totals = whole file, any page | page-only totals | §4.7 [G:steps] |
| O51 | `from=end` returns the last page; cursors walk back and forward without gaps or repeats | unit | 14,485-call root | union of pages = all steps once | gap, repeat | S19 [G:steps] |
| O52 | `turn_index` counts only real user prompts | unit | turns-with-tool-results | index increments only on prompts | increment on tool_result rows | §4.7 [G:steps] |
| O53 | `target` set for Agent/Task (agent_id) and Workflow (run_id) | unit | mixed | values | — | §4.7 [G:steps] |

## Phase 1 — frontend (unit + e2e; every e2e asserts zero unexpected console errors)

| ID | Behavior | Layer | Fixture | Exact assertion | Must NOT happen | Ref |
|---|---|---|---|---|---|---|
| O54 | Agent block header shows type, description, outcome, tools, time, tokens; unknown shows "—" | unit | mocks | DOM text | "0" for unknown | step 1.4 |
| O55 | Prompt renders as Markdown; "Raw input" shows JSON | unit | mocks | heading/list elements; JSON on toggle | — | step 1.4 |
| O56 | "Show steps" loads one page only when opened | e2e | 426-call | 0 steps requests before click; 1 after | request on render | step 1.4 |
| O57 | Launch line has "Open agent" that opens the agent route | e2e | async | URL = agent route | — | R16 |
| O58 | Notification row renders as "Subagent finished" card, not "You" bubble; "Raw" shows XML | e2e | async | card text; no "You" label | XML visible by default | step 1.4 |
| O59 | Agent route: breadcrumb, header, transcript; breadcrumb back scrolls to `#tool-<id>` with highlight | e2e | nested | URL, scroll position, highlight class | — | step 1.5 |
| O60 | Bot badge shows `subagent_count`; click opens `?panel=agents`; no agent rows in list | e2e | current | badge text; URL; no agent rows | agent rows | R15 |
| O61 | Activity toggle visibility: internal-only yes; tools-only yes; chat-only no; `?panel=agents` on chat-only shows the empty message | e2e | 4 sessions | visibility per case | hidden for internal-only | S17′ [G:entry] |
| O62 | Four switch views match the table | e2e | nested+tools | node/step sets per combo | — | S15 [G:switches] |
| O63 | Subagents switch disabled only when both counts are 0 | unit | tools-only, internal-only | disabled / enabled | disabled for internal-only | S17′ |
| O64 | Focus: anchor change, breadcrumb, no auto Tool calls, steps request only for the focused agent, exit restores view | e2e | nested | DOM + network | steps for descendants | S11 [G:focus] |
| O65 | `focus=<unknown>` shows recoverable message | e2e | any | message + back link | empty graph | S11 [G:focus] |
| O66 | Graph never holds more than 200 step nodes; "Next page" replaces | e2e/perf | 14,485 root | node count ≤ 200 after N pages | growth | S16 |
| O67 | Only one expanded scope at a time | unit | nested | expanding B collapses A | two scopes | S16 |
| O68 | Turning on Tool calls makes no steps requests for unexpanded agents | e2e | 295-agent | 0 steps requests | 295 requests | S13 |
| O69 | Tree default above 50 agents, Graph at or below 50 | unit | 50/51 agents | default view | — | R18 |
| O70 | Tree keys: Vim/Emacs/arrows, Enter opens, Right/Left expand/collapse, `focusArea` | e2e | nested | key → DOM change | key leaks to other pane | R20 |
| O71 | Graph nodes focusable with `aria-label` | unit/a11y | mocks | every node has tabindex and label | — | R20 |
| O72 | No `useContext` of churning providers in list-rendered agent blocks | unit (static) | source scan | grep/AST check passes | — | Perf #1 |
| O73 | Layout input has only topology edges | unit | resumed | dagre input edges ⊆ spawned/member/launched | continued or order edges | R6, S10 |
| O74 | "No errors in inspected activity" wording | unit | no-error steps | exact text | "No errors" | S13 |

## §8 compatibility

| ID | Behavior | Layer | Fixture | Exact assertion | Must NOT happen | Ref |
|---|---|---|---|---|---|---|
| O75 | Old `LOGIC_VERSION` row wiped at startup; next list returns counts | compat | cache file with old version | counts present | stale row used | §8.2 |
| O76 | Round trip: 1.1.7 wheel then N then 1.1.7 on the same cache; all three list correctly | compat/manual-CI | real wheel | each run 200 with correct counts for its version | crash, wrong data | S4 |
| O77 | Bookmark without `agent_id` opens its message | e2e | legacy bookmark | scroll target | error | §8.3 |
| O78 | Markdown export byte-identical before/after; PDF determinism first, then compare | compat | fixture session | bytes equal (MD); bytes or normalized text equal (PDF) | — | S4 |
| O79 | MCP closure canary passes; 5 tools' output unchanged on a fixture | compat | MCP fixture | canary green; JSON equal | — | §8.3 |
| O80 | Agent-transcript export file name/title names the agent | e2e | current | name contains description or id | session name only | S4 |

## Phase 1b (short)

- O-b1 FTS hit inside an agent carries the full locator and opens the agent route at the message.
- O-b2 Each agent file is its own indexed source: delete one file → only its rows go.
- O-b3 Linear fallback search gives the same shape for agent hits.
- O-b4 `SCHEMA_VERSION` bump rebuilds in background; search answers from fallback meanwhile.
- O-b5 Tool-call filter (U6) by file path / command / URL returns the right agents.
- O-b6 Color by tokens / elapsed shows a legend and coverage.
- O-b7 Export summary line under each Agent call.

## Phase 2 (short)

- O-2a The four amended LIVE-VIEW defects each have a failing-then-passing test (torn line, snapshot race, boundary loss, batch eviction).
- O-2b New meta file during a stream → one `agent_upsert`; repeated event is idempotent.
- O-2c Meta-first and transcript-first orders both give one node.
- O-2d Two streams, different subscriptions → isolated `agent_append`.
- O-2e 65 running agents → 64 tailed, 65th stale.
- O-2f Reconnect with cursors → `reset` for changed files, `agent_upsert` for all nodes.
- O-2g Real `watchdog.Observer` delivers an append within a deadline; no per-write event count.
- O-2h "running" only from `launched` without notification; never from writes.
- O-2i Disconnect frees subscription and cursors (no leak after 100 connects).
- O-2j `prefers-reduced-motion` stops node animation.

## Most likely to be missed

1. O49 — batches split across pages, which makes parallel calls look sequential.
2. O29 — enriched payload stays stale when only a meta file changes.
3. O25/O28 — new workflow run or new `subagents/` dir not detected.
4. O61/O63 — internal-only session loses its entry point.
5. O68 — turning on Tool calls fires one request per agent.
