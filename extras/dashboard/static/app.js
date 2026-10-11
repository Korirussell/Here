// HERE World entry point: tabs, the demo menu and keys, the render loop.
import { S, cfg, connect, loadConfig, refreshMemoryHealth, resetLive, getJSON, esc } from './core.js'
import { startLive, renderLive, renderHealth, resetCamera, momentOn, setMoment, clearMoment } from './live.js'
import { renderMemory, ask, SUGGESTIONS } from './memory.js'
import { renderReplay } from './replay.js'
import { refreshInspector, closeInspector } from './inspector.js'
import { startTour, touring } from './tour.js'
import { renderHow, downloadSvg } from './how.js'
import { initThemeUI, cycleMode, openTheme, themeOpen } from './theme.js'

const $ = id => document.getElementById(id)
let tab = 'live'

function show(name, arg) {
  tab = name
  document.querySelectorAll('#tabs button').forEach(b => { b.classList.toggle('on', b.dataset.tab === name); b.setAttribute('aria-selected', String(b.dataset.tab === name)) })
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('on', v.id === name))
  closeInspector()
  window.scrollTo(0, 0)
  if (name === 'memory') renderMemory()
  if (name === 'replay') renderReplay(arg)
  if (name === 'how') renderHow()
}
$('how-dl').addEventListener('click', downloadSvg)
initThemeUI()
// colours changed: redraw what bakes colours in (the timeline and the architecture are SVG drawn from tokens)
document.addEventListener('here:theme', () => { if (tab === 'memory') renderMemory(); if (tab === 'how') renderHow() })
$('tabs').addEventListener('click', ev => { const b = ev.target.closest('[data-tab]'); if (b) show(b.dataset.tab) })
document.addEventListener('here:replay-segment', ev => show('replay', ev.detail))

// ---- demo ------------------------------------------------------------------------------------------
async function demo(params) {
  closeMenu()
  try {
    const r = await getJSON('/dash/demo?' + new URLSearchParams(params))
    if (!r.ok) flashBanner(r.error || 'demo failed')
  } catch { flashBanner('dashboard server unreachable') }
}
function flashBanner(text) {
  const b = $('banner'); b.textContent = text; b.hidden = false
  setTimeout(renderHealth, 4000)
}
function reset() { resetLive(); clearMoment(); closeInspector(); resetCamera() }
// Panic: the screen is clean at once; stopping the scripted process (up to ~2 s on the server) happens after.
async function panic() { reset(); show('live'); await demo({ action: 'reset' }); reset() }

const MOMENTS = [
  ['all', 'All three core moments', 'captions → turn-around → doorbell + name', 'S'],
  ['1', 'Captions over the right person', 'two people talk, front-left and front-right', '1'],
  ['2', 'The turn-around', 'someone speaks behind the wearer', '2'],
  ['3', 'Doorbell + name called', 'from behind', '3'],
]
function menu() {
  const running = S.status.demo?.running
  $('demo-menu').innerHTML = MOMENTS.map(([m, t, d, k]) => `<button data-moment="${m}"><span>${esc(t)}<span class="d">${esc(d)}</span></span><kbd>${k}</kbd></button>`).join('') +
    (cfg.scenes || []).map(s => `<button data-scene="${esc(s)}"><span>${esc(s.replace(/_/g, ' '))}<span class="d">fixtures/events/${esc(s)}.jsonl</span></span></button>`).join('') +
    `<hr><button data-tour="1"><span>Judge tour<span class="d">~80 s guided: live → memory → replay lab</span></span><kbd>T</kbd></button>
     <button data-loop="1"><span>Loop the moments</span><kbd>L</kbd></button>
     <button data-local="1"><span>Play the scenes locally<span class="d">no relay needed · labelled as a replay</span></span></button>
     ${running ? `<button data-act="stop"><span>Stop “${esc(S.status.demo.label)}”</span><kbd>X</kbd></button>` : ''}
     <button data-act="reset"><span>Reset the view</span><kbd>R</kbd></button>
     <button data-act="keys"><span>Keyboard shortcuts</span><kbd>?</kbd></button>`
  $('demo-btn').innerHTML = running ? `<span class="sdot ok"></span>Playing · ${esc(S.status.demo.label)}` : 'Run demo <kbd>S</kbd>'
}
const demoBox = document.querySelector('.demo')
function closeMenu() { demoBox.classList.remove('open') }
$('demo-btn').addEventListener('click', ev => { ev.stopPropagation(); demoBox.classList.toggle('open') })
document.addEventListener('click', ev => { if (!ev.target.closest('.demo')) closeMenu() })
$('demo-menu').addEventListener('click', ev => {
  const b = ev.target.closest('button'); if (!b) return
  if (b.dataset.moment) demo(b.dataset.moment === 'all' ? {} : { moment: b.dataset.moment })
  else if (b.dataset.scene) demo({ scene: b.dataset.scene })
  else if (b.dataset.loop) demo({ loop: '1' })
  else if (b.dataset.local) demo({ local: '1', loop: '1' })
  else if (b.dataset.tour) { closeMenu(); setTimeout(() => startTour(tourApi()), 0) }
  else if (b.dataset.act === 'stop') demo({ action: 'stop' })
  else if (b.dataset.act === 'reset') { closeMenu(); reset() }
  else if (b.dataset.act === 'keys') { closeMenu(); $('keys').hidden = !$('keys').hidden }
})

// ---- Moment view (F): the latest important moment, taken apart step by step (live.js renders it) ----------
$('moment-btn').setAttribute('aria-pressed', String(momentOn()))
$('moment-btn').addEventListener('click', () => setMoment(!momentOn()))

