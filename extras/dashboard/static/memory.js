// MEMORY: the day (the memory service's /api/day, or the seeded day), Ask HERE, insights, things you may have missed.
import { esc, fmtBearing, dirWord, fmtClock, getJSON, insightProvider, storageProvider, refId } from './core.js'
import { openInspector } from './inspector.js'

const $ = id => document.getElementById(id)
export const M = { day: null, index: new Map(), enrich: new Map(), alerts: new Map(), loaded: false, flash: null }

// The seed bundle's known questions (/api/suggest); this list only shows if that fails.
export const SUGGESTIONS = ['When was my name called?', 'What did I miss today?', 'Where did the car horn come from?']

// a final utterance with no words is a retraction, not an event
const isEvent = e => (e.type === 'utterance' && e.final && !!`${e.text || ''}`.trim()) || e.type === 'sound_event' || (e.type === 'alert' && !refId(e.ref))
const idOf = e => e.utt_id || e.event_id || e.alert_id
const keyFor = e => e.utt_id ? `utt:${e.utt_id}` : e.event_id ? `ev:${e.event_id}` : `al:${e.alert_id}`

export async function loadDay(force = false) {
  if (M.loaded && !force) return M.day
  const day = await getJSON('/api/day')
  M.day = day; M.index.clear(); M.enrich.clear(); M.alerts.clear()
  for (const e of day.events || []) {
    const k = e.type === 'perception_enrichment' || e.type === 'attribution' || e.type === 'alert'
      ? (refId(e.ref) ? (e.ref.utt_id ? `utt:${e.ref.utt_id}` : `ev:${e.ref.event_id}`) : `al:${e.alert_id}`)
      : keyFor(e)
    if (!M.index.has(k)) M.index.set(k, [])
    M.index.get(k).push({ m: e, rx: e.t })
    if (e.type === 'perception_enrichment') M.enrich.set(refId(e.ref), e)
    if (e.type === 'alert' && refId(e.ref)) M.alerts.set(refId(e.ref), e)
  }
  M.loaded = true
  return day
}

export function inspectStored(key) { openInspector(key, { messages: M.index.get(key) || [] }) }

function kindOf(e) {
  if (e.type === 'utterance') return 'speech'
  if (e.type === 'alert') return 'alert'
  if (e.label === 'unknown') return 'unknown'
  if (['car_horn', 'siren', 'alarm', 'loud_bang'].includes(e.label)) return 'hazard'
  return 'sound'
}

// ------------------------------------------------------------------------------------------- timeline
const GAP = 26
function scale(segs, width) {
  const total = segs.reduce((s, g) => s + (g.end - g.start), 0) || 1
  const usable = width - GAP * (segs.length - 1)
  let x = 0
  const parts = segs.map(g => { const w = usable * (g.end - g.start) / total; const p = { ...g, x0: x, x1: x + w }; x += w + GAP; return p })
  const fx = t => {
    for (const p of parts) if (t < p.end) return t < p.start ? p.x0 : p.x0 + (p.x1 - p.x0) * (t - p.start) / (p.end - p.start)
    return width
  }
  return { parts, fx }
}

// Segment headers: the longest wording that fits the box ("Commute: University Ave crosswalk" -> "Commute" ->
// "Comm…"), measured with the page font; the full text is always in the hover title.
let measureCtx = null
function textWidth(s, font) {
  measureCtx = measureCtx || document.createElement('canvas').getContext('2d')
  measureCtx.font = font
  return measureCtx.measureText(s).width
}
function fit(s, maxW, font) {
  s = `${s || ''}`
  if (!s || maxW <= 0) return ''
  const short = s.split(':')[0].trim(), first = s.split(/\s+/)[0].replace(/[:,.]$/, '')
  for (const c of [s, short, first]) if (textWidth(c, font) <= maxW) return c
  let c = first
  while (c.length > 1 && textWidth(c + '…', font) > maxW) c = c.slice(0, -1)
  return c.length > 1 ? c + '…' : ''
}
const SEG_FONT = '600 13px Geist, system-ui, sans-serif', SEG_SUB = '12px Geist, system-ui, sans-serif', SEG_TIME = '11px Geist, system-ui, sans-serif'

