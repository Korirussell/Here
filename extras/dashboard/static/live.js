// LIVE WORLD: the orb, the "right now" headline, the pipeline strip, captions, alerts, events, latency, health.
import {
  S, cfg, now, esc, wrap, fmtBearing, fmtTime, median, p95, engineProvider, modelProvider, refId, isHazard, scripted,
  transportProvider, storageProvider, voiceFor, insightProvider, laptopLive, localReplay, serverRelay, steadyBearing, surfacing, alertFor, WEARER, WEARER_LONG,
  onMessage, phoneSeen,
} from './core.js'
import { openInspector, inspectorKey } from './inspector.js'
import { buildMoment, track, HOLD_S } from './moment.js'
import { createOrb } from './orb.js'
import { touring } from './tour.js'

const $ = id => document.getElementById(id)

// ------------------------------------------------------------------------------------------- words
export function whoFor(uttId, sourceId, bearing) {
  const a = S.attrib.get(uttId)
  if (a?.person_label) return { label: a.person_label, by: 'phone' }
  if (a && a.method === 'off_camera') return { label: `Voice ${voiceFor(uttId, sourceId, bearing)}`, by: 'phone-off' }
  return { label: `Voice ${voiceFor(uttId, sourceId, bearing)}`, by: 'array' }
}
/** The best name for a talker: the phone's attribution; else the person its camera sees speaking in that
 * direction (scene, before the final's attribution arrives); else the array's "Voice N". */
export function nameFor(uttId, sourceId, bearing) {
  const w = whoFor(uttId, sourceId, bearing)
  if (w.by !== 'array' || bearing === null || bearing === undefined) return w
  const sc = S.scene && now() - S.sceneRx < 5 ? S.scene : null
  const p = sc?.people?.find(x => x.speaking && Math.abs(((x.bearing_deg - bearing + 540) % 360) - 180) <= 20)
  return p ? { label: p.label, by: 'phone-scene' } : w
}
/** Avatar text: the number of "Voice 2" / "Person 3", else the first letter. */
export const initial = label => (/(\d+)$/.exec(label) || [])[1] || label.slice(0, 1)
export function soundKind(m) {
  if (m.label === 'unknown') return 'unknown'
  if (isHazard(m.label)) return 'hazard'
  return 'sound'
}

/** Direction in words. With the phone's heading it's relative to the wearer ("behind you"), otherwise to the array. */
export function where(b) {
  if (b === null || b === undefined) return { short: 'no direction', phrase: 'somewhere nearby' }
  const fresh = S.scene && now() - S.sceneRx < 5
  const r = fresh ? wrap(b - S.scene.heading_deg) : b
  const a = Math.abs(r), side = r > 0 ? 'right' : 'left'
  let w
  if (a <= 20) w = 'in front'
  else if (a >= 160) w = 'behind'
  else if (a < 70) w = `front-${side}`
  else if (a <= 110) w = `to the ${side}`
  else w = `behind-${side}`
  const you = fresh ? (w === 'in front' ? ' of you' : w === 'behind' ? ' you' : '') : ''
  return { short: w.replace('to the ', ''), phrase: w + you }
}

// ------------------------------------------------------------------------------------------- orb
let orb = null
/** An alert's steadied direction: it shares the history of the sound it refers to, so marker and card agree. */
const alertBearing = a => a.bearing_deg === null || a.bearing_deg === undefined ? a.bearing_deg
  : steadyBearing(a.ref?.event_id ? `ev:${a.ref.event_id}` : `al:${a.alert_id}`, a.bearing_deg)

function liveSounds(t) {
  const out = [], shown = []
  const alertRefs = new Set()
  for (const a of S.alerts.values()) if (a.active && t - a.seen < 30 && a.ref?.event_id) alertRefs.add(a.ref.event_id)
  for (const [id, m] of [...S.sounds].reverse()) {
    if (t - m.seen > cfg.page.sound_ttl_s) continue
    if (m.bearing_deg === null || m.bearing_deg === undefined || alertRefs.has(id)) continue
    const b = steadyBearing(`ev:${id}`, m.bearing_deg)                       // steadied, see core.js
    const near = x => Math.abs(((x - b + 540) % 360) - 180) < 16
    if (shown.some(x => x.label === m.label && near(x.bearing_deg))) continue
    const steady = { ...m, bearing_deg: b, raw_bearing_deg: m.bearing_deg }
    shown.push(steady); out.push([id, steady])
  }
  return out
}

function latestUttFor(sourceId, bearing) {
  for (let i = S.captionOrder.length - 1; i >= Math.max(0, S.captionOrder.length - 8); i--) {
    const id = S.captionOrder[i]
    const m = S.utts.get(id) || S.speechStart.get(id)
    if (m && m.source_id === sourceId && Math.abs(((m.bearing_deg - bearing + 540) % 360) - 180) < 30) return { id, u: S.utts.get(id) }
  }
  return null
}

