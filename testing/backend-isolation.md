# Testing: Backend isolation and mocking

Part of [TESTING.md](../../TESTING.md). Read this file when: you write pytest fixtures, mocks, or monkeypatches.

## 5 · Backend test discipline (pytest, FastAPI, async)

The Playwright lessons from sections 1–4 transpose cleanly to pytest:
write tests against the contract, falsify them, build realistic
fixtures, beware of clip-ancestor-style false-positives. The shape of
the false-positives is different on the backend, but the discipline is
the same. The 11 sub-sections below are concrete failure modes we've
shipped or nearly shipped.

## 5.1 · Test isolation: lru_cache, env vars, module singletons, time

Backend false-pass class #1: a test passes because it's actually
running against state from a *previous* test.

**`get_settings()` is `@lru_cache`d.** If your test does
`monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))` but
doesn't clear the cache, every subsequent `get_settings()` call
returns the FIRST test's settings. `tmp_path` from this test is never
read. Fixture template:

```python
@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    from backend import config
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()  # don't leak this test's settings into the next
```

**Module-level singletons need explicit reset.** Examples in this
codebase: `_refresh_in_progress` flag in `backend/routers/fetch.py`,
the `_seen` set in `backend/cc_watcher.py`, the in-memory cache
in `backend/cache.py`. Each has a test-only `reset_for_tests()`
helper or equivalent — call it from a fixture.

**Time-dependent tests need `freezegun` or `monkeypatch`.** `migrate_to_v2`'s
sentinel uses `datetime.now()`; tests that race the sentinel can flake
on slow CI. `monkeypatch.setattr("backend.foo.datetime", FakeDatetime)`
or use `freezegun.freeze_time(...)`.

**`tmp_path` is per-test by default** but `tmp_path_factory` is
session-scoped and shared. Don't write user data to `tmp_path_factory`
unless you reset it.

**Constants imported by value need patching at every call site.**
`fetcher/credentials.py` defines `DEFAULT_CREDENTIALS_PATH = ...`, and
*three* other modules import the constant by value at module-load time:
`fetcher/bulk_fetch.py`, `backend/routers/fetch.py`, and re-imports in
tests. `monkeypatch.setattr("fetcher.credentials.DEFAULT_CREDENTIALS_PATH",
new)` ONLY rebinds the canonical name — the three by-value copies still
point at `~/.claude-explorer/credentials.json`. The fixture must patch
all four:

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

Same pattern applies to any module that does
`from foo import CONSTANT` rather than `from foo import bar; bar.CONSTANT`.
Grep for the constant name globally; if it appears as a bare-name import
anywhere, patch each binding.

**`CLAUDE_DIR` and `CLAUDE_EXPLORER_DATA_DIR` are different knobs.**
`CLAUDE_DIR` controls where `~/.claude-explorer/` itself resolves
(used by capture, credentials, and the orgs router);
`CLAUDE_EXPLORER_DATA_DIR` controls where `conversations/` lives.
A test that only pins `CLAUDE_EXPLORER_DATA_DIR` can still scribble
into the user's real `~/.claude-explorer/credentials.json` if the
code under test goes through the credentials path. Pin both unless
you've verified the call graph never touches credentials.

**`isolated_data_dir` must be a SUBDIRECTORY of `tmp_path`, not
`tmp_path` itself.** `_resolve_path` uses
`data_dir.parent / "preferences.json"`, so `preferences.json` lives one
level up from the data dir. If the fixture uses `tmp_path` directly,
`preferences.json` lands in the pytest tmp root and bleeds across tests
on the same worker. The reference fixture uses `<tmp_path>/data` — `data/`
is the data dir, `<tmp_path>/preferences.json` is the prefs file.

**`real_async_client` is orthogonal to data isolation.** The `httpx.AsyncClient`
+ `ASGITransport(app=...)` fixture used for SSE/concurrency tests does NOT
imply isolated disk. Compose explicitly: a test that streams over real ASGI
AND touches preferences/credentials must use `real_async_client` PLUS
`isolated_data_dir` PLUS (if creds are involved) `_isolated_credentials_path`.
Don't fold them; an SSE test for a read-only endpoint shouldn't pay the
disk-isolation cost it doesn't need.

