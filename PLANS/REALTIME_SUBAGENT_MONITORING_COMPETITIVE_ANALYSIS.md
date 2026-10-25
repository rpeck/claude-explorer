# Real-time subagent monitoring — competitive analysis

- **Date:** 2026-10-24.
- **For:** Phase 2 (real time) of [2026.10.23-subagent-viewer-and-dag.md](./2026.10.23-subagent-viewer-and-dag.md), which extends [LIVE-VIEW.md](./LIVE-VIEW.md).
- **Status:** RATIFIED by an LLM Council. All three members agreed in round 3 of 3 (§11).
- **Council members:**
  - Claude Opus 5.5 (author and CTO);
  - `chatgpt/gpt-6-astra` (GPT-6 Astra on the user's ChatGPT plan, through PAL's ChatGPT provider);
  - `gemini-3.8-flash` (direct Google, free developer key).
- **Research:** a workflow of 9 research agents. It ran 3 discovery sweeps, 1 ranking agent, and 5 deep dives into cloned repositories.

## 1. Summary

The user wants a real-time view of subagents. It must need **no change in the observed Claude Code sessions**: no hooks, plugins, skills, environment variables, wrappers, or proxies.

1. **The goal is possible, and other tools already prove it.** claude-devtools (about 4,000 stars) and zoetrope (about 1,100 stars) show live subagent activity from passive file reading alone.
2. **So "zero install" alone does not make us different.** Our difference must be:
   - **correct trees** that the passive competitors get wrong;
   - **live and history in one app;**
   - **a multi-session rail and a catch-up card;**
   - **cited answers** to questions about a session;
   - later, **reviewed lessons** that the user can measure.
3. **Claude Code writes more live data to disk than LIVE-VIEW.md assumed.** Three facts, all verified on this laptop:
   - A long tool call's start is on disk while the call runs.
   - `~/.claude/sessions/<pid>.json` records each session's status (`idle`, `busy`, or `shell`).
   - Its `procStart` field identifies the process.
4. **Competitors make linking errors that we avoid.** These errors were confirmed in their code:
   - claude-devtools counts only `Task` as a subagent spawn, not `Agent`.
   - zoetrope puts every nested agent under the main session.
   - Agent Flow links parent and child by a display name.

## 2. Method

1. **Discovery.** Three independent sweeps ran:
   - GitHub and the web;
   - community opinion (Reddit, Hacker News, slashdot, Medium, dev.to);
   - the methods that tools use to collect data.
2. **Ranking.** One agent merged the three lists and removed duplicates. It chose 5 tools: the two seed tools, plus the most capable and best-regarded tools with public source. It checked each repository with `gh api`.
3. **Deep dives.** One agent per tool cloned the repository (read only, no install, no run). It traced the data from its source to the UI, with `path:line` evidence.
4. **Checks by the author.** The author checked the claims that drive the design against the files on this laptop and in the clones (§5).
5. **Council.** The three members ran three rounds (§11).

## 3. The landscape: how tools collect live data

| # | Method | Change in the observed session? | Examples |
|---|---|---|---|
| 1 | Hooks in `settings.json`, or a plugin that adds hooks | yes | flightdeckhq/flightdeck, Agents Observe, disler's hooks reference, CCAM, Tarsa |
| 2 | An in-session mod or skill | yes | scasella/claude-flightdeck |
| 3 | Claude Code's native OpenTelemetry (env vars) | yes | claude-code-otel, SigNoz, Grafana stacks |
| 4 | An API proxy or a launch wrapper | yes | ccxray, claude-code-proxy, claude-trace |
| 5 | An orchestrator that launches and owns the agents | yes (the tool must start the session) | justinchuby/flightdeck, Vibe Kanban, Claude Squad |
| 6 | Passive reading of files under `~/.claude` | **no** | claude-devtools, zoetrope, abtop, AgentsView, claude-code-trace, cctop, agtop, rungraph |