const alertKey = a => a.ref ? (a.ref.utt_id ? `utt:${a.ref.utt_id}` : `ev:${a.ref.event_id}`) : `al:${a.alert_id}`

function orbTargets() {
  const t = now(), list = []
  const sc = S.scene && t - S.sceneRx < 5 ? { heading: S.scene.heading_deg, people: S.scene.people || [] } : null
  for (const [k, s] of S.sources) {
    if (t - s.seen > cfg.page.source_ttl_s) { if (t - s.seen > 10) S.sources.delete(k); continue }
    if (s.speech) {
      const lu = latestUttFor(s.id, s.bearing_deg)
      // the phone's name for whoever its camera sees speaking there (scene), until the final's attribution
      const seen = sc?.people.find(p => p.speaking && Math.abs(((p.bearing_deg - s.bearing_deg + 540) % 360) - 180) <= 20)
      const att = lu ? nameFor(lu.id, s.id, lu.u?.bearing_deg ?? s.bearing_deg) : null
      const who = att && att.by !== 'array' ? att.label : seen ? seen.label : att ? att.label : `Voice ${voiceFor(null, s.id, s.bearing_deg)}`
      const words = lu?.u ? `${lu.u.text} ${lu.u.pending}`.trim() : ''
      const snip = words.length > 48 ? '…' + words.slice(-47) : words
      list.push({ key: `src:${k}:s`, kind: 'speech', bearing: s.bearing_deg, energy: Math.min(1, s.energy), pick: lu ? `utt:${lu.id}` : null,
        label: `<b>${esc(who)}</b><span class="deg">${fmtBearing(s.bearing_deg)}</span>${snip ? `<div class="say">${esc(snip)}</div>` : ''}` })
    } else if (s.energy > 0.08) {
      list.push({ key: `src:${k}:a`, kind: 'activity', bearing: s.bearing_deg, energy: Math.min(1, s.energy), ripple: false })
    }
  }
  for (const [id, m] of liveSounds(t)) {
    const kind = soundKind(m)
    list.push({ key: `ev:${id}`, kind, bearing: m.bearing_deg, energy: 0.8, pick: `ev:${id}`,
      label: `<span class="em">${kind === 'unknown' ? '?' : esc(m.emoji)}</span><b>${kind === 'unknown' ? 'Unknown sound' : esc(m.text)}</b>${m.count > 1 ? `<span class="x">×${m.count}</span>` : ''}<span class="deg">${fmtBearing(m.bearing_deg)}</span>` })
  }
  for (const a of S.alerts.values()) {
    if (!a.active || t - a.seen > ALERT_PILLAR_S || a.bearing_deg === null || a.bearing_deg === undefined) continue
    const ab = alertBearing(a)
    list.push({ key: `al:${a.alert_id}`, kind: 'alert', bearing: ab, energy: 1, pick: alertKey(a),
      label: `<span class="em">${esc(a.emoji)}</span><b>${esc(a.label)}</b><span class="deg">${fmtBearing(ab)}</span>` })
  }
  const talking = list.filter(x => x.kind === 'speech').map(x => x.bearing)
  for (const p of sc?.people || []) {                      // who the phone's camera sees, where (world frame)
    if (p.bearing_deg === null || p.bearing_deg === undefined) continue
    const named = talking.some(b => Math.abs(((b - p.bearing_deg + 540) % 360) - 180) <= 20)   // the speech label names them
    list.push({ key: `person:${p.label}`, kind: 'person', bearing: p.bearing_deg, energy: 0, ripple: false, pick: null,
      label: named ? null : `<b>${esc(p.label)}</b><span class="deg">in view</span>` })
  }
  orb.setTargets(list, sc)
}

