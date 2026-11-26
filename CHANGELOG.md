# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Code Mode runs** (issue #90): Claude Code `REPL` and Codex `exec` scripts show as one terminal block (the highlighted script, the numbered tool calls, and what returned to the model), with an overlay (Script, Calls, Data flow, Returned). A **Show Code Mode** toggle, independent of Show Tools, controls the viewer, search (`include_code_mode`, search index v16), exports, and the MCP tools. Codex projection version 5.
- **Dark mode support** with system theme detection
  - Automatically follows system preference by default
  - Manual toggle between Light/Dark/System modes
  - Theme toggle button in sidebar footer
- **Settings page** at `/settings`
  - Theme selection (Light/Dark/System)
  - Keyboard navigation mode (Emacs/Vim)
  - Data directory display
  - Conversation count
  - About section with GitHub link
- **Keyboard navigation** with two modes
  - Emacs mode (default): Ctrl+N/P to navigate, Ctrl+F to open, Ctrl+B to go back, Ctrl+S to search
  - Vim mode: j/k to navigate, l to open, h to go back, / to search, gg/G for top/bottom
  - Press `?` anywhere to see keyboard shortcuts help
- **Connection status dialog** with retry functionality
  - Shows when backend is unavailable
  - Automatic retry with exponential backoff
  - Manual retry and dismiss options
- **Jump-to-bottom button** in conversation detail view
- **Claude Code session support** with:
  - Project path and git branch display
  - Group by project view
  - Subagent expansion in conversation list
  - Phantom session filtering

### Fixed
- Circular reference detection in message tree building
- Iterative BFS for building message trees (prevents stack overflow on large conversations)
- Nested button HTML error in conversation list
- Connection dialog false positive on initial load

### Changed
- Conversation list items now use `role="button"` for proper accessibility

## [0.1.0] - 2024-03-01

### Added
- Initial release
- Browser-based credential capture with Playwright
- Proxy-based credential capture with mitmproxy
- Bulk conversation fetching from claude.ai API
- FastAPI backend with conversation browsing
- Full-text search across conversations
- Markdown and PDF export
- React frontend with Tailwind CSS
- Message tree visualization for branched conversations
- Command palette for quick search (Cmd+K)
