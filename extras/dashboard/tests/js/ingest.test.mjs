// Page logic, headless: the real static/core.js ingest. Run: node --test extras/dashboard/tests/js/ingest.test.mjs
// (python -m pytest extras/dashboard runs it too, when node is installed).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { S, ingest, resetLive, steadyBearing, bearingReports, isRetraction } from '../../static/core.js'

const T0 = 1_800_000_000
const utt = (id, text, final, extra = {}) => ({ type: 'utterance', v: 3, src: 'laptop', t: T0, utt_id: id, t_start: T0, t_end: T0 + 1,
  text, pending: '', final, bearing_deg: 180, bearing_spread_deg: 3, source_id: 1, overlap: false, tone: null,
  tone_confidence: 0, loudness: 0.5, engine: 'faster_whisper', ...extra })
const start = id => ({ type: 'speech_start', v: 3, src: 'laptop', t: T0, utt_id: id, bearing_deg: 180, source_id: 1, confidence: 0.7 })
const bell = (b, i) => ({ type: 'sound_event', v: 3, src: 'laptop', t: T0 + i, event_id: 's-19', label: 'doorbell', category: 'alert',
  emoji: '🔔', text: 'Doorbell', confidence: 0.93, bearing_deg: b, t_start: T0, t_end: T0 + i, count: 1, model: 'panns_cnn14+enrolled' })

test('an empty final (a retraction) leaves no caption card and no stream row', () => {
  resetLive()
  ingest(start('u1'), T0)
  ingest(utt('u1', 'mm', false, { pending: 'mm' }), T0 + 0.2)
  assert.deepEqual(S.captionOrder, ['u1'])                     // shown while it might be speech
  const retraction = utt('u1', '', true)
  assert.ok(isRetraction(retraction))
  ingest(retraction, T0 + 0.6)
  assert.deepEqual(S.captionOrder, [])                         // the card is gone
  assert.ok(!S.stream.some(x => x.key === 'utt:u1'))           // and its stream row
  assert.ok(S.retracted.has('u1'))
  assert.equal(S.byKey.get('utt:u1').at(-1).m.final, true)     // the inspector still has every message
  ingest(utt('u1', '', true), T0 + 0.7)                         // a repeat changes nothing
  assert.deepEqual(S.captionOrder, [])
})

test('a retracted talker gives back its voice number; real speech keeps its card', () => {
  resetLive()
  ingest(start('noise'), T0)
  ingest(utt('noise', '', true), T0 + 0.3)
  ingest(start('real'), T0 + 1)
  ingest(utt('real', 'Hey, over here!', true, { bearing_deg: 60, source_id: 0 }), T0 + 2)
  assert.deepEqual(S.captionOrder, ['real'])
  assert.equal(S.voiceOf.get('real'), 1)                       // not "Voice 2": the retracted one never existed
  assert.ok(!isRetraction(utt('x', '', true, { pending: 'wait' })))   // pending words are not a retraction
  assert.ok(!isRetraction(utt('x', '', false)))                       // nor is an empty partial
})

test('a jittery doorbell is steadied for display, and every raw report is kept', () => {
  resetLive()
  const raw = [28.8, 27.8, 0.8, 24.8, 24.8, 24.8, 0.8, 24.8, 24.8]      // the laptop's Gate 2 run, one press, one event_id
  raw.forEach((b, i) => ingest(bell(b, i), T0 + i))
  assert.equal(S.sounds.size, 1)                                         // one sound, updated in place
  assert.equal(steadyBearing('ev:s-19', 24.8), 24.8)
  assert.equal(steadyBearing('ev:s-19', 0.8), 24.8)                     // an outlier report never moves the marker
  assert.deepEqual(bearingReports('ev:s-19'), raw.slice(-7))
  // the alert for it shares the history, so card and marker agree
  ingest({ type: 'alert', v: 3, src: 'laptop', t: T0 + 9, alert_id: 'a-2', kind: 'doorbell', label: 'Doorbell', emoji: '🔔', priority: 2,
    bearing_deg: 0.8, active: true, confidence: 0.93, ref: { event_id: 's-19' } }, T0 + 9)
  assert.equal(steadyBearing('ev:s-19', 0.8), 24.8)
  assert.equal(S.alerts.size, 1)
})

test('steadying wraps around 180 / -180', () => {
  resetLive()
  ;[179, -178, 177, 20, -179].forEach((b, i) => ingest({ ...bell(b, i), event_id: 's-w' }, T0 + i))
  const s = steadyBearing('ev:s-w', 20)
  assert.ok(Math.abs(Math.abs(s) - 179) <= 2, `got ${s}`)
})