**Do not compute a home-relative path at import time.** A module
constant such as `Path.home() / "Library" / ...` freezes the developer's
real home before any test runs. `patch_home` (in
`backend/tests/_platform_home.py`) changes `HOME` later, so it cannot
redirect that constant.

- **What happened:** on 2026-09-23 a corrupt-config test ran
  `install-watcher --uninstall` under a patched home. The plist path
  was a module constant, so every local test run deleted the
  maintainer's real launchd watcher.
- **The rule:** make the path a function that reads `Path.home()` when
  it is called. `cli/watcher.py` now does this.
- **The guard:** `test_watcher_paths_follow_home.py`. It asserts that
  each path follows the patched home, and that no subprocess argument
  leaves it.

**Lifecycle tests must be order-independent.** Don't rely on file
collection order (`test_zz_step1_set_flag`, `test_zz_step2_observe_flag`);
pytest-randomly and pytest-xdist will reorder or split them across workers
and the second test will see uninitialized state. Pattern: extract the
fixture body into a plain helper (`def _reset_refresh_flag_body(...): ...`)
and have BOTH the fixture and any lifecycle test call the helper directly.
The test asserts on observable state after each helper invocation in the
same function body.

## 5.2 · Mock at the boundary, not the nesting

Backend false-pass class #2: the test mocks so much of the
implementation that the real bug never runs.

**Rule.** Mock at the HTTP boundary (outbound calls to claude.ai), or
at the filesystem boundary in the rare case where `tmp_path` won't
work. Let everything else run for real.

**Don't mock:** Pydantic models, serializers, migration code, the
prefs reader/writer, the store layer, the route handlers, the SSE
generators. They're cheap and they're where the bugs live.

**Counter-example.** The `/api/preferences` PATCH deep-merge contract
(`{savedFilters: null, activeFilterIds: null}` must explicitly null
legacy keys for the per-key overwrite to clear them). A test that
mocks `_write_atomic` and asserts "yes, _write_atomic was called with
the right body" passes — but the real bug is what lands on disk after
the round trip through `_read_blob() → merge → _write_atomic →
_read_blob()`. Only a real-`tmp_path` test catches it.

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

Backend false-pass class #3: the assertion checks structure but not
semantic value. The field could be hardcoded to 0, an empty array,
`None`, or last-write-wins junk and the test still passes.

**Examples.**

- `assert "conversation_count" in data` — passed for weeks while
  `/api/config` returned a hardcoded `0`. The right test asserts
  against a value computed from a known fixture: with 3 conversation
  files in `tmp_path`, `/api/config/stats` returns `3`.
- `assert response.json()["bookmarks"]` — Python truthy. `[]` is
  falsy, `[None]` is truthy. Assert `assert response.json()["bookmarks"]
  == [{...expected...}]`.
- `assert response.status_code == 200` — most route bugs corrupt the
  body, not the status. Always also assert the body shape and key
  values.

**For PDF / image / binary outputs:** assert against a known fixture
byte signature, NOT just "≥1 image stream". WeasyPrint emits valid
streams for broken-image icons; "stream count" can't tell broken from
fixed. The P5 test (`backend/tests/test_export_pdf_images.py`) decodes
the FlateDecode XObject and matches a deterministic 6-byte RGB
sequence in the fixture image. Bytes-in, bytes-out.

## 5.4 · Negative-space assertions

Don't only assert what should change. Also assert what should NOT
change. This catches the entire class of "endpoint clobbers
unrelated state" bugs.

**Concrete patterns.**