// ------------------------------------------------------------------------------------------- headline
const ALERT_LEAD_S = 6       // an alert leads the headline this long, or until something newer happens
const ALERT_PILLAR_S = 12    // its 3D pillar stays this long (real alerts end sooner)
const ago = s => s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`

/** The idle line: what HERE last heard and where, so a judge never walks up to a blank screen. */
function idleLine(t) {
  let best = null
  for (const m of S.sounds.values()) if (!best || m.seen > best.seen) best = { seen: m.seen, what: m.label === 'unknown' ? 'a sound I couldn’t name' : (m.text || m.label).toLowerCase(), b: m.bearing_deg }
  const lastId = S.captionOrder[S.captionOrder.length - 1], msgs = lastId && S.byKey.get(`utt:${lastId}`)
  if (msgs) {
    const m = S.utts.get(lastId) || S.speechStart.get(lastId), seen = msgs[msgs.length - 1].rx
    if (m && (!best || seen > best.seen)) best = { seen, what: nameFor(lastId, m.source_id, m.bearing_deg).label, b: m.bearing_deg }
  }
  if (best && t - best.seen < 1800) return `Quiet now. Last heard: ${best.what} ${where(best.b).phrase}, ${ago(t - best.seen)}.`
  if (!S.status.relay?.connected) return 'No relay reachable. Press S to play the recorded scenes here.'
  if (!S.heartbeats.size) return 'Waiting for the microphone array. Press S to play a scripted scene.'
  return 'The room is quiet. HERE turns every sound into a direction.'
}

function renderHeadline() {
  const t = now()
  let kind = 'idle', title = 'Listening', words = '', meta = 'The room is quiet. HERE turns every sound into a direction.', key = null
  const speaking = [...S.sources.values()].filter(s => s.speech && t - s.seen < cfg.page.source_ttl_s)
  const lastId = S.captionOrder[S.captionOrder.length - 1]
  const lastU = lastId ? S.utts.get(lastId) : null
  const lastMsgs = lastId ? S.byKey.get(`utt:${lastId}`) : null
  const lastRx = lastMsgs ? lastMsgs[lastMsgs.length - 1].rx : 0
  const snd = liveSounds(t)[0]
  // An alert leads while fresh, or while nothing newer is happening: one whose "end" never came (a scene cut
  // short, a dropped message) must not hide what is happening now.
  const newest = Math.max(lastRx, snd ? snd[1].seen : 0)
  const alert = [...S.alerts.values()].reverse().find(a => a.active && t - a.seen < 30 && (t - a.seen < ALERT_LEAD_S || a.seen >= newest - 0.5))
  if (alert) {
    kind = 'alert'; key = alertKey(alert)
    title = `${alert.label} ${where(alertBearing(alert)).phrase}`
    const enr = S.enrich.get(refId(alert.ref))
    meta = `${fmtBearing(alertBearing(alert))} · priority ${alert.priority}${enr ? ` · attention ${enr.attention}` : ''}${enr?.context ? ` · ${enr.context}` : ''}`
    if (alert.ref?.utt_id && S.utts.get(alert.ref.utt_id)) words = S.utts.get(alert.ref.utt_id).text
  } else if ((speaking.length || t - lastRx < 4) && (lastU || S.speechStart.get(lastId))) {
    const m = lastU || S.speechStart.get(lastId)
    kind = 'speech'; key = `utt:${lastId}`
    const who = nameFor(lastId, m.source_id, m.bearing_deg).label
    title = speaking.length > 1 ? `${speaking.length} people are talking` : `${who} is speaking ${where(m.bearing_deg).phrase}`
    words = lastU ? `${lastU.text} ${lastU.pending}`.trim() : ''
    const eng = lastU ? engineProvider(lastU.engine) : null
    meta = [fmtBearing(m.bearing_deg), lastU?.tone, eng && `captions by ${eng.name}`].filter(Boolean).join(' · ')
  } else if (snd) {
    const [id, m] = snd
    kind = soundKind(m); key = `ev:${id}`
    title = `${m.label === 'unknown' ? 'Something I can’t name' : m.text} ${where(m.bearing_deg).phrase}`
    const p = modelProvider(m.model)
    meta = m.label === 'unknown'
      ? `Unknown sound · ${fmtBearing(m.bearing_deg)} · uncertain what, certain where`
      : `${fmtBearing(m.bearing_deg)} · ${Math.round(m.confidence * 100)}% sure${p ? ` · ${p.name}` : ''}`
  }
  if (kind === 'idle') meta = idleLine(t)
  else if (key) {
    const id = key.slice(key.indexOf(':') + 1)
    const s = kind === 'alert' ? surfacing(refId(alert.ref) || id, null, alert) : surfacing(id, kind === 'speech' ? (lastU || S.speechStart.get(lastId)) : snd?.[1], alertFor(id))
    meta += ` · wearer gets ${WEARER_LONG[s.att]}`
  }
  const el = $('now')
  if (!el.firstChild) el.innerHTML = '<div class="now-kicker"><span class="shape"></span><span class="kt"></span></div><h1 class="now-title"></h1><p class="now-words"></p><div class="now-meta"></div>'
  const kicker = { idle: 'Right now', alert: 'Needs attention', speech: 'Speech', sound: 'Sound', hazard: 'Hazard', unknown: 'Unknown sound' }[kind]
  el.className = 'now'; el.dataset.kind = kind; el.dataset.key = key || ''
  el.querySelector('.now-kicker .shape').className = `shape ${kind}`
  el.querySelector('.kt').textContent = kicker
  const h = el.querySelector('.now-title')
  if (h.textContent !== title) { h.textContent = title; h.classList.remove('enter'); void h.offsetWidth; h.classList.add('enter') }
  const p = el.querySelector('.now-words')
  p.textContent = words ? `“${words}”` : ''; p.hidden = !words
  el.querySelector('.now-meta').textContent = meta
}

// ------------------------------------------------------------------------------------------- pipeline
const lastByType = new Map()
export const lastOf = type => lastByType.get(type)
const PIPE = [
  { id: 'array', name: 'ReSpeaker', sub: () => scripted() ? 'scripted stand-in' : !laptopLive() ? 'not connected' : S.heartbeats.get('laptop')?.m.doa_source === 'odas' ? 'ODAS tracks' : 'XVF3800 · direction', types: ['sources'] },
  { id: 'speech', name: () => engineProvider(lastOf('utterance')?.engine)?.name || 'Speech', sub: () => engineProvider(lastOf('utterance')?.engine)?.detail || 'captions', types: ['speech_start', 'utterance'] },
  { id: 'sounds', name: () => modelProvider(lastOf('sound_event')?.model)?.name || 'Sounds', sub: () => lastOf('sound_event')?.model || 'classifier', types: ['sound_event'] },
  { id: 'attn', name: () => modelProvider(lastOf('perception_enrichment')?.attention_model)?.name || 'Attention', sub: () => (lastOf('perception_enrichment')?.attention_model || 'waiting').split('/').pop(), types: ['perception_enrichment'] },
  { id: 'relay', name: () => transportProvider().name, sub: () => localReplay() ? 'recorded scene' : S.status.relay?.connected ? `session ${S.status.relay.session}` : 'offline', types: ['*'] },
  { id: 'phone', name: 'iPhone', sub: () => S.sceneRx && now() - S.sceneRx < 6 ? 'faces · heading' : 'not seen', types: ['scene', 'attribution'] },
  { id: 'memory', name: () => storageProvider()?.name || 'Memory', sub: () => storageProvider() ? 'memory' : 'not connected', types: [] },
  { id: 'trends', name: () => insightProvider(S.insightSource).name, sub: () => S.insightSource === 'databricks' ? 'pattern job' : S.insightSource ? 'Databricks offline' : 'not connected', types: [] },
]
// Which stages are running on a fallback right now (amber dot): the same providers the inspector names.
const PIPE_TAG = {
  array: () => scripted() ? 'fallback' : '',
  speech: () => engineProvider(lastOf('utterance')?.engine)?.tag,
  sounds: () => modelProvider(lastOf('sound_event')?.model)?.tag,
  attn: () => modelProvider(lastOf('perception_enrichment')?.attention_model)?.tag,
  relay: () => transportProvider().tag,
  memory: () => storageProvider()?.tag,
  trends: () => insightProvider(S.insightSource).tag,
}
const pipeCounts = {}
function renderPipe() {
  const el = $('pipe')
  if (!el.children.length) {
    el.innerHTML = PIPE.map((p, i) => `${i ? '<span class="pipe-link"><i></i></span>' : ''}<div class="pipe-node" id="pipe-${p.id}"><span class="pdot"></span><div class="ptxt"><b></b><span class="psub"></span></div></div>`).join('')
  }
  const total = [...S.counts.values()].reduce((a, b) => a + b, 0)
  for (const p of PIPE) {
    const node = $(`pipe-${p.id}`)
    node.querySelector('b').textContent = typeof p.name === 'function' ? p.name() : p.name
    node.querySelector('.psub').textContent = p.sub()
    const n = p.types.includes('*') ? total : p.types.reduce((s, ty) => s + (S.counts.get(ty) || 0), 0)
    node.classList.toggle('live', p.id === 'memory' ? !!S.memoryHealth : p.id === 'trends' ? !!S.insightSource
      : p.id === 'array' ? n > 0 && (laptopLive() || scripted()) : n > 0)
    node.classList.toggle('fb', PIPE_TAG[p.id]?.() === 'fallback')
    if (n !== pipeCounts[p.id] && n > 0) { node.classList.remove('ping'); void node.offsetWidth; node.classList.add('ping') }
    pipeCounts[p.id] = n
  }
}

// ------------------------------------------------------------------------------------------- panels
/** What HERE chose to surface for this event, and what the wearer gets. */
function attChip(id, m, alert) {
  const s = surfacing(id, m, alert ?? alertFor(id))
  return `<span class="chip att-${s.att}" title="attention ${s.att} by ${esc(s.by)}: the wearer gets ${WEARER_LONG[s.att]}">${s.att} → ${WEARER[s.att]}</span>`
}

function renderCaptions() {
  const ids = S.captionOrder.slice(-cfg.page.captions_max).reverse()
  if (!ids.length) { $('captions').innerHTML = '<div class="empty">No one has spoken yet.</div>'; return }
  $('captions').innerHTML = ids.map((id, i) => {
    const u = S.utts.get(id), ss = S.speechStart.get(id), m = u || ss
    const who = nameFor(id, m.source_id, m.bearing_deg)
    const eng = u ? engineProvider(u.engine) : null
    return `<div class="cap${i === 0 ? ' latest' : ''}${inspectorKey() === 'utt:' + id ? ' sel' : ''}" data-key="utt:${esc(id)}">
      <div class="cap-head"><span class="avatar">${esc(initial(who.label))}</span><b>${esc(who.label)}</b>
        <span class="dirtxt">${esc(where(m.bearing_deg).short)} · ${fmtBearing(m.bearing_deg)}</span>
        ${u && !u.final ? '<span class="live-dot" title="streaming"></span>' : ''}</div>
      <div class="cap-text">${u ? `${esc(u.text)} <span class="pend">${esc(u.pending)}</span>` : '<span class="pend">listening…</span>'}</div>
      <div class="cap-foot">${who.by === 'phone' ? '<span class="chip ghost" title="named by the iPhone (attribution)">iPhone</span>' : who.by === 'phone-scene' ? '<span class="chip ghost" title="the iPhone sees this person speaking here (scene); attribution follows the final">iPhone · in view</span>' : who.by === 'phone-off' ? '<span class="chip ghost" title="the iPhone saw no face in this direction">off camera · iPhone</span>' : ''}${u?.tone ? `<span class="chip">${esc(u.tone)}</span>` : ''}${attChip(id, m)}${eng ? `<span class="chip ghost">${esc(eng.name)}</span>` : ''}</div></div>`
  }).join('')
}

function ringSvg(b, color) {
  if (b === null || b === undefined) return '<svg class="ring" viewBox="0 0 40 40"><circle cx="20" cy="20" r="15" fill="none" style="stroke:var(--line)" stroke-width="2" stroke-dasharray="3 3"/></svg>'
  const th = b * Math.PI / 180, x = 20 + 15 * Math.sin(th), y = 20 - 15 * Math.cos(th)
  return `<svg class="ring" viewBox="0 0 40 40" aria-label="direction ${fmtBearing(b)}"><circle cx="20" cy="20" r="15" fill="none" style="stroke:var(--line)" stroke-width="2"/>
    <line x1="20" y1="20" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" style="stroke:${color}" stroke-width="2.5" stroke-linecap="round"/>
    <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5" style="fill:${color}"/><path d="M20 2 l-3 4 h6 z" style="fill:var(--mark-off)"/></svg>`
}

