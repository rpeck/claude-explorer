"""F2 council finding: bulk_fetch.py 429 handler had unbounded recursion.

Original code at fetcher/bulk_fetch.py:603-606 looked like::

    elif status == 429:
        click.echo("  Rate limited. Waiting 60 seconds...", err=True)
        time.sleep(60)
        return self.fetch_conversation(uuid)  # Retry

Two problems:

1. **Unbounded recursion.** Sustained 429s would grow the call stack one
   frame per minute. Python's default recursion limit (~1000) means the
   process would crash with ``RecursionError`` after roughly 16.7 hours
   of continuous rate limiting — long enough that a user leaving the
   fetcher running overnight could hit it.
2. **Bypasses the test harness.** ``time.sleep`` (stdlib) is not the
   indirection the rest of the module patches; the test convention
   established by ``test_retry.py`` patches ``fetcher.bulk_fetch._retry_sleep``.
   That meant no existing test could catch a regression here.

Fix: bounded loop using ``_retry_sleep`` (so patches work), and raise
``FetchTransientError`` on exhaustion so ``run_all_orgs`` correctly
marks the org as a transient failure (the catch block at
``bulk_fetch.py:1007-1014`` already classifies ``FetchTransientError``
as ``error_kind="TRANSIENT"``). Returning ``None`` instead would
produce a FALSE-OK org status — silent data incompleteness.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_fetcher(tmp_path: Path):
    from fetcher.bulk_fetch import ClaudeFetcher

    return ClaudeFetcher(
        session_key="sk",
        orgs=[
            {
                "uuid": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d",
                "name": None,
                "capabilities": [],
                "seen_in_response": False,
            }
        ],
        primary_org_id="ae24ae66-4622-48e7-b4b3-1ab2c49f933d",
        output_dir=tmp_path,
    )


def _make_http_429_error():
    """Construct an HTTPError whose ``getattr(err, 'response', ...).status_code`` is 429.

    The 429 branch at ``bulk_fetch.py:594`` looks at
    ``getattr(getattr(e, 'response', None), 'status_code', None)`` —
    any exception that exposes that shape will hit the 429 path.
    """

    class _FakeHTTPError(Exception):
        """Shaped like curl_cffi/requests HTTPError for the recon path."""

    response = MagicMock()
    response.status_code = 429
    err = _FakeHTTPError("429 Too Many Requests")
    err.response = response
    return err


def _make_http_200_response(payload: dict):
    """Construct a fake response that raise_for_status passes through."""
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def test_429_recovers_within_bounded_attempts(tmp_path: Path) -> None:
    """Bidirectional (positive): 429, 429, 200 → payload returned, sleep called twice."""
    fetcher = _make_fetcher(tmp_path)

    err_429 = _make_http_429_error()
    ok_response = _make_http_200_response({"uuid": "conv-1", "name": "test"})

    call_log: list[str] = []

    def fake_get(url):
        if len(call_log) < 2:
            call_log.append("429")
            raise err_429
        call_log.append("200")
        return ok_response

    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    # Patch both sleep symbols so test runs fast pre-fix (raw time.sleep)
    # and post-fix (_retry_sleep). The assertion below pins which one
    # the implementation MUST use post-fix.
    with patch.object(fetcher, "_get", side_effect=fake_get), patch(
        "fetcher.bulk_fetch._retry_sleep", side_effect=fake_sleep
    ), patch("fetcher.bulk_fetch.time.sleep", return_value=None):
        result = fetcher.fetch_conversation("conv-1")

    assert result == {"uuid": "conv-1", "name": "test"}
    assert call_log == ["429", "429", "200"]
    # Two 60-second waits between three attempts.
    assert sleep_calls == [60.0, 60.0]


def test_429_exhaustion_raises_transient_error(tmp_path: Path) -> None:
    """Bidirectional (negative): sustained 429s do NOT recurse forever.

    The original bug was unbounded recursion. Post-fix: a bounded
    number of attempts, then ``FetchTransientError`` so the org-level
    catch block in ``run_all_orgs`` marks the org as TRANSIENT failure.

    The test patches *both* ``time.sleep`` (so the test isn't held by
    the legacy raw-sleep path) and ``_retry_sleep`` (the post-fix
    indirection). The original recursive code would either crash with
    ``RecursionError`` after ~1000 attempts or hang in real sleep —
    both are failures.
    """
    from fetcher.bulk_fetch import FetchTransientError

    fetcher = _make_fetcher(tmp_path)
    err_429 = _make_http_429_error()

    call_count = {"n": 0}

    def fake_get(url):
        call_count["n"] += 1
        raise err_429

    # Belt-and-suspenders: patch both sleep symbols so the test is
    # fast regardless of whether the implementation uses raw time.sleep
    # (pre-fix) or _retry_sleep (post-fix).
    with patch.object(fetcher, "_get", side_effect=fake_get), patch(
        "fetcher.bulk_fetch._retry_sleep", return_value=None
    ), patch("fetcher.bulk_fetch.time.sleep", return_value=None):
        with pytest.raises(FetchTransientError):
            fetcher.fetch_conversation("conv-2")

    # Bounded: at most a small number of attempts. The exact cap is
    # implementation detail, but it must be finite (<= 10).
    assert 1 < call_count["n"] <= 10, (
        f"Expected bounded retry count, got {call_count['n']} attempts"
    )


def test_429_uses_retry_sleep_indirection(tmp_path: Path) -> None:
    """Boundary: ``_retry_sleep`` MUST be the sleep symbol used for 429 backoff.

    Tests that monkeypatch the stdlib ``time.sleep`` instead of the
    module-level ``_retry_sleep`` symbol would silently no-op (per
    TESTING.md §5.12). Pinning the indirection here prevents
    a future refactor from regressing to raw ``time.sleep``.
    """
    fetcher = _make_fetcher(tmp_path)
    err_429 = _make_http_429_error()
    ok_response = _make_http_200_response({"uuid": "conv-3"})

    state = {"calls": 0}

    def fake_get(url):
        state["calls"] += 1
        if state["calls"] == 1:
            raise err_429
        return ok_response

    retry_sleep_calls: list[float] = []

    def fake_retry_sleep(seconds: float) -> None:
        retry_sleep_calls.append(seconds)

    raw_sleep_calls: list[float] = []

    def fake_raw_sleep(seconds: float) -> None:
        raw_sleep_calls.append(seconds)

    with patch.object(fetcher, "_get", side_effect=fake_get), patch(
        "fetcher.bulk_fetch._retry_sleep", side_effect=fake_retry_sleep
    ), patch("fetcher.bulk_fetch.time.sleep", side_effect=fake_raw_sleep):
        result = fetcher.fetch_conversation("conv-3")

    assert result == {"uuid": "conv-3"}
    # Post-fix invariant: 429 backoff goes through _retry_sleep, NOT
    # raw time.sleep. This pins the patch convention from §5.12.
    assert retry_sleep_calls == [60.0], (
        f"429 backoff must call _retry_sleep, got {retry_sleep_calls}"
    )
    assert 60.0 not in raw_sleep_calls, (
        f"429 backoff must NOT use raw time.sleep (TESTING.md §5.12); "
        f"raw sleeps observed: {raw_sleep_calls}"
    )
