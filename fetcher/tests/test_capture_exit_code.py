"""A failed credential capture must exit non-zero.

A scheduled job or wrapper script has to be able to tell a failed capture
from a successful one. Verified 2026-09-21 after a piped invocation made a
real failure look like success; the pipeline reported `tail`'s status, not
the CLI's. The CLI contract is pinned here so a future refactor cannot
quietly turn the failure into exit 0.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from cli.main import main as cli


def test_failed_capture_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """capture_credentials returning None means timeout or closed browser."""

    async def fake_capture(timeout: int = 300, **kwargs):
        return None

    monkeypatch.setattr(
        "fetcher.playwright_capture.capture_credentials", fake_capture, raising=False
    )
    result = CliRunner().invoke(cli, ["capture", "--timeout", "1"])

    assert result.exit_code != 0, result.output
    assert "Failed to capture credentials" in result.output


def test_successful_capture_exits_zero(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Bidirectional: a real capture must still report success."""
    creds = {
        "schema_version": 2,
        "session_key": "sk-test",
        "captured_at": "2026-09-21T00:00:00+00:00",
        "orgs": [{"uuid": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d", "name": None,
                  "capabilities": ["chat"], "seen_in_response": True}],
        "primary_org_id": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d",
        "org_id": "ae24ae66-4622-48e7-b4b3-1ab2c49f933d",  # legacy mirror
    }

    async def fake_capture(timeout: int = 300, **kwargs):
        return creds

    monkeypatch.setattr(
        "fetcher.playwright_capture.capture_credentials", fake_capture, raising=False
    )
    out = tmp_path / "credentials.json"
    result = CliRunner().invoke(cli, ["capture", "--output", str(out)])

    assert result.exit_code == 0, result.output
    assert out.is_file()
