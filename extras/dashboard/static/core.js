// HERE World: shared state, the /live connection, provider attribution, small helpers.
// Message shapes: contracts/ts/contract.ts (v3). Unknown message types are ignored.

/** @typedef {import('../../../contracts/ts/contract').HereMessage} HereMessage */

export const KNOWN = new Set(['hello', 'heartbeat', 'clock_ping', 'clock_pong', 'config', 'sources', 'speech_start',
  'utterance', 'sound_event', 'alert', 'perception_enrichment', 'attribution', 'scene', 'search_request',
  'search_result'])

export const cfg = { page: { stream_max: 120, captions_max: 6, source_ttl_s: 1.6, sound_ttl_s: 6,
  latency_window: 60, heartbeat_stale_s: 5, messages_max: 600 }, relay: {}, scenes: [] }

// ---------------------------------------------------------------------------------------------- state
export const S = {
  status: { relay: { connected: false }, demo: { running: false } },
  pageConnected: false,
  sources: new Map(),        // `${src}:${id}` -> {id, bearing_deg, energy, speech, confidence, seen, trail}
  speechStart: new Map(),    // utt_id -> speech_start
  utts: new Map(),           // utt_id -> latest utterance
  captionOrder: [],          // utt_ids, newest last
  sounds: new Map(),         // event_id -> latest sound_event (+ seen)
  alerts: new Map(),         // alert_id -> latest alert (+ seen)
  enrich: new Map(),         // ref id -> perception_enrichment
  attrib: new Map(),         // ref id -> attribution
  voices: [],                // [{n, source_id, bearing, seen}]: talkers told apart by source id + direction
  voiceOf: new Map(),        // utt_id -> voice number (fixed for the whole utterance)
  retracted: new Set(),      // utt_ids the laptop withdrew (an empty final: noise, or the wearer's own voice)
  bearings: new Map(),       // sound / alert key -> recent raw bearings (the display is steadied over them)
  heartbeats: new Map(),     // src -> {m, rx}
  scene: null, sceneRx: 0,
  config: null,
  stream: [],                // [{key, kind, rx}] newest last, capped
  streamIndex: new Map(),    // key -> entry
  byKey: new Map(),          // key -> [{m, rx}] related messages for the inspector (insertion = LRU order)
  lag: new Map(),            // type -> [ms]
  stages: new Map(),         // stage -> [ms]
  counts: new Map(),         // type -> n
  memoryHealth: null,
  insightSource: null,       // 'databricks' | 'precomputed' | null
  lastRx: {},                // message type -> receive time of the latest one
  serverStatus: null,        // A3's /api/status: the relay next to the memory service (Vultr when deployed)
  attribRx: 0,               // last attribution from the phone
  demoSeen: 0,               // last time the server said a scripted scene was playing
  rev: 0,                    // bumps on anything a panel shows
}

const listeners = new Set()
export function onChange(fn) { listeners.add(fn) }
const msgListeners = new Set()
/** fn(m, key, rx, local) for each event message (speech, sounds, alerts, enrichment, attribution), after ingest. */
export function onMessage(fn) { msgListeners.add(fn) }
function changed() { S.rev++; for (const fn of listeners) fn() }

export function resetLive() {
  for (const k of ['sources', 'speechStart', 'utts', 'sounds', 'alerts', 'enrich', 'attrib', 'streamIndex', 'byKey',
    'lag', 'stages', 'counts', 'voiceOf', 'retracted', 'bearings']) S[k].clear()
  S.captionOrder = []; S.stream = []; S.scene = null; S.voices = []
  changed()
}

// ---------------------------------------------------------------------------------------------- helpers
export const now = () => Date.now() / 1000
export function wrap(deg) { const a = ((((deg + 180) % 360) + 360) % 360) - 180; return a === -180 ? 180 : a }
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}
export function fmtBearing(b) {
  if (b === null || b === undefined) return 'no direction'
  return `${b > 0 ? '+' : ''}${Math.round(b)}°`
}
export function dirWord(b) {
  if (b === null || b === undefined) return 'unknown direction'
  const a = Math.abs(b)
  const side = b > 0 ? 'right' : 'left'
  if (a <= 20) return 'front'
  if (a >= 160) return 'behind'
  if (a < 70) return `front-${side}`
  if (a <= 110) return side
  return `behind-${side}`
}
export function fmtTime(t) {
  const d = new Date(t * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
export function fmtClock(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}
export function pct(x) { return `${Math.round((x ?? 0) * 100)}%` }
export function median(xs) { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b); return s[Math.floor(s.length / 2)] }
export function p95(xs) { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(s.length * 0.95))] }
export function refKey(ref) { return ref?.utt_id ? `utt:${ref.utt_id}` : ref?.event_id ? `ev:${ref.event_id}` : null }
export function refId(ref) { return ref?.utt_id || ref?.event_id || null }

