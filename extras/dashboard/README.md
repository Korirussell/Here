# extras/dashboard: HERE World

The screen judges look at while the iPhone shows the wearer's view. It has three modes:
- **Live world:** where each sound is, what HERE thinks it is, who's speaking, and what it surfaced.
- **Memory:** what HERE remembers from the day.
- **Replay lab:** what it would have surfaced under different attention settings.

It's never on the live reaction path (SPEC §17): the core demo works with it closed.

```powershell
python -m relay                                   # local relay on :8080 (or use the Vultr one)
python -m extras.dashboard                        # http://127.0.0.1:8780; relay from laptop/config.yaml + laptop/.env
python tools/fake_events.py --loop                # scripted core moments, no audio
python -m pytest extras/dashboard                 # seed shapes, replay policy, Ask fallback, relay -> page
```

Options: `--relay ws://host:8080/ws`, `--session demo`, `--memory http://127.0.0.1:8790`, `--port 8780`
(or `HERE_MEMORY_URL`, `HERE_DASH_PORT`). Every tunable is in `settings.py`.

## How it's wired

```text
relay --(role dashboard, subscribe *)--> server.py --(/live WebSocket)--> page
memory API :8790 (§5.3) --(HTTP)--> server.py /api/* --> page     (falls back to demo_data/*.json)
page /dash/demo?moment=2 --> server.py starts tools/fake_events.py against the same relay
```

- **The relay token stays in the Python process.** The page connects only to `127.0.0.1:8780/live` and never
  sees a URL with a token in it. `/dash/config` holds nothing secret.
- **Offline at the booth:** three.js r160 and the Geist fonts are vendored in `static/vendor/` (MIT / OFL; see its README). No CDN at runtime.
- **Contract:** the page reads v3 messages (`contracts/ts/contract.ts`) unchanged and ignores unknown types.
  `/live` wraps each one as `{k: "msg", rx, m}`, where `rx` is when the dashboard got it. That gives the latency panel the send → receive lag on one clock.
- **Bounded everywhere:**
  - the server keeps 400 events of history and newest-only `sources`/`scene`/`heartbeat`;
  - each tab has a 512-message outbox;
  - the page caps the stream at 120 rows, captions at 40, sounds at 200 and the inspector store at 600 ids.

## Live world

**The look:** quiet chrome (hairlines, Geist, accent-filled primary actions) around one loud thing: the orb.
Colour only carries meaning (blue speech, amber sound, violet `unknown`, red alert), and every kind also has a
shape and a text label.

**Colours (`C`, the half-moon button in the header):** Light / Dark / System (follows the OS, live), and five
themes: **Mono** (the original black-and-white look, the default), **Ocean**, **Forest**, **Sunset**, **High
contrast** (AAA text). Each has a light and a dark variant. Themes change the chrome, surfaces, accent and the orb;
the semantic colours keep their meaning in every theme. Remembered per browser, applied before first paint (no flash).
- Every colour is a token in `static/themes.css`; nothing else names a colour. Scripts that draw (the orb, the
  timeline, the architecture) read tokens through `theme.js` `tok()` and redraw on the `here:theme` event; the orb
  recolours in place, without a reload.
- `tests/test_themes.py` checks WCAG contrast for every theme × mode (body text ≥ 4.5:1, AAA in High contrast;
  semantic marks ≥ 3:1 and distinct from each other), that a dark block overrides everything its light block sets,
  and that no colour literal lives outside the token files. Adding a theme = one light block + one dark block +
  its swatch in `themes.css`, and its id in `theme.js` and the pre-paint script in `index.html`.

- **The orb** (`static/orb.js`, three.js): about 5,000 points around the array in the world frame, with front away from the camera.
  - The field **swells and takes the colour of whatever HERE hears, in its direction**.
  - A beam runs to a 3D marker: a sphere for speech, a spinning diamond for a sound, a wireframe "?" for `unknown` (never hidden), and a pulsing pillar plus floor wedge for an alert.
  - New sounds ripple across the floor. The phone's `scene` draws a "wearer view" cone.
  - Drag to orbit, scroll to zoom, double-click to reset; it drifts back home on its own.
