"""Gemini context (README §18): one narrow prompt, a strict JSON schema, on meaningful events only.

Input: the event's label and confidence, energy, a transcript snippet, a short summary of recent events. Never audio,
never a direction relative to the wearer. Output: a richer label, a semantic category, directed-at-user, a one-line
note; `perception_enrichment.context` carries the note. Timeout, cache, debounce; any failure just drops it.

Key: GEMINI_API_KEY in laptop/.env. Without it, `GeminiContext.available` is False and nothing is ever sent.
A quota (429) or key (400/401/403) error pauses every call until the server's retryDelay (capped at
`gemini_pause_max_s`); `problem` puts that in the laptop heartbeat `detail` without degrading it.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict, deque

from . import config as C

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """You help a deaf person understand sounds around them. A local system already detected the event below
and showed it on their glasses; add context, briefly. Do not invent details the input does not support. Do not say
where the sound came from.

Event: {event}
Recent events (oldest first): {recent}

Answer in JSON with: label (a richer 1-4 word name for what happened), category (one of: safety, social, household,
transport, media, ambient, other), directed_at_user (0 to 1: is this aimed at the wearer?), note (one sentence, at most
14 words, plain language, for the wearer)."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "label": {"type": "STRING"},
        "category": {"type": "STRING", "enum": ["safety", "social", "household", "transport", "media", "ambient",
                                                "other"]},
        "directed_at_user": {"type": "NUMBER"},
        "note": {"type": "STRING"},
    },
    "required": ["label", "category", "directed_at_user", "note"],
}


