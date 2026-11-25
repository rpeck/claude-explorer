# Claude Code Code Mode fixtures (issue #90)

Real sessions, recorded on 2026-11-24 with Claude Code 2.1.331 and Sonnet 5.5,
with synthetic prompts on synthetic files (`src/app.py`, `src/util.py`). Each
file went through `scripts/scrub_fixture.py --kind cc` and was read in full
before the commit.

| File | What it holds |
|---|---|
| `background-read.jsonl` | One script in auto mode: dispatched in the background (`(dispatched #1)`), settled by a `poll_events` event. Bash and Read inner calls. |
| `failed-refused-subagent.jsonl` | A script that throws; a refused `Write`; a script with `console.log` (stdout); a script that starts a subagent. |
| `subagent-run.jsonl` | The subagent's own run. Its outer result has no `toolUseResult`. |
| `mcp-not-connected-failed.jsonl` | An MCP-to-CLI pipe whose MCP server was still connecting: eight `searchTools` calls (no parent field), a `repl-error-` record, and a failed background run. |
| `mcp-to-cli-pipe.jsonl` | The pipe that worked: `mcp__fetch__fetch` output goes into a Bash `wc -w` heredoc; only the count returns to the model. |

## How to record again

1. Make a scratch folder with `src/app.py` and `src/util.py`.
2. In it, run `env -u ANTHROPIC_API_KEY claude -p --settings '{"env":{"CLAUDE_CODE_EXPERIMENTAL_CODE_MODE":"1"}}' --allowedTools REPL ... -- "<prompt>"`.
   `claude -p` ignores the variable in the shell; it must be in the `--settings` env block.
3. Find the transcript under `~/.claude/projects/<scratch folder, dashed>/`.
4. Run `uv run python scripts/scrub_fixture.py --kind cc --in <transcript> --out <fixture> --home "$HOME" --scratch <scratch folder>`.
5. Read the fixture in full before you commit it.
