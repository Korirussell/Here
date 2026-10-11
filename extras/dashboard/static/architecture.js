// "How it works": the one-page architecture. A pure function (no DOM), so the page draws it live and
// make_architecture.mjs writes the same picture to architecture.svg for the submission.
//
// archSvg(states) -> SVG string. states[id] = {state: 'live' | 'fallback' | 'off', provider, note}; omit `states`
// for the static version (every node shows its sponsor path and its fallback, no status).

// The print palette (Mono light): the submission SVG and the download. The page passes its theme's tokens instead
// (how.js `palette()`), so the live view follows the colour theme.
export const PRINT = { ink: '#171717', ink2: '#4d4d4d', ink3: '#6f6f6f', line: '#d9d9d9', bg: '#ffffff', panel: '#fafafa',
  live: '#0a7f3f', liveBg: '#effaf3', fb: '#a35c00', fbBg: '#fff8eb', off: '#8f8f8f', offBg: '#ffffff', blue: '#0060df',
  dim: '#f2f2f2', onFill: '#ffffff', edgeOff: '#c4c4c4' }
let C = PRINT
const FONT = "Geist, Inter, 'Segoe UI', Helvetica, Arial, sans-serif"   // single quotes inside: valid as an XML attribute
const MONO = "'Geist Mono', 'SFMono-Regular', Consolas, monospace"

// x, y, w, h in a 1320 x 744 canvas. `sponsor` is the intended path; `fallback` what HERE uses without it.
export const NODES = [
  { id: 'respeaker', x: 24, y: 284, w: 210, h: 112, role: 'Hear', title: 'ReSpeaker XVF3800', sub: '4-mic array on the wearer', sponsor: 'direction of arrival', fallback: 'scripted scenes' },
  { id: 'direction', x: 282, y: 92, w: 344, h: 88, role: 'Where', title: 'Direction', sponsor: 'XVF3800 built-in DoA / ODAS', fallback: 'scripted bearings' },
  { id: 'speech', x: 282, y: 190, w: 344, h: 88, role: 'Words', title: 'Speech to text', sponsor: 'ElevenLabs Scribe Realtime', fallback: 'whisper on the laptop GPU' },
  { id: 'sounds', x: 282, y: 288, w: 344, h: 88, role: 'What', title: 'Sound classes', sponsor: 'Hugging Face audio model', fallback: 'PANNs CNN14 · enrolled sounds' },
  { id: 'attention', x: 282, y: 386, w: 344, h: 88, role: 'Priority', title: 'Attention', sponsor: 'Hugging Face (JEV-9B / DeBERTa)', fallback: 'deterministic rules' },
  { id: 'gemini', x: 282, y: 484, w: 344, h: 88, role: 'Context', title: 'One-line context', sponsor: 'Gemini', fallback: 'no note (events unchanged)' },
  { id: 'relay', x: 740, y: 284, w: 220, h: 112, role: 'Transport', title: 'Vultr relay', sub: 'wss · routes text, never audio', sponsor: 'Vultr cloud', fallback: 'relay on the laptop' },
  { id: 'phone', x: 1010, y: 92, w: 286, h: 112, role: 'See', title: 'iPhone (the glasses)', sub: 'faces · heading · AR captions', sponsor: 'Vision + ARKit', fallback: 'not connected' },
  { id: 'belt', x: 1010, y: 220, w: 286, h: 112, role: 'Feel', title: 'Haptic belt', sub: 'ESP32 · buzzes toward the sound', sponsor: 'driven by the iPhone', fallback: 'not reported here' },
  { id: 'dashboard', x: 1010, y: 348, w: 286, h: 112, role: 'Show', title: 'HERE World', sub: 'this screen', sponsor: 'every message, live', fallback: 'local replay of scenes' },
  { id: 'neon', x: 715, y: 616, w: 270, h: 112, role: 'Remember', title: 'Memory', sub: 'events + transcripts, Ask HERE', sponsor: 'Neon Postgres', fallback: 'SQLite · seeded day' },
  { id: 'databricks', x: 1040, y: 616, w: 256, h: 112, role: 'Patterns', title: 'Routine insights', sub: 'trends over stored days', sponsor: 'Databricks', fallback: 'precomputed' },
]
const by = Object.fromEntries(NODES.map(n => [n.id, n]))
const mid = n => ({ l: [n.x, n.y + n.h / 2], r: [n.x + n.w, n.y + n.h / 2], t: [n.x + n.w / 2, n.y], b: [n.x + n.w / 2, n.y + n.h] })

// [from, side, to, side, label?]
const EDGES = [
  ['respeaker', 'r', 'direction', 'l'], ['respeaker', 'r', 'speech', 'l'], ['respeaker', 'r', 'sounds', 'l'],
  ['relay', 'r', 'phone', 'l'], ['relay', 'r', 'dashboard', 'l'], ['phone', 'b', 'belt', 't'],
  ['relay', 'b', 'neon', 't', 'role memory'], ['neon', 'r', 'databricks', 'l'],
]

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

function path([x1, y1], [x2, y2]) {
  if (Math.abs(y1 - y2) < 1 || Math.abs(x1 - x2) < 1) return `M${x1} ${y1} L${x2} ${y2}`
  const vertical = Math.abs(x1 - x2) < Math.abs(y1 - y2) && x1 === x2
  if (vertical) return `M${x1} ${y1} L${x2} ${y2}`
  const mx = (x1 + x2) / 2
  return Math.abs(x1 - x2) > 40 ? `M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}` : `M${x1} ${y1} C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2} ${x2} ${y2}`
}

