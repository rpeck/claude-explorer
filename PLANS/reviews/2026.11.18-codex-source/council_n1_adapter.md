# Council: N1 source adapter, two design questions

- **Date:** 2026-11-19
- **Plan:** [2026.11.18-codex-source.md](../../2026.11.18-codex-source.md), §2 D3, §4.1, §6.1 rows N0, N1, N8
- **Result:** unanimous on both questions after 3 rounds.
- **Status:** a council recommendation. The user has not seen it yet. D3 itself does not change; only the §4.1 design text does.

## Council members

| Seat | Model that answered | Rounds |
|---|---|---|
| Lead | Claude Opus 5.5 (direct) | 1, 2, 3 |
| Google seat | `gemini-3.8-flash` (direct) in round 1; `gemini-3.7-flash` (direct) in round 2 | 1, 2 |
| GPT seat | `chatgpt/gpt-5.6-terra` | 1, 2, 3 |

- PAL ping: `chatgpt/gpt-5.6-terra`, answered "PAL OK".
- Round 2 to `gemini-3.8-flash` failed with **503 UNAVAILABLE**, not 429. The OpenRouter route was authorized only for a 429, so it was not used. The seat followed the agent's Gemini chain to `gemini-3.7-flash` (direct key), in the same PAL thread.
- Gemini calls: 3 (one of them the failed 503). This is the limit.
- The first GPT round-1 call failed before it reached the model: the files were too large for its budget (41K tokens, limit 29K). The retry sent the brief, a plan excerpt (§2 D1 to D16, §4.1, §6.1 rows), and `backend/store.py`. GPT did not see `claude_code_reader.py` or `cowork_reader.py`; the brief gave the facts from them with line numbers.

## Verified facts

Line numbers are on `main`. `backend/store.py`, `claude_code_reader.py`, and `cowork_reader.py` are the same on `integration/codex`. `search_index.py` line numbers are on `integration/codex`.

**The list path is cross-file. The coder's Q1 claim is true.**

- `store.py:589-638` `_get_all_conversations_data` has three branches, in the order AI, CODE, COWORK.
- AI (`store.py:606-618`): `_get_conversation_files()` (`store.py:413-448`, by-org plus legacy dedup), then `_load_conversation` (FileCache). It skips rows stamped `CLAUDE_CODE` or `CLAUDE_COWORK` (`store.py:615`).
- CODE (`store.py:620-624`) calls `list_claude_code_conversations` (`claude_code_reader.py:320-458`):
  - one agent index for all files (`:340`)
  - bulk `summary_cache.get_many` and `upsert_many` (`:387-394`)
  - the phantom filter (`:417`)
  - subagents from the agent index (`:423-434`)
  - `_dedup_resume_continuations` (`:458`)
- COWORK (`store.py:629-636`):
  - `full_content=True`: `list_cowork_conversations` (`cowork_reader.py:333-367`), which ends in `newest_per_uuid`.
  - Otherwise: `_cowork_summaries` (`store.py:640-675`), with the module cache `_cowork_summary_cache` (`store.py:349`) and `newest_per_uuid` across the local and remote roots (`store.py:675`).
- A loop of a per-path `read_summary` cannot give subagents, the cross-file dedup, `newest_per_uuid`, or the bulk SQLite read.

**The viewer and the index read in different ways.**

- `_find_conversation_data` (`store.py:777-890`) returns `(data, path)`, and `(None, None)` on a miss (`store.py:890`).
  - CODE data comes through `_load_conversation_cached` (FileCache).
  - COWORK reads every candidate dir and keeps the newest; the path is `dir/"audit.jsonl"`.
- `_load_conversation_at` (`search_index.py:2593`) reads CODE and COWORK **uncached**, and AI through `store._load_conversation` (cached).
- So one `load(path)` cannot serve both callers without a change. A `find(uuid) -> Path` followed by `load(path)` also reads each Cowork candidate twice.
- `_enumerate_conversation_paths(store)` (`search_index.py:2540`) already reads the roots from the store: `store._get_conversation_files()`, `store.claude_dir`, `store.cowork_root`, `store.remote_cowork_root`.

**Roots live on the store.**

- `ConversationStore(data_dir=, claude_dir=, cowork_root=)`: 62 call sites in 33 test files.
- Production calls `ConversationStore()` with no arguments, which reads the settings: `deps.py:41`, `cc_watcher.py:229` and `:572`, `routers/fetch.py:388`, `main.py:532` and `:801`, `mcp_server/server.py:140`, `cli/main.py:629`.
- The watcher's roots (`cc_watcher.py:516`, `:521`) equal the roots of `ConversationStore()`: `claude_dir/"projects"` and the local Cowork root. The watcher does not watch the Desktop conversations dir or the remote Cowork archive. No test patches these two helpers.

