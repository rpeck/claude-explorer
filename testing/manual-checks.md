# Testing: Manual checks on each platform

Part of [TESTING.md](../TESTING.md). Read this file when you verify a release by hand.

## 8 · Verifying each platform by hand

CI cannot test three things:

- the native Claude Desktop app
- operating-system certificate trust
- an interactive login

[§10](ci.md) explains why. Before a release, verify these three things by hand.
Do this on every platform that supports them.

### What each platform supports

| Check | macOS | Linux | Windows x64 | Windows ARM64 |
|---|---|---|---|---|
| Web UI | Yes | Yes | Yes | Yes |
| MCP server (stdio) | Yes | Yes | Yes | Yes |
| MCPB bundle in Claude Desktop | Yes | No: no Claude Desktop | Yes | Yes |
| Browser capture (default) | Yes | Yes | Yes | Yes |
| Proxy capture (`--proxy`) | Yes | No: no Claude Desktop | Yes | Installed, **not yet verified** |
| PDF export | Needs Pango | Needs Pango | Needs GTK3 runtime | Needs GTK3 runtime |

Claude Desktop is not available for Linux. Thus, the two checks that need
Claude Desktop do not apply to Linux.

### Test machines

- **macOS:** the development machine.
- **Linux:** the `claude-explorer-ubuntu` UTM VM (Ubuntu 24.04, ARM64).
- **Windows ARM64:** the `claude-explorer-windows` UTM VM (Windows 11,
  QEMU with HVF, 8 GB RAM).
- **Windows x64:** CI only.
  - On Apple Silicon, an x86_64 guest runs under full emulation. Full
    emulation is too slow for interactive work.
  - For hands-on x86_64 testing, use a cloud Windows VM.

The host runs UTM 4.7.5. This UTM version offers no snapshots for
hardware-accelerated guests. To restore a VM, delete the VM and clone its
`.clean` baseline. The maintainer notes are in `~/.claude-explorer/vm-fleet.md`.

**Before you boot a VM, make sure that the disk has free space.** A 37 GB
image needs space for its write overlay. A full disk can corrupt a `qcow2`
image.

### Step 1. Install the build under test

Test the build that you plan to release, not the older build on PyPI. `uv tool install claude-explorer` installs the PyPI release.

1. On the Mac, build a wheel from `main`: `uv build --wheel`. The build also compiles the web UI, so it needs Node.js on the Mac.
2. Copy `dist/claude_explorer-<version>-py3-none-any.whl` to each test machine.
3. Install the wheel on each platform:
   - macOS, Linux, and Windows x64: `uv tool install --force <path-to-wheel>`
   - Windows ARM64: `uv tool install --force <path-to-wheel> --python cpython-3.13-windows-x86_64-none`

After the release, check the PyPI build the same way: replace `<path-to-wheel>` with `claude-explorer`.

A VM that you set up earlier contains the build that was current at that time.

### Step 2. The web UI (every platform)

1. Run `claude-explorer serve`.
2. Open `http://localhost:8765` in the default browser of the platform:
   - Safari on macOS
   - Firefox on Linux
   - Edge on Windows
3. Confirm that the conversation list renders.
4. Open one conversation. Confirm that its messages render.
5. Run a search. Confirm that results appear.
6. Confirm that the source filter offers All, Desktop, Code, and Cowork.

**Pass:** all six render, and the browser console shows no error.

### Step 3. The MCPB bundle (macOS and Windows)

1. Download the `.mcpb` from the latest GitHub Release.
2. Record the prompts that appear:
   - On Windows, **record the exact SmartScreen text**. Also record the
     buttons that you clicked to continue, for example **More info**, then
     **Run anyway**.
   - On macOS, record each Gatekeeper prompt in the same way.
3. In Claude Desktop, open **Settings**, then **Extensions**. Drag the file onto that page.
4. Accept the install dialog.
5. Confirm that the five tools appear.

**Pass:** the five tools are in the list, and one call returns data.

### Step 4. PDF export (every platform)

1. Install the graphics libraries that WeasyPrint needs:
   - macOS: `brew install pango cairo libffi`
   - Linux: the `pango`, `cairo`, and `libffi` packages of the distribution.
   - Windows: the GTK3 runtime. Follow the WeasyPrint Windows instructions.
