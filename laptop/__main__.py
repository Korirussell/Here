"""HERE hearing module (SPEC §6): python -m laptop --real | --mock | --replay fixtures/<name>

    python -m laptop --real                        ReSpeaker directions + audio -> relay
    python -m laptop --real --record hall_test     ...and save fixtures/hall_test/
    python -m laptop --mock                        synthetic scene (fixtures/mock_scene), no hardware
    python -m laptop --mock --mic                  laptop mic + a direction slider window
    python -m laptop --replay fixtures/two_people  a recorded session, in real time
    python -m laptop --mock --offline              print messages instead of sending to the relay
    python -m laptop --real --dashboard            live operator view in the terminal (needs `rich` for the full view)
    python -m laptop --real --viz                  3D sound field in the browser (laptop/viz.py; + --odas for ODAS)

Options: --relay URL (default laptop/config.yaml relay.url or HERE_RELAY_URL), --mirror URL (also publish there,
e.g. the Vultr relay while --relay is the local one; laptop/client/fanout.py), --session, --no-speech,
--no-sounds, --no-enrich, --offset/--ccw (array calibration override), --device N, --speed (replay).
"""

import argparse
import asyncio
import os
import sys

from . import settings
from .app import Laptop, Recorder, fixture_path
from .client.fanout import FanoutClient
from .client.relay import ConsoleSink, RelayClient, redact, relay_url


def build_inputs(args, cfg):
    a = cfg["audio"]
    odas = args.odas or a["doa_source"] == "odas"
    odas_audio = args.odas_audio or a["odas"].get("audio", "beams")
    if args.real:
        if odas:
            # ODAS tracks on the raw mics; audio per track from the steered beams (hybrid) or from ODAS itself
            from .audio.builtin_doa import ReSpeaker
            from .audio.odas import OdasSource
            ears = ReSpeaker(args.offset, args.ccw, args.device, capture="beams" if odas_audio == "beams" else "processed")
            if ears.raw is None:
                raise SystemExit("ODAS needs the raw mics: the 6-channel firmware")
            src = OdasSource(args.offset, args.ccw, ears=ears, audio=odas_audio)
            return src, (src if src.own_audio else ears)
        from .audio.builtin_doa import ReSpeaker
        capture = a.get("capture", "processed") if args.beams is None else ("beams" if args.beams else "processed")
        ears = ReSpeaker(args.offset, args.ccw, args.device, beams_as_sources=a["builtin_beams_as_sources"],
                         capture=capture)
        return ears, ears
    if args.mic:
        from .audio.mock import MicAudio, SliderDirections
        return SliderDirections(a["doa_rate_hz"]), MicAudio(args.device, a["chunk_samples"])
    from .audio.mock import FixturePlayer
    folder = fixture_path(args.replay) if args.replay else fixture_path(cfg["mock"]["fixture"])
    player = FixturePlayer(folder, loop=args.loop or (args.mock and cfg["mock"]["loop"]), speed=args.speed,
                           chunk=a["chunk_samples"])
    if odas:
        if player.raw is None:
            raise SystemExit(f"{folder.name} has no raw.wav: ODAS replays need a raw-mic fixture")
        from .audio.odas import OdasSource
        src = OdasSource(player.offset, player.ccw, ears=player, audio=odas_audio)
        return src, (src if src.own_audio else player)
    return player, player


