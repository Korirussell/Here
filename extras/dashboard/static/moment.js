// MOMENT: the anatomy of one important event, step by step, for the judges' screen (Live, key F).
// Pure: builds from the same per-event message chain the inspector reads (S.byKey) and the same provider names.
// No DOM here, so tests/js run it under node.
//
// Offsets: every step is "when its message reached this screen" minus the moment's start, on one clock (the demo
// laptop's: the laptop stamps `t`, the dashboard stamps `rx`, both on the same machine, like the latency panel).
// The start is the audio onset the laptop reports (`t_start`) when it has one, else the first message's `t`.
// Phone render and belt buzz are never measured here: those steps say what the phone is told to do.
import {
  S, refId, fmtBearing, dirWord, engineProvider, modelProvider, directionProvider, storageProvider, off, scripted,
  localReplay, rulesAttention, WEARER, isHazard, isRetraction,
} from './core.js'

export const HOLD_S = 8          // an important moment stays at least this long before a lesser one may replace it

/** How much a message deserves the Moment panel: 3 alert / hazard, 2 important speech or a sound upgrade,
 *  1 ordinary speech or sound, 0 never (sources, heartbeats, retractions...). */
export function rankOf(m) {
  switch (m?.type) {
    case 'alert': return m.active === false ? 0 : 3
    case 'sound_event':
      if (isHazard(m.label)) return 3
      if (m.label === 'unknown') return 1
      return S.byKey.get(`ev:${m.event_id}`)?.[0]?.m.label === 'unknown' ? 2 : 1   // an unknown that got its label
    case 'speech_start': return 1
    case 'utterance': {
      if (isRetraction(m)) return 0
      const e = S.enrich.get(m.utt_id)
      return e && (e.attention === 'important' || e.attention === 'urgent') ? 2 : 1
    }
    case 'perception_enrichment': return m.attention === 'important' || m.attention === 'urgent' ? 2 : 0
    default: return 0
  }
}

export const SETTLE_S = 6       // an unfinished moment gets this long to reach its final before an equal-rank one replaces it
export const READ_S = 2.5       // a finished moment stays at least this long after it finished, so it can be read

/** True when this message completes its moment: a final caption, or an alert / classified sound. */
export const completes = m => (m?.type === 'utterance' && m.final) || m?.type === 'alert' || m?.type === 'sound_event'

/** Decide whether `cand` ({key, rank, at}) replaces `cur` ({key, rank, at, doneAt}): a higher rank always does; an
 *  equal rank does READ_S after `cur` finished, or SETTLE_S after it started if it never finished; anything does once
 *  `cur` has been on screen `hold` s. Updates to the same event never count as a replacement. */
export function replaces(cur, cand, hold = HOLD_S, settle = SETTLE_S, read = READ_S) {
  if (!cand || !cand.rank) return false
  if (!cur) return true
  if (cand.key === cur.key) return false
  if (cand.rank > cur.rank || cand.at - cur.at >= hold) return true
  if (cand.rank < cur.rank) return false
  return cur.doneAt !== undefined && cur.doneAt !== null ? cand.at - cur.doneAt >= read : cand.at - cur.at >= settle
}

/** The Moment panel's choice after one message: returns the moment to show ({key, rank, at, doneAt}).
 *  A message for the shown event updates it (a promotion restarts the hold: it became important now); any other
 *  message takes over only when `replaces` says so. */
export function track(cur, m, key, rx, hold = HOLD_S) {
  const cand = { key, rank: rankOf(m), at: rx, doneAt: completes(m) ? rx : null }
  if (cur && key === cur.key) {
    const next = { ...cur }
    if (cand.rank > cur.rank) { next.rank = cand.rank; next.at = rx }
    next.doneAt ??= cand.doneAt
    return next
  }
  return replaces(cur, cand, hold) ? cand : cur
}

