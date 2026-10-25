# Assertion map: proposals to merged test spec

> **Privacy redaction (2026-10-25):** one real agent description in this file was replaced with the neutral placeholder "Research topic A and topic B". Nothing else changed.

- **Date:** 2026-10-24.
- **Inputs:**
  - The three proposal lists in this directory: `claude-opus-5.5.md` (O1–O80), `gpt-5.6-sol.md` (P1-001–P1-105), `gemini-3.8-flash.md` (56 Phase 1 rows, P1-ENTRY-01 … P1-E2E-01).
  - The merged spec, [2026.10.24-subagent-viewer-test-cases.md](../../2026.10.24-subagent-viewer-test-cases.md), §3 (A1–K5) and §6.2.
  - The plan, [2026.10.23-subagent-viewer-and-dag.md](../../2026.10.23-subagent-viewer-and-dag.md), to decide conflicts and invented facts.
- **Method:**
  - For each Phase 1 proposal row, find every merged test whose "Sources" column lists it.
  - Compare the row's pass condition and "must not" clause with those tests, clause by clause.
  - A clause counts as kept only when a test that lists the row as a source holds it. When another test holds it without listing the row, the table says so.
  - Status values: PRESERVED, WEAKENED, LOST, EXCLUDED (named in §6.2), CONTRADICTED (the table says which side matches the plan).
  - A proposal fact that the plan does not hold (an exact UI string, a number) is WEAKENED with "not in plan", unless §6.2 lists it.
- **Count note:** the Gemini file has 56 Phase 1 rows, not the 58 that the merged spec's header and §7 state. The total here is 80 + 105 + 56 = 241.
- **Abbreviations in the table:** P = PRESERVED, W = WEAKENED, C = CONTRADICTED, X = EXCLUDED, L = LOST.

## Map