**Tests that bind to store internals.** The coder said "about 60 tests". The real count is 7 files, with about 20 bind points:

| Test file | Binds to |
|---|---|
| `test_store_find_diagnostics.py` | patch `backend.store.discover_jsonl_files`; caplog on logger `backend.store`; calls `_find_conversation_data` |
| `test_cc_detail_cache.py` | patch `backend.store.read_claude_code_conversation` (a re-export kept only for tests, `store.py:11-22`) |
| `test_search_cache.py` | patch `backend.store.json.load` |
| `test_lifespan_filecache_warm.py` | patch `backend.store.ConversationStore._find_conversation_data` |
| `test_store_layout.py` | calls `store._get_conversation_files()` |
| `test_cowork_list_cache.py`, `test_cowork_duplicate_sessions.py` | call `store_mod.clear_cowork_summary_cache()` |

**Source branches that the coder's list missed.**

- `store.py:559-563` (`_make_summary`): `has_branches` is forced False only for `CLAUDE_CODE`. `descriptor().chronological` cannot express this, because Cowork is chronological but computes the flag.
- **Latent bug, found by the Google seat and checked by the lead:** `has_branches` (`store.py:239-245`) counts children per `parent_message_uuid`. Cowork messages have none, so every Cowork list row with 2 or more messages has `has_branches: true`. The N0 golden `backend/tests/goldens/api/list.json` records `true` for both Cowork rows. N1 must keep it. A fix needs its own leaf and its own test.
- `store.py:915`, `:1126`: `is_chronological = source in (CODE, COWORK)`.
- `mcp_server/server.py:478`, `:568`: the source check lists the three ids.
- `search_index.py:2481` `_file_path_for_conv` has no production caller (one test, `test_remote_cowork_fetch.py:194`). It reads the Cowork root from the settings, not from the store.

**Ownership.** N1 owns `backend/store.py` and `backend/search_index.py` (source plumbing). It does not own `claude_code_reader.py`, `cowork_reader.py`, or any existing test file. N8 owns `backend/sources/codex.py`, `ids.py`, and `registry.py`.

## Q1: the protocol for listing

### Decision (unanimous): option A, refined

**Rules N1 must implement:**

1. `SourceAdapter` in `backend/sources/base.py` has these methods:
   - `descriptor(self) -> SourceDescriptor`
   - `watch_roots(self) -> list[WatchRoot]` (no argument; see Q2 rule 3)
   - `discover(self) -> Iterable[Path]`: the paths that the index stores as `indexed_files.path`
   - `list_rows(self, *, full_content: bool, include_phantom: bool) -> list[dict[str, Any]]`
   - `find(self, uuid: str) -> tuple[dict[str, Any], Path] | None`: for the viewer, export, and MCP; may use caches
   - `load_for_index(self, path: Path) -> dict[str, Any] | None`: takes any path from `discover()`; for Cowork it reads `path.parent`
   - `archive_sync(self, ctx: SyncContext) -> SyncReport | None`
   - `branch_flag(self, data: dict[str, Any], chat_messages: list[dict[str, Any]]) -> bool` (see Q2 rule 5)
2. `read_summary` is not in the protocol. No base class gives a generic `list_rows`. Each adapter writes its own `list_rows`, because `full_content=True` needs full messages and its own cache behavior.
3. Each port's `list_rows` returns exactly today's branch of `_get_all_conversations_data` (`store.py:606-636`), for both values of `full_content`:
   - the same dicts, in the same order
   - AI: skip rows stamped `CLAUDE_CODE` or `CLAUDE_COWORK`
   - CC: the agent index, the bulk `summary_cache`, the phantom filter, `_dedup_resume_continuations`; `FileCache.load_many_parallel` for full content
   - Cowork: `_cowork_summary_cache` and `newest_per_uuid`; `list_cowork_conversations` for full content
   - A source without phantoms ignores `include_phantom`.
4. `ConversationStore._get_all_conversations_data` becomes a loop over `get_adapters(self)` in registry order. It keeps an adapter when `source in ("all", adapter.descriptor().id)`.
5. `ConversationStore._find_conversation_data(uuid)` keeps its name, its signature, and its `(None, None)` miss. It asks the adapters in registry order and returns the first hit.
6. `load_for_index` keeps the semantics of `_load_conversation_at`:
   - CC and Cowork read uncached. They never fill the FileCache.
   - AI reads through `store._load_conversation` (cached).
   - `_load_conversation_at` keeps its try/except, its three log texts on logger `backend.search_index`, and its `source=None` fallback by file extension.
7. Registry order is behavior: `CLAUDE_AI`, `CLAUDE_CODE`, `CLAUDE_COWORK`. N8 appends `CODEX` after them.

### API names for the tester

