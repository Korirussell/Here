import type { FrameMeta } from './contract'

/**
 * Connects to the HERE backend and pairs each JSON FrameMeta with the JPEG
 * that follows it. Exposes a tiny external store so React reads metadata with
 * useSyncExternalStore, while the video texture updates without re-rendering.
 */

export type Status = 'connecting' | 'live' | 'offline'

type Listener = () => void

const DEFAULT_URL = `ws://${location.hostname || 'localhost'}:8765`

class FrameStream {
  readonly url: string
  private pendingMeta: FrameMeta | null = null
  private decoding = false
  private retryMs = 500

  meta: FrameMeta | null = null
  bitmap: ImageBitmap | null = null
  status: Status = 'connecting'

  private metaListeners = new Set<Listener>()
  private frameListeners = new Set<Listener>()
  private statusListeners = new Set<Listener>()

  constructor(url: string) {
    this.url = url
    this.connect()
  }

  private setStatus(s: Status) {
    if (this.status === s) return
    this.status = s
    this.statusListeners.forEach((l) => l())
  }

  private connect() {
    this.setStatus(this.status === 'live' ? 'connecting' : this.status)
    const ws = new WebSocket(this.url)
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      this.retryMs = 500
      this.setStatus('live')
    }
    ws.onclose = () => {
      this.setStatus('offline')
      setTimeout(() => this.connect(), this.retryMs)
      this.retryMs = Math.min(this.retryMs * 2, 5000)
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        try {
          const msg = JSON.parse(ev.data)
          if (msg?.type === 'frame') this.pendingMeta = msg as FrameMeta
        } catch {
          // Ignore malformed JSON rather than dropping the connection.
        }
        return
      }
      this.onJpeg(ev.data as ArrayBuffer)
    }
  }

  private async onJpeg(buf: ArrayBuffer) {
    const meta = this.pendingMeta
    this.pendingMeta = null
    if (!meta) return
    // If the previous frame is still decoding, drop this one: staying current
    // matters more than showing every frame.
    if (this.decoding) return
    this.decoding = true
    try {
      // Pre-flip so three.js can upload the bitmap with flipY = false.
      const bitmap = await createImageBitmap(new Blob([buf], { type: 'image/jpeg' }), {
        imageOrientation: 'flipY',
      })
      const old = this.bitmap
      this.bitmap = bitmap
      this.meta = meta
      this.frameListeners.forEach((l) => l())
      this.metaListeners.forEach((l) => l())
      // The texture has already been handed the new bitmap; release the old one.
      queueMicrotask(() => old?.close())
    } catch {
      // A corrupt JPEG should not kill the stream.
    } finally {
      this.decoding = false
    }
  }

  subscribeMeta = (l: Listener) => {
    this.metaListeners.add(l)
    return () => {
      this.metaListeners.delete(l)
    }
  }
  subscribeFrame = (l: Listener) => {
    this.frameListeners.add(l)
    return () => {
      this.frameListeners.delete(l)
    }
  }
  subscribeStatus = (l: Listener) => {
    this.statusListeners.add(l)
    return () => {
      this.statusListeners.delete(l)
    }
  }
  getMeta = () => this.meta
  getStatus = () => this.status
}

const params = new URLSearchParams(location.search)
export const stream = new FrameStream(params.get('ws') ?? DEFAULT_URL)