// Panels re-render every second; an alert row must only play its entrance once, so the panel is rewritten only
// when it changed, and rows that were already on screen are marked `old` (no animation).
let alertsHtml = '', alertsShown = new Set()
function renderAlerts() {
  const t = now()
  const on = a => a.active && t - a.seen < 30                    // an "end" that never came doesn't keep it on
  const list = [...S.alerts.values()].filter(a => on(a) || t - a.seen < 20).reverse().slice(0, 3)
  const row = a => `<div class="alert-row${on(a) ? '' : ' ended'}${alertsShown.has(alertKey(a)) ? ' old' : ''}" data-key="${esc(alertKey(a))}">
      <span class="em">${esc(a.emoji)}</span><div class="grow1"><b>${esc(a.label)}</b><div class="sub">${esc(where(alertBearing(a)).phrase)} · ${fmtBearing(alertBearing(a))}${on(a) ? '' : ' · ended'}</div><div class="alert-att">${attChip(refId(a.ref) || a.alert_id, null, a)}</div></div>
      ${ringSvg(alertBearing(a), on(a) ? 'var(--red)' : 'var(--mark-off)')}</div>`
  const html = list.length ? list.map(row).join('') : '<div class="empty">Nothing needs attention.</div>'
  alertsShown = new Set(list.map(alertKey))
  if (html === alertsHtml) return
  alertsHtml = html
  $('alerts').innerHTML = html
}

