## 2026-10-11: the 6-motor belt the team built, K1's proposals merged

- Belt: `belt/firmware/here_belt_6motor` (6 motors, Nordic-UART-style UUIDs, `[motor, strength, 10 ms]`, group
  `[FE, mask, strength, 10 ms]`, stop `[FF 0 0]`) compiles for ESP32 Dev Module (core 3.3.12). Measured motor layout:
  0 back-right 150, 1 left 270, 2 front-right 30, 3 back-left 210, 4 front-left 330, 5 right 90 (deg clockwise).
- iOS: the app now drives that belt. `BeltSixMotor` turns each pattern into timed writes toward the exact relative
  bearing (straight ahead / behind buzz both neighbours); alerts (3 pulses), name called (2 long), emergency (whole
  belt + direction x5), speech (one short), test (clockwise sweep). Lower priority can't cut off a playing alert.
  Saved old-firmware UUIDs resolve to the new belt. 6 new XCTests (not yet compiled: needs the Mac).
- `belt/controller/index.html`: a Web Bluetooth controller (Chrome/Edge, localhost) for bench tests.
- Merged K1's ship list (a first words, b+b2+b3 early enrolled doorbell, c lookback 1.5 / music no longer vetoes /
  name-echo guard) and the name-bearing golden check; `audio.level_speech` stays off. Gate 1594 passed, golden 7/7.

## 2026-10-11 (direction audit): one frame per path, replays in their own calibration, 4-point calibration check

- Audit after the recalibration (`array_clockwise` true -> false, left/right had been swapped). Every path from the
  array to a world bearing uses the SAME chain: device DoA = `to_world(DOA_VALUE)`; beams and SRP-PHAT (sounds and
  fixed-beam speech) = `to_world(aec_to_device(az))`, with `array_offset_deg` / `array_clockwise` applied once in
  `xvf3800.to_world`. SRP has no handedness of its own, so flipping `array_clockwise` flips every path together; the
  only ways two paths can disagree are `aec_to_device_sign` and `raw_mic_order` (mirror). Both were checked live:
  `logs/lr_check.log` (2026-10-10, `localize --check`): SRP with the fitted order matches the device DoA (6 deg
  median) and its mirror misses by 121 deg.
- Fixed: fixture replays mixed two calibrations. `FixturePlayer` took `array_offset_deg/clockwise` from the
  recording's meta.json but `aec_to_device_*` from the live config, so every replayed raw-mic bearing shifted by the
  difference (2.9 deg on `rot_*` / `raw_speaker_180` after tonight's recalibration) and would mirror outright if the
  fitted sign changed. `localize.aec_calibration(meta)`; `RawLocator(aec=...)`; the same for `odas_wsl`, `odas.frame_to_sources`,
  `tools/doa_compare.py` and `tools/make_raw_scene.py` (offline runs only; live paths unchanged).
- `python -m laptop.audio.calibrate --verify`: 4-point check of the saved calibration (front 0, right +90, behind 180,
  left -90), per-position error, flags swapped sides. `--four-point`: calibrate, then check behind + left before
  saving. Every run now warns when the fit is mirrored against the mic geometry (`handedness_problem`: the old
  `true` / sign 1 would have been flagged) and switches fixed beams off first (a killed `--real` run left them on,
  and then DOA_VALUE names a beam, not the talker).
- `laptop/tests/test_direction_frames.py` (74 cases): a source at 0 / +90 / -90 / 180 (+ diagonals) through the device
  DoA, beams, fixed-beam steering, sound SRP, speech SRP and replay, for `array_clockwise` false and true; the live
  config's handedness; the side mix-up a two-point fit can't see; world -> belt motor.
## 2026-10-10 (live test 2): whisper back, team names spelled right, Jennah is the default wearer

- Live with real people, Scribe over the phone hotspot gave first words in 1.4 s median (p90 2.1 s) and fell back to
  whisper mid-session; GPU whisper is 0.39 s. The demo runs whisper (`speech.engine` default; Scribe stays the
  opt-in `HERE_SPEECH_ENGINE=scribe_realtime`). 54% of finals were empty (room babble, talkers off the 0/180 beams).
- `speech.spellings` respells the team's names in captions (Corey -> Kori, Trey -> Tre, Jenna -> Jennah, Adele ->
  Adil), whole words only. Putting all the names in whisper's prompt was tried first and wrote
  "Jena, Kori, Tre, Adil, Here." back on noise, so the prompt keeps only the wearer's name.
- `names.user_name: Jennah` (aliases Jenna, Jena): the demo wearer. A phone `config` with an empty user name now
  keeps this default instead of clearing it (the phone sent `user ''`, so name called never fired live).
- `end_silence_s` stays 0.6: 0.45 and 0.5 gave golden 5/7 and 4/7 (split utterances, babble captioned).
  Golden 7/7 with these changes.
## 2026-10-10 (dashboard): collapsible captions sidebar

