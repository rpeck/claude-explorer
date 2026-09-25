# Testing: Backend isolation and mocking

Part of [TESTING.md](../TESTING.md). Read this file when you write pytest fixtures, mocks, or monkeypatches.

## 5 · Backend test discipline (pytest, FastAPI, async)

The Playwright lessons in [§1, §2, §4](principles.md) and [§3](playwright.md) also apply to pytest:

- Write tests against the contract.
- Falsify the tests.
- Build realistic fixtures.
- Be careful of false positives of the clip-ancestor type.

On the backend, the false positives have a different shape. The discipline is the same.

The sub-sections of §5 are concrete failure modes that we shipped or almost shipped. They are in four files:

- §5.1 to §5.5 and §5.12: this file.
- §5.6 to §5.11: [backend-behaviors.md](backend-behaviors.md).
- §5.13, §5.14 and §5.16: [backend-contracts.md](backend-contracts.md).
- §5.15 and §5.17: [playwright.md](playwright.md).

## 5.1 · Test isolation: lru_cache, env vars, module singletons, time

Backend false-pass class #1: a test passes because it actually runs against state from a *previous* test.

**`get_settings()` uses `@lru_cache`.**

- Your test can do `monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))` and not clear the cache.
- Then each later `get_settings()` call returns the settings of the FIRST test.
- The code never reads the `tmp_path` of this test.

Use this fixture template:

```python
@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    from backend import config
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()  # don't leak this test's settings into the next
```

**Reset module-level singletons explicitly.** These are examples in this codebase:

- the `_refresh_in_progress` flag in `backend/routers/fetch.py`
- the `_seen` set in `backend/cc_watcher.py`
- the in-memory cache in `backend/cache.py`

Each singleton has a test-only `reset_for_tests()` helper or an equivalent. Call it from a fixture.

**Use `freezegun` or `monkeypatch` in time-dependent tests.**

- The sentinel of `migrate_to_v2` uses `datetime.now()`.
- Tests that race the sentinel can fail at random on slow CI.
- Use `monkeypatch.setattr("backend.foo.datetime", FakeDatetime)`, or use `freezegun.freeze_time(...)`.

**`tmp_path` is per-test by default.**

- `tmp_path_factory` is session-scoped and shared.
- Unless you reset `tmp_path_factory`, do not write user data to it.

**Patch a constant that other modules import by value at every call site.** `fetcher/credentials.py` defines `DEFAULT_CREDENTIALS_PATH = ...`. *Three* other modules import the constant by value at module-load time:

- `fetcher/bulk_fetch.py`
- `backend/routers/fetch.py`
- re-imports in tests

`monkeypatch.setattr("fetcher.credentials.DEFAULT_CREDENTIALS_PATH",
new)` rebinds ONLY the canonical name. The three by-value copies still point at `~/.claude-explorer/credentials.json`. The fixture must patch all four:

```python
@pytest.fixture
def _isolated_credentials_path(tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    for target in (
        "fetcher.credentials.DEFAULT_CREDENTIALS_PATH",
        "fetcher.bulk_fetch.DEFAULT_CREDENTIALS_PATH",
        "backend.routers.fetch.DEFAULT_CREDENTIALS_PATH",
    ):
        monkeypatch.setattr(target, creds)
    yield creds
```

The same pattern applies to each module that does `from foo import CONSTANT` and not `from foo import bar; bar.CONSTANT`.

1. Grep for the constant name in the full codebase.
2. If the name appears as a bare-name import anywhere, patch each binding.

**`CLAUDE_DIR` and `CLAUDE_EXPLORER_DATA_DIR` are different settings.**

- `CLAUDE_DIR` controls where `~/.claude-explorer/` itself resolves. Capture, credentials, and the orgs router use it.
- `CLAUDE_EXPLORER_DATA_DIR` controls where `conversations/` lives.
- A test can pin only `CLAUDE_EXPLORER_DATA_DIR`. That test can still write into the real `~/.claude-explorer/credentials.json` of the user. This occurs if the code under test goes through the credentials path.
- Unless you verified that the call graph never touches credentials, pin both.