/** Inspector key for any message: what entity it belongs to. */
export function keyOf(m) {
  switch (m.type) {
    case 'speech_start': case 'utterance': return `utt:${m.utt_id}`
    case 'sound_event': return `ev:${m.event_id}`
    case 'alert': return refKey(m.ref) || `al:${m.alert_id}`
    case 'perception_enrichment': case 'attribution': return refKey(m.ref)
    default: return null
  }
}

function pushCapped(map, k, v, n) {
  let a = map.get(k); if (!a) { a = []; map.set(k, a) }
  a.push(v); if (a.length > n) a.splice(0, a.length - n)
}

// ---------------------------------------------------------------------------------------------- providers
// Which provider produced which field. Only claims a provider when the message says so.
export function modelProvider(model) {
  if (!model) return null
  // A2's classifier reports an enrolled-sound match as "<base model>+enrolled" (scenes say just "enrolled").
  const enrolled = /\+enrolled$/.exec(model)
  if (enrolled) return { name: 'Enrolled sounds', detail: `template match on the laptop · base model ${model.slice(0, enrolled.index).split('/').pop()}`, tag: 'local' }
  if (model === 'rules') return { name: 'Deterministic rules', detail: 'fallback · Hugging Face attention model not loaded', tag: 'fallback' }
  if (/^gemini/i.test(model)) return { name: 'Gemini', detail: model, tag: 'gemini' }
  if (model.includes('/')) return { name: 'Hugging Face', detail: model, tag: 'hf' }
  if (/panns/i.test(model)) return { name: 'PANNs CNN14', detail: 'local fallback · Hugging Face classifier not in use', tag: 'fallback' }
  if (model === 'enrolled') return { name: 'Enrolled sounds', detail: 'template match on the laptop (this exact sound was recorded)', tag: 'local' }
  return { name: model, detail: 'local model', tag: 'local' }
}
export function engineProvider(engine) {
  if (engine === 'scribe_realtime') return { name: 'ElevenLabs', detail: 'Scribe Realtime', tag: 'elevenlabs' }
  if (engine === 'faster_whisper') return { name: 'whisper', detail: 'local faster-whisper · ElevenLabs not in use', tag: 'fallback' }
  return engine ? { name: engine, detail: '', tag: 'local' } : null
}
/** The laptop pipeline is up: a fresh heartbeat from src "laptop". */
export function laptopLive() {
  const lap = S.heartbeats.get('laptop')
  return !!lap && now() - lap.rx < cfg.page.heartbeat_stale_s
}
/** True when the live stream is the scripted tool (fake_events), not the laptop pipeline. Scenes replay as
 * src "laptop" without heartbeats, so a scene played in the last minute counts as scripted too. */
export function scripted() {
  if (S.status.demo?.running || now() - S.demoSeen < 60) return true
  const tool = S.heartbeats.get('tool')
  return !laptopLive() && !!tool && now() - tool.rx < cfg.page.heartbeat_stale_s
}
export function directionProvider() {
  if (scripted()) return { name: 'Scripted', detail: 'tools/fake_events.py (no microphones)', tag: 'fallback' }
  if (!laptopLive()) return { name: 'ReSpeaker', detail: 'no laptop heartbeat: source unconfirmed', tag: 'off' }
  const hb = S.heartbeats.get('laptop')?.m
  const doa = hb?.doa_source === 'odas' ? 'ODAS tracker' : 'XVF3800 built-in DoA'
  return { name: 'ReSpeaker', detail: doa, tag: 'respeaker' }
}
export const localReplay = () => !!S.status.demo?.local
export function transportProvider() {
  if (localReplay()) return { name: 'Local replay', detail: 'fallback · no relay: the dashboard plays a recorded scene', tag: 'fallback' }
  const r = S.status.relay || {}
  return r.transport === 'Vultr relay'
    ? { name: 'Vultr', detail: 'cloud relay (wss)', tag: 'vultr' }
    : { name: 'Local relay', detail: 'fallback on the laptop · Vultr not in use', tag: 'fallback' }
}
export function storageProvider() {
  const h = S.memoryHealth
  if (!h) return null
  if (h._source === 'seed') return { name: 'Seeded JSON', detail: 'fallback · memory API (Neon) offline', tag: 'fallback' }
  return h.db === 'neon' ? { name: 'Neon', detail: `Postgres · ${h.events ?? '?'} events`, tag: 'neon' }
    : { name: 'Local SQLite', detail: 'fallback · Neon unreachable', tag: 'fallback' }
}
export function insightProvider(source) {
  return source === 'databricks' ? { name: 'Databricks', detail: 'pattern job', tag: 'databricks' }
    : source ? { name: 'Precomputed', detail: 'fallback · Databricks run not stored', tag: 'fallback' }
      : { name: 'Databricks', detail: 'not connected', tag: 'off' }
}
export const off = (name, detail) => ({ name, detail, tag: 'off' })

