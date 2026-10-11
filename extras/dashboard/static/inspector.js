// Event inspector: everything HERE knows about one event, and which provider produced each field.
import {
  S, esc, fmtBearing, dirWord, fmtTime, pct, directionProvider, engineProvider, modelProvider, transportProvider,
  storageProvider, rulesAttention, insightProvider, off, geminiSeen, phoneSeen, isRetraction, steadyBearing, bearingReports,
} from './core.js'

const panel = document.getElementById('inspector')
const body = document.getElementById('insp-body')
let current = null          // {key, messages?, origin}

export function inspectorKey() { return current && !current.messages ? current.key : null }

let opener = null           // where keyboard focus returns when the inspector closes
export function openInspector(key, opts = {}) {
  if (panel.hidden) opener = document.activeElement
  current = { key, ...opts }
  panel.hidden = false
  render()
  document.getElementById('insp-close').focus({ preventScroll: true })
}
export function closeInspector() {
  const wasOpen = !panel.hidden
  current = null; panel.hidden = true
  if (wasOpen && opener && document.contains(opener)) opener.focus({ preventScroll: true })
  opener = null
}
document.getElementById('insp-close').addEventListener('click', closeInspector)

export function refreshInspector() {
  if (!current || current.messages || panel.hidden) return
  if ((S.byKey.get(current.key) || []).length !== current.n) render()
}

function tag(p) {
  if (!p) return '<span class="dim">—</span>'
  return `<span class="ptag ${esc(p.tag)}"><b>${esc(p.name)}</b>${p.detail ? `<span>${esc(p.detail)}</span>` : ''}</span>`
}
function row(field, value, provider, why = '') {
  return `<tr><td>${esc(field)}</td><td><div class="val">${value}</div>${why ? `<div class="why">${why}</div>` : ''}</td><td>${tag(provider)}</td></tr>`
}
function bar(p, color) { return `<div class="bar"><i style="width:${Math.round((p ?? 0) * 100)}%;background:${color}"></i></div>` }

function summary(m) {
  switch (m.type) {
    case 'speech_start': return `onset at ${fmtBearing(m.bearing_deg)}, confidence ${pct(m.confidence)}`
    case 'utterance': return `${m.final ? 'final' : 'partial'}: “${m.text}${m.pending ? ' ' + m.pending : ''}”`
    case 'sound_event': return `${m.label} ${pct(m.confidence)} at ${fmtBearing(m.bearing_deg)}${m.count > 1 ? ` ×${m.count}` : ''}`
    case 'alert': return `${m.kind} ${m.active ? 'on' : 'off'}, priority ${m.priority}`
    case 'perception_enrichment': return `${m.attention} ${pct(m.attention_confidence)} (${m.attention_model})`
    case 'attribution': return `${m.person_label ?? 'no face'} by ${m.method}, score ${pct(m.score)}`
    default: return m.type
  }
}

