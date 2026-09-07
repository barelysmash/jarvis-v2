import { useState } from "react";

interface SpotifyData {
  is_playing: boolean;
  progress_ms?: number;
  duration_ms?: number;
  track?: string;
  artist?: string;
  album?: string;
  album_art?: string;
  device?: string;
}

interface SpotifyWidgetProps {
  data: SpotifyData | null;
}

async function callTransport(action: "play" | "pause" | "next" | "previous") {
  try {
    await fetch(`/api/spotify/${action}`, { method: "POST" });
  } catch {
    // Swallow -- next 3s poll will reconcile actual state regardless.
  }
}

function formatMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

export function SpotifyWidget({ data }: SpotifyWidgetProps) {
  const [collapsed, setCollapsed] = useState(true);

  const hasTrack = !!data?.track;
  const progress =
    data?.duration_ms && data.duration_ms > 0
      ? Math.min(100, ((data.progress_ms ?? 0) / data.duration_ms) * 100)
      : 0;

  if (collapsed) {
    return (
      <div
        onClick={() => setCollapsed(false)}
        className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm px-3 py-2 flex items-center gap-2 cursor-pointer w-[220px]"
      >
        {hasTrack && data?.album_art ? (
          <img
            src={data.album_art}
            alt=""
            className="w-6 h-6 rounded-sm object-cover flex-shrink-0"
          />
        ) : (
          <div className="w-6 h-6 rounded-sm bg-cyan-500/10 flex-shrink-0" />
        )}
        <div className="text-cyan-400 text-[10px] font-mono tracking-[0.15em] truncate flex-1">
          {hasTrack ? data!.track : "SPOTIFY"}
        </div>
        <div className="text-cyan-600 text-xs font-mono flex-shrink-0">
          {data?.is_playing ? "▶" : "⏸"}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm w-[220px]">
      <div
        onClick={() => setCollapsed(true)}
        className="px-4 py-2 border-b border-cyan-500/20 flex items-center justify-between cursor-pointer"
      >
        <div className="text-cyan-400 text-xs font-mono tracking-[0.2em]">
          SPOTIFY
        </div>
        <div className="text-cyan-700 text-[10px] font-mono">▼</div>
      </div>

      <div className="p-4 font-mono">
        {!hasTrack ? (
          <div className="text-cyan-800 italic text-xs">Nothing playing</div>
        ) : (
          <>
            <div className="flex gap-3">
              {data?.album_art && (
                <img
                  src={data.album_art}
                  alt=""
                  className="w-14 h-14 rounded-sm object-cover flex-shrink-0"
                />
              )}
              <div className="min-w-0 flex-1">
                <div className="text-cyan-200 text-xs leading-tight truncate">
                  {data!.track}
                </div>
                <div className="text-cyan-600 text-[10px] truncate">
                  {data!.artist}
                </div>
                <div className="text-cyan-800 text-[10px] truncate">
                  {data!.device}
                </div>
              </div>
            </div>

            <div className="mt-3 h-[2px] bg-cyan-900/40 rounded-full overflow-hidden">
              <div
                className="h-full bg-cyan-500/60"
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="flex justify-between text-[9px] text-cyan-700 mt-1">
              <span>{formatMs(data?.progress_ms ?? 0)}</span>
              <span>{formatMs(data?.duration_ms ?? 0)}</span>
            </div>

            <div className="flex justify-center items-center gap-4 mt-3">
              <button
                onClick={() => callTransport("previous")}
                className="text-cyan-400 hover:text-cyan-200 text-sm"
              >
                ⏮
              </button>
              <button
                onClick={() => callTransport(data?.is_playing ? "pause" : "play")}
                className="text-cyan-400 hover:text-cyan-200 text-lg"
              >
                {data?.is_playing ? "⏸" : "▶"}
              </button>
              <button
                onClick={() => callTransport("next")}
                className="text-cyan-400 hover:text-cyan-200 text-sm"
              >
                ⏭
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}