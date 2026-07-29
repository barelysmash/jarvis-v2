import { useEffect, useState } from 'react';
import type { JarvisState } from '../../../hooks/useJarvisState';

export type LensState =
  | 'offline'
  | 'error'
  | 'tool'
  | 'thinking'
  | 'speaking'
  | 'idle';

/** Minimum time `thinking` is held, so a fast turn still reads as thinking. */
const THINK_DWELL_MS = 500;
/** How long the iris stays red after a tool reports failure. */
const ERROR_HOLD_MS = 4000;

/**
 * useLensState -- derives the iris state from data already on the bus.
 *
 * The server only ever emits three states (thinking / speaking / idle, see
 * server/api.py). Everything else here is derived client-side from the tool
 * feed and the socket status, so no backend change is needed and no state is
 * ever shown that isn't backed by a real event.
 *
 * `listening` is deliberately absent: nothing can emit it (the voice client
 * doesn't talk to the bus), and a state that can never fire is the same lie
 * as a fake fallback.
 */
export function useLensState(j: JarvisState): LensState {
  const last = j.toolEvents.length
    ? j.toolEvents[j.toolEvents.length - 1]
    : null;
  const lastId = last?.id ?? null;
  const lastStatus = last?.status ?? null;

  const [errorActive, setErrorActive] = useState(false);
  const [dwelling, setDwelling] = useState(false);

  // Red decay after a failed tool call.
  useEffect(() => {
    if (lastStatus !== 'error') return;
    setErrorActive(true);
    const t = window.setTimeout(() => setErrorActive(false), ERROR_HOLD_MS);
    return () => window.clearTimeout(t);
  }, [lastId, lastStatus]);

  // Minimum dwell on thinking.
  useEffect(() => {
    if (j.state !== 'thinking') return;
    setDwelling(true);
    const t = window.setTimeout(() => setDwelling(false), THINK_DWELL_MS);
    return () => window.clearTimeout(t);
  }, [j.state]);

  // Priority order matters. `offline` is the only honest state when the
  // socket is down, and a fresh failure outranks whatever the brain last said.
  if (!j.status.online) return 'offline';
  if (errorActive) return 'error';
  if (lastStatus === 'running') return 'tool';
  if (j.state === 'speaking') return 'speaking';
  if (j.state === 'thinking' || dwelling) return 'thinking';
  return 'idle';
}
