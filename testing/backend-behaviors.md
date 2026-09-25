# Testing: Backend behaviors

Part of [TESTING.md](../../TESTING.md). Read this file when: you test sse, data sizes, concurrency, security inputs, async code, or pydantic models.

## 5.6 · SSE streaming tests

`/api/fetch/refresh`, `/api/fetch/start`, and any future SSE endpoint
have a contract that's ENTIRELY about the event stream. A test that
asserts `status_code == 200` proves none of it.

**The full SSE contract: event order, event types, payload shape per
event, termination.**

```python
@pytest.mark.asyncio
async def test_refresh_emits_start_progress_complete(client_with_real_app):
    events: list[tuple[str, dict]] = []
    async with client_with_real_app.stream("GET", "/api/fetch/refresh?incremental=true") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        current_event: str | None = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                current_event = line.removeprefix("event:").strip()
            elif line.startswith("data:") and current_event:
                payload = json.loads(line.removeprefix("data:").strip())
                events.append((current_event, payload))
                if current_event in ("complete", "error"):
                    break

    # Order: start, then ≥1 progress, then complete (or error — assert which).
    kinds = [k for k, _ in events]
    assert kinds[0] == "start"
    assert "progress" in kinds
    assert kinds[-1] == "complete"            # NOT error in the happy path
    # Payload shape per event:
    start_payload = next(p for k, p in events if k == "start")
    assert "total" in start_payload
```

**Termination.** Every SSE stream must reach `complete` OR `error`.
Tests should assert the terminator and that the stream actually
closes (no hang). Use `asyncio.wait_for(..., timeout=5)` on the
`async for` loop.

**Reconnection.** If the impl supports SSE retry (`retry: N`), a test
should assert the retry directive is emitted and respected.

**Cancellation.** Disconnect mid-stream and assert the server-side
generator cleans up (no leaked threads, no half-written file). For
the cc-image watcher: assert the polling loop cancels cleanly when
the lifespan teardown fires.

## 5.7 · Realistic data sizes

Backend equivalent of the Playwright "long names" rule. Bugs that
only appear at scale:

- **Search / scoring loops** — fixtures with 1 message don't test
  per-message sort, dedup, or pagination boundaries. Build a fixture
  with at least 50 messages and a known token in only one of them.
- **Filesystem walks** — `discover_jsonl_files` paginates / dedups
  across orgs. With 1 file, you don't test the dedup. With 50 files
  spanning 3 orgs, you do.
- **Memory limits** — large attachments (multi-MB images) don't fit
  in a 1×1 PNG fixture. PDF export with 10+ images can hit
  WeasyPrint memory pressure; include at least one such test.
- **UUID / off-by-one bugs** — sequential UUIDs hide collisions and
  off-by-one errors. Use `uuid.uuid4()` in fixtures, not
  `f"uuid-{i}"`.
- **Long content** — message text > 100kB exercises the streaming-
  tokenizer code paths. Title/name strings ≥ 30 chars test the
  truncation paths the UI relies on.

**Fixture helper template.**

```python
def make_realistic_conversation(uuid: str, *, message_count: int = 50,
                                  needle_index: int | None = None) -> dict:
    """Build a fixture conversation with realistic structure.

    needle_index: if set, the message at this index contains the literal
    string 'NEEDLE_TOKEN' (for search/sort tests). Use a non-zero index
    so 'first match wins' bugs surface.
    """
    msgs = []
    for i in range(message_count):
        text = f"Message {i} body with some realistic content."
        if i == needle_index:
            text += " NEEDLE_TOKEN here."
        msgs.append({
            "uuid": str(uuid_lib.uuid4()),
            "sender": "human" if i % 2 == 0 else "assistant",
            "text": text,
            "content": [{"type": "text", "text": text}],
            "created_at": (BASE_TIME + timedelta(seconds=i)).isoformat(),
            "updated_at": (BASE_TIME + timedelta(seconds=i)).isoformat(),
            "files": [],
            "files_v2": [],
            "attachments": [],
        })
    return {
        "uuid": uuid,
        "name": "Realistic conversation with a long enough title to truncate",
        "model": "claude-opus-4-7",
        "created_at": BASE_TIME.isoformat(),
        "updated_at": (BASE_TIME + timedelta(seconds=message_count)).isoformat(),
        "chat_messages": msgs,
        "current_leaf_message_uuid": msgs[-1]["uuid"],
        ...
    }
```

## 5.8 · Concurrency and atomic-op tests

Endpoints that use locks, atomic ops, or shared state need explicit
race tests. The contract is "lock holds under contention" — and the
only way to exercise that is to actually contend.

**Lock under contention.** `/api/fetch/refresh` is serialized via
`asyncio.Lock` + `_refresh_in_progress`. The test fires concurrent
requests:

```python
@pytest.mark.asyncio
async def test_refresh_serialized(real_async_client):
    # Start two refreshes "simultaneously"; one must 409.
    r1, r2 = await asyncio.gather(
        real_async_client.get("/api/fetch/refresh"),
        real_async_client.get("/api/fetch/refresh"),
        return_exceptions=False,
    )
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 409]
```

**Atomic write under crash.** When the impl uses `tmp + os.replace`,
inject a failure between write and replace. Assert (a) the original
file is intact and (b) the temp file is cleaned up.

```python
def test_atomic_write_recovers_from_replace_failure(isolated_data_dir, monkeypatch):
    target = isolated_data_dir / "preferences.json"
    target.write_text(json.dumps({"version": 1, "data": {"theme": "light"}}))
    original_bytes = target.read_bytes()

    # Force os.replace to fail.
    def boom(*a, **k): raise OSError("simulated rename failure")
    monkeypatch.setattr("os.replace", boom)

    with pytest.raises(OSError):
        write_preferences({"version": 1, "data": {"theme": "dark"}})

    # Original survived.
    assert target.read_bytes() == original_bytes
    # No temp leaked.
    assert not list(isolated_data_dir.glob("preferences.json.tmp*"))
```