function cls(ms, good, ok) { return ms === null ? '' : ms <= good ? 'good' : ms <= ok ? 'meh' : 'slow' }
function fmtMs(ms) { return ms === null ? '–' : ms < 10 ? ms.toFixed(1) : String(Math.round(ms)) }
function firstWords() {
  const out = []
  for (const id of S.captionOrder.slice(-30)) {
    const msgs = S.byKey.get(`utt:${id}`); if (!msgs) continue
    const ss = msgs.find(x => x.m.type === 'speech_start'), u = msgs.find(x => x.m.type === 'utterance')
    if (ss && u) out.push((u.rx - ss.rx) * 1000)
  }
  return out
}
function renderLatency() {
  if (localReplay() && !S.lag.size) { $('latency').innerHTML = '<div class="empty">Not measured during a local replay (no relay, no pipeline).</div>' + latencyReference(); return }
  const rows = [['sources', 'Direction', 100, 250], ['utterance', 'Caption', 100, 250], ['sound_event', 'Sound', 150, 400], ['alert', 'Alert', 150, 400]]
  const fw = firstWords()
  $('latency').innerHTML = rows.map(([type, name, g, o]) => {
    const xs = S.lag.get(type) || [], md = median(xs)
    return `<div class="stat"><span class="k">${name}</span><span class="v ${cls(md, g, o)}">${fmtMs(md)}<small>ms</small></span><span class="p">p95 ${fmtMs(p95(xs))}</span></div>`
  }).join('') + `<div class="stat"><span class="k">Onset → words</span><span class="v">${fmtMs(median(fw))}<small>ms</small></span><span class="p">p95 ${fmtMs(p95(fw))}</span></div>` + latencyReference()
}
/** The hero number on Live: the fast path (direction, laptop → relay → this screen). Live p50 only with a fresh
 *  laptop heartbeat (the real array); otherwise the laptop's measured reference, labelled as such, never passed off as live. */