- `SourceAdapter.list_rows(*, full_content, include_phantom)`
- `SourceAdapter.find(uuid)` returns `(data, path)` or `None`
- `SourceAdapter.load_for_index(path)`
- `SourceAdapter.discover()`, `SourceAdapter.watch_roots()`, `SourceAdapter.descriptor()`, `SourceAdapter.branch_flag(data, chat_messages)`, `SourceAdapter.archive_sync(ctx)`
- `ConversationStore._find_conversation_data(uuid)` returns `(data, path)` or `(None, None)`

### Seat positions per round

| Round | Lead (Opus 5.5) | Google seat | GPT (`gpt-5.6-terra`) |
|---|---|---|---|
| 1 | A; drop `read_summary` from the protocol but add a `PerFileAdapter` helper; split `find` (cached, returns data and path) from `load` (uncached, for the index) | A (3.8-flash); agree on the split; reject `PerFileAdapter` as speculative, because Codex also needs a multi-home dedup | A; reject `PerFileAdapter` (a summary loop breaks `full_content=True`); split the operations as `find_for_viewer` and `load_for_index`; keep the `(None, None)` miss; registry order is behavior |
| 2 | Concede `PerFileAdapter`; adopt `load_for_index`; keep the name `find` (viewer, export, and MCP all use it) | AGREE on every rule (3.7-flash) | AGREE on every Q1 rule |
| 3 | AGREE | (no call left) | AGREE |

## Q2: where the port code lives

### Decision (unanimous): option A, refined

**Rules N1 must implement:**

1. The ports `ClaudeAiAdapter`, `ClaudeCodeAdapter`, and `CoworkAdapter` live in `backend/sources/claude_ai.py`, `claude_code.py`, and `cowork.py`.
   - Each takes the store in its constructor and calls only store methods.
   - The per-source code stays in `store.py`, as one bridge method per source and operation.
   - The ports do not import the reader functions directly.
2. Every existing patch seam keeps working:
   - `backend.store.discover_jsonl_files`
   - `backend.store.read_claude_code_conversation`
   - `backend.store.json.load`
   - the WARNING on logger `backend.store` from the CC find scan
   - `ConversationStore._get_conversation_files`, `ConversationStore._find_conversation_data`, `ConversationStore._load_conversation`
   - `backend.store.clear_cowork_summary_cache`, `backend.store._cowork_summary_cache`
   - the constructor `ConversationStore(data_dir=, claude_dir=, cowork_root=)`
3. Inside a port, the roots come from the store, never from the settings. `watch_roots()` returns today's watcher roots exactly:
   - AI: none
   - CC: `claude_dir / "projects"`
   - Cowork: the local Cowork root only
4. Adapters hold no caches. The caches stay at module scope or on the store: `_cowork_summary_cache`, FileCache, `summary_cache`, `_DETAIL_DICT_CACHE`. Factories are cheap, so `get_adapters(store)` may build new instances on each call.
5. The CC-only branch rule (`store.py:559-563`) moves to `adapter.branch_flag(data, chat_messages)`:
   - AI and Cowork return today's computed value. Cowork keeps its latent `true`.
   - CC returns False.
   - `SourceDescriptor` keeps exactly the 9 fields of §4.1, as a frozen dataclass. The descriptor is the frontend JSON contract, and the UI never reads this flag.
6. Stored data with a missing or unknown source:
   - A missing source is `CLAUDE_AI`, as today, and uses that adapter.
   - A known source uses `get_adapter_or_none(source, store)`.
   - An unknown id gets `None` and keeps today's fallback: not chronological (`get_descriptor` returns `None`), `has_branches(chat_messages)` computed directly, and no `KeyError`.
   - `branch_flag` runs only when a list summary is built. Detail and tree chronology read `descriptor().chronological`.
   - `list_conversations` gets the adapters once per call, not once per row.
7. Dispatch rule: outside `backend/sources/` and the store's bridge methods, no code chooses behavior by comparing a source id. This covers store, search, search_index, cc_watcher, the routers, and MCP.
   - Allowed: the one-time SQL repairs in search_index that name a source, the `Literal` types (now `SourceId` and `SourceFilter`), and default values.
   - The MCP checks at `mcp_server/server.py:478` and `:568` validate against `get_descriptors()`.
8. Registry tests and the generator:
   - `test_source_registry.py` compares the `SourceId` members with `get_descriptors()` only after `reset_for_tests()`.
   - A fixture that registers a fake adapter calls `reset_for_tests()` in teardown.
   - `scripts/generate_sources_json.py` reads the built-in descriptors and never builds a store.
9. Moving the bridge code out of `store.py` into `backend/sources/` is not part of N1. It needs its own node after N8, and that node owns the 7 test files above. Codex, Pi, and Hermes are full adapters in `backend/sources/` and add nothing to `store.py`.

### API names for the tester

In `backend/sources/registry.py`:

