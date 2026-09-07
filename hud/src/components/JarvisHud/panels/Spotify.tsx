import { SpotifyWidget } from '../../SpotifyWidget';

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

interface SpotifyProps {
  data: SpotifyData | null;
}

/** Spotify panel -- wraps SpotifyWidget, stacked above ToolFeedPanel at bottom-left. */
export function Spotify({ data }: SpotifyProps) {
  return (
    <div className="absolute left-[18px] bottom-[110px] w-[220px] pointer-events-auto">
      <SpotifyWidget data={data} />
    </div>
  );
}