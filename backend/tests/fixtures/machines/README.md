# Machine bundle fixtures (#92)

## `v1-encrypted.cebundle`

An encrypted bundle in the format from before the A2 security round 2 (S6):
the header holds no scrypt parameters, and the key used N = 2^15, r = 8, p = 1.
Bundles in this format must still decrypt.

- Made on 2026-11-27 with `backend.machines.export_bundle` at commit 124fccaf,
  by the synthetic machine B of `backend/tests/_machines_env.py`.
- Passphrase: `synthetic fixture pass`.
- Machine id: `46b6aaee-ff4b-4cb3-a561-8ee694d67501`.
- One Claude Code session, `a2f6f6f6-0000-4000-8000-000000000006`, with the word
  `fixturevonekdfword`. Every value is synthetic.
