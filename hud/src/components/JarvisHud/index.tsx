import { useShutter } from './hooks/useShutter';
import { useViewportScale } from './hooks/useViewportScale';
import { useJarvisState } from '../../hooks/useJarvisState';
import { Lens } from './Lens';
import { DateClock } from './panels/DateClock';
import { DateTicker } from './panels/DateTicker';
import { Wordmark } from './panels/Wordmark';
import { Schedule } from './panels/Schedule';
import { Chat } from './panels/Chat';
import { ToolFeedPanel } from './panels/ToolFeedPanel';
import { WeatherForecast } from './panels/WeatherForecast';
import { MarketCharts } from './panels/MarketCharts';
import { StocksTicker } from './panels/StocksTicker';
import { NewsTicker } from './panels/NewsTicker';
import { CPURamRings } from './panels/CPURamRings';
import { DiskEnergy } from './panels/DiskEnergy';
import { Scanline } from './effects/Scanline';
import { Vignette } from './effects/Vignette';
import { Flash } from './effects/Flash';
import { JARVIS_VERSION } from './version';
import './JarvisHud.css';

/**
 * JarvisHud — top-level HUD container.
 *
 * Fixed 1180×664 design stage. A viewport wrapper applies a uniform CSS
 * `transform: scale()` calculated from window size so the HUD fills any
 * window. Maximize → JARVIS fills the screen.
 */
export function JarvisHud() {
  const { firing, fire, bladeFireRef } = useShutter();
  const scale = useViewportScale();
  const j = useJarvisState();

  return (
    <div className="jhud-viewport">
      <div
        className={`jhud ${firing ? 'firing' : ''}`}
        onClick={fire}
        data-version={JARVIS_VERSION}
        style={{ transform: `scale(${scale})` }}
      >
        {/* MAIN SVG — lens centerpiece + SVG-based panels */}
        <svg
          className="jhud-svg"
          viewBox="-700 -394 1400 788"
          preserveAspectRatio="xMidYMid meet"
        >
          <Lens bladeFireRef={bladeFireRef} audioLevel={j.audioLevel} />
          <DateClock />
          <CPURamRings data={j.widgets.system} />
          <DiskEnergy data={j.widgets.system} />
          <Wordmark />
        </svg>

        {/* HTML OVERLAY PANELS */}
        <DateTicker />
        <StocksTicker items={j.widgets.stocks} />
        <WeatherForecast now={j.widgets.weather} days={j.widgets.forecast} />
        <MarketCharts data={j.widgets.markets} />
        <Schedule events={j.widgets.schedule?.events ?? null} />
        <ToolFeedPanel events={j.toolEvents} />
        <Chat messages={j.messages} />
        <NewsTicker items={j.widgets.news} />

        {/* EFFECTS LAYER */}
        <Scanline />
        <Vignette />
        <Flash />
      </div>
    </div>
  );
}
