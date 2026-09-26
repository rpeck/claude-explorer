# Testing: Manual checks on each platform

Part of [TESTING.md](../TESTING.md). Read this file when you verify a release by hand. Also read it when your pull request changes install, credential capture, fetch, the watcher, PDF export, the MCP server, or the launcher.

## 8 · Verifying each platform by hand

CI cannot test three things:

- the native Claude Desktop app
- operating-system certificate trust
- an interactive login

[§10](ci.md) explains why. A person must check these things by hand.

**Who runs these checks:**

- **The maintainer** runs every check on every platform before each release.
- **A contributor** runs the checks that apply to the change, on each platform that the contributor can reach. Record the results in the table in the pull-request template.
- You do not need every platform. For a platform that you cannot test, write "not available". The maintainer covers it before the release.

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

### What you need

- **A Claude account** with at least one conversation. Use your own account. Steps 2 and 5 need it.
- **Claude Desktop, logged in,** on macOS or Windows. Step 3 and proxy capture in step 5 need it. Download it from [claude.ai/download](https://claude.ai/download), and log in with your test account.
- **uv** on each test machine. Install it with step 1 of the Quick Start in the [README](../README.md).
- **Node.js 20.19 or later** on the one machine that builds the wheel in step 1. The test machines do not need Node.js.
- **The PDF libraries** for step 4. The README lists them in its section "PDF export (optional)".

### Set up a test machine

Your own computer is a valid test machine for its own platform. A clean machine finds more problems, because your own computer already has Claude Explorer state, tools, and settings.

**Choose an environment for each platform that you want to test.**

- **On a Windows computer:**
  - Windows itself: Windows x64, or Windows ARM64 on an ARM PC.
  - **Windows Sandbox:** a clean Windows that resets when you close it. It needs Windows 11 Pro, Enterprise, or Education. It does not keep Claude Desktop logged in between sessions.
  - **WSL 2** with an Ubuntu distribution, for Linux. WSLg shows the browser window that capture opens.
    - WSL 2 does not give a full Linux desktop session. So the menu entry and its Stop action in step 6 may not appear, or may look different.
    - For step 6 on Linux, use a full Linux VM.
  - **VirtualBox or VMware Workstation,** for full Linux or Windows VMs. Both work on Windows 11 Home.
  - **Hyper-V,** for full VMs. It needs Windows 11 Pro, Enterprise, or Education.
  - All VMs need hardware virtualization, which you turn on in the firmware settings of the computer.
- **On a Mac:**
  - macOS itself.
  - **UTM, Parallels Desktop, or VMware Fusion,** for Linux and Windows VMs.
  - On Apple silicon, use ARM64 guests: Ubuntu ARM64 and Windows 11 ARM64. An x86_64 guest runs under full emulation, which is too slow for interactive work.
- **On a Linux computer:**
  - Linux itself.
  - **GNOME Boxes, virt-manager (KVM), or VirtualBox,** for Linux and Windows VMs of the same processor type as the host.
  - A Windows 11 guest on KVM needs three things: TPM 2.0 emulation (`swtpm`), UEFI firmware (OVMF), and the `virtio-win` drivers for its disk and network. virt-manager can set up all three.
- **With no suitable hardware:**
  - Rent a cloud VM with the operating system that you need. Not every provider offers Windows on ARM64.
  - Or test your own platform only. The install matrix already checks install, `serve`, the watcher, and the launcher on all six platforms. See [§7](ci.md).
- **macOS runs only on Apple hardware.** Without a Mac, write "not available" for macOS.

**Get the operating system for a VM.**

- **Ubuntu:** download the desktop image from [ubuntu.com/download/desktop](https://ubuntu.com/download/desktop). Pick the image that matches the processor type of the host.
- **Windows 11:** download an installation image from the Microsoft website. Pick the image that matches the processor type of the host. A Windows license is your responsibility.

**Keep a clean baseline of each VM.**

1. Install the operating system and its updates.
2. Save a clean copy of the VM: a snapshot, or a clone.
3. Before each test run, restore that clean copy.

Some hypervisors cannot snapshot some guests, for example guests that use hardware acceleration. For those guests, clone the VM.

**Before you boot a VM, make sure that the host disk has free space.** A VM writes its changes into a disk file that grows. If the host disk fills, that file can become corrupt.

**Clean state on your own computer.** If you test on a computer that already has Claude Explorer, remove the old install first:

1. Remove the watcher: `claude-explorer install-watcher --uninstall`
2. Remove the launcher: `claude-explorer install-app --uninstall`
3. Remove the tool: `uv tool uninstall claude-explorer`

> **Warning:** Do not delete the `.claude-explorer` folder in your home folder. It holds your downloaded conversations. To test a first run, rename the folder, for example to `.claude-explorer.bak`. After the test, delete the new folder, and rename the old one back.

### Step 1. Install the build under test

Test the build that you plan to release, not the older build on PyPI. `uv tool install claude-explorer` installs the PyPI release.

1. On a machine with uv and Node.js, get a checkout of `main`. The section "From source" in the [README](../README.md) shows how. To test a pull request, check out its branch.
2. In the checkout, build a wheel. The build also compiles the web UI:

   ```
   uv build --wheel
   ```

3. Copy `dist/claude_explorer-<version>-py3-none-any.whl` to each test machine.
4. Install the wheel on each test machine:
   - macOS, Linux, and Windows x64: `uv tool install --force <path-to-wheel>`
   - Windows ARM64: `uv tool install --force <path-to-wheel> --python cpython-3.13-windows-x86_64-none`

After the release, check the PyPI build the same way: replace `<path-to-wheel>` with `claude-explorer`.

A VM that you set up earlier contains the build that was current at that time. Restore its clean baseline, or remove the old install as described above.

### Step 2. The web UI (every platform)

The conversation list needs at least one conversation. On a clean machine, do the browser capture and fetch in step 5 first.

1. Run `claude-explorer serve`.
2. Open `http://localhost:8765` in the default browser of the platform:
   - Safari on macOS
   - Firefox on Linux
   - Edge on Windows
3. Confirm that the conversation list renders.
4. Open one conversation. Confirm that its messages render.
5. Run a search. Confirm that results appear.
6. Confirm that the source filter offers All, Desktop, Code, and Cowork.

**Pass:** each item above gives the described result, and the browser console shows no error.

### Step 3. The MCPB bundle (macOS and Windows)

1. Get the bundle for the build under test:
   - Before a release, build it in the checkout from step 1:
     1. Install the mcpb CLI one time: `npm install -g @anthropic-ai/mcpb`. On Linux, this command can need admin rights, depending on how you installed Node.js.
     2. Run `uv run python scripts/build-mcpb.py`. The bundle is `dist/claude-explorer-<version>.mcpb`.
   - After a release, download the `.mcpb` from the latest GitHub Release.
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

1. Install the graphics libraries that WeasyPrint needs. The section "PDF export (optional)" in the [README](../README.md) gives the commands for each platform.
2. From the web UI, export a conversation as PDF.

**Pass:** the file opens, and it starts with the `%PDF` magic bytes.

If a user needs only Markdown, the user can skip this step:

- Markdown export needs no extra libraries.
- `serve` starts normally without these libraries.

### Step 5. Credential capture and fetch

**Browser capture works on every platform.** Do browser capture first. Use your own Claude account:

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
4. On Windows, find the install path:
   1. Start Claude Desktop normally.
   2. Run `Get-Process Claude | Select-Object Path`.
   3. Quit Claude Desktop.
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
- The install path from item 4 of the proxy procedure. A path under
  `WindowsApps` identifies a Store install.

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