- **Hybrids:** Agent Flow and claude-view read the files passively, and they also write hooks into the global `~/.claude/settings.json` without asking.
- **The official tool:** Anthropic's Agent View (the `claude agents` screen) lists background sessions. It does not show subagents as separate rows.
- **Most adopted:** the most-starred passive readers (ccusage, Claude-Code-Usage-Monitor, Sniffly, claude-code-log) are not live subagent views. They show that passive reading is the path that most users accept.

## 4. The five tools that we read in depth

### 4.1 flightdeckhq/flightdeck

- **Source:** <https://github.com/flightdeckhq/flightdeck>, commit `2db326d6e452`, Apache-2.0. 16 stars.
- **Collection:** a Claude Code plugin made of hooks (`observe_cli.mjs`). On each hook, it rereads the same JSONL files that a passive reader sees. It also has a Python SDK that patches LLM clients for agents other than Claude Code.
- **What the user must do:** install the plugin, set `FLIGHTDECK_SERVER` and `FLIGHTDECK_TOKEN`, and run a Docker Compose stack (Postgres, NATS, API, workers, dashboard).
- **Subagent links:** from the `SubagentStart` and `SubagentStop` hooks and the hook's `agent_id`. Its timeline connector falls back to time proximity.
- **UI:** fleet swimlanes on a shared time axis, and lines from parent to child.
- **Chat or learning:** none. Search is substring matching.
- **What to borrow:**
  - Group assistant records by `message.id`, and take the last `usage` as the true value.
  - Make a deterministic child ID from the parent ID and the correlator.
  - Create a stub for the parent when a child arrives first.
- **Lesson:** its main data path is already a JSONL read. The plugin only triggers the read, so every feature that it shows from those reads can be rebuilt passively.

### 4.2 justinchuby/flightdeck

- **Source:** <https://github.com/justinchuby/flightdeck>, commit `b8190f68b58d`, MIT. 42 stars.
- **Collection:** it launches each agent CLI itself over ACP, and it parses commands that the agents print. It cannot attach to a `claude` session that it did not start.
- **Side effect:** it writes role files into the global `~/.claude/agents/`.
- **UI:**
  - a task DAG in React Flow, with its own longest-path layout and a critical-path highlight;
  - a Kanban board, a Gantt chart, and a catch-up banner.
- **Chat:** the user can message its own agents. Its "natural language commands" are a fixed phrase table, with no LLM.
- **What to borrow:**
  - One small event vocabulary for all sources.
  - Send structural events at once, and batch the text every 100 ms.
  - Replay what was missed on reconnect.
  - The catch-up banner.

### 4.3 claude-devtools (matt1398)

- **Source:** <https://github.com/matt1398/claude-devtools>, commit `1486f2042c87`, MIT.
- **Adoption:** 3,993 stars and 304 forks. A Show HN with 69 points, and a Reddit launch reported at 500+ upvotes.
- **Collection:** fully passive.
  - A recursive `fs.watch` on `~/.claude/projects` and `~/.claude/todos`, with a 100 ms debounce.
  - Byte-offset parsing of appended data.
  - A 30 s catch-up scan of files changed in the last hour.
  - A 3 s size poll over SSH.
- **UI:** a session timeline, per-subagent execution trees with tokens and time, and rule-based notification triggers.
- **Chat or learning:** none.
- **Defects that we confirmed:**
  - `src/main/utils/toolExtraction.ts` has `const isTask = block.name === 'Task'`. Current Claude Code writes `Agent`, so for current sessions it links subagents by timing.
  - The live path parses the whole session again on each change.
- **What to borrow:**
  - Watch events, plus a short catch-up scan over an "active set" of files.
  - Pin the offset until a line ends. Parse the whole file again if the file is cut short.

### 4.4 zoetrope (furkankly)