def parse(text):
    """The model's JSON -> a clean dict, or None if it doesn't fit the schema."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or not all(k in d for k in SCHEMA["required"]):
        return None
    note = " ".join(str(d["note"]).split())[:160]
    if not note:
        return None
    try:
        directed = min(1.0, max(0.0, float(d["directed_at_user"])))
    except (TypeError, ValueError):
        directed = None
    cat = d["category"] if d["category"] in SCHEMA["properties"]["category"]["enum"] else "other"
    return {"label": " ".join(str(d["label"]).split())[:40], "category": cat, "directed_at_user": directed,
            "note": note}


class GeminiContext:
    """ask(key, event, recent, on_done) never blocks: a daemon thread calls Gemini and then on_done(key, result,
    meta) with result = parse(...) or None. Debounce: at most one call per `min_gap_s`; calls inside the gap are
    dropped (the event still gets its attention enrichment). Cache: the same (event, recent) within `cache_s`."""

    def __init__(self, api_key=None, model=None, timeout_s=None, min_gap_s=None, cache_s=None, transport=None,
                 clock=time.monotonic):
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self.model = model or getattr(C, "GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout_s = float(timeout_s if timeout_s is not None else getattr(C, "GEMINI_TIMEOUT_S", 4.0))
        self.min_gap_s = float(min_gap_s if min_gap_s is not None else getattr(C, "GEMINI_MIN_GAP_S", 2.0))
        self.cache_s = float(cache_s if cache_s is not None else getattr(C, "GEMINI_CACHE_S", 60.0))
        self.transport = transport or self._post
        self.clock = clock
        self.cache = OrderedDict()
        self.last_call = -1e9
        self.inflight = 0
        self.lock = threading.Lock()
        self.stats = {"calls": 0, "ok": 0, "failed": 0, "debounced": 0, "cached": 0, "paused": 0, "tokens_in": 0,
                      "tokens_out": 0}
        self.latencies = deque(maxlen=50)
        # a quota (429) or key (400/401/403) error pauses every call until the server's retryDelay, capped
        self.pause_default_s = float(getattr(C, "GEMINI_PAUSE_DEFAULT_S", 60.0))
        self.pause_max_s = float(getattr(C, "GEMINI_PAUSE_MAX_S", 600.0))
        self.paused_until = -1e9
        self.pause_reason = None

    @property
    def available(self):
        return bool(self.api_key) and bool(getattr(C, "GEMINI_ENABLED", True))

    def paused(self):
        return self.clock() < self.paused_until

    @property
    def problem(self):
        """One line for the heartbeat `detail` while Gemini is paused, else None."""
        if not self.available or not self.paused():
            return None
        return f"Gemini paused: {self.pause_reason} (retry in {max(1, round((self.paused_until - self.clock()) / 60))} min)"

    def status(self):
        if not self.available:
            return {"state": "off", "reason": "no GEMINI_API_KEY" if not self.api_key else "disabled"}
        if self.paused():
            return {"state": "paused", "reason": self.pause_reason, "retry_in_s": round(self.paused_until - self.clock())}
        return {"state": "on", "model": self.model}

    def _trip(self, e):
        code = e.code
        if code not in (400, 401, 403, 429):
            return
        delay = self.pause_max_s
        if code == 429:
            delay = self.pause_default_s
            try:
                for d in json.loads(e.read().decode("utf-8")).get("error", {}).get("details", []):
                    if str(d.get("retryDelay", "")).endswith("s"):
                        delay = float(d["retryDelay"][:-1])
            except Exception:  # noqa: BLE001 - unreadable body: default pause
                pass
        delay = max(1.0, min(delay, self.pause_max_s))
        with self.lock:
            fresh = not self.paused()
            self.paused_until = self.clock() + delay
            self.pause_reason = "quota exhausted" if code == 429 else f"key rejected (HTTP {code})"
        if fresh:
            print(f"[enrich] gemini: {self.pause_reason}, paused {delay:.0f} s (events go out without context)",
                  flush=True)

    def _post(self, body):
        req = urllib.request.Request(URL.format(model=self.model), data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read().decode("utf-8"))

    def ask(self, key, event, recent, on_done):
        if not self.available:
            return False
        if self.paused():
            with self.lock:
                self.stats["paused"] += 1
            return False
        ck = (event, tuple(recent))
        now = self.clock()
        with self.lock:
            hit = self.cache.get(ck)
            cached = hit is not None and now - hit[0] <= self.cache_s
            if cached:
                self.stats["cached"] += 1
            elif now - self.last_call < self.min_gap_s or self.inflight >= 2:
                self.stats["debounced"] += 1
                return False
            else:
                self.last_call = now
                self.inflight += 1
        if cached:
            on_done(key, hit[1], {"ms": 0.0, "cached": True, "model": self.model})
            return True
        threading.Thread(target=self._call, args=(key, ck, event, recent, on_done), daemon=True,
                         name="gemini").start()
        return True

    def _call(self, key, ck, event, recent, on_done):
        t0 = time.perf_counter()
        body = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(
                    event=event, recent="; ".join(recent) or "none")}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA,
                                     "temperature": 0.2, "maxOutputTokens": 200,
                                     "thinkingConfig": {"thinkingBudget": 0}}}
        result, err = None, None
        try:
            resp = self.transport(body)
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            result = parse(text)
            usage = resp.get("usageMetadata", {})
            with self.lock:
                self.stats["tokens_in"] += int(usage.get("promptTokenCount", 0))
                self.stats["tokens_out"] += int(usage.get("candidatesTokenCount", 0))
            if result is None:
                err = "bad schema"
        except urllib.error.HTTPError as e:
            err = f"HTTPError: {e.code}"
            self._trip(e)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, OSError) as e:
            err = f"{type(e).__name__}: {e}"[:120]
        ms = (time.perf_counter() - t0) * 1000
        with self.lock:
            self.inflight -= 1
            self.stats["calls"] += 1
            self.stats["ok" if result else "failed"] += 1
            self.latencies.append(ms)
            if result:
                self.cache[ck] = (self.clock(), result)
                while len(self.cache) > 128:
                    self.cache.popitem(last=False)
        on_done(key, result, {"ms": ms, "cached": False, "model": self.model, "error": err})