| Proposal ID | Destination test(s) | Status | Dropped or conflicting clause (quoted) | Suggested fix (exact text to add to the destination test) |
|---|---|---|---|---|
| O1 | A1 | P | — | — |
| O2 | A2 | P | — | — |
| O3 | A5 (A1) | W | "`tool_use_id` = meta value, `spawned` edge from session" | A5 pass: "The `exact` case has `tool_use_id` equal to the meta `toolUseId`, and one `spawned` edge from the session that carries that ID." |
| O4 | A5 | W | "edge to the one matching call" | A5 pass: "The `prompt_match` case has exactly one `spawned` edge, from the one matching call." |
| O5 | A5 | P | — | — |
| O6 | A7 | W | "`caller_id` = parent id"; must not "edge session→child" (A14 holds this but does not list O6) | A7 pass: "`caller_id` = the parent agent's ID." A7 must not: "A session→child edge." |
| O7 | A7 | W | "after opening the caller: … `tool_use_id` set" | A7 pass: "After the parent loads, the child's `tool_use_id` equals the matching call in the parent." |
| O8 | A8 | P | — | — |
| O9 | A9 | P | — | — |
| O10 | A9 | P | — | — |
| O11 | A4 | P | — | — |
| O12 | A1 | P | — | — |
| O13 | A3 | P | — | — |
| O14 | A10 | P | — | — |
| O15 | A10 | P | — | — |
| O16 | A6 | P | — | — |
| O17 | B1 | P | — | — |
| O18 | B1 | P | — | — |
| O19 | B2 | P | — | — |
| O20 | B3 | P | — | — |
| O21 | B4 | P | — | — |
| O22 | B5 | W | "chip has neutral class" (layer unit+e2e); B5 is a backend unit suite and cannot render a chip | New G-suite case (or G7): "An outcome of `future_state_x` renders the chip text `future_state_x` with the neutral class, not the success class." |
| O23 | C1 | P | — | — |
| O24 | C4 | W | "0 opens under `subagents/`"; C4 counts only `agent-*.jsonl` and `*.meta.json`, so a `journal.jsonl` open passes | C4 pass: "… and 0 opens of any file under `subagents/`, `journal.jsonl` included." |
| O25 | C2 | P | — | — |
| O26 | C2 | P | — | — |
| O27 | C2 | P | — | — |
| O28 | C2 | P | — | — |
| O29 | C3 | P | — | — |
| O30 | P1 | W | fixture "real corpus"; P1 does not name the corpus | P1 measurement: "on the pinned real corpus, not a synthetic fixture or a mocked filesystem." |
| O31 | P3 | C | "wall time < timeout … must not: timeout". P3 uses a T0 budget. The merged test matches the plan (§8.2: no frontend timeout exists). | None for the timeout. |
| O32 | D1 | P | — | — |
| O33 | D1 | W | "messages from the agent file" | D1 pass: "The agent detail `messages` equal the messages of that agent's transcript file, and hold no root-session message." |
| O34 | D2 | P | — | — |
| O35 | D3 | P | — (D3 splits `../` into encoded 422 / unencoded 404, as the plan does) | — |
| O36 | D4 | W | "outside file never read"; D4 checks only that the content is absent from the response | D4 pass: "The file-open spy records no open of the sentinel path." |
| O37 | D5 | P | — | — |
| O38 | D6 | P | — | — |
| O39 | D6 | P | — | — |
| O40 | E1 | W | "each block has the right id and this invocation's outcome"; must not "ref on other tools" (E1 tests only an unknown name) | E1 pass: "Each ref holds this invocation's outcome and usage." E1 must not: "A `subagent_ref` on any other tool block, for example `Bash` or `Read`." |
| O41 | E2 | P | — | — |
| O42 | E3 | P | — | — |
| O43 | E3 | P | — | — |
| O44 | C7 | P | — | — |
| O45 | C7 | P | — | — |
| O46 | F1 | P | — | — |
| O47 | F2 | P | — | — |
| O48 | F3 | P | — | — |
| O49 | F4 | P | — | — |
| O50 | F5 | P | — | — |
| O51 | F4 | P | — | — |
| O52 | F6 | P | — | — |
| O53 | F6 | P | — | — |
| O54 | H1, G1 | P | — | — |
| O55 | H1 | W | "heading/list elements" (the check method); H1 says only "shows as Markdown" | H1 checkpoint: "A Markdown list in the prompt renders as `<ul>`/`<li>`, and a heading as an `<h*>` element." |
| O56 | H1 | P | — | — |
| O57 | H1 | P | — | — |
| O58 | H2 | W | must not "XML visible by default" | H2 must not: "The raw `<task-notification>` text visible before Raw is clicked." |
| O59 | H3 | P | — | — |
| O60 | H4 | P | — | — |
| O61 | H5 | P | — | — |
| O62 | H6 | P | — | — |
| O63 | G3 | P | — | — |
| O64 | H7 | P | — | — |
| O65 | H7 | P | — | — |
| O66 | G4, H9 | P | — | — |
| O67 | G4 | P | — | — |
| O68 | H9 | P | — | — |
| O69 | G2 | P | — | — |
| O70 | H8 | P | — | — |
| O71 | G8 | P | — | — |
| O72 | G9 | P | — | — |
| O73 | G5 | P | — | — |
| O74 | G7 | P | — | — |
| O75 | C5 | P | — | — |
| O76 | K1 | P | — (order N → 1.1.7 → N follows plan §8.5; it covers both transitions) | — |
| O77 | H10 | P | — | — |
| O78 | K2 | P | — | — |
| O79 | K3 | P | — | — |
| O80 | H3 | P | — | — |
| P1-001 | A1 | P | — | — |
| P1-002 | A2 | P | — | — |
| P1-003 | A1 | W | must not "Meta content cannot move it to another owner" | A1 must not: "An owner taken from meta content. A meta file whose fields name another session does not move the agent." |
| P1-004 | A8 | W | "node has the expected `workflow_run_id` and phase" | A8 pass: "Each member node has `workflow_run_id` = the run ID and `workflow_phase` = the journal's `phase`." |
| P1-005 | A1 | P | — | — |
| P1-006 | A3 | P | — | — |
| P1-007 | A4 | P | — | — |
| P1-008 | K4 | W | "Script exits 0 and printed rows equal the fixture's exact expected counts"; K4 checks only the real corpus against the app | K4 pass: "On FX1, `count_subagents.py` exits 0, and each printed row equals the fixture manifest. Internal runs and unknown-owner files are separate rows, not in the ordinary total." |
| P1-009 | K4 | P | — | — |
| P1-010 | A5 | W | "the expected `tool_use_id`, caller, and one `spawned` edge carrying that ID" | Same text as O3. |
| P1-011 | A5 | W | "links to that call only" | Same text as O4. |
| P1-012 | A5 | P | — | — |
| P1-013 | A5 | W | "appears under the UI/API's Unlinked grouping"; A5 does not say it, and H8 does not list P1-013 | A5 pass: "The `ambiguous` and `unresolved` agents are in the Unlinked group." |
| P1-014 | A6 | P | — | — |
| P1-015 | A7 | P | — | — |
| P1-016 | A7 | P | — | — |
| P1-017 | A7 | P | — | — |
| P1-018 | A8 | W | "with labels/phases mapped as specified" | Same text as P1-004, plus: "and the journal's `label`." |
| P1-019 | A9 | W | "one `continued` annotation edge with the sender and target" | A9 pass: "The `continued` edge has `source` = the `SendMessage` sender and `target` = the agent." |
| P1-020 | B4 | P | — | — |
| P1-021 | B3 | P | — | — |
| P1-022 | B3 | P | — | — |
| P1-023 | B5 | W | must not "infer completed, failed, or running"; B5 omits "failed" | B5 must not: "`running`, `completed`, or `failed` from file writes." |
| P1-024 | B5 | P | — | — |
| P1-025 | B5 | W | "API and rendered chip expose `future_state_x` unchanged"; no frontend test renders the chip | Same G-suite text as O22. |
| P1-026 | B1 | P | — | — |
| P1-027 | B1 | P | — | — |
| P1-028 | B2 | P | — | — |
| P1-029 | G1 | W | "Wire body contains JSON `null` … API does not emit `0`"; G1 uses a mock, so the backend is not tested | D1 pass: "For an agent with no usage evidence, the real graph body has JSON `null` for each unknown counter, never `0`." |
| P1-030 | A10 | W | "retains discoverable membership with partial/unresolved evidence" | A10 pass: "With a bad meta file and a good transcript, the agent's node stays, with `evidence: partial` or `link_quality: unresolved`." |
| P1-031 | A10 | P | — | — |
| P1-032 | A10 | P | — | — |
| P1-033 | A10 | P | — | — |
| P1-034 | C2 | P | — | — |
| P1-035 | C2 | P | — | — |
| P1-036 | C2 | P | — | — |
| P1-037 | C2, C3 | P | — | — |
| P1-038 | C2 | P | — | — |
| P1-039 | C2 | P | — | — |
| P1-040 | C7 | W | "later search/export/MCP-visible data matches" | C7 pass: "The output of each MCP tool on the session is equal before and after the enriched detail call." |
| P1-041 | C4 | P | — | — |
| P1-042 | C4 | P | — | — |
| P1-043 | C5 | P | — | — |
| P1-044 | D1 | W | "exact expected node/edge IDs and diagnostics are present"; D1 says "Expected node and edge IDs are present" | D1 pass: "The node and edge ID sets equal the expected sets exactly, and `diagnostics` equals the expected entries. The fixture holds ordinary, nested, workflow, internal, and unlinked nodes." |
| P1-045 | D1 | W | "exact transcript messages from that agent" | Same text as O33. |
| P1-046 | C6 | P | — | — |
| P1-047 | D3 | W | "Empty/overlength IDs where routable, … separators, and control/encoded variants" | D3 parametrization: "add a control character (`%00`, `%0A`) and an encoded `/` (`%2F`); each gives 422." |
| P1-048 | D3 | W | "no transcript outside the graph is opened" | D3 pass: "For the unknown valid ID, the file-open spy records no open of any `agent-*.jsonl`." |
| P1-049 | D3 | C | "Encoded and double-encoded `../` … returns 404". D3 gives 422 for encoded. The merged test matches the plan (§5 Phase 1 tests: "An encoded `../` is a bad ID, so it gives 422"). Also dropped: "double-encoded"; "No external file contents or path details appear in the response". | D3: "A double-encoded `../` (`%252E%252E%252F`) gives 422. No error body holds sentinel content or a filesystem path." |
| P1-050 | D4 | P | — | — |
| P1-051 | D5 | W | "Neither graph nor detail route follows the value"; "sentinel content is absent" | D5 pass: "Run through both the graph route and the agent detail route. Neither body holds the sentinel content." |
| P1-052 | E3 | P | — | — |
| P1-053 | E3 | P | — | — |
| P1-054 | E3 | P | — | — |
| P1-055 | E4 | P | — | — |
| P1-056 | E1 | P | — | — |
| P1-057 | E2 | P | — | — |
| P1-058 | F1 (G7) | P | — | — |
| P1-059 | F2 | W | "`started_at`, `ended_at` … reflect row timing"; must not "is not labeled as agent compute time" | F2 pass: "`started_at` = T and `ended_at` = T+14 s." F2 must not: "A label that calls `elapsed_ms` the agent's compute time." |
| P1-060 | F3 | W | "the same `parallel_group` equal to that message ID" | F3 pass: "`parallel_group` equals the raw `message.id`." |
| P1-061 | F3 | P | — | — |
| P1-062 | F4 | P | — | — |
| P1-063 | F4 | P | — | — |
| P1-064 | F4 | P | — | — |
| P1-065 | F5 | P | — | — |
| P1-066 | F6 | P | — | — |
| P1-067 | F6 | P | — | — |
| P1-068 | D6 | P | — | — |
| P1-069 | D7 | P | — | — |
| P1-070 | H1 | W | "Show steps causes one **scoped** steps request"; H1 counts the request but does not check its scope | H1 checkpoint: "The one steps request has `agent_id` = this block's agent." |
| P1-071 | H1 | P | — | — |
| P1-072 | H2 | P | — | — |
| P1-073 | H3 | W | must not "or target another invocation" | H3 checkpoint: "For a resumed agent, the link scrolls to the call of the invocation shown, not to another invocation's call." |
| P1-074 | H3 | W | "exported content belongs to that agent"; must not "Root-session content … is not exported" | H3 checkpoint: "The exported file holds the agent's messages and no root-session message." |
| P1-075 | H4 | P | — | — |
| P1-076 | H6 | P | — | — |
| P1-077 | H6, G4 | P | — | — |
| P1-078 | H6 | W | "Agent/Task/Workflow are ordinary steps with targets"; H6 names only `Agent` and no targets | H6 checkpoint: "In OFF/ON, `Agent`, `Task`, and `Workflow` calls are plain steps, each with its `target`." |
| P1-079 | H6 | P | — | — |
| P1-080 | H5 | P | — | — |
| P1-081 | H5 | W | "Tool calls view shows exact root calls"; must not "No subagent fetch is required to decide visibility" | H5 checkpoint: "For tools only, the Tool calls view shows exactly the root's calls. The network log has no steps or subagents request made before the toggle shows." |
| P1-082 | H5 | P | — | — |
| P1-083 | H7 | W | must not "Focus does not trigger a steps request until calls are requested"; H7 says only that the request is for A | H7 checkpoint: "Focus alone sends 0 steps requests. After 'Show calls', exactly 1, for A." |
| P1-084 | H7 | W | "removes focus URL state" | H7 checkpoint: "After exit, the URL has no `focus` parameter." |
| P1-085 | H7 | P | — | — |
| P1-086 | H8 | W | must not "Left/Right do not unexpectedly navigate browser history" | H8 must not: "Left or Right changes the URL or `history.length`." |
| P1-087 | G5 | P | — | — |
| P1-088 | G2 | P | — | — |
| P1-089 | G8 | P | — | — |
| P1-090 | G4, H9 | W | "Next/Earlier/Later replaces IDs"; must not "no unbounded hidden-node accumulation" | G4 pass: "'Earlier' and 'Later' also replace the step IDs. The layout input, not only the DOM, never holds more than 200 step nodes." |
| P1-091 | H9 | P | — | — |
| P1-092 | G6 | W | "token coverage matches evidence"; must not "presented as subtree totals" | G6 pass: "Token text shows the coverage of its evidence." G6 must not: "A chip count that sums the subtree." |
| P1-093 | G7 | W | fixture "with an uninspected agent containing an error" (the boundary case) | G7 pass: "The fixture has an error in an agent the user did not open. The notice still says 'No errors in inspected activity'." |
| P1-094 | P1 | W | fixture "Pinned real-corpus snapshot"; must not "mocked filesystem" | Same text as O30. |
| P1-095 | P2 | P | — | — |
| P1-096 | P3 | C | "finishes before frontend timeout"; P3 uses a T0 budget. The merged test matches the plan (§8.2). Also dropped: "median/p90 and corpus size are reported". | P3 pass: "The run reports median, p90, and the corpus size." |
| P1-097 | P4 | P | — | — |
| P1-098 | G9 | P | — | — |
| P1-099 | K2 | P | — | — |
| P1-100 | K2 | P | — | — |
| P1-101 | H10 | W | "Bookmark deserializes with `agent_id == null`" | H10 checkpoint: "The loaded bookmark has `agent_id` = null." |
| P1-102 | K3 | P | — | — |
| P1-103 | K1 | W | must not "emulate the old release with current code or delete the cache between runs" | K1 must not: "A cache deleted or reset between runs. The old release emulated with current code." |
| P1-104 | G10 | P | — | — |
| P1-105 | K5 | W | "raw logs contain none of the prohibited non-execution strings" | K5 pass: "The raw logs contain none of the non-execution strings that TESTING.md §0 lists." |
| P1-ENTRY-01 | H4 | P | — | — |
| P1-ENTRY-02 | H5 | W | case "(a) subagents only"; "visible and enabled" | H5 parametrization: "add 'subagents only'; the toggle is visible and enabled." |
| P1-ENTRY-03 | H5 | P | — | — |
| P1-SW-01 | H6 | W | "Subagents switch is checked; Tool calls switch is unchecked" as the default | H6 checkpoint: "On first open, Subagents is on and Tool calls is off." |
| P1-SW-02 | G6 | W | exact chips "`Read (5)`, `Bash (3)`, `Grep (2)`, and a `more` link" | G6 pass: "With `toolStats` {Read: 5, Bash: 3, Grep: 2, Edit: 1}, the chips read 'Read (5)', 'Bash (3)', 'Grep (2)', then 'more'." |
| P1-SW-03 | H6 | W | "in recorded order"; must not "spawn branches must NOT be drawn" | H6 checkpoint: "OFF/ON shows the steps in recorded order, with no spawn edge." |
| P1-SW-04 | H6 | W | must not "render any child agent nodes, step nodes, or connector edges" | H6 must not: "In OFF/OFF, any agent node, step node, or edge." |
| P1-SW-05 | G3 | W | must not "Tool calls switch must remain fully interactive" | G3 pass: "While Subagents is disabled, the Tool calls switch still toggles." |
| P1-FOC-01 | H7 | W | "Child B is collapsed" | H7 checkpoint: "After focus, A's children start collapsed." |
| P1-FOC-02 | H7 | W | method "pan/zoom transform matrix … restored"; "URL drops `focus` param" | H7 checkpoint: "The Graph's SVG `transform` after exit equals its value before focus. The URL has no `focus` parameter." |
| P1-FOC-03 | H7 | W | must not exit "inside a filter combobox, dropdown, or search input" (H7 names only an input) | H7 checkpoint: "Esc with an open dropdown or combobox in the panel closes that control and does not exit focus." |
| P1-FOC-04 | H7 | W | exact text "Agent … not found in this session" and button "Back to session activity" (text not in plan) | H7 checkpoint: "The message names the unknown ID." (Set the exact words in UX.md §16 first.) |
| P1-TREE-01 | G2 | P | — | — |
| P1-TREE-02 | H8 | W | "active item has `aria-selected="true"`"; must not "Focus must NOT leave the tree container" | H8 checkpoint: "The selected row has `aria-selected="true"`, and focus stays inside the tree." |
| P1-TREE-03 | H8 | P | — | — |
| P1-TREE-04 | H8 | W | "clicking expands the 4 internal transcripts"; must not "Internal runs must NOT appear as top-level peer nodes" | H8 checkpoint: "A click expands it to exactly the 4 internal runs. No internal run is a top-level row." |
| P1-TREE-05 | H8 | W | "nodes list agent ID and prompt snippet; status shows `unknown`" (row content not in plan) | None until UX.md §16 sets the row content. |
| P1-GRAPH-01 | G8 | W | `aria-label` format "Agent <id>, type <type>, status <outcome>, <tool_count> tools" (format not in plan) | G8 pass: "Each `aria-label` holds the node's description and outcome." (Set the format in UX.md §16 first.) |
| P1-GRAPH-02 | G5, A9 | P | — | — |
| P1-GRAPH-03 | G5 | W | must not "Steps without `message.id` must NOT be grouped side-by-side" (only F3 checks it, in the backend) | G5 pass: "Two adjacent steps without a `message.id` are in different ranks." |
| P1-GRAPH-04 | G4 | P | — ("total rendered step nodes <= 50" is the global 50 limit that §6.2 excludes) | — |
| P1-GRAPH-05 | G4 | P | — | — |
| P1-GRAPH-06 | H9 | P | — | — |
| P1-GRAPH-07 | G7 | P | — | — |
| P1-BLOCK-01 | H1 | W | exact header "Agent · general-purpose · Research topic A and topic B · 36 tools · 3m 48s · 99.6k tokens · completed" (the plan step 1.4 gives this format) | H1 checkpoint: "For the fixture of agent `ae52567b5b2b1b29e`, the header reads 'Agent · general-purpose · Research topic A and topic B · 36 tools · 3m 48s · 99.6k tokens · completed', and the chip has the completed style." |
| P1-BLOCK-02 | H1 | W | "formatted HTML list tags"; "`<pre>` element with exact JSON input" | Same text as O55, plus: "Raw input shows a `<pre>` whose text parses to the call's exact input JSON." |
| P1-BLOCK-03 | H1 | W | "`GET …/steps?agent_id=<id>&cursor=...`"; "drawer shows 20 1-line tool summaries" | H1 checkpoint: "The request has `agent_id` = the block's agent. The drawer shows one line per call: 20 lines for a 20-call agent." |
| P1-BLOCK-04 | H1 | P | — | — |
| P1-BLOCK-05 | H2 | W | must not "raw `<task-notification>` XML text must NOT be visible unless toggle clicked" | Same text as O58. |
| P1-BLOCK-06 | E3 | W | "DOM renders standard user message bubble" for an unknown task ID (E3 is a parser test; H2 covers only a user-typed tag) | H2 checkpoint: "A notification with an unknown task ID also stays a normal bubble." |
| P1-NAV-01 | H3 | P | — | — |
| P1-NAV-02 | H3 | P | — | — |
| P1-NAV-03 | H3 | W | exact header "3 invocations" and "2 of 3 invocations report usage" (the plan §4.3 gives the second) | H3 checkpoint: "For an agent with 3 invocations of which 2 report usage, the header shows the latest outcome and '2 of 3 invocations report usage'." |
| P1-STEPS-01 | F1, G7 | P | — | — |
| P1-STEPS-02 | F4 | P | — | — |
| P1-STEPS-03 | F5 | P | — | — |
| P1-DISC-01 | A1 | W | must not "trigger eager full-file transcript parsing" | A1 pass: "A file-read spy shows that `build_graph` reads no agent transcript past its first user row." |
| P1-DISC-02 | A5 | P | — | — |
| P1-DISC-03 | A5 | W | "node is grouped under Unlinked" | Same text as P1-013. |
| P1-DISC-04 | A7 | P | — | — |
| P1-DISC-05 | A10 | W | must not "parent session must remain readable" | A10 pass: "The session detail route also returns 200, with its messages." |
| P1-SEC-01 | D3 | C | "(a) `../secret` … return HTTP 422". D3 gives 404 for an unencoded `../`. The merged test matches the plan (§5 Phase 1 tests). Also dropped: must not "`os.path.join()`" of the parameter. | D3 must not: "A path built from the request text." |
| P1-SEC-02 | D4 | W | must not "read, follow" the outside file (D4 checks only the response). The proposal's API path `/agents/evil` is the wrong path that §6.2 names; D4 uses the right one. | Same text as O36. |
| P1-SEC-03 | D5 | P | — | — |
| P1-COMPAT-01 | B1 | P | — (the proposal's "non-null token/duration usage" for all 5 shapes is wrong for the async-launch shape, which has no usage, plan §8.1; B1's "expected … usage" is right) | — |
| P1-COMPAT-02 | B1 | W | "header displays `Agent · ...` and status; steps route binds identically" | H1 checkpoint: "A `Task` call renders the same Agent block header as an `Agent` call." |
| P1-COMPAT-03 | P3, C5 | X | "< 3.0s" (§6.2). The wipe and the cold call stay in C5 and P3. | — |
| P1-COMPAT-04 | K1 | P | — | — |
| P1-COMPAT-05 | D6 | P | — | — |
| P1-COMPAT-06 | C7 | W | must not "Search and MCP server callers … must NOT see mutated fields" (MCP dropped) | Same text as P1-040. |
| P1-COMPAT-07 | K2 | P | — | — |
| P1-COMPAT-08 | K3 | P | — | — |
| P1-SCALE-01 | C4 (P1) | P | — | — |
| P1-SCALE-02 | P2 | W | "< 500ms" is excluded (§6.2). Also dropped: "peak RSS remains within budget set in T0"; must not "eager synchronous parsing of all 295 subagent transcripts". | P2 pass: "Each run records peak RSS and meets the T0 RSS budget. A file-read spy shows no agent transcript read past its first user row." |
| P1-SCALE-03 | P4, H9 | X | "< 300ms" layout and "< 50ms" input latency (§6.2 numeric budgets). The 200-node limit stays in H9 and P4. | — |
| P1-E2E-01 | — | X | Whole row (§6.2: H3, H4, and H7 cover each step) | — |

