# HERE: 36-hour parallel build plan

Start here. Two humans, six Claude workers, plus a separate iOS team.

| Lane | Owns | Workers |
|---|---|---|
| **Kori** (integrator, contract owner; the demo laptop with the ReSpeaker) | The sensor and the story: array → canonical events, the dashboard, integration | K1 ReSpeaker + event engine · K2 dashboard · K3 integration |
| **Adil** (works from recordings, no array) | Meaning and memory: captions, sound AI, cloud | A1 speech · A2 sound AI · A3 cloud |
| **iOS team** | `ios/`. We log what they need in `IOS_INTEGRATION.md` (repo root). | |

## Read

**Kori:**
1. `Kori/CONTEXT.md`
2. `Shared/INTEGRATION_CONTRACT.md`
3. `Kori/TASKS.md`

**Adil:**
1. `Adil/CONTEXT.md`
2. `Shared/INTEGRATION_CONTRACT.md`
3. `Adil/TASKS.md`

**Both:** `Shared/SIX_CLAUDE_ORCHESTRATION.md` (worktrees, file ownership, merges, secrets, ports) and `Shared/ATTENTION.md`.

## Start the workers

**The quick way:** open Claude Code in the main `shh` folder and say **"I'm Kori, I'm ready to go"** or **"I'm Adil,
let's go"**. Claude follows `START.md`: it sets up the worktrees, runs the checks and opens the three worker tabs.

The manual way:

`KICKOFF.md` has the setup steps and the one-line prompt to paste into each Claude session. The full worker
prompts are `Kori/CLAUDE_K1.md` to `K3` and `Adil/CLAUDE_A1.md` to `A3`.

## The one rule

> Kori's lane turns reality into canonical HERE events. Adil's lane gives them words, meaning and memory.
> Everything meets in `contracts/`, which only Kori changes.
