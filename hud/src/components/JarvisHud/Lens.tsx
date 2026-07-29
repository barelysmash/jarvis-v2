import type { RefObject } from 'react';

interface LensProps {
  /** Ref from useShutter — attached to the SMIL blade-fire <animate> element. */
  bladeFireRef: RefObject<SVGAnimateElement | null>;
  /** 0..1 audio level. Bumps the lens body scale via CSS custom property `--jhud-audio`. */
  audioLevel?: number;
}

/**
 * Lens — the amorphous camera-lens centerpiece.
 *
 * Layered (outer → inner):
 *   1. ambient halo + faint reference circles
 *   2. outer rotating tick ring
 *   3. counter-rotating segmented ring
 *   4. fast rotating accent arc
 *   5. amorphous iris (turbulence-displaced circles, breathing)
 *   6. secondary amorphous detail (counter-rotating, faster)
 *   7. orbit BACK arcs at +8°, -22°, +38° (rendered before lens body)
 *   8. infalling particles spiraling toward the pupil
 *   9. lens body — barrel, glass, deep pupil, 7 iris blades, specular highlights
 *  10. orbit FRONT arcs (rendered after lens body → 3D occlusion illusion)
 *  11. cardinal tick markers
 */
export function Lens({ bladeFireRef, audioLevel = 0 }: LensProps) {
  // The lens body scales subtly with the audio level (server-driven, 0..1).
  // 1.0 + 0..0.12 multiplier on the dilate keyframe.
  const audioScale = 1 + audioLevel * 0.12;
  return (
    <g className="jhud-lens">
      <defs>
        {/* turbulence + displacement filters drive the "amorphous" feel */}
        <filter id="jhud-amorphous" x="-30%" y="-30%" width="160%" height="160%">
          <feTurbulence type="fractalNoise" baseFrequency="0.012" numOctaves={2} seed={4} result="noise">
            <animate attributeName="baseFrequency"
              values="0.010;0.022;0.013;0.018;0.010" dur="14s" repeatCount="indefinite"/>
          </feTurbulence>
          <feDisplacementMap in="SourceGraphic" in2="noise" scale={16}
            xChannelSelector="R" yChannelSelector="G"/>
        </filter>
        <filter id="jhud-amorphous2" x="-30%" y="-30%" width="160%" height="160%">
          <feTurbulence type="fractalNoise" baseFrequency="0.022" numOctaves={2} seed={11} result="noise2">
            <animate attributeName="baseFrequency"
              values="0.018;0.030;0.018" dur="8s" repeatCount="indefinite"/>
          </feTurbulence>
          <feDisplacementMap in="SourceGraphic" in2="noise2" scale={8}/>
        </filter>
        <filter id="jhud-glow"><feGaussianBlur stdDeviation="2.4"/></filter>
        <filter id="jhud-orbitglow"><feGaussianBlur stdDeviation="1.6"/></filter>
        <filter id="jhud-partglow"><feGaussianBlur stdDeviation="1.2"/></filter>

        <radialGradient id="jhud-halo">
          <stop offset="0%"   stopColor="#00e5ff" stopOpacity="0"/>
          <stop offset="55%"  stopColor="#00e5ff" stopOpacity="0.06"/>
          <stop offset="80%"  stopColor="#00e5ff" stopOpacity="0.15"/>
          <stop offset="100%" stopColor="#00e5ff" stopOpacity="0"/>
        </radialGradient>
        <radialGradient id="jhud-band">
          <stop offset="0%"   stopColor="#00e5ff" stopOpacity="0"/>
          <stop offset="75%"  stopColor="#00e5ff" stopOpacity="0"/>
          <stop offset="85%"  stopColor="#4dd0e1" stopOpacity="0.30"/>
          <stop offset="95%"  stopColor="#00b8d4" stopOpacity="0.12"/>
          <stop offset="100%" stopColor="#00b8d4" stopOpacity="0"/>
        </radialGradient>
        <radialGradient id="jhud-lens-glass" cx="32%" cy="28%" r="78%">
          <stop offset="0%"   stopColor="#e0f7fa" stopOpacity="0.85"/>
          <stop offset="20%"  stopColor="#4dd0e1" stopOpacity="0.4"/>
          <stop offset="55%"  stopColor="#003844" stopOpacity="0.78"/>
          <stop offset="100%" stopColor="#000a14" stopOpacity="1"/>
        </radialGradient>
        <radialGradient id="jhud-pupil" cx="50%" cy="50%" r="55%">
          <stop offset="0%"   stopColor="#001824"/>
          <stop offset="65%"  stopColor="#000810"/>
          <stop offset="100%" stopColor="#000204"/>
        </radialGradient>
        <linearGradient id="jhud-blade" x1="0%" x2="100%" y1="0%" y2="0%">
          <stop offset="0%"   stopColor="#00161e"/>
          <stop offset="60%"  stopColor="#001f2a"/>
          <stop offset="100%" stopColor="#00343f"/>
        </linearGradient>

        {/* one shared blade — referenced by <use> seven times at 360/7° spacing */}
        <path id="jhud-blade-path"
          fill="url(#jhud-blade)" stroke="#00566b" strokeWidth="0.5" strokeLinejoin="round">
          <animate attributeName="d"
            values="M 44 -19 C 40 -17 28 -9 18 -2.5 L 18 2.5 C 28 9 40 17 44 19 Z;
                    M 44 -19 C 38 -16 24 -8 14 -2 L 14 2 C 24 8 38 16 44 19 Z;
                    M 44 -19 C 40 -17 28 -9 18 -2.5 L 18 2.5 C 28 9 40 17 44 19 Z"
            keyTimes="0; 0.5; 1" dur="6s" repeatCount="indefinite"/>
          <animate ref={bladeFireRef as React.RefObject<SVGAnimateElement>}
            attributeName="d"
            values="M 44 -19 C 40 -17 28 -9 18 -2.5 L 18 2.5 C 28 9 40 17 44 19 Z;
                    M 44 -19 C 30 -13 12 -5 3 -1 L 3 1 C 12 5 30 13 44 19 Z;
                    M 44 -19 C 30 -13 12 -5 3 -1 L 3 1 C 12 5 30 13 44 19 Z;
                    M 44 -19 C 40 -17 28 -9 18 -2.5 L 18 2.5 C 28 9 40 17 44 19 Z"
            keyTimes="0; 0.22; 0.55; 1" dur="0.9s" begin="indefinite" fill="remove"/>
        </path>
      </defs>

      {/* ambient halo */}
      <circle r={200} fill="url(#jhud-halo)"/>

      {/* faint concentric reference rings */}
      <g stroke="#005566" fill="none" strokeWidth="0.4" opacity="0.5">
        <circle r={210}/><circle r={170}/><circle r={130}/><circle r={75}/>
      </g>

      {/* outer tick ring (slow) */}
      <g className="rotg r-slow">
        <circle r={215} fill="none" stroke="#0096b8" strokeWidth="6"
          strokeDasharray="1.5 21.5" opacity="0.55"/>
        <circle r={207} fill="none" stroke="#4dd0e1" strokeWidth="0.6"
          opacity="0.4" strokeDasharray="0.5 14"/>
      </g>

      {/* mid ring (counter, with bright accent arc) */}
      <g className="rotg r-med-cc">
        <circle r={195} fill="none" stroke="#00b8d4" strokeWidth="1"
          opacity="0.7" strokeDasharray="8 4 20 6 4 6 60 18"/>
        <circle r={184} fill="none" stroke="#4dd0e1" strokeWidth="0.5"
          opacity="0.45" strokeDasharray="3 9"/>
        <path d="M -180 -50 A 187 187 0 0 1 -50 -180"
          stroke="#b2ebf2" strokeWidth="2.5" fill="none"
          filter="url(#jhud-glow)" opacity="0.85"/>
      </g>

      {/* bright fast accent */}
      <g className="rotg r-fast">
        <path d="M 170 0 A 170 170 0 0 1 90 145"
          stroke="#80deea" strokeWidth="1.5" fill="none" opacity="0.85"/>
        <path d="M 170 0 A 170 170 0 0 1 130 110"
          stroke="#e0f7fa" strokeWidth="2.5" fill="none"
          filter="url(#jhud-glow)" opacity="0.95"/>
        <circle cx={170} cy={0} r={3} fill="#ffffff" filter="url(#jhud-glow)"/>
      </g>

      {/* amorphous iris */}
      <g className="breathe" filter="url(#jhud-amorphous)" opacity="0.65">
        <circle r={148} fill="none" stroke="#00e5ff" strokeWidth="1.2" opacity="0.55"/>
        <circle r={138} fill="url(#jhud-band)"/>
        <circle r={118} fill="none" stroke="#4dd0e1" strokeWidth="1.5" opacity="0.45"/>
      </g>

      {/* secondary amorphous detail (faster, counter) */}
      <g className="rotg r-vfast-cc" filter="url(#jhud-amorphous2)" opacity="0.55">
        <circle r={105} fill="none" stroke="#4dd0e1" strokeWidth="0.5"
          opacity="0.6" strokeDasharray="2 8 4 14"/>
        <circle r={95}  fill="none" stroke="#80deea" strokeWidth="0.5"
          opacity="0.5" strokeDasharray="1 5"/>
      </g>

      {/* ===== ORBIT BACK ARCS ===== */}
      <g transform="rotate(8)">
        <path className="orbit-arc" fill="none" stroke="#4dd0e1" strokeWidth="2"
          opacity="0.85" filter="url(#jhud-orbitglow)" strokeLinecap="round">
          <animate attributeName="d"
            values="M -168 0 A 168 4 0 0 0 168 0;
                    M -168 0 A 168 132 0 0 0 168 0;
                    M -168 0 A 168 4 0 0 0 168 0"
            dur="7.5s" repeatCount="indefinite"/>
        </path>
      </g>
      <g transform="rotate(-22)">
        <path className="orbit-arc" fill="none" stroke="#e0f7fa" strokeWidth="1"
          opacity="0.9" strokeLinecap="round">
          <animate attributeName="d"
            values="M -142 0 A 142 8 0 0 0 142 0;
                    M -142 0 A 142 108 0 0 0 142 0;
                    M -142 0 A 142 8 0 0 0 142 0"
            dur="5.5s" begin="-1.6s" repeatCount="indefinite"/>
        </path>
      </g>
      <g transform="rotate(38)">
        <path className="orbit-arc" fill="none" stroke="#80deea" strokeWidth="1.4"
          opacity="0.75" strokeLinecap="round">
          <animate attributeName="d"
            values="M -118 0 A 118 3 0 0 0 118 0;
                    M -118 0 A 118 82 0 0 0 118 0;
                    M -118 0 A 118 3 0 0 0 118 0"
            dur="4.2s" begin="-0.9s" repeatCount="indefinite"/>
        </path>
      </g>

      {/* infalling particles */}
      <Particles/>

      {/* ===== LENS BODY =====
          Outer <g> handles the audio-reactive scale; inner <g> runs the
          dilate keyframe. SVG composes them, so they multiply cleanly. */}
      <g style={{ transform: `scale(${audioScale})`, transformBox: 'fill-box',
                  transformOrigin: 'center' }}>
      <g className="dilate">
        <circle r={46} fill="#000810" stroke="#005566" strokeWidth="1" opacity="0.95"/>
        <circle r={42} fill="none" stroke="#00b8d4" strokeWidth="0.4" opacity="0.7"/>
        <circle r={40} fill="url(#jhud-lens-glass)"/>
        <circle r={20} fill="url(#jhud-pupil)"/>

        {/* 7 iris blades — one shared path instanced via <use> */}
        <g opacity="0.96">
          <use href="#jhud-blade-path" transform="rotate(0)"/>
          <use href="#jhud-blade-path" transform="rotate(51.43)"/>
          <use href="#jhud-blade-path" transform="rotate(102.86)"/>
          <use href="#jhud-blade-path" transform="rotate(154.29)"/>
          <use href="#jhud-blade-path" transform="rotate(205.71)"/>
          <use href="#jhud-blade-path" transform="rotate(257.14)"/>
          <use href="#jhud-blade-path" transform="rotate(308.57)"/>
        </g>

        {/* specular highlights on the glass surface */}
        <path d="M -28 -16 A 30 30 0 0 1 -8 -32"
          stroke="#e0f7fa" strokeWidth="1.5" fill="none" opacity="0.6" strokeLinecap="round"/>
        <path d="M -22 -22 A 24 24 0 0 1 -2 -28"
          stroke="#ffffff" strokeWidth="0.7" fill="none" opacity="0.85" strokeLinecap="round"/>
        <ellipse cx={-3} cy={-3} rx={2.5} ry={1.5} fill="#ffffff" opacity="0.5"/>
      </g>
      </g>

      {/* ===== ORBIT FRONT ARCS (drawn after lens body) ===== */}
      <g transform="rotate(8)">
        <path className="orbit-arc" fill="none" stroke="#4dd0e1" strokeWidth="2"
          opacity="0.95" filter="url(#jhud-orbitglow)" strokeLinecap="round">
          <animate attributeName="d"
            values="M -168 0 A 168 4 0 0 1 168 0;
                    M -168 0 A 168 132 0 0 1 168 0;
                    M -168 0 A 168 4 0 0 1 168 0"
            dur="7.5s" repeatCount="indefinite"/>
        </path>
      </g>
      <g transform="rotate(-22)">
        <path className="orbit-arc" fill="none" stroke="#e0f7fa" strokeWidth="1"
          opacity="1" strokeLinecap="round">
          <animate attributeName="d"
            values="M -142 0 A 142 8 0 0 1 142 0;
                    M -142 0 A 142 108 0 0 1 142 0;
                    M -142 0 A 142 8 0 0 1 142 0"
            dur="5.5s" begin="-1.6s" repeatCount="indefinite"/>
        </path>
      </g>
      <g transform="rotate(38)">
        <path className="orbit-arc" fill="none" stroke="#80deea" strokeWidth="1.4"
          opacity="0.85" strokeLinecap="round">
          <animate attributeName="d"
            values="M -118 0 A 118 3 0 0 1 118 0;
                    M -118 0 A 118 82 0 0 1 118 0;
                    M -118 0 A 118 3 0 0 1 118 0"
            dur="4.2s" begin="-0.9s" repeatCount="indefinite"/>
        </path>
      </g>

      {/* cardinal tick markers (top, right, bottom, left) */}
      <g stroke="#4dd0e1" strokeWidth="1" opacity="0.85" filter="url(#jhud-glow)">
        <line x1={0}    y1={-218} x2={0}    y2={-205}/>
        <line x1={218}  y1={0}    x2={205}  y2={0}/>
        <line x1={0}    y1={218}  x2={0}    y2={205}/>
        <line x1={-218} y1={0}    x2={-205} y2={0}/>
      </g>
    </g>
  );
}

