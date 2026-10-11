// HOW IT WORKS: the architecture (architecture.js), each node lit from the running system: live, fallback or not
// connected. Same provider logic as the inspector and the pipeline strip; nothing secret is shown.
import {
  S, now, engineProvider, modelProvider, transportProvider, storageProvider, insightProvider, laptopLive, scripted,
  geminiSeen, phoneSeen, localReplay, serverRelay,
} from './core.js'
import { lastOf } from './live.js'
import { archSvg, PRINT } from './architecture.js'
import { tok, current } from './theme.js'

const $ = id => document.getElementById(id)
const state = tag => tag === 'off' ? 'off' : tag === 'fallback' || tag === 'seed' ? 'fallback' : 'live'

const IDLE_S = 60
function from(p, none, type) {
  if (!p) return { state: 'off', provider: 'not connected', note: none }
  const age = type && S.lastRx[type] ? now() - S.lastRx[type] : 0
  if (age > IDLE_S) return { state: 'idle', provider: 'idle', note: `last: ${p.name}, ${Math.round(age / 60)} min ago` }
  return { state: state(p.tag), provider: p.name, note: p.detail }
}

/** The phone, from what it sends: heartbeat (status, thermal), scene (faces + heading), attribution. */
function phoneState() {
  const hb = S.heartbeats.get('phone')?.m
  const sceneOn = S.scene && now() - S.sceneRx < 5
  const bits = [sceneOn ? `${(S.scene.people || []).length} face${(S.scene.people || []).length === 1 ? '' : 's'} · heading ${Math.round(S.scene.heading_deg)}°` : 'scene off (Settings › Send scene)']
  if (hb?.thermal) bits.push(`thermal ${hb.thermal}`)
  if (hb?.status === 'degraded') return { state: 'fallback', provider: `iPhone degraded`, note: hb.detail || bits.join(' · ') }
  return { state: 'live', provider: 'iPhone connected', note: bits.join(' · ') }
}

export function liveStates() {
  const hb = S.heartbeats.get('laptop')?.m
  const doa = hb?.doa_source === 'odas' ? 'ODAS tracker' : 'XVF3800 built-in DoA'
  const array = laptopLive() ? { state: 'live', provider: 'ReSpeaker XVF3800', note: 'laptop heartbeat' }
    : scripted() ? { state: 'fallback', provider: 'Scripted scenes', note: 'no microphones' }
      : { state: 'off', provider: 'not connected', note: 'no laptop heartbeat' }
  const direction = laptopLive() ? { state: 'live', provider: doa, note: 'bearings in the world frame' }
    : scripted() ? { state: 'fallback', provider: 'Scripted bearings', note: 'from the recorded scene' } : { state: 'off', provider: 'not connected' }
  const engine = lastOf('utterance')?.engine || (laptopLive() ? hb?.engine : null)
  let relay = S.status.relay?.connected || localReplay() ? from(transportProvider()) : { state: 'off', provider: 'not connected', note: 'no relay reachable' }
  const sr = serverRelay()
  if (sr?.where === 'vultr') {                         // the memory service's status from the Vultr box: is the laptop's mirror there?
    relay = !sr.ok ? { state: relay.state === 'off' ? 'off' : relay.state, provider: relay.provider, note: `Vultr: ${sr.reason}` }
      : sr.roles.has('laptop') ? { state: 'live', provider: 'Vultr relay', note: `${[...sr.roles].sort().join(' · ')}${sr.rate !== null ? ` · ${sr.rate.toFixed(1)} msg/s` : ''}` }
        : { state: relay.state === 'live' ? 'live' : 'fallback', provider: relay.provider, note: 'Vultr up, laptop mirror not connected' }
  } else if (sr?.ok && relay.state !== 'off') {
    relay = { ...relay, note: `${[...sr.roles].sort().join(' · ') || 'no clients'}${sr.rate !== null ? ` · ${sr.rate.toFixed(1)} msg/s` : ''}` }
  }
  const phoneOnRelay = !!sr?.ok && sr.roles.has('phone')
  const enr = lastOf('perception_enrichment')
  return {
    respeaker: array,
    direction,
    speech: from(engineProvider(engine), 'no captions yet', laptopLive() ? null : 'utterance'),
    sounds: from(modelProvider(lastOf('sound_event')?.model), 'no sounds yet', 'sound_event'),
    attention: from(modelProvider(enr?.attention_model), 'no enrichment yet', 'perception_enrichment'),
    gemini: geminiSeen() ? { state: 'live', provider: 'Gemini', note: 'context notes arriving' } : { state: 'off', provider: 'not connected', note: 'no context notes this session' },
    relay,
    phone: phoneSeen() ? phoneState()
      : phoneOnRelay ? { state: 'live', provider: 'iPhone on the relay', note: 'connected; nothing sent yet' }
        : { state: 'off', provider: 'not connected', note: 'no scene or attribution' },
    belt: { state: 'off', provider: 'not reported here', note: 'the iPhone drives it over Bluetooth' },
    dashboard: { state: S.pageConnected ? 'live' : 'off', provider: S.pageConnected ? 'this screen' : 'server offline', note: localReplay() ? 'playing a local replay' : 'every message, live' },
    neon: from(storageProvider(), 'memory API not reached'),
    databricks: from(S.insightSource ? insightProvider(S.insightSource) : null, 'no insights loaded'),
  }
}

// The architecture in the current colour theme (the download keeps the print palette).
const palette = () => ({ ink: tok('--text'), ink2: tok('--text-2'), ink3: tok('--text-3'), line: tok('--line-2'), bg: tok('--bg'),
  panel: tok('--bg-2'), live: tok('--green-ink'), liveBg: tok('--tint-green'), fb: tok('--amber-ink'), fbBg: tok('--tint-amber'),
  off: tok('--off-line'), offBg: tok('--bg'), blue: tok('--blue-ink'), dim: tok('--bg-3'), onFill: tok('--bg'), edgeOff: tok('--edge-off') })

let last = ''
export function renderHow() {
  const states = liveStates()
  const key = JSON.stringify(states) + current().theme + current().mode
  if (key === last) return                               // redraw only when a state (or the colour theme) changes
  last = key
  $('how-svg').innerHTML = archSvg(states, { title: false, palette: palette() })
  const n = s => Object.values(states).filter(x => x.state === s).length
  $('how-sum').textContent = `${n('live')} live · ${n('fallback')} on a fallback · ${n('idle')} idle · ${n('off')} not connected`
}

export function downloadSvg() {
  const blob = new Blob([archSvg(liveStates(), { title: true, palette: PRINT })], { type: 'image/svg+xml' })
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'here-architecture-live.svg' })
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
