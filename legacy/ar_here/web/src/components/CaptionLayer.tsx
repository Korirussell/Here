import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { Caption as CaptionData, FrameMeta } from '../contract'
import {
  bboxBottomCentre,
  bboxTopCentre,
  clamp,
  pixelForBearing,
  relativeBearing,
  toScreen,
  type VideoFit,
} from '../lib/geometry'
import { stream } from '../stream'
import { theme } from '../theme'
import { Caption, type CaptionLayout } from './Caption'

interface Entry {
  data: CaptionData
  leaving: boolean
  leftAt: number
}

/** Keeps captions alive for a short exit animation after the server drops them. */
function useCaptionEntries(meta: FrameMeta | null) {
  const entries = useRef(new Map<number, Entry>())
  const [, bump] = useState(0)

  useEffect(() => {
    if (!meta) return
    const seen = new Set<number>()
    for (const cap of meta.captions) {
      seen.add(cap.speaker_id)
      entries.current.set(cap.speaker_id, { data: cap, leaving: false, leftAt: 0 })
    }
    const now = performance.now()
    for (const [id, e] of entries.current) {
      if (seen.has(id)) continue
      if (!e.leaving) {
        e.leaving = true
        e.leftAt = now
      } else if (now - e.leftAt > theme.motion.exitMs) {
        entries.current.delete(id)
      }
    }
    bump((n) => n + 1)
  }, [meta])

  return [...entries.current.values()]
}

type Size = { w: number; h: number }

function boxSize(measured: { textW: number; textH: number } | undefined, fontSize: number): Size {
  const c = theme.caption
  const textW = measured?.textW ?? c.minWidth - 2 * c.pad
  const textH = measured?.textH ?? fontSize * c.lineHeight
  const q = (v: number) => Math.ceil(v / 4) * 4 // fewer geometry rebuilds
  return {
    w: q(clamp(textW + 2 * c.pad, c.minWidth, c.maxTextWidth + 2 * c.pad)),
    h: q(c.pad + c.headerHeight + textH + c.pad),
  }
}

/**
 * Decides where each caption sits:
 *  1. speaker's face is tracked  -> above the face (below it if no room), tail to the head
 *  2. no face but a bearing       -> at that bearing; at the frame edge with a chevron if off-camera
 *  3. neither                     -> bottom centre, like classic subtitles
 * Then nudges overlapping boxes apart vertically.
 */
function computeLayouts(
  entries: Entry[],
  meta: FrameMeta,
  fit: VideoFit,
  sizes: Map<number, { textW: number; textH: number }>,
): Map<number, CaptionLayout> {
  const c = theme.caption
  const halfW = (fit.videoW * fit.scale) / 2
  const halfH = (fit.videoH * fit.scale) / 2
  const m = c.screenMargin
  const out = new Map<number, CaptionLayout>()

  for (const { data } of entries) {
    const { w, h } = boxSize(sizes.get(data.speaker_id), c.fontSize)
    const face = meta.faces.find((f) => f.id === data.speaker_id)
    let layout: CaptionLayout

    if (face) {
      const [ax, ay] = bboxTopCentre(fit, face.bbox)
      let y = ay + c.gap + c.tailHeight + h / 2
      let tailDir: -1 | 1 = -1
      if (y + h / 2 > halfH - m) {
        // No room above: sit under the face. Never over the mouth.
        const [, by] = bboxBottomCentre(fit, face.bbox)
        y = by - c.gap - c.tailHeight - h / 2
        tailDir = 1
      }
      const x = clamp(ax, -halfW + m + w / 2, halfW - m - w / 2)
      const tailX = clamp(ax - x, -w / 2 + c.radius + c.tailWidth, w / 2 - c.radius - c.tailWidth)
      layout = { x, y, w, h, tailX, tailDir, edge: null }
    } else if (data.bearing != null) {
      const rel = relativeBearing(data.bearing, meta.camera)
      const inView = Math.abs(rel) <= meta.camera.hfov_deg / 2
      const y = halfH * 0.45
      if (inView) {
        const [ax] = toScreen(fit, pixelForBearing(data.bearing, meta.camera, meta.width), 0)
        const x = clamp(ax, -halfW + m + w / 2, halfW - m - w / 2)
        layout = { x, y, w, h, tailX: null, tailDir: -1, edge: null }
      } else {
        const edge = rel < 0 ? 'left' : 'right'
        const inset = m + 56 // leave room for the chevron
        const x = edge === 'left' ? -halfW + inset + w / 2 : halfW - inset - w / 2
        layout = { x, y, w, h, tailX: null, tailDir: -1, edge }
      }
    } else {
      layout = { x: 0, y: -halfH + m + h / 2, w, h, tailX: null, tailDir: -1, edge: null }
    }
    out.set(data.speaker_id, layout)
  }

  // Resolve overlaps: push the lower box down a little and the upper one up.
  const list = [...out.values()]
  for (let iter = 0; iter < 6; iter++) {
    let moved = false
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const a = list[i]
        const b = list[j]
        const ox = (a.w + b.w) / 2 + 12 - Math.abs(a.x - b.x)
        const oy = (a.h + b.h) / 2 + 12 - Math.abs(a.y - b.y)
        if (ox > 0 && oy > 0) {
          const push = oy / 2
          if (a.y >= b.y) {
            a.y += push
            b.y -= push
          } else {
            a.y -= push
            b.y += push
          }
          moved = true
        }
      }
    }
    if (!moved) break
  }
  for (const l of list) l.y = clamp(l.y, -halfH + m + l.h / 2, halfH - m - l.h / 2)
  return out
}

export function CaptionLayer({ fit }: { fit: VideoFit }) {
  const meta = useSyncExternalStore(stream.subscribeMeta, stream.getMeta)
  const entries = useCaptionEntries(meta)
  const [sizes, setSizes] = useState(() => new Map<number, { textW: number; textH: number }>())

  const onMeasure = useCallback((id: number, textW: number, textH: number) => {
    setSizes((prev) => {
      const old = prev.get(id)
      if (old && Math.abs(old.textW - textW) < 1 && Math.abs(old.textH - textH) < 1) return prev
      const next = new Map(prev)
      next.set(id, { textW, textH })
      return next
    })
  }, [])

  const layouts = useMemo(
    () => (meta ? computeLayouts(entries, meta, fit, sizes) : new Map<number, CaptionLayout>()),
    [entries, meta, fit, sizes],
  )

  if (!meta) return null
  return (
    <>
      {entries.map((e) => {
        const layout = layouts.get(e.data.speaker_id)
        if (!layout) return null
        return (
          <Caption
            key={e.data.speaker_id}
            data={e.data}
            layout={layout}
            leaving={e.leaving}
            onMeasure={onMeasure}
          />
        )
      })}
    </>
  )
}
