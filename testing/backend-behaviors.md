# Testing: Backend behaviors

Part of [TESTING.md](../TESTING.md). Read this file when you test one of these areas:

- SSE streams
- data sizes
- concurrency
- security inputs
- async code
- pydantic models

## 5.6 · SSE streaming tests

The contract of an SSE endpoint is ALL about the event stream. This rule applies to these endpoints:

- `/api/fetch/refresh`
- `/api/fetch/start`
- any SSE endpoint that the project adds in a later change

A test that asserts `status_code == 200` proves no part of this contract.

**The full SSE contract has four parts:**

- the event order
- the event types
- the payload shape for each event
- the termination

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

- Assert the terminator event.
- Assert that the stream really closes and does not hang.
- To do this, put `asyncio.wait_for(..., timeout=5)` on the `async for` loop.

**Reconnection.** If the implementation supports SSE retry (`retry: N`), write a test for it. The test should assert two things:

- The server sends the retry directive.
- The client obeys the retry directive.

**Cancellation.** Disconnect in the middle of the stream. Then assert that the server-side generator cleans up:

- No threads leak.
- No file stays half-written.

For the cc-image watcher, assert that the polling loop cancels cleanly when the lifespan teardown occurs.

## 5.7 · Realistic data sizes

This section is the backend equivalent of the Playwright "long names" rule. Some bugs occur only at scale:

- **Search and scoring loops.** A fixture with 1 message does not test these areas:
  - the sort for each message
  - dedup
  - pagination boundaries

  Build a fixture with at least 50 messages. Put a known token in only one of them.
- **Filesystem walks.** `discover_jsonl_files` paginates and dedups across orgs.
  - With 1 file, you do not test the dedup.
  - With 50 files across 3 orgs, you do test it.
- **Memory limits.** Large attachments (images of several MB) do not fit in a 1×1 PNG fixture.
  - PDF export with 10+ images can cause WeasyPrint memory pressure.
  - Include at least one test of this type.
- **UUID and off-by-one bugs.** Sequential UUIDs hide collisions and off-by-one errors. In fixtures, use `uuid.uuid4()`, not `f"uuid-{i}"`.
- **Long content.**
  - Message text > 100kB exercises the code paths of the streaming tokenizer.
  - Title and name strings ≥ 30 chars test the truncation paths that the UI relies on.

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

Some endpoints use locks, atomic operations, or shared state. These endpoints need explicit race tests.

- The contract is "lock holds under contention".
- The only way to test that contract is to cause real contention.

**Lock under contention.** `/api/fetch/refresh` uses `asyncio.Lock` + `_refresh_in_progress` to run one request at a time. The test sends concurrent requests:

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

**Atomic write under crash.** If the implementation uses `tmp + os.replace`, inject a failure between the write and the replace. Then assert two things:

1. The original file is intact.
2. The temp file is cleaned up.

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

**Filesystem order in migrations.** The user can kill the process in the middle of a migration. Test the partial states that this causes.

- Example: a migration writes files A, B, and C in that order.
- Simulate a crash after each file.
- Assert that recovery occurs on the next mount.

**SQLite WAL contention.** If the project uses SQLite at some time, do these tests:

- Test concurrent readers + a writer.
- Assert that no `database is locked` errors leak to the client.

The project does not use SQLite at this time. But the cache.db hint suggests that SQLite can be relevant. If it is relevant, flag it.

## 5.9 · Security-adjacent inputs

Some routes take a path, a URL, a pattern, or other external input. Every such route needs explicit tests with malicious input.

- The test passes when the route *refuses* the input (4xx with no leakage).
- The test does not pass when the route serves something.

**Path traversal.** Send `/api/cc-image?path=../../../etc/passwd`.

- Assert 403 or 400.
- Do not accept 200 with /etc/passwd content.
- Do the same test for `/api/attachments/<conv>/<file>/<variant>`.
- The real pattern: the route must call `Path(...).resolve(strict=True).relative_to(allowed_root)`, and return 404 on `ValueError`.

**Symlink resolution.** Put a symlink in `tmp_path` that points outside the data dir. Assert that the route does not follow it.

**Permission bits.** After a write of `~/.claude-explorer/credentials.json` or `preferences.json`, assert `os.stat(p).st_mode & 0o777 == 0o600`.

