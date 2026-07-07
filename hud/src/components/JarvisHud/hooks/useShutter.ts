import { useCallback, useEffect, useRef, useState } from 'react';

const FIRE_DURATION_MS = 950;

export interface ShutterState {
  firing: boolean;
  /** Call to fire the shutter. Debounced — no-ops if already firing. */
  fire: () => void;
  /** Ref to attach to the SMIL <animate> element that drives the blade clamp.
   *  The hook calls .beginElement() on it when firing transitions to true. */
  bladeFireRef: React.RefObject<SVGAnimateElement | null>;
}

export function useShutter(): ShutterState {
  const [firing, setFiring] = useState(false);
  const lockRef = useRef(false);
  const bladeFireRef = useRef<SVGAnimateElement | null>(null);

  const fire = useCallback(() => {
    if (lockRef.current) return;
    lockRef.current = true;
    setFiring(true);

    // Trigger the SMIL blade-clamp animation. The ref points to an
    // <animate begin="indefinite" /> element inside the lens body.
    const node = bladeFireRef.current as
      (SVGAnimateElement & { beginElement?: () => void }) | null;
    try { node?.beginElement?.(); } catch { /* SMIL not supported */ }
  }, []);

  // Auto-clear the firing flag after the animation completes.
  useEffect(() => {
    if (!firing) return;
    const id = window.setTimeout(() => {
      setFiring(false);
      lockRef.current = false;
    }, FIRE_DURATION_MS);
    return () => window.clearTimeout(id);
  }, [firing]);

  return { firing, fire, bladeFireRef };
}
