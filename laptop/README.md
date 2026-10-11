# Laptop: the hearing module (Windows)

`python -m laptop` reads the ReSpeaker XVF3800 (directions + processed audio), transcribes speech,
detects the doorbell and name called, and sends contract v3 messages to the relay (README §6). No audio
or video leaves the laptop. Verified on the demo laptop: RTX 5000 Ada, NVIDIA driver 538.18.

```
ReSpeaker ─► laptop/audio  (built-in DoA -> sources; processed 16 kHz audio, in-process)
                 ├─► laptop/speech  (Silero VAD + faster-whisper: speech_start, utterance, tone, name_called)
                 └─► laptop/sounds  (PANNs CNN14: doorbell sound_event + alert)
             laptop/client ─► wss relay ─► phone
```

## 1. Python 3.12 environment

Python 3.12, not 3.14: CTranslate2, Silero and PANNs are most reliable there.

```powershell
py install 3.12                      # Python install manager; or the python.org installer
py -V:3.12 -m venv .venv             # at the repo root
.\.venv\Scripts\python -m pip install --upgrade pip
```

## 2. PyTorch with CUDA 12 (install this first)

```powershell
.\.venv\Scripts\python -m pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
.\.venv\Scripts\python -m pip install -r laptop\requirements.txt
.\.venv\Scripts\python -c "import torch; x=torch.randn(9,9,device='cuda'); print(torch.cuda.get_device_name(0), (x@x).shape)"
```

**Why cu121 and torch 2.5.1:** driver 538.18 supports CUDA 12.2. Current torch wheels (cu126+) report
`torch.cuda.is_available() == True` but fail on the first allocation with `CUDA error: CUDA-capable
device(s) is/are busy or unavailable`. cu121 matches the driver. After a driver update to 560+, newer wheels work.

## 3. faster-whisper DLL errors

`RuntimeError: Library cublas64_12.dll is not found or cannot be loaded`: CTranslate2 needs cuBLAS/cuDNN,
which the torch wheel ships. **`import torch` before `faster_whisper`** and they're already loaded;
`laptop/speech/transcriber.py` does that. Also pass numpy audio to `transcribe`, never file paths (the
installed PyAV rejects an argument faster-whisper passes when decoding files).

Laptops without an NVIDIA GPU still run: speech falls back to CPU `base.en` and the heartbeat says
`degraded`. Fine for development, not for the demo.

## 4. Secrets

```powershell
copy laptop\.env.example laptop\.env     # then fill in HERE_RELAY_URL / HERE_RELAY_TOKEN (never committed)
```

## 5. ReSpeaker XVF3800

- **Driver:** Windows uses the built-in USB audio driver for the audio, and the "reSpeaker Control"
  interface uses WinUSB by default (pyusb + libusb-package talk to it). If `python laptop\audio\xvf3800.py`
  says *not found* while the array is plugged in, install WinUSB on the control interface with Zadig.
- **Firmware:** either USB firmware works. Raw-mic sound direction (SRP-PHAT for the doorbell, which the
  speech-gated built-in DoA often misses) needs Seeed's **6-channel** firmware: ch 0/1 processed, ch 2-5 raw
  mics. Flash with Seeed's DFU guide (`xmos_firmwares/dfu_guide.md` in respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY);
  `python -m sounddevice` should then list the ReSpeaker with 6 inputs. With 2 channels, doorbell bearings
  come from recent `sources` and the heartbeat says so.
- **Mic capture from a sandboxed process returns digital silence.** Run from a normal terminal.

Only one program can use the array's control interface at a time (stop `python -m laptop` first):

| Command | What |
|---|---|
| `python laptop\audio\xvf3800.py` | standalone live compass readout (`--log f.jsonl`, `--dump` every register) |
| `python -m laptop.audio.calibrate` | **array forward + rotation direction** -> `laptop/config.yaml` (README §8; once) |
| `python -m laptop.audio.calibrate --wearer` | **the wearer's own voice**: wearer talks 5 s from their mark -> `audio.wearer_bearing_deg`; their speech is never captioned and never buzzes the belt (every new rig layout) |
| `python -m laptop.audio.ring --follow` | the ring lights toward the device's DoA; tune `ring_led0_deg` / `ring_led_step` |
| `python -m laptop.audio.localize --check` | fit `aec_to_device_*` (beam/SRP angles -> device angle) |

## 6. Run

