// The LIVE orb: a field of points around the array that swells toward whatever HERE hears, with beams to
// 3D markers, floor ripples, alert pillars and the phone's view cone. three.js r160 (vendored).
// World frame: 0 deg = array front = -Z, clockwise positive (seen from above), so x = sin(b), z = -cos(b).
import * as THREE from 'three'
import { tok } from './theme.js'

// Marker colours come from the theme tokens (themes.css); readColors() refreshes them on a theme change.
const TOKENS = { speech: '--blue', sound: '--amber', unknown: '--violet', alert: '--red', hazard: '--red',
  activity: '--off-line', ink: '--orb-ink', view: '--blue', person: '--orb-ink' }
export const COLORS = {}
function readColors() { for (const [k, t] of Object.entries(TOKENS)) COLORS[k] = tok(t) }
const ORB_Y = 1.15
const MARK_R = 3.1
const SLOTS = 8

const rad = b => b * Math.PI / 180
// Reduced motion (the OS setting, or ?reduce-motion): the field holds still, no sway, sweep, ripples, bobbing or
// spinning. Markers, beams and labels still appear and fade exactly where the sounds are.
export const REDUCE_MOTION = (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches) ||
  (typeof location !== 'undefined' && new URLSearchParams(location.search).has('reduce-motion'))
export const dir = b => new THREE.Vector3(Math.sin(rad(b)), 0, -Math.cos(rad(b)))
const angDiff = (a, b) => ((a - b + 540) % 360) - 180

// ---------------------------------------------------------------------------------------------- shaders
const POINTS_VS = `
attribute float aRand;
uniform float uTime, uPixel;
uniform vec3 uDir[${SLOTS}];
uniform float uAmp[${SLOTS}];
uniform vec3 uCol[${SLOTS}];
uniform vec3 uBase;
varying vec3 vCol;
varying float vAlpha;
void main() {
  vec3 n = normalize(position);
  float spike = 0.0, glow = 0.0;
  vec3 col = uBase;
  for (int i = 0; i < ${SLOTS}; i++) {
    float d = max(dot(n, uDir[i]), 0.0);
    float k = pow(d, 14.0) * uAmp[i];
    float h = pow(d, 3.0) * uAmp[i];
    spike += k; glow += h;
    col = mix(col, uCol[i], clamp(k * 1.8 + h * 0.55, 0.0, 1.0));
  }
  float wave = 0.018 * sin(uTime * 1.2 + aRand * 6.2831) + 0.012 * sin(uTime * 0.6 + n.y * 4.0 + n.x * 3.0);
  float ripple = 0.03 * glow * sin(uTime * 6.0 - acos(clamp(dot(n, normalize(n + 0.0001)), -1.0, 1.0)) * 8.0);
  vec3 p = n * (1.0 + wave + spike * 0.95 + glow * 0.1 + ripple);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = (1.8 + spike * 6.0 + glow * 1.4) * uPixel * (6.5 / -mv.z);
  vec3 vn = normalize(normalMatrix * n);
  vAlpha = (0.18 + 0.82 * smoothstep(-0.35, 0.65, vn.z)) * (0.55 + 0.45 * clamp(glow * 1.5, 0.0, 1.0));
  vCol = col;
}`
const POINTS_FS = `
varying vec3 vCol;
varying float vAlpha;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float r = length(c);
  if (r > 0.5) discard;
  gl_FragColor = vec4(vCol, smoothstep(0.5, 0.25, r) * vAlpha);
}`

const CORE_VS = `
varying vec3 vN; varying vec3 vV; varying vec3 vW;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vW = normalize(position);
  vN = normalize(normalMatrix * normal);
  vec4 mv = viewMatrix * wp;
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`
const CORE_FS = `
uniform float uTime, uEnergy;
uniform vec3 uTint, uA, uB, uC;
varying vec3 vN; varying vec3 vV; varying vec3 vW;
void main() {
  float f = pow(1.0 - max(dot(vN, vV), 0.0), 2.2);
  vec3 a = uA, b = uB, c = uC;
  float t = 0.5 + 0.5 * sin(uTime * 0.35 + vW.y * 2.4 + vW.x * 1.7);
  vec3 col = mix(mix(a, b, t), c, 0.5 + 0.5 * sin(uTime * 0.27 + vW.z * 2.0));
  col = mix(col, uTint, 0.35 * uEnergy);
  float alpha = 0.10 + f * 0.75;
  gl_FragColor = vec4(col, alpha);
}`