function renderHero() {
  const el = $('hero'), r = cfg.page.latency_reference, xs = S.lag.get('sources') || []
  let ms, line, tag
  if (laptopLive() && xs.length >= 5) {
    ms = median(xs)
    line = `direction · laptop → relay → this screen · p50, p95 ${fmtMs(p95(xs))} ms`
    tag = '<span class="hero-tag live">live</span>'
  } else if (r) {
    ms = r.direction_p50_ms
    line = `direction on the real array · p95 ${r.direction_p95_ms} ms · measured, n ${r.n}`
    tag = `<span class="hero-tag ref" title="${esc(r.label)}">${scripted() || localReplay() ? 'measured · now scripted' : 'measured'}</span>`
  } else { el.hidden = true; return }
  const target = r ? r.target_ms : 250
  el.hidden = false
  el.innerHTML = `<div class="hero-k">Sound → wearer ${tag}</div>
    <div class="hero-v"><b>${fmtMs(ms)}</b><small>ms</small><span class="hero-tgt">target ≤ ${target} ms</span></div>
    <div class="hero-sub">${esc(line)}</div>`
}

/** the laptop's measured real-array numbers against the target: a labelled reference, never mixed into the live stats. */
function latencyReference() {
  const r = cfg.page.latency_reference
  if (!r) return ''
  const w = v => `${Math.min(100, (100 * v / r.target_ms)).toFixed(1)}%`
  return `<div class="lat-ref" title="${esc(r.label)}">
    <div class="lat-ref-h">Direction on the real array: <b>${r.direction_p50_ms} ms</b> p50 · ${r.direction_p95_ms} ms p95 <span class="dim">(n ${r.n})</span> vs ≤ ${r.target_ms} ms ${esc(r.target_label)}</div>
    <div class="lat-bar" role="img" aria-label="p50 ${r.direction_p50_ms} ms, p95 ${r.direction_p95_ms} ms, target ${r.target_ms} ms"><i class="p95" style="width:${w(r.direction_p95_ms)}"></i><i class="p50" style="width:${w(r.direction_p50_ms)}"></i><span class="tgt">${r.target_ms} ms</span></div>
    <div class="lat-ref-src">${esc(r.label)}</div></div>`
}

function streamRow(key) {
  const msgs = S.byKey.get(key); if (!msgs) return null
  const last = type => { for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i].m.type === type) return msgs[i].m; return null }
  const u = last('utterance'), ss = last('speech_start'), se = last('sound_event'), al = last('alert')
  const id = key.slice(key.indexOf(':') + 1)
  let shape = 'speech', main = '', b = null
  if (u || ss) { const m = u || ss; b = m.bearing_deg; main = `<b>${esc(nameFor(id, m.source_id, m.bearing_deg).label)}</b> ${u ? esc((u.text + ' ' + u.pending).trim()) || '…' : '<span class="pend">speaking…</span>'}` }
  else if (se) { shape = soundKind(se); b = se.bearing_deg; main = `${esc(se.emoji)} <b>${se.label === 'unknown' ? 'Unknown sound' : esc(se.text)}</b>${se.count > 1 ? ` ×${se.count}` : ''}` }
  else if (al) { shape = 'alert'; b = al.bearing_deg; main = `${esc(al.emoji)} <b>${esc(al.label)}</b>` }
  if (al && (u || se)) main += ` <span class="chip att-urgent">${esc(al.label)}</span>`
  main += ' ' + attChip(id, u || ss || se, al)
  return `<div class="ev${inspectorKey() === key ? ' sel' : ''}" data-key="${esc(key)}"><span class="shape ${shape}"></span>
    <span class="main">${main}</span><span class="meta">${fmtBearing(b)}</span><span class="meta">${fmtTime(msgs[0].rx)}</span></div>`
}
function renderStream() {
  const rows = []
  for (let i = S.stream.length - 1; i >= 0 && rows.length < 40; i--) { const r = streamRow(S.stream[i].key); if (r) rows.push(r) }
  $('stream').innerHTML = rows.join('') || '<div class="empty">Waiting for events. Press <kbd>S</kbd> to play the scripted moments.</div>'
}