**Make `isolated_data_dir` a SUBDIRECTORY of `tmp_path`. Do not use `tmp_path` itself.**

- `_resolve_path` uses `data_dir.parent / "preferences.json"`. Thus `preferences.json` is one level above the data dir.
- If the fixture uses `tmp_path` directly, `preferences.json` goes into the pytest tmp root. The file then leaks across tests on the same worker.
- The reference fixture uses `<tmp_path>/data`:
  - `data/` is the data dir.
  - `<tmp_path>/preferences.json` is the prefs file.

**`real_async_client` is orthogonal to data isolation.**

- SSE and concurrency tests use a fixture with `httpx.AsyncClient` + `ASGITransport(app=...)`. That fixture does NOT give isolated disk.
- Combine the fixtures explicitly. A test that streams over real ASGI AND touches preferences or credentials uses `real_async_client` PLUS `isolated_data_dir`.
- If the test also involves credentials, add `_isolated_credentials_path`.
- Do not fold the fixtures into one. An SSE test for a read-only endpoint does not need the cost of disk isolation.

**Do not compute a home-relative path at import time.** A module constant such as `Path.home() / "Library" / ...` stores the real home of the developer before any test runs. `patch_home` (in `backend/tests/_platform_home.py`) changes `HOME` later. Thus it cannot redirect that constant.

- **What happened:**
  - On 2026-09-23, a corrupt-config test ran `install-watcher --uninstall` under a patched home.
  - The plist path was a module constant.
  - Thus each local test run deleted the real launchd watcher of the maintainer.
- **The rule:** make the path a function that reads `Path.home()` at call time. `cli/watcher.py` now does this.
- **The guard:** `test_watcher_paths_follow_home.py`. It asserts two conditions:
  - Each path follows the patched home.
  - No subprocess argument leaves the patched home.

**Make lifecycle tests order-independent.**

- Do not rely on the order in which pytest collects files (`test_zz_step1_set_flag`, `test_zz_step2_observe_flag`).
- pytest-randomly and pytest-xdist reorder these tests or split them across workers. Then the second test sees uninitialized state.

Use this pattern:

1. Extract the fixture body into a plain helper (`def _reset_refresh_flag_body(...): ...`).
2. Make BOTH the fixture and each lifecycle test call the helper directly.
3. In the same function body, make the test assert on observable state after each call to the helper.

## 5.2 · Mock at the boundary, not the nesting

Backend false-pass class #2: the test mocks so much of the implementation that the real bug never runs.

**Rule.**

- Mock at the HTTP boundary (outbound calls to claude.ai).
- In the rare case where `tmp_path` does not work, mock at the filesystem boundary.
- Let all other code run for real.

**Do not mock these parts:**

- Pydantic models
- serializers
- migration code
- the prefs reader/writer
- the store layer
- the route handlers
- the SSE generators

These parts are cheap to run, and the bugs are in them.

**Counter-example.** Look at the deep-merge contract of the `/api/preferences` PATCH.

- The contract: `{savedFilters: null, activeFilterIds: null}` must explicitly null the legacy keys, so that the per-key overwrite clears them.
- A test can mock `_write_atomic` and assert "yes, _write_atomic was called with the right body". That test passes.
- But the real bug is in the data on disk after the round trip through `_read_blob() → merge → _write_atomic →
_read_blob()`.
- Only a test with a real `tmp_path` catches the bug.

```python
# WRONG: mocks too much
def test_patch_merges(monkeypatch):
    seen = {}
    monkeypatch.setattr("backend.routers.preferences._write_atomic",
                        lambda p, d: seen.update(json.loads(d)))
    client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    assert seen["data"]["theme"] == "dark"  # passes; doesn't test merge

# RIGHT: round-trip through real disk
def test_patch_merges(isolated_data_dir, client):
    # seed
    client.put("/api/preferences", json={"data": {"theme": "light", "lang": "en"}})
    # patch
    client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    # round-trip read
    final = client.get("/api/preferences").json()["data"]
    assert final["theme"] == "dark"
    assert final["lang"] == "en"  # NEGATIVE-SPACE: must not be wiped
```

