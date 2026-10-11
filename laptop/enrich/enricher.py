r"""perception_enrichment (SPEC §12.7): watches what the laptop sends, judges it afterwards, sends the judgment.

    client.send(sound_event / utterance final)  ->  Enricher.observe  ->  AttentionWorker (tiers) ---\
                                                                     \-> GeminiContext (meaningful) --> PerceptionEnrichment(ref)

- Triggers: a new sound_event (its first message or a label upgrade, not count bumps) and final utterances. Never the
  `sources` stream.
- The attention decision is always sent (rules when the models are slow, unsure or down). Gemini's note is added when
  it arrives: in the same message if it's already there, else as an update with the same `ref`.
- Every decision is also logged to logs/laptop.jsonl as a log-only `attention_decision` line (option probabilities,
  the tier that decided, what the rules would have said, timings) for the dashboard inspector and run_report.
- No "behind the wearer": the laptop doesn't know the phone's heading, so states never mention direction.
"""

import asyncio
import json
import threading
import time
from collections import deque

from contracts.python.here_contract import PerceptionEnrichment, Ref

from laptop.sounds.labels import ALERT_DISPLAY, ALERTING_LABELS

from . import config as C
from .attention import AttentionWorker, Job, TieredDecider, build_decider
from .gemini import GeminiContext
from .states import sound_state, speech_state


