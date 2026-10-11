# WORKLOG K2: dashboard "HERE World"

Branch `kori/k2-dashboard`, worktree `../shh-k2`. Owns `extras/dashboard/` only.

## Visual direction (revised 2026-10-10, Kori: "super clean Vercel look, three.js 3D orb, light mode")

**Light, quiet chrome around one loud thing.**
- The chrome is white with hairline borders, Geist Sans and Mono, and black primary buttons.
- Colour only carries meaning: blue speech, amber sound, violet `unknown`, red alert. Each kind also has a shape and a text label.
- **The hero is a three.js point-field orb** around the array. It swells and takes the colour of each sound in its direction, with beams to 3D markers, floor ripples, alert pillars and the wearer's view cone.
- Above it, a plain-language headline says what HERE understands right now.
- Below it, a live pipeline strip shows which provider handled each message. That's how sponsors are shown: no logo wall.
- three.js r160 and Geist are vendored in `static/vendor/` for the offline booth.

The first pass (a dark top-down 2D radar) was replaced: it looked like a dev tool, not a product.

## Log

### 2026-10-10: LIVE + inspector + MEMORY + REPLAY LAB, first pass (all from fixtures)

**Server** (`extras/dashboard/server.py`): one asyncio `websockets` process on `127.0.0.1:8780`.
- It's a relay client as role `dashboard`, subscribed to `*`.
- It fans messages out to pages over `/live`, wrapped with a receive timestamp. The token never leaves Python.
- It serves `static/`, plus `/api/*` (A3's memory API with seeded fallback) and `/dash/demo` (spawns `tools/fake_events.py`).
- The browser makes only GET requests.

**LIVE:**
- The radar shows sources, sounds, `unknown`, alerts, and the phone `scene` (wearer cone and people).
- Panels: captions, alerts with direction rings, latency (per-type median/p95, onset → first words, `stage_ms`), the event stream, and the health strip.
- **Honesty:** with only `fake_events` running (a `tool` heartbeat, no `laptop` heartbeat), the page shows SCRIPTED and the inspector credits "Scripted", not the ReSpeaker.

**Inspector:**
- per-field providers;
- rules-would-have-said;
- linked ids and timings;
- all related messages, with raw JSON.

**MEMORY:** A3's API isn't up yet, so I wrote my own seed (`demo_data/make_day.py` → `day.json`, `ask.json`, `insights.json`).
- 206 events in canonical v3 shapes plus §5.3 metadata, over 6 places. pytest validates every one.
- The tab has a compressed timeline with person lanes, Ask HERE (canned + keyword fallback), "may have missed", insights and important moments.

**REPLAY LAB:**
- `fallback.replay` is a deterministic offline policy over the stored attention.
- Important alerts are always kept, and suppressed unknowns keep their direction.
- The page has presets, sliders, animated counts, category bars, and a changed-events list with reasons.

**Bugs found while driving it in Chrome:**
- Replayed history made old sounds look fresh: `seen` now uses receive time.
- On Windows a refused localhost connection takes ~2 s, which stalled every offline Ask. A TCP probe (0.3 s) now runs before each API call.
- Crowd filtering had no visible effect, because chatter was already under every threshold: sensitivity now spans 0.1-1.0.

**Tests:** `python -m pytest extras/dashboard` has 10 tests, including relay → dashboard → page end to end on free ports. `contracts relay tests` still pass (65 total).

### 2026-10-10: two speakers + soak

**Two talkers at once** (scratch script):
- two rays, with the scene's wearer cone and both people;
- `attribution` names Person 1 on its caption;
- an `unknown` at +135° with its Hugging Face model in the inspector.

**Fixed:** repeated doorbells (a new `event_id` each time) stacked three markers at the same bearing. Now the radar shows one marker per label and direction.

**Soak**, about 5 minutes with two `fake_events --loop` streams (one at 4x speed), about 35 messages/s:
- DOM is flat at 1052 nodes;
- the stream is capped at 120 rows;
- the JS heap stays at 4-8 MB.

The inspector store was the one thing still growing, so its cap is now 600 ids (was 3000).

### 2026-10-10: redesign: light theme + three.js orb

- `static/orb.js` (new): the point-field orb, with markers pooled by key (geometry and materials disposed on fade-out), at most 24 ripples, and a camera that orbits and drifts back home.
- New pieces: a headline sentence, the pipeline strip, a system popover, and the Run demo menu.
- Memory and Replay lab are restyled to match (segmented place picker, 72 px counts with a delta, coloured category bars).
- Found while driving it in Chrome:
  - the headline re-animated on every caption update; it now animates only when the title changes;
  - the rail's `.alert` card style leaked into the 3D labels and the headline; classes are now namespaced;
  - speech and silence on the same track shared one marker; they're keyed separately now, so the colour can't go stale.
- Can't measure frame rate from the automation tab (Chrome pauses rendering for a hidden tab). The orb is about 5,000 points in one draw call plus about 10 meshes per marker, which is tiny for the RTX 5000. Still to do: check on the demo monitor.

### 2026-10-10: A3 reconciliation + supervisor polish (dates, idle, voices, counts) + K1 scenes

**Memory data (K3 flagged it):** once A3 merged, `extras/memory/seed/insights.json` would have paired with *our* `day.json`, leaving dangling evidence links.
- `fallback.py` now loads **one bundle**: A3's `extras/memory/seed/api/` (day, ask, insights) when it exists, else `demo_data/`. Never mixed.
- Normalizers give the page one shape from every source:
  - location ids become names;
  - A3's Ask (`{question: {answer, evidence}}`) becomes the match list;
  - replay decisions are joined with **the same day** (label, person, bearing, the page's categories; `score` becomes `model_score`);
  - the page's slider policy maps to A3's booleans (`crowd_filter >= 0.25`, `hazard_priority >= 0.6`, `speech_priority > 0.6`).
- `/api/suggest` returns the bundle's own questions.
- Seeded data from the API is badged SEEDED, not "live memory".
- Replay shows MISSED when A3 reports an important event as not surfaced.
- Verified against **A3's real service** (an export of `origin/adil/a3-cloud`, `python -m extras.memory --no-relay` on :8790) and against their snapshots offline: no dangling evidence, and every suggested question answers itself.
- Tests: an A3-shaped bundle, the replay join, the today shift, and a check of A3's real snapshots that is skipped until they merge. I ran it with their files copied in: 15/15.
- Existing tests are pinned to `Seed(S.DEMO_DATA)`; the default seed becomes A3's after their merge.

**Seeded day on today's date:** times shift by whole days, so clock times stay the same (`HERE_DASH_SEED_AS_TODAY=0` turns it off).
- Only items whose id belongs to the seeded day move. A3's Ask also cites live-day events, and those keep their real time.

**Voices:** `fake_events` sends `source_id: 0` for every talker, so everyone was "Voice 1".
- A voice is now a source id plus a direction (within 30°, seen in the last 2 min).
- Numbers are assigned on arrival (receive time), so replayed history keeps speaking order; an utterance keeps its voice.
- Avatars show the number. The inspector no longer says "Speaker 0".

**Idle:**
- The orb runs a slow listening sweep with a soft floor ripple about every 4.5 s. The ramp is time-based: a frame-based ramp never showed in the barely-rendering automation tab.
- The headline reads "Quiet now. Last heard: Voice 3 behind, 1 min ago", or prompts S when nothing is connected.
- A lost or failed WebGL context shows a message instead of a blank canvas.

**Layout:**
- Replay counts have their own column. The default alternative is "Hear everything": under A3's policy, Crowd shield never changes anything and Focus changes nothing at lunch (reported to the supervisor for A3).
- 3D labels clamp inside the canvas and stay below the headline (measured twice a second).
- Below 900 px: the header shows the status as a dot, the demo button is capped, and the pipeline fits.
- The scripted banner text is one span (it had wrapped into flex columns).

**K1 scenes:** I played all seven `fixtures/events` scenes with the scene buttons.
- Setup: my dashboard copied into an export of `kori/k1-respeaker`, so the buttons spawn K1's own `fake_events --scene`. Their CLI matches mine.
- `two_speakers`: Voice 1 front (id 0), Voice 2 behind (id 1).
- `unknown_sound`: violet at -120°, "uncertain what, certain where".
- `doorbell` / `name_called`: alert pillar and urgent / important attention.

**Merge:** origin/main (A1) merged. The CHANGELOG conflict markers got committed (no `python` on PATH); fixed in the next commit.

**Ports:** someone else runs the relay on :8080 and a dashboard on :8780; I left them alone. My test stack: relay :8091, dashboard :8781 (this worktree) and :8782 (in K1's tree), A3 API :8790.

**20-minute soak** (two `fake_events --loop` feeds, 1x + 2x, page on :8781, 1523 px wide): heap 6.9 → 10.6 (7.6 min) → 10.6 (12.9 min) → 11.9 MB (20.2 min); DOM 629 → 644 → 637 nodes; stream capped at 40 rendered rows, 6 captions, 2-5 live 3D labels. Flat: no leak. (The tab only renders on screenshots, so the panels were sampled right after one.)

### 2026-10-10: judge audit, booth resilience, provider attribution

**Judge audit** (TASKS K2 "Done when").
- Setup: page opened cold at 1536 px; each of K1's 7 scenes played; memory API off (the booth's offline case).
- Each scene checked for: where is each sound, what does HERE think it is, who's speaking, what did HERE surface, what does it remember, what patterns did it find.

| Scene | Where | What | Who | Surfaced | Gap found (fixed) |
|---|---|---|---|---|---|
| front_speech | ok (0°, "in front") | ok | Voice 1 | was implicit | the 0° label covered the legend |
| rear_speech | ok ("behind") | ok | Voice 2 | was implicit | none |
| doorbell_rear | ok (+165°, pillar) | ok | n/a | ok (alert) | classifier credited to **"enrolled"** (a raw model id) |
| name_called_rear | ok | ok | Voice 2 | ok (alert) | **the alert held the headline for 30 s over the next 3 scenes** (its "end" never came) |
| unknown_sound | ok (-120°, violet) | "Something I can't name" | n/a | ambient | none |
| two_speakers | ok (0 / 180) | ok | Voice 1 / 2 | was implicit | hidden under the stale alert (above) |
| crowd_noise | ok | ok | Voice 1 | was implicit | none |
| Memory | | | | | the timeline had no legend |
| Replay lab | | | | | the right column didn't name its policy |

Across all scenes:
- "What HERE surfaced" was never stated outright.
- The status said **Degraded** on a normal offline booth.
- After a scene ended, direction was credited to the **ReSpeaker**: scenes replay as `src: laptop` with no heartbeat.

Fixes (commit `aafb1eb`):
- Surfacing chips everywhere (`important → card + belt`, `notice → small cue`), from IOS_INTEGRATION.md's mapping; the headline ends "wearer gets ...".
- An alert leads the headline for 6 s, or until something newer happens. Pillars last 12 s; the rail calls an alert "on" for 30 s at most.
- The ReSpeaker is only credited with a fresh laptop heartbeat. A scene in the last minute counts as scripted, otherwise "not connected".
- Voice numbers are held 10 min, so a number on screen is never reused. Voices are matched by direction only (`0df0512`).
- "Enrolled sounds"; labels avoid the legend; a memory legend; the Replay lab names its alternative ("Hear everything" / custom); the status says "Fallbacks active".

Checked and fine:
- Tab clicks that "didn't work" were clicks right after load in the automation tab, before the modules had loaded. A scripted `.click()` and later real clicks switch tabs.

**Provider attribution:** every event's inspector shows the whole chain, one row per stage:

> Direction → Caption / Sound class → Attention → Context (Gemini) → Who (iPhone) → Transport (Vultr) → Memory (Neon) → Trends (Databricks)

- Each row is a named provider, an amber **fallback** that names what's missing, or a dashed **not connected**. Nothing is blank.
  - Fallbacks: whisper "ElevenLabs not in use", PANNs, rules, local relay, local replay, seeded JSON, SQLite, precomputed.
- The pipeline strip matches: an amber dot on a fallback stage, plus a Trends node.
- The Memory row no longer claims "mirrored" when no memory service runs.

**Booth resilience** (`0df0512`):
- With no relay, Run demo / S / 1-3 / L / scene buttons play K1's scenes from the dashboard server itself, labelled "Local replay ... Not live".
- If the relay drops while `fake_events` plays, the same request resumes locally by itself.
- Local messages stay out of the latency stats.
- Drilled in Chrome: relay up, loop running, relay killed. Within about 6 s the page showed "Local replay · all moments (loop)" and kept playing.
- 3 new tests: rebase, no-relay Run demo end to end, relay lost mid-demo.

### 2026-10-10: input before load is never lost; cold-load-to-interactive measured

**Risk:** right after a refresh, a key press or tab click arrived before `app.js` (and three.js, about 650 KB) had loaded, and was dropped.

**Fix:**
- An inline classic script in `<head>` queues keys and tab / Run demo clicks from first paint. `app.js` replays them, in order, through the same handlers once the orb exists (`ready()`).
- While loading, "Run demo" is dimmed and says "loading".
- `modulepreload` covers the whole module graph. The server lets the browser cache `vendor/` (three.js, fonts) for a day; app code stays `no-store`.

**Verified** (iframe with `app.js` held back 1.5 s): `p` plus a Memory click during loading were queued (2), replayed in order (ended on Memory), and the loading flag cleared.

**Measured on this laptop** (`window.__hereInteractive`, ms from navigation to every handler attached and the orb built):

| Case | Interactive |
|---|---|
| First load after a server restart (three.js fetched fresh) | 237 ms |
| Warm reloads (three.js from cache, 0 KB), 6 runs | 95, 150, 154, 156, 156, 191 ms |

**Panic (Shift+Esc)** during a looping scene, from the Replay lab:
- The view is back on LIVE with state cleared in **1 ms**; the screen repaints on the next frame.
- The server reports the demo stopped in **15 ms**.
- Before this change, panic waited for the server to terminate `fake_events` first (about 1 s).

**Footgun removed:** an unknown `/dash/demo?action=...` used to *start* a demo. It's refused now, with a test.

**Note:** keys sent by the Chrome automation extension often don't reach the page at all (a capture listener saw nothing). Earlier "lost keys" were partly that. The queue test above uses real DOM events.

### 2026-10-10: judge tour, How it works, accessibility, A3 on main

**Judge tour (`T`, `f1c74e1`):** about 80 s.
- LIVE: a voice behind → doorbell → name called, always a labelled local replay.
- MEMORY: one Ask with its evidence.
- REPLAY LAB: the real before/after numbers.
- Back to LIVE.
- Any key or click takes over (the overlay goes, the scene stops).
- Cold run after the A3 merge with no relay and no memory service: 77 s end to end.
  - Memory answered from A3's bundled snapshots; Replay used the offline policy on A3's day ("Noisy lunch: 5 of 46 ... 46").

**How it works (`H`, `7bc0922`):** `static/architecture.js` (pure function) plus `how.js`.
- Every node is live / fallback / idle (no message for 60 s) / not connected.
- **For the submission:** `extras/dashboard/architecture.svg` + `.png` (K3 told).
  - The first standalone SVG was invalid XML (double quotes inside font attributes); fixed and validated with `xml.dom.minidom`.

**A3 on main** (`8d78be3` merged):
- `pytest extras/dashboard`: 21 passed, 0 skipped. The A3-snapshot test runs for real now.
- Checked against A3's real service on main (`python -m extras.memory`, SQLite, my relay):
  - MEMORY: A3's day, its five questions, Ask "from memory API" with 4 evidence events highlighted;
  - REPLAY: decisions from the API, 35 rows, Noisy lunch 11 → 46;
  - `/api/status` feeds a "Vultr relay" / "Server relay" row in the System menu (roles, msg/s, laptop → relay age; "laptop mirror down" on Vultr) and the relay / iPhone nodes in How it works.
- **Found:** A3's API requires the relay token on proxied requests, and the dashboard never sent one, so it would have got 401 against Vultr. Now it's a Bearer header, server side, for non-local URLs only. Replies are scrubbed of credential-like keys.

**Accessibility** (`dee86e3`, `f6e5d07`):
- **Keyboard:** 47 focusable rows on LIVE alone. Enter opens the inspector, focus moves to its close button, Esc returns focus to the row (verified with DOM events). `aria-selected` on tabs; a skip link.
- **Focus:** rows use an inset ring, because the rail's scroll box clipped outside rings.
  - In the automation window `:focus` never matches (the window isn't focused); the ring was checked visually with the same rule on a test class.
- **Reduced motion:** the orb holds still.
- **Contrast:** `--text-3` 3.2 → 5.3:1; red / green / amber / blue text inks ≥ 5:1; ended alerts no longer faded to < 3:1.
- **Size:** captions scale to ~26-31 px on a 1920 px monitor.

### 2026-10-10: Gate 2 bugs: retracted captions, jittery doorbell (`3b02851`)

Both bugs come from K1's real-array run, and both are verified by replaying K1's own logged messages (`shh-k1/logs/laptop.jsonl`) through a local relay.

**Retractions:** 57 in that log. A1 sends an empty final (no text, no pending) for noise or the wearer's own voice.
- The page now drops the caption card and the stream row, and gives back a Voice N used only by that utterance.
- MEMORY and the offline replay ignore it.
- The inspector keeps the messages and says "Retracted".

**Doorbell `s47475-19`:** 9 updates on one `event_id`, bearings 28.8 / 27.8 / 0.8 / 24.8 / 24.8 / 24.8 / 0.8 / 24.8 / 24.8.
- Updates were already in place (same `event_id` and `alert_id`); only the direction jumped.
- The display now uses the circular median of the last 7 reports, so the marker, label, alert card and headline sit at +25°.
- The inspector lists every raw report ("steadied over 7 reports").

**Tests:** `tests/js/ingest.test.mjs` (`node:test` on the real `static/core.js`, run from pytest).

**Lesson:** `node --check file.js` parses CommonJS. A misplaced `//` in `memory.js` broke the page in the browser and passed that check. A new test parses every page module as `.mjs`. The break was caught in Chrome before commit.

### 2026-10-10: iPhone (PR #2) shown truthfully; real-array latency reference (`f5fea42`)

**Merged main with Kori's iOS PR #2.** No contract change.

What the phone sends (`ios/HERE/App/AppModel.swift`):
- `hello`, `config`, `heartbeat` (status, `thermal`, camera `detail` when degraded), `clock_ping`;
- with Settings › Send scene: `scene` (heading + labelled people, `speaking`) and `attribution` on each non-empty final.
- Identity, objects and fusion are on-device only.

The dashboard now shows exactly that:
- **Names:** attribution, else the person the phone sees speaking in that direction (`scene`), else Voice N. Chips say iPhone / iPhone · in view / off camera · iPhone.
- **Orb:** people markers in the view cone.
- **How it works and the System menu:** phone faces, heading and thermal (or degraded + detail, or "scene off").

`extras/dashboard/fake_phone.py`: a stand-in for the app. Verified in Chrome with `fake_events` + `fake_phone`:
- Person 1 / Person 2 captions, "Person 1 is speaking front-right" while still speaking;
- iPhone node "2 faces · heading 5° · thermal fair".

**Latency panel:** "Direction on the real array: 31 ms p50 / 39 ms p95 (n 292) vs ≤ 250 ms".
- These are K1's numbers from `origin/kori/k1-respeaker` WORKLOG_K1 "Real-array latency" (not yet on main), labelled with their source in `settings.PAGE.latency_reference`.

**Judge tour re-run after the merge:** 78 s, complete.

## Next
- [ ] Frame rate on the demo monitor with the orb in the foreground: the automation tab can't measure it (Chrome runs no rAF and throttles timers there).
- [ ] After A3 merges: run `python -m pytest extras/dashboard`; the skipped A3-snapshot test then runs for real.
- FYI (not mine): `laptop/tests/test_transcribers.py::test_network_drop_mid_utterance_hands_the_whole_utterance_to_whisper` failed once in the full suite after the K1 merge; it passes 3/3 alone (timing-sensitive). Told the supervisor.

## Interfaces I need from others
- **A3:** nothing blocking: their API and snapshots are handled as they are on `origin/adil/a3-cloud`. FYI: Crowd shield never changes anything under their policy, and Focus changes nothing at lunch.
- **K1:** nothing: `--scene` and `fixtures/events` work as-is once merged.
- **No contract change needed.**
