# Contributing to claude-explorer

Thanks for the interest. This is a solo-maintained project; PRs are welcome but please open an issue first for anything bigger than a typo so we can agree on the approach before you write code.

## Prerequisites

- Python 3.11+ (uv will bootstrap if missing)
- Node.js 20+ + npm (for the React frontend build)
- System libraries for PDF export (skip if you only care about Markdown export):
    - macOS: `brew install pango cairo libffi`
    - Linux (Debian/Ubuntu): `apt install libpango-1.0-0 libcairo2 libffi-dev`
    - Windows: install [MSYS2](https://www.msys2.org), then in its shell run `pacman -S mingw-w64-x86_64-pango`. Or grab the standalone WeasyPrint .exe from the [WeasyPrint GitHub releases](https://github.com/Kozea/WeasyPrint/releases) to skip the system-library dance entirely.

## Repo setup

```bash
git clone https://github.com/rpeck/claude-explorer
cd claude-explorer
uv sync --extra dev
cd frontend && npm install && cd ..
uv run playwright install chromium
```

## Running locally (dev mode)

- Back end (with auto-reload):
  `DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run uvicorn backend.main:app --reload --port 8765`
  (On macOS the `DYLD_LIBRARY_PATH` prefix is needed for WeasyPrint; see [CLAUDE.md](./CLAUDE.md) for details.)
- Frontend (separate dev server): `cd frontend && npm run dev`
- Tests:
  - Backend: `uv run pytest backend/tests -q`
  - Vitest: `cd frontend && npm run test:run`
  - Playwright: `cd frontend && npx playwright test`

## Code style

- Python: PEP 8 with type hints; run `ruff check` and `pyflakes` locally before pushing (CI runs the test suites but does not yet enforce lint; please don't regress).
- TypeScript: strict mode, `tsc --noEmit` clean, eslint via vite-plugin; prefer functional components.
- Testing discipline: see [TESTING.md](./TESTING.md) for the black-box / spec-driven rules, Playwright "deterministic settle barrier" pattern, and the pre-flight checklist.
- General coding practices and project structure are documented in [CLAUDE.md](./CLAUDE.md).
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

1. Open an issue describing the change (skip for typos / docs).
2. Fork, branch, commit.
3. Run all three test suites locally before pushing.
4. On PR open, the [CLA Assistant](https://cla-assistant.io) bot will ask you to sign the [Contributor License Agreement](./CLA.md) (one-time, takes 30 seconds via GitHub OAuth).
5. PR review focuses on: test coverage, voice consistency for any article/doc changes, no silent regressions.

## What we don't accept

- PRs that paste official Anthropic source code or API responses beyond the minimal samples already in test fixtures.
- PRs that bypass the credential capture / fetch model (e.g., scraping `claude.ai` HTML); we deliberately use only the same endpoints the official clients use.
- Feature additions without tests.
