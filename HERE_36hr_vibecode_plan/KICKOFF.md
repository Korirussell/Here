# Kickoff: one Claude Code session per worker

> The quick way is in `START.md`: say "I'm Kori, I'm ready to go" or "I'm Adil, let's go" in the main folder. This file
> is the manual version of the same steps.

## 1. Set up the worktrees (once per machine)
Use the setup commands in `Shared/SIX_CLAUDE_ORCHESTRATION.md` → "Setup". That gives one folder per worker
(`..\shh-k1`, `..\shh-a1`, ...), each on its own branch.
- Copy the `.env` files you need into each worktree.
- Use the main checkout's venv, or create one per worktree.

## 2. Start each session in its worktree folder and paste its line

| Worker | Open Claude Code in | Paste this |
|---|---|---|
| **K1**, ReSpeaker + event engine (Kori) | `..\shh-k1` | `Read HERE_36hr_vibecode_plan/Kori/CLAUDE_K1.md and do it. You're on branch kori/k1-respeaker. You're the only worker allowed to use the ReSpeaker.` |
| **K2**, dashboard (Kori) | `..\shh-k2` | `Read HERE_36hr_vibecode_plan/Kori/CLAUDE_K2.md and do it. You're on branch kori/k2-dashboard. Don't use the ReSpeaker; work from fake_events and seeded JSON.` |
| **K3**, integration (Kori) | `..\shh-k3` | `Read HERE_36hr_vibecode_plan/Kori/CLAUDE_K3.md and do it. You're on branch kori/k3-integration. Bring contract decisions and merges to me before acting.` |
| **A1**, speech (Adil) | `..\shh-a1` | `Read HERE_36hr_vibecode_plan/Adil/CLAUDE_A1.md and do it. You're on branch adil/a1-speech. No ReSpeaker on this machine; use fixtures.` |
| **A2**, sound AI (Adil) | `..\shh-a2` | `Read HERE_36hr_vibecode_plan/Adil/CLAUDE_A2.md and do it. You're on branch adil/a2-sound-ai. No ReSpeaker on this machine; use fixtures and recorded clips.` |
| **A3**, cloud (Adil) | `..\shh-a3` | `Read HERE_36hr_vibecode_plan/Adil/CLAUDE_A3.md and do it. You're on branch adil/a3-cloud. Ask me before creating any paid or public cloud resource.` |

## 3. Run it
- Each worker logs to `HERE_36hr_vibecode_plan/WORKLOG_<worker>.md`. Skim those instead of scrolling the sessions.
- **Merge green slices into `main` often** (rules in the orchestration guide). Workers pull `main` in.
- **The first merges that unblock everyone:**
  1. K1's `fixtures/events/` + `fake_events --scene`;
  2. A3's seeded day + local memory API;
  3. K3's end-to-end tests.
- **Every change to what the phone receives** lands in `IOS_INTEGRATION.md` for the iOS team.