| Command | What |
|---|---|
| `python -m laptop --real` | ReSpeaker -> relay (the demo) |
| `python -m laptop --real --record hall_test` | ...and save `fixtures/hall_test/` (audio.wav, sources.jsonl, meta.json) |
| `python -m laptop --mock` | synthetic scene (`fixtures/mock_scene`, README §4 moments) through the real models |
| `python -m laptop --mock --mic` | laptop mic + a direction slider window |
| `python -m laptop --replay fixtures/<name>` | a recorded session in real time (`--loop`, `--speed`) |
| `... --offline` | print messages instead of sending them (no relay needed) |
| `... --dashboard` | live operator view: direction compass (+ wearer and alert marks), input level, VAD / utterance state, caption, alerts, relay + heartbeat (`rich`) |
| `python -m laptop.sounds.classifier --wav f.wav` | what the doorbell classifier hears in a recording |
| `python tools\check_gpu.py` | **pre-demo check:** CUDA, whisper + PANNs speed, ReSpeaker, relay |

Every option: `python -m laptop --help`. Tunables: `laptop/config.yaml`. Logs: `logs/laptop.jsonl`
(every message sent). Each part (speech, sounds, directions, relay) is supervised: a crash is logged and
that part restarts; the others keep running.

## 7. What it sends (README §12)

| Message | When |
|---|---|
| `sources` | 20 Hz while speech/energy is present, otherwise on change or every second |
| `speech_start` | at voice onset, with the newest speech direction (fast path, before any words) |
| `utterance` | every ~0.4 s while someone talks (`text` = agreed words, `pending` = provisional), then `final` |
| `sound_event` | doorbell (other labels in `sounds.publish`) with a bearing |
| `alert` | doorbell, name called (fuzzy + sounds-alike match on the phone's `user_name` / aliases); `active: false` 10 s after the last detection |
| `heartbeat` | every 2 s: `engine` (faster_whisper / scribe_realtime), `doa_source`, problems in `detail` |
| `clock_pong` | answers the phone's `clock_ping` |

Measured on the demo laptop: whisper partial decode 264 ms per 10 s of audio, final 380 ms; PANNs 5 ms per
window; 3.2 GB of GPU memory with both models loaded.

## 8. Loud-hall behaviour (tools/stress_test.py)

- In crowd babble Silero hears speech constantly, so segmentation also uses the array: an utterance only
  starts while the array hears a talker, ends when the array stops hearing them, splits on a > 40 deg turn
  change, and is dropped if the array barely heard it (`speech.endpoint_use_doa`).
- `speech_start` uses a trimmed mean of the onset readings, and its `confidence` drops when they disagree;
  the phone buzzes immediately only when it's confident, otherwise after the first utterance update.
- Doorbell: **enroll the exact sound** (`sounds.enrolled`, laptop/sounds/enrolled.py). The template match
  catches it down to 0 dB SNR babble where PANNs scores ~0.05. Replace `fixtures/sounds/doorbell.wav` with the
  file the Bluetooth speaker plays.

| condition | mock WER | real-array WER | phantom captions | final lag | doorbell | name called |
|---|---|---|---|---|---|---|
| clean | 0.00 | 0.00 | 0 | 0.7-0.9 s | hit | hit |
| babble +10 dB | 0.00 | 0.02 | 0 | 0.75-1.2 s | hit | hit |
| babble +5 dB | 0.00 | 0.08 | 0 | 0.7-1.2 s | hit | hit |
| babble 0 dB | 0.32 | 0.58 | 0 | 0.8-1.2 s | hit | mock miss, array hit |
| babble only (nobody talking) | 0 captions, 0 alerts | | | | | |

"Real-array" = `fixtures/array_tts` (TTS from a loudspeaker through the XVF3800, `tools/speaker_scene.py`)
with the same babble mixed in. Before/after for each change: CHANGELOG.md (2026-10-08 night).
Synthetic babble isn't the hall: confirm with real recordings (fixtures/README.md).

## 9. Two sources at once (beams mode)

`audio.capture: beams` (default; `--no-beams` for one processed stream) routes the XVF3800's two focused beams
to the two USB channels: a talker in front and a baby crying behind (or two people talking over each other)
each get their own captions, sounds and direction. Each beam's caption audio is time-frequency masked against
the other beam, one talker on both beams is captioned once, and a sound is reported once from the beam that
hears it best. `python tools/stress_test.py --two-source` scores it; `--golden` includes it.

## 10. ElevenLabs Scribe (at the event)

Set `speech.engine: scribe_realtime` in `config.yaml` and `ELEVENLABS_API_KEY` in `laptop/.env`, then wire
the WebSocket in `ScribeRealtimeTranscriber` (laptop/speech/transcriber.py). `FallbackTranscriber` already
switches to whisper automatically on any error or an answer slower than 1.5 s, and the heartbeat reports
which engine is live.
