# Adil: things only a human can do

Everything below needs an account, a payment method, a key or a physical device. The code for each already exists
and falls back cleanly while it's missing, so nothing here blocks the demo. Order = value per minute.

Never paste a key into chat or git. Every key goes in a gitignored `.env` file (paths below).

## 1. Gemini API key (2 min, free)

Adds the one-line context to meaningful events (`perception_enrichment.context`) and lets Ask HERE phrase answers.

1. https://aistudio.google.com/apikey → **Create API key**.
2. Put `GEMINI_API_KEY=<key>` in:
   - `laptop/.env` (main checkout; the line already exists, empty)
   - `extras/memory/.env` (in `../shh-a3`; today that line holds only a comment)
3. Check: `.venv\Scripts\python.exe -m laptop --replay fixtures/mock_scene --offline` and look for `perception_enrichment`
   messages with a `context` field.

## 2. Vultr relay (10 min, about $5-6/month, billed hourly)

The relay is **no longer on the live path**: the laptop always publishes to the local relay and *mirrors* to Vultr
(`HERE_RELAY_MIRRORS`). Vultr gives the phone a fallback that works on campus Wi-Fi without device-to-device
networking, and hosts the memory API. Without it, the demo runs on the local relay.

1. https://my.vultr.com → Deploy → **Cloud Compute, Shared CPU**, **Atlanta**, **Ubuntu 24.04**, cheapest plan
   with IPv4.
2. SSH key: paste the contents of `C:\Users\User\.ssh\here_vultr.pub` (already generated on this laptop).
3. Copy the server's IPv4, then from the main checkout:
   ```powershell
   .venv\Scripts\python.exe tools\deploy_vultr.py --ip <IPv4> --memory --save-token
   ```
   Installs Caddy + the relay + the memory service, gets the HTTPS certificate, and writes the token and
   `wss://<ip-with-dashes>.sslip.io/ws` into `laptop/.env` (never printed). `--dry-run` shows every step first.
4. Give the iOS team the URL + token (in person or a private channel, not git). The phone tries the local relay
   first and falls back to that URL (`IOS_INTEGRATION.md`).
5. Later updates: `tools\deploy_vultr.py --ip <IPv4> update`. Delete the server after the event to stop billing.
6. Server status page once it's up: `https://<ip-with-dashes>.sslip.io/api/status.html?token=<token>`.
7. **Optional: Object Storage for session bundles** (another Vultr role; costs extra, about $6/month minimum,
   check current price). Vultr → Products → Object Storage → add (same region if offered) → create a bucket, e.g.
   `here-exports`. Copy the hostname, access key and secret key into `extras/cloud/.env`:
   `VULTR_S3_ENDPOINT=https://<hostname>`, `VULTR_S3_BUCKET=here-exports`, `VULTR_S3_ACCESS_KEY=...`,
   `VULTR_S3_SECRET_KEY=...`. Check: `.venv\Scripts\python.exe -m extras.cloud.export_bundle --upload`. Without it,
   bundles are still written to `logs/exports/`.

## 3. ElevenLabs: confirm the plan has Scribe Realtime (done, just know it)

`ELEVENLABS_API_KEY` is in `laptop/.env` and works. Measured here: Scribe first words 1.24 s, final 0.82 s, WER 0.04;
pulling the network mid-sentence hands over to whisper at once and comes back after 10 s. Check the account's
remaining minutes before the demo. Kori picks the demo engine after the live array check (WORKLOG_A1 step 5).

## 4. Already done (no action)

| Service | State |
|---|---|
| Neon | Live (`extras/memory/.env` → `NEON_DATABASE_URL`), HTTPS transport because this Wi-Fi blocks port 5432 |
| Databricks | Live, Free Edition, job `here-insights` (`extras/cloud/.env`) |
| Hugging Face | No token needed (no gated models) |

## 5. Kori's laptop (needs the GPU or the ReSpeaker)

- Golden after today's merges: `.\tasks.ps1 golden` must stay 7/7.
- A2's sound-model bakeoff and the attention benches: `HERE_36hr_vibecode_plan/Adil/A2_ON_KORI_LAPTOP.md`
  (this laptop has 8 GB RAM and was OOM-killed running them).
- Scribe on the array: `python -m laptop --real --engine scribe_realtime`, say "Hey Kori" from behind.
- Copy `laptop/.env` to Kori's laptop (USB / private channel) so Scribe and Gemini work there.

## 6. Optional: CI on GitHub (ask Kori, it's Kori's repo and minutes)

Overnight a SyntaxError in `python -m laptop` reached `main` because nothing runs the gate on push (now caught by
`tests/test_compiles.py`). A GitHub Actions job running `python -m pytest contracts relay laptop tests extras -q` on
each push would stop the next one. It costs Actions minutes on a private repo, so it wasn't added without asking.

## 7. Overnight log

What the overnight Claude loop did is in `CHANGELOG.md` (entries dated 2026-10-10) and the `WORKLOG_A*.md` files.
