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

/** Spotify panel -- wraps SpotifyWidget, positions it below Schedule at left-mid. */
export function Spotify({ data }: SpotifyProps) {
  return (
    <div className="absolute left-[18px] top-[560px] w-[220px] pointer-events-auto">
      <SpotifyWidget data={data} />
    </div>
  );
}