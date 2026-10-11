/**
 * The HERE frame contract.
 *
 * This file is the single source of truth for what the backend sends to ar_here.
 * Every other HERE project (vision, speech, audio, belt) produces or consumes
 * these shapes, so change it deliberately and update CLAUDE.md + the mock server
 * at the same time.
 *
 * Over one WebSocket, for every video frame the server sends:
 *   1. a text message: JSON `FrameMeta`
 *   2. immediately after, a binary message: the JPEG bytes of that same frame
 * The client pairs them by order and draws captions against the exact frame
 * they were computed from.
 */

/** Pixel box in the video frame: [x, y, width, height], origin top-left. */
export type BBox = [number, number, number, number]

export type Tone =
  | 'neutral'
  | 'excited'
  | 'laughing'
  | 'shouting'
  | 'whispering'
  | 'questioning'
  | 'sad'

export interface Face {
  /** Stable tracking id. Captions point at faces through this id. */
  id: number
  bbox: BBox
  /** Degrees in the world frame (0 = array's forward, clockwise positive). */
  bearing?: number
}

export interface Caption {
  /** Who is speaking. Matches a Face.id when the speaker is on camera. */
  speaker_id: number
  /** Optional display name ("Sarah"). Falls back to "Person {speaker_id}". */
  speaker_label?: string
  /** Words that are final and will not change. */
  text: string
  /** Provisional words still being recognised; rendered dimmer. */
  pending: string
  /** True once the utterance has ended and the caption is locked in. */
  final: boolean
  tone?: Tone | null
  /** 0..1, how sure the tone guess is. Low confidence fades the tone chip. */
  tone_confidence?: number
  /** 0..1 relative loudness. Scales caption text size a little. */
  loudness?: number
  /** World bearing of the sound. Used when the speaker has no visible face. */
  bearing?: number
}

export interface CameraInfo {
  /** Horizontal field of view of the video, in degrees. */
  hfov_deg: number
  /** Which world bearing the centre of the frame points at, in degrees. */
  yaw_deg: number
}

export interface FrameMeta {
  type: 'frame'
  frame_id: number
  /** Capture time, seconds (unix). */
  t: number
  width: number
  height: number
  camera: CameraInfo
  faces: Face[]
  /** At most one caption per speaker: the line currently on screen. */
  captions: Caption[]
}
