import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

interface OrbProps {
  state: "idle" | "listening" | "thinking" | "speaking";
  audioLevel: number;
}

const stateColors: Record<
  OrbProps["state"],
  { primary: string; secondary: string; intensity: number }
> = {
  idle: { primary: "#0ea5e9", secondary: "#1e3a8a", intensity: 0.5 },
  listening: { primary: "#06b6d4", secondary: "#0891b2", intensity: 1.5 },
  thinking: { primary: "#a855f7", secondary: "#6b21a8", intensity: 1.2 },
  speaking: { primary: "#f59e0b", secondary: "#dc2626", intensity: 2.0 },
};

function OrbCore({ state, audioLevel }: OrbProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const ringsRef = useRef<THREE.Group>(null);
  const colors = stateColors[state];

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: {
          time: { value: 0 },
          audioLevel: { value: 0 },
          colorA: { value: new THREE.Color(colors.primary) },
          colorB: { value: new THREE.Color(colors.secondary) },
          intensity: { value: colors.intensity },
        },
        vertexShader: `
          uniform float time;
          uniform float audioLevel;
          varying vec3 vNormal;
          varying vec3 vPosition;

          vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
          vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
          vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
          vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

          float snoise(vec3 v) {
            const vec2 C = vec2(1.0/6.0, 1.0/3.0);
            const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
            vec3 i  = floor(v + dot(v, C.yyy));
            vec3 x0 = v - i + dot(i, C.xxx);
            vec3 g = step(x0.yzx, x0.xyz);
            vec3 l = 1.0 - g;
            vec3 i1 = min(g.xyz, l.zxy);
            vec3 i2 = max(g.xyz, l.zxy);
            vec3 x1 = x0 - i1 + C.xxx;
            vec3 x2 = x0 - i2 + C.yyy;
            vec3 x3 = x0 - D.yyy;
            i = mod289(i);
            vec4 p = permute(permute(permute(
              i.z + vec4(0.0, i1.z, i2.z, 1.0))
              + i.y + vec4(0.0, i1.y, i2.y, 1.0))
              + i.x + vec4(0.0, i1.x, i2.x, 1.0));
            float n_ = 0.142857142857;
            vec3 ns = n_ * D.wyz - D.xzx;
            vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
            vec4 x_ = floor(j * ns.z);
            vec4 y_ = floor(j - 7.0 * x_);
            vec4 x = x_ * ns.x + ns.yyyy;
            vec4 y = y_ * ns.x + ns.yyyy;
            vec4 h = 1.0 - abs(x) - abs(y);
            vec4 b0 = vec4(x.xy, y.xy);
            vec4 b1 = vec4(x.zw, y.zw);
            vec4 s0 = floor(b0) * 2.0 + 1.0;
            vec4 s1 = floor(b1) * 2.0 + 1.0;
            vec4 sh = -step(h, vec4(0.0));
            vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
            vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
            vec3 p0 = vec3(a0.xy, h.x);
            vec3 p1 = vec3(a0.zw, h.y);
            vec3 p2 = vec3(a1.xy, h.z);
            vec3 p3 = vec3(a1.zw, h.w);
            vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
            p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
            vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
            m = m * m;
            return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
          }

          void main() {
            vNormal = normal;
            float noise = snoise(position * 1.5 + time * 0.3) * 0.15;
            float displacement = noise + audioLevel * 0.3;
            vec3 newPos = position + normal * displacement;
            vPosition = newPos;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(newPos, 1.0);
          }
        `,
        fragmentShader: `
          uniform vec3 colorA;
          uniform vec3 colorB;
          uniform float intensity;
          uniform float time;
          varying vec3 vNormal;
          varying vec3 vPosition;

          void main() {
            float fresnel = pow(1.0 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.0);
            vec3 color = mix(colorB, colorA, fresnel);
            color += colorA * fresnel * intensity;
            float pulse = sin(time * 2.0) * 0.1 + 0.9;
            gl_FragColor = vec4(color * pulse, 0.95);
          }
        `,
        transparent: true,
      }),
    [colors.primary, colors.secondary, colors.intensity]
  );

  useFrame((_, delta) => {
    if (meshRef.current) {
      meshRef.current.rotation.y += delta * 0.2;
      meshRef.current.rotation.x += delta * 0.05;
      material.uniforms.time.value += delta;
      material.uniforms.audioLevel.value = THREE.MathUtils.lerp(
        material.uniforms.audioLevel.value,
        audioLevel,
        0.1
      );
    }
    if (ringsRef.current) {
      ringsRef.current.rotation.z += delta * 0.3;
    }
  });

  return (
    <group>
      <mesh ref={meshRef} material={material}>
        <icosahedronGeometry args={[1.2, 32]} />
      </mesh>

      <group ref={ringsRef}>
        {[1.6, 1.9, 2.2].map((r, i) => (
          <mesh key={i} rotation={[Math.PI / 2 + i * 0.3, 0, 0]}>
            <torusGeometry args={[r, 0.005, 16, 100]} />
            <meshBasicMaterial
              color={colors.primary}
              transparent
              opacity={0.3 - i * 0.08}
            />
          </mesh>
        ))}
      </group>

      <pointLight
        color={colors.primary}
        intensity={colors.intensity * 2}
        distance={6}
      />
    </group>
  );
}

export function VoiceOrb({ state, audioLevel }: OrbProps) {
  return (
    <div className="relative w-full h-full">
      <Canvas
        camera={{ position: [0, 0, 4], fov: 50 }}
        gl={{ alpha: true, antialias: true }}
      >
        <ambientLight intensity={0.2} />
        <OrbCore state={state} audioLevel={audioLevel} />
      </Canvas>

      <div className="absolute bottom-8 left-0 right-0 text-center pointer-events-none">
        <div className="text-cyan-400/60 text-xs tracking-[0.3em] uppercase">
          {state}
        </div>
      </div>
    </div>
  );
}