const SHAPE = {
  speech: (x, y, c) => `<circle cx="${x}" cy="${y}" r="4" style="fill:var(--blue)" class="tick ${c}"/>`,
  sound: (x, y, c) => `<rect x="${x - 4}" y="${y - 4}" width="8" height="8" transform="rotate(45 ${x} ${y})" style="fill:var(--amber)" class="tick ${c}"/>`,
  hazard: (x, y, c) => `<rect x="${x - 4.5}" y="${y - 4.5}" width="9" height="9" transform="rotate(45 ${x} ${y})" style="fill:var(--red)" class="tick ${c}"/>`,
  unknown: (x, y, c) => `<circle cx="${x}" cy="${y}" r="4.5" fill="none" style="stroke:var(--violet)" stroke-width="2" stroke-dasharray="2 2" class="tick ${c}"/>`,
  alert: (x, y, c) => `<path d="M${x - 5} ${y - 4} h10 l-5 9 z" style="fill:var(--red)" class="tick ${c}"/>`,
}

function renderTimeline() {
  const day = M.day
  const el = $('timeline')
  const width = Math.max(600, el.clientWidth)
  const segs = [...(day.segments || [])].sort((a, b) => a.start - b.start)
  if (!segs.length) { el.innerHTML = '<div class="empty">No segments.</div>'; return }
  const { parts, fx } = scale(segs, width - 110)
  const L = 110
  const events = (day.events || []).filter(isEvent)
  const people = {}
  for (const e of events) if (e.type === 'utterance' && e.person_label) people[e.person_label] = (people[e.person_label] || 0) + 1
  const recurring = Object.entries(people).filter(([, n]) => n >= 2).sort((a, b) => b[1] - a[1]).map(([p]) => p)
  const rowY = { seg: 6, sounds: 92, crowd: 122 }
  recurring.forEach((p, i) => { rowY[`p:${p}`] = 152 + i * 26 })
  const height = 152 + recurring.length * 26
  let svg = `<svg viewBox="0 0 ${width} ${height}" height="${height}" role="img" aria-label="Timeline of the day">`
  // segments with a noise summary
  parts.forEach((p, i) => { svg += `<clipPath id="segclip${i}"><rect x="${L + p.x0}" y="${rowY.seg}" width="${Math.max(0, p.x1 - p.x0 - 6)}" height="70"/></clipPath>` })
  for (const [i, p] of parts.entries()) {
    const evs = events.filter(e => e.t >= p.start && e.t < p.end)
    const sp = evs.filter(e => e.type === 'utterance').length
    const unk = evs.filter(e => e.label === 'unknown').length
    const snd = evs.length - sp
    const perMin = evs.length / Math.max(1, (p.end - p.start) / 60)
    const fill = `color-mix(in srgb, var(--blue) ${((0.03 + Math.min(0.12, perMin * 0.07)) * 100).toFixed(1)}%, transparent)`   // busier = bluer
    const w = p.x1 - p.x0, room = w - 16
    const stats = `${fmtClock(p.start)} · ${sp} speech · ${snd} sounds${unk ? ` · ${unk} ?` : ''}`
    const clock = fmtClock(p.start)                    // a time is never shortened at its colon: whole or not at all
    const timeLine = textWidth(stats, SEG_TIME) <= room ? stats : textWidth(clock, SEG_TIME) <= room ? clock : ''
    svg += `<g class="seg" data-seg="${esc(p.id)}" clip-path="url(#segclip${i})"><title>${esc(`${p.label}${p.location ? ` · ${p.location}` : ''} · ${stats}`)}</title>
      <rect x="${L + p.x0}" y="${rowY.seg}" width="${w}" height="70" rx="8" style="fill:${fill};stroke:var(--line)"/>
      <text x="${L + p.x0 + 8}" y="${rowY.seg + 20}" style="fill:var(--text);font-weight:600;font-size:13px">${esc(fit(p.label, room, SEG_FONT))}</text>
      <text x="${L + p.x0 + 8}" y="${rowY.seg + 38}">${esc(fit(p.location, room, SEG_SUB))}</text>
      <text x="${L + p.x0 + 8}" y="${rowY.seg + 58}" style="font-size:11px">${esc(timeLine)}</text></g>`
  }
  svg += `<text x="0" y="${rowY.sounds + 4}">Sounds</text><text x="0" y="${rowY.crowd + 4}">Background talk</text>`
  recurring.forEach(p => { svg += `<text x="0" y="${rowY[`p:${p}`] + 4}" style="fill:var(--blue-ink);font-weight:500">${esc(p)}</text>` })
  for (const p of parts) for (const r of Object.values(rowY).slice(1)) svg += `<line x1="${L + p.x0}" x2="${L + p.x1}" y1="${r}" y2="${r}" style="stroke:var(--bg-3)"/>`
  for (const e of events) {
    const x = (L + fx(e.t)).toFixed(1)
    const k = kindOf(e)
    let y
    if (e.type === 'utterance') y = e.person_label && rowY[`p:${e.person_label}`] ? rowY[`p:${e.person_label}`] : rowY.crowd
    else y = rowY.sounds
    const key = keyFor(e)
    const enr = M.enrich.get(idOf(e))
    const big = enr && (enr.attention === 'important' || enr.attention === 'urgent')
    svg += `<g data-key="${esc(key)}" class="tick-g${M.flash?.has(key) ? ' flashing' : ''}">${SHAPE[k](x, y, big ? 'big' : '')}${M.flash?.has(key) ? `<circle cx="${x}" cy="${y}" r="9" fill="none" style="stroke:var(--text)" stroke-width="1.5"/>` : ''}<title>${esc(fmtClock(e.t))} ${esc(e.person_label || '')} ${esc(e.text || e.label || '')} (${dirWord(e.bearing_deg)})</title></g>`
  }
  svg += '</svg>'
  el.innerHTML = svg
}

