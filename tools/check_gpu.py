"""Pre-demo check for the laptop: GPU stack, model speed, the ReSpeaker, and the relay (README §23 setup).

    python tools/check_gpu.py
    python tools/check_gpu.py --skip-respeaker
    python tools/check_gpu.py --demo      # with the demo stack up (tools/demo.py): relay, token accepted, laptop
                                          # heartbeat (models warm, engine, doa source), dashboard, memory API.
                                          # Loads no models and leaves the array to the running laptop.

Loads the real models with the real settings (laptop/config.yaml speech + sounds), times them,
and prints OK / WARN / FAIL per line (WARN: slower than budget, or not set up yet). Exit code 1 if anything failed.
"""

import torch  # noqa: I001  (first: provides the cuBLAS/cuDNN DLLs faster-whisper needs on Windows)

import argparse
import glob
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Budgets on the RTX 5000 Ada (tune after the first real run)
PARTIAL_BUDGET_S = 0.30   # beam-1 decode of 10 s of audio; partials refresh every 0.5 s
FINAL_BUDGET_S = 0.80     # beam-5 decode with word timestamps
PANNS_BUDGET_S = 0.15     # one 1 s window; windows arrive every 0.5 s

failed = False
warnings = 0


def report(ok, label, detail, slow=False):
    global failed, warnings
    tag = "OK  " if ok else ("WARN" if slow else "FAIL")
    failed |= not ok and not slow
    warnings += not ok and slow
    print(f"[{tag}] {label}: {detail}")


def timed(fn, runs=3):
    fn()  # warm-up
    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


def test_audio():
    """10 s of real speech from a fixture if one exists, else noise."""
    for path in sorted(glob.glob(str(ROOT / "fixtures" / "*" / "audio.wav"))):
        with wave.open(path) as w:
            pcm = np.frombuffer(w.readframes(w.getframerate() * 10), np.int16)
        if len(pcm) >= 16000 * 5:
            return pcm.astype(np.float32) / 32768, Path(path).parent.name
    return (np.random.default_rng(0).standard_normal(16000 * 10) * 0.01).astype(np.float32), "noise"


def check_torch():
    print(f"torch {torch.__version__}, CUDA build {torch.version.cuda}")
    if not torch.cuda.is_available():
        report(False, "CUDA", "torch.cuda.is_available() is False (CPU-only torch wheel? see laptop/README.md)")
        return False
    try:
        x = torch.randn(1024, 1024, device="cuda")
        (x @ x).sum().item()
    except Exception as e:
        report(False, "CUDA", f"allocation failed: {e} (driver/wheel mismatch, laptop/README.md)")
        return False
    props = torch.cuda.get_device_properties(0)
    report(True, "CUDA", f"{props.name}, {props.total_memory / 2**30:.1f} GB")
    return True


def check_whisper():
    import ctranslate2
    from faster_whisper import WhisperModel

    from laptop.speech import config as S

    n = ctranslate2.get_cuda_device_count()
    if n == 0:
        report(False, "faster-whisper", "CTranslate2 sees no CUDA device (speech would fall back to CPU)")
        return
    try:
        model = WhisperModel(S.MODEL, device=S.DEVICE, compute_type=S.COMPUTE_TYPE)
    except Exception as e:
        report(False, "faster-whisper", f"loading {S.MODEL} failed: {e} (cuBLAS DLLs? laptop/README.md)")
        return
    audio, source = test_audio()

    def decode(beam, words):
        segs, _ = model.transcribe(audio, language=S.LANGUAGE, beam_size=beam, vad_filter=False,
                                   condition_on_previous_text=False, word_timestamps=words,
                                   without_timestamps=not words)
        return " ".join(s.text.strip() for s in segs)

    partial = timed(lambda: decode(1, False))
    final = timed(lambda: decode(S.FINAL_BEAM_SIZE, True))
    label = f"{S.MODEL} {S.COMPUTE_TYPE}"
    report(partial <= PARTIAL_BUDGET_S, f"{label} partial", f"{partial * 1000:.0f} ms per 10 s decode "
           f"(budget {PARTIAL_BUDGET_S * 1000:.0f})", slow=True)
    report(final <= FINAL_BUDGET_S, f"{label} final", f"{final * 1000:.0f} ms (budget {FINAL_BUDGET_S * 1000:.0f})",
           slow=True)
    if source != "noise":
        print(f"       transcript of fixture '{source}': {decode(S.FINAL_BEAM_SIZE, False)[:120]}")