/** A3's /api/status, summarised: the relay that runs next to the memory service (Vultr when deployed there).
 * {where, ok, roles: Set of roles connected in our session, rate: msg/s over the window, ageP50, reason} */
export function serverRelay() {
  const st = S.serverStatus
  if (!st) return null
  const h = st.relay?.health, log = st.relay?.log
  if (!st.relay || !h?.ok) return { where: st.where, ok: false, reason: h?.reason || st.reason || 'no status' }
  const session = S.status.relay?.session
  const clients = (h.clients || []).filter(c => !session || c.session === session)
  const rate = log?.ok ? Object.values(log.by_type || {}).reduce((a, x) => a + (x.per_s || 0), 0) : null
  return { where: st.where, ok: true, roles: new Set(clients.map(c => c.role)), rate, ageP50: log?.laptop_age_ms?.p50 ?? null }
}
/** Has any message this session carried a Gemini context note? */
export function geminiSeen() { for (const e of S.enrich.values()) if (e.context) return true; return false }
/** Is the phone talking to us (heartbeat, scene or attribution in the last 30 s)? */
export function phoneSeen() {
  const t = now(), seen = Math.max(S.heartbeats.get('phone')?.rx || 0, S.sceneRx || 0, S.attribRx || 0)
  return seen > 0 && t - seen < 30
}

// ATTENTION.md tier 3, mirrored so the inspector can say "rules would have said X".
const HAZARDS = new Set(['car_horn', 'siren', 'alarm', 'loud_bang', 'vehicle'])
const MEDIA = new Set(['music', 'tv', 'laughter'])
export function rulesAttention(m, alert) {
  if (alert) return alert.priority === 3 ? 'urgent' : 'important'
  if (m?.type === 'sound_event') {
    if (HAZARDS.has(m.label)) return 'important'
    if (m.label === 'unknown') return (m.confidence ?? 0) < 0.4 ? 'ambient' : 'notice'
    return MEDIA.has(m.label) ? 'ambient' : 'notice'
  }
  return 'notice'
}
export function isHazard(label) { return HAZARDS.has(label) }

// What the phone does with each attention level (IOS_INTEGRATION.md, the perception_enrichment row).
export const WEARER = { ambient: 'minimal', notice: 'small cue', important: 'card + belt', urgent: 'interrupt' }
export const WEARER_LONG = { ambient: 'kept ambient (minimal)', notice: 'a small indicator', important: 'a full card + belt buzz',
  urgent: 'an interrupt' }
/** HERE's surfacing decision for one event: the enrichment's attention, else the deterministic rules. */
export function surfacing(id, m, alert) {
  const e = S.enrich.get(id)
  return e ? { att: e.attention, by: e.attention_model } : { att: rulesAttention(m, alert), by: 'rules (no enrichment)' }
}
export function alertFor(id) {
  for (const a of S.alerts.values()) if (refId(a.ref) === id) return a
  return null
}

// ---------------------------------------------------------------------------------------------- ingest
/** A retraction: a final utterance with no words (A1 ends an utterance that turned out to be noise or the wearer). */
export const isRetraction = m => m.type === 'utterance' && m.final && !`${m.text || ''}${m.pending || ''}`.trim()

function retract(uttId) {
  S.retracted.add(uttId); if (S.retracted.size > 500) S.retracted.delete(S.retracted.values().next().value)
  S.captionOrder = S.captionOrder.filter(id => id !== uttId)
  S.stream = S.stream.filter(x => x.key !== `utt:${uttId}`); S.streamIndex.delete(`utt:${uttId}`)
  // give back a "Voice N" that only this utterance used, so the next real talker doesn't skip a number
  const n = S.voiceOf.get(uttId)
  if (n !== undefined && ![...S.voiceOf].some(([id, v]) => v === n && id !== uttId && !S.retracted.has(id)))
    S.voices = S.voices.filter(v => v.n !== n)
}