// ------------------------------------------------------------------------------------------- cards
function line(e, extra = '') {
  const k = kindOf(e)
  const em = e.type === 'utterance' ? '🗨️' : e.emoji || '🔊'
  const who = e.person_label ? `<b>${esc(e.person_label)}</b> ` : ''
  const what = e.type === 'utterance' ? `“${esc(e.text)}”` : esc(e.label === 'unknown' ? 'Unknown sound' : e.text || e.label)
  return `<div class="mini" data-key="${esc(keyFor(e))}"><span class="em">${em}</span><div>
    <div class="t1">${who}${what}</div>
    <div class="t2">${fmtClock(e.t)} · <span class="shape ${k}"></span> ${dirWord(e.bearing_deg)} ${fmtBearing(e.bearing_deg)}${extra}</div></div></div>`
}

function renderCards() {
  const events = (M.day.events || []).filter(isEvent)
  const att = e => M.enrich.get(idOf(e))
  const missed = events.filter(e => {
    const a = att(e)
    const behind = e.bearing_deg !== null && Math.abs(e.bearing_deg) >= 120
    const alertish = M.alerts.has(idOf(e)) || (a && (a.attention === 'important' || a.attention === 'urgent'))
    return behind && alertish && (e.type !== 'utterance' || (a?.directed_at_user ?? 0) >= 0.5 || M.alerts.has(idOf(e)))
  }).slice(0, 7)
  $('missed').innerHTML = missed.map(e => line(e, ' · <b>behind you</b>')).join('') || '<div class="empty">Nothing from behind.</div>'
  const imp = events.filter(e => { const a = att(e); return a && (a.attention === 'urgent' || a.attention === 'important') })
  $('important').innerHTML = imp.slice(0, 9).map(e => {
    const a = att(e)
    return line(e, ` · <span class="chip att-${a.attention}">${a.attention}</span>${a.context ? ` · ${esc(a.context)}` : ''}`)
  }).join('') || '<div class="empty">No important moments.</div>'
}

async function renderInsights() {
  let data
  try { data = await getJSON('/api/insights') } catch { $('insights').innerHTML = '<div class="empty">Insights unavailable.</div>'; return }
  $('insights').innerHTML = (data.insights || []).sort((a, b) => b.score - a.score).map(i => {
    const p = insightProvider(i.source)
    return `<div class="ins"><div class="t1">${esc(i.title)}</div><div class="t2">${esc(i.text)}</div>
      <div class="foot"><span class="chip">${esc(i.category)}</span><span class="ptag ${p.tag}"><b>${esc(p.name)}</b><span>${esc(p.detail)}</span></span>
      ${(i.evidence_ids || []).length ? `<button class="chip ins-ev" data-ids="${esc((i.evidence_ids || []).join(','))}">show ${i.evidence_ids.length} on timeline</button>` : ''}
      <span class="score">${Math.round(i.score * 100)}</span></div></div>`
  }).join('')
}

