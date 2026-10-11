import { Environment, Lightformer } from '@react-three/drei'
import { Canvas, useThree } from '@react-three/fiber'
import { Bloom, EffectComposer } from '@react-three/postprocessing'
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { CaptionLayer } from './components/CaptionLayer'
import { DebugFaces } from './components/DebugFaces'
import { VideoPlane } from './components/VideoPlane'
import { fitVideo } from './lib/geometry'
import { stream } from './stream'
import { lite, theme } from './theme'

function Scene({ debug }: { debug: boolean }) {
  const size = useThree((s) => s.size)
  const meta = useSyncExternalStore(stream.subscribeMeta, stream.getMeta)
  const videoW = meta?.width ?? 1280
  const videoH = meta?.height ?? 720
  const fit = useMemo(
    () => fitVideo(videoW, videoH, size.width, size.height),
    [videoW, videoH, size.width, size.height],
  )

  return (
    <>
      {/* Soft studio light so the glass edges catch highlights. No network fetch. */}
      <Environment resolution={256}>
        <Lightformer form="rect" intensity={2.2} position={[0, 5, -4]} scale={[10, 2, 1]} />
        <Lightformer form="rect" intensity={1.2} position={[-6, 1, 2]} scale={[2, 6, 1]} />
        <Lightformer form="rect" intensity={1.2} position={[6, -1, 2]} scale={[2, 6, 1]} />
      </Environment>

      <group scale={fit.scale}>
        <VideoPlane width={videoW} height={videoH} />
      </group>
      {debug && <DebugFaces fit={fit} />}
      <CaptionLayer fit={fit} />

      {!lite && (
      <EffectComposer multisampling={4}>
        <Bloom
          mipmapBlur
          intensity={theme.bloom.intensity}
          luminanceThreshold={theme.bloom.threshold}
          luminanceSmoothing={theme.bloom.smoothing}
        />
      </EffectComposer>
      )}
    </>
  )
}

function StatusOverlay() {
  const status = useSyncExternalStore(stream.subscribeStatus, stream.getStatus)
  const meta = useSyncExternalStore(stream.subscribeMeta, stream.getMeta)
  if (status === 'live' && meta) return null
  return (
    <div className="status" role="status">
      <p className="status-title">
        {status === 'live' ? 'Connected. Waiting for the first frame.' : 'No video yet.'}
      </p>
      <p className="status-body">
        Start the mock server with <code>python server/mock_server.py</code>.
        <br />
        Looking for it at <code>{stream.url}</code>
      </p>
    </div>
  )
}

export default function App() {
  const [debug, setDebug] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'd' || e.key === 'D') setDebug((d) => !d)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <>
      <Canvas
        orthographic
        flat
        dpr={[1, 2]}
        camera={{ position: [0, 0, 1000], zoom: 1, near: 1, far: 4000 }}
        gl={{ antialias: true, powerPreference: 'high-performance' }}
      >
        <color attach="background" args={['#05070A']} />
        <Scene debug={debug} />
      </Canvas>
      <StatusOverlay />
    </>
  )
}