- **The headline:** a plain sentence of what HERE understands right now ("Doorbell behind you", "Maya is speaking front-right"), plus the live words.
  - Directions are relative to the wearer when the phone sends `scene`; otherwise they're relative to the array.
- **The pipeline strip:** ReSpeaker → speech engine → sound model → attention model → relay → iPhone → memory → trends.
  - Each node names the provider reported by the latest message, and pulses when one passes through.
  - Green = the sponsor path is live; amber = that stage is running on a fallback; grey = not connected.
- **The rail:**
  - live captions: who (the phone's `attribution`, else "Voice N": one number per direction, held 10 min), direction,
    tone, engine, and **what HERE surfaced**: `important → card + belt`, `notice → small cue` (the phone's mapping in
    from `perception_enrichment` or else the rules); pending words in grey;
  - alerts, with direction rings;
  - latency: per-type median and p95 laptop → dashboard, and onset → first words;
  - the event stream.
- **System menu** (header): relay, laptop heartbeat, captions, direction source, attention model, iPhone and memory.
  - With only `fake_events` running, it says **Scripted demo**, the banner says so too, and nothing credits the ReSpeaker.
- **Event inspector:** click any caption, alert, stream row, 3D label or marker. It shows **which provider produced which field**,
  one row per stage for every event. Each row is a named provider, an amber **fallback** that says what is missing, or a
  dashed **not connected**; never a blank:
  - direction: ReSpeaker (only with a fresh laptop heartbeat), else Scripted or "not connected";
  - caption: ElevenLabs Scribe, or whisper (fallback: ElevenLabs not in use);
  - class: Hugging Face, Enrolled sounds, or PANNs (fallback: Hugging Face classifier not in use);
  - attention: the Hugging Face model, or the rules (fallback), with "rules would have said X";
  - context: Gemini, or "not connected" / "no note for this event";
  - who: the iPhone, or "not connected · voice told apart by direction";
  - transport: Vultr, or the local relay / local replay (fallback);
  - memory: Neon, or local SQLite / seeded JSON (fallback);
  - trends: Databricks, or precomputed (fallback).

  It also lists linked ids, timings, and every related message (click one for the raw JSON).

## Memory and Replay lab

- **Seeded fallback:** `/api/*` tries the memory service's API first. If it doesn't answer, the server falls back to `demo_data/` (or `extras/memory/seed/*.json` when A3 ships those). Every reply carries `_source: "api" | "seed"`, and the page labels seeded data **SEEDED**. A seed reply given because the API failed also carries `_fallback_reason` (e.g. `memory API slow (>4 s)`). One slow answer doesn't mark the API down: only a second timeout in a row, a refusal or a connection error skips it for `MEMORY_RETRY_S`.
- **Rebuilding the seed:** `demo_data/make_day.py` rebuilds `day.json`, `ask.json` and `insights.json` deterministically.
  - The day has 206 events in the canonical v3 shapes, plus §5.3 metadata (`person_label`, `location_id`, `seeded`), over six places.
  - `pytest` validates every event against the schemas.
- **Memory tab:**
  - a compressed timeline: places with a noise summary, a sounds lane, background talk, and one lane per recurring person;
  - Ask HERE: canned answers for the demo questions, keyword search otherwise, with evidence that highlights on the timeline and opens the inspector.
    A seeded fallback says why (`_fallback_reason`, e.g. "memory API slow"); an answer Gemini phrased says "phrased by Gemini";
  - place headers use the longest wording that fits the box ("Commute: University Ave crosswalk" → "Commute" → "Com…"); hover for the full text;
  - things you may have missed (from behind you);
  - insights, labelled Databricks or Precomputed;
  - important moments.
- **Replay lab:** pick a place; set the sliders (sensitivity, crowd filtering, speech priority, hazard priority, focus mode) or a preset. You see actual vs alternative counts, by-category bars, and a per-event list with the reason for each decision.
  - When the memory service's `/api/replay` is down, `fallback.replay` decides. It's deterministic, built on the stored `perception_enrichment` attention, and falls back to the docs/ATTENTION.md tier-3 rules.
  - Priority ≥ 2 alerts and `urgent` are always kept. A suppressed `unknown` stays ambient with its direction.

## Demo keys

| Key | Action |
|---|---|
| `T` | **Judge tour**: ~80 s guided auto-play (LIVE → MEMORY → REPLAY LAB); any key or click takes over |
| `S` | Play all three core moments (through the relay; with no relay, as a local replay) |
| `1` `2` `3` | Play one core moment |
| `L` | Loop the moments |
| `X` | Stop the scripted scene |
| `M` | Open the seeded day |
| `A` | Ask a known question |
| `P` | Open the Replay lab |
| `H` | How it works (the architecture, lit live) |
| `F` | **Moment view** (also the header's **Moment** button; remembered per browser) |
| `B` | **Captions sidebar**: hide / show it (also the toggle at the stage's top right; remembered per browser). While it's hidden the orb takes the room and a dot on the toggle marks what arrived: red for a new alert, else blue for a new caption |
| `C` | **Colours**: open the theme and light / dark picker (arrow keys choose, Esc closes) |
| `D` | Cycle Light → Dark → System |
| `R` | Reset the view |
| `Shift+Esc` | Panic: stop everything, reset, back to Live |
| `?` | Show the keys |

The same actions are in the header's **Run demo** menu, plus one button per scene in `fixtures/events/*.jsonl` and
**Play the scenes locally**.

## Moment view (`F`)

What judges ask is "how fast is it?" and "what does each part do?". The Moment view answers both for the event that
just happened, on Live, without a click. Turn it on before judging starts; it survives a refresh.

- **What it shows:** the latest important moment, taken apart as a vertical timeline. Each step has its time, what
  happened, and the provider that did it (the same names as the inspector):
  - speech: voice began (direction: ReSpeaker or Scripted) → voice detected, phone told → first words (ElevenLabs or
    whisper) → final caption → who (iPhone) → name called;
  - sounds: sound began → classified (Hugging Face, enrolled template or PANNs) → label upgraded → alert;
  - then attention (the Hugging Face model or the rules, and what the wearer gets), Gemini's context (or why there is
    none: not connected / paused, quota exhausted), and memory.
- **Times, honestly:** each step's time is when its message reached this screen, from the audio onset the laptop
  reports (else its first message), on the demo laptop's one clock. That's the same basis as the latency panel.
  Phone and belt lines say what the phone is told; they're never shown as measured. The header says **live**,
  **scripted** or **replay · not timed** (a local replay is timed by this server, so it shows no times).
- **Which moment:** an alert or hazard outranks important speech, which outranks ordinary speech and sounds. A
  higher rank takes over at once; an equal rank waits until the shown moment finished and has been readable 2.5 s
  (or 6 s if it never finishes); anything may replace it after `settings.PAGE.moment_hold_s` (8 s). A voice that
  becomes a name call restarts its hold. The logic is `static/moment.js` (pure, tested under node).
- Click the panel for the full inspector. With no moment yet, the usual headline stays.
- The **Sound → wearer** card (direction latency) stays on Live in both views: **live** only with a fresh laptop
  heartbeat, otherwise the laptop's measured real-array number (`settings.PAGE.latency_reference`), labelled **measured**
  (and **now scripted** while `fake_events` runs).

## Judge tour (`T`)

For when a judge walks up mid-sentence. It runs about 80 s and always plays as a labelled local replay, so it needs no relay:

1. LIVE plays a voice behind, then a doorbell, then a name called (`settings.LOCAL_MOMENTS["tour"]`), with captions saying what the glasses and belt do.
2. MEMORY asks a known question; the evidence lights up on the timeline.
3. REPLAY LAB states the real before/after numbers for one segment.
4. Back to LIVE.

Any key or click hands control back and stops the scene. Also in the Run demo menu.

## How it works (`H`) and the architecture picture

`static/architecture.js` draws the one-page architecture as a pure function:

> ReSpeaker → laptop (direction, ElevenLabs / whisper, Hugging Face / PANNs, attention, Gemini) → Vultr relay (text and bearings, no audio) → iPhone + belt + this screen, and → Neon memory → Databricks.

- **The view:** lights each node live / fallback / idle / not connected, from the same provider logic as the inspector.
  - When the memory API answers, its `/api/status` adds the relay's own client list and message rates.
  - On Vultr, that includes whether the laptop's mirror is connected.
- **For the submission:** `architecture.svg` and `architecture.png` here.
- **Regenerate:**
  - `node extras/dashboard/make_architecture.mjs`
  - PNG: `chrome --headless=new --window-size=1320,876 --force-device-scale-factor=2 --screenshot=extras/dashboard/architecture.png extras/dashboard/architecture.svg`.

## Testing without an iPhone

`python -m extras.dashboard.fake_phone --relay ws://127.0.0.1:8080/ws --session demo` joins as role `phone` (relay, session and token from the environment or `laptop/.env`, like `tools/demo.py`) and sends what the app sends:
- `heartbeat` with `thermal`;
- `scene` with Person 1 at +35° and Person 2 at -35° (where `fake_events` puts them);
- `attribution` by bearing on each final.

Options: `--no-scene` (Settings › Send scene off), `--camera-error`, `--thermal`, `--people "Name:bearing,..."`.

## Accessibility

- **Keyboard:** every control and every clickable row (captions, alerts, events, timeline marks, evidence, replay rows) is reachable by keyboard.
  - Enter or Space opens a row.
  - The inspector takes focus and returns it on Esc.
  - "Skip to live captions" link; tabs carry `aria-selected`.
- **Focus:** always visible (3 px ring).
- **Reduced motion:** `prefers-reduced-motion` (or `?reduce-motion`) stills the orb.
  - Nothing moves except markers appearing at their direction.
  - CSS animations are off.
- **Contrast:** text is ≥ 4.5:1 (WCAG AA) in every theme and mode, ≥ 7:1 in **High contrast**; checked by `tests/test_themes.py`.
- **Size:** captions scale with the screen. On a 1920 px demo monitor the live words are ~31 px and the newest caption ~26 px, readable at 2 m.

## Memory API through a proxy

the memory service's API wants the relay token on proxied (Vultr) requests. The server sends it as a Bearer header to non-local memory URLs only; it never reaches the page. A 401 shows up as "memory API refused the token".

## When the relay is gone (booth Wi-Fi)

The dashboard never needs the relay to put a demo on screen:

- **No relay reachable:** the page says so ("Press S to play the recorded scenes here"). Run demo, `S`, `1`-`3`, `L` or
  a scene button plays the laptop's `fixtures/events` scenes **from the dashboard server itself**. Times are rebased onto now
  and ids are fresh per playback; the core moments map onto scenes in `settings.LOCAL_MOMENTS`.
- **The relay drops mid-demo:** the same request carries on as a local replay by itself.
- **The laptop is live:** while a fresh heartbeat from src `laptop` is on the relay, Run demo, `S`, `1`-`3` and the scene
  buttons also play locally (the reply says why), so a stray key never puts scripted captions or buzzes on the
  wearer's phone. `/dash/demo?...&force_relay=1` sends a scene to the relay anyway, on purpose.
- **Always labelled:** the banner says "Local replay ... Not live", the status says **Local replay**, and the transport
  reads "Local replay · fallback". Local messages stay out of the latency panel and the inspector's timings.
- **Memory and Replay lab** need no network at all (seeded JSON).