- `AdapterFactory: TypeAlias = Callable[["ConversationStore"], SourceAdapter]` (a string annotation: `backend/sources/*.py` import `ConversationStore` only under `TYPE_CHECKING`)
- `register_adapter(descriptor: SourceDescriptor, factory: AdapterFactory) -> None`
- `get_descriptors() -> tuple[SourceDescriptor, ...]`
- `get_descriptor(source_id: str) -> SourceDescriptor | None`
- `get_adapters(store: ConversationStore) -> tuple[SourceAdapter, ...]`
- `get_adapter(source_id: SourceId, store: ConversationStore) -> SourceAdapter` (`KeyError` for an unknown id)
- `get_adapter_or_none(source_id: str, store: ConversationStore) -> SourceAdapter | None`
- `reset_for_tests() -> None`: restores the full, ordered built-in registry (never empty)

Class names: `ClaudeAiAdapter`, `ClaudeCodeAdapter`, `CoworkAdapter`; `SourceDescriptor` (frozen, 9 fields); `WatchRoot`.

### Seat positions per round

| Round | Lead (Opus 5.5) | Google seat | GPT (`gpt-5.6-terra`) |
|---|---|---|---|
| 1 | A; registry takes the store; a descriptor next to each factory; keep the patch seams; a descriptor field `detects_branches`; moving the code is a later node | A (3.8-flash); agree, except: no `detects_branches` (the descriptor is the frontend contract), use an adapter method; `TYPE_CHECKING` import only; stateless adapters | A ("store-bound compatibility ports"); `watch_roots()` with no argument; `reset_for_tests` restores the built-ins; parity test on the defaults only; an explicit bridge exception to the dispatch rule; wanted a descriptor bool `compute_has_branches` |
| 2 | Adopt Gemini's method (named `branch_flag`) and all of GPT's other points | AGREE on every rule (3.7-flash) | DISAGREE on Q2.2 and Q2.6: add `get_adapter_or_none`, use a string `TypeAlias`, spell out the unknown-source fallback |
| 3 | Fold GPT's amendments in (rules 6 and the registry list above). The Google seat had seen them in its round-2 call. | (no call left) | AGREE: "No real flaw remains in Q2.2 or Q2.6." |

## Changes to the plan text

**§4.1, the `SourceAdapter` method list:**

- Replace `read_summary(path) -> dict | None` with `list_rows(*, full_content: bool, include_phantom: bool) -> list[dict]`. Say that each adapter writes it, and that the three ports return today's per-source output.
- Change `find(uuid) -> Path | None` to `find(uuid) -> tuple[dict, Path] | None`.
- Rename `load(path)` to `load_for_index(path)`. Say that it reads uncached for CC and Cowork, as the index does today.
- Add `branch_flag(data, chat_messages) -> bool`.
- Change `watch_roots(settings)` to `watch_roots()`: the adapter is bound to a store.

**§4.1, the registry bullet:**

- Replace `get_adapters(settings)` and `get_adapter(source_id)` with the registry API above.
- Add: "Each factory takes the `ConversationStore`. The roots come from the store."
- Keep the sentence "A test registers a fake adapter through a fixture." Add: "The `SourceId` parity test runs after `reset_for_tests()` only."

**§4.1, the `SourceDescriptor` table:** add one sentence: "The descriptor has exactly these fields. Backend-only rules are adapter methods."

**§4.2, line 273** (the Codex list-summary cache) stays. It is how Codex writes `list_rows`.

**§6.1, the N1 row, "Done when":** add these items:

- Every existing test passes, and N1 edits none of them (the 7 files that bind to store internals included).
- No code outside `backend/sources/` and the store's bridge methods compares a source id (rule 7 of Q2 lists the exceptions).
- The registry order is `CLAUDE_AI`, `CLAUDE_CODE`, `CLAUDE_COWORK`.

**New leaf for a later wave (for the orchestrator to schedule):** Cowork list rows report `has_branches: true` (`store.py:239-245` and `:559-563`). The fix changes the N0 golden, so it needs its own leaf with its own test.

## Risks recorded, not N1 rules

- **`_DETAIL_DICT_CACHE` is keyed by uuid only** (`store.py:68`, used near `store.py:1064-1112`). When Codex arrives (N8), two sources can in theory share a uuid. Registry order then decides which conversation the viewer opens. N8 should decide whether the cache key needs the source or the path. (GPT, round 1.)
- **The bridge code stays in `store.py` until a later node moves it.** The three old sources are thin ports and the new ones are full adapters, so the layout is uneven for a while. Q2 rule 9 names the node that removes the unevenness.
- **`_file_path_for_conv`** (`search_index.py:2481`) reads the Cowork root from the settings and has no production caller. N1 leaves it as it is, because it changes nothing visible. A later cleanup can remove it.