const BEAM_VS = `
varying float vT;
void main() { vT = uv.y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`
const BEAM_FS = `
uniform vec3 uColor; uniform float uAlpha, uTime;
varying float vT;
void main() {
  float flow = 0.65 + 0.35 * sin(vT * 26.0 - uTime * 9.0);
  gl_FragColor = vec4(uColor, uAlpha * smoothstep(0.0, 0.35, vT) * flow);
}`

// ---------------------------------------------------------------------------------------------- floor
function floorTexture() {
  const ink = a => tok('--orb-ink', a)
  const size = 2048, c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d'), m = size / 2, px = size / 12          // 12 world units across
  g.translate(m, m)
  for (const [r, a] of [[1.6, 0.16], [2.6, 0.13], [MARK_R, 0.22], [4.4, 0.08]]) {
    g.beginPath(); g.arc(0, 0, r * px, 0, Math.PI * 2)
    g.strokeStyle = ink(a); g.lineWidth = r === MARK_R ? 3 : 2; g.stroke()
  }
  for (let b = 0; b < 360; b += 5) {
    const major = b % 90 === 0, mid = b % 15 === 0
    const r0 = MARK_R * px + 6, r1 = r0 + (major ? 46 : mid ? 26 : 12)
    const th = rad(b)
    g.beginPath(); g.moveTo(Math.sin(th) * r0, -Math.cos(th) * r0); g.lineTo(Math.sin(th) * r1, -Math.cos(th) * r1)
    g.strokeStyle = ink(major ? 0.5 : mid ? 0.25 : 0.12); g.lineWidth = major ? 4 : 2; g.stroke()
  }
  g.font = '600 46px Geist, system-ui, sans-serif'; g.fillStyle = ink(0.55); g.textAlign = 'center'; g.textBaseline = 'middle'
  for (const [b, t] of [[0, 'FRONT'], [90, 'RIGHT'], [180, 'BACK'], [270, 'LEFT']]) {
    const r = MARK_R * px + 110, th = rad(b)
    g.save(); g.translate(Math.sin(th) * r, -Math.cos(th) * r); g.rotate(th); g.fillText(t, 0, 0); g.restore()
  }
  g.fillStyle = ink(0.07)
  for (let x = -m; x < m; x += 64) for (let y = -m; y < m; y += 64) { g.beginPath(); g.arc(x, y, 2.2, 0, Math.PI * 2); g.fill() }
  const fade = g.createRadialGradient(0, 0, size * 0.28, 0, 0, size * 0.5)
  fade.addColorStop(0, 'transparent'); fade.addColorStop(1, 'black')       // an alpha mask (destination-out)
  g.globalCompositeOperation = 'destination-out'; g.fillStyle = fade; g.fillRect(-m, -m, size, size)
  const tex = new THREE.CanvasTexture(c); tex.anisotropy = 8; tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

function shadowTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 256
  const g = c.getContext('2d'), grd = g.createRadialGradient(128, 128, 0, 128, 128, 128)
  grd.addColorStop(0, tok('--orb-shadow', 0.28)); grd.addColorStop(0.5, tok('--orb-shadow', 0.08)); grd.addColorStop(1, tok('--orb-shadow', 0))
  g.fillStyle = grd; g.fillRect(0, 0, 256, 256)
  return new THREE.CanvasTexture(c)
}