def main():
    for stream in (sys.stdout, sys.stderr):      # Windows consoles default to cp1252: emoji would crash prints
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="python -m laptop", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--real", action="store_true", help="ReSpeaker XVF3800")
    mode.add_argument("--mock", action="store_true", help="synthetic scene (or --mic for the laptop mic)")
    mode.add_argument("--replay", metavar="FIXTURE", help="fixtures/<name> or a folder path")
    p.add_argument("--mic", action="store_true", help="with --mock: laptop mic + direction slider")
    p.add_argument("--relay", help="relay base URL, e.g. wss://host/ws")
    p.add_argument("--mirror", action="append", default=[], metavar="URL",
                   help="also publish to this relay (repeatable; relay.mirrors / HERE_RELAY_MIRRORS), e.g. Vultr")
    p.add_argument("--session")
    p.add_argument("--offline", action="store_true", help="print messages instead of sending them")
    p.add_argument("--record", metavar="NAME", help="with --real/--mic: save fixtures/NAME")
    p.add_argument("--no-speech", action="store_true")
    p.add_argument("--no-sounds", action="store_true")
    p.add_argument("--no-enrich", action="store_true", help="no attention / Gemini enrichment (laptop/enrich/)")
    p.add_argument("--offset", type=float, help="array_offset_deg override")
    p.add_argument("--ccw", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--device", type=int, help="audio input index (python -m sounddevice)")
    p.add_argument("--speed", type=float, default=1.0, help="replay speed")
    p.add_argument("--loop", action="store_true", help="loop the replay")
    p.add_argument("--model", help="faster-whisper model override")
    p.add_argument("--engine", choices=["faster_whisper", "scribe_realtime", "fake"],
                   default=os.environ.get("HERE_SPEECH_ENGINE") or None,
                   help="speech engine override (speech.engine, or env HERE_SPEECH_ENGINE); scribe_realtime falls back to whisper")
    p.add_argument("--user", help="user name for name-called until the phone sends config")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--beams", action=argparse.BooleanOptionalAction, default=None,
                   help="with --real: capture the two focused beams as two streams (audio.capture: beams)")
    p.add_argument("--odas", action="store_true", help="directions from ODAS on the raw mics (audio.doa_source: odas); "
                                                        "real array or a raw fixture replay")
    p.add_argument("--odas-audio", choices=["beams", "separated", "postfiltered"],
                   help="with --odas: audio per track from the steered beams (hybrid) or ODAS's own streams")
    p.add_argument("--dashboard", action="store_true", help="live operator view: direction, level, VAD, caption, "
                                                             "alerts, relay (laptop/dashboard.py)")
    p.add_argument("--viz", action="store_true", help="3D sound field in the browser on this laptop (laptop/viz.py, "
                                                       "config viz:); needs raw mics (6-ch firmware or a raw fixture)")
    args = p.parse_args()

    cfg = settings.load()
    r = cfg["relay"]
    if args.relay:
        r["url"] = args.relay
    if args.session:
        r["session"] = args.session
    if args.model:
        cfg["speech"]["model"] = args.model
    if args.engine:
        cfg["speech"]["engine"] = args.engine

    if args.offline:
        # with the dashboard on, it shows the messages: don't also print them
        quiet = ("sources", "heartbeat")
        if args.dashboard:
            quiet += ("speech_start", "utterance", "sound_event", "alert")
        client = ConsoleSink(quiet_types=quiet, log_path=settings.ROOT / r["log"], heartbeat_s=r["heartbeat_s"])
    else:
        mirrors = [u for u in dict.fromkeys([*r["mirrors"], *args.mirror]) if u != r["url"]]
        if mirrors:
            client = FanoutClient([r["url"], *mirrors], r["session"], r["token"], role="laptop",
                                  log_path=settings.ROOT / r["log"], heartbeat_s=r["heartbeat_s"])
        else:
            client = RelayClient(r["url"], r["session"], r["token"], role="laptop", log_path=settings.ROOT / r["log"],
                                 heartbeat_s=r["heartbeat_s"])
        print(f"[laptop] relay {redact(relay_url(r['url'], r['session'], 'laptop', r['token']))}"
              + "".join(f"\n[laptop] mirror {redact(relay_url(u, r['session'], 'laptop', r['token']))}" for u in mirrors))

    enricher = None
    if not args.no_enrich:                       # attention + Gemini after the fact (laptop/enrich/); never blocks
        from .enrich.enricher import make_enricher
        enricher = make_enricher(client)

    directions, audio = build_inputs(args, cfg)
    recorder = None
    if args.record:
        meta = {"name": args.record, "mode": "real" if args.real else "mic",
                "array_offset_deg": args.offset if args.offset is not None else cfg["audio"]["array_offset_deg"],
                "array_clockwise": cfg["audio"]["array_clockwise"],
                "capture": getattr(audio, "capture", "processed")}
        if hasattr(audio, "meta"):
            meta.update(audio.meta())            # firmware, routing, fixed beams, calibration, mic geometry
        raw_channels = len(cfg["audio"]["raw_mic_channels"]) if getattr(audio, "raw", None) is not None else 0
        recorder = Recorder(settings.ROOT / "fixtures" / args.record, meta,
                            channels=int(getattr(audio, "streams", 1)), raw_channels=raw_channels,
                            raw_gain=cfg["audio"].get("raw_record_gain", 1.0))

    user = args.user or (cfg["mock"]["user_name"] if (args.mock or args.replay) else "") or cfg["names"]["user_name"]
    names = [n for n in [user, *cfg["names"]["aliases"]] if n]

    shared = {}                                  # one whisper + one sound model for every audio stream (beams)

    def make_speech(app, stream=0):
        from .speech.pipeline import SpeechPipeline
        from .speech.transcriber import make_transcriber
        if "transcriber" not in shared:
            shared["transcriber"], app.speech_detail = make_transcriber(cfg["speech"])
        return SpeechPipeline(cfg["speech"], shared["transcriber"], app.stream_history(stream), client.send,
                              on_name=app.alerts.name_called, wearer_bearing=cfg["audio"]["wearer_bearing_deg"],
                              wearer_ignore_deg=cfg["audio"]["wearer_ignore_deg"],
                              locate_speech=getattr(audio, "locate_speech", None))

    def make_sounds(app, stream=0):
        from .sounds.classifier import Classifier, SoundPipeline
        locate = getattr(audio, "locate", None) if getattr(audio, "srp", None) is not None else None
        if "classifier" not in shared:
            shared["classifier"] = Classifier()
        pipeline = SoundPipeline(shared["classifier"], app.stream_history(stream), client.send, app.alerts,
                                 locate=locate, verbose=args.verbose)
        if getattr(app, "onsets", None) is not None:     # the laptop's onset candidates (docs/INTEGRATION.md §5.1)
            app.onsets.subscribe(pipeline.on_onset)
        return pipeline

    app = Laptop(cfg, (directions, audio), client,
                 speech=None if args.no_speech else make_speech,
                 sounds=None if (args.no_sounds or not cfg["sounds"]["enabled"]) else make_sounds,
                 recorder=recorder, names=names)
    if enricher is not None:
        on_config = client.on_config             # the app's; the enricher also wants focus mode
        client.on_config = lambda c: (on_config(c), enricher.set_config(c))
        app.detail_sources.append(enricher.problem)
        enricher.start()
    if args.dashboard:
        from .dashboard import Dashboard
        board = Dashboard(app, client, wearer_bearing=cfg["audio"]["wearer_bearing_deg"])
        board.wrap(client)
        app.extra_parts["dashboard"] = board.run
    if args.viz:
        from .viz import Viz
        app.extra_parts["viz"] = Viz(app, cfg.get("viz", {})).run
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\n[laptop] stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
