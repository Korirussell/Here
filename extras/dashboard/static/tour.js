// Judge tour (T): a guided ~80 s auto-play for the booth. LIVE (a voice behind -> doorbell -> name called, as a
// labelled local replay, no relay needed), then MEMORY (one Ask HERE answer with its evidence), then REPLAY LAB (one
// before/after). Captions say what is happening. Any key or click hands control back.
import { esc } from './core.js'
import { replayState, setPreset } from './replay.js'

const $ = id => document.getElementById(id)
let T = null                     // {timers, hooks}

const pick = (list, re) => list.find(q => re.test(q)) || list[0]
const sleep = ms => new Promise(r => { const id = setTimeout(r, ms); T?.timers.push(id) })

function say(step, total, title, text) {
  const el = $('tour')
  el.hidden = false
  el.innerHTML = `<div class="tour-k">Judge tour · ${step}/${total}<span>press any key or click to take over</span></div>
    <div class="tour-t">${esc(title)}</div><div class="tour-x">${esc(text)}</div>
    <div class="tour-dots">${Array.from({ length: total }, (_, i) => `<i class="${i < step ? 'on' : ''}"></i>`).join('')}</div>`
}

export const touring = () => !!T

export function stopTour(api) {
  if (!T) return
  for (const id of T.timers) clearTimeout(id)
  document.removeEventListener('keydown', T.onInput, true)
  document.removeEventListener('pointerdown', T.onInput, true)
  T = null
  $('tour').hidden = true
  api?.demo({ action: 'stop' })
}

/** api: {show(tab, arg), demo(params), ask(q), suggestions} */
export async function startTour(api) {
  stopTour()
  const onInput = ev => { if (ev.type === 'keydown' && ev.repeat) return; stopTour(api) }
  T = { timers: [], onInput }
  // listen from the next tick, so the T that started the tour doesn't end it
  setTimeout(() => { if (T?.onInput === onInput) { document.addEventListener('keydown', onInput, true); document.addEventListener('pointerdown', onInput, true) } }, 0)
  const live = () => !!T && T.onInput === onInput
  const N = 7

  api.show('live')
  api.demo({ moment: 'tour', local: '1' })          // rear_speech -> doorbell_rear -> name_called_rear (~26 s)
  say(1, N, 'This is what HERE understands, right now', 'The orb is the room around the wearer. Every sound becomes a direction. Watch behind the wearer.')
  await sleep(1200); if (!live()) return
  say(2, N, 'A voice behind the wearer', 'HERE places it at 180° and captions it. The belt buzzes toward the speaker, so the wearer turns around and the caption slides into view.')
  await sleep(7400); if (!live()) return
  say(3, N, 'A doorbell, behind', 'HERE names the sound and keeps its direction. The glasses show a ring pointing back; the belt pulses that way.')
  await sleep(8400); if (!live()) return
  say(4, N, 'Someone calls the wearer’s name', 'From behind, so it is important: a full card on the glasses plus a belt buzz. Click any event to see which provider produced each part of it.')
  await sleep(9800); if (!live()) return

  api.show('memory')
  await sleep(600); if (!live()) return
  const q = pick(api.suggestions, /name/i)
  say(5, N, 'HERE remembers the day', `Ask it anything. “${q}” The answer cites the exact moments it came from, highlighted on the timeline.`)
  api.ask(q)
  await sleep(1200); if (!live()) return
  $('ask-answer')?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  await sleep(14000); if (!live()) return

  setPreset('Hear everything')
  api.show('replay')
  let r = null
  for (let i = 0; i < 40 && live(); i++) { await sleep(150); r = replayState(); if (r?.altRes) break }
  if (!live()) return
  const a = r?.actual, b = r?.altRes
  say(6, N, 'Replay lab: what HERE chose to surface',
    a && b ? `${r.segment}: as it happened, HERE surfaced ${a.surfaced} of ${a.surfaced + a.suppressed} events. If it passed everything through, ${b.surfaced}. The ${a.kept_important} important alert${a.kept_important === 1 ? '' : 's'} got through either way; background chatter stayed in the background.`
      : 'The same recorded moments under another attention policy: what reaches the wearer, and why.')
  await sleep(16000); if (!live()) return

  api.show('live')
  say(7, N, 'That’s HERE', 'Where every sound is, what it is, who said it, what reached the wearer, and what it remembers. Press T to watch again.')
  await sleep(9000); if (!live()) return
  stopTour(api)
}