2. From the web UI, export a conversation as PDF.

**Pass:** the file opens, and it starts with the `%PDF` magic bytes.

If a user needs only Markdown, the user can skip this step:

- Markdown export needs no extra libraries.
- `serve` starts normally without these libraries.

### Step 5. Credential capture and fetch

**Browser capture works on every platform.** Do browser capture first:

1. Run `claude-explorer capture`.
2. In the window that opens, log in to Claude.
3. Run `claude-explorer fetch`.

**Pass:** both of these conditions are true:

- The `captured_at` value in `~/.claude-explorer/credentials.json` shows
  today's date.
- `fetch` reports conversations.

**Proxy capture** applies on macOS and Windows. On Windows ARM64 it is installed but not yet verified, so record the result there. Proxy capture is
for users who cannot log in on the web but still have a working Claude
Desktop session. Do proxy capture last, because it has the most risk:

1. Start the proxy: `claude-explorer capture --proxy`.
2. Stop the proxy one time. The certificate file appears only after the
   first run.
3. Trust the mitmproxy certificate authority. The CLI prints the command
   for your platform.
   - **Before you launch Claude Desktop, do this step.** The
     `--ignore-certificate-errors` flag covers only the requests that
     Chromium itself makes. The flag does not cover the Electron main
     process.
4. On Windows, confirm the install path:
   `Get-Process Claude | Select-Object Path`.
5. Launch Claude Desktop through the proxy. The CLI prints the command.
6. Click different items in Claude Desktop for a minute.

**Pass:** the addon reports that it captured the credentials.

#### If proxy capture gets no credentials on Windows

You can install Claude Desktop from the **Microsoft Store**. That version
runs in a sandbox from a protected `C:\Program Files\WindowsApps\` folder.
It possibly ignores the proxy flags. Then its traffic never gets to
mitmproxy, and the proxy captures nothing. This statement is a prediction
from the June 2026 test run. Nobody confirmed it yet.

**What the user sees:**

- `capture --proxy` starts.
- The user clicks different items in Claude Desktop.
- No credentials ever appear.
- No error appears.

**Who it affects:** only users who need proxy capture. These users cannot
log in on the web, but they are still logged in to Claude Desktop. All
other users use browser capture and never use the proxy.

**Why it is important for these users:** proxy capture is their only way
back to their archive. Thus, it is possible that a Store-installed Claude
Desktop leaves them with no working recovery path in this tool today.

**What to record:**

- Whether Claude Desktop obeyed the proxy flags.
- The install path from step 4. A path under `WindowsApps` identifies a
  Store install.

### Step 6. The launcher (every platform)

CI proves that each launcher starts and stops the server. CI cannot click
the icons, so check the icons by hand.

1. Stop each `claude-explorer serve` that runs in a terminal.
2. Run `claude-explorer install-app`.
3. Start Claude Explorer from the menu of the platform:
   - **macOS:** open **Claude Explorer** from Launchpad or Spotlight.
   - **Windows:** open **Claude Explorer** from the Start menu.
   - **Linux:** open **Claude Explorer** from the app menu.
4. Confirm that the browser opens the app. On Windows, also confirm that no
   console window appears.
5. On Windows, confirm that the Claude Explorer icon appears in the
   notification area. Click the icon one time. Confirm that the browser
   opens again.
6. Stop the app with the control of the platform:
   - **macOS:** quit the app from the Dock.
   - **Windows:** right-click the notification-area icon, and choose
     **Quit**.
   - **Linux:** right-click the menu entry, and choose **Stop Claude
     Explorer**. Some desktops show this item only in the dock or the app
     grid.
7. Confirm that `http://localhost:8765` no longer answers.
8. Start `claude-explorer serve` in a terminal. Open the launcher. Confirm
   that the browser opens.
   - **Windows:** confirm that no notification-area icon appears. The
     launcher does not manage a server that it did not start.
   - **macOS and Linux:** stop the launcher as in step 6.

   Confirm that the server in the terminal still runs.

**Pass:** each step gives the described result. On Windows, record the
exact text of each SmartScreen or Defender prompt. Do not expect a prompt,
because `install-app` creates the shortcut on the machine.
