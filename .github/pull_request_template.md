## What this changes

<!-- One or two sentences. Link the issue, for example "Fixes #12". -->

## How you tested it

- [ ] I ran the Python suite and read the counts, as [TESTING.md §0](https://github.com/rpeck/claude-explorer/blob/main/TESTING.md#0--running-the-tests-and-trusting-the-result) describes.
      Result: `___ passed, ___ skipped, 0 failed`
- [ ] If I changed the frontend, I ran `npm run test:run` in `frontend/`.
- [ ] I added or changed tests for this change, and they failed before the fix.

## Manual checks

CI cannot test Claude Desktop, certificate trust, or a real login.
[TESTING.md §8](https://github.com/rpeck/claude-explorer/blob/main/TESTING.md#8--verifying-each-platform-by-hand) lists the checks that a person must run.

**If this change touches any of these areas, run the matching §8 steps:**

- Install or upgrade
- Credential capture, or fetch
- The image-cache watcher
- PDF export
- The MCP server, or the `.mcpb` bundle

Run them on each platform that you can reach. Write "not available" for a platform that you cannot test. The maintainer runs the other platforms before the release.

| Platform | §8 steps run | Result |
|---|---|---|
| macOS | | |
| Linux | | |
| Windows x64 | | |
| Windows ARM64 | | |

- [ ] This change touches none of those areas, so no manual check applies.

## Docs

- [ ] I updated README.md, UX.md, AGENTS.md, or TESTING.md where this change affects them.

## Before you push

- [ ] The diff has no secrets, session keys, personal paths, or email addresses. See "Pre-push checklist" in [AGENTS.md](https://github.com/rpeck/claude-explorer/blob/main/AGENTS.md).
- [ ] The commit messages follow conventional commits, with no AI attribution lines.
