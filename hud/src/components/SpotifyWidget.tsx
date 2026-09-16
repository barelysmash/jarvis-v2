import { useEffect, useState } from "react";

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

interface SpotifyDevice {
  id: string;
  name: string;
  type: string;
  is_active: boolean;
  volume_percent?: number;
}

interface QueueItem {
  track: string;
  artist: string;
  album_art?: string;
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

async function fetchDevices(): Promise<SpotifyDevice[]> {
  try {
    const resp = await fetch("/api/spotify/devices");
    const data = await resp.json();
    return data.devices ?? [];
  } catch {
    return [];
  }
}

async function fetchQueue(): Promise<QueueItem[]> {
  try {
    const resp = await fetch("/api/spotify/queue");
    const data = await resp.json();
    return data.queue ?? [];
  } catch {
    return [];
  }
}

async function transferDevice(deviceId: string) {
  try {
    await fetch(`/api/spotify/devices/${deviceId}/transfer`, { method: "POST" });
  } catch {
    // Swallow -- next poll will reconcile.
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
  const [showDevices, setShowDevices] = useState(false);
  const [devices, setDevices] = useState<SpotifyDevice[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);

  const hasTrack = !!data?.track;
  const progress =
    data?.duration_ms && data.duration_ms > 0
      ? Math.min(100, ((data.progress_ms ?? 0) / data.duration_ms) * 100)
      : 0;

  useEffect(() => {
    if (collapsed) return;
    fetchDevices().then(setDevices);
    fetchQueue().then(setQueue);
  }, [collapsed]);

  const handleDeviceClick = async (deviceId: string) => {
    await transferDevice(deviceId);
    setDevices(await fetchDevices());
  };

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

      <div className="p-4 font-mono max-h-[320px] overflow-y-auto">
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

        <div className="mt-3 pt-2 border-t border-cyan-500/10 text-cyan-700 text-[9px] tracking-[0.1em]">
          DEVICES
        </div>

        {(
          <div className="mt-1 space-y-1">
            {devices.length === 0 ? (
              <div className="text-cyan-800 italic text-[10px]">No devices found</div>
            ) : (
              devices.map((d) => (
                <div
                  key={d.id}
                  onClick={() => handleDeviceClick(d.id)}
                  className={`text-[10px] px-2 py-1 rounded-sm cursor-pointer truncate ${
                    d.is_active
                      ? "bg-cyan-500/20 text-cyan-200"
                      : "text-cyan-600 hover:bg-cyan-500/10"
                  }`}
                >
                  {d.is_active ? "● " : "○ "}
                  {d.name}
                </div>
              ))
            )}
          </div>
        )}

        {hasTrack && queue.length > 0 && (
          <div className="mt-3 pt-2 border-t border-cyan-500/10">
            <div className="text-cyan-700 text-[9px] tracking-[0.1em] mb-1">
              UP NEXT
            </div>
            <div className="space-y-1 max-h-[120px] overflow-y-auto">
              {queue.slice(0, 5).map((item, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px]">
                  {item.album_art && (
                    <img
                      src={item.album_art}
                      alt=""
                      className="w-4 h-4 rounded-sm object-cover flex-shrink-0"
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="text-cyan-400 truncate">{item.track}</div>
                    <div className="text-cyan-700 truncate">{item.artist}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}