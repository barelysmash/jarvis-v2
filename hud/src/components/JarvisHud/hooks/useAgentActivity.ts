import { useEffect, useRef, useState } from 'react';
import type { AgentInfo, JarvisState, ToolEvent } from '../../../hooks/useJarvisState';

export type AgentLiveState = 'offline' | 'idle' | 'active' | 'error';

export interface AgentActivity {
  info: AgentInfo;
  /** Derived from registry status + the most recent tool event for this agent. */
  state: AgentLiveState;
  /** Tool currently running (agent prefix stripped), or null. */
  currentTool: string | null;
  /** Most recent tool this agent ran (prefix stripped), running or not. */
  lastTool: string | null;
  /** Terminal (success|error) calls seen since the HUD loaded. */
  calls: number;
  errors: number;
}

/** How long a node stays red after one of its tools fails. Matches the iris. */
const ERROR_HOLD_MS = 4000;

/** `friday_live_scan` → `live_scan`; names without the prefix pass through. */
export function shortToolName(tool: string, agent: string): string {
  const prefix = `${agent}_`;
  return tool.startsWith(prefix) ? tool.slice(prefix.length) : tool;
}

/**
 * useAgentActivity -- one live record per declared subagent.
 *
 * Nothing here is invented: the agent list comes from the server's registry
 * snapshot, and every state transition is backed by a `tool` event that
 * carries an `agent` field (or matches a tool the registry attributed). An
 * agent the registry reports as skipped/disabled/failed is `offline` no
 * matter what -- it has no tools registered, so it can never fire.
 */
export function useAgentActivity(j: JarvisState): AgentActivity[] {
  const { agents, toolEvents } = j;

  // Cumulative counters survive the 20-event buffer window in useJarvisState.
  const seen = useRef<Set<string>>(new Set());
  const counts = useRef<Map<string, { calls: number; errors: number }>>(new Map());
  // Per-agent red-hold expiry (ms epoch); a re-render is scheduled when it lapses.
  const [errorUntil, setErrorUntil] = useState<Record<string, number>>({});

  useEffect(() => {
    const now = Date.now();
    let changed = false;
    const next: Record<string, number> = { ...errorUntil };
    for (const evt of toolEvents) {
      if (!evt.agent || seen.current.has(evt.id)) continue;
      seen.current.add(evt.id);
      if (evt.status === 'running') continue;
      const c = counts.current.get(evt.agent) ?? { calls: 0, errors: 0 };
      c.calls += 1;
      if (evt.status === 'error') {
        c.errors += 1;
        next[evt.agent] = now + ERROR_HOLD_MS;
        changed = true;
      }
      counts.current.set(evt.agent, c);
    }
    if (changed) setErrorUntil(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolEvents]);

  // Clear the earliest pending red-hold when it expires.
  useEffect(() => {
    const pending = Object.values(errorUntil).filter((t) => t > Date.now());
    if (!pending.length) return;
    const wait = Math.max(0, Math.min(...pending) - Date.now()) + 20;
    const t = window.setTimeout(() => {
      const now = Date.now();
      setErrorUntil((cur) => {
        const out: Record<string, number> = {};
        for (const [k, v] of Object.entries(cur)) if (v > now) out[k] = v;
        return out;
      });
    }, wait);
    return () => window.clearTimeout(t);
  }, [errorUntil]);

  const now = Date.now();
  return agents.map((info) => {
    const last = lastEventFor(info.name, toolEvents);
    const running = last?.status === 'running';
    const counter = counts.current.get(info.name) ?? { calls: 0, errors: 0 };

    let state: AgentLiveState;
    if (info.status !== 'loaded') state = 'offline';
    else if ((errorUntil[info.name] ?? 0) > now) state = 'error';
    else if (running) state = 'active';
    else state = 'idle';

    return {
      info,
      state,
      currentTool: running && last ? shortToolName(last.name, info.name) : null,
      lastTool: last ? shortToolName(last.name, info.name) : null,
      calls: counter.calls,
      errors: counter.errors,
    };
  });
}

function lastEventFor(agent: string, events: ToolEvent[]): ToolEvent | null {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].agent === agent) return events[i];
  }
  return null;
}