- **Source:** <https://github.com/furkankly/zoetrope>, commit `b1f31dd26bd4`, MIT.
- **Adoption:** 1,096 stars and 78 forks in about two months.
- **Collection:** fully passive. It polls offsets every 200 ms. It reads `subagents/`, `workflows/`, and the meta sidecars again until they parse.
- **UI:** a live flow graph in a terminal UI and in the browser (WASM), with replay and scrubbing. Each agent shows the prompt that spawned it.
- **Persistence:** none. It shows one session at a time.
- **Defect that we confirmed:** `src/provider/claude/mod.rs:355` sets each agent's parent to the workflow or to `MAIN_ID`. So nested agents appear under the main session.
- **What to borrow:**
  - Its rule: "A provider states what its records contain; the core decides what it means when nothing was recorded."
  - A fold that does not depend on arrival order and gives the same result when a fact arrives twice, with a shuffle test.
  - "A spawn acknowledgment is not completion."

### 4.5 Agent Flow (patoles)

- **Source:** <https://github.com/patoles/agent-flow>, commit `de99a3712bf6`, Apache-2.0. 1,735 stars and 214 forks.
- **Collection:** a hybrid.
  - Hooks that the VS Code extension writes into the global `~/.claude/settings.json` without asking.
  - Passive watching of `~/.claude/projects` and Codex logs, with a 1 s scan and a 3 s poll as fallback.
