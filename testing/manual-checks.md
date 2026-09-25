# Testing: Manual checks on each platform

Part of [TESTING.md](../../TESTING.md). Read this file when: you verify a release by hand.

## 8 · Verifying each platform by hand

CI cannot reach three things: the native Claude Desktop app, operating-system
certificate trust, and an interactive login. Section 10 explains why. Verify
those by hand before a release, on every platform that supports them.

### What each platform supports

| Check | macOS | Linux | Windows x64 | Windows ARM64 |
|---|---|---|---|---|
| Web UI | Yes | Yes | Yes | Yes |
| MCP server (stdio) | Yes | Yes | Yes | Yes |
| MCPB bundle in Claude Desktop | Yes | No: no Claude Desktop | Yes | Yes |
| Browser capture (default) | Yes | Yes | Yes | Yes |
| Proxy capture (`--proxy`) | Yes | No: no Claude Desktop | Yes | Installed, **not yet verified** |
| PDF export | Needs Pango | Needs Pango | Needs GTK3 runtime | Needs GTK3 runtime |

Claude Desktop is not available for Linux, so the two checks that depend on
it do not apply there.

### Test machines

- **macOS:** the development machine.
- **Linux:** the `claude-explorer-ubuntu` UTM VM (Ubuntu 24.04, ARM64).
- **Windows ARM64:** the `claude-explorer-windows` UTM VM (Windows 11,
  QEMU with HVF, 8 GB RAM).
- **Windows x64:** CI only. On Apple Silicon an x86_64 guest runs under full
  emulation, which is too slow for interactive work. For hands-on x86_64
  testing, use a cloud Windows VM.

The host runs UTM 4.7.5. It offers no snapshots for hardware-accelerated
guests, so restore a VM by deleting it and cloning its `.clean` baseline.
Maintainer notes live in `~/.claude-explorer/vm-fleet.md`.

**Free disk space before you boot a VM.** A 37 GB image needs room for its
write overlay, and a full disk can corrupt a `qcow2` image.

### Step 1. Install the build under test

On every platform, install from the current `main`:

- macOS and Linux: `uv tool install --force claude-explorer`
- Windows x64: the same command.
- Windows ARM64:
  `uv tool install --force claude-explorer --python cpython-3.13-windows-x86_64-none`

A VM that you set up earlier carries whatever build was current then.

### Step 2. The web UI (every platform)

1. Run `claude-explorer serve`.
2. Open `http://localhost:8765` in the platform's default browser: Safari
   on macOS, Firefox on Linux, Edge on Windows.
3. Confirm that the conversation list renders.
4. Open one conversation. Confirm that its messages render.
5. Run a search. Confirm that results appear.
6. Confirm that the source filter offers All, Desktop, Code, and Cowork.

**Pass:** all six render, with no error in the browser console.

### Step 3. The MCPB bundle (macOS and Windows)

1. Download the `.mcpb` from the latest GitHub Release.
2. On Windows, **record the exact SmartScreen text** and the path you took.
   On macOS, record any Gatekeeper prompt the same way.
3. Drag the file into Claude Desktop, then Settings, then Extensions.
4. Accept the install dialog.
5. Confirm that the five tools appear.

**Pass:** the five tools are listed, and one call returns data.

### Step 4. PDF export (every platform)

1. Install the graphics libraries WeasyPrint needs:
   - macOS: `brew install pango cairo libffi`
   - Linux: the distribution's `pango`, `cairo`, and `libffi` packages.
   - Windows: the GTK3 runtime. Follow the WeasyPrint Windows instructions.
2. Export any conversation as PDF from the web UI.

**Pass:** the file opens, and it starts with the `%PDF` magic bytes.

A user who needs only Markdown can skip this. Markdown export needs no extra
libraries, and `serve` starts normally without them.

### Step 5. Credential capture and fetch

**Browser capture works on every platform.** Run it first:

1. Run `claude-explorer capture`.
2. Log in to Claude in the window that opens.
3. Run `claude-explorer fetch`.

**Pass:** `captured_at` in `~/.claude-explorer/credentials.json` carries
today's date, and `fetch` reports conversations.

**Proxy capture** applies only on macOS and Windows x64. It serves users who
cannot log in on the web but still have a working Claude Desktop session.
Do it last, because it carries the most risk:

1. Start the proxy: `claude-explorer capture --proxy`.
2. Stop it once. The certificate file appears only after the first run.
3. Trust the mitmproxy certificate authority. The CLI prints the command for
   your platform. Do this **before** you launch Claude Desktop:
   `--ignore-certificate-errors` covers only Chromium's own requests, not
   the Electron main process.
4. On Windows, confirm the install path:
   `Get-Process Claude | Select-Object Path`.
5. Launch Claude Desktop through the proxy. The CLI prints the command.
6. Click around in Claude Desktop for a minute.

**Pass:** the addon reports that it captured the credentials.

#### If proxy capture captures nothing on Windows

A Claude Desktop installed from the **Microsoft Store** runs sandboxed from a
protected `C:\Program Files\WindowsApps\` folder. It may ignore the proxy
flags, so its traffic never reaches mitmproxy and nothing is captured. This
is a prediction from the June test run; it is not yet confirmed.

**What the user sees:** `capture --proxy` starts, the user clicks around in
Claude Desktop, and no credentials ever appear. Nothing fails loudly.

**Who it affects:** only users who need proxy capture, meaning people who
cannot log in on the web but are still logged in to Claude Desktop.
Everyone else uses browser capture and never touches the proxy.

**Why it matters for that group:** proxy capture is their only route back
to their archive. A Store-installed Claude Desktop may therefore leave them
with no working recovery path in this tool today.

**What to record:** whether the flags were honoured, and the install path
from step 4. A path under `WindowsApps` identifies a Store install.

### Step 6. The launcher (every platform)

CI proves that each launcher starts and stops the server. It cannot click
the icons, so check those by hand.

1. Stop any `claude-explorer serve` that runs in a terminal.
2. Run `claude-explorer install-app`.
3. Start Claude Explorer from the platform's own menu:
   - **macOS:** open **Claude Explorer** from Launchpad or Spotlight.
   - **Windows:** open **Claude Explorer** from the Start menu.
   - **Linux:** open **Claude Explorer** from the app menu.
4. Confirm that the browser opens the app, and that no console window
   appears (Windows).
5. Windows: confirm that the Claude Explorer icon appears in the
   notification area. Click it once. Confirm that the browser opens again.
6. Stop it from the platform's own control:
   - **macOS:** quit the app from the Dock.
   - **Windows:** right-click the notification-area icon, and choose
     **Quit**.
   - **Linux:** right-click the menu entry, and choose **Stop Claude
     Explorer**. Some desktops show this only in the dock or the app grid.
7. Confirm that `http://localhost:8765` no longer answers.
8. Start `claude-explorer serve` in a terminal. Open the launcher, and
   confirm that the browser opens.
   - **Windows:** confirm that no notification-area icon appears. The
     launcher does not manage a server that it did not start.
   - **macOS and Linux:** stop the launcher as in step 6.

   Confirm that the terminal's server still runs.

**Pass:** each step behaves as described. On Windows, record the exact
text of any SmartScreen or Defender prompt. None is expected, because
the shortcut is created on the machine.
