// The Moment view's builder, headless: the real static/moment.js over the real static/core.js ingest.
// Run: node --test extras/dashboard/tests/js/moment.test.mjs (python -m pytest extras/dashboard runs it too).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { S, ingest, resetLive } from '../../static/core.js'
import { buildMoment, rankOf, replaces, completes, track } from '../../static/moment.js'

const T0 = 1_800_000_000
const base = { v: 3, src: 'laptop' }
const start = (id, t) => ({ ...base, type: 'speech_start', t, utt_id: id, bearing_deg: 180, source_id: 1, confidence: 0.8 })
const utt = (id, t, text, final, extra = {}) => ({ ...base, type: 'utterance', t, utt_id: id, t_start: T0, t_end: t, text, pending: '',
  final, bearing_deg: 180, bearing_spread_deg: 3, source_id: 1, overlap: false, tone: null, tone_confidence: 0, loudness: 0.5,
  engine: 'scribe_realtime', ...extra })
const enrich = (ref, t, extra = {}) => ({ ...base, type: 'perception_enrichment', t, ref, attention: 'important', attention_confidence: 0.9,
  attention_model: 'MoritzLaurer/deberta-v3-base-zeroshot-v2.0', ...extra })
function live() {
  S.status = { relay: { connected: true }, demo: { running: false } }
  S.demoSeen = 0
  S.heartbeats.set('laptop', { m: { ...base, type: 'heartbeat', t: Date.now() / 1000, doa_source: 'builtin' }, rx: Date.now() / 1000 })
}
const step = (mo, id) => mo.steps.find(s => s.id === id)

test('a speech moment: offsets from the audio onset, each step names its provider', () => {
  resetLive(); S.heartbeats.clear(); live()
  ingest(start('u1', T0 + 0.20), T0 + 0.202)                                    // VAD 200 ms after the onset, 2 ms transport
  ingest(utt('u1', T0 + 0.55, 'Hey Kori', false), T0 + 0.551)
  ingest(utt('u1', T0 + 1.30, 'Hey Kori, the pizza is here.', true, { stage_ms: { asr: 410 } }), T0 + 1.302)
  ingest(enrich({ utt_id: 'u1' }, T0 + 1.60, { context: 'Someone says food arrived.', context_model: 'gemini-2.5-flash' }), T0 + 1.601)
  const mo = buildMoment('utt:u1')
  assert.equal(mo.kind, 'speech')
  assert.equal(mo.source, 'live')
  assert.equal(step(mo, 'onset').at, 0)
  assert.equal(step(mo, 'onset').provider.name, 'ReSpeaker')
  assert.equal(step(mo, 'start').at, 202)
  assert.equal(step(mo, 'words').at, 551)
  assert.equal(step(mo, 'words').provider.name, 'ElevenLabs')
  assert.equal(step(mo, 'final').at, 1302)
  assert.match(step(mo, 'final').note, /410 ms/)
  assert.equal(step(mo, 'attention').provider.name, 'Hugging Face')
  assert.match(step(mo, 'attention').result, /important → card \+ belt/)
  assert.equal(step(mo, 'context').provider.name, 'Gemini')
  assert.equal(mo.words, 'Hey Kori, the pizza is here.')
  assert.equal(mo.span, 1601)
  assert.match(step(mo, 'start').note, /not measured/)                          // the phone side is never claimed
})

test('missing pieces are labelled, never invented', () => {
  resetLive(); S.heartbeats.clear(); live()
  ingest(start('u2', T0), T0 + 0.003)
  const mo = buildMoment('utt:u2')
  assert.equal(step(mo, 'words').at, null)
  assert.equal(step(mo, 'words').provider.tag, 'off')
  assert.equal(step(mo, 'attention').provider.name, 'Deterministic rules')       // no enrichment: the rules' call
  assert.equal(step(mo, 'context').provider.detail, 'not connected')
  S.heartbeats.get('laptop').m.detail = 'Gemini paused: quota exhausted (retry in 4 min)'
  assert.match(step(buildMoment('utt:u2'), 'context').provider.detail, /paused · quota exhausted/)
})

test('a doorbell alert moment: classified by the enrolled template, alert, attention', () => {
  resetLive(); S.heartbeats.clear(); live()
  ingest({ ...base, type: 'sound_event', t: T0 + 0.7, event_id: 's1', label: 'doorbell', category: 'alert', emoji: '🔔', text: 'Doorbell',
    confidence: 1, bearing_deg: 172, t_start: T0, t_end: T0 + 0.7, count: 1, model: 'panns_cnn14+enrolled' }, T0 + 0.702)
  ingest({ ...base, type: 'alert', t: T0 + 0.71, alert_id: 'a1', kind: 'doorbell', label: 'Doorbell', emoji: '🔔', priority: 2, active: true,
    bearing_deg: 172, ref: { event_id: 's1' } }, T0 + 0.712)
  const mo = buildMoment('ev:s1')
  assert.equal(mo.kind, 'alert')
  assert.equal(mo.rank, 3)
  assert.equal(step(mo, 'class').at, 702)
  assert.equal(step(mo, 'class').provider.name, 'Enrolled sounds')
  assert.equal(step(mo, 'alert').at, 712)
  assert.match(step(mo, 'alert').note, /what the phone is told/)
})

