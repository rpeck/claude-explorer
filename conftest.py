"""Root pytest configuration.

Holds the test-client host names for the backend's Host guard, and one
diagnostic: report threads that would block interpreter shutdown. Windows CI passed every test and still failed the step, because a
worker process never exited and was interrupted at threading shutdown. A
non-daemon thread left running is the usual cause, and it is invisible on
POSIX when the same thread happens to be collected earlier.
"""

from __future__ import annotations

import os
import threading

# The backend answers only loopback Host names (backend/host_guard.py).
# Starlette's TestClient sends "Host: testserver", and the httpx ASGI clients
# in backend/tests use "test". Set here, at the root, because mcp_server/tests
# also calls the backend app.
os.environ.setdefault("CLAUDE_EXPLORER_ALLOWED_HOSTS", "testserver,test")


def pytest_sessionfinish(session, exitstatus):
    """Name any non-daemon thread still alive when the session ends."""
    lingering = [
        t
        for t in threading.enumerate()
        if t is not threading.main_thread() and t.is_alive() and not t.daemon
    ]
    if not lingering:
        return
    print("\n=== NON-DAEMON THREADS STILL ALIVE AT SESSION END ===")
    for t in lingering:
        print(f"  name={t.name!r} class={type(t).__module__}.{type(t).__qualname__}")
    print("=== these block interpreter shutdown ===")