// ---- Captions sidebar (B): collapsible; the orb takes the room (its ResizeObserver). While hidden, a dot on the
// toggle says something new arrived: red for an alert, else blue for a caption (re-renders of known rows don't count).
const railKeys = id => new Set([...$(id).querySelectorAll('[data-key]')].map(e => e.dataset.key))
let railSeen = null
const railHistoryUntil = performance.now() + 2000        // a page opened collapsed: the server's history isn't "new"
function railUnseen() {
  if (!railSeen) return
  if (performance.now() < railHistoryUntil) { railSeen = { captions: railKeys('captions'), alerts: railKeys('alerts') }; return }
  const fresh = id => [...railKeys(id)].some(k => !railSeen[id].has(k))
  const btn = $('rail-btn')
  if (fresh('alerts')) btn.dataset.unseen = 'alert'
  else if (fresh('captions') && btn.dataset.unseen !== 'alert') btn.dataset.unseen = 'caption'
}
const railWatch = new MutationObserver(railUnseen)
function setRail(open) {
  $('live').classList.toggle('rail-off', !open)
  const btn = $('rail-btn')
  btn.setAttribute('aria-expanded', String(open))
  btn.title = `${open ? 'Hide' : 'Show'} the captions sidebar (B)`
  delete btn.dataset.unseen
  railWatch.disconnect()
  railSeen = open ? null : { captions: railKeys('captions'), alerts: railKeys('alerts') }
  if (!open) for (const id of ['captions', 'alerts']) railWatch.observe($(id), { childList: true })
  try { localStorage.setItem('here.rail', open ? '1' : '0') } catch { /* private window: per session only */ }
}
const railOpen = () => !$('live').classList.contains('rail-off')
$('rail-btn').addEventListener('click', () => setRail(!railOpen()))
setRail((() => { try { return localStorage.getItem('here.rail') !== '0' } catch { return true } })())

const tourApi = () => ({ show, demo, ask, suggestions: SUGGESTIONS })
function onKey(ev) {
  if (touring()) return                                   // the tour's own listener takes this key over
  if (ev.key === 'Escape' && ev.shiftKey) { ev.preventDefault(); panic(); return }
  if (ev.target.closest('input, textarea')) return
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return
  const k = ev.key.toLowerCase()
  if (k === 'escape') { closeInspector(); closeMenu(); openTheme(false); $('keys').hidden = true }
  else if (k === '1' || k === '2' || k === '3') { show('live'); demo({ moment: k }) }
  else if (k === 's') { show('live'); demo({}) }
  else if (k === 't') startTour(tourApi())
  else if (k === 'l') { show('live'); demo({ loop: '1' }) }
  else if (k === 'x') demo({ action: 'stop' })
  else if (k === 'm') show('memory')
  else if (k === 'a') { show('memory'); setTimeout(() => ask(SUGGESTIONS[0]), 50) }
  else if (k === 'p') show('replay')
  else if (k === 'h') show('how')
  else if (k === 'f') { show('live'); setMoment(!momentOn()) }
  else if (k === 'b') { show('live'); setRail(!railOpen()) }
  else if (k === 'c') openTheme(!themeOpen())
  else if (k === 'd') cycleMode()
  else if (k === 'r') reset()
  else if (k === '?') $('keys').hidden = !$('keys').hidden
  else return
  ev.preventDefault()
}
document.addEventListener('keydown', onKey)

/** Interactive: every handler is attached. Replay what was pressed while the modules loaded (index.html). */
function ready() {
  window.__hereReady = true
  const q = window.__hereQ || []
  window.__hereQ = []
  for (const x of q) {
    if (x.kind === 'key') onKey({ ...x, target: document.body, preventDefault() {} })
    else document.querySelector(x.sel)?.click()
  }
  delete document.documentElement.dataset.loading
  window.__hereInteractive = Math.round(performance.now())
  console.info(`[here] interactive ${window.__hereInteractive} ms after navigation${q.length ? `, replayed ${q.length} early input(s)` : ''}`)
}

// ---- loop ------------------------------------------------------------------------------------------
let lastMenu = null
// ---- keyboard: every clickable row is focusable and opens with Enter / Space ---------------------------
const CLICKABLE = '[data-key]:not(button):not(a), [data-seg]:not(button), .rl-ev'
function focusables() {
  for (const el of document.querySelectorAll(CLICKABLE)) {
    if (el.hasAttribute('tabindex')) continue
    el.setAttribute('tabindex', '0'); el.setAttribute('role', 'button')
  }
}
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Enter' && ev.key !== ' ') return
  const el = ev.target.closest?.(CLICKABLE)
  if (!el || ev.target.closest('input, textarea, button, a')) return
  ev.preventDefault()
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }))       // SVG elements have no .click()
})
// Live panels re-render their rows; put keyboard focus back on the same row (same data-key) afterwards.
function keepFocus(render) {
  const a = document.activeElement, key = a?.dataset?.key, box = a?.closest?.('#captions, #alerts, #stream')
  render()
  focusables()
  if (key && box && !document.contains(a)) box.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus({ preventScroll: true })
}

function tick() {
  if (tab === 'live') keepFocus(renderLive)
  else if (tab === 'how') { renderLive(); renderHow() }      // keep lastOf() fresh while this view is up
  else focusables()
  refreshInspector()
  const d = JSON.stringify(S.status.demo || {}) + (cfg.scenes || []).length
  if (d !== lastMenu) { lastMenu = d; menu() }
  setTimeout(() => requestAnimationFrame(tick), 100)       // panels at <= 10 Hz; the orb runs at frame rate
}

async function main() {
  await loadConfig()               // local, a few ms
  connect()
  startLive()
  menu()
  ready()                          // handlers attached and the orb exists: queued input can run now
  refreshMemoryHealth()
  setInterval(refreshMemoryHealth, 15000)
  tick()
}
main()
