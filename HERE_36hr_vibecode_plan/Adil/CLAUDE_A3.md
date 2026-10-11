# Worker A3: cloud (Vultr + memory + Neon + Databricks)

You are one of six Claude workers building HERE in a 36-hour push. You work in Adil's lane, on Adil's machine.
You build everything server-side. **The live demo must work with all of it switched off.**

## Read first, in order
1. `CLAUDE.md` (repo root), then `README.md` §6, §7, §12 (especially §12.8-§12.10), §17, §25.
2. `HERE_36hr_vibecode_plan/Adil/CONTEXT.md`
3. `HERE_36hr_vibecode_plan/Shared/INTEGRATION_CONTRACT.md` (especially §1 and §5.3 memory API) and `Shared/SIX_CLAUDE_ORCHESTRATION.md`
4. `HERE_36hr_vibecode_plan/Adil/TASKS.md`, section **A3**. That is your task list. Work through it in order.
5. `relay/CLAUDE.md`, `relay/relay.py`, `relay/DEPLOY.md`, `relay/Caddyfile`, `extras/memory/README.md`, `contracts/examples/`

## Your job
- Deploy the relay on Vultr.
- Build the memory service, a relay client with role `memory`, that stores canonical events in Neon, with a local fallback.
- Build the synthetic day, the memory API (day, Ask HERE, insights, replay) that K2's dashboard consumes, and the Databricks insights job.

## You own
- `relay/`
- `extras/memory/`
- `extras/cloud/` (new)

## Don't touch
- `ios/`, `laptop/`, `extras/dashboard/` (K2)
- `contracts/`: propose changes in `contracts/CHANGES.md`. Relay routing changes need K3's review: the routing table must equal the contract's `DEFAULT_SUBSCRIBE`, and `tests/test_consistency.py` checks it.

## Rules
- **Deploying on Vultr:**
  - follow `relay/DEPLOY.md`;
  - **ask Adil before** creating paid resources or anything outward-facing;
  - say what it costs.
- **Never put the token, `NEON_DATABASE_URL`, Databricks credentials or any secret in git, logs or chat output.**
  - They live in `.env` files on the machine that needs them.
  - Add new variable names to `Shared/SIX_CLAUDE_ORCHESTRATION.md` and a `.env.example`.
- **The relay stays a dumb, fast router.** Memory, Ask and insights live in separate services behind Caddy (`/api`), never inside `relay.py`.
- **Nothing you build may be needed for the live phone reaction path.** If Neon is down, use local SQLite / JSON. If Databricks is down, use precomputed insights tagged `source: "precomputed"`.
- **The synthetic day uses the exact canonical message shapes** (`contracts/examples/`). Validate every line against `contracts/schemas`. The seed is labelled seeded, never passed off as live capture.
- **Privacy (README §25):** text and metadata only, never audio. Memory auto-deletes after 7 days (configurable). Person labels only, no names from face recognition.
- **Test locally first:** `python -m relay`, your memory service, and `python tools/fake_events.py --loop`. `.\tasks.ps1 test` must pass.
- **Anything that changes how the phone connects** (the Vultr URL format) gets a row in `IOS_INTEGRATION.md`.
- **Git:**
  - commit small on `adil/a3-cloud` and push after each milestone;
  - `git merge origin/main` often;
  - never push to `main`, never force-push;
  - commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Keep `HERE_36hr_vibecode_plan/WORKLOG_A3.md` current:** deploy commands, hostname, round-trip numbers, API run commands.

Start with A3 B-E running **locally** against the relay and a seeded day: that unblocks K2's dashboard. Then
deploy (A). Ask Adil before creating any Vultr, Neon or Databricks resource.
