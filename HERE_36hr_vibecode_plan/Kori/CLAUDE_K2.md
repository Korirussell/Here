# Worker K2: dashboard, "HERE World"

You are one of six Claude workers building HERE in a 36-hour push. You work in Kori's lane, on the demo laptop.
You build the screen judges stare at while the iPhone shows the wearer's view.

## Read first, in order
1. `CLAUDE.md` (repo root), then `README.md` §1, §4, §12, §17, §23.
2. `HERE_36hr_vibecode_plan/Kori/CONTEXT.md`
3. `HERE_36hr_vibecode_plan/Shared/INTEGRATION_CONTRACT.md` (especially §4 live messages and §5.3 memory API) and `Shared/SIX_CLAUDE_ORCHESTRATION.md`
4. `HERE_36hr_vibecode_plan/Shared/ATTENTION.md`
5. `HERE_36hr_vibecode_plan/Kori/TASKS.md`, section **K2**. That is your task list.
6. `extras/dashboard/README.md`, `contracts/ts/contract.ts`, `contracts/examples/`, and `laptop/viz.py` + `laptop/viz.html` (an existing three.js sound view to borrow ideas from).

## Your job
A local web dashboard (`extras/dashboard/`, `127.0.0.1:8780`) with three modes:
- **LIVE WORLD:** orb, sources, captions, alerts, latency, provider health, and an **event inspector** that shows which provider produced which field.
- **MEMORY:** timeline, Ask HERE, insights.
- **REPLAY LAB.**

Live data comes from the relay as role `dashboard`. Memory data comes from A3's API, with seeded JSON fallbacks.

## You own
`extras/dashboard/`. Nothing else.

## Don't touch
- `ios/`, `laptop/`, `relay/`, `extras/memory/`
- `contracts/`: propose changes in `contracts/CHANGES.md`

## Rules
- **Work from fixtures from minute one.**
  - Run a local relay with `python -m relay`.
  - Drive it with `python tools/fake_events.py --loop`, or `--scene <name>` once K1 ships scenes.
  - If A3's API isn't up, write your own seed JSON in the shape of `Shared/INTEGRATION_CONTRACT.md` §5.3 under `extras/dashboard/demo_data/`.
  - Never block on another worker.
- **Don't touch the ReSpeaker.** Only K1 runs `--real`.
- **The page must work offline at the booth:** vendor three.js (or any library) into `extras/dashboard/`. No CDN at runtime.
- **Read only contract types** (`contracts/ts/contract.ts`). Ignore unknown message types.
- **`unknown` sounds** always show with their direction.
- **Performance:** cap history, keep animation smooth, no leaks over a 20-minute session. Never display secrets or tokens.
- **Accessibility:** identity never by colour alone; legible contrast.
- **Before building, decide the visual direction** and note it in your WORKLOG. Show the product, not a wall of sponsor logos.
- **Git:**
  - commit small on `kori/k2-dashboard` and push after each milestone;
  - `git merge origin/main` often;
  - never push to `main`, never force-push;
  - commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Keep `HERE_36hr_vibecode_plan/WORKLOG_K2.md` current.**

Start with LIVE WORLD plus the event inspector against `fake_events.py --loop`, then MEMORY on seeded data, then REPLAY LAB.
