# Codex code mode fixtures (issue #90)

Real rollouts, recorded on 2026-11-24 with Codex CLI 0.164.0
(`codex exec --enable code_mode`, model gpt-5.6-terra), with synthetic prompts
on synthetic files. Each file went through `scripts/scrub_fixture.py --kind codex`
and was read in full before the commit.

| File | What it holds |
|---|---|
| `completed.jsonl` | One `exec` with two nested commands; both outputs return to the model. |
| `failed-wait-notify-two-cells-terminate.jsonl` | A script that throws (`Script failed`, `Script error:`); a yielded cell finished by `wait`; `notify()` (its output comes after the final one); two cells alive at once (the first command is ambiguous, the second links exactly); a cell stopped with `wait` `terminate: true` (its command's item comes after the turn ends). |
| `cli-to-cli-pipe.jsonl` | `cat` output goes into a `printf ... \| wc -c` command; only the count returns to the model. |
| `exec-view-image.jsonl` | Recorded 2026-11-26 (issue #103). A script calls `view_image` on a synthetic 16x16 PNG and returns it with `image()`; the `exec` output is a list: the `input_text` header and an `input_image` with a `data:image/png;base64,` URL. |

## How to record again

1. Make a scratch folder with `src/app.py` and `src/util.py`.
2. In it, run `codex exec --enable code_mode --skip-git-repo-check -s read-only --json "<prompt>"`.
3. The first event names the thread id. Find `~/.codex/sessions/**/rollout-*-<thread id>.jsonl`; read only that file.
4. Run `uv run python scripts/scrub_fixture.py --kind codex --in <rollout> --out <fixture> --home "$HOME" --scratch <scratch folder>`.
5. Read the fixture in full before you commit it.