test('scripted and replayed moments say so; a replay has no times', () => {
  resetLive(); S.heartbeats.clear()
  S.status = { relay: { connected: true }, demo: { running: true } }
  ingest(start('u3', T0), T0 + 0.002)
  assert.equal(buildMoment('utt:u3').source, 'scripted')
  resetLive()
  ingest(start('u4', T0), T0 + 0.002, true)                                      // local replay: timed by the server
  const mo = buildMoment('utt:u4')
  assert.equal(mo.source, 'replay')
  assert.ok(mo.steps.every(s => s.at === null))
  assert.equal(buildMoment('utt:nothing'), null)
})

test('ranking: alerts and hazards outrank speech; an important moment holds before lesser ones replace it', () => {
  resetLive()
  assert.equal(rankOf({ type: 'alert', active: true }), 3)
  assert.equal(rankOf({ type: 'alert', active: false }), 0)
  assert.equal(rankOf({ type: 'sound_event', label: 'siren', event_id: 'x' }), 3)
  assert.equal(rankOf({ type: 'speech_start' }), 1)
  assert.equal(rankOf({ type: 'utterance', final: true, text: '', pending: '' }), 0)   // a retraction never shows
  const bell = { key: 'ev:s1', rank: 3, at: 100, doneAt: 100 }        // an alert is complete on arrival
  assert.ok(!replaces(bell, { key: 'utt:u9', rank: 1, at: 103 }))                   // speech can't steal it at once
  assert.ok(replaces(bell, { key: 'utt:u9', rank: 1, at: 108.5 }))                  // after the hold it can
  assert.ok(!replaces(bell, { key: 'al:a2', rank: 3, at: 101 }))                    // another alert lets it be read first
  assert.ok(replaces(bell, { key: 'al:a2', rank: 3, at: 102.6 }))                   // ...then takes over
  assert.ok(!replaces(bell, { key: 'ev:s1', rank: 3, at: 120 }))                    // updates aren't replacements
  const talk = { key: 'utt:a', rank: 1, at: 50, doneAt: null }
  assert.ok(!replaces(talk, { key: 'utt:b', rank: 1, at: 53 }))                    // the next talker waits for the final
  assert.ok(replaces(talk, { key: 'utt:b', rank: 1, at: 56.1 }))                   // ...up to SETTLE_S
  const said = { ...talk, doneAt: 52 }
  assert.ok(!replaces(said, { key: 'utt:b', rank: 1, at: 53 }))                    // a finished caption is read first
  assert.ok(replaces(said, { key: 'utt:b', rank: 1, at: 54.6 }))                   // READ_S after it finished
  assert.ok(replaces(talk, { key: 'ev:s9', rank: 3, at: 50.2 }))                   // a hazard never waits
  assert.ok(!replaces(bell, { key: 'ev:s2', rank: 1, at: 104 }))                   // a lesser sound waits for the hold
  assert.ok(completes({ type: 'utterance', final: true }) && !completes({ type: 'utterance', final: false }))
  assert.ok(replaces(null, { key: 'utt:u1', rank: 1, at: 0 }))
  assert.ok(!replaces(null, { key: 'x', rank: 0, at: 0 }))
})

test('tracking: a voice that turns into a name call holds from the alert, not from the voice', () => {
  resetLive()
  let cur = null
  cur = track(cur, start('n1', T0), 'utt:n1', 100)
  cur = track(cur, utt('n1', T0, 'Hey Kori, the pizza is here!', true), 'utt:n1', 102.8)
  assert.equal(cur.rank, 1)
  assert.equal(cur.doneAt, 102.8)
  cur = track(cur, { ...base, type: 'alert', t: T0, alert_id: 'a9', kind: 'name_called', label: 'Name called', priority: 2,
    active: true, bearing_deg: 180, ref: { utt_id: 'n1' } }, 'utt:n1', 103)
  assert.equal(cur.rank, 3)
  assert.equal(cur.at, 103)                                                      // promoted: the hold restarts
  const other = start('n2', T0)
  assert.equal(track(cur, other, 'utt:n2', 109).key, 'utt:n1')                   // 6 s after the alert: still held
  assert.equal(track(cur, other, 'utt:n2', 111.1).key, 'utt:n2')                 // 8 s after: lesser moments may come
})