- Live: `B` or the toggle at the stage's top right hides / shows the captions sidebar (remembered per browser); the orb
  takes the room and its labels re-place. While hidden, the toggle shows a red dot for a new alert, else a blue dot for a
  new caption (new = a row key not seen when it was hidden; re-renders and the history loaded with the page don't count).
  The panels fade out / in around the slide so text never reflows mid-animation; below 900 px the stacked sidebar just hides.
- With the sidebar hidden the orb is centred (it sat ~140 px right: the headline offset is 11 % of the now-wider stage);
  it eases between the two positions with the slide (snaps under reduced motion).
- Fixed: the orb blinked out while the sidebar slid. Each resize step cleared the WebGL canvas and the redraw waited for the
  next animation frame; `resize()` now draws immediately.

## 2026-10-10 (dashboard): colour themes, light / dark / system

- Header **Colours** button (`C`; `D` cycles the mode): Light / Dark / System (follows the OS live) and five themes,
  Mono (the original look, default), Ocean, Forest, Sunset, High contrast (AAA text), each light and dark. Remembered
  per browser, applied before first paint (no flash). The orb, the memory timeline and How it works recolour in place.
- Every colour is now a token in `extras/dashboard/static/themes.css`; `theme.js` resolves them for scripts (canvas,
  three.js, SVG). The semantic colours (speech / sound / unknown / alert, live / fallback / off) keep their meaning in
  every theme; the tinted light themes use a deeper amber so a sound mark still reads. The architecture download keeps
  the print palette.
- `extras/dashboard/tests/test_themes.py`: WCAG contrast for all 10 theme × mode pairs, dark blocks override their
  light blocks, every theme has a swatch, no colour literal outside the token files. Mono light is unchanged (computed
  colours of the chrome compared against the previous build).
- Fixed while in there: orb labels no longer cover the Moment panel or the Sound → wearer card (they move to the
  nearest clear spot; 0 overlaps in 130 samples with Moment on and off); the Moment panel fits a 7-step moment at a
  630 px window (one-line step labels at short heights; the bottom fades and scrolls only when steps are really cut).

## 2026-10-10 (dashboard): Moment view replaces Presenter mode

- Presenter mode only made the text bigger. `F` (and the header's **Moment** button) now opens the **Moment view** on
  Live: the latest important moment taken apart step by step (voice/sound began → detected → first words → final →
  alert → attention → Gemini context → memory), each with its time from the onset and the provider that did it.
  Times are message arrival on the demo laptop's clock (the latency panel's basis); phone and belt lines say what the
  phone is told, never measured; scripted and replayed moments say so (a replay shows no times).
- Choosing the moment: alerts/hazards > important speech > ordinary speech/sounds; equal rank waits for the shown
  moment to finish and be readable 2.5 s (6 s if unfinished); anything after `PAGE.moment_hold_s` (8 s); a promotion
  (voice → name called) restarts the hold. Pure logic in `extras/dashboard/static/moment.js`, node tests in
  `tests/js/moment.test.mjs` (run by `test_page_logic_in_node`).
- Kept from Presenter mode: the alert-flicker fix and the **Sound → wearer** latency card, now always on Live.
- Also: the Alerts panel's attention chip moved under the title (a narrow rail squeezed "Name called" into a column).

## 2026-10-10 (A1): flaky whisper-fallback test, root cause

- `test_mid_utterance_failure_keeps_the_utterance_on_whisper` failed about once per low-memory full run: `FallbackTranscriber`
  times every decode, and a thread stalled > 1.5 s (its default `max_latency_s`) counted the instant fake Scribe as slow,
  so a partial switched to whisper early. Tests not about slowness now pass `max_latency_s=SLOW` (30 s); the slowness
  test keeps 0.1 s. Reproduced deterministically (every decode made to "take" 2 s): fails at 1.5 s, passes at 30 s.

## 2026-10-10: JEV-9B measured on the demo GPU; DeBERTa is the demo's attention tier

- JEV-9B (nf4) now loads on Windows: peft 0.21 builds the adapter's hub path with `os.path.join` (a backslash) and
  couldn't find `adapter/adapter_model.safetensors` even when cached; `JevDecider._load` loads it from the local snapshot.
- `--bench` on the RTX 5000 Ada: load 62 s, 5.7 GB (7.7 GB peak), median 496 / worst 970 ms: **gate 3 fails**
  (<= 250 / 600 ms); agreement 14/30 (obvious 8/13) vs DeBERTa 15/30 (obvious 10/13). Per ATTENTION.md, `enrich.tiers`
  is now `[deberta]`; JEV is an opt-in line in `laptop/config.yaml`. The slow path is Qwen3.5's gated delta rule
  without `flash-linear-attention`, whose current release needs a newer Triton than `triton-windows` for torch 2.5.1
  (tried, `Autotuner(do_bench=...)` TypeError, uninstalled).

## 2026-10-10 (Adil, dashboard backend): Ask HERE live during the demo, Run demo can't reach the phone, Gemini quota pauses

- **Ask HERE fell back to SEEDED during every live demo:** the memory API took 5-6 s (Neon round trips + Gemini) and
  the dashboard waits 4 s; its cache was keyed on the event count, which live ingest changed every few seconds.
  Now: cache per question for 30 s, one store call per session (round trips 7 -> 2-3), session list reused 5 s,
  Gemini phrasing without thinking. Uncached on Neon: professor 1.09 -> 0.30 s, name called 7.58 -> 0.54 s, with
  Gemini ~1 s (was 4 s); through the dashboard with live ingest the demo questions answer from the API in 0.01-0.7 s.
- **Gemini quota:** a 429 (or a key error) pauses Gemini for the server's `retryDelay` (capped 10 min) on the laptop
  and in memory, instead of a doomed call per event/question. The laptop heartbeat `detail` says "Gemini paused: quota
  exhausted (retry in N min)" without degrading; memory `/api/health` has `gemini: {state, reason, retry_in_s}`.
- **Run demo / S with a live laptop** now plays as a local replay on the dashboard only (it used to start
  `fake_events` on the live relay: scripted captions and buzzes on the wearer's phone). `force_relay=1` on purpose.
- **Dashboard memory client:** a timeout is `_fallback_reason: "memory API slow (>4 s)"` on the seed reply and no
  longer marks the API down for 10 s (a second timeout in a row does).
- `fake_phone` reads relay / session / token from `laptop/.env` like every tool (was HTTP 401 under `demo.py`).
- The node page test passes `--experimental-detect-module` on node 20/21 (it failed on this laptop's node 20).
## 2026-10-10: dashboard presenter mode, readable timeline, alerts stop flickering

- **Presenter mode** (`F`, or the header's **Presenter** button; per browser, applied before first paint): bigger
  headline, captions, alerts and 3D labels for a monitor seen from a few metres; the event stream, latency grid and
  orbit hint are hidden; alerts stay on screen. A **"Sound → wearer"** card shows the direction latency, labelled
  **live** only with a fresh laptop heartbeat, otherwise K1's **measured** real-array number (never passed off as live).
- **Bug:** every alert row replayed its entrance animation once a second (the panel is re-rendered by the 1 s timer),
  so an alert flickered on the judges' screen. The panel is rewritten only when it changes; rows already shown don't animate.
- **Memory timeline:** place headers use the longest wording that fits ("Commute: University Ave crosswalk" →
  "Commute" → "Com…", measured with the page font) instead of being clipped mid-word; the full text is in the hover title.
- **Ask HERE** says when an answer is a seeded fallback and why (`_fallback_reason`), and when Gemini phrased it.
- **Test fix:** `extras/dashboard/static/package.json` (`"type": "module"`): node 20 loaded `static/core.js` as CommonJS, so
  `test_page_logic_in_node` failed on `main` wherever node 20 is installed.

## 2026-10-10: attention tiers installable on the demo laptop

- `laptop/requirements.txt` adds `transformers==5.18.0` (pinned: 5.19 breaks on import with torch 2.5.1, < 5 downgrades
  hub/tokenizers under faster-whisper), `peft`, `bitsandbytes`, `accelerate`. Installed in the demo venv: torch, hub,
  tokenizers unchanged, faster-whisper still imports. DeBERTa (tier 2) can load; JEV-9B (tier 1) is downloading for the
  ATTENTION.md gates (`python -m laptop.enrich.attention --bench`). `psycopg[binary]` installed for Neon (extras/memory).

## 2026-10-10 (live test): more sounds reach the phone, Scribe A/B switch

- First live run through the Mac relay: doors and knocks never showed. `sounds.publish` was only
  `[doorbell, baby_crying]`; it now also has door, knock, alarm, siren, car_horn, dog_barking, phone_ringing,
  loud_bang (ambience such as music, laughter, tv, vehicle stays unpublished). door / knock thresholds 0.2 (PANNs scores
  short impacts low, like the doorbell).
- More honest unknowns: 195 of ~235 onsets in 20 min were merged or dropped. `unknown.min_energy` 0.25 -> 0.18,
  `min_active_s` 0.15 -> 0.1, `cooldown_s` 6 -> 2.5, `ambient_n` 3 -> 4.
- `HERE_SPEECH_ENGINE` (env) sets `--engine`, so `demo.py` / `connect_to_mac_relay.ps1` can run Scribe live without
  changing the default. Default stays faster_whisper: golden with Scribe was 6/7 (two_talkers WER 0.13).
  Golden with whisper is flaky between runs (6/7 on unchanged main, 7/7 with this change).

## 2026-10-10: relay on the Mac (`relay/MAC_RELAY.md`)

- The phone can't connect in to the Windows demo laptop (a managed machine; its security agent blocks inbound, on
  UCF_Guest and on a hotspot alike). The relay moves to the Mac: the laptop publishes out to it with
  `HERE_RELAY_URL=ws://<mac-ip>:8080/ws` + `tools/demo.py up --source real --cloud`, the phone connects to the same URL.
  Handoff with steps, checks and troubleshooting in `relay/MAC_RELAY.md`; `IOS_INTEGRATION.md` row. No code change.

## 2026-10-10 (A2): sound_event.source_id names the track the sound came from

- `laptop/sounds/classifier.py` fills the optional `sound_event.source_id` (already in v3, no contract change): the
  onset's stream when an onset triggered the window, the pipeline's beam with several streams (fixed beams: 0 front,
  1 rear), kept on later updates of the same event and across an unknown -> label upgrade. A single-stream
  sliding-window detection with no onset omits it. Matches the scripted scenes (`doorbell_rear`, `unknown_sound`:
  `source_id` 1). The alert still links via `ref.event_id`. Tests in `laptop/tests/test_sound_ai.py`.

## 2026-10-10 (iOS): two relays, one stream

- Settings > Relay has a primary URL (the laptop's local relay, `ws://<laptop-ip>:8080/ws`) and an optional
  fallback (Vultr, `wss://<host>/ws`), same token and session; the fallback persists with the other settings.
- `RelayClient` connects to the primary; if it isn't up within `Tunables.relayPrimaryConnectS` (2 s) or drops, it
  switches to the fallback and probes the primary every `relayPrimaryRetryS` (5 s), switching back when a probe
  connects. One connection at a time, no dedupe; `seq` gaps are never treated as loss (nothing reads `seq`).
  Probe and deadline timers are generation-checked and stop on `connect()`, `disconnect()` and a 401/403.
- The status pill says "cloud" while on the fallback; the debug overlay and Settings show primary / fallback.
  Decisions in the pure `RelayFailover` (`HERETests/RelayFailoverTests`). Not yet built or run on the Mac.

## 2026-10-10 (docs): IOS_INTEGRATION.md checked against the iOS code

- Each Open row read against `ios/HERE`: six moved to Done (code read, not yet on a phone), `unknown` sound marked
  partial (it can be suppressed, not drawn), four rows marked in progress. New "On-device acceptance" table: the
  three core moments plus belt-only, with the real-array command and the fake_events scene for each. No code change.

## 2026-10-10 (iOS): presence vs the real array, perception_enrichment wired

- `"sources": []` (nothing live, the 1 s keepalive) no longer feeds `RoomBaseline` as level 0: the baseline holds
  (`RoomBaseline.hold`), so a quiet stretch in a loud room doesn't make the next sound look fully prominent.
- A sound's level comes from its own `sources` track when `sound_event.source_id` names a live one, else from a
  source near its bearing; if nothing matches (fixed beams at 0 / 180, raw-mic bearings at 25 / 90) it uses the
  classifier fallback, never 0. `SoundTag.sourceId`, `AlertState.refEventId`.
- `perception_enrichment` sets prominence for the caption / sound / alert it refers to: ambient = minimal (opacity
  cap, lowest rank), notice = small indicator (shown even below the relevance threshold), important = full card +
  one belt buzz, urgent = interrupt (top of the stack, pulses, through focus mode, emergency buzz). Alerts, the
  wearer's name, danger sounds and captions are only ever raised; `directed_at_user` >= 0.8 raises speech.
  Without an enrichment nothing changes. Numbers in `Tunables.swift`; 5 new `PresentationTests`. Not yet built on a Mac.

## 2026-10-10 (iOS): spread-aware attribution

- `Attributor` now uses the utterance's `bearing_spread_deg`: the match window is
  max(matchDeg / matchDegEstablished, min(spread, `Tunables.matchDegSpreadMax` = 40)). A fixed-beam ReSpeaker that only
  knows "front" (bearing ~0, spread ~30) now puts a lone talker 25 deg off-centre on their face instead of off-camera.
- Several faces inside a spread-widened window: established audio-source owner, then the most recent speaker, else
  the nearest with a tentative score (`Tunables.tentativeAttributionScore`). The developer overlay's ATTRIB line
  says when the window was widened. A spread <= matchDeg behaves exactly as before; `speech_start` carries no spread,
  so the provisional onset decision is unchanged and the first utterance re-decides with the spread. No contract change.

## 2026-10-10 (K1): speech bearings in fixed-beam mode from the raw mics

- **Problem:** with `fixed_beams: [0, 180]` every caption's `bearing_deg` was the beam's (0.8 / -179.2) with
  `bearing_spread_deg: 0.0`, so two people in front at +-30 both missed the phone's 15 deg face match and the spread
  claimed a precision it didn't have.
- **Now:** `localize.SpeechBearings` (shared by `ReSpeaker` and `FixturePlayer` as `locate_speech`, the same
  one-bearing-per-event idea as `EventBearings`): SRP-PHAT on the newest 1 s of the utterance's raw mics, only peaks
  in that beam's half of the room (+-100 deg around it), at most every 0.3 s, coherence >= 0.6; the utterance's
  bearing = circular median of its looks (no jitter between partials), spread = circular std of the looks (stray
  looks ignored unless >= 20% of them), floor 8 deg, 15 with one look. speech_start uses the same look. No
  confident look (or no raw mics) -> the beam's bearing with `audio.fixed_beam_spread_deg` (30), never 0. Spread
  > 25 sets `overlap`. Tunables `audio.speech_srp`; `speech.*` unchanged; no contract change.
- **Measured** (replays through the real pipeline, whisper + PANNs, same captions before/after; truths are the
  loudspeaker's nominal position, which SRP and the device DoA put at ~174-178 for "180"):

  | fixture (captions) | before: error med / worst, spread | after: error med / worst, spread |
  |---|---|---|
  | raw_speaker_180, real array, rear (7) | 0.8 / 0.8, 0.0 | 5.2 / 7.2, 8 |
  | k1_room_live2, real noisy room, laptop front-right (3 front captions; proxy truth 24 = the doorbell's SRP) | 23.2 / 23.2, 0.0 | 0.8 / 0.8, 8 |
  | rear-beam leak copies of front lines (k1_room_live1/2, raw_speaker_180: 4) | wrong side, 0.0 | wrong side (beam's -179.2 / 0.8), **30** |
  | beams_two_talkers_180 (rotation scene + recorded beams, overlapping front + rear, 7) | 0.8 / 0.8, 0.0 | 3.2 / 9.2, 8 |
  | beams_single_0 / beams_single_180 (13) | 0.8 / 0.8, 0.0 | 5.2-6.7 / 9.2, 8 |
  | offline, rotation scenes at +-90 (14 lines) | 89-91, 0.0 | 6.2 / 9.2 |
  | offline, all rotation scenes + raw_speaker_180 (56 lines) | 0.8 / 90.8 (68% within 15) | 6.2 / 73.8 (96% within 15) |

  speech_start: 4-5 deg median, worst 11. Partials stay within 6 deg of the final. The 73.8 worst: two talkers in the
  SAME half at once (rot_two_talkers_90, front 0 + side 90): the louder wins, spread 37-44 -> `overlap: true`.
- **Limits (said plainly):** truths for off-axis talkers are only +-90 (mic-permutation rotations) and one
  front-right laptop speaker whose truth is itself an SRP estimate; no real people at +-30 yet. The laptop's
  fan/other coherent noise reads 0.3-0.55 coherence in quiet gaps (0.7-0.8 when co-located with the speaker); a
  look needs 0.6. The raw-mic order's x-mirror is still unconfirmed (SPIKE.md): if it's mirrored, left/right
  bearings flip sign (front/back is unaffected). A leak caption on the wrong beam still gets the wrong side.
- Golden 7/7 (no raw mics there: fallback path); `laptop/tests/test_speech_bearing.py`.

## 2026-10-10 (docs pass): README and briefs match the overnight code

- README §3, §10, §17-§18, §22, §23, §27, root CLAUDE.md, relay/laptop/tools briefs, laptop/.env.example: local relay
  primary + Vultr mirror, `tools/deploy_vultr.py`, `/api/status`, export bundles, measured Scribe numbers, the DeBERTa
  tier, `extras` in the test gate. No code change.

## 2026-10-10 (K2): the iPhone on the dashboard, real-array latency reference

- After iOS PR #2: talkers are named from the phone (attribution, else who its camera sees speaking), people
  appear in the wearer's view cone, and How it works / the System menu show the phone's faces, heading and
  thermal. `extras/dashboard/fake_phone.py` stands in for the app. No contract change.
- Latency panel: K1's measured real-array direction latency (31 / 39 ms vs <= 250 ms), labelled with its source.
- Retracted captions no longer show as empty cards; a jittery sound's direction is steadied on screen.

## 2026-10-10 (review, round 2): .env parsing and deploy idempotence

- The three `.env` readers (laptop, relay, memory) keep `#` inside quoted values, and the relay's now strips
  trailing comments like the others: a server `.env` copied from `relay/.env.example` used to bind to
  `127.0.0.1       # Caddy terminates ...`. `laptop/tests/test_dotenv.py` covers all three.
- `tools/deploy_vultr.py update` without `--memory` (the form in ADIL_TODO) also installs requirements for and
  restarts a memory service deployed earlier; re-running `deploy --memory` from a checkout with no local
  `NEON_DATABASE_URL` keeps the server's instead of silently moving memory to SQLite.
- Reviewed, no bug found: fanout (one seq per message on every link, pong on its own link, config from either),
  `/api/status` (no secrets in `fallback_reason`; relay log fields match), the Scribe `send()` change. The
  intermittent `PytestUnhandledThreadExceptionWarning` didn't reproduce in 4 full gates + 3 targeted loops.
## 2026-10-10 (A2, overnight): attention tier 2 measured, demo set on CPU

- WORKLOG_A2: DeBERTa tier 2 on CPU, median 447 ms / worst 797 ms per decision, 15/30 agreement (obvious 10/13);
  unsure answers fall through to the rules. Demo set replayed with PANNs + enrolled doorbell: doorbell behind
  (172.6) with alert, identical with enrichment off; `rot_*` fixtures carry no directions without ODAS.
- TASKS.md A2: B (wrapper), C (unknown policy), D (Gemini), E (attention tiers), F (delivery) ticked; the bakeoff,
  publishing the new labels and the bart/ModernBERT comparison stay open (Kori's laptop).

## 2026-10-10 (K1): noisy-room diagnosis, fixed-beam bearings, `stress_test --room`

- A fixed beam's stream keeps its beam's bearing (`audio.fixed_beam_spread_deg`, 30) when the firmware flags no
  speech during an utterance (quiet talker in a loud room): `name_called` from behind had `bearing_deg: None`.
  `IOS_INTEGRATION.md` row.
- `tools/stress_test.py --room <beams fixture>`: real two-beam room audio (and its array readings) mixed into a
  two-beam scene per beam at `--snr`; `--no-empty`. Findings (music veto, `array_lookback_s`, the firmware's speech
  flag vs `endpoint_use_doa`): `HERE_36hr_vibecode_plan/WORKLOG_K1.md` "F. Noisy-room".

## 2026-10-10 (K2): judge tour, How it works, accessibility

- `T` plays an ~80 s guided judge tour (live → memory → replay lab) as a labelled local replay; any key takes over.
- "How it works" view: the sponsor architecture with every node lit live / fallback / idle / not connected (A3's
  `/api/status` adds the relay's clients and rates). Static `extras/dashboard/architecture.svg` / `.png` for the
  submission.
- Accessibility: keyboard everywhere, visible focus, reduced motion, AA contrast, captions sized for 2 m.
- The dashboard sends the relay token to a proxied (Vultr) memory API, server side only. No contract change.

## 2026-10-10 (A1): Scribe drop reason is deterministic; live Scribe numbers

- `ScribeSession.send()` on a socket the server closed before the reader noticed now raises
  `ConnectionError(<reader's reason> or "connection closed (...)")` instead of websockets' raw
  `received 1000 (OK)...`, so `heartbeat.detail` always names the outage. Fixes the 1-in-15 flaky
  `test_network_drop_mid_utterance...` (K3's root cause; 300/300 after, 21/300 failing before).
- WORKLOG_A1: Scribe Realtime on the live API (`array_tts`): first words 1.24 s, final 0.82 s, WER 0.04; unplug drill
  switches to whisper at 12.4 s and back at 22.5 s with 0 duplicate finals.

## 2026-10-10 (A3): extra Vultr roles (TASKS A3 H)

- `/api/status` + `/api/status.html` on the memory service: relay `/health`, per-type message rates and
  laptop -> relay ages (p50/p95) from the relay log's tail, memory database, which insights are served. relay.py is
  unchanged. `tools/deploy_vultr.py` points it at `/var/log/here/relay.jsonl`; `status` prints it.
- Session export bundles (`python -m extras.cloud.export_bundle [--session S] [--upload]`): events + insights +
  manifest (sha256, `audio: false`), local always, Vultr Object Storage when `VULTR_S3_*` are set (stdlib SigV4,
  no new dependency). Setup step in ADIL_TODO.md. Nothing here is on the live path.

## 2026-10-10 (Adil): one-command Vultr deploy

- `tools/deploy_vultr.py --ip <IPv4> [--memory] [--save-token] [--dry-run]`: relay/DEPLOY.md steps 3-8 over ssh,
  re-runnable; `update` and `status` subcommands. Secrets never on a command line or in output (the token is made on
  the server; the Neon URL goes over stdin). `--save-token` puts the token in `laptop/.env` and adds the Vultr URL to
  `HERE_RELAY_MIRRORS`. Tests: `tests/test_deploy_vultr.py` (nothing connects).

## 2026-10-10 (Adil): Vultr off the reflex path (local relay + cloud mirror)

- `laptop/client/fanout.py`: `FanoutClient` publishes every message once-numbered and identical to several relays,
  each with its own connection, backoff and bounded outbox; a dead, hanging or slow mirror never delays the primary
  (tests/test_fanout.py). Heartbeats on every link; `clock_ping` answered where it arrived; config from any link.
- `relay.mirrors` (config.yaml), `HERE_RELAY_MIRRORS`, `python -m laptop --mirror URL`, `fake_events.py --mirror`.
  `tools/demo.py up` keeps the local relay as primary and mirrors to `laptop/.env`'s `HERE_RELAY_URL` when it's a
  `wss://` cloud relay (`--no-mirror` to skip; `--cloud` unchanged).
- `laptop/settings.py`: `.env` trailing comments are stripped (the `.env.example` lines carry them).
- README §7, `laptop/.env.example`, `IOS_INTEGRATION.md` row (primary + fallback relay URL on the phone). No contract
  change. Audio, speech and sounds untouched; K3: run golden once anyway.

## 2026-10-10 (K2): dashboard survives the venue Wi-Fi; every provider named

- No relay reachable: Run demo plays K1's `fixtures/events` scenes from the dashboard itself, labelled "Local
  replay ... Not live"; a relay drop mid-demo carries on locally.
- The inspector names the provider of every stage for every event (ReSpeaker, ElevenLabs / whisper, Hugging Face /
  PANNs / enrolled, attention model / rules, Gemini, iPhone, Vultr / local relay, Neon / SQLite / seed, Databricks /
  precomputed): named, "fallback" with what is missing, or "not connected". Never blank.
- Judge audit fixes: what HERE surfaced is explicit (`important → card + belt`); a stale alert no longer hides
  newer speech; the ReSpeaker is never credited without a laptop heartbeat. No contract change.

## 2026-10-10 (K1): event engine: source tracker, onset candidates, stage timings

- `laptop/events/tracker.py`: the wire `sources` list only live tracks (stable ids, snap on big moves, 1.5 s hold
  then expiry, empty list when nothing's heard, leak suppression between the fixed beams); per-beam energy.
- `laptop/events/onsets.py`: onset candidates per audio stream for A2 (`app.onsets.subscribe`); one per doorbell
  press, none for steady fan noise.
- `laptop/events/stats.py`: capture / doa / serialize / socket_send timings in `logs/laptop.jsonl` (`_stage`,
  `_dropped`) and the `--dashboard` (plus dropped, suppressed, queue depth, onsets, tracks).

## 2026-10-10 (K1): scripted event scenes

- `fixtures/events/*.jsonl`: seven canonical-message scenes (front/rear speech, doorbell and name called from the
  rear, unknown sound, two speakers, crowd noise) in the real array's beams-mode shape, for the phone, dashboard and
  cloud to build against. `tools/fake_events.py --scene <name>|all [--loop]` plays them through the relay;
  `--build-scenes` and `--from-log` make them. `IOS_INTEGRATION.md` row.

## 2026-10-10 (K2): dashboard ready for A3's memory and K1's scenes

- Memory: one seed bundle (A3's `extras/memory/seed/api/` when merged, else the dashboard's), never mixed, so
  evidence and insights always point at the same day. A3's API and snapshot shapes are normalized for the page;
  checked against A3's real service. A seeded day is shown on today's date; live memory never moves.
- LIVE: talkers without a face are told apart by source id and direction (Voice 1/2/3); an idle orb sweep and
  "last heard" line; labels stay on screen; narrow screens fit. Replay lab shows important events A3 missed.
- K1's seven `fixtures/events` scenes play from the Run demo menu. No contract change.

## 2026-10-10 (night): K3 end-to-end tests and the demo harness

- `tests/test_e2e_roles.py`: the core moments through the local relay to a phone, a dashboard, a memory client and a
  foreign session at once, with `perception_enrichment` after each event. Each role gets exactly its subscription, the
  enrichment always arrives after its event, and nothing crosses sessions. Local relay p95 is 1.5 ms.
- `tools/demo.py` and `.\tasks.ps1 demo-up | demo-reset | demo-down | demo-status | demo-check`: one command for the demo
  stack, a reset between judges (6 s light, 16 s full with the scripted source), and a pre-demo check of the running
  stack (`check_gpu.py --demo`, no model loads). README §23 updated.
- `demo-check` warns, with the reason, when the memory API is not on Neon. `demo.py up --scene X` refuses a scene
  that isn't in `fixtures/events` (it used to start and report "source NOT UP").

## 2026-10-10 (A1): Scribe Realtime behind the transcriber interface

- `laptop/speech/transcriber.py`: one provider interface (`decode(audio, final, use_hint, utt_id)`, `end`, `warm`,
  `close`). `ScribeRealtimeTranscriber` speaks ElevenLabs Scribe v2 Realtime (manual commit per utterance, warm
  connection pool, only utterance audio sent); `FakeTranscriber` for tests; `FallbackTranscriber` hands the whole
  utterance to whisper on an error, a dropped connection, a missing commit or a slow answer, and probes Scribe in the
  background to switch back. Heartbeat `engine` / `detail` follow it.
- Pipeline: streaming engines get the `utt_id`; a retracted blip frees its session; punctuation-only text is empty;
  provider timings (`first_partial`, `commit`) join `stage_ms`. `--engine` override; `speech.scribe` /
  `fallback_*` tunables; `python -m laptop.speech.bench` (latency + `--unplug` failure injection on a fixture).
- Default engine is still `faster_whisper`. Not yet run against the live API (no key on Adil's machine):
  `HERE_36hr_vibecode_plan/WORKLOG_A1.md`.

## 2026-10-10 (late night): HERE World redesign: light theme, three.js orb

- The dashboard's LIVE view is now a three.js point-field orb that swells toward each sound, with beams, 3D markers,
  floor ripples, alert pillars and the wearer's view cone. Also new: a plain-language "right now" headline and a live
  provider pipeline strip. Light, Vercel-style chrome (Geist). three.js r160 and Geist are vendored for offline use.
  Memory and Replay lab are restyled to match. No contract change.

## 2026-10-10 (night): HERE World dashboard, first pass (K2)

- `python -m extras.dashboard` on `127.0.0.1:8780`: LIVE WORLD (radar with sources, sounds, `unknown` with its
  direction, alerts and the phone's wearer cone; captions, alerts, latency, event stream, health strip), an event
  inspector that names the provider of each field, MEMORY (timeline, Ask HERE, insights, missed) and REPLAY LAB
  (actual vs alternative attention policy). It joins the relay as role `dashboard`; the token stays server-side.
- A3's memory API (§5.3) with a seeded fallback: `extras/dashboard/demo_data/` (206 canonical events, validated by
  `python -m pytest extras/dashboard`). No CDN; no contract change.
## 2026-10-10 (A3, later still): Replay Lab relevance model

- Optional (`HERE_REPLAY_MODEL=1`): the zero-shot model scores utterances in the background (cached, never blocks
  replay); `/api/replay` blends it in at 30 % and reports `model_score` per decision plus a `model` status block.

## 2026-10-10 (A3, later): Neon and Databricks live

- Neon: the memory service runs on Neon (direct Postgres when port 5432 is open, Neon's SQL over HTTPS otherwise).
- Databricks Free Edition: `python -m extras.cloud.databricks_sync` exports memory, runs the `here-insights` job on
  serverless and stores 12 insights tagged `source: "databricks"`; `/api/insights` serves them, the precomputed set
  stays as the fallback. Insights are keyed by `(id, source)` and carry `data` (chart numbers).

## 2026-10-10 (A3): memory service, seeded day, Ask HERE, replay, insights

- `extras/memory/`: relay client (role `memory`) storing canonical events in Neon, local SQLite fallback (switches on
  any Neon error); batched writes from a bounded queue; final utterances indexed for full-text search; `rel_bearing`
  from the phone's `scene` heading; 7-day retention for live data.
- Memory API on :8790 (INTEGRATION_CONTRACT §5.3): `/api/health`, `/api/day`, `/api/ask`, `/api/insights`,
  `/api/replay`, `/api/segments`; `search_request` answered with `search_result` over the relay.
- SEEDED synthetic day (`extras/memory/seed/synthetic_day.jsonl`, 838 canonical v3 messages, validated) + frozen API
  answers in `seed/api/` for the dashboard's offline fallback.
- Ask HERE answers the five demo questions with evidence (rules + full-text, optional Gemini phrasing).
- Replay Lab backend (deterministic policy engine, optional zero-shot score). `extras/cloud/`: shared insight
  metrics, precomputed fallback, Databricks notebook/job/sync (not run yet).

## 2026-10-10 (night): JEV-9B as the attention model's top tier

- `autotrust/JEV-9B` (Apache-2.0, Qwen3.5-9B + a decision head, distilled to behave like TypeSafe Jev) becomes tier 1,
  4-bit on the demo laptop's GPU. Tier 2 is the DeBERTa zero-shot model; tier 3 is the rules. `JEV-27B-VL` was rejected:
  52 GB, needs an 80 GB GPU, and the laptop's RTX 5000 Ada has 16 GB. Four gates on Kori's laptop (VRAM, whisper
  unaffected, speed, calibration after quantization) decide whether the demo uses tier 1. Shape unchanged.

## 2026-10-10 (evening): attention from Hugging Face, not Jev

- TypeSafe isn't a sponsor, so Jev is dropped. A local Hugging Face zero-shot model
  (`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`, alternatives in the bakeoff) judges attention
  (ambient/notice/important/urgent) and directed-at-user, with deterministic rules as the fallback. Same
  `perception_enrichment` shape; `attention_model` is now the Hugging Face repo id or "rules". No API key, runs on
  Adil's CPU for development and the demo laptop's GPU for the demo.
- `Shared/JEV.md` replaced by `Shared/ATTENTION.md`; README §12.7/§17/§18/§19/§27, the plan, `IOS_INTEGRATION.md`,
  `PROJECT_BRIEF.md`, the contract comments and example updated.

## 2026-10-10 (later): Jev decided, `perception_enrichment` in the contract, plan for no-GPU Adil

- Research: Jev (TypeSafe, `jev-1.13`) is a hosted typed-decision API with no public weights and no Hugging Face
  model; the Hugging Face blog post about a "Jev AI API" promotes an unaffiliated site. Decision record:
  `HERE_36hr_vibecode_plan/Shared/JEV.md`. Call TypeSafe directly (sponsor key) or through OpenRouter.
- Contract: new `perception_enrichment` (`ref`, `attention` ambient/notice/important/urgent, `attention_confidence`,
  `attention_model` jev or "rules", optional `directed_at_user`, `context`, `context_model`), additive, still v3. Phone and
  memory default subscriptions and the relay updated; `IOS_INTEGRATION.md` row.
- README §17 (extras built in parallel, never on the live path), §18 (all sponsors' roles), §19 (Kori / Adil / iOS
  team), §27; laptop and audio briefs now name Kori as the ReSpeaker owner.
- Plan: Adil's machine has no NVIDIA GPU. His workers develop on CPU, K3 runs golden and `tools/sound_bench.py` on
  Kori's laptop before his merges; Jev tasks rewritten for the API.

## 2026-10-10: contract: car horn, vehicle, siren, door, unknown (additive, still v3)

- `SoundLabel` + `car_horn`, `vehicle`, `siren`, `door`, `unknown` (`unknown` = heard but not classifiable, keeps
  its direction; `other` = recognised, not ours). `AlertKind` + `car_horn` (2), `siren` (3), `door` (1).
- `sound_event` gains optional `source_id` (its sources track) and `model` (which classifier: the laptop sends
  `panns_cnn14`), for the dashboard's provider view.
- Laptop: AudioSet map for the new labels (Siren moved out of `alarm` into `siren`), display rows, non-speech
  list; "door" doesn't overrule an enrolled doorbell (AudioSet Door fires on doorbells). Not yet published.
- `contracts/CHANGES.md`: contract change log + known drift (the 36-hour plan's JSON) for the accordance pass.
- Merged `laptop/odas` and `ios/build` into `main` (one basis for both lanes); added the 36-hour plan.

## 2026-10-09 (ios build): ambient sound visualization, box-size smoothing, caption/alert color polish

Live on-device follow-up after confirming the FaceTracker fix held up (see entry below): Kori
reported the box was now stable but visibly "expanding and getting smaller," that leaving and
re-entering frame mints a new label (confirmed: intentional, README §25 -- no facial recognition,
"Person N" is per-appearance only, not a bug), and asked for (a) a real visual treatment for ambient
sounds from the laptop's audio pipeline -- "circle where we think the sound is coming from... some
type of shading" -- and (b) a general caption/UI polish pass.

- **Removed** all the temporary `print()` debug instrumentation added for live console debugging
  (FaceTracker.swift) -- that diagnosis is done, this is clean again.
- **Box-size smoothing**: `Track` now keeps a separate `smoothedBox` (exponential blend toward each
  new observation, factor 0.45) used for everything rendered (`TrackedFace.box`); `observation` itself
  stays raw so Vision's own tracking continuity isn't affected. `.accurate` tracking's box *size* in
  particular visibly breathes frame to frame even when the face isn't moving -- pure display fix, no
  tracking-logic change.
- **Ambient sound visualization** (`OverlayView.SoundGlow`, new): sound_events that aren't
  speech/alerts (music, TV, animals, ...) now render as a soft pulsing radial "shading" glow at their
  screen position when in view, replacing the old plain text pill. Off-screen, a dim edge glow
  (`EdgeGlow`, now parameterized with `width`/`peakOpacity`/`restOpacity` so alerts stay visually more
  urgent than ambient awareness) plays toward the bearing alongside the existing edge tag. New
  `AppModel.debugInjectTestSound()` + a Developer > Test triggers button so this is checkable without
  the laptop/relay.
- **Caption/alert color cohesion**: `CaptionView` and `AlertCard` both gained a soft stroke + shadow
  glow in the speaker's/alert's color around the whole glass card (previously only the small header
  dot carried color) -- ties the whole card to who/what it's about, not just one dot.
- 45/45 tests green throughout (all additions are pure UI/display, no production logic beyond the
  box-smoothing blend, which is a pure function but not yet unit-tested -- `FaceTracker` still has no
  direct test target since it needs real Vision input, consistent with the rest of this file).

## 2026-10-10 (ios, eighth pass): person recognition debug overlay, distance tiers

- Settings > Developer > **Show Person Recognition Debug** (off by default) with Face boxes, Body
  boxes, Identity scores, Speaker scores, Audio associations. `UI/PersonRecognitionOverlay.swift`
  draws each person's boxes in their identity color (dashed face box = body-held) with session id,
  track, confidences, distance tier, re-identification evidence, mouth activity, speaker lock and
  bearing error. Replaces the old "Show tracking boxes" under the debug overlay.
- Distance tiers by face height (close >= 110 px, far < 45 px): appearance similarity shifts from
  face to torso/clothing as the face gets small (`ReID.appearance`); a face detection scoring
  0.25-0.5 may continue an existing track but never starts a new person.
- `SessionPerson` keeps recent bearings and audio sources. "Lip movement as speaker evidence" toggle.
- Tests 177 -> 179.
- Not seen running: the overlay (no way to switch it on from the command line in the Simulator).

## 2026-10-10 (ios, seventh pass): per-source speaker locks, lip movement, motion prediction

Speaker attribution was decided from scratch for every sentence (nearest visible face within 15 deg),
so a bystander crossing in front of the speaker took the next caption.

- `Attribution/SpeakerLocks.swift`: one `ActiveSpeakerAssociation` per active speech source (the
  laptop's stream id plus a bearing cluster). Once a source belongs to a SessionPerson it stays theirs
  through bearing jitter (smoothed bearing), pauses (2.5 s), their face being hidden (3 s grace in
  which nobody can take it) and them leaving view. It changes hands only when a challenger beats the
  holder by 0.25 for 1 s; every lock, switch and release is logged with its scores. A new source
  still takes README §13's attribution as its first holder. Captions follow the holder by person,
  not by Vision track.
- Two sources hold two independent locks, so two people talking at once keep their own captions
  when the laptop sends two utterances.
- `MouthActivity`: lip opening from Vision face landmarks each detect cycle, scored on its variation
  over ~1 s; adds to a plausible candidate's claim, never creates one.
- `SessionPerson`: smoothed bearing velocity; re-identification expects a vanished person along their
  path for 2 s. `state` (visible / temporarilyOccluded / offscreen / dormant).
- Developer > Calibration: "Speaker locks" section (holder, state, raw/smoothed bearing, per-person
  scores, mouth activity, and the log).
- Scenarios 69-72; tests 165 -> 177 (`SpeakerLockTests`).
- Upstream fact: the laptop transcribes two streams, one per fixed beam (front half / rear half). Two
  talkers in the same half arrive as one mixed caption with `overlap`; the phone cannot split that.
- Not verified on a phone: landmark-based mouth activity (scale `mouthActivityFull` is a guess).

## 2026-10-10 (ios, sixth pass): array frame vs wearer frame, measured calibration, label confidence

Audit of the direction and audio pipeline; full write-up in `ios/AUDIO_GEOMETRY.md`. Phone-side
changes only (laptop tests could not be run on the Mac this was done on).

- **Flaw found:** laptop bearings are measured at the array; the phone used them as if measured at
  the wearer. Right only when the wearer stands at the array (README §5 says within 0.5 m; 1 m off
  with a speaker 1.5 m away is a 34 deg error).
- `Geometry/RoomGeometry.swift`: the wearer has a position relative to the array (entered once).
  Speech is attributed in the array's frame using each face's estimated distance (exact given that
  distance); other sounds are converted with an assumed source radius, with the resulting
  uncertainty discounting sound-to-object attachment. Wearer at 0 m (default) = unchanged.
- `CalibrationCheck`: measured agreement per sentence from a visible speaker (or a helper on a
  marked spot): mean/max error, jitter, GOOD/FAIR/BAD, and a diagnosis (constant offset, reversed by
  180, mirrored, changes with direction, noisy), plus a one-tap heading correction.
- Sound labels: under-bar labels are shown as unknown; one continuing source keeps one
  notification across label changes; its label is decided by accumulated evidence.
- Settings > Developer > Calibration & audio check: procedure, wearer position, the check, every
  stage of each source's direction, a transcript record, and what the laptop said per sound.
- Tests 147 -> 165 (`RoomGeometryTests`).
- Not done: wearer position tracking (ARKit), any laptop change, any measurement on hardware.

## 2026-10-10 (ios, fifth pass): sounding things glow

A visible object that a sound is confidently attributed to now glows, like a speaking person does.

- `ObjectAura` in `SpatialScene`, built by `PresenceEngine` from every notification resolved onto a
  visible object; drawn by `ObjectAuraView` (blurred ellipse from the object's box, never the box).
  Color = the notification's color (an unnamed sound with a known source glows green); strength = the
  sound's smoothed intensity; lifetime = the notification's; warnings x1.25; more than three glowing
  things dims the ordinary ones.
- Gate: fusion score >= `auraAttachConfidence` (0.55) and the object in view and recently seen.
  Otherwise the sound stays a top-right notification. Hidden mid-sound: back to the top-right, then
  the same SceneObject glows again.
- The on-object icon sits above a boxed object; the label no longer repeats the object's name.
- Scene Understanding lists each active aura (entity, sound, confidence, intensity, color, opacity, state).
- Scenarios 63-68; tests for glow logic in `SceneObjectTests`. Still: `ios/screenshots/24_*`.
- Limit: nothing HERE runs can *see* a wall-mounted alarm (not in the detector's or classifier's
  labels), so the alarm glow only happens in the scripted scenario.

## 2026-10-10 (ios, fourth pass): spatial-first fusion, 155-label vocabulary, steadier identities

- **Fusion** (`Fusion` in `Scene/SceneObjects.swift`, replaces `ObjectSoundFusion`): a sound attaches to
  whichever known object occupies its direction; what kind of sound it is no longer has to fit the
  object (an unknown sound at the door's bearing reads "Sound · Door"). Kind-of-sound and "already owned
  this source" only break ties; several things in one direction with nothing to set one apart stay
  unattached; a person in view at that bearing is a competing candidate.
- **Vocabulary**: `ObjectClass` is now any of 155 everyday-object labels from Apple's classifier
  (verified against `supportedIdentifiers()`), folded to 138 classes. Still no added model.
- **People**: two-stage matching (continuity, then re-identification); an ambiguous new track is left
  unlabelled for up to 2 s and then takes the better existing identity instead of becoming Person N+1;
  descriptors are only banked from good views; `ReID.shouldSwitch` hysteresis margin.
- Scene Understanding overlay now also lists PERSON / AUDIO lines with what each sound is attributed to.
- Scenarios 58-62; tests 122 -> 126.

## 2026-10-10 (ios, later still): quiet objects -- SceneObject, on-device detection, sound fusion

HERE now knows about silent, stationary things. Apple Vision only, no added model, no contract change.

- `Vision/ObjectScanner.swift`: about once a second, `VNClassifyImageRequest` on 5 overlapping vertical
  strips plus up to 3 `VNGenerateObjectnessBasedSaliencyImageRequest` regions; labels mapped to 14
  classes (door, chair, table, backpack, laptop, phone, cup, television, vehicle, sofa, bottle, book,
  window, bed). Region-level localization (a direction and rough width), not tight boxes.
- `Scene/SceneObjects.swift`: `SceneObject` = the same physical object over time (class, smoothed
  world bearing and width, confidence, sightings, state visible / temporarilyLost / dormant /
  reacquired). Out of view it is remembered; back in view it is reacquired by class + bearing + size
  (`ObjectReID`); in view but missing for 20 s it is dropped. No distance (no depth source).
- `ObjectSoundFusion`: a sound attaches to a known object only with semantic compatibility (knock or
  doorbell + door, ringing + phone, TV + television, car horn + vehicle...), bearing agreement and a
  clear lead over other candidates. Speech never attaches; an unknown sound stays unknown. Attached,
  the notification reads "Knock · Door", and while that object is in view it sits on it.
- Quiet objects draw nothing. Settings > Developer > "Scene Understanding" shows regions, class,
  confidence, stable id, state and scan time.
- Scenarios 52-57; tests 107 -> 122 (`HERETests/SceneObjectTests.swift`). Still: `ios/screenshots/23_*`.
- Not verified on a phone: what the classifier actually returns per strip in a real room, scan time.

## 2026-10-10 (ios, later): no boxes in demo UI, real aura, identity that survives track loss

After a live test: boxes on faces, no visible glow, people renumbered on movement and re-entry.

- **Boxes:** they were `DebugOverlay`'s (solid face boxes, dashed body boxes), drawn whenever "Debug
  overlay" was on. Now behind their own `Settings.debugBoxes` switch, off by default, so the stats
  overlay can be on with nothing drawn on people. No production view draws a rectangle.
- **Aura:** `SpeakerAura` is two blurred radial gradients stretched to an ellipse (wide haze + brighter
  rim), screen-blended, clear in the middle, 2.7 x 2.5 face sizes. Opacity and a small pulse follow the
  person's smoothed speech intensity (same attack/release smoothing as notifications); Reduce Motion
  removes the pulse.
- **Identity:** three causes fixed. (1) A detection geometry can't place is matched by appearance to a
  track that found no detection, so a fast mover keeps their track instead of becoming a new person
  while the old track lingers. (2) Appearance is now a gallery of face-region and torso (clothing)
  feature prints per person, refreshed about once a second; scoring is appearance OR place-and-time
  (either can carry a match), with poor appearance discounting place so two people swapping spots
  don't inherit each other. (3) Captions and "spoke last" follow a re-identified person onto their
  new track (`Attributor.retarget`), so the caption and aura don't blink.
- Sound events arriving under a new id for the same continuing source (same kind, within 20 deg,
  within 2.5 s) stay one notification (`SoundContinuity`).
- Scenarios 31-36 (aura) and 37-51 (identity); preview pair "A. Production aura" / "B. Debug: raw face box".
- Tests: 99 -> 107. Still: `ios/screenshots/22_speaker_aura_no_boxes.png`.
- Not verified on a phone: feature-print distance scale (`printDistanceSame` / `printDistanceDifferent`).

## 2026-10-10 (ios): Spatial V2 -- top-right notifications, SessionPerson identity, session pins, People

Presentation and on-device identity only: no contract, relay or laptop change. Legacy look still at
Settings > Developer > Visual style > Legacy; Spatial V2 is the default. Nothing here has run on a phone.

- **Why "Person N" kept changing:** the label lived on the Vision track, and the only memory was "gone
  < 5 s and back within 25 deg". Now `Identity/SessionPeople.swift` holds a `SessionPerson` above the
  tracks: a track attaches to a person and detaches when it ends; the person stays. A new track is
  re-identified by `ReID` (appearance from Vision's general feature print of head + shoulders, bearing
  and time since last known, body and audio continuity), with an ambiguity margin: two plausible
  matches means a new person, never a swap. A pinned identity needs a higher score.
- **Body fallback** (`FaceTracker`): one `VNDetectHumanRectanglesRequest` per detect cycle; a face that
  stops being detected while its body is still there is held on an estimated head region.
- **Audio continuity:** `sources[].id` is "stable while tracked (built-in: always 0)", so ids are used
  only when the laptop heartbeat says `doa_source: odas`; otherwise time + bearing with explicit
  credibility (`spatialCredibleS`). No contract change.
- **Notifications** (`Presentation/`, `UI/Spatial*`): one per active source in a top-right stack (max 5,
  the rest queue). Icon + color by meaning (green environment, red danger, blue unattributed human,
  neutral question mark for unknown, a person's color once speech is theirs) + a ring with a marker at
  `Geo.relative`, the same value the belt rounds to a motor. Opacity = current smoothed intensity only;
  lifetime = active, short hold, fade, the same for every sound. A faced environmental sound resolves
  and is removed. Speech out of view is a notification with no floating caption; a pinned person's
  notification carries their captions and shows "location unknown" instead of a stale bearing.
- **Pins** are on the SessionPerson (survive track loss and re-entry). **People** (explicit "Add to
  People", Settings > People) is separate and persistent: name, color, priority; automatic recognition
  of enrolled people is experimental and off by default (it would need a real face-embedding model to
  be dependable -- not added).
- **Diagnostics** (Debug overlay): FACE / BODY / AUDIO / ATTRIB / REID / PEOPLE / UI lines plus per-face
  pixel size and confidence, to tell which stage fails at range.
- 30 deterministic scenarios (Settings > Developer, previews, tests; `-scenario N -scenarioFreeze T`).
- Tests: 45 -> 99. Still: `ios/screenshots/21_spatial_notifications_and_pins.png`.
- Open: README §14 and §25 ("no facial recognition") no longer describe the app and need the
  integrator's edit; feature-print distance scale (`printDistanceFull`) is a guess until tuned on device.

## 2026-10-09 (ios build): fix Person 1/2 label swapping (reported on real hardware)

- Kori reported on-device: the same physical person keeps flipping between "Person 1" and "Person 2"
  (not the earlier-fixed "cycles through 1,2,3,4,5" symptom -- genuinely swapping between two stable
  labels). Root cause in `FaceTracker.detect()`: for each new Vision detection, the old code picked
  the single existing track with the *highest IoU*, then separately checked a center-distance fallback
  *against that same track* if the IoU was low. When two tracks both have ~zero IoU with a new box --
  ordinary noise on real handheld/chest-mounted footage (a head turn, the dedup/NMS box jumping a few
  percent), far more common than in the Simulator's smooth synthetic motion that this was verified
  against -- `.max` on an all-zero-score list picks by `Dictionary` iteration order, not by who's
  actually closest. The distance fallback then gets checked against the *wrong* person's track,
  silently assigning the new detection (and therefore the label) to the wrong physical person.
  `labelFor` (track-loss reacquisition) had the same shape of bug: `firstIndex` grabbed the first
  `lost` entry within range instead of the closest one, so if both people's last-known bearings were
  both in range, reacquisition could grab the wrong one too.
- Replaced the per-detection, order-dependent matching with `assignDetections`: two global greedy
  passes over *all* detection/track pairs -- best-IoU-first, then (among what's left) closest-center-
  distance-first -- so a match is never decided by which detection or which track happened to be
  iterated first. `labelFor` now picks the closest `lost` candidate, not the first.
- 45/45 tests still green (no regression; `FaceTracker` has no direct unit tests -- it needs real
  `CVPixelBuffer`/`VNObservation` input -- so this fix is code-review-verified only, same class of
  limitation as the rest of the camera/Vision work). Needs retesting on the actual phone to confirm;
  asked Kori to retest and report back rather than claiming this is fixed from the Mac.

## 2026-10-09 (ios build): bearing accuracy for any mount orientation (portrait, upside down, ...)

- `HeadingProvider` already computes heading correctly for any physical mount (full attitude matrix,
  not Euler yaw) and `Frames`/Vision's rotation handling were already correct for all four capture
  angles -- but **bearing-from-pixel-position** (`Geo.bearing`/`Geo.normalizedX`) always used the
  camera's native *horizontal* field of view, even when the phone is mounted in portrait (captureAngle
  90/270), where the upright buffer's x axis is actually the sensor's *vertical* extent. Skewed every
  bearing by a large, orientation-dependent amount in that case, even though the face rectangles
  themselves still landed in the right place.
- `CameraController` now derives `vfovDeg` from the format's real pixel dimensions
  (`CMVideoFormatDescriptionGetDimensions`) via the standard pinhole FOV relationship, alongside the
  existing `hfovDeg`. New `Geo.effectiveHFOVDeg(hfovDeg:vfovDeg:captureAngle:)` picks whichever axis is
  actually horizontal right now; `Frames` gained `quantizedAngle`/`swapsDimensions` (refactored out of
  `sensorRect`, now shared). `AppModel.hfovDeg` and the Settings "Field of view: auto" display both go
  through this -- a manual override still always wins, unchanged.
- 45/45 tests (2 new: `testSwapsDimensionsForPortraitMounts`, `testEffectiveHFOVPicksTheAxisThatIs
  ActuallyHorizontal`). The live device-format FOV derivation itself is camera-hardware-dependent and
  not unit-testable -- code-review-verified only. Full writeup and the matching on-device checklist
  addition in `ios/SPIKE.md`.

## 2026-10-09 (ios build): "Reset display settings" safety net

- Added a destructive-styled "Reset display settings" button (with a confirmation dialog) to the
  bottom of Settings: resets Modes, Show, Alerts toggles and belt strength back to defaults in one
  tap. Deliberately narrow -- it does *not* touch Relay URL/token/session, calibration, or the
  wearer's name/aliases, so it can't undo the setup work the checklist just walked through. For
  recovering fast from a fat-fingered toggle between setup and a demo, not a full factory reset.
- 43/43 tests still green (UI-only addition, no new production logic beyond a plain field-copy).

## 2026-10-09 (ios build): AR/HUD polish + thermal headroom + haptics

- **Thermal headroom**: `hereGlass`'s flat-plate fallback now triggers at `.serious`, not just
  `.critical` (new `AppModel.simplifyGlass`, replacing five separate `thermal == .critical` call
  sites in OverlayView/ContentView/SoundPanel/Indicators). Detection rate already halves at
  `.serious`; this keeps rendering cost in step with it one tier earlier, cheap insurance against
  the 20-minute-with-AirPlay thermal risk that's unverified on hardware (SPIKE.md).
- **Haptics**: Calibrate now fires a success notification haptic, belt test buttons fire impact
  haptics, and a new alert (doorbell/name called) fires a warning notification haptic on the phone
  itself -- independent of the belt, so the wearer still gets a cue if BLE has dropped (README §23's
  own stated backup) or Settings > Belt is off. All added in `AppModel` next to the actions/state
  changes they confirm, not duplicated in views.
- **SpeakerRing**: solid-color stroke replaced with a subtle `AngularGradient` sweep -- same pulse
  timing/amplitude as before (untouched), just a richer line.
- **DirectionRing**: added a one-shot ripple ("ping") that plays once via `onAppear` when an alert
  card first mounts -- an impact cue distinct from the panel's continuous pulses, costs nothing at
  steady state since it doesn't repeat. Respects Reduce Motion (skipped entirely, same as the
  existing pulses).
- Verified: `xcodebuild build` + `test` both clean, 43/43 green. The visual changes (gradient
  stroke, ping) are code-review-verified only -- reuse the exact `@State` + `onAppear` +
  `withAnimation` pattern already proven elsewhere in this file (`EdgeGlow`, the existing
  `SpeakerRing` pulse), but a Simulator screenshot during a live alert wasn't obtained this round
  (UserDefaults seeding for a non-interactive Simulator run didn't take reliably within the time
  spent on it; not worth chasing further for a visual-only, low-risk change).

## 2026-10-09 (ios build): Settings restructure + guided Demo Setup walkthrough

- Settings was one flat 177-line `Form` mixing every user-facing control (Calibrate, Relay, Belt)
  with rehearsal-only debug controls (simulated faces/heading/thermal, front-camera toggle, raw
  test-injection buttons) in the same scroll. A judge or demo viewer paging through Settings could
  land on "inject test caption" by accident, and the list didn't read as a finished product.
- Split Developer-only controls (debug overlay, simulated input, thermal rehearsal, belt BLE UUIDs,
  no-laptop test triggers) into a new `DeveloperSettingsView`, reached via a single "Developer"
  `NavigationLink` at the bottom of the main Settings screen instead of being inline. Main Settings
  now only has what you'd actually touch around a demo: Calibrate, You, Modes, Show, Alerts, Belt,
  Relay. Added SF Symbol icons to every section header (`Label` instead of a bare string) for visual
  hierarchy.
- Added `DemoSetupView`: a guided, step-by-step walkthrough of README §23's 2-minute setup checklist
  (array → calibrate → speaker check → belt test → mirror), reached from a new "Demo Setup" button at
  the top of Settings. Step 2 (calibrate) and step 4 (belt test) trigger the same `AppModel` actions
  Settings already exposes; step 3 (speaker check) shows a live readout -- relay status, faces
  tracked, newest caption -- so "check" landing on Person 1 is confirmable in the wizard itself
  instead of eyeballing the monitor. Progress dots + Back/Next/Done, matches existing visual style
  (`hereGlass`, `Typeface`).
- Verified: `xcodegen generate` + `xcodebuild build` and `test` both clean, 43/43 tests still green
  (no production logic touched, pure UI restructure). Settings sheet navigation itself is
  code-review-verified, not a tap-through Simulator screenshot -- no UI-automation harness set up
  here to drive Simulator taps programmatically.

## 2026-10-09 (ios build): app icon + launch screen (was the system default)

- There was no icon asset catalog in the project at all -- the app ran under the generic default
  iOS icon and a blank white launch screen. First impression matters for a demo; this was the
  cheapest, highest-visibility gap to close.
- Added `HERE/Assets.xcassets` with an `AppIcon` (single 1024x1024 source; Xcode 26 derives every
  legacy size from it automatically) and a `LaunchBackground`/`LaunchLogo` pair wired into the
  Info.plist `UILaunchScreen` (no storyboard needed). Mark: an open ring with a center dot in the
  app's teal (`Palette.people[0]`) on a near-black background -- the same "ring around a tracked
  point" language the on-screen `SpeakerRing`/`DirectionRing` already use, so the icon reads as
  the product rather than generic clip art. Drawn with CoreGraphics (a throwaway `swift` script,
  not checked in) since no image-generation tool was available; no design tool or designer
  involved, so treat as a placeholder a real designer can replace outright, not final branding.
- `project.yml`: `ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon`, `UILaunchScreen` now points at
  `LaunchBackground`/`LaunchLogo` instead of `{}`.
- Verified: `xcodegen generate` + `xcodebuild build` succeeds, `Info.plist` in the built bundle
  shows `CFBundleIcons` resolved and `UILaunchScreen` pointing at the new assets, installed and
  launched in the Simulator, icon checked at both 1024 and the compiled 60x60@2x size (legible at
  both). 43/43 tests still green (no production code touched).

## 2026-10-08 (ios build): minor -- RelayClient.disconnect() didn't stop its timers

- `disconnect()` never invalidated the heartbeat/clock-ping `Timer`s `startTimers()` creates -- they'd
  keep firing forever (harmlessly, since `task` is nil so sends are no-ops, but wastefully). Currently
  dead code in practice (`relay.disconnect()` has no call site in the app today; only `belt.disconnect()`
  is used), but worth fixing for correctness regardless of whether something calls it yet. 43/43 tests
  green.

## 2026-10-08 (ios build): belt could get stuck in "connecting" forever

- Proactive robustness pass on `BeltController`: `didConnect` fires at the BLE link layer, then
  `discoverServices`/`discoverCharacteristics` can simply never find the expected UUIDs (firmware
  mismatch, a stale/wrong peripheral, flaky discovery in a crowded room -- README §16/§24's own stated
  BLE risk) and both failure branches were a bare `guard ... else { return }` -- no error handling, no
  retry, nothing. `peripheral` stays non-nil forever, which blocks `scanIfPossible`'s `peripheral == nil`
  guard from ever trying again. The only way out was the peripheral happening to drop the link on its
  own, or the user manually toggling the belt off/on in Settings.
- Added a 10 s connection deadline, started in `didConnect`, cancelled the moment the real command
  characteristic is actually found: if it's not found in time, forcibly cancels the connection, which
  triggers the existing `didDisconnectPeripheral` -> `reset()` -> automatic rescan path already in place
  for every other disconnect case. No new recovery mechanism, just closing the one gap that had none.
- Not testable without real belt hardware (no device here) -- code-review-verified only, same class of
  limitation as the camera/Vision work. 43/43 tests green (no regression).

## 2026-10-08 (ios build): VoiceOver labels for the alert panel and status pill

- Proactive accessibility pass: only `CaptionView`, `SoundPanel`'s container, and the Settings gear
  button had any VoiceOver annotation at all. `AlertCard` (top-right doorbell/name-called panel) had
  `.accessibilityElement(children: .combine)` but no explicit label -- `.combine` reads child views'
  own labels in layout order, and the single most important piece of information on the card, which
  *direction* to turn, lives entirely in `DirectionRing`'s arc rotation angle: a `Shape`, no text, no
  label, silently dropped. A VoiceOver user got "Doorbell, x2" with no direction at all, for a feature
  whose whole purpose is direction. Switched to `.ignore` + an explicit `.accessibilityLabel` that states
  it plainly ("Doorbell alert, repeated 2 times, behind you"), reusing the same `Geo.describe` text
  already shown visually. Same fix pattern `CaptionView` already uses for its own off-camera hint.
- `StatusPill` had no accessibility grouping at all -- VoiceOver would stop on the colored dot, the
  status text, the engine name and the thermometer icon as separate, disconnected swipe stops. Added
  `.accessibilityElement(children: .combine)` so it reads as one "connection status" announcement.
- `BeltIndicator` left alone on purpose: README §23 frames it as a sighted-bystander/demo-operator aid
  ("so the audience ... can see what the belt does"), not primary information for the wearer, who feels
  the belt directly -- lower priority than the two information-bearing fixes above.
- 43/43 tests still green.

## 2026-10-08 (ios build): Dynamic Type support -- captions never scaled with text size

- Proactive accessibility pass, not a reported bug: every font in the app was `.system(size: <fixed
  pixel>, ...)`, which never responds to the user's preferred text size at all. Real gap given captions
  are the literal product -- README §14's legibility rules cover contrast and never mention type size.
- `Typeface` (`Theme.swift`) rebuilt on `Font.TextStyle` (`.title3`, `.subheadline`, `.caption`,
  `.caption2`, `.footnote`) instead of raw point sizes -- these scale with Dynamic Type automatically, no
  `@ScaledMetric` plumbing needed. Each old fixed-pixel call site got the closest-matching style (not an
  exact pixel match -- once Dynamic Type is in play, the user's preference matters more than fidelity to
  the old value). 7 call sites updated across `OverlayView`/`Indicators`/`SoundPanel`.
- Verified in the Simulator via `xcrun simctl ui <device> content_size <size>` (discovered this session):
  at a realistic larger size ("extra-extra-large"), the caption and debug overlay scale up cleanly, fully
  legible, no overlap. At the most extreme accessibility tier
  ("accessibility-extra-extra-extra-large") text does overlap/truncate -- expected and common even in
  Apple's own apps at that tier; the realistic range is what was verified to look genuinely good, not
  just "doesn't crash." 43/43 tests still green (no test asserts on font sizes).

## 2026-10-08 (ios build): one face briefly became two simultaneous "Person N"s

- Reported on-device, follow-on from the label-cycling symptom: eventually a single face got tracked as
  *both* Person 1 and Person 2 at once. Cause: `VNDetectFaceRectanglesRequest` can return more than one
  overlapping box for one real face (partial occlusion, an angled profile, motion blur), each clearing
  the confidence filter independently -- nothing deduplicated them, so two overlapping detections in the
  same frame could each match or spawn their own track.
- Added `FaceTracker.dedup(_:)`: standard non-max suppression on fresh Vision detections, keeping only
  the highest-confidence box in each cluster of mutually-overlapping ones (new `Tunables.faceDedupIoU`,
  0.3) before any track-matching happens. Also added `mergeOverlappingTracks`, a defensive pass after
  every detect/track cycle that merges any two *tracks* which end up heavily overlapping (keeps the
  older one) -- catches the case where two tracks already diverged onto the same face on a prior bad
  frame, before this fix was in place.
- Not unit-tested: `VNFaceObservation` doesn't expose a public initializer that lets a test control
  `confidence`, so `dedup`'s sort-by-confidence behavior isn't mechanically verifiable off real Vision
  output -- code-review-verified only, same class of limitation as the rest of the camera/Vision pipeline
  (Task 6's Projector note, the front-camera addition). 43/43 existing tests still pass (no regression).

## 2026-10-08 (ios build): face label cycling through Person 1, 2, 3... for one person

- Reported on-device: a single stationary face kept getting relabeled "Person 1" -> "Person 2" -> "Person
  3"... instead of staying one person. Diagnosis (tuning-based reasoning, not directly verified on
  hardware -- no device here to iterate on): `VNTrackObjectRequest(.fast)` drops a track the instant one
  frame's confidence dips below `trackMinConfidence`, no grace period, and `detect()`'s own
  missed-detection counter only tolerated 2 misses -- both easy to trip at close/handheld range (a selfie
  test holds the phone much closer to the face than the intended chest-mounted, 1-3m-away rig, so normal
  hand tremor moves the box a lot more in pixel terms). When a track drops and a fresh detection comes in,
  `labelFor()` only reuses the old "Person N" label if the *recomputed bearing* is within 12 deg of where
  it was -- and since bearing depends on heading, which drifts with handheld camera movement, even the
  same physical face easily misses that window after a drop, minting a brand new label every time.
- Widened three `Tunables`: `trackMinConfidence` 0.3 -> 0.2, a new `missedDetectionsBeforeDrop` (2 -> 3,
  previously a hardcoded literal in `FaceTracker.swift`), `relabelDeg` 12 -> 25. All three make track
  continuity more forgiving of brief Vision confidence dips and bearing drift between a drop and a
  recreate, which should reduce how often a drop happens at all and make the relabel-match far more
  likely to succeed when it does. Tradeoff worth knowing: a wider `relabelDeg` means two *different*
  people within 25 deg of each other are slightly more likely to get mixed up across a brief drop -- low
  real-world risk given normal conversational speaker spacing, but a conscious one, not free.
- 43/43 tests still green (no test hardcodes these specific values). This is a best-diagnosis tuning
  change, not something I could verify fixes the actual symptom without a device -- worth retesting, and
  if it's still cycling, the next things to check are whether it happens on the back camera too (rules
  front-camera-specific geometry in or out) and whether propping the phone stationary (vs. handheld)
  changes anything (isolates hand tremor as a factor).

## 2026-10-08 (ios build): fixed captions freezing after the face leaves frame

- `Attributor.refresh`: a caption attached to a tracked face (`.person(id, label)`) never checked whether
  that id was still being tracked. Once the face left frame, `Layout.captions` couldn't find a rect for
  it, fell into the "in view, no face found" branch, and placed the caption at whatever bearing it was
  last given -- which, with nothing left to re-derive it, stayed frozen forever. Looked like the caption
  drifting free of the person and lingering indefinitely after they left frame.
- Fix: `refresh` (called every frame) now also checks the reverse direction -- a `.person` assignment
  whose face has been missing for `faceLostHoldS` (0.5 s, new `Tunables` entry) falls back to `.offCamera`
  at its last known bearing, which gets it the proper edge/chevron treatment and the normal caption-expiry
  clock, instead of being stuck as a dead `track_id` forever. The hold matters: Vision's per-frame
  tracking can drop a face for a single frame on a blink or fast turn without them leaving, so reacting
  instantly would flicker the caption off-camera and back on every blip -- same spirit as the existing
  `moveHoldS` bearing-drift tolerance.
- 2 new `AttributionTests`: confirms no flip within the hold (including on brief reappearance, which
  resets the timer) and confirms the flip does happen once the hold elapses with the face still gone.
  43/43 tests green. Checked against `testFlickeringFaceKeepsItsAssignment` (a different code path,
  `attribute()` not `refresh()`) for no regression.
- Tried to also screenshot the live transition in the Simulator (inject a test caption attached to a
  face, then shift simulated heading until that face leaves view) but couldn't reliably land a
  screenshot inside the narrow 0.5 s window given this environment's command round-trip latency; across
  several attempts the end state was consistently "resolved to nothing" rather than "stuck showing a
  dead Person N", which is the actual bug fixed, just without a clean mid-transition screenshot as
  evidence. The precise timing logic itself is covered exactly by the two new unit tests.

## 2026-10-08 (ios build): caption tracking felt laggy on-device

- `OverlayView.swift`: captions visibly detached from the face/box during head or camera movement on a
  real device. Cause: the caption's `.position(p.point)` was wrapped in a 0.25 s smoothing animation, but
  the speaker ring and the debug face box next to it have no position animation at all -- they snap to
  the current tracked rect every frame. That mismatch meant the caption always trailed a quarter-second
  behind the box it's supposed to sit above. Shortened to 0.08 s: still smooths out single-frame jitter
  (README §14 "no jitter"), but close enough to instant not to visibly lag. Not verified against real
  Vision tracking (the Simulator's SimulatedFaces uses synthetic sine-wave motion, not real camera
  jitter) -- needs a look on-device to confirm it actually feels tighter.

## 2026-10-08 (ios build): solo on-device testing tools

- Added Settings > Developer > "Use front camera (testing)": production is always the back camera, but
  testing alone with no mount needs to see yourself and the screen at once. `CameraController` gained a
  `position` property (default `.back`); front has no ultrawide on any iPhone so it goes straight to wide.
- Added Settings > "Test triggers": "Inject test caption" ("Hey, my name's Kori, how are you?", attached
  to whichever face is currently tracked) and "Trigger test doorbell" / "Trigger test name called" (both
  clear themselves after 6 s like a real one ending). All three route through the same `onUtterance`/
  `onAlert` handlers real laptop messages hit, so attribution, belt buzzing, and alert dedup/fade behave
  exactly as they would live -- nothing reimplemented. Verified in the Simulator: caption lands correctly
  on a tracked face with the right tone chip, doorbell alert shows with its direction ring, no stacking.
- Noted for next time: `DebugOverlay` (face boxes + bearings) only draws when Settings > Debug overlay is
  on -- easy to miss on first on-device test and read as "face tracking isn't working" when it actually is.

## 2026-10-08 (ios build): kickoff done -- human checklist finalized

All 8 tasks from `prompts/IOS_AGENT.md` complete: builds clean, 41/41 `HERETests` pass, every README §4
core moment verified against `tools/fake_events.py` in the Simulator with screenshots, three real bugs
found and fixed along the way (stacked `name_called` alerts in the fixture, a caption-contrast violation
under bright scenes, a silent "wrong token" relay failure), and two new Settings > Developer debug controls
(simulated heading, simulated thermal state) to rehearse what would otherwise need a phone. Added a
"before you start" note to `ios/SPIKE.md`'s on-device checklist pointing at what's already covered so the
remaining items are specifically what needs the physical rig. No blockers -- nothing in
"Blocked / needs a human" because there's nothing blocked.

## 2026-10-08 (ios build): relay robustness -- fixed a silent "wrong token" failure

- Verified live against a real relay process: stop/start while the app runs takes the status pill
  `live` -> `offline` -> `live` within the existing reconnect backoff, with no code change needed (already
  correct), stale captions staying on screen rather than blanking, and fresh captions/alerts flowing again
  on reconnect. `config` re-send on reconnect was already wired (`AppModel`'s `onConnected` callback) and
  confirmed firing on the same reconnect.
- **Found and fixed**: a wrong relay token (HTTP 401, relay.py's `HERE_RELAY_TOKEN` check) showed as
  generic "offline" forever while the client silently kept retrying a token that could never work --
  exactly the silent failure the kickoff called out. `RelayClient.Status` only had
  `disconnected/connecting/connected`; added `rejected(Int)`, detected via
  `urlSession(_:task:didCompleteWithError:)`'s HTTP response. First attempt compared task identity
  (`task === self.task`) and never fired: root-caused with a temporary debug print (removed before
  committing) that `receive()`'s own failure callback reliably wins the race and swaps `self.task` for a
  new task before the slower `didCompleteWithError` delegate lands. Fixed by dropping the identity check --
  a 401/403 means *this token* is rejected regardless of which in-flight task reports it. On rejection,
  `wanted = false` stops the backoff loop cleanly instead of hammering the relay forever. Status pill now
  shows "bad token" instead of "offline"; Settings shows the HTTP code and what to do about it.
- Testing postmortem for next time: killing a relay by `lsof -ti :8080` (all sockets on the port) instead
  of `lsof -i :8080 -sTCP:LISTEN -t` (the listener only) also kills the app's own socket if connected --
  looks exactly like an app crash (process vanishes, no crash report) and cost real time chasing a
  regression that wasn't there. Screenshots and full writeup in `ios/SPIKE.md`.

## 2026-10-08 (ios build): thermal + Reduce Motion debug paths

- Added Settings > Developer > "Simulated thermal state" (`ios/HERE/Settings/Settings.swift`,
  `SettingsView.swift`): `ProcessInfo.thermalState` is read-only and the Simulator essentially never
  reports anything but nominal, so there was no way to rehearse `.serious`/`.critical` without a hot
  phone. Wired into `AppModel.thermalChanged()` ahead of the real `ProcessInfo` read.
- Verified in the Simulator: `.critical` switches captions to the flat non-glass plate
  (`12_thermal_critical_simple_glass.png`); `.serious` correctly keeps the normal glass -- only the
  detection rate halves there, not the rendering (`13_thermal_serious_normal_glass.png`), confirming the
  `simple` flag's `== .critical` gating is exactly right. `tracker.detectEvery`'s halving itself needs a
  real camera to observe in effect, same class of limitation as the Projector real-path note (Task 6).
- Confirmed the Simulator can be driven into Reduce Motion headlessly (`defaults write
  com.apple.Accessibility ReduceMotionEnabled -int 1` via `simctl spawn`) and that SwiftUI's
  `accessibilityReduceMotion` environment key -- the same one `OverlayView`/`TypingDots` already gate
  their animations on -- correctly reflects it in a relaunched app (verified with a temporary probe view,
  reverted before committing). Screenshots in `ios/screenshots/`, full writeup in `ios/SPIKE.md`.

## 2026-10-08 (ios build): Projector/coordinate tests

- `ios/HERETests/ProjectorTests.swift`: 8 new tests. `Frames.sensorRect` for all four `captureAngle`s
  (0/90/180/270 -- 270 had no prior coverage), plus a 90-then-270 round trip (the two landscape mount
  orientations are inverses of each other) and a 180-twice round trip. `Frames.sensorPoint` directly
  (previously untested on its own). `Projector`'s `layer == nil` fallback path (Simulator / simulated
  faces): aspect-fill math for both width- and height-constrained screens, `x(forBearing:)` composition
  (center case, off-center via `Geo.normalizedX`, outside-HFOV -> nil), and the `bearing`/`relative`
  wrappers. 41/41 tests green total.
- Noted in `ios/SPIKE.md`: `Projector`'s real (layer-backed) path goes through
  `AVCaptureVideoPreviewLayer.layerRectConverted`, which depends on a live capture session and isn't
  meaningfully unit-testable off a device -- that's what the on-device "do the face boxes sit on faces?"
  checklist item is for.

## 2026-10-08 (ios build): Layout unit tests

- `ios/HERETests/LayoutTests.swift`: 8 new tests for `OverlayView.Layout.captions` (README §14 "never cover
  a mouth", edge placement, no overlap). Covers above-head vs. below-chin placement (including the forced-
  above edge case when a short screen means "below" would run off the bottom), the 14pt no-overlap gap
  against the face rect in both directions, off-camera edge placement with the correct chevron side and
  `Geo.describe` hint text, the "in view but no face tracked" middle branch, two close on-camera captions
  getting separated rather than stacked, and two off-camera captions on the same edge stacking by row. All
  pass against the existing implementation unchanged (33/33 tests green total).

## 2026-10-08 (ios build): fixed a real contrast violation in the caption glass

- Verified SwiftUI previews render identically under light/dark `preferredColorScheme` (none of the custom
  glass/Palette colors read `colorScheme` -- confirmed, not assumed) and added a dark-room `Backdrop`
  variant alongside the existing bright one so both ends of real-world scene brightness are covered.
- **Found and fixed**: README §14's non-negotiable "provisional words dimmer but still >= 4.5:1 contrast"
  did not hold. Measured by temporarily rendering the bright-backdrop caption preview as the app's root
  view, running it on-device in the Simulator (`.glassEffect()`/Liquid Glass is a private material, not a
  flat alpha blend, so this had to be measured from real rendered pixels, not computed by hand), and
  sampling with ImageMagick: pending text (`.white.opacity(0.72)` over `.black.opacity(0.35)` glass tint)
  measured **1.47:1** against the brightest backdrop corner; even fully-opaque committed white only reached
  **2.55:1**. Root cause: dimming pending text via *opacity* means its rendered color blends with whatever
  the glass tint lets through, so it loses contrast right along with the backdrop -- raising the tint alone
  barely helped (2.19:1 at tint 0.6) because the translucent text darkened too.
- Fix: `CaptionView.bodyText`'s pending words now use an opaque `Color(white: 0.85)` instead of
  `.white.opacity(0.72)` (`ios/HERE/UI/CaptionView.swift`), and `hereGlass`'s default tint moved from
  `.black.opacity(0.35)` to `.black.opacity(0.94)` (`ios/HERE/UI/Theme.swift`). Re-measured: **4.89:1**.
  Also checked the thermal-fallback flat path (`simple=true`, exact arithmetic since it's plain alpha
  compositing): **7.25:1**. `HERETests` still 20/20 green; no regression against the app's normal (dim)
  look. Screenshots and full methodology in `ios/SPIKE.md`.

## 2026-10-08 (ios build): turn-around debug control

- Added Settings > Developer > "Simulated heading" slider (shown only with Simulated faces on): drives
  `AppModel.headingDeg` directly, since CoreMotion doesn't run in the Simulator and there was previously no
  way to rehearse turning without a phone. Added a third simulated person, Person 3, at world bearing 180°
  (Moment 2's off-camera speaker's position), so turning far enough actually reveals a face.
- Verified across heading 0/90/150/180: the off-camera caption's directional hint live-recalculates
  ("behind you" -> "to your right"), Person 3 becomes a tracked face once in the camera's HFOV, and the
  caption re-attributes from "Someone" onto "Person 3" once they're visible -- `Attributor`'s turn-around
  rule (README §13) firing correctly. Screenshots `06_turnaround_*` through `09_turnaround_*` in
  `ios/screenshots/`, details in `ios/SPIKE.md`.

## 2026-10-08 (ios build): Simulator end-to-end verified

- Verified the three README §4 core moments in the iPhone 17 Simulator against `relay` + `tools/fake_events.py
  --loop` with Settings > Simulated faces: captions land on Person 1/2 with tone chips and rings, the
  turn-around's off-camera caption shows at the screen edge with a chevron, the doorbell alert shows the
  direction ring and fades correctly, the belt indicator lights up even with BLE unavailable. Screenshots in
  `ios/screenshots/`, described in `ios/SPIKE.md`.
- Found and fixed in `tools/fake_events.py`: `name_called` alerts were sent `active=true` and never closed
  (unlike doorbell, which already did), so every `--loop` iteration left a permanent new alert card -- three
  "Name called" cards end up stacked on top of each other after a few loops. Mirrored doorbell's pattern
  (reuse `alert_id`, send `active=false` 6 s later). This was a fixture-fidelity bug in the dev tool, not an
  `ios/` app bug -- `AppModel`'s alert dedup/fade logic was already correct.
- Noted, not fixed: an off-camera edge caption can land on top of a nearby on-camera face box (`Layout`
  dedupes caption-vs-caption overlap, not caption-vs-face). Cosmetic with the Simulator's abstract faces;
  worth a look once real Vision face boxes are available. See `ios/SPIKE.md`.

## 2026-10-08 (ios build): first compile on a Mac

- `ios/HERE/App/HEREApp.swift`: `var body: some Scene` didn't compile — `HereContract.Scene` (the `scene`
  message type) shadows `SwiftUI.Scene`. Qualified the one usage as `some SwiftUI.Scene`; no contract shape
  touched. That was the only compile error in the whole app and test target.
- `xcodegen generate` + `xcodebuild ... test` on iPhone 17 Simulator (iOS 26 SDK, Xcode 26): builds clean,
  all 20 `HERETests` pass, including `ContractTests.testAllExamplesDecode` (all 14 examples).

## 2026-10-09 (day): 3D sound field in the browser (laptop/odas)

- `python -m laptop --real --viz` (or any `--replay` of a raw fixture; `--odas` adds ODAS): a local page at
  http://127.0.0.1:8770 (laptop/viz.py + viz.html, three.js 0.160.0 from jsdelivr, `viz:` in config.yaml). Shows,
  in the world frame seen from behind the wearer: a hemisphere heat map of our SRP-PHAT power (azimuth x 6
  elevations, the room's quiet map subtracted), ODAS tracks with trails and its potential sources (energy points),
  our SRP tracks, the contract `sources`, and the array with its 12 LEDs as lit. Layer toggles, drag to orbit.
  Local only (binds 127.0.0.1; no audio leaves the laptop). Nothing goes to the relay.
- Measured: a 66 mm planar array barely resolves elevation (row maxima within ~0.05 of each other) and diffuse
  room noise looks like it comes from overhead (quiet map peaks at 60 deg up) -> background subtraction; the sky is
  a guide, not a measurement. Replay of rot_talker_baby: our tracks at -9 (talker, truth 0) and -162 (baby, truth
  180). Live (laptop speaker at 177): tracks 170-175, LEDs as drawn; second LED blinked on the speaker's wall echo
  for 12 of 425 frames (~3%).
- `SrpPhat(elevations_deg=...)` grid; the ODAS bridge now parses potentials and keeps the newest tracked frame.


- **Found: DOA_VALUE is the active focused beam's direction, not the sound's.** With fixed beams it only ever
  names one of them: beams at +-90, laptop speaker at 177 -> DOA_VALUE +92 / -88 (live); with ODAS steering the
  beams it read 167 / 169 / -178 / -143 = wherever the beams were. So in the demo config (fixed beams [0, 180])
  the ring could only show front or back. With auto-tracking beams (processed capture) it is fine (2.4 deg).
- **Main marker = our SRP-PHAT on the raw mics** (`ring.RingPlanner`, `audio.ring_main: auto`), dark while the
  firmware's free-running beam (its 0.5 s cluster) disagrees by > 30 deg. Live, one loudspeaker, 6 runs: right
  (within 20 deg) 87-95% of the time it is lit, lit for 66-82% of the sound, dark in silence. Was: front/back only
  with fixed beams; a stale direction for ~0.15 s then a 0.3 s sweep at every new talker with auto beams.
  2-ch firmware: DOA_VALUE through `LeadDoa` (the free-running beam stands in while DOA_VALUE is stale or sweeping;
  a reflection is dropped if DOA_VALUE never follows within 0.25 s).
- **Second marker** (`audio.ring_second: auto`): ODAS tracks with `--odas`, else another SRP track; never a fixed
  beam (the old code drew the front fixed beam whenever it leaked, while the sound was behind). It must have been
  the loudest source itself at some point (a wall echo never is) and only shows beside a main marker. Live, one
  source: phantom second marker 0% (default) / 3% (`--odas`) of the sound, 0 in silence (was 17-21% and 46% +
  silence before the rule). Rotation scenes (two real-acoustics sources): both markers right 11-46% of the
  overlap with SRP, ODAS tracks 60-69% (DOA_VS_ODAS.md) -> `--odas` for the two-LED demo.
- `SrpSources` (localize.py): streaming SRP tracker (noise gate + peaks + SrpTracker), shared by the ring and
  `tools/doa_compare.py` (identical scores). `ring_hold_s` 1.2 -> 0.6 (no trail). `python -m laptop.audio.ring
  --follow` shows the fast marker too.

## 2026-10-09 (night): ODAS track, laptop/odas branch (overnight agent, prompts/ODAS_AGENT.md)

- **Raw mics amplified on-chip.** USB ch 3-6 carried category 1 (unamplified raw mics): ambient ~-78 dBFS =
  ~4 LSB, quantization-dominated (int32/float capture gives no extra bits). Category 3 = the same mics after
  `AUDIO_MGR_MIC_GAIN` (90x, +39 dB): ambient -35..-40 dBFS. `audio.raw_mic_category: 3` (volatile, restored to 1
  on exit; processed ch 0/1 unchanged). Registers added: `AUDIO_MGR_OP_CH3..6`, `AUDIO_MGR_MIC_GAIN`.
- **6-channel recorder.** `--record` writes `raw.wav` (4 raw mics, USB order, int16, `audio.raw_record_gain`
  noted in meta) next to `audio.wav`, sample-aligned; meta.json now has firmware, routing, fixed beams,
  calibration, mic geometry, raw category/order. `FixturePlayer` replays raw.wav (`on_raw`) and `locate()`s sounds
  on it like the live array. `RawLocator` (localize.py) is shared by both; `audio.raw_mic_order` applies the mic order.
- `tools/speaker_scene.py --raw --true-bearing 180`: + raw.wav, + a baby cry; aligns on the louder beam (beams
  mode records 2 channels). Recorded `fixtures/raw_speaker_180` (TTS + doorbell + baby cry from the laptop speakers
  at world 180; committed: no people). In-band (300-6000 Hz) SNR only ~5 dB: speech -44, room/fan -49 dBFS.
- **Raw mic order fitted.** `python -m laptop.audio.localize --fit <raw fixture>` scores all 24 permutations
  against a known bearing: identity [0,1,2,3] = AEC_MIC_ARRAY_GEO order (177.8 for truth 177.2, coherence 0.79);
  its x-mirror can't be excluded from 180 alone. `localize --check` now compares the fitted order with its mirror
  against the device DoA (`--seconds N` for unattended runs): Kori's 2-minute front + right check decides it.
- **Rotation scenes.** `tools/make_raw_scene.py`: permuting the 4 raw mics of `raw_speaker_180` = rotating the
  source by 90 deg (exact far field). Scenes `fixtures/rot_*` (regenerated in seconds, not committed): single
  sources at 0/90/180/-90, talker (0) + baby (180), two talkers 90 and 180 apart, with a room-noise bed and truth
  in meta.json. SRP on them: 9/9 lines per single scene land 90 deg apart, all ~6 deg short of nominal (the speaker
  really sits at ~174-178: SRP 173-175, device DoA 177.6).
- **ODAS builds and runs in WSL2.** `tools/odas/setup_wsl.sh` (introlab/odas pinned at bcb8454, CMake 4 policy
  floor). Ubuntu 26.04's glibc broke ODAS's JSON sinks (`sprintf(buf, "%s...", buf)`: every frame came out as `}`);
  the script patches those calls in the local clone at build time (nothing vendored). `laptop/audio/odas_cfg.py`
  generates the config from `audio.odas` (geometry in AEC order, hop 128, 4 tracks; offline = files, live = sockets;
  `laptop/audio/odas/here.cfg` is the generated live one). `python -m laptop.audio.odas_wsl --offline <raw fixture>`
  runs it on a fixture and scores tracks against the truth. Stock tracking parameters already hold talker (0) + baby
  (180) as two tracks (99% / 92% of their active time within 20 deg) on `rot_talker_baby`.
- **ODAS live, no socat/FIFO:** ODAS's raw source and all its sinks are TCP clients, and WSL2 mirrored networking
  reaches Windows' 127.0.0.1, so `OdasBridge` (laptop/audio/odas.py) serves raw audio and receives tracks /
  potentials / separated / post-filtered audio directly, starts odaslive in WSL itself (restarts it on exit) and maps
  frame stamps to capture times. `OdasSource`: contract `sources` (ODAS track ids, world bearings via the beams'
  calibration, throttled to doa_rate_hz), heartbeat problems when ODAS is down. Audio per track: `beams` (hybrid:
  XVF fixed beams steered to the two strongest tracks, `BeamSteer` hysteresis) or `separated` / `postfiltered`
  (one pipeline per ODAS slot). `python -m laptop --real --odas [--odas-audio ...]`, or `--replay <raw fixture>
  --odas` (playback waits until ODAS connects). Default unchanged: `audio.doa_source: builtin`.
- Laptop: generic hooks only. Directions may route sources to audio streams (`stream_for`); the raw tap is the audio
  input or the ODAS source's `ears`.
- **Comparison harness** `tools/doa_compare.py` (+ `tools/doa_live.py`): one scorer for any `sources` stream
  (detection, median error, two-source rate, ghosts = wrong directions vs stale = right direction while silent, id
  switches, fresh-onset latency); `--scenes` (srp + odas offline), `--live` (real array + laptop speaker: builtin,
  fixed beams, odas/hybrid, with end-to-end latency and captions), `--captions` (ODAS separated / post-filtered /
  unseparated mix on rotation scenes), `--replay`. `SrpTracker` + `peaks` (localize.py): Track A's planned tracker,
  as the raw-mic baseline. ODAS activity peak-hold (0.4 s): activity flickers within words (detection 44% -> 85%).
- Results (laptop/audio/DOA_VS_ODAS.md): ODAS holds two sources (66-68% of overlap time vs SRP 16-72%) with 0
  wrong-direction ghosts but 5-30 s/min of stale tracks and ~10-15 deg error (SRP 5-7, device DoA 2.4 live);
  captions from ODAS's own audio are poor (WER 0.21-0.52, baby alert missed). Tuned `probMin` 0.5 -> 0.3.
- `python laptop/audio/xvf3800.py --reset`: restores fixed beams, OP_L/R and the raw-mic routing after a killed run.
- Hybrid `BeamSteer` keeps the two beams >= 45 deg apart (live, ODAS split one speaker into two tracks and steered
  both beams behind the array). **Report:** laptop/audio/DOA_VS_ODAS.md "Results" (tables, decision rule: ODAS fails
  4 of 5 gates -> Track A stays the demo path) and the morning summary + 10-minute check at the top of
  laptop/audio/SPIKE.md. README §11 status row updated.

## 2026-10-09: 6-channel firmware on the demo array

- XVF3800 flashed to Seeed v2.1.1 16 kHz 6-channel (by Kori, dfu-util; see laptop/audio/SPIKE.md): two beams on
  ch 1-2 + four raw mics on ch 3-6; the existing code runs unchanged (raw-mic SRP direction switches on). The SRP
  angle frame still needs a two-position fit (it read -98 for a source at 180).

## 2026-10-09: two sources at once, live on the real array

- Live test, person in front talking through a baby crying from the laptop at 180 (recorded locally as
  fixtures/two_real2, the person's voice, not committed). Auto-tracking beams: the cry took beam 0, beam 1
  sat at -90, nothing the person said during the cry was captioned. **Fixed beams** (`audio.fixed_beams:
  [0, 180]`, AEC_FIXEDBEAMS*): every line during the cry captioned from the front (+0.8), word for word;
  name called from the front; baby crying from -179. False "Doorbell" from the cry fixed (template ignored
  when PANNs hears another alert sound). Startup crash from the fixed-beam commit fixed.
- Still wrong on that recording (replayed): 2 short phantom captions on the baby's beam ("That's a good
  question.", "So that's great.") and 1 baby alert from the front: the person's voice leaks into the baby's
  beam, so its classifier hears speech > crying and the guards don't trigger. Next: per-beam speech/cry
  energy ratios from raw mics (6-channel firmware) instead of the beam audio alone.
- Golden gate 7/7. `nonspeech_share` 0.25; a beam whose classifier hears more speech than the sound can't
  raise that sound's alert.

## 2026-10-08 (late night): two sources at once (beams mode)

Someone talks to the wearer while a baby cries behind them: the cry must be detected, from the right
direction, without touching the talker's captions; two people talking over each other must both be captioned.

- **No firmware flash needed.** The XVF3800's audio-manager routing (`AUDIO_MGR_OP_L/R`, Seeed's host docs)
  can put the two slow-moving focused beams (category 6, sources 0/1) on USB channels 0/1 on firmware 2.0.6.
  Measured on the array: both beams at normal speech level (-19/-22 dBFS); one talker -> both beams on them
  (correlation 0.71). Writes are volatile; the ReSpeaker sets the routing its mode needs at start and restores
  the defaults (8,0)/(7,3) on exit. (A 6-channel firmware would add raw mics; flashing was not allowed here.)
- `audio.capture: beams` (default; `--no-beams` for the old single processed stream): two audio streams, `sources`
  id 0/1 = each beam's own direction + speech energy (beam 1 only counts when >= 30 deg from beam 0, so one
  talker is one source). Per beam: a SpeechPipeline (sharing one whisper) and a SoundPipeline (sharing PANNs).
- **Time-frequency masking between the beams** (`beam_mask`): each beam's caption audio keeps only the STFT bins
  it hears louder than the other beam. Without it whisper transcribed the other talker leaking in at -9 dB.
  The VAD also ignores windows where the other beam is >= 3 dB louder (a leak, not this beam's talker).
- Cross-talk: the same words from both beams at the same time are captioned once, by the louder beam.
- Sound arbiter: a sound heard on both beams is published once, from the beam scoring it highest (its direction).
- Non-speech guard: a beam whose own classifier hears a baby / doorbell / alarm louder than speech for half an
  utterance isn't captioned; "Mmmmm." style filler is never a caption. Baby crying is now published + alerted
  (`sounds.publish`, alert min 0.4: a real cry scores 0.44 median, speech/babble ~0; Silero never fires on it).
- `tools/stress_test.py --two-source` (beam leakage -9 and -6 dB, CC0 baby cry in fixtures/sounds):

| scene | streams | caption WER | phantom / duplicate captions | baby crying alert |
|---|---|---|---|---|
| talker at +35 + baby at 180 | 1 processed (before) | 0.10 | 0 | first at +36 (the talker!), 3 more pointing elsewhere |
| talker at +35 + baby at 180 | 2 beams, -9 dB leak | 0.00 | 0 | at 180, none elsewhere |
| talker at +35 + baby at 180 | 2 beams, -6 dB leak | 0.00 | 0 | at 180, none elsewhere |
| two talkers overlapping, +-35 | 2 beams, -9 dB / -6 dB | 0.00 / 0.00 | 0 / 0 | name called hit |

  First beams run without masking: talker+baby WER 0.33 (a line captioned at the baby's direction), two
  talkers 0.40 (one caption mixing both people).
- Real array, beams mode, laptop speaker: lines captioned once each on beam 0 (beam 1 never duplicated); the
  real baby cry -> "Baby crying" at +136 deg (the speaker), no caption from it, captions before/after intact.
- Golden gate now 7 conditions incl. both two-source scenes: 7/7 pass. Found by it: the enrolled-doorbell
  matcher's hit lingers for seconds after the bell, which made the guard drop the next sentence; the speech
  pipeline now gets each window's own classifier scores.
- Dashboard shows the second beam; `--record` in beams mode writes a 2-channel fixture that replays as 2 streams.
- The iPhone app already keys captions by `utt_id` and draws every `sources` entry: no app or contract change.
- `python -m laptop.audio.calibrate` now also fits `aec_to_device_offset_deg/sign` (beam azimuths -> device
  angles) from the same front/right talk, so beams-mode bearings are calibrated with the array. Starting in
  either mode sets the routing it needs (a killed beams run no longer leaves the next processed run on beams;
  checked on hardware). `localize --check` import fixed.

## 2026-10-08 (night): the hearing module on the real XVF3800 (branch laptop/respeaker)

Measurements and what's still unverified: laptop/audio/SPIKE.md.

### Task 1: real-array smoke run
- `python -m laptop --real --offline` works end to end on the array (firmware 2.0.6, 2-ch USB audio): sources,
  speech_start (~0.2 s after the voice), partials every 0.4 s, finals 0.75-0.89 s after speech ends, bearings
  steady (±0.5°). No people were around: speech came from the laptop's own speakers at one spot.
- `tools/speaker_scene.py`: plays the README §4 lines + the enrolled doorbell from a loudspeaker, records the
  real array (`--record` format) and aligns ground truth from the recording itself (envelope correlation:
  Windows playback starts 0.6-2.4 s after the call). Committed as `fixtures/array_tts` (TTS voices only).
  `tools/stress_test.py --scene array_tts` scores it; `--set key=value` overrides config for A/B runs.
- Found: opening an output stream per line clicks ~0.6 s before the audio and the XVF3800 flags clicks as
  speech; the blips bridged pauses and merged utterances in babble (WER 0.31 at +10 dB). One continuous
  buffer fixes the test method (clean gaps; WER 0.04 at +10 dB).
- Found: Silero sometimes fires a few words late on the array's processed audio ("Hey Kori, the" scored
  0.01-0.32 while the array already flagged speech). With DoA gating an utterance now starts where the array
  started hearing the talker (`speech.array_lookback_s` 0.8). Mock 0 dB WER 0.37 -> 0.25; on the array it
  recovered a clipped first word ("If somebody" -> "Can somebody"), other differences are run-to-run noise.
- The XVF3800 flags the doorbell chime as speech with the right direction (81 readings in 4 s), so doorbell
  bearings work on the 2-ch firmware without raw-mic SRP. PANNs scores the real-array doorbell 0.81.
- `--offline` now writes `logs/laptop.jsonl` (+ heartbeats) like a relay run; every log line carries `_sent`
  (laptop send time, log only). `xvf3800.py --dump` no longer crashes on write-only `LED_RING_COLOR`.

### Task 2: the wearer's own voice
- `audio.wearer_bearing_deg` (null = off) + `wearer_ignore_deg` (20): speech from the wearer's mark (README §5,
  within 0.5 m of the array, so one fixed world bearing) never sends speech_start (no typing dots, no buzz),
  never captions and never raises name called. An utterance that only turns out to be the wearer is retracted
  (empty final); one whose onset looked like the wearer but is someone else gets a late speech_start from the
  talker's bearing. The wearer's readings are left out of everyone else's bearing.
- `python -m laptop.audio.calibrate --wearer`: talk 5 s from the wearer's mark, saves the world bearing.
- Unit tests (fake VAD/engine): wearer silent, others unaffected, late start, retraction, config rewrite.
  Not yet done on the rig (needs the wearer on the mark: SPIKE.md "Blocked").

### Tasks 3 + 4: names for whisper, and no captions from noise
Measured with `tools/stress_test.py` (mock scene with TTS babble, and the real-array `array_tts` recording with
the same babble mixed in); every A/B run is in logs/ (not committed). Baseline = before this branch.

| | mock WER clean/+10/+5/0 dB | array WER clean/+10/+5/0 dB | phantom captions (array, babble) | name called at 0 dB (mock / array) | false name alerts | babble only: captions / alerts |
|---|---|---|---|---|---|---|
| baseline | 0.03/0.03/0.03/0.37 | 0.02/0.04/0.06/1.15 | 1/1/1 | miss / miss | 0 | 5 / 0 (array) |
| hotwords, no guard | 0.00/0.00/0.00/0.37 | - | - | hit / - | 5 (mock) | 4 / 5 |
| prompt, no guard | 0.00/0.00/0.00/0.22 | 0.00/0.03/0.52/1.03 | 1/1/1 | hit / hit | 4 (array) | 4 / 4 |
| **now** (prompt + guards) | **0.00/0.00/0.00/0.32** | **0.00/0.02/0.08/0.58** | **0/0/0** | miss / **hit** | **0** | **0 / 0** |

- Task 3: the user's name + aliases (phone `config`, else `names:` / `mock:`) reach faster-whisper as a hint
  (`speech.name_hints`: prompt | hotwords | *_final | off). Both hint modes make whisper write the name back on
  noise in a loop ("Kori. Kori. Kori.", e.g. on the doorbell in babble), which raised false name-called alerts
  (= a triple buzz). Guard: loops collapse to one name; a looped or lone name only raises name called (and a
  lone name is only shown) if a second decode *without* the hint hears it too (extra decode only then).
  Result: WER better everywhere except array +5 dB (0.06 -> 0.08, within run-to-run noise: the same config
  gave 0.10 and 0.08); 0 dB name now hit on the real array, still a coin flip on the mock scene (depends on
  whether the unhinted decode catches the name); 0 false name alerts in every condition.
- Task 4: whisper's inventions ("Thank you.", "Thanks for watching!", lone fillers: `speech.hallucinations`)
  and low-`avg_logprob` segments are dropped unless the array heard a talker > 1 s; looping segments
  (compression ratio > 2.4) always. The logprob rule was first unconditional: it dropped a real sentence
  with laughter underneath, hence the array condition. Diffuse babble (confident readings spread
  > `diffuse_spread_deg` 60°; one talker spreads ~0.5°) is retracted. And with an array attached, segmentation
  is DoA-gated from the first second (`reports_speech`): before, babble at startup went through VAD alone and
  was glued onto the first real line (array +5 dB WER 3.00 on that line).
- New stress condition `babble_only`: 40 s of crowd babble, nobody talking to the wearer, with a *modelled*
  array track (speech flagged 60% of the time from random directions, weak confidence; no crowd recording yet):
  0 captions, 0 alerts (3-4 partials still flash before their final retracts them).

### Task 5: laughter in the tone
- The sounds pipeline hands every window's scores to the speech pipeline; PANNs laughter >= `tone_laugh_min`
  (0.15) during an utterance (or 0.3 s after it) makes the tone `laughing`. Transcribed laughs ("Ha, ha, ha.")
  also count now (the old rule only knew "haha").
- Found: PANNs files laughter under "Snicker" too, which `label_map` missed: added (whole AudioSet Laughter family).
- Measured on real laughter (Wikimedia Commons, CC BY 2.5, not committed): plain speech and babble score <= 0.01,
  real laughs 0.2-0.35 but only in ~6% of 1 s windows (PANNs is weak on short windows of acted laughs).
  Mixed clip through the real models: line + laugh after it -> laughing (0.67); line with a laugh underneath
  -> laughing (0.62); control line -> questioning. With the laugh quiet under the line's tail it stayed neutral.

### Task 9: operator dashboard
- `python -m laptop --real --dashboard` (any mode): one terminal screen with a top-down compass (array front up,
  talker `@`, wearer `w`, alert `!`), input level, VAD / utterance state (talker or wearer?, array seconds), the
  current caption and typing dots, active alerts and sound events, relay link + heartbeat problems, session
  counts. Read-only: it taps sent messages and polls the app; a display error never blocks sending.
  `rich` (13.9.4) for the live view, plain one-line status without it. Checked on the real array (level, DoA).

### Task 10: golden conditions (people-recorded fixtures still to do)
- `python tools/stress_test.py --golden` (`.\tasks.ps1 golden`, `make golden`): mock scene and the real-array
  `array_tts` fixture, clean and +10 dB babble, plus babble only; limits just outside what was measured (WER
  <= 0.05 clean / 0.10 noisy, 0 phantoms, doorbell + name called hit, 0 false alarms, median final lag
  <= 1.3 / 1.6 s; babble only: 0 captions, 0 alerts). Exit 1 on any regression. Now: 5/5 pass.
- The fixtures/README.md sessions with people (two_people, turn_around, ...) could not be recorded (nobody
  around): SPIKE.md "Blocked". Add each one as a GOLDEN row once its meta.json has `lines`.

### Task 11: ODAS gate 1 (raw channels on Windows)
- Fails on the current firmware: the USB descriptors (2.0.6) offer 2 capture channels in every alt setting;
  raw mics need Seeed's 6-channel firmware over DFU (not flashed: shared demo hardware, the team's call).
  Per README §11 built-in DoA stays; nothing measured argues for ODAS (SPIKE.md).

### Task 6: enroll a sound from the array
- `python -m laptop.sounds.enroll doorbell [--seconds 8] [--name N] [--from-wav f] [--no-config]`: records the
  array's processed audio while the sound plays once, trims to the strongest event (presses up to 1 s apart stay
  one sound), scores the template against itself and the room around it, saves `fixtures/sounds/<name>.wav`
  and rewrites only that label's `sounds.enrolled` line (adds the label to `sounds.publish` if needed).
- On the real array (synthetic doorbell from the laptop speaker -> `fixtures/sounds/doorbell_array.wav`): trimmed
  to 3.89 s, matches itself 0.46, the room 0.04.
- Checked against the stress-test audio: in babble the bell's match falls to 0.14-0.23 (0.37-0.46 quiet) while
  speech and babble elsewhere stay <= 0.13, for both the synthetic and the array template. So the suggested
  threshold is just above the room (max(0.12, room + 0.08), at most half the self-match), not half-way to the
  self-match (that gave 0.25 and would miss every babble condition). Config still enrolls the synthetic
  `doorbell.wav` (0.12): enroll the real demo doorbell from the Bluetooth speaker at the booth.

### Task 7: DoA accuracy spike tool + report
- `tools/doa_accuracy.py`: guided prompts (every 45° at 1 m and 1.5 m, 10 s each, optional crowd noise looping
  on a second output), or `--speaker` TTS playback from a loudspeaker with `--auto` trials; per position the
  median bearing and error, spread, share within 15°/30°, settle time and the second beam's ghost/detection
  rate; JSON to logs/doa_accuracy/ and a markdown table for laptop/audio/SPIKE.md.
- Measured (one fixed loudspeaker, quiet): 6 trials at +133°, spread 0.0-0.5°, 100% within 15°, settle
  0.00-0.15 s from the first confident reading, second beam 0-1%. Absolute error, distance, angle and noise
  dependence need a person or a movable speaker: SPIKE.md "Blocked".

### Task 8: run report
- `tools/run_report.py [logs/laptop.jsonl] [--last N] [--json]`: per session (seq restart or > 60 s quiet):
  captions, retractions (and how many only ever showed typing dots), speech_start with no utterance, alerts and
  sound events by kind, tones; latency stats + text histograms for speech_start detection, first words and
  final-after-speech-end (from the log's `_sent`); heartbeat gaps, degraded details, engine switches.
  Smoke run on the real array: speech_start 0.25 s into the utterance audio (= the pre-roll), finals 0.75 s.

## 2026-10-08 (later): stress testing in a simulated loud hall

- `tools/stress_test.py`: mock scene + TTS crowd babble at clean/+10/+5/0 dB through the real models, scored
  against ground truth; plus a DoA-noise simulation of attribution.
- Found and fixed: in babble, VAD never ended an utterance (one 15 s caption across all speakers, finals 5-11 s
  late, 35 deg bearing error). Segmentation now also uses the array (start/end/turn-change/drop). Result at
  +10/+5 dB: same as clean (WER 0.03, final lag 0.75 s, bearing error 0.1 deg, 0 phantom captions).
- Found and fixed: the speech_start bearing (newest reading only) was right 36-68% of the time under DoA noise;
  now a trimmed mean (57-100%), with confidence the phone uses to decide whether to buzz at once. The phone's
  speech_start attribution is provisional until the first utterance update.
- Doorbell: PANNs scored the doorbell 0.26 at +10 dB and 0.05 at 0 dB. Added enrolled-sound template matching
  ("teach HERE your doorbell"): detected at every SNR down to 0 dB, 0 false alarms, 1.5 s instead of 2.5-3 s.
- Belt firmware compiles (arduino-cli, esp32 core 3.3.12): 85% flash, 12% RAM. Fixed: struct types moved to
  belt_types.h (Arduino's generated prototypes come before sketch structs).
- `tests/test_resilience.py`: relay restart mid-session -> laptop reconnects, config replayed, messages flow.

## 2026-10-08: v3 overhaul (README.md is the source of truth)

Everything moved to the README §22 layout; see MIGRATION_PLAN.md for what came from where.

### Phase 0: audit
- `MIGRATION_PLAN.md`: inventory, old -> new mapping, conflicts C1-C22 with README v3.
- Decisions taken (no answers were needed from the hardware owners for these):
  in-tree WIP committed as-is before any move; ar_here copied into `legacy/`; Ranger left outside the repo
  (`reference/` documented, gitignored, nothing copied); `doa.beams` -> optional `sources[1]` behind
  `audio.builtin_beams_as_sources` (off); `sound_doa` -> used inside the laptop to fill `sound_event` /
  `alert` bearings. No contract shape was changed for either.

### Phase 1: structure and contracts
- `README NEW.md` -> `README.md`; v1 plan -> `legacy/HERE_PLAN_v1.md`.
- `git mv`: `audio_here` -> `laptop/audio` (`respeaker_probe.py` -> `xvf3800.py`, `__main__.py` ->
  `builtin_doa.py`), `speech_here` -> `laptop/speech`, `sound_here` -> `laptop/sounds`,
  `here_common` -> `laptop/{geometry,client}`, `ios_here` -> `ios`. Laptop-side vision, the v1 hub, the
  video/binary-audio tools and the v1 iPhone transport -> `legacy/`.
- `contracts/`: JSON Schemas for all 14 v3 types generated from `build_schemas.py`; Python dataclasses with
  validation; Swift `Codable` mirror (compiled into the app); TypeScript types; one example per type; tests
  in Python and `tsc`, and Swift decoding tests in the iOS target.
- Root and per-component `CLAUDE.md`.

### Phase 2: relay
- `relay/relay.py`: token auth (401 before upgrade), sessions, routing by `hello.subscribe` with README §12.10
  role defaults, latest `config` replayed per session, latest-only outbox for `sources`/`scene`/`heartbeat`
  and a bounded event outbox (no buffering of stale data), `TCP_NODELAY`, `GET /health`, JSON-lines log.
- `relay/Caddyfile`, `here-relay.service`, `DEPLOY.md` (Vultr Atlanta, Ubuntu 24.04, sslip.io or a domain).
- `tools/relay_tap.py`, `tools/fake_events.py` (README §4 moments at +35 / -35 / 180 deg).

### Phase 3: laptop
- `python -m laptop --real | --mock | --replay`, `--record`, `--offline`.
- ReSpeaker code wrapped, not rewritten: `ReSpeaker` (DirectionSource + AudioSource) keeps the v1 DoA poll,
  beams, LED ring and SRP-PHAT; `xvf3800.py` still runs standalone; `calibrate.py` now writes `config.yaml`.
  `OdasSource` stub. In-process audio fan-out to speech, sounds and the recorder.
- Speech: `speech_start` at VAD onset with the current direction; streaming whisper (0.4 s partials, local
  agreement); utterance bearing = circular mean of speech sources, spread, overlap; loudness relative to the
  room's normal talking level; tone rules; engine behind `Transcriber` with a Scribe stub and automatic fallback.
- Name called: fuzzy + sounds-alike matching ("Kori" = "Cory" = "Corey"), once per utterance, on committed words.
- Sounds: PANNs doorbell on 1 s windows every 0.5 s -> `sound_event` + `alert` with raw-mic SRP or recent-sources
  bearing. Alerts deduped (4 s, 45 deg) and ended (`active: false`) 10 s after the last detection.
- One `laptop/config.yaml`; secrets from `laptop/.env`. Each part supervised (a crash restarts that part only).
- Fixed on the way: emoji in console output crashed Windows cp1252 consoles; tone was "shouting" for everything
  (loudness was measured against the room floor); 0.1 s noise blips left typing dots on the phone.
- `fixtures/mock_scene` (synthetic, committed) + `tools/make_mock_scene.py`; `tools/replay.py`; `check_gpu.py` ported.

### Phase 4: iPhone app (`ios/`, XcodeGen, iOS 26) - written, not yet compiled
- Camera (ultrawide, landscape via `AVCaptureDevice.RotationCoordinator`, 30 fps, late frames dropped),
  Vision faces (detect every 6 frames, track in between, Person N labels with fixed colors, relabel within 5 s),
  quaternion heading (v1 HeadingProvider kept), Projector + Frames (Vision rect -> sensor rect -> layer).
- Attribution (README §13, ported from the v1 fusion logic) including the turn-around: an off-camera caption
  attaches to the face once it comes into view.
- Belt: CoreBluetooth central, BeltPolicy (front 30 deg silent, 5 s per person, 1.5 s non-alert gap),
  on-screen belt indicator even when BLE is down.
- UI: Liquid Glass captions (header dot + Person N + tone chip, dim pending words, tail, typing dots), rings,
  top-right sound panel with direction ring and xN, off-camera chevrons, edge glow, status pill, debug overlay,
  focus and belt-only modes, thermal step-down, Reduce Motion. Settings for everything in README §14.
  Simulated faces for the Simulator. SwiftUI previews. XCTests for geometry, attribution, belt, contract.

### Phase 5: belt firmware - written, not yet compiled
- `belt/firmware/here_belt`: BLE peripheral, 6-byte packet, README §16 patterns timed on-device with priority
  interruption, LEDC PWM per motor with gain calibration, WS2812B status, serial bench commands.
- `belt/README.md`: wiring diagram (ULN2803 COM to motor supply, shared ground, separate bank ports).

### Phase 6: tools, fixtures, checks
- `Makefile` + `tasks.ps1`: relay, laptop-mock, laptop-real, fake-events, tap, test, schemas, mock-scene, check.
- `tests/test_smoke.py`: relay + fake_events + headless phone receives the core moments in order, all valid.
- `tests/test_consistency.py`: belt UUIDs/pattern ids firmware == app, routing defaults everywhere, app
  thresholds == README, nothing references Ranger.
- `fixtures/README.md`: how to record the README §20/§21 sessions.

Verified: 110 Python tests pass; live run relay + `laptop --mock` (real whisper + PANNs) + `relay_tap --validate`:
all moments arrive, 0 contract violations. Not verified: anything that needs Xcode, an Arduino toolchain,
the ReSpeaker, the phone or the belt.