def check_panns():
    from laptop.sounds import config as N
    from laptop.sounds.classifier import Classifier

    try:
        classify = Classifier()
    except Exception as e:
        report(False, "PANNs CNN14", f"load failed: {e}")
        return
    window = np.zeros(int(N.WINDOW_S * N.SAMPLE_RATE), np.int16)
    t = timed(lambda: classify(window), runs=10)
    report(t <= PANNS_BUDGET_S and classify.device != "cpu", f"PANNs CNN14 on {classify.device}",
           f"{t * 1000:.0f} ms per {N.WINDOW_S:.1f} s window (budget {PANNS_BUDGET_S * 1000:.0f})", slow=True)


def check_relay():
    import json
    import urllib.request
    from urllib.parse import urlsplit

    from laptop import settings

    r = settings.load()["relay"]
    parts = urlsplit(r["url"])
    health = f"{'https' if parts.scheme == 'wss' else 'http'}://{parts.netloc}/health"
    try:
        body = json.loads(urllib.request.urlopen(health, timeout=5).read())
        report(body.get("ok") is True, "relay", f"{health} up, sessions {body.get('sessions')}")
    except Exception as e:
        report(False, "relay", f"{health}: {e}", slow=True)
    report(bool(r["token"]), "relay token", "set" if r["token"] else "HERE_RELAY_TOKEN not set (laptop/.env)",
           slow=True)


def check_respeaker():
    import usb.core

    from laptop.audio import config as A

    try:
        from laptop.audio.builtin_doa import find_input
        from laptop.audio.xvf3800 import XVF3800

        dev = XVF3800()
        version = ".".join(map(str, dev.read("VERSION")))
        dev.close()
        report(True, "ReSpeaker control", f"firmware {version}")
    except SystemExit as e:
        report(False, "ReSpeaker control", str(e).splitlines()[0])
    except usb.core.USBError as e:
        report(False, "ReSpeaker control", f"USB error: {e}")
    try:
        report(True, "ReSpeaker audio", f"input device {find_input()}")
    except SystemExit as e:
        report(False, "ReSpeaker audio", str(e))
    cal = (A.ARRAY_OFFSET_DEG, A.ARRAY_CLOCKWISE)
    report(cal != (0.0, True), "array calibration",
           f"offset {cal[0]} deg, {'clockwise' if cal[1] else 'counter-clockwise'}"
           + ("" if cal != (0.0, True) else " (defaults: run python -m laptop.audio.calibrate)"), slow=True)


DASHBOARD_URL = f"http://127.0.0.1:{os.environ.get('HERE_DASH_PORT', '8780')}/"     # same override as the dashboard
MEMORY_HEALTH_URL = f"http://127.0.0.1:{os.environ.get('HERE_MEMORY_PORT', '8790')}/api/health"
HEARTBEAT_WAIT_S = 5.0     # the laptop sends one every 2 s


def check_live():
    """The running stack, seen as a relay client: token accepted, and a laptop heartbeat (its models are loaded
    and it holds the array, so we don't load or open either ourselves)."""
    import json
    from urllib.parse import quote

    from websockets.sync.client import connect

    from contracts.python import here_contract as hc
    from laptop import settings

    r = settings.load()["relay"]
    url = f"{r['url']}?session={quote(r['session'])}&role=tool&token={quote(r['token'])}"
    try:
        with connect(url, open_timeout=5) as ws:
            ws.send(hc.Hello(src="tool", t=time.time(), role="tool", subscribe=["heartbeat"]).to_json())
            report(bool(r["token"]), "relay session", "joined, token accepted" if r["token"] else "joined, relay auth OFF",
                   slow=True)
            end = time.time() + HEARTBEAT_WAIT_S
            while time.time() < end:
                try:
                    m = json.loads(ws.recv(timeout=max(0.1, end - time.time())))
                except TimeoutError:
                    break
                if m.get("type") == "heartbeat" and m.get("src") == "laptop":
                    detail = f"status {m['status']}, engine {m.get('engine')}, doa {m.get('doa_source')}"
                    detail += f" ({m['detail']})" if m.get("detail") else ""
                    report(m["status"] == "ok", "laptop live (models warm)", detail, slow=m["status"] == "degraded")
                    return
            report(False, "laptop live", f"no laptop heartbeat in {HEARTBEAT_WAIT_S:.0f} s "
                   "(scripted source only? python tools/demo.py status)", slow=True)
    except Exception as e:
        reason = "token rejected" if "401" in str(e) or "403" in str(e) else str(e)
        report(False, "relay session", f"{r['url']}: {reason}")


