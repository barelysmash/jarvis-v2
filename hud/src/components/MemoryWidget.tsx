import { useEffect, useState } from "react";
import { API_BASE } from "../lib/ws";

interface MemoryData {
  facts: string[];
  episodes: { time: string; user: string; jarvis: string }[];
}

export function MemoryWidget() {
  const [data, setData] = useState<MemoryData>({ facts: [], episodes: [] });

  useEffect(() => {
    const fetchMemory = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/memory?facts=4&episodes=3`);
        if (r.ok) setData(await r.json());
      } catch {
        // Silent — connection might be down
      }
    };
    fetchMemory();
    const interval = setInterval(fetchMemory, 8000); // refresh every 8s
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm">
      <div className="px-4 py-2 border-b border-cyan-500/20">
        <div className="text-cyan-400 text-xs font-mono tracking-[0.2em]">
          MEMORY
        </div>
      </div>
      <div className="p-4 space-y-3 text-xs font-mono">
        {/* Facts */}
        <div>
          <div className="text-cyan-700 text-[10px] mb-1">KNOWN FACTS</div>
          {data.facts.length === 0 ? (
            <div className="text-cyan-800 italic">No facts stored</div>
          ) : (
            <ul className="space-y-0.5 text-cyan-200">
              {data.facts.map((f, i) => (
                <li key={i} className="leading-snug">
                  · {f}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Episodes */}
        <div>
          <div className="text-cyan-700 text-[10px] mb-1">RECENT EXCHANGES</div>
          {data.episodes.length === 0 ? (
            <div className="text-cyan-800 italic">No history</div>
          ) : (
            <ul className="space-y-1 text-cyan-300">
              {data.episodes.map((e, i) => (
                <li key={i} className="leading-tight">
                  <span className="text-cyan-700">[{e.time}]</span> {e.user}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
