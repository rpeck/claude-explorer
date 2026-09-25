# Testing: CI, credentials, and the limits of automation

Part of [TESTING.md](../TESTING.md). Read this file when you change CI, or when you need to know what CI proves.

## 7 · What CI proves

Two workflows run automatically.

| Workflow | Runners | What it proves |
|---|---|---|
| `test.yml` | `ubuntu-latest` | The Python suite, and Playwright in fixture mode |
| `install-matrix.yml` | Six runners, listed below | The documented install, the CLI, Chromium, `serve`, the watcher, and the Python suite |

### The install matrix

`install-matrix.yml` covers every platform that the README supports. All six
runners are free for public repositories.

| Platform | Runner | Watcher supervisor |
|---|---|---|
| macOS arm64 | `macos-latest` | launchd |
| macOS x86_64 | `macos-15-intel` | launchd |
| Linux x86_64 | `ubuntu-latest` | systemd user unit |
| Linux arm64 | `ubuntu-24.04-arm` | systemd user unit |
| Windows x86_64 | `windows-latest` | Task Scheduler |
| Windows ARM64 | `windows-11-arm` | Task Scheduler |

**When it runs:**

- On a push to `main` or to any `ci/**` branch.
- On a pull request.
- On a manual dispatch. The `source` input selects one of these:
  - the checkout
  - the published PyPI package

The push trigger and the pull-request trigger apply only when a change touches one of these:

- the Python code
- `pyproject.toml`
- `uv.lock`
- `README.md`
- the workflow file

**The `install` job runs the README commands themselves.** Thus the tests
cover the documentation, not only the code. On each runner, the job checks
these items:

- The documented install command works.
  - On Windows ARM64, this includes the x86_64-Python pin.
  - The step prints the interpreter. The interpreter must report `AMD64` on
    an `ARM64` machine. That result proves the Prism translation path.
- All six subcommands appear in `--help`.
- The Chromium build installs and starts.
  - On Linux, the install uses `--with-deps`. This flag adds the system
    libraries.
- `serve` answers `/api/config` with HTTP 200.
- The watcher installs, and its supervisor reports it:
  - macOS: `launchctl list` shows the job.
  - Linux: `systemctl --user is-active` reports the unit active. The job
    first enables lingering, so that the runner has a user session bus.
  - Windows: `schtasks` finds the task.
- The uninstall removes the watcher. A second query confirms that the
  watcher is gone.
- The launcher from `install-app` starts and stops the server. Each step
  first waits until port 8765 is free. Thus a server from an earlier step
  cannot make the step pass.
  - macOS: the app passes `codesign --verify`. When the job opens the app,
    the server starts. When the job quits the app, that server stops.
  - Linux: the menu entry passes `desktop-file-validate`. The `Exec` lines
    of the menu entry start and stop the server.
  - Windows: the shortcut targets `pythonw.exe` with the tray arguments.
    `pystray` imports. The `open` and `stop` commands of the launcher
    start and stop the server.
  - Windows: CI cannot click the tray icon. [§8](manual-checks.md) covers
    the tray icon.

**The `pytest` job runs the full suite on the same six runners.**

- On macOS and Linux, the job uses xdist.
- On Windows, the job runs the tests serially with `-n 0`. The reasons:
  - On Windows, the xdist controller hung at shutdown.
  - On Windows, parallel runs hid order-dependent failures.

## 9 · Protecting credentials on every platform

`~/.claude-explorer/credentials.json` holds the session key. A person who
can read this file can act as the user on claude.ai. Thus, on every
platform, the app gives access to the file and to its `.bak` copy only to
the current user.

Each platform needs a different mechanism, because each platform stores
permissions differently. `harden_path_permissions` in
`fetcher/credentials.py` applies the correct mechanism.

| Platform | Mechanism | What the app does |
|---|---|---|
| macOS | POSIX mode bits | Sets mode `0600` (owner read and write only) |
| Linux | POSIX mode bits | Sets mode `0600` (owner read and write only) |
| Windows | NTFS access control list | Removes inherited entries, then grants the current user alone |

Windows ignores POSIX mode bits completely.

- Before 2026-09-22, the app also set `0600` on Windows. This setting did
  nothing.
- Thus the file stayed readable by every account that the inherited access
  list allowed.
- The fix gives Windows the same protection as macOS and Linux. It uses the
  mechanism that Windows actually uses.

### Verify it

- **macOS:** `stat -f '%Sp' ~/.claude-explorer/credentials.json`.
  Expect `-rw-------`.
- **Linux:** `stat -c '%A' ~/.claude-explorer/credentials.json`.
  Expect `-rw-------`.
- **Windows:** in PowerShell, run
  `icacls "$env:USERPROFILE\.claude-explorer\credentials.json"`.
  - Expect only your own account.
  - `Everyone`, `BUILTIN\Users`, and `Authenticated Users` must not appear.

The protection is best effort on every platform.

- If the operating system refuses, the app logs a warning and keeps the
  credential. The app does not fail the capture.
- If a user reports a permission problem, the user should run the check for
  their platform.

## 10 · What cannot be automated, and why

**Browser automation controls web pages.** Browser automation includes
Playwright and Claude in Chrome. Of the manual checks in
[§8](manual-checks.md), only the web UI in step 2 is fully a web page. Step 4
starts in the web UI, but its check is the PDF file. Step 6 opens the web UI
from a native launcher:

- You **can** automate the web UI. CI already proves that `serve` answers
  with HTTP 200. A Playwright screenshot pass can add the visual check.

The other checks are not browser work. Thus browser automation cannot do
them:

- **The MCPB drag-drop** is a native Claude Desktop dialog. A browser tool
  cannot see the windows of another application.
- **Certificate trust** for proxy capture is a security decision of the
  operating system. The operating system makes this decision outside all
  browsers.
- **The real login** is interactive by design. A scripted login through a
  proxy is exactly the flow that the security design makes difficult.

**Desktop automation** is a different technology. It controls the full
screen, the mouse, and the keyboard. Theoretically, it can control the MCPB
drag-drop. Three facts argue against its use here:

- It runs against the display of a VM. Thus it is slow, and it breaks when
  a dialog moves or changes its text.
- Proxy capture needs the real account of the maintainer. Do not give that
  login to an automated tool.
- Each check runs once per release. Thus the manual cost is small.

Keep steps 3 and 5 manual. When a screenshot pass becomes part of CI,
automate the visual half of step 2.
