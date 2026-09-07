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

/** Spotify panel -- wraps SpotifyWidget, positioned beside Schedule at the same top anchor
 *  so it never collides with Schedule's variable height as event count changes. */
export function Spotify({ data }: SpotifyProps) {
  return (
    <div className="absolute left-[250px] top-[358px] w-[220px] pointer-events-auto">
      <SpotifyWidget data={data} />
    </div>
  );
}