function node(n, st) {
  const live = st ? st.state : null
  const dim = live === 'off' || live === 'idle'
  const stroke = live === 'live' ? C.live : live === 'fallback' ? C.fb : dim ? C.off : C.ink
  const fill = live === 'live' ? C.liveBg : live === 'fallback' ? C.fbBg : C.bg
  const dash = dim ? ' stroke-dasharray="5 4"' : ''
  let y = n.y + 22
  let s = `<g class="arch-node" data-node="${n.id}"><rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="12" fill="${fill}" stroke="${stroke}" stroke-width="${live ? 1.8 : 1.2}"${dash}/>`
  s += `<text x="${n.x + 14}" y="${y}" font-family="${MONO}" font-size="11" fill="${C.ink3}" letter-spacing=".06em">${esc(n.role.toUpperCase())}</text>`
  if (live) {                       // status pill, top right
    const label = live === 'live' ? 'LIVE' : live === 'fallback' ? 'FALLBACK' : live === 'idle' ? 'IDLE' : 'NOT CONNECTED'
    const w = label.length * 7 + 18
    s += `<rect x="${n.x + n.w - w - 10}" y="${n.y + 9}" width="${w}" height="18" rx="9" fill="${dim ? C.dim : stroke}"/>`
    s += `<text x="${n.x + n.w - w / 2 - 10}" y="${n.y + 22}" text-anchor="middle" font-family="${MONO}" font-size="10.5" font-weight="600" fill="${dim ? C.ink2 : C.onFill}">${label}</text>`
  }
  y += 20
  s += `<text x="${n.x + 14}" y="${y}" font-family="${FONT}" font-size="16" font-weight="600" fill="${C.ink}">${esc(n.title)}</text>`
  if (n.sub) { y += 18; s += `<text x="${n.x + 14}" y="${y}" font-family="${FONT}" font-size="12.5" fill="${C.ink2}">${esc(n.sub)}</text>` }
  const fit = (t, size) => { const max = Math.floor((n.w - 28) / (size * 0.53)); return t.length > max ? t.slice(0, max - 1) + '…' : t }
  const line1 = fit(st ? (st.provider || (live === 'off' ? 'not connected' : n.sponsor)) : n.sponsor, 13.5)
  const line2 = fit(st ? (st.note || '') : `fallback: ${n.fallback}`, 12)
  y += 20
  s += `<text x="${n.x + 14}" y="${y}" font-family="${FONT}" font-size="13.5" font-weight="600" fill="${st ? stroke : C.blue}">${esc(line1)}</text>`
  if (line2) { y += 17; s += `<text x="${n.x + 14}" y="${y}" font-family="${FONT}" font-size="12" fill="${C.ink3}">${esc(line2)}</text>` }
  return s + '</g>'
}

/** The diagram. `states` omitted = the static version for the submission. */
export function archSvg(states = null, { title = !states, palette = PRINT } = {}) {
  C = palette
  const W = 1320, H = title ? 876 : 744, dy = title ? 84 : 0
  let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="How HERE works: ReSpeaker to the laptop's speech, sound, attention and context models, through the Vultr relay to the iPhone, the belt and this dashboard, and into Neon memory and Databricks insights">`
  s += `<rect width="${W}" height="${H}" fill="${C.bg}"/>`
  if (title) {
    s += `<text x="24" y="40" font-family="${FONT}" font-size="26" font-weight="700" fill="${C.ink}">How HERE works</text>`
    s += `<text x="24" y="66" font-family="${FONT}" font-size="15" fill="${C.ink2}">Sound becomes direction, words and priority on the laptop; only text and bearings travel; the wearer sees and feels it.</text>`
  }
  s += `<g transform="translate(0 ${dy})">`
  // the laptop group
  s += `<rect x="262" y="44" width="384" height="544" rx="16" fill="${C.panel}" stroke="${C.line}"/>`
  s += `<text x="282" y="72" font-family="${MONO}" font-size="12" fill="${C.ink2}" letter-spacing=".05em">DEMO LAPTOP · RTX 5000 · understands the sound</text>`
  // laptop -> relay, the privacy rule on the wire
  s += `<path d="M646 340 L740 340" stroke="${C.ink}" stroke-width="2" fill="none" marker-end="url(#arr)"/>`
  s += `<text x="693" y="294" text-anchor="middle" font-family="${MONO}" font-size="11" fill="${C.ink2}"><tspan x="693">text +</tspan><tspan x="693" dy="14">bearings</tspan><tspan x="693" dy="14" font-weight="700" fill="${C.ink}">no audio</tspan></text>`
  s += `<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="${C.ink}"/></marker></defs>`
  for (const [a, as, b, bs, label] of EDGES) {
    const p1 = mid(by[a])[as], p2 = mid(by[b])[bs]
    const off = states && (states[a]?.state === 'off' || states[b]?.state === 'off')
    s += `<path d="${path(p1, p2)}" stroke="${off ? C.edgeOff : C.ink}" stroke-width="1.5" fill="none"${off ? ' stroke-dasharray="5 4"' : ''} marker-end="url(#arr)"/>`
    if (label) s += `<text x="${p1[0] + 8}" y="${(p1[1] + p2[1]) / 2 + 4}" font-family="${MONO}" font-size="11" fill="${C.ink2}">${esc(label)}</text>`
  }
  for (const n of NODES) s += node(n, states ? states[n.id] || { state: 'off' } : null)
  if (!states) {
    s += `<text x="24" y="${H - dy - 32}" font-family="${FONT}" font-size="12.5" fill="${C.ink3}">Every stage has a fallback, so the demo never depends on one service: blue = the sponsor path, grey = what HERE uses without it.</text>`
    s += `<text x="24" y="${H - dy - 14}" font-family="${FONT}" font-size="12.5" fill="${C.ink3}">HERE World's "How it works" view lights each node from the running system: live, fallback or not connected.</text>`
  }
  return s + '</g></svg>'
}
