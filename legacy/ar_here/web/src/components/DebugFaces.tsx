import { Line, Text } from '@react-three/drei'
import { useSyncExternalStore } from 'react'
import { toScreen, type VideoFit } from '../lib/geometry'
import { stream } from '../stream'
import { speakerColor, theme } from '../theme'

/** Press D to toggle. Draws tracked face boxes with their id and bearing. */
export function DebugFaces({ fit }: { fit: VideoFit }) {
  const meta = useSyncExternalStore(stream.subscribeMeta, stream.getMeta)
  if (!meta) return null
  return (
    <>
      {meta.faces.map((f) => {
        const [x, y, w, h] = f.bbox
        const pts = [
          toScreen(fit, x, y),
          toScreen(fit, x + w, y),
          toScreen(fit, x + w, y + h),
          toScreen(fit, x, y + h),
          toScreen(fit, x, y),
        ].map(([px, py]) => [px, py, 5] as [number, number, number])
        const [lx, ly] = toScreen(fit, x, y + h)
        return (
          <group key={f.id}>
            <Line points={pts} color={speakerColor(f.id)} lineWidth={2} />
            <Text
              font={theme.font.bold}
              fontSize={16}
              color={speakerColor(f.id)}
              anchorX="left"
              anchorY="top"
              position={[lx + 4, ly - 4, 5]}
            >
              {`#${f.id}${f.bearing != null ? `  ${f.bearing.toFixed(1)}°` : ''}`}
            </Text>
          </group>
        )
      })}
    </>
  )
}
