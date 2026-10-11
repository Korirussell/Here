import { useEffect, useMemo, useState } from 'react'
import * as THREE from 'three'
import { stream } from '../stream'

/**
 * The live camera feed as a texture. Updates imperatively on each frame so
 * the video never waits on React.
 */
export function VideoPlane({ width, height }: { width: number; height: number }) {
  const texture = useMemo(() => {
    const t = new THREE.Texture()
    t.colorSpace = THREE.SRGBColorSpace
    t.flipY = false // bitmaps arrive pre-flipped (see stream.ts)
    t.minFilter = THREE.LinearFilter
    t.generateMipmaps = false
    return t
  }, [])
  const [hasFrame, setHasFrame] = useState(false)

  useEffect(() => {
    const apply = () => {
      if (!stream.bitmap) return
      texture.image = stream.bitmap
      texture.needsUpdate = true
      setHasFrame(true)
    }
    apply()
    const unsubscribe = stream.subscribeFrame(apply)
    return () => {
      unsubscribe()
    }
  }, [texture])

  useEffect(() => () => texture.dispose(), [texture])

  return (
    <mesh visible={hasFrame} renderOrder={-1}>
      <planeGeometry args={[width, height]} />
      <meshBasicMaterial map={texture} toneMapped={false} />
    </mesh>
  )
}
