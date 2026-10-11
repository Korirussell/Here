import { Text, useTexture } from '@react-three/drei'
import type { Tone } from '../contract'
import { theme, tones } from '../theme'

useTexture.preload(Object.values(tones).map((t) => t.image))

/**
 * Right-aligned "emoji + word" chip. The word is always shown so the tone
 * never depends on reading an emoji. Low confidence fades the chip.
 */
export function ToneChip({
  tone,
  confidence,
  right,
  y,
  z,
  color,
}: {
  tone: Tone
  confidence: number
  right: number
  y: number
  z: number
  color: string
}) {
  const info = tones[tone]
  const map = useTexture(info.image)
  const size = 30
  const opacity = 0.35 + 0.65 * Math.min(1, Math.max(0, confidence))
  return (
    <group position={[right, y, z]}>
      <mesh position={[-size / 2, 0, 0]}>
        <planeGeometry args={[size, size]} />
        <meshBasicMaterial map={map} transparent opacity={opacity} toneMapped={false} />
      </mesh>
      <Text
        font={theme.font.bold}
        fontSize={theme.caption.labelSize}
        color={color}
        fillOpacity={opacity}
        anchorX="right"
        anchorY="middle"
        position={[-size - 8, 1, 0]}
      >
        {info.label}
      </Text>
    </group>
  )
}