// One sound reports its direction several times (a doorbell: 28.8, 27.8, 24.8, 0.8, 24.8 ...: the other beam
// sometimes wins a frame). The display uses the circular median of the last few reports, so the marker and the
// alert card stay put; the inspector still lists every raw report.
const STEADY_N = 7
const angDist = (a, b) => Math.abs(((a - b + 540) % 360) - 180)
function noteBearing(key, b) {
  if (!key || b === null || b === undefined) return
  let h = S.bearings.get(key)
  if (!h) { h = []; S.bearings.set(key, h) } else { S.bearings.delete(key); S.bearings.set(key, h) }
  h.push(b); if (h.length > STEADY_N) h.shift()
  if (S.bearings.size > 300) S.bearings.delete(S.bearings.keys().next().value)
}
/** The steadied bearing for a sound (ev:<id>) or its alert: the report closest to all the others. */
export function steadyBearing(key, raw) {
  const h = S.bearings.get(key)
  if (!h || h.length < 2) return raw
  let best = h[h.length - 1], bestCost = Infinity
  for (const c of h) { const cost = h.reduce((s, x) => s + angDist(c, x), 0); if (cost < bestCost - 1e-9) { best = c; bestCost = cost } }
  return best
}
export const bearingReports = key => S.bearings.get(key) || []

const STREAM_KINDS = new Set(['speech_start', 'utterance', 'sound_event', 'alert', 'perception_enrichment', 'attribution'])

export function ingest(m, rx, local = false) {
  if (!m || !KNOWN.has(m.type)) return
  S.counts.set(m.type, (S.counts.get(m.type) || 0) + 1)
  S.lastRx[m.type] = rx
  // A local replay is timed by this server, so it says nothing about real latency: keep it out of the stats.
  if (!local && typeof m.t === 'number' && m.src !== 'phone') {
    const lag = (rx - m.t) * 1000
    if (lag > -2000 && lag < 60000) pushCapped(S.lag, m.type, lag, cfg.page.latency_window)
  }
  const key = keyOf(m)
  if (key) {
    let a = S.byKey.get(key)
    if (!a) { a = []; S.byKey.set(key, a) } else { S.byKey.delete(key); S.byKey.set(key, a) }
    a.push(local ? { m, rx, local } : { m, rx }); if (a.length > 60) a.splice(1, 1)          // keep the first message, trim the middle
    if (S.byKey.size > cfg.page.messages_max) S.byKey.delete(S.byKey.keys().next().value)
  }
  switch (m.type) {
    case 'sources': {
      for (const s of m.sources || []) {
        const k = `${m.src}:${s.id}`
        const prev = S.sources.get(k)
        const trail = prev ? prev.trail : []
        trail.push(s.bearing_deg); if (trail.length > 12) trail.shift()
        S.sources.set(k, { ...s, src: m.src, seen: rx, trail })
      }
      return                                                    // canvas only; no panel refresh
    }
    case 'scene': S.scene = m; S.sceneRx = rx; return
    case 'heartbeat': S.heartbeats.set(m.src, { m, rx }); break
    case 'config': S.config = m; break
    case 'speech_start': if (S.retracted.has(m.utt_id)) return; S.speechStart.set(m.utt_id, m); voiceFor(m.utt_id, m.source_id, m.bearing_deg, rx); addCaption(m.utt_id); break
    case 'utterance':
      S.utts.set(m.utt_id, m)
      if (isRetraction(m)) { retract(m.utt_id); changed(); return }        // no caption card, no stream row
      if (S.retracted.has(m.utt_id)) { changed(); return }
      voiceFor(m.utt_id, m.source_id, m.bearing_deg, rx); addCaption(m.utt_id)
      if (m.final && m.stage_ms) for (const [k, v] of Object.entries(m.stage_ms)) pushCapped(S.stages, k, v, cfg.page.latency_window)
      break
    case 'sound_event': noteBearing(`ev:${m.event_id}`, m.bearing_deg); S.sounds.set(m.event_id, { ...m, seen: rx }); trimMap(S.sounds, 200); break
    case 'alert': noteBearing(m.ref?.event_id ? `ev:${m.ref.event_id}` : `al:${m.alert_id}`, m.bearing_deg); S.alerts.set(m.alert_id, { ...m, seen: rx }); trimMap(S.alerts, 100); break
    case 'perception_enrichment': if (refId(m.ref)) { S.enrich.set(refId(m.ref), m); trimMap(S.enrich, 500) } break
    case 'attribution': S.attribRx = rx; if (refId(m.ref)) { S.attrib.set(refId(m.ref), m); trimMap(S.attrib, 500) } break
    default: break
  }
  if (STREAM_KINDS.has(m.type) && key) { addStream(key, m, rx); for (const fn of msgListeners) fn(m, key, rx, local) }
  changed()
}

