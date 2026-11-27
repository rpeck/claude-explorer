# Codex agent message fixtures (issue #91, L1)

Recorded on 2026-11-27 with Codex 0.164.0 (`codex exec`, `code_mode` on), with a
synthetic prompt in a scratch folder with `src/app.py` and `src/util.py`. Each file
went through `scripts/scrub_fixture.py --kind codex` and was read in full before the
commit.

| File | What it holds |
|---|---|
| `root.jsonl` | The root thread: a `spawn_agent` call and a `send_message` call (namespace `collaboration`), each with an encrypted message; the worker's `FINAL_ANSWER` as a `response_item` `agent_message` in plain text. |
| `worker.jsonl` | The worker thread (`agent_path` `/root/src_count`): the inherited root records, and two received `agent_message` records whose payloads are `encrypted_content` parts equal to the ciphertext of the root's two calls. |

`backend/tests/test_peer_messages.py` writes both into a synthetic Codex home.