- After a PATCH: GET back the resource and assert untouched fields.
- After a migration: assert the keys you didn't migrate are still
  there, and the values are byte-identical (`.read_bytes() ==
  expected_bytes` if it's a file).
- After copying to a cache: assert the source file is unchanged
  (mtime + bytes).
- After a delete: assert siblings/parents are unchanged.

**Fenced-block strip incident (2026-05-05 P1.3, council caught).** The
TOOL_PLACEHOLDER regex stripped placeholder text *inside* fenced code
blocks, killing the friendly badge. A "strip works" test passes
trivially. The real test is two-pronged: stripped *outside* fences;
*preserved* inside fences. Negative-space assertion as a first-class
test, not an afterthought.

```python
def test_tool_placeholder_strip_outside_fence_only():
    md = "before\n\nTOOL_PLACEHOLDER_TEXT here\n\n```\nTOOL_PLACEHOLDER_TEXT inside\n```\nafter"
    out = filter_tool_placeholders(md)
    assert "TOOL_PLACEHOLDER_TEXT here" not in out                  # stripped outside
    assert "TOOL_PLACEHOLDER_TEXT inside" in out                    # PRESERVED inside fence
```

## 5.5 · Migration tests MUST seed the legacy shape

Backend false-pass class #4 (and the most common): tests seed the new
schema, the migration code never runs, and the test happily verifies
the new schema is still the new schema.

**Rule.** Migration tests seed the on-disk shape USERS WILL HAVE
(legacy), then run the migration, then assert the post-migration
shape AND the full contract of what the migration was supposed to do
(tombstone keys, sentinel flags, side effects).

**v1 → v2 filter migration template.**

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

**Idempotency.** Run the migration twice. Assert the second run is a
no-op (no PATCH, no on-disk diff). The 2026-05-05 P3a fix uses a
sentinel for exactly this; if the sentinel can be bypassed, the
migration runs every page load and silently rewrites user state.

**Tombstone keys.** When a migration is supposed to clear legacy keys
(via the per-key-overwrite PATCH path), assert they're EXPLICITLY
nulled in the request body OR absent from the post-migration GET.
Omitting them from the PATCH leaves them on disk — that's exactly the
bug Gemini's council review caught in CFR1.

## 5.12 · Monkeypatching: prefer attribute-patch over value-binding

The way a test rebinds a symbol determines whether the module under
test can be safely refactored. Two distinct idioms:

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

**Why this matters.** When the module under test imports a helper from
elsewhere (`from .helpers import save_credentials`), the helper is bound
to the local module's namespace AT IMPORT TIME. A test that
attribute-patches the local namespace (`fetch_router.save_credentials =
...`) reaches through to the late-bound runtime call. A test that
value-binds a snapshot of the function won't see updates.

**The refactor-safety consequence.** If you extract `save_credentials`
out of `fetch.py` into a new `fetch_pipeline.py` module:

- Tests that use **attribute-patch on `fetch_router`** keep working iff
  `fetch_router` still has `save_credentials` as a top-level attribute
  (i.e., it's re-imported at the top of `fetch.py`). Mass refactors
  that move helpers out without re-importing them break these tests
  silently — the patch lands on a module that no longer routes the
  call through.

- Tests that use **value-binding** (`from backend.routers.fetch import
  save_credentials; ... = fake`) only patch the test's local symbol —
  the route's call goes through to the real `save_credentials`. These
  tests are vacuously green and DON'T catch the bug they should.

**Incident**: the 2026-05-21 A2 refactor of `routers/fetch.py`
surfaced this. The Engineer council persona (gpt-5.2-pro) caught that
23+ tests used the attribute-patch idiom against `fetch_router`, which
forced the council to ship a CONSERVATIVE split (preserve top-level
attributes on `fetch_router`) instead of the aggressive split the
Architect originally proposed. Detailed in
`PLANS/CODE-REVIEW-BACKEND.md`.

**Rule**: prefer attribute-patch via `monkeypatch.setattr(module,
"name", fake)`. Avoid value-binding via `from module import name` in
test files — it makes future refactors strictly harder. When
refactoring a module that has heavy test coverage, run this grep
FIRST to surface the landmine count:

```bash
grep -rnE 'monkeypatch\.setattr\(|patch\.object\(|patch\(["\'][^"\']*<module>' \
  backend/tests/ fetcher/tests/ | grep -E '<module>' | wc -l
```

If the count is > 0, the refactor must either (a) preserve top-level
attributes on the original module via re-export, or (b) migrate the
test sites in lockstep.
