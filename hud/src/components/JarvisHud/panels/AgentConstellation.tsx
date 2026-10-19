import type { AgentActivity, AgentLiveState } from '../hooks/useAgentActivity';

/**
 * AgentConstellation — the JAM star pattern, drawn literally.
 *
 * Every subagent declared in agents.yaml is a satellite node on an arc
 * beneath the lens, spoked to the iris. JARVIS is the hub; the engines are
 * the spokes. A node lights and its spoke carries energy outward while one
 * of that agent's tools is running, flashes red on failure, and sits dim
 * with a dashed ring when the registry reports it never loaded (missing
 * key, disabled, crashed on start).
 *
 * Stage geometry (SVG units, viewBox -700..700 × -394..394): the lens
 * reaches r≈215; the arc sits at r=272 so nodes land between the Spotify
 * panel (left, ends x≈-142) and the Chat panel (right, starts x≈+180), and
 * above the wordmark at y=345.
 */

const HUB_R = 222;      // where spokes leave the lens
const ARC_R = 272;      // node ring
const NODE_R = 10;
const MAX_SPREAD = 64;  // degrees across the whole arc
const MAX_STEP = 16;    // degrees between neighbours

const COLORS: Record<AgentLiveState, { ring: string; dot: string; spoke: string; text: string }> = {
  offline: { ring: '#005566', dot: '#003844', spoke: '#003844', text: '#3a6b76' },
  idle:    { ring: '#4dd0e1', dot: '#0096b8', spoke: '#004d5c', text: '#80deea' },
  active:  { ring: '#00e5ff', dot: '#e0f7fa', spoke: '#00e5ff', text: '#e0f7fa' },
  error:   { ring: '#ff5252', dot: '#ff8a80', spoke: '#ff5252', text: '#ff8a80' },
};

function polar(deg: number, r: number): [number, number] {
  const rad = (deg * Math.PI) / 180;
  return [r * Math.cos(rad), r * Math.sin(rad)];
}

function arcPath(fromDeg: number, toDeg: number, r: number): string {
  const [x1, y1] = polar(fromDeg, r);
  const [x2, y2] = polar(toDeg, r);
  return `M ${x1.toFixed(1)} ${y1.toFixed(1)} A ${r} ${r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`;
}

function statusCaption(a: AgentActivity): string {
  if (a.state === 'offline') {
    const r = a.info.reason ?? '';
    if (a.info.status === 'disabled') return 'PARKED';
    if (/missing env/i.test(r)) return 'NO KEY';
    if (a.info.status === 'failed') return 'FAILED';
    return 'OFFLINE';
  }
  if (a.currentTool) return a.currentTool;
  if (a.errors && a.state === 'error') return 'ERROR';
  if (a.calls) return `${a.calls} CALL${a.calls === 1 ? '' : 'S'}`;
  return 'READY';
}

export function AgentConstellation({ agents }: { agents: AgentActivity[] }) {
  const n = agents.length;
  if (n === 0) return null;

  const step = n === 1 ? 0 : Math.min(MAX_STEP, MAX_SPREAD / (n - 1));
  const spread = step * (n - 1);
  const start = 90 - spread / 2;
  const anyActive = agents.some((a) => a.state === 'active');

  return (
    <g className="jhud-agents" data-active={anyActive ? '1' : '0'}>
      <defs>
        <filter id="jhud-agent-glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.2" />
        </filter>
      </defs>

      {/* faint reference arc, same vocabulary as the lens' guide circles */}
      <path
        d={arcPath(start - 9, start + spread + 9, ARC_R)}
        fill="none"
        stroke="#4dd0e1"
        strokeWidth="0.5"
        strokeDasharray="2 5"
        opacity="0.35"
      />

      {agents.map((a, i) => {
        // Angles grow clockwise (SVG y is down), so walk them backwards to
        // keep agents.yaml declaration order reading left → right.
        const deg = start + (n - 1 - i) * step;
        const [hx, hy] = polar(deg, HUB_R);
        const [nx, ny] = polar(deg, ARC_R - NODE_R - 2);
        const [cx, cy] = polar(deg, ARC_R);
        const c = COLORS[a.state];
        const caption = statusCaption(a);
        const titleLines = [
          `${a.info.name} — ${a.info.transport} — ${a.info.status}` +
            (a.info.reason ? ` (${a.info.reason})` : ''),
          a.info.description,
          a.info.tools.length ? `tools: ${a.info.tools.join(', ')}` : 'no tools registered',
        ];

        return (
          <g key={a.info.name} className={`jhud-agent jhud-agent-${a.state}`}>
            <title>{titleLines.join('\n')}</title>

            {/* spoke: hub → node */}
            <line
              className="jhud-agent-spoke"
              x1={hx} y1={hy} x2={nx} y2={ny}
              stroke={c.spoke}
              strokeWidth={a.state === 'active' ? 1.4 : 0.8}
              strokeDasharray={a.state === 'offline' ? '2 4' : a.state === 'active' ? '6 5' : undefined}
              strokeLinecap="round"
              opacity={a.state === 'offline' ? 0.5 : 0.9}
            />
            {a.state === 'active' && (
              <line
                x1={hx} y1={hy} x2={nx} y2={ny}
                stroke={c.spoke}
                strokeWidth={3}
                opacity={0.45}
                filter="url(#jhud-agent-glow)"
              />
            )}

            {/* node */}
            <g transform={`translate(${cx.toFixed(1)}, ${cy.toFixed(1)})`}>
              {a.state !== 'offline' && (
                <circle
                  className="jhud-agent-halo"
                  r={NODE_R + 5}
                  fill={c.ring}
                  opacity={a.state === 'active' ? 0.22 : a.state === 'error' ? 0.2 : 0.06}
                  filter="url(#jhud-agent-glow)"
                />
              )}
              <circle
                className="jhud-agent-ring"
                r={NODE_R}
                fill="#000a14"
                fillOpacity={0.85}
                stroke={c.ring}
                strokeWidth={a.state === 'active' ? 1.8 : 1.2}
                strokeDasharray={a.state === 'offline' ? '2.5 2.5' : undefined}
                opacity={a.state === 'offline' ? 0.6 : 1}
              />
              {a.state !== 'offline' && (
                <circle className="jhud-agent-dot" r={3.4} fill={c.dot} />
              )}
              {/* transport glyph: ring tick for mcp-stdio, none for in-process adapters */}
              {a.info.transport === 'mcp-stdio' && (
                <circle r={NODE_R + 2.5} fill="none" stroke={c.ring}
                  strokeWidth="0.6" strokeDasharray="1.5 3" opacity={0.7} />
              )}

              {/* labels */}
              <text
                y={NODE_R + 14}
                textAnchor="middle"
                fontSize={8.5}
                fill={c.text}
                fontFamily="ui-monospace"
                letterSpacing={1.6}
                style={{ textTransform: 'uppercase' }}
              >
                {a.info.name.toUpperCase()}
              </text>
              <text
                className="jhud-agent-caption"
                y={NODE_R + 25}
                textAnchor="middle"
                fontSize={7}
                fill={a.state === 'active' ? '#00e5ff' : c.text}
                fontFamily="ui-monospace"
                letterSpacing={1}
                opacity={a.state === 'offline' ? 0.7 : 0.85}
              >
                {caption.length > 16 ? caption.slice(0, 15) + '…' : caption}
              </text>
            </g>
          </g>
        );
      })}
    </g>
  );
}