// ---------------------------------------------------------------------------------------------- orb
export function createOrb(container, labels, onPick) {
  readColors()
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
  renderer.domElement.addEventListener('webglcontextlost', e => { e.preventDefault(); container.dataset.gl = 'lost' })
  renderer.domElement.addEventListener('webglcontextrestored', () => { delete container.dataset.gl })
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  container.appendChild(renderer.domElement)
  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100)
  const hemi = new THREE.HemisphereLight(tok('--orb-sky'), tok('--orb-ground'), 2.2); scene.add(hemi)
  const sun = new THREE.DirectionalLight(tok('--orb-sky'), 1.6); sun.position.set(3, 6, 4); scene.add(sun)

  // floor + orb shadow
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(12, 12), new THREE.MeshBasicMaterial({ map: floorTexture(), transparent: true, depthWrite: false }))
  floor.rotation.x = -Math.PI / 2; scene.add(floor)
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 3.4), new THREE.MeshBasicMaterial({ map: shadowTexture(), transparent: true, depthWrite: false }))
  shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.005; scene.add(shadow)

  // the array on the floor
  const array = new THREE.Group()
  const puck = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.3, 0.06, 48), new THREE.MeshStandardMaterial({ color: tok('--orb-puck'), roughness: 0.4 }))
  puck.position.y = 0.03; array.add(puck)
  const inkMat = new THREE.MeshStandardMaterial({ color: COLORS.ink })
  for (let i = 0; i < 4; i++) {
    const mic = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.02, 16), inkMat)
    const a = rad(45 + i * 90); mic.position.set(Math.sin(a) * 0.17, 0.07, -Math.cos(a) * 0.17); array.add(mic)
  }
  const arrow = new THREE.Mesh(new THREE.ConeGeometry(0.06, 0.16, 3), inkMat)
  arrow.rotation.x = -Math.PI / 2; arrow.position.set(0, 0.07, -0.42); array.add(arrow)
  scene.add(array)

  // point field
  const N = 5200, pos = new Float32Array(N * 3), rnd = new Float32Array(N)
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * Math.PI * (3 - Math.sqrt(5))
    pos.set([Math.cos(th) * r, y, Math.sin(th) * r], i * 3); rnd[i] = Math.random()
  }
  const pg = new THREE.BufferGeometry()
  pg.setAttribute('position', new THREE.BufferAttribute(pos, 3)); pg.setAttribute('aRand', new THREE.BufferAttribute(rnd, 1))
  const uniforms = { uTime: { value: 0 }, uPixel: { value: renderer.getPixelRatio() },
    uDir: { value: Array.from({ length: SLOTS }, () => new THREE.Vector3(0, 1, 0)) },
    uAmp: { value: new Array(SLOTS).fill(0) }, uCol: { value: Array.from({ length: SLOTS }, () => new THREE.Color()) }, uBase: { value: new THREE.Color(tok('--orb-point')) } }
  const points = new THREE.Points(pg, new THREE.ShaderMaterial({ vertexShader: POINTS_VS, fragmentShader: POINTS_FS, uniforms, transparent: true, depthWrite: false }))
  points.position.y = ORB_Y; scene.add(points)
  const coreU = { uTime: { value: 0 }, uEnergy: { value: 0 }, uTint: { value: new THREE.Color(COLORS.speech) },
    uA: { value: new THREE.Color(tok('--orb-core-a')) }, uB: { value: new THREE.Color(tok('--orb-core-b')) }, uC: { value: new THREE.Color(tok('--orb-core-c')) } }
  const core = new THREE.Mesh(new THREE.SphereGeometry(0.66, 64, 48), new THREE.ShaderMaterial({ vertexShader: CORE_VS, fragmentShader: CORE_FS, uniforms: coreU, transparent: true, depthWrite: false }))
  core.position.y = ORB_Y; scene.add(core)
  const orbit = new THREE.Mesh(new THREE.TorusGeometry(1.32, 0.0035, 8, 160), new THREE.MeshBasicMaterial({ color: COLORS.ink, transparent: true, opacity: 0.18 }))
  orbit.position.y = ORB_Y; orbit.rotation.x = Math.PI / 2 - 0.35; scene.add(orbit)
  const orbit2 = orbit.clone(); orbit2.material = orbit.material.clone(); orbit2.material.opacity = 0.09
  orbit2.scale.setScalar(1.12); orbit2.rotation.set(Math.PI / 2 + 0.5, 0.3, 0); scene.add(orbit2)

  // wearer view cone (phone scene)
  const cone = new THREE.Mesh(new THREE.CircleGeometry(4.2, 48, 0, rad(60)), new THREE.MeshBasicMaterial({ color: COLORS.view, transparent: true, opacity: 0.07, depthWrite: false, side: THREE.DoubleSide }))
  cone.rotation.x = -Math.PI / 2; cone.position.y = 0.01; cone.visible = false; scene.add(cone)

  // ---- markers (pooled by key) -------------------------------------------------------------------
  const markers = new Map()       // key -> marker
  const geo = { sphere: new THREE.SphereGeometry(0.1, 32, 24), oct: new THREE.OctahedronGeometry(0.14), ico: new THREE.IcosahedronGeometry(0.15, 0),
    beam: new THREE.CylinderGeometry(0.008, 0.026, 1, 12, 1, true), pillar: new THREE.CylinderGeometry(0.16, 0.16, 2.6, 32, 1, true),
    ring: new THREE.RingGeometry(0.16, 0.19, 48), disc: new THREE.CircleGeometry(0.05, 24) }
  geo.beam.translate(0, 0.5, 0); geo.beam.rotateX(Math.PI / 2)                 // along +Z, 0..1, uv.y 0 at the orb

  function makeMarker(kind) {
    const color = new THREE.Color(COLORS[kind] || COLORS.activity)
    const g = new THREE.Group()
    const beamU = { uColor: { value: color.clone() }, uAlpha: { value: 0 }, uTime: uniforms.uTime }
    const beam = new THREE.Mesh(geo.beam, new THREE.ShaderMaterial({ vertexShader: BEAM_VS, fragmentShader: BEAM_FS, uniforms: beamU, transparent: true, depthWrite: false }))
    let head
    if (kind === 'unknown') head = new THREE.LineSegments(new THREE.EdgesGeometry(geo.ico), new THREE.LineBasicMaterial({ color, transparent: true }))
    else head = new THREE.Mesh(kind === 'speech' || kind === 'activity' || kind === 'person' ? geo.sphere : geo.oct,
      new THREE.MeshStandardMaterial({ color, roughness: 0.25, metalness: 0.05, emissive: color, emissiveIntensity: 0.25, transparent: true }))
    if (kind === 'activity' || kind === 'person') head.scale.setScalar(0.55)
    const halo = new THREE.Mesh(geo.ring, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.5, side: THREE.DoubleSide, depthWrite: false }))
    const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, -ORB_Y, 0)]),
      new THREE.LineDashedMaterial({ color, transparent: true, opacity: 0.35, dashSize: 0.05, gapSize: 0.05 }))
    drop.computeLineDistances()
    const dot = new THREE.Mesh(geo.disc, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6, depthWrite: false }))
    dot.rotation.x = -Math.PI / 2
    let pillar = null, wedge = null
    if (kind === 'alert' || kind === 'hazard') {
      pillar = new THREE.Mesh(geo.pillar, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.12, depthWrite: false, side: THREE.DoubleSide }))
      wedge = new THREE.Mesh(new THREE.RingGeometry(1.25, 4.2, 32, 1, 0, rad(28)), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.12, depthWrite: false, side: THREE.DoubleSide }))
      wedge.rotation.x = -Math.PI / 2
      scene.add(wedge)
    }
    g.add(head, halo, drop); if (pillar) g.add(pillar)
    scene.add(g, beam, dot)
    return { kind, g, head, halo, drop, dot, beam, beamU, pillar, wedge, color, bearing: null, life: 0, target: 1, born: performance.now() / 1000, label: null }
  }

  function dispose(m) {
    for (const o of [m.g, m.beam, m.dot, m.wedge]) if (o) scene.remove(o)
    m.beam.material.dispose(); m.head.material.dispose(); m.halo.material.dispose(); m.drop.material.dispose(); m.drop.geometry.dispose()
    m.dot.material.dispose(); if (m.head.geometry !== geo.sphere && m.head.geometry !== geo.oct) m.head.geometry.dispose()
    if (m.pillar) m.pillar.material.dispose(); if (m.wedge) { m.wedge.material.dispose(); m.wedge.geometry.dispose() }
    if (m.label) m.label.remove()
  }

  // ---- floor ripples ------------------------------------------------------------------------------
  const ripples = []
  function ripple(bearing, kind) {
    if (REDUCE_MOTION) return
    const color = new THREE.Color(COLORS[kind] || COLORS.activity)
    const r = new THREE.Mesh(new THREE.RingGeometry(0.96, 1, 96), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6, depthWrite: false, side: THREE.DoubleSide }))
    r.rotation.x = -Math.PI / 2
    const p = bearing === null ? new THREE.Vector3() : dir(bearing).multiplyScalar(MARK_R)
    r.position.set(p.x, 0.012, p.z)
    scene.add(r); ripples.push({ r, t0: performance.now() / 1000 })
    if (ripples.length > 24) { const old = ripples.shift(); scene.remove(old.r); old.r.geometry.dispose(); old.r.material.dispose() }
  }

  // ---- camera control -----------------------------------------------------------------------------
  const cam = { az: 0, el: 33, dist: 8.2, taz: 0, tel: 33, tdist: 8.2, lastInput: 0, drag: null }
  const el = renderer.domElement
  el.addEventListener('pointerdown', e => { cam.drag = { x: e.clientX, y: e.clientY, az: cam.taz, el: cam.tel, moved: 0 }; el.setPointerCapture(e.pointerId) })
  el.addEventListener('pointermove', e => {
    if (!cam.drag) return
    const dx = e.clientX - cam.drag.x, dy = e.clientY - cam.drag.y
    cam.drag.moved = Math.max(cam.drag.moved, Math.abs(dx) + Math.abs(dy))
    cam.taz = cam.drag.az - dx * 0.3; cam.tel = Math.max(8, Math.min(70, cam.drag.el + dy * 0.2)); cam.lastInput = performance.now()
  })
  el.addEventListener('pointerup', e => {
    const d = cam.drag; cam.drag = null
    if (d && d.moved < 5) pick(e)
  })
  el.addEventListener('wheel', e => { e.preventDefault(); cam.tdist = Math.max(5, Math.min(14, cam.tdist * (1 + e.deltaY * 0.001))); cam.lastInput = performance.now() }, { passive: false })
  el.addEventListener('dblclick', () => { cam.taz = 0; cam.tel = 33; cam.tdist = 8.2 })

  const ray = new THREE.Raycaster()
  function pick(e) {
    const r = el.getBoundingClientRect()
    const v = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1)
    ray.setFromCamera(v, camera)
    const heads = [...markers.entries()].filter(([, m]) => m.pick).map(([k, m]) => { m.head.userData.key = m.pick; return m.head })
    const hit = ray.intersectObjects(heads, false)[0]
    if (hit) onPick(hit.object.userData.key)
  }

  // The orb sits right of centre (room for the headline on the left), and centred once the captions sidebar is
  // hidden (#live.rail-off), where the wider stage made the shift too big. The frame loop eases between the two.
  const shiftWanted = () => container.clientWidth > 900 && !container.closest('.rail-off') ? 0.11 : 0
  let shift = shiftWanted()
  function applyShift() {
    const w = container.clientWidth, h = container.clientHeight
    camera.setViewOffset(w, h, -w * shift, 0, w, h)
    camera.updateProjectionMatrix()
  }
  function resize() {
    const w = container.clientWidth, h = container.clientHeight
    renderer.setSize(w, h, false); camera.aspect = w / Math.max(1, h)
    applyShift()
    renderer.domElement.style.width = '100%'; renderer.domElement.style.height = '100%'
    // Resizing a WebGL canvas clears it, and the next frame() may be a paint away: draw now, or the orb blinks out
    // on every step of a resize (the captions sidebar sliding resizes the stage each frame).
    renderer.render(scene, camera)
  }
  new ResizeObserver(resize).observe(container); resize()

  // ---- per-frame state ----------------------------------------------------------------------------
  const tmp = new THREE.Vector3()
  let wanted = []                  // [{key, kind, bearing, energy, label: {html, cls}, pick}]
  let view = null                  // {heading, people}

  function setTargets(list, scene_) { wanted = list; view = scene_ }

  function project(v) {
    tmp.copy(v).project(camera)
    return [(tmp.x * 0.5 + 0.5) * container.clientWidth, (-tmp.y * 0.5 + 0.5) * container.clientHeight, tmp.z]
  }

  // idle: a slow listening sweep around the orb, with a soft floor ripple now and then
  const IDLE_COLOR = new THREE.Color(tok('--orb-idle')), IDLE_DEG_S = 22, IDLE_RIPPLE_S = 4.5
  let idleSince = null, idleRipple = 0

  // labels stay clear of what sits over the canvas (measured ~7 times a second): the headline and the legend along
  // the top push a label down; the Moment panel and the latency card push it sideways, to whichever side is nearer
  let avoid = [], aside = [], avoidAt = 0
  function measureAvoid(t) {
    if (t - avoidAt < 0.15) return                     // the Moment panel resizes as each moment arrives
    avoidAt = t
    const c = container.getBoundingClientRect()
    const box = n => { const r = n.getBoundingClientRect(); return { left: r.left - c.left, right: r.right - c.left, top: r.top - c.top - 6, bottom: r.bottom - c.top + 6 } }
    const shown = sel => [...document.querySelectorAll(sel)].filter(n => n.offsetParent && !n.hidden)
    avoid = shown('#now, .legend').map(box)
    aside = shown('#moment, #hero').map(box)
  }

  // A label (anchored bottom-centre at sx, sy) that would cover the Moment panel or the latency card moves to the
  // nearest spot beside, above or below one of them that covers neither: least overlap first, then the shortest move.
  function placeClear(sx, sy, half, h, dy) {
    const W = container.clientWidth, H = container.clientHeight     // the label's box: x +- half, (y + dy - h) .. (y + dy)
    const cover = (x, y) => aside.reduce((sum, a) => sum + Math.max(0, Math.min(x + half, a.right) - Math.max(x - half, a.left)) *
      Math.max(0, Math.min(y + dy, a.bottom) - Math.max(y + dy - h, a.top)), 0)
    if (!cover(sx, sy)) return [sx, sy]
    const cands = [[sx, sy]]
    for (const a of aside) cands.push([a.right + half + 6, sy], [a.left - half - 6, sy], [sx, a.top - 2 - dy], [sx, a.bottom + h + 2 - dy])
    let best = null
    for (const [x0, y0] of cands) {
      const x = Math.max(half, Math.min(W - half, x0)), y = Math.max(h - dy, Math.min(H - dy, y0))
      const score = cover(x, y) * 1e4 + Math.hypot(x - sx, y - sy)
      if (!best || score < best[2]) best = [x, y, score]
    }
    return [best[0], best[1]]
  }

  let last = performance.now() / 1000
  function frame() {
    const t = performance.now() / 1000, dt = Math.min(0.1, t - last); last = t
    const at = REDUCE_MOTION ? 0 : t                     // the clock every decorative motion uses
    uniforms.uTime.value = at; coreU.uTime.value = at
    measureAvoid(t)
    const sw = shiftWanted()
    if (sw !== shift) { shift = REDUCE_MOTION || Math.abs(sw - shift) < 0.002 ? sw : shift + (sw - shift) * (1 - Math.pow(0.0005, dt)); applyShift() }

    // camera: gentle idle sway; returns home after you let go
    if (!cam.drag && performance.now() - cam.lastInput > 7000) { cam.taz += (0 - cam.taz) * 0.02; cam.tel += (33 - cam.tel) * 0.02 }
    const k = 1 - Math.pow(0.001, dt)
    cam.az += (cam.taz - cam.az) * k; cam.el += (cam.tel - cam.el) * k; cam.dist += (cam.tdist - cam.dist) * k
    const az = rad(cam.az + Math.sin(at * 0.12) * 5), elv = rad(cam.el)
    camera.position.set(Math.sin(az) * Math.cos(elv) * cam.dist, ORB_Y * 0.6 + Math.sin(elv) * cam.dist, Math.cos(az) * Math.cos(elv) * cam.dist)
    camera.lookAt(0, ORB_Y * 0.62, 0)
    orbit.rotation.z = at * 0.05; orbit2.rotation.z = -at * 0.035

    // markers: create / update / fade out
    const seen = new Set()
    for (const w of wanted) {
      seen.add(w.key)
      let m = markers.get(w.key)
      if (!m) { m = makeMarker(w.kind); markers.set(w.key, m); m.bearing = w.bearing; if (w.ripple !== false) ripple(w.bearing, w.kind) }
      m.target = 1; m.energy = w.energy ?? 0.6; m.pick = w.pick
      m.bearing += angDiff(w.bearing, m.bearing) * Math.min(1, dt * 10)
      m.labelHtml = w.label
    }
    let slot = 0, energySum = 0
    const amps = uniforms.uAmp.value
    for (const [key, m] of markers) {
      if (!seen.has(key)) m.target = 0
      m.life += (m.target - m.life) * Math.min(1, dt * (m.target ? 8 : 3))
      if (m.target === 0 && m.life < 0.02) { dispose(m); markers.delete(key); continue }
      const d = dir(m.bearing), L = m.life
      const pos = d.clone().multiplyScalar(MARK_R).setY(ORB_Y)
      const bob = REDUCE_MOTION ? 0 : Math.sin(t * 2 + m.born) * 0.03
      m.g.position.set(pos.x, ORB_Y + bob, pos.z)
      const pulse = REDUCE_MOTION ? 1 : 1 + 0.12 * Math.sin(t * 5 + m.born)
      m.head.scale.setScalar((m.kind === 'activity' || m.kind === 'person' ? 0.55 : 1) * (0.4 + 0.6 * L) * (m.kind === 'speech' ? 1 + 0.35 * m.energy * Math.sin(at * 9) ** 2 : m.kind === 'person' ? 1 : pulse))
      m.head.material.opacity = L
      if (m.kind === 'unknown' || m.kind === 'sound' || m.kind === 'hazard') { m.head.rotation.y = at * 0.9; m.head.rotation.x = at * 0.4 }
      const hp = REDUCE_MOTION ? 0.35 : (t * 0.8 + m.born) % 1
      m.halo.lookAt(camera.position); m.halo.scale.setScalar(1 + hp * 1.6); m.halo.material.opacity = 0.45 * L * (1 - hp)
      m.drop.material.opacity = 0.35 * L
      m.dot.position.set(pos.x, 0.015, pos.z); m.dot.material.opacity = 0.6 * L
      const start = d.clone().multiplyScalar(1.12).setY(ORB_Y)
      m.beam.position.copy(start); m.beam.lookAt(pos.x, ORB_Y + bob, pos.z)
      m.beam.scale.set(1 + m.energy, 1 + m.energy, start.distanceTo(m.g.position) - 0.12)
      m.beamU.uAlpha.value = (m.kind === 'person' ? 0 : m.kind === 'activity' ? 0.25 : 0.75) * L
      if (m.pillar) {
        const p = REDUCE_MOTION ? 0.6 : 0.5 + 0.5 * Math.sin(t * 5)
        m.pillar.position.y = 1.3 - ORB_Y; m.pillar.material.opacity = (0.08 + 0.12 * p) * L
        m.wedge.rotation.z = rad(76 - m.bearing)          // sector angle θ maps to bearing 90 - θ; 28° wide
        m.wedge.material.opacity = (0.06 + 0.12 * p) * L
      }
      if (slot < SLOTS && m.kind !== 'person') {          // people are not sounds: no swell
        uniforms.uDir.value[slot].copy(d); uniforms.uCol.value[slot].copy(m.color)
        const target = L * (m.kind === 'activity' ? 0.35 : m.kind === 'speech' ? 0.75 + 0.35 * m.energy : 0.9)
        amps[slot] = target; energySum += target; if (slot === 0) coreU.uTint.value.copy(m.color)
        slot++
      }
      // label
      if (m.labelHtml) {
        if (!m.label) {
          m.label = document.createElement('div'); m.label.className = `tag3d k-${m.kind}`
          m.label.addEventListener('click', () => m.pick && onPick(m.pick)); labels.appendChild(m.label)
        }
        if (m.label._html !== m.labelHtml) { m.label.innerHTML = m.labelHtml; m.label._html = m.labelHtml; m.label._w = m.label.offsetWidth; m.label._h = m.label.offsetHeight
          m.label._dy = parseFloat(getComputedStyle(m.label).marginTop) || 0 }    // an alert tag sits higher (CSS margin)
        let [sx, sy, sz] = project(m.g.position.clone().setY(ORB_Y + 0.32))
        const half = (m.label._w || 0) / 2 + 8                     // keep the whole label on screen
        sx = Math.max(half, Math.min(container.clientWidth - half, sx))
        for (const a of avoid) if (sx + half > a.left && sx - half < a.right) sy = Math.max(sy, a.bottom + (m.label._h || 0))
        if (aside.length) [sx, sy] = placeClear(sx, sy, half, m.label._h || 0, m.label._dy || 0)
        m.label.style.transform = `translate(-50%, -100%) translate(${sx.toFixed(1)}px, ${sy.toFixed(1)}px)`
        m.label.style.opacity = sz > 1 ? 0 : L
        m.label.style.zIndex = String(1000 - Math.round(sz * 1000))
      } else if (m.label) { m.label.remove(); m.label = null }     // a marker can lose its label (a person who starts talking)
    }
    if (slot === 0 && !REDUCE_MOTION) {
      if (idleSince === null) idleSince = t
      const idle = Math.min(1, (t - idleSince) / 1.5)
      const b = (t * IDLE_DEG_S) % 360
      uniforms.uDir.value[0].copy(dir(b)).setY(0.15).normalize(); uniforms.uCol.value[0].copy(IDLE_COLOR)
      amps[0] = 0.5 * idle * (0.8 + 0.2 * Math.sin(t * 1.3)); slot = 1
      if (idle >= 1 && t - idleRipple > IDLE_RIPPLE_S) { ripple(b, 'activity'); idleRipple = t }
    } else idleSince = null
    for (let i = slot; i < SLOTS; i++) amps[i] *= 0.9
    coreU.uEnergy.value += (Math.min(1, energySum) - coreU.uEnergy.value) * 0.08

    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i], a = (t - r.t0) / 2.2
      if (a >= 1) { scene.remove(r.r); r.r.geometry.dispose(); r.r.material.dispose(); ripples.splice(i, 1); continue }
      r.r.scale.setScalar(0.15 + a * 1.4); r.r.material.opacity = 0.55 * (1 - a) ** 2
    }

    if (view) {
      cone.visible = true; cone.rotation.z = rad(60 - view.heading)   // 60° wide, centred on the heading
      cone.material.opacity = 0.08
    } else cone.visible = false

    renderer.render(scene, camera)
    requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)

  // ---- theme: recolour the running scene from the tokens (no rebuild, no reload) ---------------------------
  function retint(m) {
    const c = COLORS[m.kind] || COLORS.activity
    m.color.set(c); m.beamU.uColor.value.set(c); m.head.material.color.set(c); m.head.material.emissive?.set(c)
    for (const o of [m.halo, m.drop, m.dot, m.pillar, m.wedge]) o?.material.color.set(c)
  }
  function applyTheme() {
    readColors()
    for (const mesh of [floor, shadow]) mesh.material.map.dispose()
    floor.material.map = floorTexture(); shadow.material.map = shadowTexture()
    floor.material.needsUpdate = shadow.material.needsUpdate = true
    hemi.color.set(tok('--orb-sky')); hemi.groundColor.set(tok('--orb-ground')); sun.color.set(tok('--orb-sky'))
    puck.material.color.set(tok('--orb-puck')); inkMat.color.set(COLORS.ink)
    orbit.material.color.set(COLORS.ink); orbit2.material.color.set(COLORS.ink); cone.material.color.set(COLORS.view)
    uniforms.uBase.value.set(tok('--orb-point'))
    coreU.uA.value.set(tok('--orb-core-a')); coreU.uB.value.set(tok('--orb-core-b')); coreU.uC.value.set(tok('--orb-core-c'))
    IDLE_COLOR.set(tok('--orb-idle'))
    for (const m of markers.values()) retint(m)
  }

  return { setTargets, ripple, project, applyTheme, resetCamera: () => { cam.taz = 0; cam.tel = 33; cam.tdist = 8.2 } }
}
