import { useEffect, useState } from "react";
import { getMicAudioLevel } from "../lib/audio";

export function useAudioLevel(enabled: boolean = false): number {
  const [level, setLevel] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setLevel(0);
      return;
    }
    let cleanup: (() => void) | undefined;
    getMicAudioLevel(setLevel, (err) =>
      console.warn("Mic access denied:", err)
    ).then((c) => (cleanup = c));
    return () => cleanup?.();
  }, [enabled]);

  return level;
}