// ------------------------------------------------------------------------------------------- health
export function renderHealth() {
  const t = now(), r = S.status.relay || {}
  const items = []
  const add = (name, value, st, title = '') => items.push({ name, value, st, title })
  add('Relay', r.connected ? `${r.transport} · ${r.session}` : (S.pageConnected ? (localReplay() ? 'unreachable · local replay' : 'not connected') : 'server offline'),
    r.connected ? 'ok' : localReplay() ? 'warn' : 'bad', r.error || r.url || '')
  const sr = serverRelay()
  if (sr) {
    const name = sr.where === 'vultr' ? 'Vultr relay' : 'Server relay'
    if (!sr.ok) add(name, sr.reason, sr.where === 'vultr' ? 'bad' : 'off')
    else {
      const roles = [...sr.roles].sort().join(', ') || 'no clients in this session'
      const rate = sr.rate !== null ? ` · ${sr.rate.toFixed(1)} msg/s` : ''
      const mirrorDown = sr.where === 'vultr' && !sr.roles.has('laptop')
      add(name, `${roles}${rate}${mirrorDown ? ' · laptop mirror down' : ''}`, mirrorDown ? 'warn' : 'ok',
        sr.ageP50 !== null ? `laptop -> relay age p50 ${Math.round(sr.ageP50)} ms (includes clock offset)` : '')
    }
  }
  const hb = S.heartbeats.get('laptop'), stale = !hb || t - hb.rx > cfg.page.heartbeat_stale_s
  if (scripted()) add('Source', 'scripted (fake_events)', 'warn', 'no laptop heartbeat: events come from tools/fake_events.py')
  else add('Laptop', hb ? (stale ? 'no heartbeat' : (hb.m.detail || hb.m.status)) : 'no heartbeat yet', !hb ? 'off' : stale ? 'bad' : hb.m.status === 'ok' ? 'ok' : hb.m.status === 'degraded' ? 'warn' : 'bad')
  const eng = engineProvider(hb?.m.engine)
  add('Captions', eng ? eng.detail : '—', !eng || stale ? 'off' : hb.m.engine === 'scribe_realtime' ? 'ok' : 'warn')
  add('Direction', hb?.m.doa_source === 'odas' ? 'ODAS' : hb?.m.doa_source ? 'ReSpeaker built-in DoA' : '—', hb?.m.doa_source && !stale ? 'ok' : 'off')
  const enr = lastOf('perception_enrichment')
  add('Attention', enr ? enr.attention_model : '—', enr ? (enr.attention_model === 'rules' ? 'warn' : 'ok') : 'off')
  const phoneSeen = Math.max(S.heartbeats.get('phone')?.rx || 0, S.sceneRx || 0)
  const phb = S.heartbeats.get('phone')?.m
  const pthermal = phb?.thermal ? ` · thermal ${phb.thermal}` : ''
  add('iPhone', phoneSeen ? (t - phoneSeen < 6 ? (phb?.status === 'degraded' ? `degraded: ${phb.detail || 'camera'}` : `connected${pthermal}`) : 'quiet') : 'not seen',
    !(phoneSeen && t - phoneSeen < 6) ? 'off' : phb?.status === 'degraded' || phb?.thermal === 'serious' || phb?.thermal === 'critical' ? 'warn' : 'ok')
  const mh = S.memoryHealth
  if (mh) add('Memory', mh._source === 'seed' ? 'seeded JSON (API offline)' : mh.db === 'neon' ? 'Neon' : 'local SQLite', mh._source === 'seed' ? 'warn' : 'ok')
  const worst = items.some(i => i.st === 'bad') ? 'bad' : items.some(i => i.st === 'warn') ? 'warn' : 'ok'
  $('sys-label').textContent = localReplay() ? 'Local replay' : { ok: 'All systems normal', warn: scripted() ? 'Scripted demo' : 'Fallbacks active', bad: r.connected ? 'Issue' : 'Relay offline' }[worst]
  $('sys-dot').className = `sdot ${worst}`
  $('sys-list').innerHTML = items.map(i => `<div class="sys-row" title="${esc(i.title)}"><span class="sdot ${i.st}"></span><span class="k">${esc(i.name)}</span><span class="v">${esc(i.value)}</span></div>`).join('')

  const banner = $('banner')
  let msg = ''
  if (!S.pageConnected) msg = 'The dashboard server isn’t running. Start it with <code>python -m extras.dashboard</code>.'
  else if (touring()) msg = '<b>Judge tour</b> · recorded scenes and the seeded day, not live · any key or click takes over'
  else if (localReplay()) msg = `<b>Local replay</b> · ${esc(S.status.demo.label.replace(' · local replay', ''))} · no relay is reachable, so the dashboard is playing a recorded scene itself. Not live.`
  else if (!r.connected) msg = `<b>No relay reachable.</b> Press <kbd>S</kbd> to play the recorded scenes locally. <span class="dim">(Relay: <code>${esc(r.url || '')}</code>; start one with <code>python -m relay</code>.)</span>`
  else if (S.status.demo?.running) msg = `<b>Scripted scene</b> · ${esc(S.status.demo.label)} · these events come from <code>tools/fake_events.py</code>, not the microphones.`
  else if (scripted()) msg = '<b>Scripted</b> · events come from <code>tools/fake_events.py</code>, not the microphones.'
  banner.innerHTML = msg ? `<span>${msg}</span>` : ''; banner.hidden = !msg
}

