# Contributing to claude-explorer

Thanks for the interest. This is a solo-maintained project; PRs are welcome but please open an issue first for anything bigger than a typo so we can agree on the approach before you write code.

## Prerequisites

- [uv](https://docs.astral.sh/uv/). It installs Python 3.11+ for you if needed.
- Node.js 20.19 or later, and npm, for the React frontend build.
- The system libraries for PDF export, if you work on PDF export. The README lists them per platform in [PDF export (optional)](./README.md#pdf-export-optional).

## Repo setup

Follow [From source](./README.md#prerequisites) in the README. It covers every platform, including the extra steps for Linux and Windows on ARM.

## Running locally (dev mode)

- Back end (with auto-reload):
  `DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run uvicorn backend.main:app --reload --port 8765`
  (On macOS the `DYLD_LIBRARY_PATH` prefix is needed for WeasyPrint; see [AGENTS.md](./AGENTS.md) for details.)
- Frontend (separate dev server): `cd frontend && npm run dev`
- Tests:
  - Backend: `uv run pytest backend/tests -q`
  - Vitest: `cd frontend && npm run test:run`
  - Playwright: `cd frontend && npx playwright test`

## Code style

- Python: PEP 8 with type hints; run `ruff check` and `pyflakes` locally before pushing (CI runs the test suites but does not yet enforce lint; please don't regress).
- TypeScript: strict mode, `tsc --noEmit` clean, eslint via vite-plugin; prefer functional components.
- Testing discipline: see [TESTING.md](./TESTING.md) for the black-box / spec-driven rules, Playwright "deterministic settle barrier" pattern, and the pre-flight checklist.
- General coding practices and project structure are documented in [AGENTS.md](./AGENTS.md).
- Commit messages: conventional commits, no AI attribution lines.

## Bumping GitHub Action versions

Actions in `.github/workflows/` are SHA-pinned for supply-chain integrity — a moving tag like `@v4` could be silently retargeted to a malicious commit, but a 40-char SHA can't. To bump an action:

1. Look up the new version's commit SHA from the GitHub API (don't trust release-notes text or an LLM — read the API directly):
   ```bash
   gh api repos/<owner>/<repo>/git/refs/tags/<tag>
   ```
   If the tag is annotated (`"type":"tag"` in the response), dereference it once more to get the underlying commit SHA:
   ```bash
   gh api repos/<owner>/<repo>/git/tags/<tag-object-sha>
   ```
2. Replace the SHA in the workflow file; update the trailing `# v<tag>` comment so the human-readable version stays accurate.
3. Commit per-workflow with a message like `chore(ci): bump <action> to v<tag>`.

## Pull request process

Every change reaches `main` through a pull request. Direct pushes to `main` are blocked.

1. Open an issue describing the change (skip for typos / docs).
2. Fork, branch, commit.
3. Run all three test suites locally before pushing. Read the counts, as [TESTING.md §0](./TESTING.md) describes.
4. Open the pull request, and fill in the template.
5. On PR open, the [CLA Assistant](https://cla-assistant.io) bot will ask you to sign the [Contributor License Agreement](./CLA.md) (one-time, takes 30 seconds via GitHub OAuth).
6. PR review focuses on: test coverage, voice consistency for any article/doc changes, no silent regressions.

**What a pull request needs before it can merge:**

- The maintainer's approval. `.github/CODEOWNERS` makes the maintainer the reviewer for every file.
- A new approval after any later push. A push after approval cancels the approval.
- Both jobs of the `Tests` workflow pass: the Python suite and Playwright.

**Your first pull request waits for the maintainer.** The CI workflows of an outside contributor run only after the maintainer approves them.

### Manual checks

CI cannot test Claude Desktop, certificate trust, or a real login. [§8 in testing/manual-checks.md](./testing/manual-checks.md) lists the checks that a person must run.

If your change touches install or upgrade, credential capture, fetch, the watcher, PDF export, the MCP server, or the `.mcpb` bundle, run the matching §8 steps on each platform that you can reach. Record them in the table in the pull-request template. The maintainer runs the other platforms before the next release.

### Releases

Only the maintainer releases. Tags that start with `v` start the release workflow. The upload to PyPI and the GitHub Release wait for the maintainer's approval in the `pypi` environment. Before the maintainer tags, they run every §8 check on every platform.

## What we don't accept

- PRs that paste official Anthropic source code or API responses beyond the minimal samples already in test fixtures.
- PRs that bypass the credential capture / fetch model (e.g., scraping `claude.ai` HTML); we deliberately use only the same endpoints the official clients use.
- Feature additions without tests.