- **Subagent links:** a display-name string of 30 characters or fewer, taken from the spawning call. Two agents with the same description can merge.
- **UI:** an animated force-directed node graph.
- **Chat:** none. Its "chat panel" is a read-only message log.
- **Known issues:** it slows down in long sessions (#20), and it has path bugs (#4, #18, #22). Hacker News users asked for detection of repeated file reads and retry loops.
- **What to borrow:**
  - An async subagent stays open until its `<task-notification>`.
  - A session counts as active by the newest change time across its main file and its subagent files.

### 4.6 Other tools worth a note

| Tool | Why it matters to us |
|---|---|
| AgentsView (kenn-io) | The closest competitor for archive and search (SQLite FTS5, SSE web UI). It has no subagent DAG. |
| claude-view (tombelieber) | It reads `~/.claude/sessions/<pid>.json` and checks the PID, without hooks. Its live channel also writes hooks into `settings.json`. |
| rungraph (fayzan123) | Its MCP tools (`find_nodes`, `get_graph`, `get_detail`) are a precedent for the chatbot's read-only tools. |
| agtop (ldegio) | It rebuilds "ghost" rows from the parent transcript when Claude Code has deleted the subagent files. |
| abtop, cctop | They add the OS process table to file reading, to know which sessions are alive. |
| Agents Observe | Hacker News comments report that hooks block for 50–60 ms each, that "jsonl files provide the full picture", and that agents show "sanitised optimism". |

## 5. Facts verified on disk (2026-10-24)

| # | Fact | How we checked it | What it means for Phase 2 |
|---|---|---|---|
| F1 | A long tool call's `tool_use` row is on disk while the call runs, and its `tool_result` is not. | A live probe on this session during a 15 s Bash call | The live view shows the start of a call at once. Only the end waits. |
| F2 | `~/.claude/sessions/<pid>.json` exists for each running session. It has `sessionId`, `cwd`, `status`, `statusUpdatedAt`, `procStart`, `entrypoint`, and more. | Read the 14 files | Liveness and busy or idle state, with no hooks. Desktop-started sessions have it too. |
| F3 | Status values seen: `idle` (9), `busy` (1), `shell` (2). No value means "waiting for permission". | The same 14 files | The app cannot know that a session waits for permission. |
| F4 | 2 of 14 lifecycle files have extra bytes after the first JSON object. | `json.load` failed; `raw_decode` worked | Parse only the first object. Keep the last valid copy. |
| F5 | `procStart` equals the process start time in UTC (`ps` prints local time, 7 h behind). | All 14 files | PID reuse is detectable: compare the PID's start time with `procStart`. |
| F6 | A `<pid>.<hash>.key` file sits beside each lifecycle file. Each lifecycle file also names a `messagingSocketPath`. | Directory listing | Never read the `.key` files. Never connect to the socket. |
| F7 | `type: progress` rows with `agent_progress` appear only in 2026-03 files. | A scan of all parent files | Support them for old sessions only. |
| F8 | claude-devtools treats only `Task` as a subagent spawn. | `toolExtraction.ts` in the clone | A wrong tree for current sessions (they use `Agent`). |
| F9 | zoetrope puts nested agents under the main session. | `mod.rs:355` in the clone | A wrong tree for nested agents. |

## 6. Phase 2 design: what we take from this

These decisions are the council's T1′–T7.

1. **Sources (T1′).**
   - **Read:** the main JSONL, the `subagents/` transcripts and `.meta.json` files, the workflow journals, and `~/.claude/sessions/<pid>.json`. The `todos/` files are optional.
   - **Never:** read a `*.key` file, or connect to `messagingSocketPath`.
   - **Parse the first JSON object** with `raw_decode`. Record trailing bytes as a diagnostic, not as a failure.
   - **Cache the last valid object of each file,** keyed by `(inode, mtime, size)`. Read the file again on any change.
   - **On a parse failure,** keep the last valid object, mark it stale, and retry on the next tick.
2. **Liveness and the words that the UI uses (T2′).** Each of these is a separate field:
   - the process is alive;
   - the status that Claude Code reports, kept as raw text;
   - the time of the newest recorded activity;
   - the open calls;
   - the outcome of each invocation;
   - how fresh the data is.

   | Evidence | The UI says |
   |---|---|
   | An open call, without stronger evidence | "call recorded N s ago; completion not observed" |
   | Status `busy`, PID alive, start time equal to `procStart`, and the open call is the newest record in its transcript | "running: Bash, started N s ago" |
   | An async launch without a notification | "launched; completion not observed" |
   | No new record | "no recorded activity for N min" |
   | No lifecycle file | session liveness "unknown" |

   - The UI never says "stuck" or "waiting for permission", because no file records either state (F3).
3. **A long tool call (T3).** The start shows at once (F1). The UI promises updates only after the data reaches the disk.
4. **The active set and polling (T4′).**
   - **The active set holds:**
     - sessions whose lifecycle PID is alive, with any status;
     - sessions that the user has open or follows, at any age;
     - all descendant files of those sessions;
     - any session file changed in the last 10 minutes.
   - **Poll** the active set every 2 s with `stat`.
   - **Rediscover** every 10 s: `~/.claude/sessions/`, the project-root transcripts, `subagents/`, and `workflows/`.
   - **Keep** the watchdog events and the backstop scan.
   - **Keep resource limits separate from eligibility.** The limits are the polling cost, the file descriptors, and the 64-file tail limit.
   - **Test the watchdog under low Linux inotify limits.** On an overflow or `ENOSPC`, the poll and the rediscovery are the full fallback.
5. **Resume after a disconnect (T5).**
   - The app uses its own event sequence, a cursor for each source that notices when a file is replaced, and a snapshot watermark.
   - On reconnect, the server replays the missed events or forces a fresh snapshot.
   - This replaces `<file>:<byte offset>` as the event ID. The four LIVE-VIEW.md amendments stay.
6. **One fact vocabulary (T6).**
   - **Each fact carries:** its source locator, its generation and order, the identity of its invocation, and the quality of its evidence.
   - **Precedence is explicit,** not "the last arrival wins".
   - **Tests:** shuffled order, duplicates, corrections, and continuations.
7. **Linking (T7).**
   - Keep the ratified Phase 1 rules: `toolUseId`, a unique prompt match within the immediate caller, and the workflow `runId`.
   - Index the spawning calls across the caller transcripts.
   - Keep an unresolved agent unresolved until evidence arrives.
   - Never link by timing or by a display name.

## 7. The feature set that sets us apart

### 7.1 Ranked features (T8)

The ranking is a product hypothesis. Test it with users, including users who are not developers.

| Rank by user value | Feature | Rank by how hard it is to copy |
|---|---|---|
| 1 | **Attach any time, all sessions:** a live rail of every running session in every project. No install. | 5 |
| 2 | **Catch-up card ("since you last looked"):** the goal, the current status, results, and files touched, in plain words, with evidence links. Both reviewers proposed it. | — |
| 3 | **Ask this session:** questions about one session, with cited answers (§7.2). | 4 |
| 4 | **Live and history in one place:** a live run becomes a searchable archive at once. | 3 |
| 5 | **Factual alerts:** at least 3 failures in a row, the same command repeated, an error burst. | 6 |
| 6 | **Reviewed lessons** from mistakes (§7.3). | 1 (once validated) |
| 7 | **Replay and scrub:** backlog. | 7 |

- **Correct trees** (`Agent` and `Task`, nested agents, async notifications, workflows, resumed agents) are the foundation of every feature above. They are not a separate line, and they are hard to copy (rank 2 in copy difficulty).
- **A plain-language inbox** of results and items that need attention, with the graph for drill-down. Astra proposed it, and it fits users who are not developers.
- **Possible addition: keep the transcripts that the app has observed.** Reviewed by `chatgpt/gpt-6-astra` with Claude Opus 5.5 (2026-10-24; agreed in round 3), not by a full council.
  - **Evidence (2026-10-24):** 82 Agent results reference IDs with no transcript found anywhere under the home folder. They belong to two sessions that are not Claude Desktop sessions.
  - Missing-transcript calls precede 2026-01-25T18:56Z; calls with a kept transcript follow 2026-01-25T20:14Z. The parent session files remain.
  - This pattern is consistent with one removal event. It does not prove that the transcripts were written, when they disappeared, or what removed them. Older calls without agent IDs do not prove missing transcripts that could be identified.
  - Keeping a copy could preserve transcripts that the app saw before their files disappeared. It cannot recover content that the app never saw.
  - This stays a backlog proposal. It needs its own design for storage, retention, deletion, and user control. The existing cache and search index follow the source files, so they are not a durable archive.

### 7.2 "Ask this session" (T9′)

- **Scope:** one session, its descendants, and the active branch, at an "as of" revision that the answer records.
- **Method:**
  1. Resolve the entities, counts, and joins in code, not in the LLM.
  2. Find passages with the FTS5 index.
  3. Let the LLM call **internal read-only tools** (outline, messages, steps, graph, search) for a capped number of steps.
  4. Give the LLM a bounded bundle of evidence.
  5. Require a locator for each claim, and check each locator against the archive.
  6. Abstain when the evidence is missing. Say "latest recorded action", not "what it is doing".
- **Provider:**
  - a local model (for example Ollama), or a key that the user configures;
  - the app shows the provider, the scope, and a cost cap before any remote call;
  - it never reuses Claude Code's tokens.
- **Privacy:**
  - The user consents to remote analysis within a scope that they choose.
  - The server enforces that scope on every tool call.
  - Transcripts are untrusted evidence. The LLM gets no tool that has side effects.
  - Retention and deletion rules cover prompts, answers, incidents, and lessons.
  - Regex removal of secrets is an extra layer, not a guarantee.
- **Proof that it helps:** a labeled set of questions, on live and archived sessions, that includes questions with no answer. Measure:
  - correctness;
  - whether the citations support the claims;
  - correct abstention;
  - cost;
  - time to the answer, against manual browsing.
- **Note:** the 5 MCP tools stay as they are (a ratified plan rule). The chatbot uses internal adapters. Exposing them through MCP later is a separate decision.

### 7.3 The learning loop (T10)

| Pattern | Found by code (a candidate) | Needs a judgment (an LLM or the user) |
|---|---|---|
| Tool failure | `is_error: true`, or a structured failure. A missing `is_error` stays unknown. | Was it avoidable? |
| Repeated calls | The same normalized tool and input, 3 or more times | Was the repetition wasteful? |
| Repeated reads | The same file read 3 or more times with no write between | Was rereading justified? |
| Permission denial | A structured denial | Was the attempted action a mistake? |
| Reverted edit | An exact inverse edit, when the full edit evidence exists | Did the revert fix a mistake? |
| "Done", then a failed test | A verified test failure after a "done" claim | Are the claim and the test about the same outcome? |
| A correction or a contradiction | Candidate passages only | Did the user correct an error, change the scope, or add new facts? |

- **Phrase matching alone is not enough.** justinchuby/flightdeck's knowledge extractor uses fixed phrases.
- **A lesson record holds:**
  - the evidence and the counter-evidence;
  - where the lesson applies;
  - the proposed action;
  - the reviewer's decision;
  - the detector and judge versions;
  - the criteria for a recurrence.
- **The flow:**
  1. The app shows a candidate lesson with its evidence.
  2. "Accept" shows a scoped diff for `CLAUDE.md` or memory.
  3. "Apply" is a separate, explicit action.
  4. The user can edit, dismiss, set an expiry, or roll back.
- **Proof that it helps:**
  - Measure detector precision against human labels.
  - Measure recurrence for each eligible opportunity, with holdouts or staggered adoption.
  - Never show a bare "reduced by X%".
  - Passive observation cannot prove that a lesson reached the agent.
- **Privacy:** the rules of §7.2 apply here too.

## 8. Phasing (T11)

| Phase | Content |
|---|---|
| 1 and 1b | Unchanged (ratified). |
| 2a | The amended LIVE-VIEW.md core, lifecycle liveness (§6.2), the resume contract (§6.5), and compatibility diagnostics. |
| 2b | Live subagents, the multi-session rail, the catch-up card, and factual alerts. |
| 2c | "Ask this session", on history first. |
| 2d | Reviewed lessons, after the detector precision is measured. |

- **No Phase 1c.** The mistake miner does not delay real time.
- **An optional spike beside Phase 1b** can measure detector precision on history. It is not a release gate.

## 9. Tests and risks (T12)

- **Correctness under load:**
  - missed watcher events;
  - partial UTF-8 lines;
  - malformed and oversized records;
  - rewrites that keep the same size;
  - replaced metadata files;
  - deleted transcripts;
  - late notifications;
  - duplicate usage;
  - resumed agents.
- **Load:** several tabs and several sessions at once, with the largest fixtures from the Phase 1 plan. Measure the time from a disk write to the screen separately from Claude Code's own buffering.
- **Limits:** bounded parsing, queues, retention, and graph updates. Merge repeated summaries, but keep every structural change, or force a fresh snapshot.
- **Alert fatigue:** require a threshold, for example 3 failures in a row. A `grep` that finds nothing returns exit code 1, which is normal.
- **Security:**
  - the localhost access controls (the Host-header guard);
  - path containment;
  - prompt injection from transcripts;
  - no `.key` reads.
- **Coverage disclosure:** mark agents that are stale or not tailed (beyond the 64-file limit). Alerts and chat answers inherit that coverage.
- **Platform:** the lifecycle files, `procStart`, and the PID checks are verified on macOS only. Linux and Windows are UNVERIFIED. On Windows, never use `os.kill(pid, 0)` (see CLAUDE.md).

## 10. Claims that we can make (T13′)

- "Claude Explorer needs nothing installed in your Claude Code sessions." This is true, but claude-devtools and zoetrope can claim the same.
- "Neither zoetrope nor Agent Flow keeps its own persistent activity archive: zoetrope replays existing transcripts, and Agent Flow's transcript attachment does not rebuild full prior activity."
- "justinchuby/flightdeck persists only the agents that it launches."
- **Do not claim** "competitors have no history".
- **Claim correct trees only after the Phase 1 tests prove them** (test-case report suites A and B).

## 11. Council record

- **Members:**
  - Claude Opus 5.5 (author and CTO);
  - `chatgpt/gpt-6-astra` (systems and product critic). It ran through a separate PAL process with the ChatGPT provider, because the running PAL server had not reloaded its config.
  - `gemini-3.8-flash` (product, UX, and differentiation).
- **Stop rule:** stop when all three agree, or after 3 rounds.
- **Result:** all three agreed in round 3.

### Round 1 — independent reviews

- Both reviewers: "approve with changes".
- **Astra, main points:**
  - Replace `<file>:<offset>` resume with an app sequence and cursors per source.
  - Separate liveness, activity, and outcome. Never say "stuck" or "awaiting permission".
  - My claim that a long tool shows nothing until it ends was UNVERIFIED.
  - Rank user value separately from copy difficulty.
  - Make the chatbot a bounded evidence reader.
  - Build the learning loop as an evidence review, with a table of deterministic and judged patterns.
  - No Phase 1c.
- **Gemini, main points:**
  - Retry sidecars until they parse.
  - Poll open sessions every 2–3 s.
  - Use a 10-minute active window, to avoid running out of file descriptors.
  - Rank the chatbot first for non-developers.
  - Add a catch-up card.
  - Add a permission-wait badge after 5 s of silence.
  - Use deterministic detectors with thresholds.
  - Move the mistake miner to Phase 3.
- **Facts checked by the author after round 1:**
  - F1: the start of a call is on disk, so Astra was right.
  - F3: no permission status exists.
  - F4: trailing bytes in lifecycle files.

### Round 2 — cross-critique of resolutions T1–T13

- **Gemini:** agreed with all 13.
  - It dropped its permission badge, because of F1 and F3.
  - It accepted the merged ranking with the catch-up card second.
  - It accepted that a 10-minute cutoff would drop quiet open sessions.
  - It added three P1s: cache lifecycle files by `(mtime, size)`; use `raw_decode`; test inotify limits.
- **Astra:** agreed with 8 and disagreed with 5, each with an exact change:
  - **T1:** reread on change, and keep a stale copy on failure.
  - **T2:** an open call is not proof of execution; check PID identity.
  - **T4:** followed sessions stay active at any age; rediscovery covers lifecycle files and root transcripts.
  - **T9:** keep consent, scope enforcement, and retention rules.
  - **T13:** exact wording about history.
- **Fact checked by the author:** F5, that `procStart` identifies the process (in UTC).

### Round 3 — final verdict

- The author adopted all five changes and Gemini's three P1s (T1′, T2′, T4′, T9′, T13′).
- **`chatgpt/gpt-6-astra`:** "VERDICT: AGREE WITH PLAN AS RESOLVED".
- **`gemini-3.8-flash`:** "No remaining P0 or P1 objections. … VERDICT: AGREE WITH PLAN AS RESOLVED".
  - The first call returned a 429: the free-tier limit on input tokens per minute.
  - The same model answered after a 30 s wait, with a shorter prompt.
- **Claude Opus 5.5:** agrees.

## 12. Open items (UNVERIFIED)

1. Whether lifecycle files exist, how often they update, and what they mean on Linux, on Windows, and for Cowork.
2. **Answered by the Time Machine backups (2026-10-24):** Claude Code's default 30-day cleanup ran on about 2026-02-24. Older files were restored later from backups, but no backup covered 2025-12-19 to 2026-02-25, so the 82 transcripts from that window are lost. Who did the restore is UNVERIFIED. Details are in the recovery notes below.
   - **Recovery research (2026-10-24):**
     - **No Anthropic API returns them.** The Messages API keeps no retrievable history. The Admin, usage, cost, and Claude Code analytics APIs return metrics only. The Compliance API covers sessions that Anthropic stores (cloud sessions), not local CLI files.
     - **Local CLI transcripts reach Anthropic only by an explicit share.** A "Yes" to the survey "Can Anthropic look at your session transcript?" uploads the transcript and its subagent transcripts, which are kept up to 6 months, so a January 2026 upload would have expired. `/feedback`, `/bug`, and `/share` uploads are kept 5 years, but the prompt history shows none in these two projects from 2026-01-13 to 2026-04-10. Neither path offers the user a download.
     - **The documented cleanup does not explain it.** The docs say that the cleanup deletes `<session>.jsonl` and `<session>/subagents/` together, by age (30 days by default). They do not mention the legacy top-level `agent-<id>.jsonl` files. And 191 main session files older than the 2026-01-25 boundary still exist, back to 2025-05-27, which a 30-day cleanup would have deleted.
     - **Time Machine search (2026-10-24): not recoverable.** The disk holds 42 backups. None is dated between 2025-12-19 and 2026-02-25. The 2026-02-25 backup has none of the 80 missing IDs, and the 2025-12-19 backup is older than the agents.
     - **The cause, from the backups:**
       - **The 2026-02-25 backup** holds only 26 main session files and 431 agent files, and no file older than 2026-01-25. That is exactly 30 days before the backup, so Claude Code's default 30-day cleanup ran on about 2026-02-24 and deleted every file older than about 2026-01-25, main sessions included.
       - **Later, older files were restored.** All 279 top-level files of the 2025-12-19 backup exist today with their old dates. 658 more files from before 2025-12-19 exist today from another source. Who restored them is UNVERIFIED.
       - **Today, no file in `~/.claude/projects` is dated between 2025-12-19 and 2026-01-25.** Files written in that window had no backup, so the restore could not bring them back. The 82 missing agent transcripts are in that window. Their two parent sessions survived because they were written after the cutoff.
       - **`cleanupPeriodDays` is now 3650,** so this cleanup does not repeat.
     - **One remaining lead, outside the API: a privacy access request.**
       - Anthropic's privacy policy gives a right to request a copy of your personal data, including your "Inputs" and "Outputs", by email to privacy@anthropic.com. Claude Code is not named in the policy.
       - For consumer accounts (Free, Pro, Max), the Claude Code docs say that Anthropic keeps Claude Code data 5 years if "allow data use for model improvement" is on, and 30 days if it is off.
       - So the data can exist only if that setting was on in January 2026. With it off, the data was deleted in February 2026.
       - A copy would hold API request and response content, not Claude Code's JSONL files. Each subagent call sends the subagent's history so far, so the last request of each subagent would hold almost its whole transcript. Whether a privacy export includes Claude Code API traffic is UNVERIFIED.
     - **A lesson for the product:** an app that keeps its own copy of what it observes would have saved these files (§7.1).
     - **Partial data that exists today:** each ghost's prompt, final result, and usage in the parent transcript (already used for ghost nodes); one TodoWrite file (`~/.claude/todos/<session>-agent-a094ac6.json`); and, for the Money session only, a 246 MB debug log whose MCP stderr includes PAL's DEBUG request options for calls in that period.
3. Whether `.meta.json` is written before the first line of the subagent transcript.
4. Whether other `status` values exist (only `idle`, `busy`, and `shell` were seen).
5. The latency from a disk write to the screen, against the competitors' "within a few seconds" claims.
6. Whether the ranking in §7.1 holds with real users.
7. Which providers a user can use for the chatbot without extra cost (local model, API key, or a subscription).

## 13. Sources

- **Deep dives:**
  - flightdeckhq/flightdeck at `2db326d6e452`;
  - justinchuby/flightdeck at `b8190f68b58d`;
  - matt1398/claude-devtools at `1486f2042c87`;
  - furkankly/zoetrope at `b1f31dd26bd4`;
  - patoles/agent-flow at `de99a3712bf6`.
- **The full research output** is in [research/2026.10.24-realtime-subagent-monitoring-research.json](./research/2026.10.24-realtime-subagent-monitoring-research.json). It holds the `path:line` evidence for each claim, the search logs, and 25 more tools. The workflow run ID is `wf_e332ae6b-e55`.