## 5.3 · Strong assertions, not "field exists"

Backend false-pass class #3: the assertion checks the structure but not the semantic value. The field can hold any of these values, and the test still passes:

- a hardcoded 0
- an empty array
- `None`
- last-write-wins junk

**Examples.**

- `assert "conversation_count" in data`:
  - This assertion passed for weeks while `/api/config` returned a hardcoded `0`.
  - The correct test asserts against a value computed from a known fixture.
  - For example: with 3 conversation files in `tmp_path`, `/api/config/stats` returns `3`.
- `assert response.json()["bookmarks"]`:
  - This checks Python truthiness. `[]` is falsy, and `[None]` is truthy.
  - Assert the full value: `assert response.json()["bookmarks"]
  == [{...expected...}]`.
- `assert response.status_code == 200`:
  - Most route bugs corrupt the body, not the status.
  - Always also assert the body shape and the key values.

**For PDF, image, or binary outputs:** assert against a known fixture byte signature. Do NOT assert only "≥1 image stream".

- WeasyPrint emits valid streams for broken-image icons. Thus a "stream count" cannot tell broken from fixed.
- The P5 test (`backend/tests/test_export_pdf_images.py`) decodes the FlateDecode XObject.
- The test then matches a deterministic 6-byte RGB sequence in the fixture image.
- Bytes in, bytes out.

## 5.4 · Negative-space assertions

Do not assert only what must change. Also assert what must NOT change. This catches the full class of "endpoint clobbers unrelated state" bugs.

**Concrete patterns.**

- After a PATCH: GET the resource back, and assert the untouched fields.
- After a migration: assert that the keys you did not migrate are still there. Also assert that their values are byte-identical (`.read_bytes() ==
  expected_bytes` if it is a file).
- After a copy to a cache: assert that the source file did not change (mtime + bytes).
- After a delete: assert that siblings and parents did not change.

**Fenced-block strip incident (2026-05-05 P1.3, the council caught it).**

- The TOOL_PLACEHOLDER regex stripped placeholder text *inside* fenced code blocks. This removed the friendly badge.
- A "strip works" test passes trivially.
- The real test has two parts:
  - The text is stripped *outside* fences.
  - The text is *preserved* inside fences.
- Make the negative-space assertion a first-class test, not an afterthought.

```python
def test_tool_placeholder_strip_outside_fence_only():
    md = "before\n\nTOOL_PLACEHOLDER_TEXT here\n\n```\nTOOL_PLACEHOLDER_TEXT inside\n```\nafter"
    out = filter_tool_placeholders(md)
    assert "TOOL_PLACEHOLDER_TEXT here" not in out                  # stripped outside
    assert "TOOL_PLACEHOLDER_TEXT inside" in out                    # PRESERVED inside fence
```

## 5.5 · Migration tests MUST seed the legacy shape

Backend false-pass class #4 (and the most common):

- The tests seed the new schema.
- Thus the migration code never runs.
- The test only verifies that the new schema is still the new schema.

**Rule.** A migration test does these steps:

1. Seed the on-disk shape that USERS ACTUALLY HAVE (legacy).
2. Run the migration.
3. Assert the post-migration shape.
4. Assert the full contract of the migration:
   - tombstone keys
   - sentinel flags
   - side effects

**Template for the v1 → v2 filter migration.**

```python
def test_v1_to_v2_atom_polarity_promotes_to_behavior(isolated_data_dir, client):
    prefs = isolated_data_dir / "preferences.json"
    prefs.write_text(json.dumps({
        "version": 1,
        "data": {
            "filters": {
                "nodes": {
                    "atom-x": {
                        "id": "atom-x", "type": "atom", "name": "X",
                        "enabled": True,
                        "polarity": "exclude",   # legacy v1
                        # NO 'behavior' key
                        "patterns": ["*X*"], "mode": "glob", "target": "title",
                    },
                },
                "activeId": "atom-x",
                "_migratedV1": True,
                # NO _migratedV2
            },
        },
    }))
    # Trigger the migration via the normal path (a GET that the app uses
    # on first mount). Don't reach into private migration functions —
    # tests should exercise the public surface.
    client.get("/api/preferences")
    final = json.loads(prefs.read_text())["data"]["filters"]
    atom = final["nodes"]["atom-x"]
    assert atom["behavior"] == "hide"        # promoted
    assert "polarity" not in atom             # legacy stripped
    assert final["_migratedV2"] is True       # sentinel set
    assert final["activeId"] == "atom-x"      # active preserved
```