/** Six dots spiraling inward toward the pupil — SMIL-driven independently. */
function Particles() {
  // [startX, startY, dur, beginOffset, fill, startR]
  const specs: [number, number, number, number, string, number][] = [
    [  78,  34, 3.4,  0.0, '#80deea', 2.3],
    [ -60,  62, 3.0, -1.2, '#e0f7fa', 2.0],
    [ -82, -30, 3.8, -2.4, '#4dd0e1', 2.4],
    [  40, -75, 2.6, -0.6, '#80deea', 1.8],
    [ -50, -58, 3.3, -1.9, '#e0f7fa', 2.0],
    [  70, -45, 3.6, -2.8, '#80deea', 2.1],
  ];
  return (
    <g filter="url(#jhud-partglow)">
      {specs.map(([sx, sy, dur, begin, fill, startR], i) => (
        <circle key={i} r={2} fill={fill}>
          <animate attributeName="cx" values={`${sx}; 0`}
            dur={`${dur}s`} begin={`${begin}s`} repeatCount="indefinite"/>
          <animate attributeName="cy" values={`${sy}; 0`}
            dur={`${dur}s`} begin={`${begin}s`} repeatCount="indefinite"/>
          <animate attributeName="opacity" values="0; 0.9; 0"
            dur={`${dur}s`} begin={`${begin}s`} repeatCount="indefinite"/>
          <animate attributeName="r" values={`${startR}; 0.4`}
            dur={`${dur}s`} begin={`${begin}s`} repeatCount="indefinite"/>
        </circle>
      ))}
    </g>
  );
}
