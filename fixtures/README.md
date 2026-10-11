# Fixtures: recorded sessions (SPEC §20, §21)

A fixture is a folder the laptop can replay as if the ReSpeaker were live:

```
fixtures/<name>/
  audio.wav       16 kHz mono s16le: the array's processed output, aligned to the clock
  sources.jsonl   {"t": seconds_from_start, "sources": [{id, bearing_deg, energy, speech, confidence}]} per line
  meta.json       name, mode, array calibration at record time, duration, notes
```

`mock_scene/` is synthetic (Windows text-to-speech + a synthesized doorbell, `tools/make_mock_scene.py`)
and is committed: it's what `python -m laptop --mock` plays. Real recordings stay local unless the team
decides to commit them (they're voices of people who agreed to be recorded; SPEC §25).

## Event scenes (`fixtures/events/`)

`fixtures/events/<name>.jsonl`: one canonical v3 message per line (what the laptop sends, no `seq` / `session`),
`t` / `t_start` / `t_end` in seconds from the scene start. No audio: they feed the relay directly, so the phone,
the dashboard and the cloud can be built without the array or the models. They mirror `python -m laptop --real`
in beams mode (fixed beams at world 0 / 180: `sources` id 0 = front, id 1 = rear).

| Scene | What |
|---|---|
| `front_speech` | one talker in front (beam 0), two lines |
| `rear_speech` | the turn-around line from behind (beam 1, 180) |
| `doorbell_rear` | doorbell on the rear beam, located at 165: `sound_event` + `alert` + enrichment |
| `name_called_rear` | "Hey Kori, the pizza is here!" from behind + `name_called` alert |
| `unknown_sound` | an unclassifiable sound at -120: `label: unknown`, keeps its bearing |
| `two_speakers` | front and rear talking over each other: two speech sources, `overlap: true` |
| `crowd_noise` | babble on both beams (speech flickering, low confidence), one caption through it |

```powershell
python tools/fake_events.py --scene rear_speech --loop     # through the relay (laptop/config.yaml)
python tools/fake_events.py --scene all
python tools/fake_events.py --build-scenes                 # rewrite the scripted scenes after changing the script
python tools/fake_events.py --from-log logs/laptop.jsonl --start T0 --end T1 --out fixtures/events/x.jsonl
```

The scripted ones come from `tools/fake_events.py` (`SCENES`); scenes cut from real runs are listed in its
`RECORDED` set so `--build-scenes` leaves them alone. `laptop/tests/test_scenes.py` validates every line.

## Record

```powershell
python -m laptop --real --record hall_two_people       # Ctrl+C to stop; sends to the relay as usual
python -m laptop --real --record quiet_test --offline  # record without a relay
```

## Replay

```powershell
python -m laptop --replay fixtures/hall_two_people          # into the relay, in real time
python tools/replay.py hall_two_people --loop --speed 1.5
```

## Sessions to record (SPEC §20 day 2, §21 spikes)

Mark the array, wearer and speaker positions with floor tape first and calibrate the array.

| Name | What | Why |
|---|---|---|
| `two_people` | Person 1 front-right, Person 2 front-left, taking turns, 2 min | moment 1, attribution |
| `turn_around` | someone speaks from behind, the wearer turns, they keep talking | moment 2 |
| `doorbell_back` | doorbell from the Bluetooth speaker behind, 10 presses | moment 3, doorbell threshold |
| `name_called` | the wearer's name from 4 directions, plus near-misses ("Cory", "sorry") | name-called tuning |
| `crowd_noise` | crowd noise loud, both speakers talking | false alerts, DoA in noise |
| `overlap` | both speakers at once, short bursts | overlap flag |
| `angles` | one speaker at 0, 45, ... 315 deg at 1 m and 1.5 m, 10 s each, with crowd noise | DoA accuracy table (tools/doa_accuracy.py) |

Write the positions and anything odd in `meta.json`'s notes.
