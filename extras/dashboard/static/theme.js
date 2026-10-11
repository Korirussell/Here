// Colour themes + light / dark / system. The tokens live in themes.css; index.html applies the saved choice before
// first paint. This module owns the header switcher (C opens it, D cycles the mode), follows the OS when the mode
// is System, and tells the scripts that draw with colour (orb, timeline, architecture) through a 'here:theme' event.
// Scripts never name a colour: they read tokens with tok() / tokRGB().

export const THEMES = [
  { id: 'mono', label: 'Mono' },
  { id: 'ocean', label: 'Ocean' },
  { id: 'forest', label: 'Forest' },
  { id: 'sunset', label: 'Sunset' },
  { id: 'contrast', label: 'High contrast' },
]
export const MODES = ['light', 'dark', 'system']
const html = document.documentElement
const darkMq = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null

const store = {
  get: k => { try { return localStorage.getItem(k) } catch { return null } },
  set: (k, v) => { try { localStorage.setItem(k, v) } catch { /* private window: this page only */ } },
}

export function current() {
  return { theme: html.dataset.theme || 'mono', mode: html.dataset.mode || 'light', pref: html.dataset.modePref || 'system' }
}

// ---- tokens for scripts ----------------------------------------------------------------------------
let probe = null
const cache = new Map()
/** [r, g, b] (0-255) of a colour token, resolved by the browser (so color-mix() tokens work too). */
export function tokRGB(name) {
  const key = `${current().theme}/${current().mode}/${name}`
  if (cache.has(key)) return cache.get(key)
  if (!probe) { probe = document.createElement('span'); probe.style.display = 'none'; document.body.appendChild(probe) }
  probe.style.color = `var(${name})`
  const c = getComputedStyle(probe).color
  let rgb = [0, 0, 0]
  const m = c.match(/rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)/)
  const s = c.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/)            // how Chrome serializes color-mix()
  if (m) rgb = [+m[1], +m[2], +m[3]]
  else if (s) rgb = [+s[1] * 255, +s[2] * 255, +s[3] * 255]
  rgb = rgb.map(v => Math.round(v))
  cache.set(key, rgb)
  return rgb
}
/** A colour token as 'rgb(r, g, b)', or with alpha as 'rgba(r, g, b, a)': for canvas, SVG attributes, three.js. */
export function tok(name, alpha) {
  const [r, g, b] = tokRGB(name)
  return alpha === undefined ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// ---- applying ---------------------------------------------------------------------------------------
function apply(theme, pref, animate) {
  const dark = pref === 'dark' || (pref === 'system' && !!darkMq?.matches)
  const before = current()
  if (animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    html.classList.add('theme-anim')
    clearTimeout(apply.t); apply.t = setTimeout(() => html.classList.remove('theme-anim'), 320)
  }
  html.dataset.theme = theme; html.dataset.mode = dark ? 'dark' : 'light'; html.dataset.modePref = pref
  syncUI()
  const now = current()
  if (now.theme !== before.theme || now.mode !== before.mode) document.dispatchEvent(new CustomEvent('here:theme', { detail: now }))
}
export function setTheme(id) {
  if (!THEMES.some(t => t.id === id)) return
  store.set('here.theme', id); apply(id, current().pref, true)
}
export function setMode(pref) {
  if (!MODES.includes(pref)) return
  store.set('here.mode', pref); apply(current().theme, pref, true)
}
/** D: light -> dark -> system -> light. */
export function cycleMode() { const p = current().pref; setMode(MODES[(MODES.indexOf(p) + 1) % MODES.length]) }
darkMq?.addEventListener?.('change', () => { if (current().pref === 'system') apply(current().theme, 'system', true) })

// ---- the switcher -----------------------------------------------------------------------------------
const $ = id => document.getElementById(id)
function syncUI() {
  const { theme, pref, mode } = current()
  document.querySelectorAll('#theme-pop .mode-seg button').forEach(b => {
    const on = b.dataset.pickMode === pref
    b.setAttribute('aria-checked', String(on)); b.classList.toggle('on', on); b.tabIndex = on ? 0 : -1
  })
  document.querySelectorAll('#theme-swatches button').forEach(b => {
    const on = b.dataset.pickTheme === theme
    b.setAttribute('aria-checked', String(on)); b.classList.toggle('on', on); b.tabIndex = on ? 0 : -1
  })
  const btn = $('theme-btn')
  if (btn) btn.title = `Colours: ${THEMES.find(t => t.id === theme)?.label || theme}, ${pref === 'system' ? `system (${mode})` : mode} (C)`
}
export function themeOpen() { return !!$('theme-pop') && !$('theme-pop').hidden }
export function openTheme(open = !themeOpen()) {
  const pop = $('theme-pop'), btn = $('theme-btn')
  if (!pop) return
  pop.hidden = !open; btn.setAttribute('aria-expanded', String(open))
  if (open) (pop.querySelector('#theme-swatches .on') || pop.querySelector('button'))?.focus()
}
/** Arrow keys move within a radio group (roving tabindex), Enter / Space pick. */
function radioKeys(group, pick) {
  group.addEventListener('keydown', ev => {
    const items = [...group.querySelectorAll('button')]
    const i = items.indexOf(document.activeElement)
    if (i < 0) return
    let j = null
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') j = (i + 1) % items.length
    else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') j = (i - 1 + items.length) % items.length
    if (j === null) return
    ev.preventDefault(); ev.stopPropagation()
    items[j].focus(); pick(items[j])
  })
}
export function initThemeUI() {
  const box = $('theme'), pop = $('theme-pop'), sw = $('theme-swatches')
  if (!box || !pop || !sw) return
  sw.innerHTML = THEMES.map(t => `<button type="button" role="radio" data-pick-theme="${t.id}" aria-label="${t.label}">
      <span class="sw" data-sw="${t.id}" aria-hidden="true"><i></i><b></b></span><span class="sw-l">${t.label}</span></button>`).join('')
  $('theme-btn').addEventListener('click', ev => { ev.stopPropagation(); openTheme() })
  // data-pick-*, not data-mode / data-theme: <html> carries those, so closest() would find it from any click
  pop.addEventListener('click', ev => {
    const m = ev.target.closest('[data-pick-mode]'), t = ev.target.closest('[data-pick-theme]')
    if (m) setMode(m.dataset.pickMode)
    if (t) setTheme(t.dataset.pickTheme)
  })
  radioKeys(pop.querySelector('.mode-seg'), b => setMode(b.dataset.pickMode))
  radioKeys(sw, b => setTheme(b.dataset.pickTheme))
  pop.addEventListener('keydown', ev => { if (ev.key === 'Escape') { ev.stopPropagation(); openTheme(false); $('theme-btn').focus() } })
  document.addEventListener('click', ev => { if (themeOpen() && !box.contains(ev.target)) openTheme(false) })
  syncUI()
}
