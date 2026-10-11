// REPLAY LAB: one recorded segment, the actual policy vs an alternative. Decisions come from the memory service's
// /api/replay, or the dashboard's offline replay policy (extras/dashboard/fallback.py) on the seeded day.
import { esc, fmtClock, fmtBearing, dirWord, getJSON } from './core.js'
import { M, loadDay, inspectStored } from './memory.js'

const $ = id => document.getElementById(id)
const ACTUAL = { sensitivity: 0.6, crowd_filter: 0.3, focus_mode: false, hazard_priority: 0.7, speech_priority: 0.6 }
const PRESETS = {
  'As it happened': { ...ACTUAL },
  'Focus mode': { ...ACTUAL, focus_mode: true, sensitivity: 0.45 },
  'Crowd shield': { ...ACTUAL, crowd_filter: 1, speech_priority: 0.4 },
  'Hear everything': { sensitivity: 1, crowd_filter: 0, focus_mode: false, hazard_priority: 0.5, speech_priority: 0.5 },
  'Hazards first': { ...ACTUAL, hazard_priority: 1, sensitivity: 0.35, speech_priority: 0.3 },
}
const SLIDERS = [
  ['sensitivity', 'Sensitivity', 'how much reaches the wearer'],
  ['crowd_filter', 'Crowd filtering', 'background talk and media not aimed at you'],
  ['speech_priority', 'Speech priority', 'conversation vs everything else'],
  ['hazard_priority', 'Hazard priority', 'horns, sirens, alarms, bangs'],
]
const CATS = ['speech', 'alert', 'hazard', 'sound', 'unknown', 'media']
const R = { seg: null, alt: { ...PRESETS['Hear everything'] }, preset: 'Hear everything', actual: null, altRes: null, filter: 'changed', open: new Set(), shown: { a: 0, b: 0 } }

function sliders() {
  $('rl-sliders').innerHTML = SLIDERS.map(([k, name, hint]) => `<div class="slider"><label for="rl-${k}">${name}<span id="rl-${k}-v">${R.alt[k].toFixed(2)}</span></label>
    <input id="rl-${k}" type="range" min="0" max="1" step="0.05" value="${R.alt[k]}" data-k="${k}"><div class="hint">${hint}</div></div>`).join('') +
    `<label class="toggle"><input id="rl-focus" type="checkbox" ${R.alt.focus_mode ? 'checked' : ''}><span><b>Focus mode</b><span>Raise the bar. Important alerts still get through.</span></span></label>`
  $('rl-presets').innerHTML = Object.keys(PRESETS).map(p => `<button type="button" data-p="${esc(p)}">${esc(p)}</button>`).join('')
}

function q(policy) {
  return `/api/replay?segment_id=${encodeURIComponent(R.seg)}&policy=${encodeURIComponent(JSON.stringify(policy))}`
}

let timer = null
function schedule() { clearTimeout(timer); timer = setTimeout(run, 90) }

async function run() {
  if (!R.seg) return
  try {
    const [a, b] = await Promise.all([R.actual?.segment_id === R.seg ? R.actual : getJSON(q(ACTUAL)), getJSON(q(R.alt))])
    R.actual = a; R.altRes = b
  } catch { $('rl-compare').innerHTML = '<div class="empty">Replay unavailable.</div>'; return }
  $('rl-source').innerHTML = R.altRes._source === 'api'
    ? '<span class="badge api">decisions from the memory API</span>'
    : '<span class="badge seed">SEEDED day · offline replay policy</span>'
  render()
}

