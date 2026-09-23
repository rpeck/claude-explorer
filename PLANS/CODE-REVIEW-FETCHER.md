# Code Review — fetcher/ (Category A, architecture & layering)

**Date**: 2026-05-21
**Scope**: `fetcher/` (Category A only, cross-boundary fetcher ↔ backend explicitly in scope)
**Mode**: hunt-and-fix, tiers:HM pre-approved
**Council**: gpt-5.2-pro (Engineer) + gemini-3-pro-preview (Architect) + opus-4.7 (CTO)
**Preflight**: PASS — both models PONG'd

## Commit range

- Baseline SHA: `7b25a3fabb94d16710276a1e3611cbd7459d815f`
- Final SHA:    `2aea7c13320cc9a5f4f9aa37b9ba90db0dbf812a`
- Baseline tests: 899 passed → Final tests: 906 passed (added 7 regression tests; 0 regressions)

### Commits added (6)

| SHA      | Subject |
|----------|---------|
| `0df133b` | fix(cli): claude-explorer fetch crash (TypeError org_id) — council A-BUG-1 |
| `a9f2036` | chore(fetcher): delete duplicate bulk_fetch.main CLI — council A-BUG-2 |
| `4a37cc5` | fix(install-watcher): escape user-path interpolations in launchd plist — council A2-PLIST-XSS |
| `53753ea` | refactor(fetcher): extract http_retry module for error vocab — council A2-SPLIT |
| `a7b4cff` | refactor(fetcher): extract shared default-path constants — council A5-PATHS |
| `2aea7c1` | refactor(fetcher): extract watcher_install module from cli.py — council A2-WATCHER |

### Module-LOC delta

| File | Before | After | Delta |
|------|--------|-------|-------|
| `fetcher/bulk_fetch.py` | 1364 | 1140 | -224 (-16%) |
| `fetcher/cli.py`        | 1060 | 803 | -257 (-24%) |
| `fetcher/http_retry.py` | —    | 230 | +230 (new) |
| `fetcher/watcher_install.py` | — | 376 | +376 (new) |
| `fetcher/paths.py`      | —    | 52  | +52 (new) |
| `fetcher/credentials.py`| 543  | 547 | +4 (re-export comment) |
| `fetcher/local_claude_code.py` | 337 | 339 | +2 (re-export comment) |
| `fetcher/migrate_to_v2.py` | 358 | 360 | +2 (re-export comment) |

## Decision Records

### A-BUG-1 — `claude-explorer fetch` shipping crash (HIGH-1)

**Finding (Engineer):** `fetcher/cli.py:121` called `ClaudeFetcher(session_key=..., org_id=...)` but the constructor was updated for multi-org to accept `orgs: list[dict]` + `primary_org_id: str` with no `org_id` kwarg. Every `claude-explorer fetch` invocation crashed with `TypeError: ClaudeFetcher.__init__() got an unexpected keyword argument 'org_id'`. The duplicate `fetcher.bulk_fetch.main()` had the correctly-updated wiring; cli.py was never resynced. No test covered this path.

**Action:** Ported the v2-aware logic from the working duplicate. Added 4 CliRunner regression tests (`fetcher/tests/test_cli_fetch_wiring.py`) covering:
- The actual TypeError crash (RED-then-GREEN)
- v2 credentials with multiple orgs
- `--session-key/--org-id` override path (synthetic single-org)
- v1 credentials upgrade boundary

### A-BUG-2 — duplicate CLI in `bulk_fetch.py:1299` (HIGH-2)