function render() {
  const { key } = current
  const msgs = current.messages || S.byKey.get(key) || []
  current.n = msgs.length
  if (!msgs.length) { body.innerHTML = `<div class="empty">No messages for ${esc(key)} (it may have aged out of history).</div>`; return }
  const last = type => { for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i].m.type === type) return msgs[i].m; return null }
  const u = last('utterance'), ss = last('speech_start'), se = last('sound_event'), al = last('alert')
  const enr = last('perception_enrichment'), att = last('attribution')
  const main = u || ss || se || al
  const seeded = !!main?.seeded
  const firstRx = msgs[0].rx ?? msgs[0].m.t

  let title, shape
  if (u || ss) { shape = 'speech'; title = att?.person_label || main.person_label || (S.voiceOf.has(main.utt_id) ? `Voice ${S.voiceOf.get(main.utt_id)}` : 'Unnamed speaker') }
  else if (se) { shape = se.label === 'unknown' ? 'unknown' : 'sound'; title = `${se.emoji} ${se.label === 'unknown' ? 'Unknown sound' : se.text}` }
  else { shape = 'alert'; title = `${al.emoji} ${al.label}` }

  let h = `<div class="insp-title"><span class="shape ${shape}"></span>${esc(title)}</div>
    <div class="insp-sub">${fmtTime(main.t)} · ${dirWord(main.bearing_deg)} ${fmtBearing(main.bearing_deg)}
    ${seeded ? ' · <span class="badge seed">seeded day</span>' : ''}</div>`
  if (u && isRetraction(u)) h += `<div class="answer dim">Retracted: the laptop withdrew this caption (it turned out to be noise or the wearer’s own voice), so no caption card was shown.</div>`
  else if (u) h += `<div class="answer">“${esc(u.text)}${u.pending ? ` <span class="pend dim">${esc(u.pending)}</span>` : ''}”</div>`

  h += '<h2 class="insp-h">Who produced what</h2><table class="prov">'
  // One row per stage, always: a named provider, a fallback (amber, says what is missing) or "not connected".
  if (main.bearing_deg !== null && main.bearing_deg !== undefined) {
    const spread = u ? ` ±${Math.round(u.bearing_spread_deg)}°` : ''
    const bkey = se ? `ev:${se.event_id}` : al?.ref?.event_id ? `ev:${al.ref.event_id}` : al ? `al:${al.alert_id}` : null
    const reports = bkey ? bearingReports(bkey) : []
    const shown = bkey ? steadyBearing(bkey, main.bearing_deg) : main.bearing_deg
    const steadied = reports.length > 1 ? `<div class="why">shown ${fmtBearing(shown)}: steadied over ${reports.length} reports (${reports.map(fmtBearing).join(', ')})</div>` : ''
    h += row('Direction', `${fmtBearing(main.bearing_deg)}${spread} <span class="dim">${dirWord(main.bearing_deg)}</span>${steadied}`,
      seeded ? { name: 'ReSpeaker', detail: 'stored bearing (seeded day)', tag: 'respeaker' } : directionProvider(), `world frame, track ${main.source_id ?? se?.source_id ?? '—'}${ss ? `, onset confidence ${pct(ss.confidence)}` : ''}`)
  } else {
    h += row('Direction', '<span class="dim">none reported</span>', off('No direction', 'sent without a bearing'))
  }
  if (u || ss) {
    if (u) {
      h += row('Caption', u.final ? 'final' : 'streaming', engineProvider(u.engine) || off('Speech engine', 'engine not reported'),
        `${u.overlap ? 'overlapping speech · ' : ''}loudness ${pct(u.loudness)}`)
      h += row('Tone', u.tone ? `${esc(u.tone)} <span class="dim">${pct(u.tone_confidence)}</span>` : '<span class="dim">not set</span>',
        { name: 'HERE laptop', detail: 'text + loudness rules', tag: 'local' })
    } else h += row('Caption', '<span class="dim">waiting for words</span>', off('Speech engine', 'no words yet'))
  }
  if (se) {
    h += row('Sound class', `${esc(se.label)} <span class="dim">${pct(se.confidence)}</span>${bar(se.confidence, se.label === 'unknown' ? 'var(--violet)' : 'var(--amber)')}`,
      modelProvider(se.model) || off('Classifier', 'model not reported'),
      se.label === 'unknown' ? 'heard, not classifiable: kept with its direction' : `category ${esc(se.category)}${se.count > 1 ? `, merged ×${se.count}` : ''}`)
  }
  if (al) h += row('Alert', `${esc(al.emoji)} ${esc(al.label)} · priority ${al.priority}`, { name: 'HERE laptop', detail: 'alert rules', tag: 'local' },
    al.active ? 'active' : 'ended')
  const rules = rulesAttention(se || u || ss, al)
  if (enr) {
    h += row('Attention', `<span class="chip att-${enr.attention}">${esc(enr.attention)}</span> ${pct(enr.attention_confidence)}${bar(enr.attention_confidence, 'var(--text)')}`,
      modelProvider(enr.attention_model),
      enr.attention_model === 'rules' ? 'decided by the deterministic fallback' : `rules would have said <b>${rules}</b>${rules === enr.attention ? ' (agrees)' : ''}`)
    if (enr.directed_at_user !== undefined) h += row('Addressed to you', `${pct(enr.directed_at_user)}${bar(enr.directed_at_user, 'var(--blue)')}`, modelProvider(enr.attention_model))
  } else {
    h += row('Attention', `<span class="chip att-${rules}">${rules}</span> <span class="dim">by rules</span>`,
      { name: 'Deterministic rules', detail: 'no enrichment for this event · Hugging Face model not heard from', tag: 'fallback' })
  }
  h += row('Context', enr?.context ? esc(enr.context) : '<span class="dim">no note</span>',
    enr?.context ? modelProvider(enr.context_model || 'gemini')
      : geminiSeen() ? off('Gemini', 'no note for this event') : off('Gemini', 'not connected'))
  if (u || ss) {
    h += row('Who', esc(att?.person_label ?? main.person_label ?? (att ? 'off camera' : title)),
      att ? { name: 'iPhone', detail: `Vision faces · ${att.method} · ${pct(att.score)}`, tag: 'phone' }
        : main.person_label ? { name: 'iPhone', detail: 'stored attribution', tag: 'phone' }
          : phoneSeen() ? off('iPhone', 'no face matched this voice') : off('iPhone', 'not connected · voice told apart by direction'))
  }
  h += row('Transport', `src <span class="mono">${esc(main.src)}</span>${main.seq !== undefined ? ` · seq <span class="mono">${main.seq}</span>` : ''}`,
    seeded ? { name: 'Seeded day', detail: 'not live', tag: 'seed' } : transportProvider())
  const st = storageProvider()
  h += row('Memory', seeded ? 'stored event' : st && S.memoryHealth?._source !== 'seed' ? 'mirrored by role memory'
    : st ? '<span class="dim">live events not stored; the seeded day stands in</span>' : '<span class="dim">not stored</span>', st || off('Memory', 'not connected'))
  h += row('Trends', 'routine insights over stored days', insightProvider(S.insightSource))
  h += '</table>'

  // timings
  const timing = []
  if (u?.stage_ms) for (const [k, v] of Object.entries(u.stage_ms)) timing.push([k, v])
  if (!seeded) for (const x of msgs.slice(0, 1).concat(msgs.slice(-1))) {
    if (typeof x.m.t === 'number' && x.rx && !x.local) timing.push([`${x.m.type} send → here`, (x.rx - x.m.t) * 1000])
  }
  if (ss && u && !seeded) {
    const fu = msgs.find(x => x.m.type === 'utterance')
    if (fu) timing.push(['onset → first words', (fu.rx - msgs.find(x => x.m.type === 'speech_start').rx) * 1000])
  }
  if (timing.length) {
    h += '<h2 class="insp-h">Timings (ms)</h2><table class="prov">' +
      timing.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="mono">${Math.round(v)}</td><td>${bar(Math.min(1, v / 1000), 'var(--blue)')}</td></tr>`).join('') + '</table>'
  }

  // linked ids
  const ids = []
  if (main.utt_id) ids.push(['utt_id', main.utt_id])
  if (se?.event_id) ids.push(['event_id', se.event_id])
  if (al) ids.push(['alert_id', al.alert_id])
  if ((u || ss || se)?.source_id !== undefined) ids.push(['source_id', (u || ss || se).source_id])
  h += `<h2 class="insp-h">Linked ids</h2><div class="chips">${ids.map(([k, v]) => `<span class="chip"><span class="dim">${k}</span>&nbsp;<span class="mono">${esc(v)}</span></span>`).join('')}</div>`

  h += `<h2 class="insp-h">Messages (${msgs.length})</h2><div class="tl">` + msgs.map((x, i) => {
    const off = ((x.rx ?? x.m.t) - firstRx) * 1000
    return `<div class="row" data-i="${i}"><span class="off">+${Math.round(off)}ms</span><span class="ty">${esc(x.m.type)}</span><span>${esc(summary(x.m))}</span></div>`
  }).join('') + '</div>'
  body.innerHTML = h
  body.querySelectorAll('.tl .row').forEach(el => el.addEventListener('click', () => {
    const next = el.nextElementSibling
    if (next && next.tagName === 'PRE') { next.remove(); return }
    const pre = document.createElement('pre')
    pre.textContent = JSON.stringify(msgs[+el.dataset.i].m, null, 1)
    el.after(pre)
  }))
}