**Idempotency.** Run the migration two times. Assert that the second run is a no-op (no PATCH, no on-disk diff).

- The 2026-05-05 P3a fix uses a sentinel for exactly this purpose.
- If code can bypass the sentinel, the migration runs on each page load. It then silently rewrites user state.

**Tombstone keys.** Some migrations must clear legacy keys through the per-key-overwrite PATCH path. For these migrations, assert one of these conditions:

- The request body EXPLICITLY nulls the keys.
- The keys are absent from the post-migration GET.

If the PATCH omits the keys, the keys stay on disk. The council review by Gemini caught exactly this bug in CFR1.

## 5.12 · Monkeypatching: prefer attribute-patch over value-binding

The way a test rebinds a symbol determines whether you can safely refactor the module under test. There are two different idioms:

```python
# ✓ ATTRIBUTE PATCH (refactor-safe)
from backend.routers import fetch as fetch_router
monkeypatch.setattr(fetch_router, "save_credentials", fake_save)

# ✗ VALUE BINDING (refactor-fragile)
from backend.routers.fetch import save_credentials
monkeypatch.setattr("backend.routers.fetch.save_credentials", fake_save)
# Or worse, capturing the value at import time:
saved_real = save_credentials
monkeypatch.setattr(saved_real, "__call__", fake_save)  # doesn't do what you think
```

**Why this matters.**

- The module under test can import a helper from another module (`from .helpers import save_credentials`).
- Python then binds the helper to the namespace of the local module AT IMPORT TIME.
- A test that attribute-patches the local namespace (`fetch_router.save_credentials =
...`) reaches the late-bound runtime call.
- A test that value-binds a snapshot of the function does not see updates.

**The consequence for refactor safety.** Assume that you extract `save_credentials` out of `fetch.py` into a new `fetch_pipeline.py` module.

- Tests that use **attribute-patch on `fetch_router`**:
  - These tests continue to work iff `fetch_router` still has `save_credentials` as a top-level attribute. That is, `fetch.py` re-imports it at the top.
  - Mass refactors that move helpers out without a re-import break these tests silently. The patch goes to a module that no longer sends the call through.
- Tests that use **value-binding** (`from backend.routers.fetch import
  save_credentials; ... = fake`):
  - These tests patch only the local symbol of the test. The call of the route goes to the real `save_credentials`.
  - These tests are vacuously green. They do NOT catch the bug that they exist to catch.

**Incident:** the 2026-05-21 A2 refactor of `routers/fetch.py` showed this problem.

- The Engineer council persona (gpt-5.2-pro) found that 23+ tests used the attribute-patch idiom against `fetch_router`.
- This forced the council to ship a CONSERVATIVE split. That split preserves the top-level attributes on `fetch_router`.
- The Architect originally proposed an aggressive split. The council shipped the conservative split instead.
- `PLANS/CODE-REVIEW-BACKEND.md` has the details.

**Rule:** prefer attribute-patch through `monkeypatch.setattr(module,
"name", fake)`.

- Avoid value-binding through `from module import name` in test files. It makes future refactors strictly harder.
- Before you refactor a module that has heavy test coverage, run this grep FIRST. It shows the count of risky patch sites:

```bash
grep -rnE 'monkeypatch\.setattr\(|patch\.object\(|patch\(["\'][^"\']*<module>' \
  backend/tests/ fetcher/tests/ | grep -E '<module>' | wc -l
```

If the count is > 0, the refactor must do one of these:

- (a) Preserve top-level attributes on the original module through re-export.
- (b) Migrate the test sites in lockstep with the refactor.