// ------------------------------------------------------------------------------------------- moment (F)
// The anatomy of the latest important moment (static/moment.js builds it): what happened, when each step reached
// this screen, and who did it. Holds an important moment HOLD_S before a lesser one may take its place.
let current = null               // {key, rank, at}
const holdS = () => cfg.page.moment_hold_s ?? HOLD_S
onMessage((m, key, rx) => { current = track(current, m, key, rx, holdS()) })
export const momentOn = () => document.documentElement.classList.contains('moment-view')
export function setMoment(on) {
  document.documentElement.classList.toggle('moment-view', on)
  $('moment-btn')?.setAttribute('aria-pressed', String(on))
  try { localStorage.setItem('here.moment', on ? '1' : '0') } catch { /* private window: per session only */ }
  renderMoment()
}
export function clearMoment() { current = null; renderMoment() }

const SOURCE_TAG = { live: '<span class="m-src live">live</span>', scripted: '<span class="m-src ref">scripted</span>',
  replay: '<span class="m-src ref">replay · not timed</span>' }
function ptag(p) {
  if (!p) return ''
  return `<span class="ptag ${esc(p.tag)}" title="${esc(p.detail ? `${p.name} · ${p.detail}` : p.name)}"><b>${esc(p.name)}</b>${p.detail ? `<span>${esc(p.detail)}</span>` : ''}</span>`
}
let shownKey = null, shownSteps = new Set()
function renderMoment() {
  const el = $('moment')
  const mo = momentOn() && current ? buildMoment(current.key, { who: nameFor, phoneSeen: phoneSeen() }) : null
  el.hidden = !mo                                   // nothing yet: the headline stays (the button shows it's on)
  $('now').hidden = !!mo
  if (!mo) { shownKey = null; return }
  const fresh = mo.key !== shownKey
  if (fresh) { shownKey = mo.key; shownSteps = new Set() }
  const where_ = mo.bearing === null ? '' : `${esc(where(mo.bearing).short)} · ${fmtBearing(mo.bearing)}`
  const span = mo.span !== null && mo.source !== 'replay' ? ` · <span class="m-span">${mo.span} ms</span> onset → last step` : ''
  const rows = mo.steps.map(s => {
    const enter = shownSteps.has(s.id) ? '' : ' enter'
    shownSteps.add(s.id)
    const t = s.at === null ? '<span class="dim">—</span>' : s.at === 0 ? '0' : `+${s.at < 1000 ? `${s.at} ms` : `${(s.at / 1000).toFixed(2)} s`}`
    return `<li class="mstep${enter}${s.at === null ? ' untimed' : ''}"><span class="mt">${t}</span>
      <div class="mb"><div class="ml">${esc(s.label)}</div>${s.result ? `<div class="mr">${esc(s.result)}</div>` : ''}${s.note ? `<div class="mn">${esc(s.note)}</div>` : ''}</div>
      <div class="mp">${ptag(s.provider)}</div></li>`
  }).join('')
  el.dataset.kind = mo.kind; el.dataset.key = mo.key
  el.innerHTML = `<div class="m-kicker"><span class="shape ${mo.kind}"></span>Moment ${SOURCE_TAG[mo.source]}${span}</div>
    <h2 class="m-title${fresh ? ' enter' : ''}">${esc(mo.title)}</h2>
    ${mo.words ? `<p class="m-words">“${esc(mo.words)}”</p>` : ''}${where_ ? `<div class="m-where">${where_}</div>` : ''}
    <ol class="msteps">${rows}</ol>
    <p class="m-foot">Times: when each message reached this screen, from the onset, on the demo laptop's clock. Phone and belt lines say what the phone is told; they aren't measured here.</p>`
  if (fresh) { el.classList.remove('swap'); void el.offsetWidth; el.classList.add('swap') }
  const list = el.querySelector('.msteps')                 // fade the edge only when steps are actually cut off
  list.classList.toggle('more', list.scrollHeight > list.clientHeight + 1)
}

// ------------------------------------------------------------------------------------------- wiring
for (const id of ['captions', 'alerts', 'stream', 'now', 'moment']) {
  $(id).addEventListener('click', ev => {
    const el = ev.target.closest('[data-key]')
    if (el && el.dataset.key) openInspector(el.dataset.key)
  })
}

let lastRev = -1
export function renderLive() {
  for (const [, msgs] of [...S.byKey].slice(-6)) for (const x of msgs.slice(-3)) lastByType.set(x.m.type, x.m)
  renderHeadline(); renderPipe()
  if (S.rev === lastRev) return
  lastRev = S.rev
  renderCaptions(); renderAlerts(); renderStream(); renderMoment()
}

export function startLive() {
  try { orb = createOrb($('orb'), $('tags'), key => key && openInspector(key)) } catch (e) {
    console.error('[here] orb unavailable', e); $('orb').dataset.gl = 'failed'
    orb = { setTargets() {}, ripple() {}, resetCamera() {}, applyTheme() {} }
  }
  document.addEventListener('here:theme', () => orb.applyTheme())
  setInterval(orbTargets, 66)
  setInterval(() => { renderLatency(); renderHero(); renderHealth(); lastRev = -1 }, 1000)
}
export function resetCamera() { orb?.resetCamera() }