class Enricher:
    def __init__(self, send, decider=None, gemini=None, log=None, clock=time.time, conversation_s=20.0):
        self.send = send                       # the client's real send (not the tapped one)
        self.log = log                         # a text file (the client's JSON-lines log) or None
        self.clock = clock
        self.loop = None
        self.focus = False
        self.conversation_s = conversation_s
        self.worker = AttentionWorker(decider or TieredDecider([], {}), self.on_decision,
                                      queue_max=int(getattr(C, "QUEUE_MAX", 4)),
                                      cache_s=float(getattr(C, "CACHE_S", 5.0)),
                                      max_rate_hz=float(getattr(C, "MAX_RATE_HZ", 5.0)))
        self.gemini = gemini if gemini is not None else GeminiContext()
        self.seen = {}                         # event_id -> last label enriched
        self.sounds = deque(maxlen=64)         # (t, label) recently sent, for repeats
        self.recent = deque(maxlen=6)          # short summaries for Gemini
        self.last_final_t = -1e9
        self.named = deque(maxlen=64)          # utt_ids with a name_called alert
        self.state = {}                        # ref key -> {"ref", "decision", "context", "sent"}
        self.stats = {"jobs": 0, "sent": 0, "context": 0}

    # ---- wiring --------------------------------------------------------------------------------

    def wrap(self, client):
        """client.send also feeds this. Call before anything captures client.send."""
        send = client.send
        self.send = send
        if self.log is None:
            self.log = getattr(client, "log", None)

        def tapped(msg):
            ok = send(msg)
            try:
                self.observe(msg)
            except Exception as e:  # noqa: BLE001 - enrichment must never break sending
                print(f"[enrich] observe failed: {type(e).__name__}: {e}", flush=True)
            return ok
        client.send = tapped
        return client

    def problem(self):
        """For the laptop heartbeat `detail` (never degrades it): e.g. Gemini paused on an exhausted quota."""
        return getattr(self.gemini, "problem", None)

    def start(self, load_models=True):
        self.worker.start()
        if load_models:      # rules answer until the models are loaded (DeBERTa ~5 s, JEV-9B much longer)
            threading.Thread(target=self._load, name="attention-load", daemon=True).start()
        print(f"[enrich] attention on (rules until the models load); Gemini "
              f"{'on (' + self.gemini.model + ')' if self.gemini.available else 'off (no GEMINI_API_KEY)'}", flush=True)
        return self

    def _load(self):
        d = build_decider(log=lambda s: print(s, flush=True))
        self.worker.decider = d
        print(f"[enrich] attention tiers: {[t.name for t in d.tiers] + ['rules']}", flush=True)

    def set_config(self, cfg_msg):
        self.focus = bool(getattr(cfg_msg, "focus_mode", False))

    # ---- in (event loop thread) ------------------------------------------------------------------

    def observe(self, msg):
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        kind = msg.TYPE
        if kind == "sound_event":
            self._sound(msg)
        elif kind == "utterance" and msg.final and (msg.text or "").strip():
            self._utterance(msg)
        elif kind == "alert" and msg.kind == "name_called" and msg.ref is not None and msg.ref.utt_id:
            self.named.append(msg.ref.utt_id)

    def _sound(self, m):
        if m.label == "speech" or self.seen.get(m.event_id) == m.label:
            return                             # a count bump or a later window of the same sound
        upgrade = m.event_id in self.seen
        self.seen[m.event_id] = m.label
        if len(self.seen) > 512:
            self.seen.pop(next(iter(self.seen)))
        now = m.t
        repeats = sum(1 for t, lab in self.sounds if lab == m.label and now - t <= 60.0)
        self.sounds.append((now, m.label))
        conversation = now - self.last_final_t <= self.conversation_s
        energy = m.confidence if m.label == "unknown" else None     # unknown's confidence is its loudness
        state = sound_state(m.label, m.confidence, energy, repeats, conversation, self.focus,
                            text=None if m.label == "unknown" else m.text.lower())
        priority = ALERT_DISPLAY[m.label][2] if m.label in ALERTING_LABELS else 0
        job = Job(m.event_id, "sound", state, label=m.label, energy=energy or 0.0, priority=priority, t=now)
        self._submit(job, Ref(event_id=m.event_id))
        summary = f"{m.text.lower()} ({m.confidence:.2f})"
        meaningful = m.label in ALERTING_LABELS or upgrade or (m.label == "unknown" and m.confidence >= 0.5)
        if meaningful:
            self._ask_gemini(m.event_id, f"sound: {summary}" + (" (label just upgraded)" if upgrade else ""))
        self.recent.append(summary)

    def _utterance(self, m):
        self.last_final_t = m.t
        named = m.utt_id in self.named
        state = speech_state(m.text, getattr(m, "tone", None), named, bool(getattr(m, "overlap", False)), self.focus)
        job = Job(m.utt_id, "speech", state, label="speech", priority=2 if named else 0, name_called=named,
                  ask_directed=True, t=m.t)
        self._submit(job, Ref(utt_id=m.utt_id))
        text = " ".join(m.text.split()[:25])
        if len(m.text.split()) >= int(getattr(C, "GEMINI_MIN_WORDS", 3)):
            tone = getattr(m, "tone", None)
            self._ask_gemini(m.utt_id, f'speech: "{text}"' + (f" (tone {tone})" if tone and tone != "neutral" else "")
                             + (" (contains the wearer's name)" if named else ""))
        self.recent.append(f'speech "{" ".join(text.split()[:8])}"')

    def _submit(self, job, ref):
        self.stats["jobs"] += 1
        self.state[job.key] = {"ref": ref, "decision": None, "context": None, "context_model": None, "sent": 0}
        while len(self.state) > 256:
            self.state.pop(next(iter(self.state)))
        self.worker.submit(job)

    def _ask_gemini(self, key, event):
        if self.gemini.available:
            self.gemini.ask(key, event, list(self.recent)[-4:], self.on_context)

    # ---- out (worker threads -> event loop) --------------------------------------------------------

    def _call(self, fn, *args):
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(fn, *args)
        else:
            fn(*args)

    def on_decision(self, job, d):
        self._call(self._decided, job, d)

    def on_context(self, key, result, meta):
        self._call(self._contexted, key, result, meta)

    def _decided(self, job, d):
        st = self.state.get(job.key)
        self._log({"type": "attention_decision", "ref": job.key, "kind": job.kind, "state": job.state,
                   "attention": d.attention, "confidence": d.confidence, "model": d.model, "probs": d.probs,
                   "directed_at_user": d.directed, "rules_attention": d.rules_attention, "ms": round(d.ms, 1),
                   "tried": d.tried, "queued_ms": round((self.clock() - job.t) * 1000, 1)})
        if st is None:
            return
        st["decision"] = d
        self._send(st)

    def _contexted(self, key, result, meta):
        self._log({"type": "gemini_context", "ref": key, "result": result, **meta})
        st = self.state.get(key)
        if st is None or not result:
            return
        st["context"], st["context_model"] = result["note"], meta.get("model")
        self.stats["context"] += 1
        if st["decision"] is not None:          # else it rides along with the attention message
            self._send(st)

    def _send(self, st):
        d = st["decision"]
        msg = PerceptionEnrichment(src="laptop", t=self.clock(), ref=st["ref"], attention=d.attention,
                                   attention_confidence=round(min(1.0, max(0.0, d.confidence)), 3),
                                   attention_model=d.model, directed_at_user=d.directed,
                                   context=st["context"], context_model=st["context_model"] if st["context"] else None)
        st["sent"] += 1
        self.stats["sent"] += 1
        self.send(msg)

    def _log(self, entry):
        if self.log is None:
            return
        try:
            self.log.write(json.dumps({**entry, "t": round(self.clock(), 3), "_log_only": True},
                                      ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass


def make_enricher(client, enabled=None):
    """The enricher for `python -m laptop`, or None when `enrich.enabled` is false."""
    if not (getattr(C, "ENABLED", True) if enabled is None else enabled):
        return None
    e = Enricher(client.send)
    e.wrap(client)
    return e