function keysForIds(ids) {
  const out = new Set()
  for (const id of ids) for (const k of [`utt:${id}`, `ev:${id}`, `al:${id}`]) if (M.index.has(k)) out.add(k)
  return out
}
function flash(keys) {
  M.flash = keys; renderTimeline()
  $('timeline').scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

// ------------------------------------------------------------------------------------------- Ask HERE
export async function ask(q) {
  $('ask-q').value = q
  $('ask-answer').innerHTML = '<div class="answer dim">Thinking…</div>'
  let r
  try { r = await getJSON(`/api/ask?q=${encodeURIComponent(q)}`) } catch { $('ask-answer').innerHTML = '<div class="empty">Ask HERE is unavailable.</div>'; return }
  const ev = r.evidence || []
  const src = r._source === 'api' ? `memory API${r.cached ? ' (cached)' : ''}` : `seeded day · ${r._mode || 'offline'}`
  // honest provenance: a seeded fallback says why; a Gemini-phrased answer says so (the evidence is always the database's)
  const why = r._source === 'seed' && r._fallback_reason ? `<span class="ask-note fb">seeded answer · ${esc(r._fallback_reason)}</span>` : ''
  const gem = r.phrased_by && r.phrased_by !== 'template' ? `<span class="ask-note gem">phrased by Gemini</span>` : ''
  $('ask-answer').innerHTML = `<div class="answer">${esc(r.answer)}</div><div class="answer-meta">from ${esc(src)} · ${ev.length} evidence event${ev.length === 1 ? '' : 's'}${why || gem ? ` ${why}${gem}` : ''}</div>` +
    ev.map(x => {
      const id = x.utt_id || x.event_id
      const key = x.utt_id ? `utt:${x.utt_id}` : `ev:${x.event_id}`
      return `<div class="evi" data-key="${esc(key)}" data-id="${esc(id)}"><span class="tm">${x.t ? fmtClock(x.t) : ''}</span>
        <span>${x.person_label ? `<span class="who">${esc(x.person_label)}</span> ` : ''}${esc(x.text)}</span>
        <span class="dir">${dirWord(x.bearing_deg)} ${fmtBearing(x.bearing_deg)}</span></div>`
    }).join('')
  flash(keysForIds(ev.map(x => x.utt_id || x.event_id)))
}

// ------------------------------------------------------------------------------------------- render
export async function renderMemory(force = false) {
  try { await loadDay(force) } catch { $('day-title').textContent = 'Memory unavailable'; return }
  const d = M.day
  const dt = d.date ? new Date(d.date + 'T12:00:00') : new Date((d.segments?.[0]?.start || Date.now() / 1000) * 1000)
  $('day-title').textContent = dt.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })
  const n = (d.events || []).filter(isEvent).length
  $('day-sub').textContent = `${n} events · ${(d.segments || []).length} places · ${d.label || ''}`
  const st = storageProvider()
  $('mem-source').innerHTML = d._source !== 'api'
    ? '<span class="badge seed">SEEDED synthetic day: memory API offline</span>'
    : d.seeded ? `<span class="badge seed">SEEDED synthetic day · via memory API (${esc(st?.name || 'API')})</span>`
      : `<span class="badge api">live memory · ${esc(st?.name || 'API')}</span>`
  renderTimeline(); renderCards(); renderInsights()
  try {
    const s = await getJSON('/api/suggest')
    if (s.questions?.length) SUGGESTIONS.splice(0, SUGGESTIONS.length, ...s.questions)
  } catch { /* keep the built-in list */ }
  $('ask-suggest').innerHTML = SUGGESTIONS.map(s => `<button type="button">${esc(s)}</button>`).join('')
}

$('timeline').addEventListener('click', ev => {
  const g = ev.target.closest('[data-key]'); if (g) { inspectStored(g.dataset.key); return }
  const s = ev.target.closest('[data-seg]'); if (s) document.dispatchEvent(new CustomEvent('here:replay-segment', { detail: s.dataset.seg }))
})
for (const id of ['missed', 'important', 'ask-answer']) {
  $(id).addEventListener('click', ev => { const el = ev.target.closest('[data-key]'); if (el) inspectStored(el.dataset.key) })
}
$('insights').addEventListener('click', ev => {
  const b = ev.target.closest('.ins-ev'); if (b) flash(keysForIds(b.dataset.ids.split(',')))
})
$('ask-suggest').addEventListener('click', ev => { const b = ev.target.closest('button'); if (b) ask(b.textContent) })
$('ask-form').addEventListener('submit', ev => { ev.preventDefault(); const q = $('ask-q').value.trim(); if (q) ask(q) })
window.addEventListener('resize', () => { if (M.loaded && document.getElementById('memory').classList.contains('on')) renderTimeline() })
