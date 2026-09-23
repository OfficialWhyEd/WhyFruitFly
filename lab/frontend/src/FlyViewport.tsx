import { OrbitControls } from '@react-three/drei'
import { Canvas, useFrame } from '@react-three/fiber'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'
import { telemetry } from './telemetry'

const dummy = new THREE.Object3D()
const target = new THREE.Vector3()

function FlyBody() {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const previous = useRef<number[][]>([])
  const geometry = useMemo(() => new THREE.SphereGeometry(0.075, 10, 7), [])
  const material = useMemo(
    () => new THREE.MeshStandardMaterial({ color: '#151512', roughness: 0.82, metalness: 0.02 }),
    [],
  )

  useFrame((_, delta) => {
    const positions = telemetry.snapshot?.body_pos_mm
    if (!positions || !mesh.current) return
    if (previous.current.length !== positions.length) previous.current = positions.map((p) => [...p])
    const alpha = 1 - Math.exp(-Math.min(delta, 0.05) * 16)
    positions.forEach((position, index) => {
      const current = previous.current[index]
      current[0] += (position[0] - current[0]) * alpha
      current[1] += (position[1] - current[1]) * alpha
      current[2] += (position[2] - current[2]) * alpha
      target.set(current[0], current[2], -current[1])
      dummy.position.copy(target)
      const isThorax = index < 7
      dummy.scale.setScalar(isThorax ? 1.6 : 1)
      dummy.updateMatrix()
      mesh.current!.setMatrixAt(index, dummy.matrix)
    })
    mesh.current.instanceMatrix.needsUpdate = true
  })

  return <instancedMesh ref={mesh} args={[geometry, material, 69]} frustumCulled={false} />
}

function Ground() {
  return (
    <>
      <gridHelper args={[12, 48, '#9D978A', '#D8D2C6']} position={[0, 0, 0]} />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.005, 0]}>
        <planeGeometry args={[12, 12]} />
        <meshStandardMaterial color="#F8F5ED" roughness={1} />
      </mesh>
    </>
  )
}

export function FlyViewport() {
  return (
    <Canvas
      className="fly-canvas"
      dpr={[1, 1.5]}
      camera={{ fov: 42, near: 0.01, far: 100, position: [4.6, 3.1, 5.2] }}
      gl={{ antialias: true, powerPreference: 'high-performance' }}
      fallback={<p className="canvas-error">WebGL non disponibile su questo dispositivo.</p>}
    >
      <color attach="background" args={['#F0ECE2']} />
      <ambientLight intensity={2.1} />
      <directionalLight position={[4, 7, 3]} intensity={2.8} color="#FFF7E8" />
      <directionalLight position={[-3, 2, -4]} intensity={0.9} color="#D8E0DD" />
      <Ground />
      <FlyBody />
      <OrbitControls
        makeDefault
        target={[0, 1.2, 0]}
        minDistance={1.5}
        maxDistance={12}
        enableDamping
        dampingFactor={0.08}
      />
    </Canvas>
  )
}

