import type { BBox, CameraInfo } from '../contract'

/** Wrap an angle to (-180, 180]. */
export function wrapDeg(a: number): number {
  let x = ((a + 180) % 360 + 360) % 360 - 180
  if (x === -180) x = 180
  return x
}

/** Bearing relative to the camera's centre line, in degrees. */
export function relativeBearing(bearing: number, cam: CameraInfo): number {
  return wrapDeg(bearing - cam.yaw_deg)
}

/** Horizontal pixel in the video frame for a world bearing (may be off-frame). */
export function pixelForBearing(bearing: number, cam: CameraInfo, width: number): number {
  const f = width / 2 / Math.tan(((cam.hfov_deg / 2) * Math.PI) / 180)
  const rel = (relativeBearing(bearing, cam) * Math.PI) / 180
  return width / 2 + f * Math.tan(rel)
}

/**
 * Maps video pixels (origin top-left, y down) to screen-space scene units
 * (origin centre, y up). The video is fitted inside the viewport ("contain").
 */
export interface VideoFit {
  scale: number
  videoW: number
  videoH: number
  viewW: number
  viewH: number
}

export function fitVideo(videoW: number, videoH: number, viewW: number, viewH: number): VideoFit {
  const scale = Math.min(viewW / videoW, viewH / videoH)
  return { scale, videoW, videoH, viewW, viewH }
}

export function toScreen(fit: VideoFit, x: number, y: number): [number, number] {
  return [(x - fit.videoW / 2) * fit.scale, (fit.videoH / 2 - y) * fit.scale]
}

export function bboxTopCentre(fit: VideoFit, b: BBox): [number, number] {
  return toScreen(fit, b[0] + b[2] / 2, b[1])
}

export function bboxBottomCentre(fit: VideoFit, b: BBox): [number, number] {
  return toScreen(fit, b[0] + b[2] / 2, b[1] + b[3])
}

export const clamp = (v: number, lo: number, hi: number) =>
  lo > hi ? (lo + hi) / 2 : Math.min(hi, Math.max(lo, v))