def attention_tiers_running(log_path=ROOT / "logs" / "demo" / "source.log"):
    """The tiers the running laptop loaded: its last '[enrich] attention tiers: [...]' line, or None."""
    import ast
    try:
        lines = [ln for ln in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
                 if ln.startswith("[enrich] attention tiers:")]
        return ast.literal_eval(lines[-1].split(":", 1)[1].strip()) if lines else None
    except (OSError, ValueError, SyntaxError):
        return None


def attention_tiers_possible():
    """Without loading anything: which configured tiers this venv + cache + GPU could load."""
    import importlib.util

    from laptop.enrich import config as EC

    have = {m: importlib.util.find_spec(m) is not None for m in ("transformers", "peft", "bitsandbytes")}
    out = []
    for name in getattr(EC, "TIERS", []):
        jev = name.startswith("autotrust/")
        missing = [m for m in (("transformers", "peft", "bitsandbytes") if jev else ("transformers",)) if not have[m]]
        if jev and not torch.cuda.is_available():
            missing.append("CUDA")
        out.append((name, missing))
    return out


def check_attention():
    """The demo must never be silently rules-only (Shared/ATTENTION.md tiers)."""
    running = attention_tiers_running()
    if running is not None:
        models = [t for t in running if t != "rules"]
        report(bool(models), "attention tier", f"running laptop loaded {running}"
               + ("" if models else ": RULES ONLY (see logs/demo/source.log for why)"), slow=True)
        return
    possible = attention_tiers_possible()
    ok = [n for n, miss in possible if not miss]
    detail = "; ".join(f"{n}: {'loadable' if not miss else 'missing ' + ', '.join(miss)}" for n, miss in possible)
    report(bool(ok), "attention tier", f"no running laptop log; {detail or 'no tiers configured'}"
           + ("" if ok else " -> RULES ONLY"), slow=True)


def check_http(label, url, describe):
    import json
    import urllib.request

    try:
        body = urllib.request.urlopen(url, timeout=3).read()
        try:
            body = json.loads(body)
        except ValueError:
            body = None
        report(True, label, describe(body))
    except Exception as e:
        report(False, label, f"{url}: {e} (seeded JSON fallback applies)", slow=True)


def summary():
    if failed:
        return "\nsomething FAILED; see above"
    return "\nall good" + (f" ({warnings} warning{'s' * (warnings != 1)})" if warnings else "")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-respeaker", action="store_true")
    p.add_argument("--demo", action="store_true", help="check the running demo stack instead of loading models")
    args = p.parse_args()

    if args.demo:
        check_relay()
        check_live()
        check_attention()
        check_http("dashboard", DASHBOARD_URL, lambda b: f"{DASHBOARD_URL} up")
        mem = {}
        check_http("memory API", MEMORY_HEALTH_URL,
                   lambda b: (mem.update(b), f"db {b.get('db')}, {b.get('events')} events")[1]
                   if isinstance(b, dict) else "up")
        if mem:
            report(mem.get("db") == "neon", "memory database",
                   "Neon" if mem.get("db") == "neon" else
                   f"{mem.get('db')} ({mem.get('fallback_reason') or 'NEON_DATABASE_URL not set'}): Ask/Replay work, "
                   "but the Neon story isn't live", slow=True)
        print(summary())
        sys.exit(1 if failed else 0)

    if check_torch():
        check_whisper()
        check_panns()
        free, total = torch.cuda.mem_get_info()
        print(f"       GPU memory in use with both models loaded: {(total - free) / 2**30:.1f} of {total / 2**30:.1f} GB")
    if not args.skip_respeaker:
        check_respeaker()
    check_relay()
    print(summary())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