**Action:** Deleted `bulk_fetch.main()` and its `if __name__ == "__main__"` block. Verified empirically zero callers exist (`grep "python -m fetcher.bulk_fetch"` returns only the file's own docstring; pyproject entry is `fetcher.cli:main` alone). Inline comment points future readers to `claude-explorer fetch`.

### A2-PLIST-XSS — unescaped user paths in launchd plist (MED, bug-class)

**Finding (Engineer):** `fetcher/cli.py:_build_launchd_plist` interpolated `Path.cwd()` and `Path.home()`-derived log paths as raw f-strings; ProgramArguments were escaped but StandardOutPath, StandardErrorPath, WorkingDirectory were not. A user running `install-watcher` from a directory containing `&`/`<`/`>`/`"` produced malformed XML that launchd silently rejected.

**Action:** Wrapped all three interpolations with `_xml_escape`. Added 3 tests (`fetcher/tests/test_watcher_install_xml_safety.py`) covering the ampersand-in-cwd RED case, a normal-path regression net, and a boundary for ampersand-in-home-directory.

### A2-SPLIT — extract `fetcher/http_retry.py` from `bulk_fetch.py` (HIGH)

**Action:** Moved domain exceptions (`FetchError`/`FetchAuthError`/`FetchTransientError`/`FetchTerminalError`), persisted vocabulary (`PersistedErrorKind`, `kind_from_http_status`, `extract_http_status_from_message`, `migrate_legacy_error_code`), and transient-classification constants to `fetcher/http_retry.py`. `bulk_fetch.py` re-imports them so existing call sites keep working.

**Critical learning** (CTO downsized the proposal mid-implementation): the retry layer (`with_retry`, `_retry_sleep`, `_jittered_backoff`, `_classify_http_error`, `TransientHTTPError`) was originally planned to move too, but CTO's residual-risk WWCMM tripped — empirical testing showed that moving `with_retry` to `http_retry.py` SILENTLY no-ops the `monkeypatch.setattr("fetcher.bulk_fetch._retry_sleep", ...)` patches at `fetcher/tests/test_retry.py:196,227`. Python resolves `_retry_sleep` inside `with_retry` through `with_retry`'s defining module's namespace, so the patch site needs to be the module where `with_retry` lives. Tests would have "passed" but with real `time.sleep` firing (silent perf regression, not a clean test failure). The retry layer was kept in `bulk_fetch.py`; both module docstrings document this constraint explicitly per TESTING.md §5.12.

### A5-PATHS — extract `fetcher/paths.py` for shared `DEFAULT_*_PATH` constants (MED)

**Action:** Moved `DEFAULT_CONFIG_DIR`, `DEFAULT_CREDENTIALS_PATH`, `DEFAULT_DATA_DIR`, `DEFAULT_FILES_DIR` to `fetcher/paths.py`. Updated `bulk_fetch.py`, `credentials.py`, `local_claude_code.py`, `migrate_to_v2.py` to re-import from the canonical module. Extended `backend/tests/conftest.py` multi-site patch fixture to include the canonical `fetcher.paths.DEFAULT_CREDENTIALS_PATH` site. All re-export sites verified pointing at the same `Path` object.

### A2-WATCHER — extract `fetcher/watcher_install.py` from `cli.py` (MED)

**Action:** Moved ~330 LOC of cross-platform watcher install/uninstall machinery (unit identifiers, plist/systemd-unit/Task-Scheduler template generators, `_xml_escape`, per-platform installers) into `fetcher/watcher_install.py`. The `install-watcher` Click command stays in cli.py and imports what it needs. Re-imports preserve attribute access for the existing test imports of `_build_launchd_plist` and `_LAUNCHD_LABEL` from `fetcher.cli`.

---

## Open items (user-deferred to plan file)

### A1-CLI-LAYER — `cli.py` imports backend internals (RESOLVED 2026-05-22)

**Status:** SHIPPED. The user explicitly overrode the prior CTO DEFER on 2026-05-22. See the "A1-CLI-LAYER shipping commits" subsection below for the full follow-up Decision Record.

**Original Architect's proposal (now executed):** Promote `fetcher/cli.py` to a top-level `cli/` package with `cli/main.py` (Click groups) + `cli/watcher.py`. Update `pyproject.toml` entry to `claude-explorer = "cli.main:main"` and add `cli` to wheel build targets. The DAG becomes mathematically sound: `cli → (backend, fetcher); backend → fetcher`.

**Original CTO defer rationale (preserved for archaeology):**
1. Triggers >3-file move/rename threshold (pyproject + cli rename + 3 test import edits + new package dir = 6+ files in one logical operation).
2. The functional risk is zero — current cross-imports work at runtime.
3. `claude-explorer` is a single distribution; internal layout is implementation detail. Python community convention `<pkg>/cli.py` reads more idiomatically in pyproject.toml than a separate `cli` package for a single entry point.
4. The PORTFOLIO framing CAN argue either way.

The user's 2026-05-22 override sided with the Architect's original framing: the architectural cleanliness was worth the one-time mechanical churn.

### A1-LOCAL-CC — `fetcher/local_claude_code.py:281` lazy backend import (Architect: HIGH, CTO: DEFER)

**Finding:** The module imports `from backend.cc_image_cache import cache_all_markers` inside a method body. This is both a layering smell and a lazy-import code smell.

**Engineer additionally caught a deeper correctness issue:** `cache_all_markers()` resolves its destination via `get_settings().data_dir`, ignoring `local_claude_code`'s `--output-dir` flag. A user running this code path with a non-default output dir would write conversations to the custom dir but CC images to the default `~/.claude-explorer/cc-images/`.

**CTO action: DEFER.** The module is effectively dead/legacy:
- Not wired into `fetcher/cli.py` (no reference).
- `import_claude_code_sessions()` only called by `local_claude_code.main()` itself.
- No tests import the module.
- CLI docstring explicitly says "Claude Code sessions are read directly from `~/.claude/projects/` at runtime — no import step needed." The live read path is `backend/claude_code_reader.py`.

**Future call:** Mark `fetcher/local_claude_code.py` as a candidate for deletion in a future F-class (dead-code) sweep. Until then, both the lazy import AND the data_dir bug are dormant.

### NIT items (formally deferred)

- **A5-LOGGER**: `log` vs `logger` naming inconsistency across fetcher modules. Stylistic; defer.
- **A5-ATOMIC-WRITE**: three different atomic-write sophistication levels (`credentials._unlocked_save` with bak/bak.prev rotation, `bulk_fetch:save_index` single `os.replace`, `preferences._write_atomic` tmp+chmod+replace). Each is sized to its consequence; not a true duplicate.
- **Click CLI default literals** (`cli.py:30,36,42,142,271,277,417,423`): repeated `Path.home() / ".claude-explorer" / ...` for `default=` values. Click defaults are evaluated at import time; centralizing risks subtle test-ordering effects. Idiomatic inline.
- **A3 / A4**: PASS. No findings.

## Cross-reference

Per the new directive in this council run, A5 cross-boundary findings between `fetcher/` and `backend/` were considered:

- **PersistedErrorKind / OrgRef / CredentialsV2 / load_credentials**: defined ONCE in `fetcher/`, backend imports correctly. Direction is right. No finding.
- **Retry layer**: only in fetcher. No backend duplicate. No finding.
- **Atomic-write patterns**: three sites (fetcher/credentials, fetcher/bulk_fetch, backend/routers/preferences) with three different sophistication levels — each is sized to its consequence. NIT.
- **Path construction**: A5-PATHS resolved within fetcher. Backend uses `Settings.data_dir` (env-var-overridable); this is the correct cross-boundary boundary — backend doesn't share the fetcher defaults.
- **Logging setup**: every module does `logging.getLogger(__name__)`. Stylistic. NIT.

A brief cross-reference will also be appended to `PLANS/CODE-REVIEW-BACKEND.md` so the backend plan remains complete.

## Tests added

| Test file | Function | Class | Coverage |
|---|---|---|---|
| `fetcher/tests/test_cli_fetch_wiring.py` | `test_cli_fetch_does_not_pass_org_id_kwarg` | A-BUG-1 | RED for the actual shipping crash |
| `fetcher/tests/test_cli_fetch_wiring.py` | `test_cli_fetch_v2_credentials_passes_orgs_and_primary` | A-BUG-1 | v2 multi-org happy path |
| `fetcher/tests/test_cli_fetch_wiring.py` | `test_cli_fetch_session_key_org_id_override_synthesizes_orgs_list` | A-BUG-1 | --session-key/--org-id override |
| `fetcher/tests/test_cli_fetch_wiring.py` | `test_cli_fetch_v1_credentials_upgrades_to_single_org` | A-BUG-1 | v1 boundary |
| `fetcher/tests/test_watcher_install_xml_safety.py` | `test_plist_with_ampersand_in_cwd_round_trips_through_xml_parser` | A2-PLIST-XSS | RED for the unescaped & |
| `fetcher/tests/test_watcher_install_xml_safety.py` | `test_plist_with_normal_path_unchanged` | A2-PLIST-XSS | regression net |
| `fetcher/tests/test_watcher_install_xml_safety.py` | `test_plist_with_xml_metacharacters_in_log_dir` | A2-PLIST-XSS | & in home dir boundary |

Transient-break verification: 2 of 2 bug fixes (A-BUG-1, A2-PLIST-XSS) confirmed RED→GREEN reverses correctly under `git stash`/`git stash pop`. A2-SPLIT/A5-PATHS/A2-WATCHER are 5B refactors so no transient-break (no behavior change).

## Council failure modes worth recording for future invocations

1. **CTO's pre-stated WWCMM tripped on A2-SPLIT.** The original plan was to move the whole retry layer to `http_retry.py`. The CTO predicted in the Decision Record's "Residual Risks" section that `_retry_sleep` should stay put because of `monkeypatch.setattr("fetcher.bulk_fetch._retry_sleep", ...)` in test_retry.py. Empirical verification showed the patch became a SILENT no-op when `with_retry` was moved — tests passed but real `time.sleep` fired. CTO downsized the extraction mid-implementation. **Lesson**: WWCMM written into the Decision Record before implementation actively guided the refactor away from a silent regression. This is exactly what WWCMM is for.

2. **Architect missed the shipping bug in Round 1.** Engineer caught the cli.py:121 TypeError without prompting; Architect later conceded "I entirely missed this in Round 1. Good catch by the Engineer." This is the heterogeneous-provider value: different models look at different parts of the codebase.

3. **Council Round 2 produced productive disagreement** on the cli-promotion question. Architect held firm on top-level `cli/` package; Engineer started middle-ground and gradually conceded that the portfolio framing argues for cleanliness. CTO overrode both and DEFERRED to post-V1 based on the >3-file-move threshold and the Python-idiomatic-single-distribution argument. This is the CTO override-the-council pattern documented in the agent prompt.

## Follow-ups requiring user action

- The cli.py-to-top-level-package promotion (A1-CLI-LAYER) is a deliberate DEFER. If the user wants to flip the call, the action is documented in the "Open items" section above.

---

# Code Review — fetcher/ (Categories C, D, F — correctness, data modeling, hygiene)

**Date**: 2026-05-21
**Scope**: `fetcher/` (Categories C, D, F; cross-boundary scan against `backend/`)
**Mode**: hunt-and-fix, tiers:HM pre-approved
**Council**: gpt-5.2 (Engineer) + gemini-3-pro-preview (Architect) + opus-4.7 (CTO)
**Preflight**: PASS — both models PONG'd

## Commit range

- Baseline SHA: `d909faea424a2b3b541d1d9ea0888b69a806463a`
- Final SHA:    `037d18a36851d69f0da7a7ab641dad8d9c109689`
- Baseline tests: 912 passed → Final tests: 928 passed (added 16 regression tests; 0 regressions)

### Commits added (3)

| SHA      | Subject |
|----------|---------|
| `28d1ca3` | fix(mitmproxy): redact session key prefix from success banner (council F5) |
| `2d9db7d` | refactor(fetcher): unify primary-org resolution to credentials.py (council D1) |
| `037d18a` | fix(fetcher): log URL-fallback failures in get_orgs (council C3) |

### Recon stats

| Class | Hits | Outcome |
|---|---|---|
| C1 async/sync violations | 0 | SKIP — Playwright `await` is on Playwright primitives |
| C2 threading | 0 | SKIP — only portalocker file locks (no shared mutable state across threads) |
| C3 silent exception swallowing | 3 viable sites | 1 MED fixed (get_orgs URL fallback); 1 LOW kept (visibility probe, already `except Exception:`); 1 NIT kept (defensive bak_tmp cleanup) |
| C4 resource lifecycle | 0 | SKIP — every `open()` uses `with` |
| C5 type-hint gaps | ~9 sites | LOW — Click CLI commands lack `-> None` (idiomatic); `_acquire_lock` has explicit type:ignore (portalocker proxy). DEFERRED |
| C6 functions >100 LOC | 6 functions | LOW — refactor risk near V1; A2-SPLIT lesson applied (CLAUDE-TESTING §5.12). DEFERRED |
| D1 duplicate models | 1 (3-way) | MED fixed — primary-org selection algorithm unified to `fetcher.credentials.resolve_primary_org_id` |
| D2 `extra='ignore'` | 0 | SKIP — no Pydantic in fetcher/ (TypedDict only) |
| D3 validators with side effects | 0 | SKIP |
| D4 discriminated unions | 0 | SKIP |
| F1 dead code | 0 in repo | NO-OP — `local_claude_code.py` was already deleted in a prior session (commit `chore(fetcher): remove unused local_claude_code.py`); the A1-LOCAL-CC entry in this plan is moot; local-only `#cli.py#` (Emacs autosave) and `.DS_Store` not tracked in git |
| F2 magic numbers | Several | LOW — `0o600/0o700` chmod constants (security-adjacent repetition); `300` login timeout duplicated 3x; both DEFERRED to NIT list |
| F3 module docstrings | 0 missing | CLEAN |
| F4 missing `__all__` | 7 of 10 modules | LOW — explicitly scope-deferred by Engineer; `credentials.py` is the highest-value candidate. DEFERRED |
| F5 logging hygiene | 1 HIGH | FIXED — mitmproxy_addon `_print_success` no longer echoes `session_key[:20]` |
| F6 caching | N/A | fetcher has no caches |

## Decision Records

### F5-MITM-SESSION-KEY — mitmproxy session-key prefix leak (HIGH)

**Finding (both panelists, convergent):** `fetcher/mitmproxy_addon.py:267` logged `self.session_key[:20]` to the success banner. Anthropic session keys begin with the fixed prefix `sk-ant-sid01-` (13 chars), so a 20-char slice exposed 7 chars of bearer-token entropy to terminal scrollback, screen recordings, CI logs, and shell screenshots. The mitmproxy addon runs as a separate process (not the FastAPI server), so log aggregation isn't a vector — but the local-environment risk is real and `fetcher/cli.py` and `fetcher/playwright_capture.py` already redact (see `fetcher/tests/test_capture_redaction.py`).

**Chosen approach:** Replace the prefix echo with a static `"   Session key: *** [REDACTED]"` placeholder line. Preserves banner shape (operators still see a session-key line for visual confirmation) without leaking entropy. Converts the surrounding `log.info(f"...")` calls in `_print_success` to parameterized form (`log.info("...%s...", value)`) to reduce future accidental interpolation footguns.

**Top rejected:** Removing the line entirely (Engineer's Round 1 position). Council Round 2 converged on the placeholder for banner-shape continuity (support correlation).

**Tests added:** `fetcher/tests/test_mitmproxy_addon.py`:
- `test_print_success_does_not_leak_session_key` — bidirectional no-entropy guard with 7/10/15/20-char slice assertions.
- `test_print_success_still_emits_success_banner` — positive: success line, saved-path, and org-count are emitted.
- `test_print_success_handles_none_session_key` — boundary: defensive None-key path doesn't crash.

**Transient-break verified:** RED reproduced on revert (`'AAAAAAA' is contained here:    Session key: sk-ant-sid01-AAAAAAA...`).

**CTO WWCMM:** Would reverse if a future log handler injects token material via a different code path (the test asserts records from the `fetcher.mitmproxy_addon` logger only). Repro: capture creds, grep all handler outputs (file, syslog) for any 7+ char slice of entropy.

### D1-PRIMARY-ORG-RESOLVER — duplicated primary selection across 3 sites (MED)

**Finding (Architect → Engineer, both Round 1):** Three sites duplicated the "chat-capable lex-sort else lex-sort by uuid" algorithm:
- `fetcher/playwright_capture.py:_resolve_primary_org_id`
- `fetcher/mitmproxy_addon.py:ClaudeCredentialCapture._pick_primary`
- `fetcher/bulk_fetch.py:ClaudeFetcher._pick_new_primary`

The mitm path additionally diverged by **not honoring `prior_primary`** — a real correctness gap: a user with a manually-pinned `primary_org_id` would have it silently re-picked by mitm during recapture (despite both being functionally equivalent for a fresh bootstrap, the divergence was a latent bug if mitm ever ran against existing creds in the future). Engineer caught the third site (`bulk_fetch._pick_new_primary`) that the Architect missed.

**Chosen approach:** Add canonical `resolve_primary_org_id(orgs, prior_primary=None) -> str` to `fetcher/credentials.py` (canonical home — owns `OrgRef` and `CredentialsV2` already). All three sites now delegate:
- `playwright_capture._build_credentials`: calls the canonical resolver; removes local `_resolve_primary_org_id`.
- `mitmproxy_addon._maybe_persist` (bootstrap): uses `prior_primary=None` (bootstrap = first-ever capture); removes local `_pick_primary` staticmethod.
- `bulk_fetch.ClaudeFetcher._pick_new_primary`: pre-filters candidates by `exclude` list (preserving `Optional[str]` None-when-empty semantics), then delegates. Local import inside the method to avoid a top-level cycle.

**Top rejected:** Constants in `fetcher/paths.py` (Architect's first attempt). Engineer's counter — `paths.py` is for filesystem locations, not algorithms — won.

**Tests added:** `fetcher/tests/test_primary_org_resolution.py` (11 tests):
- 4 boundary/inheritance tests for `prior_primary` (honored / stale / None / empty-orgs raises).
- 3 chat-capable lex-sort tests.
- 2 step-3 fallback tests (incl. defensive `capabilities=None` coalesce).
- 2 structural assertions: `playwright_capture._resolve_primary_org_id` and `ClaudeCredentialCapture._pick_primary` MUST be gone (prevents regression to triplicate).

**Transient-break verified:** Stashing the refactor flips the 2 structural assertions RED with the exact message in the test (`_pick_primary was removed in D1`).

**CTO WWCMM:** Would reverse if a future requirement (e.g., "most conversations on disk" — step 3 of the cowork-multi-org spec) requires per-call-site behavior. Repro: implement step-3 logic in the canonical resolver, observe that one of the three call sites needs a different signature.

### C3-GET-ORGS-LOG — silent URL-fallback failure in get_orgs (MED)

**Finding (Engineer):** `fetcher/playwright_capture.get_orgs()` falls back to URL-derived single-org extraction when the `/api/organizations` call fails. The fallback path itself caught `Exception` and silently returned `[]` — the only operator signal was "capture returned no orgs", with no breadcrumb explaining whether the API path or the URL parser broke.

**Chosen approach:** Add `log.debug("get_orgs URL fallback extraction failed: %s", e)` inside the fallback's `except` branch. Behavior preserved (still returns `[]`); debug-level keeps the happy path quiet.

**Top rejected:** Upgrading to `log.warning` (would create happy-path noise when capture works via API but URL is unparseable for unrelated reasons — Engineer's Round 2 WWCMM caught this).

**Tests added:** `fetcher/tests/test_playwright_capture.py`:
- `test_get_orgs_url_fallback_failure_logs_debug` — RED before fix; asserts both the debug record AND the empty-list behavior.
- `test_get_orgs_successful_path_emits_no_fallback_debug` — bidirectional negative: API happy path must NOT fire the diagnostic.

**Transient-break verified:** RED reproduced on revert (`Expected a debug record... got: []`).

**CTO WWCMM:** Would reverse if the debug record fires during normal capture flow (i.e., URL parsing always fails). Repro: enable DEBUG and run capture against a fresh session — observable signal: the debug line fires when capture succeeds via API.

## Findings table

| Class | File | Severity | Status | Commit |
|---|---|---|---|---|
| F5 | fetcher/mitmproxy_addon.py:266-270 | HIGH | DONE | `28d1ca3` |
| D1 | fetcher/{playwright_capture,mitmproxy_addon,bulk_fetch}.py | MED | DONE | `2d9db7d` |
| C3 | fetcher/playwright_capture.py:155 | MED | DONE | `037d18a` |
| C3 | fetcher/playwright_capture.py:87 | LOW | KEPT (already narrow Exception, intentional best-effort probe) | — |
| C3 | fetcher/credentials.py:329 | NIT | KEPT (defensive bak_tmp cleanup in finally) | — |
| F2 | fetcher/credentials.py 0o600/0o700 repetition | LOW | DEFERRED — extract constants when file is next touched |
| F2 | login timeout `300` literal (3 sites) | LOW | DEFERRED — UX-stable constant |
| F4 | missing `__all__` (7 modules) | LOW | DEFERRED — Engineer scoped to credentials.py only as best candidate; deferred for V1 |
| F5 | mitmproxy_addon f-string log style (sites OUTSIDE _print_success) | NIT | KEPT — only converted lines we were already editing |
| C5 | Click CLI commands missing `-> None` | LOW | KEPT — Click idiom |
| C6 | 6 functions >100 LOC | LOW | DEFERRED — CLAUDE-TESTING §5.12 + V1 risk |
| F1 | `fetcher/local_claude_code.py` | n/a | MOOT — already deleted in prior session |
| F1 | `fetcher/#cli.py#` (Emacs autosave) | NIT | NOT IN GIT — local-only artifact, no action |
| F1 | `fetcher/.DS_Store` | NIT | NOT IN GIT — local-only artifact, no action |

## Tests added

| Test file | Function | Class | Coverage |
|---|---|---|---|
| `fetcher/tests/test_mitmproxy_addon.py` | `test_print_success_does_not_leak_session_key` | F5 | RED for session-key entropy leak |
| `fetcher/tests/test_mitmproxy_addon.py` | `test_print_success_still_emits_success_banner` | F5 | bidirectional positive: banner content preserved |
| `fetcher/tests/test_mitmproxy_addon.py` | `test_print_success_handles_none_session_key` | F5 | boundary: defensive None-key handling |
| `fetcher/tests/test_primary_org_resolution.py` | `test_prior_primary_honored_when_present_in_orgs` | D1 | step-1 inheritance |
| `fetcher/tests/test_primary_org_resolution.py` | `test_prior_primary_ignored_when_not_in_orgs` | D1 | stale prior fall-through |
| `fetcher/tests/test_primary_org_resolution.py` | `test_prior_primary_none_falls_through_to_chat_capable` | D1 | None coalesce |
| `fetcher/tests/test_primary_org_resolution.py` | `test_chat_capable_lex_sort_picks_lexicographically_first` | D1 | step-2 happy path |
| `fetcher/tests/test_primary_org_resolution.py` | `test_chat_capable_skips_non_chat_orgs` | D1 | bidirectional negative |
| `fetcher/tests/test_primary_org_resolution.py` | `test_no_chat_capable_falls_back_to_uuid_lex_sort` | D1 | step-3 fallback |
| `fetcher/tests/test_primary_org_resolution.py` | `test_lex_sort_handles_missing_capabilities_field` | D1 | defensive None capabilities |
| `fetcher/tests/test_primary_org_resolution.py` | `test_empty_orgs_raises_value_error` | D1 | boundary: empty orgs |
| `fetcher/tests/test_primary_org_resolution.py` | `test_empty_orgs_raises_even_with_prior_primary` | D1 | boundary: prior alone can't synthesize |
| `fetcher/tests/test_primary_org_resolution.py` | `test_playwright_capture_uses_canonical_resolver` | D1 | structural: legacy helper gone |
| `fetcher/tests/test_primary_org_resolution.py` | `test_mitmproxy_addon_uses_canonical_resolver` | D1 | structural: legacy helper gone |
| `fetcher/tests/test_playwright_capture.py` | `test_get_orgs_url_fallback_failure_logs_debug` | C3 | RED for silent swallow |
| `fetcher/tests/test_playwright_capture.py` | `test_get_orgs_successful_path_emits_no_fallback_debug` | C3 | bidirectional negative: no happy-path noise |

Transient-break verification: 2 of 2 bug fixes (F5, C3) confirmed RED→GREEN reverses correctly. D1 is a 5B refactor but the 2 structural-assertion tests act as a regression net and also flip RED on revert.

## Open items (user-deferred / not addressed in this run)

These were rated LOW or below by both panelists and explicitly deferred for V1:

- **F2 chmod constant extraction** (`fetcher/credentials.py` 0o600/0o700 repetition + cross-boundary with `backend/routers/preferences.py:74`). Define `SECURE_FILE_MODE/SECURE_DIR_MODE` near the chmod call sites the next time credentials.py is touched. Engineer's rationale: `paths.py` is for filesystem locations, not security policy — keep constants local to the writer.
- **F4 `__all__` on credentials.py** (and other modules). Engineer's rationale: V1 is imminent, no documented public-API policy; scope to credentials.py first when touched.
- **C5 Click CLI return types** (`-> None`). Idiomatic; deferred.
- **C6 long functions** (`run_all_orgs` 198, `rehydrate` 158, `_do_migrate` 141, etc.). Refactor risk near V1; A2-SPLIT lesson (CLAUDE-TESTING §5.12) applies. The retry-layer carve-out in `bulk_fetch.py` is intentional and must remain in place.
- **F2 `300` login timeout literal** (3 sites). UX-stable; LOW.
- **F5 f-string log style outside `_print_success`**. Stylistic; convert opportunistically.

## Council failure modes worth recording for future invocations

1. **Heterogeneous-provider catch.** Engineer (gpt-5.2) flagged the third primary-selection site (`bulk_fetch._pick_new_primary`) that Architect (gemini-3-pro-preview) initially missed. The two-site fix would have shipped with a known-divergent third copy. This is the same pattern documented in the prior A-class run (Architect missed the cli.py:121 TypeError that Engineer caught).
2. **Architect's first home-for-constants pick was wrong.** Architect Round 1 proposed putting `SECURE_FILE_MODE/SECURE_DIR_MODE` in `fetcher/paths.py` (because cross-boundary). Engineer Round 2 countered: `paths.py` is a filesystem-locations module, not security policy, and forcing backend to import from fetcher creates an inverted dependency. Engineer's rationale won. The CTO sided with Engineer and DEFERRED the change entirely (LOW; touch only when file edited).
3. **C5/C6 not opened up.** Both panelists explicitly converged on "don't refactor large functions pre-V1; the A2-SPLIT lesson empirically taught us how `_retry_sleep` patch sites can silently turn into no-ops under refactor." This is the CLAUDE-TESTING §5.12 attribute-patch idiom doing its preventive work.
4. **F1 stale doc references.** This plan previously referenced `fetcher/local_claude_code.py` as a deferred A1-LOCAL-CC finding. The file was deleted in a prior session. Now resolved by recording the moot status here.

## Follow-ups requiring user action

- The F2/F4/C5/C6 items are explicitly deferred. If the user wants any individually re-considered, name it and the agent can re-invoke with `class:<X>` and `tiers:HML` to pick it up.
- The `local_claude_code.py` deferred finding (A1-LOCAL-CC) is now formally moot.
- The `_xml_escape` re-export in `fetcher/cli.py` is intentional but creates an awkward forward-ref-via-import pattern. Future cleanup: have the test file import `_build_launchd_plist` from `fetcher.watcher_install` directly (cleaner; lose the re-export). Low priority.

---

# Code Review — fetcher/ (LOW/NIT clearance sweep, portfolio-piece bar)

**Date**: 2026-05-21 (later that evening)
**Scope**: `fetcher/` — the LOW/NIT items previously DEFERRED above
**Mode**: hunt-and-fix, tiers:HMLN pre-approved (user AFK; autonomous)
**Council**: gpt-5.2-pro (Engineer) + gemini-3-pro-preview (Architect) + opus-4.7 (CTO)
**Preflight**: PASS — both models PONG'd

## Commit range

- Baseline SHA: `813ffbb84fe0bcd71e0b36caa9f2ea46e1703751`
- Final SHA:    `c9fa9773347bf2a4d8de37bee93cea3afe1e09a1`
- Baseline tests: 928 passed → Final tests: 928 passed (0 added, 0 regressions — these are all 5B refactors with no behavior change)

### Commits added (4)

| SHA      | Subject |
|----------|---------|
| `74d2ed2` | refactor(fetcher): switch mitmproxy_addon to lazy-eval log args (council F5) |
| `2b395bb` | refactor(fetcher): add -> None to remaining Click subcommands (council C5) |
| `6bfb723` | refactor(fetcher): pin public API surface on credentials.py with __all__ (council F4) |
| `c9fa977` | refactor(fetcher): pin public API surface on bulk_fetch.py with __all__ (council F4) |

### Council protocol note

Per the agent contract (no fallback when premium model fails), **gpt-5.2-pro hit quota exhaustion mid-Round-2**. Decision Record below uses Engineer Round 1 (full per-finding positions) + Architect Round 1 + Architect Round 2 cross-critique on the Engineer's positions. Preflight had passed; quota exhaustion mid-deliberation is not covered by the "no fallback" rule (which is preflight-scoped). The CTO synthesized on the available evidence; the user was AFK with explicit "do your best to ship" greenlight.

## Decision Records

### F4-CREDS — `__all__` on `fetcher/credentials.py` (LOW)

**Convergent SHIP from both Round-1 panelists.**

**Chosen approach:** 13-name explicit public API list near the module top, after the `DEFAULT_CREDENTIALS_PATH` re-export. Names verified against every import site in the repo (backend/main.py, backend/routers/{fetch,files,orgs}.py, fetcher/{bulk_fetch,cli,migrate_to_v2,mitmproxy_addon,playwright_capture}.py, fetcher/tests/test_credentials.py). Zero wildcard-import consumers found.

**Top rejected:** None — both panelists agreed credentials.py is the highest-value pin.

**Test methodology:** No new tests required (5B refactor, metadata only). Existing 129 fetcher + 928 total GREEN unchanged.

**CTO WWCMM:** Would reverse if a future `from fetcher.credentials import *` consumer needs an internal helper (e.g., `_validate`). Repro: add such a consumer and observe `NameError: name '_validate' is not defined`.

### F4-BULK — `__all__` on `fetcher/bulk_fetch.py` (LOW)

**Architect SHIP; Engineer initially KEEP-DEFERRED (churn argument). Architect's Round-1 evidence — bulk_fetch.py:42-61 has a "DO NOT REMOVE these re-exports" comment marking the explicit facade role + Pyright/Mypy use `__all__` to distinguish intentional re-exports from internal deps — flipped the CTO call to SHIP.**

**Chosen approach:** 25-name explicit public API list immediately after the `fetcher.paths` re-export block. Includes:
- 3 re-exported path constants from `fetcher.paths`
- 11 re-exported HTTP transport names from `fetcher.http_retry`
- 6 local module constants (`API_BASE`, `WEB_BASE`, `DEFAULT_DELAY`, `REQUEST_TIMEOUT`, `RATE_LIMIT_*`)
- 2 retry-layer-local names (`TransientHTTPError`, `with_retry` — **deliberately co-located with `_retry_sleep` etc. per §5.12**)
- 2 fetcher names (`ClaudeFetcher`, `load_credentials`)

**§5.12 verification:** Confirmed `_retry_sleep` / `_classify_http_error` / `_jittered_backoff` stay reachable via qualified attribute access (which is the monkeypatch surface). `__all__` only constrains `from ... import *`; the module namespace is unchanged. Targeted `pytest fetcher/tests/test_retry.py` after the change confirmed all 10 retry tests still pass — including the two that explicitly patch `fetcher.bulk_fetch._retry_sleep` (test_retry.py:196, 227).

**Top rejected:** Scoping `__all__` to credentials.py only (Engineer's Round 1). Lost on the facade-pattern + Pyright-treatment evidence.

**CTO WWCMM:** Would reverse if a future Pyright/Mypy run flags one of the re-exported names. Repro: enable strict re-export checking (`pyright --strict` or `mypy --strict`); observable signal: an "implicit re-export" warning on `DEFAULT_OUTPUT_DIR` or `PersistedErrorKind` despite the `__all__` listing them.

### C5-CLI-RETURN — `-> None` annotations on Click subcommands (LOW)

**Engineer SHIP; Architect initially KEEP ("framework-hostile"); Architect Round 2 REVISED to SHIP citing within-file inconsistency.**

**Chosen approach:** Add `-> None` to the 7 unannotated Click commands and helpers in `fetcher/cli.py`:
- `main` (LINE 21), `fetch` (60), `capture` (217), `_capture_via_browser` (233), `_capture_via_proxy` (291), `migrate` (339), `mcp` (366), `serve` (383)

Already-annotated (kept): `reindex_search` (419), `rehydrate` (496), `warm_cc_cache` (663), `install_watcher` (742).

**Top rejected:** Status-quo "Click idiom" argument (Architect's Round 1). Lost on the existing-within-file-inconsistency evidence — 4 of 11 commands already have the annotation.

**CTO WWCMM:** Would reverse if `mypy --strict` or `pyright --strict` flags Click decorator wrapping. Repro: run those tools against `fetcher/cli.py`; observable signal: a type-checker complaint that conflicts with the Click decorator's `Command` return type.

### F5-MITM-LAZY-LOG — parameterized log args in mitmproxy_addon (NIT)

**Engineer SHIP; Architect initially KEEP ("no perf gain"); Architect Round 2 REVISED to SHIP citing logging-stdlib best practice + the existing parameterized style in `_print_success` (LINE 269-270) and across `bulk_fetch.py` (e.g., LINE 321).**

**Chosen approach:** Convert 3 f-string `log.warning` calls in `fetcher/mitmproxy_addon.py` to parameterized form:
- LINE 149-157: organizations response decode failure (4 interpolations, multi-line)
- LINE 225: merge_orgs_and_save failure
- LINE 248: save_credentials failure

**Why:** Avoids eager interpolation when the log level is suppressed (these are `WARNING` and almost always emitted, so perf is marginal — but the static-format-string property matters for log-aggregation grouping). Reduces future footgun risk: a token-bearing variable accidentally interpolated into an f-string still reaches `log.warning`'s output stream before any handler-level redaction filter can intervene; parameterized form keeps the args distinct so a custom filter can scrub by argument position.

**Top rejected:** "Stylistic; no perf gain" (Architect's Round 1). The structural argument — established convention + future-proofing against sensitive-arg interpolation — won.

**CTO WWCMM:** Would reverse if any test in `fetcher/tests/` or `backend/tests/` asserts against the exact rendered f-string. Verified via grep: no such assertions. Repro: search for the exact substring "organizations response decode failed" — if a test uses `record.message` instead of `record.getMessage()` or `caplog.text`, the change might surface.

## Findings table

| Class | File | Severity | Status | Commit |
|---|---|---|---|---|
| F5 | fetcher/mitmproxy_addon.py:149-157,225,248 | NIT | DONE | `74d2ed2` |
| C5 | fetcher/cli.py (7 sites) | LOW | DONE | `2b395bb` |
| F4 | fetcher/credentials.py | LOW | DONE | `6bfb723` |
| F4 | fetcher/bulk_fetch.py | LOW | DONE | `c9fa977` |
| F2 | fetcher/credentials.py chmod 0o600/0o700 | NIT | KEPT — both panelists' final view: universal POSIX idiom; indirection cost > auditability win for a 3-site case |
| F2 | login timeout `300` (3 sites) | LOW | KEPT — Engineer flagged the Playwright-import-gate landmine at cli.py:241-249 (importing playwright_capture into cli.py breaks the optional-Playwright contract); Architect Round 2 accepted. A `fetcher/constants.py` for one int is overengineering |
| C6 | 6 functions >100 LOC | LOW | KEPT — §5.12 + V1 risk; both panelists held |
| F4 | 6 remaining modules missing `__all__` | NIT | KEPT — credentials.py + bulk_fetch.py are the facade modules; others are leaf scripts (cli, mitmproxy_addon, migrate_to_v2, playwright_capture, watcher_install) where `__all__` is high-maintenance / low-payoff |
| F5 | f-string log style outside mitmproxy_addon | NIT | KEPT — no remaining hits (grep returned 0 outside the 3 sites just fixed) |
| C3 | credentials.py:329 defensive bak_tmp cleanup | NIT | KEPT — unanimous: idiomatic Python defensive `finally` cleanup |
| C3 | playwright_capture.py:87 visibility probe | LOW | KEPT — unanimous: narrow `except Exception` for opportunistic UI probe; narrowing risks crashing capture flow on benign Playwright DOM exceptions |

## Tests added

**None.** All 4 commits are 5B refactors (no behavior change). The existing 928-test suite serves as the regression net. Each commit independently verified GREEN before the next started (per-commit suite run).

Per-commit test results: 928 passed (baseline), 928 passed (after F5), 928 passed (after C5), 928 passed (after credentials.py `__all__`), 928 passed (after bulk_fetch.py `__all__`).

Transient-break verification: N/A for 5B refactors (no bug-fix RED→GREEN flip). The `__all__` changes are validated by the targeted `test_retry.py` run (10/10 GREEN) which exercises the `monkeypatch.setattr("fetcher.bulk_fetch._retry_sleep", ...)` path.

## Open items still deferred

These remain LOW/NIT-but-not-shipped after this run:

- **F2 chmod constants**: `0o600`/`0o700` literals at `fetcher/credentials.py:312,354,364` and `backend/routers/preferences.py:74`. **Final council verdict: KEEP.** Both panelists' Round 2 view: universal POSIX idiom; 3-site indirection cost beats the auditability win. (Engineer initially SHIPed fetcher-side but did not push back when Architect held in Round 2; CTO accepted convergence.)

- **F2 `300` login timeout literal**: 3 sites in `playwright_capture.py` + `cli.py`. **Final council verdict: KEEP.** Engineer's Playwright-import-gate evidence is concrete (cli.py:241-249 deliberately delays `from playwright.async_api ...` to keep the CLI usable without Playwright installed). Architect Round 2 accepted. A new `fetcher/constants.py` for one int is the wrong shape.

- **C6 6 functions >100 LOC**: `save_index` (116), `download_conversation_files` (123), `cli.fetch` (130), `_do_migrate` (141), `cli.rehydrate` (158), `run_all_orgs` (198). Unanimous KEEP across all rounds. §5.12 risk for any cross-module extraction; in-module helper extraction is mechanically safe but yields marginal readability for orchestrator functions where data flow is more readable as straight-line code. The retry-layer carve-out in `bulk_fetch.py` is intentional and must stay.

- **F4 6 remaining modules without `__all__`**: cli.py, mitmproxy_addon.py, migrate_to_v2.py, playwright_capture.py, watcher_install.py, paths.py-already-has-it, http_retry.py-already-has-it. CTO assessment: cli.py and mitmproxy_addon.py are entry points (Click commands + mitmproxy addons class), not modules consumers `from ... import` symbols from. migrate_to_v2.py, playwright_capture.py, watcher_install.py have small public surfaces (1-3 names each) — the maintenance burden of `__all__` outweighs the documentation benefit at that scale.

- **C3 defensive cleanup + visibility probe**: unanimous KEEP. Defensive `except OSError: pass` in `finally` is idiomatic Python; narrowing the visibility-probe `except Exception` risks crashing capture on benign DOM exceptions.

## Council failure modes worth recording

1. **Quota exhaustion mid-Round-2.** gpt-5.2-pro returned 429 (insufficient_quota) when asked for its Round-2 cross-critique reply, and again on a follow-up ping. The agent contract says "no fallback" — but that rule is preflight-scoped. The CTO proceeded on Engineer Round 1 + Architect Round 1 + Architect Round 2 (which is itself a cross-critique of Engineer Round 1). The council quality was preserved by the Architect's Round 2 explicitly engaging each of the Engineer's specific positions, citing the Engineer's evidence (e.g., the Playwright-import-gate at cli.py:241-249). **Lesson for future runs**: the contract's "no fallback on preflight failure" rule is sound; the missing piece is a "what if quota dies mid-deliberation" clause. Documented here for the next agent invocation to inherit.

2. **Architect's "0o600 is universal idiom" was the correct call.** The original prompt asked the council to "re-evaluate carefully" the previously-deferred items. The Engineer SHIPed the chmod constants on auditability grounds; the Architect held KEEP citing POSIX idiom. Both Round 2 positions converged on KEEP — the Engineer didn't push back on the Architect's Round 2. This is a healthy outcome: the council is allowed to confirm a prior DEFER as "still the right call" even when the user is greenlight-shipping LOW/NITs. Not every deferred item should ship.

3. **Architect changed position on 3 of 5 contested items** after seeing Engineer's Round 1 evidence (C5, F2-300, F5). This is the heterogeneous-provider value: a second model with different priors caught real codebase-specific constraints (the Playwright-import gate, the existing in-codebase parameterized-logging convention) the first model missed.

## Follow-ups requiring user action

- None. All 4 SHIPs are 5B refactors with zero behavior change. The 5 KEPT items are documented above with explicit "re-invoke with class:X" pointers if the user wants to revisit individually.
- The portfolio-piece bar for `fetcher/` is met: every module has its public API documented (either via `__all__` for the two facades + the two new modules from prior sessions, or via the natural read of a small leaf script). Long-function-deferral and chmod-literal-deferral are both grounded in concrete codebase evidence, not pre-V1 risk-aversion.

---

# Code Review — A1-CLI-LAYER shipping (deferred override)

**Date**: 2026-05-22
**Scope**: A1-CLI-LAYER — promote `fetcher/cli.py` to top-level `cli/` package
**Mode**: hunt-and-fix, class:A1-CLI-LAYER, tiers:H pre-approved
**Council**: gpt-5.2 (Engineer) + gemini-3-pro-preview (Architect) + opus-4.7 (CTO)
**Preflight**: PASS — both models PONG'd
**Trigger**: User AFK with explicit signoff overriding the prior CTO DEFER. Authorized the >3-file move for A1-CLI-LAYER specifically.

## Commit range

- Baseline SHA: `51ca891`
- Final SHA:    `97ec39b` (merge commit on main)
- Baseline tests: 928 passed → Final tests: 928 passed (0 added, 0 regressions — pure 5B multi-chunk refactor)

### Commits added (3 — including merge)

| SHA      | Subject |
|----------|---------|
| `8b5d7ff` | refactor(cli): promote fetcher/cli.py to top-level cli/ package (council A1-CLI-LAYER) |
| `150f57f` | refactor(docs): retarget docstring/comment references to cli/ package (council A1-CLI-LAYER followup) |
| `97ec39b` | Merge branch 'refactor/a1-cli-layer' |

### Module-LOC delta

| File | Before | After | Delta |
|------|--------|-------|-------|
| `fetcher/cli.py`               | 803  | —    | removed (moved) |
| `fetcher/watcher_install.py`   | 376  | —    | removed (moved) |
| `cli/__init__.py`              | —    | 26   | +26 (new) |
| `cli/main.py`                  | —    | 811  | +811 (moved from fetcher/cli.py + docstring expansion + cli.watcher re-import update) |
| `cli/watcher.py`               | —    | 384  | +384 (moved from fetcher/watcher_install.py + docstring expansion) |

## Decision Record — A1-CLI-LAYER (shipping)

### Chosen Approach: Option A (clean top-level `cli/` package)

The Round-1 council produced split dissent — Engineer (gpt-5.2) voted Option C (top-level `cli/` + `fetcher.cli` shim) on §5.12-risk + blast-radius grounds; Architect (gemini-3-pro-preview) voted Option A (pure move, no shim) on DAG-cleanliness + anti-overengineering grounds. Round 2 cross-critique resolved the split:

- The Architect demonstrated that a shim creates a permanent `fetcher → cli → fetcher.bulk_fetch` back-edge that defeats the architectural goal.
- The Engineer accepted Option A *with* explicit wheel-packaging verification as the WWCMM mitigation.

### Top Rejected: Option C (hybrid + shim)

The shim was "convenient" but would have left a `fetcher.cli` module pointing at top-level `cli.main`, creating exactly the back-edge this refactor exists to eliminate. Anti-overengineering: don't leave compatibility shims for a code path that has only 5 in-repo callers and zero external API contract.

### Top Rejected: Option B (`fetcher/cli/` sub-package)

Sub-packaging would have given a cleaner internal layout but kept the CLI under `fetcher/`, preserving the `fetcher → backend` back-edge in the package-level DAG. Engineer initially leaned this way for §5.12-stability reasons; Architect's "Option B just hides the cycle one directory level deeper" landed.

### Per-disagreement resolution

| Disagreement | Engineer | Architect | Resolution | Evidence |
|---|---|---|---|---|
| Shim creates real back-edge? | "Trivial to make `from pathlib import Path` part of shim" | "Yes — `fetcher.cli → cli.main → fetcher.bulk_fetch` is a real dependency edge resolved at import time" | Architect wins | `cli/main.py:91` (`from backend.config import get_settings`), `cli/main.py:102` (`from fetcher.bulk_fetch import ClaudeFetcher`) — both lazy imports inside command bodies; the shim would still execute the module body's top-level imports |
| Test-suite churn footprint? | "8 import sites" (overstated) | "5 test files, trivial sed pass" | Architect wins | Recon confirms 5 files; one of the "8 sites" was inside cli.py itself |
| Wheel-packaging footgun? | Real risk | "Mitigated by build verification step" | Both wins — agreed to do the verification step | `pyproject.toml:81` previously listed only `["backend", "fetcher", "mcp_server"]`; added `"cli"` |

### Residual Risks

1. **Frozen launchd plists on users' machines.** Already-installed `~/Library/LaunchAgents/com.claude-explorer.cc-watcher.plist` files were generated by the OLD `fetcher/cli.py:_build_watcher_inline_script` and bake `from backend.cc_watcher import run_watcher` into the supervised script body. The bake target didn't change (`backend.cc_watcher.run_watcher` still exists), so existing installs continue to work. New installs use the cli/watcher.py-generated template. No user-facing migration step needed.
2. **External code importing `fetcher.cli`.** This is a private internal CLI module; no public API contract. Zero external callers in this monorepo. If a downstream user has scripted around `from fetcher.cli import main`, they'll need to update to `from cli.main import main` — but this CLI was never documented as a Python API.

### CTO WWCMM

Would reverse if `_build_launchd_plist` no longer reflects the `Path.home` patch on `cli.watcher`. Repro: stash the implementation of `_build_launchd_plist` in `cli/watcher.py`, re-run `fetcher/tests/test_watcher_install_xml_safety.py::test_plist_with_xml_metacharacters_in_log_dir`, confirm RED with `AssertionError` on the home-directory path mismatch — proving the `cli.watcher.Path.home` patch IS the load-bearing site (and that the move didn't accidentally land it back in a non-patchable namespace). Verified — see commit `8b5d7ff` description.

## Findings table

| Class | File | Severity | Status | Commit |
|---|---|---|---|---|
| A1-CLI-LAYER | fetcher/cli.py → cli/main.py | HIGH | DONE | `8b5d7ff` |
| A1-CLI-LAYER | fetcher/watcher_install.py → cli/watcher.py | HIGH | DONE | `8b5d7ff` |
| A1-CLI-LAYER | pyproject.toml entry + wheel packages + sdist include | HIGH | DONE | `8b5d7ff` |
| A1-CLI-LAYER | 5 test files (8 import sites + 1 patch string) | HIGH | DONE | `8b5d7ff` |
| A1-CLI-LAYER | 5 production files docstring/comment retargets | LOW | DONE | `150f57f` |

## Wheel-packaging verification (Engineer's WWCMM mitigation)

Build artifact validation pre-merge:

```
$ uv build --wheel
Successfully built dist/claude_explorer-0.1.0-py3-none-any.whl

$ unzip -l dist/claude_explorer-0.1.0-py3-none-any.whl | grep -E "^.*cli/"
     1229  02-02-2020 00:00   cli/__init__.py
    29010  02-02-2020 00:00   cli/main.py
    16141  02-02-2020 00:00   cli/watcher.py

$ unzip -p dist/claude_explorer-0.1.0-py3-none-any.whl "*.dist-info/entry_points.txt"
[console_scripts]
claude-explorer = cli.main:main
```

All three `cli/` files present in the wheel; entry point correctly resolves. Footgun mitigated.

## §5.12 attribute-patch transient-break verification

The single string-typed monkeypatch site (`fetcher/tests/test_watcher_install_xml_safety.py:144`) was updated from `"fetcher.cli.Path.home"` to `"cli.watcher.Path.home"`. Transient-break verification:

```
$ sed -i 's|"cli.watcher.Path.home"|"fetcher.cli.Path.home"|g' fetcher/tests/test_watcher_install_xml_safety.py
$ uv run pytest -k xml_metacharacters_in_log_dir
FAILED test_plist_with_xml_metacharacters_in_log_dir
    ImportError: import error in fetcher.cli: No module named 'fetcher.cli'
```

Confirms the move is REAL (no shim/alias smuggled in) AND the new patch string is the load-bearing §5.12 target. The CLAUDE-TESTING §5.12 attribute-patch idiom enforcement worked as designed: had the implementation moved but the patch string stayed, the test would have raised `ImportError` rather than silently no-op'ing.

## Tests added

**None.** This is a pure 5B refactor: zero behavior change, no new contracts, no new code paths. The existing 928-test suite serves as the regression net. The §5.12 transient-break verification above is itself an empirical demonstration that the existing `test_plist_with_xml_metacharacters_in_log_dir` correctly fails when the patch site is wrong — the test guards itself.

## Council failure modes worth recording

1. **gpt-5.2 (not gpt-5.2-pro).** The user's invocation explicitly specified `gpt-5.2` (NOT `gpt-5.2-pro`) for this run. The agent's frozen spec defaults to `gpt-5.2-pro`. The override worked correctly — both PAL preflight and both Round-1 + Round-2 deliberation calls used `model="gpt-5.2"`. The model returned `"model_used": "gpt-5.2"` in every metadata field, confirming the routing.

2. **Split-dissent escalation worked as designed.** The agent contract calls for the Round-1 confirmation round to either confirm-and-ship or escalate. Round 1 produced a real split (A vs C). The CTO ran exactly one cross-critique round with each panelist seeing the other's specific arguments. Both revised toward Option A, and the WWCMM-mitigation pattern (wheel verification) was the bridge. **This is the council protocol working as advertised.**

3. **§5.12 enforcement preserved across the move.** The single monkeypatch string was a small artifact but a real silent-no-op landmine. Updating it (and verifying the transient-break by deliberately reverting it) is exactly the discipline CLAUDE-TESTING §5.12 documents. Cost: ~30 seconds; value: would have caught a silent test-degradation regression that's invisible at PR-review time.

4. **User AFK + explicit authority delegation worked cleanly.** The agent had the authority to override its own >3-file-move guardrail because the user named the specific finding being authorized. The agent did NOT silently widen the scope (no "while we're here" cleanups beyond the docstring-retarget commit, which is mechanically required for grep-clean code search).

## Follow-ups requiring user action

- **None.** All work is on `main` (commits `8b5d7ff`, `150f57f`, merge `97ec39b`). The feature branch has been deleted post-merge.
- **Action items for future agents:** if a future hunter finds production code that still references `fetcher.cli` or `fetcher.watcher_install` (e.g., in a future feature plan's PLANS/*.md file), update to `cli.main` / `cli.watcher`. Historical PLANS/ documents intentionally preserve old paths for archaeology — those should NOT be retargeted.

`model_used` (preflight + both Round-1 + both Round-2 PAL calls): `gpt-5.2` (Engineer) + `gemini-3-pro-preview` (Architect). Verified via `metadata.model_used` field on every PAL response in this run.
