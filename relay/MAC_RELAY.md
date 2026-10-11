# Handoff: run the relay on the Mac

Written 2026-10-10. For whoever sets this up on the Mac (person or AI) with no other context.

## Why

The phone can't connect to the relay on the Windows demo laptop. Two things were checked:
- **UCF_Guest Wi-Fi:** nothing reached the laptop.
- **A phone hotspot with both devices on it:** still nothing. `http://<laptop-ip>:8080/health` doesn't load in
  the phone's Safari, but loads on the laptop itself, and the relay logs show no attempt from the phone at all.

The laptop is a managed corporate machine running **CrowdStrike Falcon**, which blocks incoming connections no matter what the
Windows Firewall settings say. Don't try to work around it. **Outgoing** connections from the laptop work (Scribe
captions keep flowing).

So the relay moves to the Mac. The laptop and the phone both connect **out** to it, as they would to Vultr
(README §7). Nothing connects in to the laptop.

```
ReSpeaker -USB-> Windows laptop (GPU: whisper/Scribe, sound models, everything heavy)
                      |  outbound ws://<mac-ip>:8080/ws  (role laptop)
                      v
                 Mac: python -m relay   (routes JSON only: stdlib + websockets, no GPU, no models)
                      ^
                      |  outbound ws://<mac-ip>:8080/ws  (role phone)
                 iPhone app
```

The relay is `relay/relay.py`, one file. Its only dependency is `websockets>=14` (`relay/requirements.txt`).

## 1. Mac: start the relay

All three devices (Mac, Windows laptop, iPhone) on the **same hotspot** (or any network that lets devices reach each other).

```bash
cd <repo> && git pull
python3 -m venv .venv-relay && .venv-relay/bin/pip install -r relay/requirements.txt
caffeinate -i .venv-relay/bin/python -m relay --host 0.0.0.0 --port 8080     # keep this terminal open
```

- `--host 0.0.0.0` is required. The default `127.0.0.1` only accepts connections from the Mac itself.
- **Token:** with no `HERE_RELAY_TOKEN` set, the relay runs with auth OFF and accepts any token. That's fine on a
  private hotspot. To turn auth on, run `HERE_RELAY_TOKEN=<token> .venv-relay/bin/python -m relay ...`, using
  the same value as `HERE_RELAY_TOKEN` in the Windows laptop's `laptop/.env`. Pass it in person, never through git or chat.
- If macOS asks whether Python may accept incoming connections, click **Allow**. If you clicked Deny earlier, go to System
  Settings > Network > Firewall > Options, set python3 to Allow (or turn the firewall off for the test).
- `caffeinate -i` stops the Mac from sleeping while the relay runs.

Get the Mac's IP and check the relay answers:

```bash
ipconfig getifaddr en0                       # e.g. 172.20.10.4 on an iPhone hotspot
curl http://$(ipconfig getifaddr en0):8080/health
```

The answer is `{"ok": true, "sessions": {}, ...}`.

## 2. Windows laptop: publish to the Mac

Run in PowerShell, from the repo root on the laptop. The environment variable overrides `laptop/.env`, and
`--cloud` means "no local relay; use `HERE_RELAY_URL`". `demo.py` refuses to start if the relay is unreachable, so
it doubles as a reachability check.

```powershell
.venv\Scripts\python.exe tools\demo.py down
$env:HERE_RELAY_URL = "ws://<mac-ip>:8080/ws"
.venv\Scripts\python.exe tools\demo.py up --source real --cloud
.venv\Scripts\python.exe tools\demo.py status --cloud        # same PowerShell window (it reads the variable)
```

`status` should list `laptop` (and `memory`) in session `demo`. The laptop loads its models first, about 35 s.
On the Mac, the relay terminal prints `+ laptop joined session 'demo'`.

## 3. iPhone

1. Safari: open `http://<mac-ip>:8080/health`. It must load. If it doesn't, the network is blocking it, not the app.
2. App: Settings > Relay > URL `ws://<mac-ip>:8080/ws`, session `demo`, token: anything with auth off, the real
   one with auth on.
3. If iOS asks for **Local Network** access, Allow. If it never asked and nothing connects: iPhone Settings > Privacy
   & Security > Local Network > HERE on. (`ios/project.yml` already has `NSAllowsLocalNetworking` for plain
   `ws://` and the usage string.)
4. The Mac's relay terminal prints `+ phone joined session 'demo'`.

## 4. Check it end to end

- Mac, in a second terminal (repo venv with the laptop requirements, or any machine with the repo):
  `python tools/relay_tap.py --relay ws://<mac-ip>:8080/ws --session demo` prints every message going through.
  Check `python tools/relay_tap.py --help` for the exact flags.
- Talk in front of the array: a caption on the phone. Talk behind it: off-camera notification, belt buzz.
- Without the array: on the laptop, `demo.py up --source fake --cloud` (scripted core moments) or `--scene all`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `demo.py up --cloud` says UNREACHABLE | The relay isn't on `0.0.0.0`, the macOS firewall blocks it, the laptop is on another network, or the IP is wrong. Try `curl http://<mac-ip>:8080/health` from the laptop. |
| Laptop joins, phone can't load `/health` | The phone is on another network, or the network isolates clients (guest Wi-Fi). Use a hotspot. |
| `/health` loads on the phone but the app says rejected | Auth is on and the token differs. Run the relay without a token, or copy the laptop's. |
| `/health` loads but the app never connects | The Local Network permission is off (step 3.3). |
| Hotspot IPs change | Re-run `ipconfig getifaddr en0` after every reconnect and update the laptop's variable and the phone. |

## Latency

The relay on the same machine measured p95 1 ms. A Mac relay on the same hotspot adds two local Wi-Fi hops (estimated
5-20 ms, not measured). Vultr is estimated at 25-50 ms on good Wi-Fi and 80-150 ms on a cellular hotspot. Measure
the real number with `python tools/latency_probe.py --relay ws://<mac-ip>:8080/ws` (`.\tasks.ps1 latency`).
Captions (about 1 s of transcription) won't notice. The turn-around buzz (`speech_start`, 250 ms budget, README §10)
is the only path where it matters.

## After the hackathon setup

- **Vultr** (`ADIL_TODO.md` §2, `tools/deploy_vultr.py`) is the same idea with a `wss://` address that works on any
  network. The new app build has a primary + fallback relay URL with automatic failover (`IOS_INTEGRATION.md`
  "Two relays, one stream"): Mac relay primary, Vultr fallback.
- The phone build that's installed predates today's merges. Build `main` on the Mac first: `cd ios && xcodegen
  generate && xcodebuild -scheme HERE -destination 'platform=iOS Simulator,name=iPhone 17' test`, about 147 tests.
  None of today's Swift changes have been compiled. Fix any compile errors before installing.