function tween(el, from, to) {
  const t0 = performance.now()
  const step = now => {
    const k = Math.min(1, (now - t0) / 450), e = 1 - Math.pow(1 - k, 3)
    el.textContent = Math.round(from + (to - from) * e)
    if (k < 1) requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}

function column(res, title, side, base) {
  const total = res.surfaced + res.suppressed
  const cats = CATS.filter(c => res.by_category?.[c]).map(c => {
    const x = res.by_category[c], n = x.surfaced + x.suppressed
    return `<div class="catrow"><span>${c}</span><span class="catbar"><i class="${c}" style="width:${(100 * x.surfaced / Math.max(1, n)).toFixed(1)}%"></i></span><span class="n">${x.surfaced}/${n}</span></div>`
  }).join('')
  return `<div><h3>${title}</h3><div class="big-n"><span id="rl-n-${side}">${R.shown[side]}</span><small>of ${total} surfaced</small>${base ? delta(res.surfaced - base.surfaced) : ''}</div>
    <div class="kept"><b>${res.kept_important}</b> important alert${res.kept_important === 1 ? '' : 's'} kept${res.missed_important ? ` · <b class="missed">${res.missed_important} missed</b>` : ''} · ${res.suppressed} kept as ambient</div>
    <div class="cats">${cats}</div></div>`
}

const delta = d => `<span class="delta ${d < 0 ? 'down' : d > 0 ? 'up' : 'same'}">${d > 0 ? '+' : ''}${d}</span>`

function render() {
  const a = R.actual, b = R.altRes
  $('rl-compare').innerHTML = `<div class="cmp">${column(a, 'As it happened', 'a')}<div class="divider"></div>${column(b, `Alternative · ${esc(R.preset)}`, 'b', a)}</div>`
  tween($('rl-n-a'), R.shown.a, a.surfaced); tween($('rl-n-b'), R.shown.b, b.surfaced)
  R.shown = { a: a.surfaced, b: b.surfaced }
  const altBy = new Map((b.decisions || []).map(d => [d.event_id, d]))
  const rows = (a.decisions || []).map(d => ({ d, alt: altBy.get(d.event_id) })).filter(x => x.alt)
  const changed = rows.filter(x => x.d.surfaced !== x.alt.surfaced).length
  const list = rows.filter(x => R.filter === 'all' || (R.filter === 'changed' ? x.d.surfaced !== x.alt.surfaced : x.alt.surfaced))
  const dec = d => d.important ? (d.surfaced ? '<span class="dec imp">KEPT ‼</span>' : '<span class="dec imp missed">MISSED ‼</span>') : d.surfaced ? '<span class="dec on">SURFACED</span>' : '<span class="dec off">ambient</span>'
  $('rl-events').innerHTML = `<div class="rl-filter chips">
      <button type="button" data-f="changed" class="${R.filter === 'changed' ? 'on' : ''}">Changed (${changed})</button>
      <button type="button" data-f="surfaced" class="${R.filter === 'surfaced' ? 'on' : ''}">Surfaced in alternative (${b.surfaced})</button>
      <button type="button" data-f="all" class="${R.filter === 'all' ? 'on' : ''}">All (${rows.length})</button></div>
      <div class="rl-ev rl-hd"><span></span><span></span><span></span><span>ACTUAL</span><span>ALTERNATIVE</span></div>` +
    (list.map(({ d, alt }) => {
      const stored = M.index.get(`utt:${d.event_id}`) || M.index.get(`ev:${d.event_id}`) || M.index.get(`al:${d.event_id}`)
      const e = stored?.[0]?.m || d
      const shape = d.category === 'speech' ? 'speech' : d.category === 'unknown' ? 'unknown' : d.category === 'hazard' ? 'hazard' : d.category === 'alert' ? 'alert' : 'sound'
      const text = e.type === 'utterance' ? `${e.person_label ? `<b>${esc(e.person_label)}</b> ` : '<span class="dim">background</span> '}“${esc(e.text)}”`
        : esc(e.label === 'unknown' ? 'Unknown sound' : e.text || d.label)
      const open = R.open.has(d.event_id)
      return `<div class="rl-ev${d.surfaced !== alt.surfaced ? ' diff' : ''}" data-id="${esc(d.event_id)}">
        <span class="tm">${fmtClock(e.t ?? d.t)}</span><span class="shape ${shape}"></span>
        <span class="txt">${text} <span class="dir">${dirWord(e.bearing_deg)} ${fmtBearing(e.bearing_deg)}</span></span>
        <span>${dec(d)}</span><span>${dec(alt)}</span>
        ${open ? `<div class="reason">Actual: ${esc(d.reason)} (score ${d.model_score ?? '–'})<br>Alternative: ${esc(alt.reason)} (score ${alt.model_score ?? '–'})
          <br><a href="#" data-inspect="${esc(d.event_id)}">inspect event</a></div>` : ''}</div>`
    }).join('') || '<div class="empty">No differences: try another preset.</div>')
}

export async function renderReplay(segId) {
  await loadDay()
  const segs = M.day.segments || []
  if (segId) R.seg = segId
  if (!R.seg || !segs.find(s => s.id === R.seg)) R.seg = (segs.find(s => s.id === 'lunch' || s.environment === 'cafe') || segs[0])?.id
  $('rl-segments').innerHTML = segs.map(s => `<button type="button" data-seg="${esc(s.id)}" class="${s.id === R.seg ? 'on' : ''}">${esc(s.label)} <span>${fmtClock(s.start)}</span></button>`).join('')
  if (!$('rl-sliders').children.length) sliders()
  R.actual = null
  run()
}

/** For the judge tour: the segment shown and both results. */
export function replayState() {
  const seg = (M.day?.segments || []).find(s => s.id === R.seg)
  return { segment: seg?.label || R.seg, preset: R.preset, actual: R.actual, altRes: R.altRes }
}
export function setPreset(name) {
  if (!PRESETS[name]) return
  R.alt = { ...PRESETS[name] }; R.preset = name; R.altRes = null
  if ($('rl-sliders').children.length) syncSliders()
}

function syncSliders() {
  for (const [k] of SLIDERS) { $(`rl-${k}`).value = R.alt[k]; $(`rl-${k}-v`).textContent = R.alt[k].toFixed(2) }
  $('rl-focus').checked = R.alt.focus_mode
}

$('rl-segments').addEventListener('click', ev => { const b = ev.target.closest('[data-seg]'); if (b) renderReplay(b.dataset.seg) })
$('rl-sliders').addEventListener('input', ev => {
  const k = ev.target.dataset.k
  if (k) { R.alt[k] = +ev.target.value; $(`rl-${k}-v`).textContent = R.alt[k].toFixed(2) }
  if (ev.target.id === 'rl-focus') R.alt.focus_mode = ev.target.checked
  R.preset = 'custom'
  schedule()
})
$('rl-presets').addEventListener('click', ev => {
  const b = ev.target.closest('[data-p]'); if (!b) return
  R.alt = { ...PRESETS[b.dataset.p] }; R.preset = b.dataset.p; syncSliders(); schedule()
})
$('rl-events').addEventListener('click', ev => {
  const insp = ev.target.closest('[data-inspect]')
  if (insp) {
    ev.preventDefault()
    const id = insp.dataset.inspect
    inspectStored(M.index.has(`utt:${id}`) ? `utt:${id}` : M.index.has(`ev:${id}`) ? `ev:${id}` : `al:${id}`)
    return
  }
  const f = ev.target.closest('[data-f]'); if (f) { R.filter = f.dataset.f; render(); return }
  const row = ev.target.closest('.rl-ev'); if (!row) return
  const id = row.dataset.id
  R.open.has(id) ? R.open.delete(id) : R.open.add(id)
  render()
})
