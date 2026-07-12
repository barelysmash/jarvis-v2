import { useEffect, useState } from 'react';

/** The HUD is designed at exactly this size; everything scales from here. */
export const BASE_W = 1180;
export const BASE_H = 664;

function calc(): number {
  if (typeof window === 'undefined') return 1;
  // 12px vertical slack: at exactly 16:9 the stage otherwise fits to the
  // pixel, and any rounding or browser-chrome quirk clips the newswire.
  return Math.min(
    window.innerWidth / BASE_W,
    (window.innerHeight - 12) / BASE_H,
  );
}

/** Returns the uniform scale factor that fits a {@link BASE_W} × {@link BASE_H}
 *  stage into the current viewport. The HUD wrapper applies this as a CSS
 *  `transform: scale()`, so everything inside — SVG and HTML overlays alike —
 *  grows or shrinks together. Recomputes on window resize. */
export function useViewportScale(): number {
  const [scale, setScale] = useState<number>(calc);
  useEffect(() => {
    const onResize = () => setScale(calc());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return scale;
}
