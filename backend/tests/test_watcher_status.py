"""Pin the cross-platform CC-image-watcher install-detection contract.

Used by :mod:`backend.cc_image_cache` to decide the log level for the
"image referenced by conv X not on disk" message:

* watcher INSTALLED → INFO  (permanent loss is historical; future
  losses are prevented)
* watcher NOT installed → WARNING  (loss is ongoing; user must act)

Tests run with the autouse ``_force_watcher_uninstalled`` conftest
fixture defaulting to "not installed" so dev-machine state can't
leak into the suite. Each test below explicitly overrides via the
``CLAUDE_EXPLORER_WATCHER_INSTALLED`` env var or by stubbing the
platform-detection subprocess.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest


def test_env_override_truthy_returns_true(monkeypatch):
    """``CLAUDE_EXPLORER_WATCHER_INSTALLED=1`` short-circuits the
    platform probe — useful for tests and for debugging when launchctl
    is unreachable (sandboxes, CI containers)."""
    from backend import watcher_status

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "1")
    watcher_status.invalidate_cache()
    assert watcher_status.is_watcher_installed() is True


def test_env_override_falsy_returns_false(monkeypatch):
    """``CLAUDE_EXPLORER_WATCHER_INSTALLED=0`` short-circuits to False
    even if a real launchd agent is present on the developer's machine."""
    from backend import watcher_status

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "0")
    watcher_status.invalidate_cache()
    assert watcher_status.is_watcher_installed() is False


def test_env_override_garbage_falls_through_to_platform_probe(monkeypatch):
    """A garbage value in the env var (anything that isn't 0/1/true/
    false/yes/no) must NOT silently coerce to either True or False —
    we fall through to the real platform probe. This is the
    least-surprise contract; an unparseable override should not
    silently flip the user's correctness signal."""
    from backend import watcher_status

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "maybe")
    watcher_status.invalidate_cache()
    # Force the platform probe to a known answer so we can assert
    # the override was ignored.
    with patch.object(watcher_status, "_platform_check", return_value=True):
        assert watcher_status.is_watcher_installed() is True
    watcher_status.invalidate_cache()
    with patch.object(watcher_status, "_platform_check", return_value=False):
        assert watcher_status.is_watcher_installed() is False


def test_result_is_cached_within_process(monkeypatch):
    """The detection result is cached for the process lifetime — the
    install state doesn't change mid-run in practice. Pin that the
    second call doesn't re-probe."""
    from backend import watcher_status

    monkeypatch.delenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", raising=False)
    watcher_status.invalidate_cache()

    probe_calls = []

    def fake_probe():
        probe_calls.append(1)
        return False

    with patch.object(watcher_status, "_platform_check", side_effect=fake_probe):
        watcher_status.is_watcher_installed()
        watcher_status.is_watcher_installed()
        watcher_status.is_watcher_installed()

    assert len(probe_calls) == 1, (
        f"expected platform probe to fire once (cached); got {len(probe_calls)}"
    )


def test_invalidate_cache_re_probes(monkeypatch):
    """``invalidate_cache()`` MUST force the next call to re-probe.
    Bidirectional pair to the cache test above — verifies the cache
    isn't a write-once dead-end."""
    from backend import watcher_status

    monkeypatch.delenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", raising=False)
    watcher_status.invalidate_cache()

    probe_calls = []

    def fake_probe():
        probe_calls.append(1)
        return False

    with patch.object(watcher_status, "_platform_check", side_effect=fake_probe):
        watcher_status.is_watcher_installed()
        watcher_status.invalidate_cache()
        watcher_status.is_watcher_installed()

    assert len(probe_calls) == 2


def test_subprocess_failure_does_not_raise(monkeypatch):
    """If the platform probe raises (launchctl missing, permission
    denied, etc.) we MUST NOT propagate — detection is advisory.
    Fall back to ``False`` so the data-loss warning stays at WARNING
    level (the louder, safer default)."""
    from backend import watcher_status

    monkeypatch.delenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", raising=False)
    watcher_status.invalidate_cache()

    with patch.object(
        watcher_status,
        "_platform_check",
        side_effect=FileNotFoundError("launchctl"),
    ):
        # Must not raise; must default to False.
        assert watcher_status.is_watcher_installed() is False


def test_macos_probe_matches_on_label_in_launchctl_output(monkeypatch):
    """macOS detection contract: ``launchctl list`` must contain the
    canonical label ``com.claude-explorer.cc-watcher`` exactly."""
    import sys

    if sys.platform != "darwin":
        pytest.skip("macOS-specific probe")

    from backend import watcher_status

    monkeypatch.delenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", raising=False)
    watcher_status.invalidate_cache()

    fake_output = (
        "PID\tStatus\tLabel\n"
        "62263\t0\tcom.claude-explorer.cc-watcher\n"
        "-\t0\tcom.apple.SomethingElse\n"
    )
    with patch.object(
        subprocess, "run",
        return_value=subprocess.CompletedProcess(
            args=["launchctl", "list"],
            returncode=0,
            stdout=fake_output,
            stderr="",
        ),
    ):
        assert watcher_status._macos_check() is True


def test_macos_probe_returns_false_when_label_absent(monkeypatch):
    """Bidirectional pair: launchctl ran fine, but the label isn't in
    the output → watcher not installed. Bare ``substring in output``
    on the wrong field would false-positive any line containing the
    word 'claude'; the test pins that the specific label is the
    match key."""
    import sys

    if sys.platform != "darwin":
        pytest.skip("macOS-specific probe")

    from backend import watcher_status

    monkeypatch.delenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", raising=False)
    watcher_status.invalidate_cache()

    # Note: 'claude' appears in another line but the canonical label
    # does NOT. Must return False.
    fake_output = (
        "PID\tStatus\tLabel\n"
        "111\t0\tcom.claude.something-unrelated\n"
        "222\t0\tcom.apple.foo\n"
    )
    with patch.object(
        subprocess, "run",
        return_value=subprocess.CompletedProcess(
            args=["launchctl", "list"],
            returncode=0,
            stdout=fake_output,
            stderr="",
        ),
    ):
        assert watcher_status._macos_check() is False
