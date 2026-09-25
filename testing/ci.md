# Testing: CI, credentials, and the limits of automation

Part of [TESTING.md](../../TESTING.md). Read this file when: you change ci, or need to know what it proves.

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
- By manual dispatch. The `source` input selects the checkout or the
  published PyPI package.

The push and pull-request triggers apply only when a change touches the
Python code, `pyproject.toml`, `uv.lock`, `README.md`, or the workflow file.

**The `install` job runs the README commands themselves.** So the
documentation is under test, not only the code. On each runner it checks
these items:

- The documented install command works. On Windows ARM64 this includes the
  x86_64-Python pin. The step prints the interpreter, which must report
  `AMD64` on an `ARM64` machine. That proves the Prism translation path.
- All six subcommands appear in `--help`.
- The Chromium build installs and launches. On Linux the install uses
  `--with-deps`, which adds the system libraries.
- `serve` answers `/api/config` with HTTP 200.
- The watcher installs, and its supervisor reports it:
  - macOS: `launchctl list` shows the job.
  - Linux: `systemctl --user is-active` reports the unit active. The job
    first enables lingering, so the runner has a user session bus.
  - Windows: `schtasks` finds the task.
- The uninstall removes the watcher, and a second query confirms it is gone.
- The launcher from `install-app` starts and stops the server. Each
  step first waits for port 8765 to be free, so a server from an earlier
  step cannot make it pass.
  - macOS: the app passes `codesign --verify`. Opening it starts the
    server, and quitting it stops that server.
  - Linux: the menu entry passes `desktop-file-validate`. Its own `Exec`
    lines start and stop the server.
  - Windows: the shortcut targets `pythonw.exe` with the tray arguments,
    and `pystray` imports. The launcher's `open` and `stop` start and
    stop the server. CI cannot click the tray icon; section 8 covers it.

**The `pytest` job runs the full suite on the same six runners.** It uses
xdist on macOS and Linux. On Windows it runs serially with `-n 0`. There the
xdist controller hung at shutdown, and parallel runs hid order-dependent
failures.

## 9 · Protecting credentials on every platform

`~/.claude-explorer/credentials.json` holds the session key. Anyone who can
read it can act as the user on claude.ai. The app therefore restricts the
file, and its `.bak` copy, to the current user on every platform.

Each platform needs a different mechanism, because each stores permissions
differently. `harden_path_permissions` in `fetcher/credentials.py` applies
the right one.

| Platform | Mechanism | What the app does |
|---|---|---|
| macOS | POSIX mode bits | Sets mode `0600` (owner read and write only) |
| Linux | POSIX mode bits | Sets mode `0600` (owner read and write only) |
| Windows | NTFS access control list | Removes inherited entries, then grants the current user alone |

Windows ignores POSIX mode bits entirely. Before 2026-09-22 the app set
`0600` on Windows too, which did nothing, and the file stayed readable by
every account the inherited access list allowed. The fix gives Windows the
same protection as macOS and Linux, through the mechanism Windows actually
uses.

### Verify it

- **macOS:** `stat -f '%Sp' ~/.claude-explorer/credentials.json`.
  Expect `-rw-------`.
- **Linux:** `stat -c '%A' ~/.claude-explorer/credentials.json`.
  Expect `-rw-------`.
- **Windows:** in PowerShell,
  `icacls "$env:USERPROFILE\.claude-explorer\credentials.json"`.
  Expect only your own account. `Everyone`, `BUILTIN\Users`, and
  `Authenticated Users` must not appear.

The protection is best effort on every platform. If the operating system
refuses, the app logs a warning and keeps the credential rather than fail
the capture. A user who reports a permission problem should run the check
for their platform.

## 10 · What cannot be automated, and why

**Browser automation drives web pages.** That covers Playwright, and Claude
in Chrome. Of the manual checks, only the web UI in step 2 is a web page:

- The web UI **can** be automated. CI already proves `serve` answers with
  HTTP 200. A Playwright screenshot pass would add the visual check.

The rest is not browser work, so browser automation cannot reach it:

- **The MCPB drag-drop** is a native Claude Desktop dialog. A browser tool
  cannot see another application's windows.
- **Certificate trust** for proxy capture is an operating-system security
  decision, made outside any browser.
- **The real login** is interactive by design. Scripting a login through a
  proxy is exactly the flow the security design makes hard.

**Desktop automation** is a separate technology. It controls the whole
screen, mouse, and keyboard, and it could in principle drive the MCPB
drag-drop. Three things argue against it here:

- It runs against a VM's display, so it is slow and breaks when a dialog
  moves or changes its wording.
- Proxy capture needs the maintainer's real account. That login must not be
  handed to an automated tool.
- Each check runs once per release, so the manual cost is small.

Keep steps 3 and 5 manual. Automate the visual half of step 2 when a
screenshot pass is added to CI.