## Totals

| Status | Opus (80) | GPT (105) | Gemini (56) | All (241) |
|---|---|---|---|---|
| PRESERVED | 67 | 68 | 24 | 159 |
| WEAKENED | 12 | 35 | 28 | 75 |
| LOST | 0 | 0 | 0 | 0 |
| EXCLUDED | 0 | 0 | 3 | 3 |
| CONTRADICTED | 1 | 2 | 1 | 4 |

- **No behavior is lost.** Every row maps to at least one test that covers its behavior.
- **All 4 contradictions favor the merged spec.** In each, the merged test matches the plan:
  - the first list call has a budget, not a timeout (O31, P1-096);
  - an encoded `../` gives 422 (P1-049);
  - an unencoded `../` gives 404 (P1-SEC-01).
- **Most WEAKENED rows lost a must-not clause, an exact value, or a check method.** Three of them name UI text or formats that the plan does not hold: P1-FOC-04, P1-TREE-05, and P1-GRAPH-01. UX.md §16 must set these first. P1-COMPAT-01 makes a usage claim that the plan contradicts, so its row stays PRESERVED.
- **Two placement gaps:**
  - B5 (backend unit) asserts a chip color. No frontend test renders that chip (O22, P1-025).
  - G1 (Vitest, mock) asserts `null` for unknown counters. No backend test checks the wire body (P1-029).
