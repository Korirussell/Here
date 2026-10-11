import { MeshTransmissionMaterial, RoundedBox, Text } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { easing } from 'maath'
import { Suspense, useLayoutEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { Caption as CaptionData } from '../contract'
import { captionDisplay } from '../lib/text'
import { lite, speakerColor, theme } from '../theme'
import { ToneChip } from './ToneChip'

export interface CaptionLayout {
  /** Centre of the glass box, screen units. */
  x: number
  y: number
  w: number
  h: number
  /** Tail position along the box edge, relative to the box centre. null = no tail. */
  tailX: number | null
  /** -1: tail on the bottom pointing down (caption above face). 1: tail on top. */
  tailDir: -1 | 1
  /** Off-screen speaker: show a chevron on this side. */
  edge: 'left' | 'right' | null
}

const reducedMotion =
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

const glow = (hex: string, k = 1.35) => new THREE.Color(hex).multiplyScalar(k)

export function Caption({
  data,
  layout,
  leaving,
  onMeasure,
}: {
  data: CaptionData
  layout: CaptionLayout
  leaving: boolean
  onMeasure: (speakerId: number, textW: number, textH: number) => void
}) {
  const c = theme.caption
  const group = useRef<THREE.Group>(null!)
  const accent = speakerColor(data.speaker_id)
  const accentGlow = useMemo(() => glow(accent), [accent])
  const label = data.speaker_label || `Person ${data.speaker_id}`

  const loud = data.loudness ?? 0.5
  const fontSize = c.fontSize * (0.92 + 0.22 * Math.min(1, Math.max(0, loud)))

  const { display, stableLen } = captionDisplay(data.text, data.pending, c.maxChars)
  const colorRanges = useMemo(
    () => ({
      0: new THREE.Color(theme.color.text).getHex(),
      [stableLen]: new THREE.Color(theme.color.textPending).getHex(),
    }),
    [stableLen],
  )

  const { w, h } = layout
  const front = c.depth / 2 + 1
  const left = -w / 2 + c.pad
  const right = w / 2 - c.pad
  const headerY = h / 2 - c.pad - c.headerHeight / 2 + 4
  const bodyTop = h / 2 - c.pad - c.headerHeight

  // Snap into place on first render instead of flying in from the origin.
  useLayoutEffect(() => {
    group.current.position.set(layout.x, layout.y, 0)
    group.current.scale.setScalar(reducedMotion ? 1 : 0.6)
  }, [])

  useFrame((_, dt) => {
    const g = group.current
    const follow = reducedMotion ? 0.001 : theme.motion.follow
    easing.damp3(g.position, [layout.x, layout.y, 0], follow, dt)
    const targetScale = leaving ? 0.001 : 1
    const s = { v: g.scale.x }
    easing.damp(s, 'v', targetScale, reducedMotion ? 0.001 : theme.motion.enter, dt)
    g.scale.setScalar(s.v)
  })

  const tail = useMemo(() => {
    if (layout.tailX === null) return null
    const tw = c.tailWidth
    const th = c.tailHeight
    const edgeY = layout.tailDir === -1 ? -h / 2 + 2 : h / 2 - 2
    const tipY = layout.tailDir === -1 ? -h / 2 - th : h / 2 + th
    const shape = new THREE.Shape()
    shape.moveTo(layout.tailX - tw / 2, edgeY)
    shape.lineTo(layout.tailX + tw / 2, edgeY)
    shape.lineTo(layout.tailX, tipY)
    shape.closePath()
    return new THREE.ShapeGeometry(shape)
  }, [layout.tailX, layout.tailDir, h, c.tailWidth, c.tailHeight])

  const radius = Math.min(c.radius, w / 2 - 1, h / 2 - 1)

  return (
    <group ref={group}>
      {/* The glass box itself */}
      <RoundedBox args={[w, h, c.depth]} radius={radius} smoothness={5}>
        {lite ? (
          <meshStandardMaterial color={theme.color.glassTint} transparent opacity={0.82} roughness={0.4} />
        ) : (
        <MeshTransmissionMaterial
          transmissionSampler
          transmission={1}
          color={theme.color.glassTint}
          thickness={theme.glass.thickness}
          roughness={theme.glass.roughness}
          ior={theme.glass.ior}
          chromaticAberration={theme.glass.chromaticAberration}
          anisotropicBlur={theme.glass.anisotropicBlur}
          distortion={theme.glass.distortion}
          distortionScale={theme.glass.distortionScale}
          temporalDistortion={0}
          samples={theme.glass.samples}
          backside={theme.glass.backside}
        />
        )}
      </RoundedBox>

      {tail && (
        <mesh geometry={tail} position={[0, 0, 0]}>
          <meshBasicMaterial color={accentGlow} toneMapped={false} />
        </mesh>
      )}

      {/* Header: speaker dot + name, tone chip on the right */}
      <mesh position={[left + 6, headerY, front]}>
        <circleGeometry args={[6, 24]} />
        <meshBasicMaterial color={accentGlow} toneMapped={false} />
      </mesh>
      <Text
        font={theme.font.bold}
        fontSize={c.labelSize}
        color={accent}
        anchorX="left"
        anchorY="middle"
        position={[left + 20, headerY + 1, front]}
      >
        {label}
      </Text>
      {data.tone && (
        <Suspense fallback={null}>
          <ToneChip
            tone={data.tone}
            confidence={data.tone_confidence ?? 1}
            right={right}
            y={headerY}
            z={front}
            color={theme.color.textPending}
          />
        </Suspense>
      )}

      {/* Caption text: final words bright, provisional words dimmer */}
      <Text
        font={theme.font.regular}
        fontSize={fontSize}
        lineHeight={c.lineHeight}
        maxWidth={c.maxTextWidth}
        color={theme.color.text}
        // troika-three-text prop; drei forwards it but its types omit it
        {...({ colorRanges } as object)}
        anchorX="left"
        anchorY="top"
        position={[left, bodyTop, front]}
        onSync={(mesh: { textRenderInfo?: { blockBounds: number[] } }) => {
          const b = mesh.textRenderInfo?.blockBounds
          if (b) onMeasure(data.speaker_id, b[2] - b[0], b[3] - b[1])
        }}
      >
        {display || ' '}
      </Text>

      {/* Off-screen speaker: chevron pointing the way to turn */}
      {layout.edge && (
        <Text
          font={theme.font.bold}
          fontSize={64}
          color={accentGlow}
          anchorX="center"
          anchorY="middle"
          position={[layout.edge === 'left' ? -w / 2 - 30 : w / 2 + 30, 4, front]}
          material-toneMapped={false}
        >
          {layout.edge === 'left' ? '‹' : '›'}
        </Text>
      )}
    </group>
  )
}