**Filesystem ordering in migrations.** What happens if the user kills
the process mid-migration? Test the partial states. If migration
writes files A, B, C in order, simulate a crash after each and assert
recovery on next mount.

**SQLite WAL contention.** If we ever use SQLite, test concurrent
readers + a writer; assert no `database is locked` errors leak to the
client. (Currently no SQLite — but the cache.db hint suggests it
might be relevant; flag if so.)

## 5.9 · Security-adjacent inputs

Every route that takes a path / URL / pattern / external input needs
explicit malicious-input tests. The test passes when the route
*refuses* the input (4xx with no leakage), not when it serves
something.

**Path traversal.** `/api/cc-image?path=../../../etc/passwd` — assert
403 or 400, not 200 with /etc/passwd content. Same for
`/api/attachments/<conv>/<file>/<variant>`. Real pattern: the route
must `Path(...).resolve(strict=True).relative_to(allowed_root)` and
404 on `ValueError`.

**Symlink resolution.** Place a symlink in `tmp_path` pointing
outside the data dir. Assert the route doesn't follow it.

**Permission bits.** After writing `~/.claude-explorer/credentials.json`
or `preferences.json`, assert `os.stat(p).st_mode & 0o777 == 0o600`.
The atomic-write path is what writes mode bits; if it
`os.replace()`s a `tmp` file with `0o644`, the permission slips. We
have this test for credentials but not preferences — write it.

**Regex DoS.** If the user can supply regex patterns
(`AtomFilter.mode == 'regex'`), a pathological pattern like
`(a+)+$` with a long input can hang. Assert the matcher terminates
within a small time budget OR validates pattern complexity.

**Auth headers.** Routes that expect headers (X-Org-ID,
Authorization, etc.) should 401 on missing headers, 403 on
malformed. Don't rely on FastAPI's default behavior; explicit tests
prevent regressions.

**Header / form smuggling.** Tests that supply unexpected
content-type, oversized JSON, or duplicate headers should produce
4xx with a useful detail body, not 500.

## 5.10 · Async / await pitfalls

Backend false-pass class #5: a coroutine is created but not awaited.
The test happily passes; the assertion runs against the coroutine
object instead of its resolved value.

**Concrete trap.**

```python
# WRONG — silent pass
def test_get_config(client):
    response = client.get("/api/config")  # if `client` is AsyncClient, returns a coroutine
    assert response.status_code == 200    # `response` is a coroutine; status_code attribute access throws AttributeError
                                           # ...but if you got the imports wrong AsyncClient might be a sync mock,
                                           # silently passing.
```

**Discipline.**

1. `pyproject.toml` sets `asyncio_mode = "auto"` so all `async def`
   tests run via `pytest-asyncio` automatically. Or use
   `asyncio_mode = "strict"` and decorate explicitly with
   `@pytest.mark.asyncio`. Don't mix.
2. CI runs with `-W error::RuntimeWarning` so "coroutine was never
   awaited" is a test failure, not a silent warning.
3. For the simple HTTP tests, use FastAPI's `TestClient` (sync) — it
   wraps `httpx.AsyncClient` internally and you write plain
   `def test_…`. For SSE / streaming / explicit async behavior, use
   `httpx.AsyncClient` + `async def test_…`.
4. Never `asyncio.run()` inside a test; always let `pytest-asyncio`
   manage the loop.

**Warning hygiene.** `filterwarnings` in `pyproject.toml` should NOT
contain a blanket `ignore::DeprecationWarning`. Real deprecations
from third-party libs are how we learn about upgrade requirements.
Filter only the specific warnings you've consciously decided to live
with, with a comment explaining why.

## 5.11 · Pydantic / FastAPI specifics

**Strict input validation.** Input models should declare
`model_config = ConfigDict(extra='forbid')` so unknown fields produce
422, not silent acceptance. Tests should send a payload with one
extra field and assert 422 with a useful detail.

**Edge cases for every input model.**

- empty list, empty dict, empty string for required-non-empty fields
- `null` for required fields → 422
- Type coercion: `"1"` (string) where `int` is required — assert the
  coercion happens AND the right cases reject (e.g. `"abc"` → 422).
- Float / int boundary: `1.0` for `int` field; `2**53 + 1` for large
  ints (JSON precision loss).
- Datetime: ISO-8601 with and without timezone; assert tz handling.

**Response model coercion only runs through HTTP.** Calling a route
handler directly skips `response_model`. Always test via
`httpx.AsyncClient`/`TestClient`, not by importing the handler.

**Schema migration tests.** When you add a response field, write a
test that consumes the OLD response shape and adapts (proves
backwards compat). When you remove a field, write a test that the
new response does NOT contain it (proves you actually removed it,
didn't accidentally keep it for one extra release).

**`Depends()` overrides.** Use `app.dependency_overrides[get_settings]
= lambda: TestSettings()` for unit testing. Do NOT monkeypatch
`get_settings` globally — that breaks lru_cache discipline (5.1).

**Status codes are part of the contract.** A 200/201/204/404/422 etc
distinction matters to clients. Tests should assert the *exact* code,
not "≥ 200 and < 300".

**Test the error path.** For every route, assert at least one error
case explicitly: missing data → 404; bad input → 422; conflict → 409;
internal failure → 500 with a sanitized detail (no traceback in body
for production responses).
