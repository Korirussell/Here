// HERE contract v3 for TypeScript (SPEC §12). Mirrors contracts/schemas/*.schema.json.
// Used by the extras (dashboard, memory page). Type-checked against contracts/examples by
// contracts/tests/test_contracts.py (tsc --noEmit on a generated examples.ts).

export const CONTRACT_VERSION = 3 as const

/** degrees, world frame (0 = array forward), clockwise positive, (-180, 180] */
export type WorldBearing = number
/** degrees, body frame, 0 = wearer's front */
export type RelBearing = number

export type Role = 'laptop' | 'phone' | 'dashboard' | 'memory' | 'tool'
export type Src = Role | 'relay'
export type Tone = 'neutral' | 'excited' | 'laughing' | 'shouting' | 'whispering' | 'questioning' | 'sad'
export type SoundLabel =
  | 'speech' | 'doorbell' | 'dog_barking' | 'baby_crying' | 'knock' | 'alarm'
  | 'music' | 'tv' | 'laughter' | 'phone_ringing' | 'loud_bang' | 'car_horn' | 'vehicle' | 'siren' | 'door'
  | 'other' | 'unknown'   // other = recognised, not in our list; unknown = heard, not classifiable
export type AlertKind = 'doorbell' | 'name_called' | 'dog_barking' | 'baby_crying' | 'knock' | 'alarm' | 'loud_bang'
  | 'car_horn' | 'siren' | 'door'
export type SoundCategory = 'speech' | 'alert' | 'animal' | 'media' | 'other'
export type Engine = 'faster_whisper' | 'scribe_realtime'

export interface Envelope {
  type: string
  v: 3
  src: Src
  t: number
  seq?: number
  session?: string
}

// §12.4 session
export interface Hello extends Envelope {
  type: 'hello'
  role: Role
  subscribe: string[]
  app_version?: string
}

export interface Heartbeat extends Envelope {
  type: 'heartbeat'
  status: 'ok' | 'degraded' | 'error'
  detail?: string
  thermal?: 'nominal' | 'fair' | 'serious' | 'critical'
  engine?: Engine
  doa_source?: 'builtin' | 'odas'
}

export interface ClockPing extends Envelope { type: 'clock_ping'; t_phone: number }
export interface ClockPong extends Envelope { type: 'clock_pong'; t_phone: number; t_laptop: number }

export interface Config extends Envelope {
  type: 'config'
  user_name: string
  name_aliases: string[]
  focus_mode: boolean
  belt_only_mode: boolean
  features: { captions: boolean; tone: boolean; rings: boolean; alerts: boolean; belt: boolean }
  alerts_enabled: Partial<Record<AlertKind, boolean>>
  belt_strength: number
}

// §12.5 directions
export interface Source {
  id: number
  bearing_deg: WorldBearing
  energy: number
  speech: boolean
  confidence: number
}

export interface Sources extends Envelope { type: 'sources'; sources: Source[] }

// §12.6 speech
export interface SpeechStart extends Envelope {
  type: 'speech_start'
  utt_id: string
  bearing_deg: WorldBearing
  source_id: number
  confidence: number
}

export interface Utterance extends Envelope {
  type: 'utterance'
  utt_id: string
  t_start: number
  t_end: number
  text: string
  pending: string
  final: boolean
  bearing_deg: WorldBearing
  bearing_spread_deg: number
  source_id: number
  overlap: boolean
  tone: Tone | null
  tone_confidence: number
  loudness: number
  engine: Engine
  stage_ms?: Record<string, number>
}

// §12.7 sounds and alerts
export interface SoundEvent extends Envelope {
  type: 'sound_event'
  event_id: string
  label: SoundLabel
  category: SoundCategory
  emoji: string
  text: string
  confidence: number
  bearing_deg: WorldBearing | null
  t_start: number
  t_end: number
  count: number
  source_id?: number   // the sources track it came from
  model?: string       // which classifier labelled it, e.g. "panns_cnn14"
}

export interface Ref { utt_id?: string; event_id?: string }

export interface Alert extends Envelope {
  type: 'alert'
  alert_id: string
  kind: AlertKind
  label: string
  emoji: string
  priority: 1 | 2 | 3
  bearing_deg: WorldBearing | null
  active: boolean
  confidence: number
  ref?: Ref
}

export type Attention = 'ambient' | 'notice' | 'important' | 'urgent'

/** Optional enrichment of an earlier event (ref): attention (Hugging Face model), Gemini context. Never decides existence/direction. */
export interface PerceptionEnrichment extends Envelope {
  type: 'perception_enrichment'
  ref: Ref
  attention: Attention
  attention_confidence: number
  attention_model: string        // Hugging Face repo id, or "rules" (deterministic fallback)
  directed_at_user?: number      // probability the speech is aimed at the wearer
  context?: string               // Gemini one-line note
  context_model?: string
}

// §12.8 phone -> relay (extras)
export interface Attribution extends Envelope {
  type: 'attribution'
  ref: Ref
  person_label: string | null
  method: 'bearing' | 'off_camera'
  score: number
}

export interface Scene extends Envelope {
  type: 'scene'
  heading_deg: WorldBearing
  people: { label: string; bearing_deg: WorldBearing; speaking: boolean }[]
}

// §12.9 extras
export interface SearchRequest extends Envelope { type: 'search_request'; query_id: string; text: string; limit: number }
export interface SearchResult extends Envelope {
  type: 'search_result'
  query_id: string
  results: {
    t: number
    kind: 'utterance' | 'alert'
    person_label?: string
    text: string
    tone?: Tone | null
    bearing_deg?: WorldBearing
    score: number
  }[]
}

export type HereMessage =
  | Hello | Heartbeat | ClockPing | ClockPong | Config | Sources | SpeechStart | Utterance
  | SoundEvent | Alert | PerceptionEnrichment | Attribution | Scene | SearchRequest | SearchResult

/** §12.10 default subscriptions per role. */
export const DEFAULT_SUBSCRIBE: Record<Role, string[]> = {
  phone: ['sources', 'speech_start', 'utterance', 'sound_event', 'alert', 'perception_enrichment', 'config', 'clock_pong', 'heartbeat'],
  laptop: ['config', 'clock_ping'],
  dashboard: ['*'],
  memory: ['utterance', 'alert', 'sound_event', 'perception_enrichment', 'attribution', 'search_request'],
  tool: ['*'],
}

/** Wrap any angle into (-180, 180]. */
export function wrap(deg: number): number {
  const a = ((((deg + 180) % 360) + 360) % 360) - 180
  return a === -180 ? 180 : a
}
