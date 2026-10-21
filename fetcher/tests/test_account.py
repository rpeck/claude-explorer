"""``fetcher.account.fetch_account``: the ``/api/account`` request itself.

The backend tests replace ``fetch_account`` as a whole, so these tests are
the only ones that run its body. They patch the ``curl_cffi`` ``get`` call
(the network boundary) and never reach claude.ai.
"""

from __future__ import annotations

import pytest
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

from fetcher import account


class _FakeResponse:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 400:
            raise CurlHTTPError(f"HTTP Error {self.status_code}: ", 0, self)

    def json(self) -> dict:
        return self._body


def _record(monkeypatch: pytest.MonkeyPatch, resp: _FakeResponse) -> list[dict]:
    calls: list[dict] = []

    def fake_get(url: str, **kwargs) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        return resp

    monkeypatch.setattr(account.curl_requests, "get", fake_get)
    return calls


def test_sends_all_three_cookies_with_a_timeout_and_returns_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"uuid": "acct-1", "email_address": "user@example.com"}
    calls = _record(monkeypatch, _FakeResponse(200, body))

    result = account.fetch_account("sk-test-fake-key", cf_bm="bm-1", cf_clearance="clr-1")

    assert result == body
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://claude.ai/api/account"
    assert call["cookies"] == {
        "sessionKey": "sk-test-fake-key",
        "__cf_bm": "bm-1",
        "cf_clearance": "clr-1",
    }
    assert call["timeout"] == account.REQUEST_TIMEOUT == 15.0
    assert call["impersonate"] == "chrome"


def test_leaves_out_the_cloudflare_cookies_it_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record(monkeypatch, _FakeResponse(200, {}))

    account.fetch_account("sk-test-fake-key")

    assert calls[0]["cookies"] == {"sessionKey": "sk-test-fake-key"}


def test_an_http_error_is_raised_with_its_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _record(monkeypatch, _FakeResponse(401))

    with pytest.raises(CurlHTTPError) as info:
        account.fetch_account("sk-test-fake-key")

    assert info.value.response.status_code == 401