/**
 * A voice number for a talker the phone hasn't named. Source ids don't identify people (fake_events reuses id 0
 * for everyone; beams mode means id 1 = whoever is behind), so a voice is a direction: within VOICE_DEG of a
 * voice heard in the last VOICE_TTL_S, it's the same voice. An utterance keeps its first voice. Assigned on
 * arrival (receive time), so replayed history numbers voices in the order they spoke.
 */
const VOICE_DEG = 30, VOICE_TTL_S = 600     // a number is never reused while captions with it may still be on screen
export function voiceFor(uttId, sourceId, bearing, at) {
  if (uttId && S.voiceOf.has(uttId)) return S.voiceOf.get(uttId)
  const t = at ?? now(), sid = sourceId ?? 0
  S.voices = S.voices.filter(v => t - v.seen < VOICE_TTL_S)
  let v = bearing === null || bearing === undefined ? S.voices.find(x => x.source_id === sid)
    : S.voices.filter(x => Math.abs(wrap(bearing - x.bearing)) <= VOICE_DEG)
      .sort((a, b) => Math.abs(wrap(bearing - a.bearing)) - Math.abs(wrap(bearing - b.bearing)))[0]
  if (!v) {
    const used = new Set(S.voices.map(x => x.n))
    let n = 1; while (used.has(n)) n++
    v = { n, source_id: sid, bearing: bearing ?? 0 }
    S.voices.push(v)
  }
  v.seen = t
  if (bearing !== null && bearing !== undefined) v.bearing = bearing
  if (uttId) { S.voiceOf.set(uttId, v.n); trimMap(S.voiceOf, 500) }
  return v.n
}

function trimMap(map, n) { while (map.size > n) map.delete(map.keys().next().value) }

function addCaption(uttId) {
  const i = S.captionOrder.indexOf(uttId)
  if (i === -1) {
    S.captionOrder.push(uttId)
    while (S.captionOrder.length > 40) {
      const old = S.captionOrder.shift(); S.utts.delete(old); S.speechStart.delete(old)
    }
  }
}

function addStream(key, m, rx) {
  let e = S.streamIndex.get(key)
  if (!e) {
    if (m.type === 'perception_enrichment' || m.type === 'attribution') return   // they decorate an existing row
    e = { key, first: m.type, rx }
    S.streamIndex.set(key, e); S.stream.push(e)
    while (S.stream.length > cfg.page.stream_max) S.streamIndex.delete(S.stream.shift().key)
  }
  e.rx = rx
}

// ---------------------------------------------------------------------------------------------- connection
let ws = null
export function connect() {
  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/live`
  ws = new WebSocket(url)
  ws.onopen = () => { S.pageConnected = true; changed() }
  ws.onclose = () => { S.pageConnected = false; S.status.relay = { connected: false }; changed(); setTimeout(connect, 1000) }
  ws.onerror = () => { try { ws.close() } catch { /* already closed */ } }
  ws.onmessage = ev => {
    let w; try { w = JSON.parse(ev.data) } catch { return }
    if (w.k === 'status') { S.status = w; if (w.demo?.running) S.demoSeen = now(); changed() } else if (w.k === 'msg') ingest(w.m, w.rx, !!w.local)
  }
}

export async function getJSON(path) {
  const r = await fetch(path, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

export async function loadConfig() {
  try { Object.assign(cfg, await getJSON('/dash/config')) } catch { /* defaults */ }
}

export async function refreshMemoryHealth() {
  try { S.memoryHealth = await getJSON('/api/health'); changed() } catch { /* keep last */ }
  try { const i = await getJSON('/api/insights'); S.insightSource = i.source || i.insights?.[0]?.source || null } catch { /* keep last */ }
  try { S.serverStatus = await getJSON('/api/status') } catch { /* keep last */ }
}