const ms = (a, b) => Math.round((a - b) * 1000)
const geminiState = () => {
  const d = S.heartbeats.get('laptop')?.m?.detail || ''
  const paused = /Gemini paused: ([^;(]+)/.exec(d)
  if (paused) return off('Gemini', `paused · ${paused[1].trim()}`)
  for (const e of S.enrich.values()) if (e.context) return off('Gemini', 'no note for this event')
  return off('Gemini', 'not connected')
}

/** The steps for one event key ("utt:<id>", "ev:<id>", "al:<id>"), or null when nothing is known about it.
 *  opts.who(id, source_id, bearing) -> {label}: the page passes its name logic; the default is the array's voice. */
export function buildMoment(key, opts = {}) {
  const msgs = S.byKey.get(key)
  if (!msgs || !msgs.length) return null
  const id = key.slice(key.indexOf(':') + 1)
  const first = (type, pred = () => true) => msgs.find(x => x.m.type === type && pred(x.m))
  const last = type => { for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i].m.type === type) return msgs[i]; return null }
  const ss = first('speech_start'), u1 = first('utterance', m => `${m.text}${m.pending}`.trim()),
    fin = first('utterance', m => m.final), uL = last('utterance'), se1 = first('sound_event'), seL = last('sound_event')
  const al = last('alert')
  const alertMsg = al ? al.m : [...S.alerts.values()].find(a => refId(a.ref) === id) || null
  const enr = last('perception_enrichment')?.m || S.enrich.get(id) || null
  const att = last('attribution')?.m || S.attrib.get(id) || null
  const anchor = (ss || u1 || se1 || al || msgs[0])
  const main = anchor.m
  const local = msgs.some(x => x.local) || localReplay()
  const isScripted = scripted()

  // the start: the audio onset the laptop reports, if it is on the laptop clock and plausible; else the first send
  const onset = [uL?.m.t_start, seL?.m.t_start].find(t => typeof t === 'number' && t <= main.t + 0.001 && main.t - t < 10)
  const t0 = onset ?? main.t
  const at = x => (x && typeof x.rx === 'number' && !local) ? ms(x.rx, t0) : null

  const kind = alertMsg ? 'alert' : (ss || uL) ? 'speech' : seL ? (seL.m.label === 'unknown' ? 'unknown' : isHazard(seL.m.label) ? 'hazard' : 'sound') : 'alert'
  const bearing = (alertMsg ?? uL?.m ?? ss?.m ?? seL?.m)?.bearing_deg ?? null
  const who = opts.who || ((uid, sid, b) => ({ label: att?.person_label || (S.voiceOf.has(uid) ? `Voice ${S.voiceOf.get(uid)}` : 'A voice') }))
  const steps = []
  const step = (id_, label, x, provider, result = '', note = '') => steps.push({ id: id_, label, at: at(x), provider, result, note })

  // 1. the onset, with its direction
  steps.push({ id: 'onset', label: kind === 'speech' ? 'Voice began' : 'Sound began', at: local ? null : 0,
    provider: directionProvider(), result: bearing === null ? 'no direction' : `${fmtBearing(bearing)} · ${dirWord(bearing)}`,
    note: onset !== undefined ? 'audio onset, laptop clock' : 'first message, laptop clock' })

  if (kind === 'speech' || (ss || uL)) {
    if (ss) step('start', 'Voice detected · phone told', ss,
      { name: 'HERE laptop', detail: 'voice activity + direction, before any words', tag: 'local' },
      'typing dots on the speaker', 'phone rings the speaker · belt toward them if not in front (what the phone is told; not measured)')
    if (u1) step('words', 'First words', u1, engineProvider(u1.m.engine) || off('Speech engine', 'engine not reported'),
      `“${`${u1.m.text} ${u1.m.pending}`.trim()}”`)
    else steps.push({ id: 'words', label: 'First words', at: null, provider: off('Speech engine', 'no words yet'), result: '' })
    if (fin) step('final', 'Final caption', fin, engineProvider(fin.m.engine) || off('Speech engine', 'engine not reported'),
      `“${fin.m.text}”`, fin.m.stage_ms?.asr !== undefined ? `transcription ${Math.round(fin.m.stage_ms.asr)} ms` : '')
    if (att || opts.phoneSeen) steps.push({ id: 'who', label: 'Who', at: at(last('attribution')),
      provider: att ? { name: 'iPhone', detail: `Vision faces · ${att.method}`, tag: 'phone' } : off('iPhone', 'no face matched this voice'),
      result: att?.person_label || (att ? 'off camera' : '') })
  }
  if (se1) {
    step('class', seL.m.label === 'unknown' ? 'Heard, not classifiable' : 'Classified', se1,
      modelProvider(se1.m.model) || off('Classifier', 'model not reported'),
      seL.m.label === 'unknown' ? 'unknown · kept with its direction' : `${seL.m.text || seL.m.label} · ${Math.round((seL.m.confidence ?? 0) * 100)}%`)
    if (seL !== se1 && seL.m.label !== se1.m.label) step('upgrade', 'Label upgraded', seL, modelProvider(seL.m.model) || off('Classifier', ''), `${se1.m.label} → ${seL.m.label}`)
  }
  if (alertMsg) {
    const pattern = alertMsg.kind === 'name_called' ? 'belt: name-called pattern toward them'
      : (alertMsg.priority ?? 0) >= 3 ? 'belt: emergency pattern' : 'belt: alert pattern toward it'
    steps.push({ id: 'alert', label: `Alert: ${alertMsg.label || alertMsg.kind}`, at: at(al),
      provider: { name: 'HERE laptop', detail: 'alert rules', tag: 'local' }, result: 'card + belt',
      note: `${pattern} (what the phone is told; not measured)` })
  }
  // attention: the model's (or the rules') call, and what the wearer gets for it
  const enrX = last('perception_enrichment')
  if (enr) steps.push({ id: 'attention', label: 'Attention', at: at(enrX), provider: modelProvider(enr.attention_model),
    result: `${enr.attention} → ${WEARER[enr.attention] || enr.attention}` })
  else steps.push({ id: 'attention', label: 'Attention', at: null,
    provider: { name: 'Deterministic rules', detail: 'no enrichment for this event', tag: 'fallback' },
    result: (a => `${a} → ${WEARER[a]}`)(rulesAttention(seL?.m || uL?.m || ss?.m, alertMsg)) })
  steps.push(enr?.context
    ? { id: 'context', label: 'Context', at: at(enrX), provider: modelProvider(enr.context_model || 'gemini'), result: enr.context }
    : { id: 'context', label: 'Context', at: null, provider: geminiState(), result: '' })
  const st = storageProvider()
  if (st && S.memoryHealth?._source !== 'seed') steps.push({ id: 'memory', label: 'Remembered', at: null, provider: st,
    result: 'mirrored by role memory', note: 'stored off the live path' })

  const words = fin ? fin.m.text : u1 ? `${u1.m.text} ${u1.m.pending}`.trim() : ''
  const title = kind === 'speech'
    ? who(id, (uL || ss)?.m.source_id, bearing).label
    : alertMsg ? `${alertMsg.emoji || ''} ${alertMsg.label || alertMsg.kind}`.trim()
      : seL.m.label === 'unknown' ? 'Unknown sound' : `${seL.m.emoji || ''} ${seL.m.text || seL.m.label}`.trim()
  const timed = steps.filter(s => s.at !== null && s.at !== 0)
  return {
    key, kind, title, words, bearing, steps,
    rank: Math.max(...msgs.map(x => rankOf(x.m)), alertMsg ? 3 : 0),
    source: local ? 'replay' : isScripted ? 'scripted' : 'live',
    span: timed.length ? Math.max(...timed.map(s => s.at)) : null,
  }
}