- The atomic-write path sets the mode bits.
- If that path calls `os.replace()` with a `tmp` file that has `0o644`, the permission is wrong.
- The project has this test for credentials but not for preferences. Write the test for preferences.

**Regex DoS.** The user can supply regex patterns (`AtomFilter.mode == 'regex'`). A pathological pattern like `(a+)+$` with a long input can hang. Assert one of these two things:

- The matcher stops within a small time budget.
- The matcher validates the complexity of the pattern.

**Auth headers.** Some routes expect headers (X-Org-ID, Authorization, etc.).

- These routes should return 401 when a header is missing.
- These routes should return 403 when a header is malformed.
- Do not rely on the default behavior of FastAPI. Explicit tests prevent regressions.

**Header and form smuggling.** Write tests that send these inputs:

- an unexpected content-type
- oversized JSON
- duplicate headers

Each test should get a 4xx with a useful detail body, not a 500.

## 5.10 · Async / await pitfalls

Backend false-pass class #5: the code creates a coroutine but does not await it. The test passes with no error. The assertion runs against the coroutine object, not against its resolved value.

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

1. Use one of these two modes. Do not mix them.
   - `pyproject.toml` sets `asyncio_mode = "auto"`. Then `pytest-asyncio` runs all `async def` tests automatically.
   - Or, use `asyncio_mode = "strict"`, and decorate each test explicitly with `@pytest.mark.asyncio`.
2. CI runs with `-W error::RuntimeWarning`. Thus "coroutine was never awaited" is a test failure, not a silent warning.
3. Select the client for the type of test:
   - For simple HTTP tests, use the FastAPI `TestClient` (sync). It wraps `httpx.AsyncClient` internally, and you write a plain `def test_…`.
   - For SSE, streams, or explicit async behavior, use `httpx.AsyncClient` + `async def test_…`.
4. Do not call `asyncio.run()` inside a test. Always let `pytest-asyncio` manage the loop.

**Warning hygiene.** Do NOT put a blanket `ignore::DeprecationWarning` in `filterwarnings` in `pyproject.toml`.

- Real deprecation warnings from third-party libraries tell the project about necessary upgrades.
- Filter only the specific warnings that you consciously decided to accept.
- Add a comment that explains the reason for each filter.

## 5.11 · Pydantic / FastAPI specifics

**Strict input validation.** Input models should declare `model_config = ConfigDict(extra='forbid')`.

- With this setting, unknown fields produce 422. They are not silently accepted.
- Write a test that sends a payload with one extra field.
- Assert 422 with a useful detail.

**Edge cases for every input model.**

- An empty list, an empty dict, or an empty string for fields that are required and must not be empty.
- `null` for required fields → 422.
- Type coercion: `"1"` (string) where `int` is required.
  - Assert that the coercion occurs.
  - Also assert that the correct cases are rejected (e.g. `"abc"` → 422).
- The float / int boundary:
  - `1.0` for an `int` field.
  - `2**53 + 1` for large ints (JSON precision loss).
- Datetime: ISO-8601 with a timezone and without a timezone. Assert the timezone handling.

**Response model coercion runs only through HTTP.** A direct call to a route handler skips `response_model`. Always test through `httpx.AsyncClient`/`TestClient`. Do not import the handler and call it.

**Schema migration tests.**

- When you add a response field, write a test that consumes the OLD response shape and adapts. This test proves backwards compatibility.
- When you remove a field, write a test that asserts the new response does NOT contain it. This test proves that you really removed it. It also proves that you did not keep it by accident for one more release.

**`Depends()` overrides.** For unit tests, use `app.dependency_overrides[get_settings] = lambda: TestSettings()`.

- Do NOT monkeypatch `get_settings` globally.
- A global monkeypatch breaks the lru_cache discipline ([§5.1](backend-isolation.md)).

**Status codes are part of the contract.** The difference between 200, 201, 204, 404, 422, and other codes matters to clients. Tests should assert the *exact* code, not "≥ 200 and < 300".

**Test the error path.** For every route, assert at least one error case explicitly:

- missing data → 404
- bad input → 422
- conflict → 409
- internal failure → 500 with a sanitized detail (no traceback in the body of production responses)
