# START: one sentence sets up a whole lane

**Humans:** open Claude Code in the repo's main folder (`shh`, on `main`) and say your line:

| Who | Say |
|---|---|
| Kori | **"I'm Kori, I'm ready to go"** |
| Adil | **"I'm Adil, let's go"** |

Adil, the first time: clone the repo first (`git clone https://github.com/Korirussell/Here.git`), then open Claude Code in
that folder. Python 3.12 must be installed.

Claude then follows the steps below for that person. It sets up the three worktrees, checks everything, and opens
three Windows Terminal tabs, each running a Claude worker that already has its kickoff prompt.

---

## Instructions for Claude

When the user says one of the lines above, follow that person's section from top to bottom without asking further
questions. The line itself is the go-ahead for everything here, including opening the worker tabs. **Stop and report**
only if a step fails in a way the section doesn't cover.

**Rules for both:**
- Never print the contents of `.env` files. Only report "present / missing" and which variable *names* are set.
- Never force anything in git. If `main` has local changes or doesn't fast-forward, stop and show `git status`.
- `ROOT` = the main checkout's absolute path. Worktrees are its siblings: `<parent of ROOT>\shh-k1` and so on.
- If a worktree folder already exists and is on the right branch, reuse it. If the branch exists but the folder doesn't, run `git worktree add <dir> <branch>` without `-b`.
- Workers use the main checkout's Python: `ROOT\.venv\Scripts\python.exe`. Each launch prompt says so.

**Launching a worker tab** (PowerShell; one call per worker):
```powershell
wt.exe -w 0 new-tab --title "<ID>" -d "<worktree dir>" claude "<launch prompt>"
```
- Launch prompts must **not contain semicolons**: Windows Terminal treats `;` as a command separator.
- If `wt.exe` or `claude` isn't on PATH, don't launch. Print the three folders and prompts in a table for the human to open by hand.

### Kori (demo laptop with the ReSpeaker)

1. **Update:** `git checkout main`, then `git pull --ff-only`.
2. **Free the array:** make sure no `python -m laptop` process is running (if one is, ask before stopping it). Then run `ROOT\.venv\Scripts\python.exe laptop\audio\xvf3800.py --reset` **unsandboxed**. If the array isn't found, report it and carry on.
3. **Worktrees:**
   - `..\shh-k1` on `kori/k1-respeaker`
   - `..\shh-k2` on `kori/k2-dashboard`
   - `..\shh-k3` on `kori/k3-integration`
4. **Secrets:** if `laptop\.env` exists, copy it into `laptop\` of each worktree. Report which variable names it sets (for example `HERE_RELAY_TOKEN`, `HERE_RELAY_URL`), not their values.
5. **Check:** run `ROOT\.venv\Scripts\python.exe -m pytest contracts relay tests -q` in ROOT. It must pass. Skip golden here: K3 runs it.
6. **Launch three tabs:**

   | ID | Folder | Launch prompt |
   |---|---|---|
   | K1 | `..\shh-k1` | `I'm K1. Read HERE_36hr_vibecode_plan/Kori/CLAUDE_K1.md and do it. Branch kori/k1-respeaker. Python: <ROOT>\.venv\Scripts\python.exe. You are the only worker allowed to use the ReSpeaker.` |
   | K2 | `..\shh-k2` | `I'm K2. Read HERE_36hr_vibecode_plan/Kori/CLAUDE_K2.md and do it. Branch kori/k2-dashboard. Python: <ROOT>\.venv\Scripts\python.exe. Do not use the ReSpeaker - work from fake_events and seeded JSON.` |
   | K3 | `..\shh-k3` | `I'm K3. Read HERE_36hr_vibecode_plan/Kori/CLAUDE_K3.md and do it. Branch kori/k3-integration. Python: <ROOT>\.venv\Scripts\python.exe. Bring contract decisions and merges to Kori before acting.` |

7. **Report** in about five lines:
   - tabs opened;
   - array reset or not found;
   - `.env` present or missing;
   - test result;
   - "first merges to watch for: K1 scenes, A3 seeded day + memory API, K3 end-to-end tests".

### Adil (no array, no NVIDIA GPU)

1. **Update:** `git checkout main`, then `git pull --ff-only`.
2. **Python environment** (only if `ROOT\.venv` doesn't exist):
   - `py -3.12 -m venv .venv` (or `python -m venv .venv` if that is 3.12);
   - **CPU** PyTorch first: `.venv\Scripts\pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu`;
   - then `.venv\Scripts\pip install -r laptop\requirements.txt`.

   This takes a while; tell Adil it's running.
3. **Fixtures:** if `fixtures\rot_talker_baby` is missing, run `.venv\Scripts\python.exe tools\make_raw_scene.py` to rebuild the rotated scenes from the committed `raw_speaker_180`.
4. **Worktrees:**
   - `..\shh-a1` on `adil/a1-speech`
   - `..\shh-a2` on `adil/a2-sound-ai`
   - `..\shh-a3` on `adil/a3-cloud`
5. **Secrets:**
   - If `laptop\.env` exists, copy it into `laptop\` of each worktree. Report whether it sets `ELEVENLABS_API_KEY` and `GEMINI_API_KEY` (names only).
   - If it's missing or lacks them, tell Adil which ones to add. The workers can start without keys: they use fakes and fallbacks first.
   - A3's cloud credentials (`NEON_DATABASE_URL`, Databricks, the Vultr relay token) get added later, when A3 asks.
6. **Check:** run `.venv\Scripts\python.exe -m pytest contracts relay laptop tests -q` in ROOT. It must pass on CPU. Golden isn't run here: K3 runs it on Kori's laptop.
7. **Launch three tabs:**

   | ID | Folder | Launch prompt |
   |---|---|---|
   | A1 | `..\shh-a1` | `I'm A1. Read HERE_36hr_vibecode_plan/Adil/CLAUDE_A1.md and do it. Branch adil/a1-speech. Python: <ROOT>\.venv\Scripts\python.exe. No ReSpeaker and no NVIDIA GPU on this machine - use fixtures.` |
   | A2 | `..\shh-a2` | `I'm A2. Read HERE_36hr_vibecode_plan/Adil/CLAUDE_A2.md and do it. Branch adil/a2-sound-ai. Python: <ROOT>\.venv\Scripts\python.exe. No ReSpeaker and no NVIDIA GPU on this machine - use fixtures and recorded clips.` |
   | A3 | `..\shh-a3` | `I'm A3. Read HERE_36hr_vibecode_plan/Adil/CLAUDE_A3.md and do it. Branch adil/a3-cloud. Python: <ROOT>\.venv\Scripts\python.exe. Ask Adil before creating any paid or public cloud resource.` |

8. **Report** in about five lines:
   - tabs opened;
   - venv created or reused;
   - fixtures rebuilt;
   - which keys are present or missing;
   - test result.
