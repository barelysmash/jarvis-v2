import { useEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  XCircle,
} from "lucide-react";
import type { ToolEvent } from "../hooks/useJarvisState";

const PIN_KEY = "jhud.toolfeed.pinned";
/** How long the feed stays open after the most recent tool event. */
const FRESH_MS = 4000;

/**
 * ToolFeed -- collapsible activity panel.
 *
 * Collapsed it is a ~34px header strip, which keeps it clear of the SCHEDULE
 * panel above it. It auto-expands while tools are firing, then folds itself
 * away again. Clicking the header pins it open (or shut) and that choice
 * persists across HUD reloads.
 */
export function ToolFeed({ events }: { events: ToolEvent[] }) {
  const [pinned, setPinned] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(PIN_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [fresh, setFresh] = useState(false);

  const last = events.length ? events[events.length - 1] : null;
  const lastId = last?.id ?? null;
  const seen = useRef<string | null>(null);

  useEffect(() => {
    if (!lastId || seen.current === lastId) return;
    seen.current = lastId;
    setFresh(true);
    const t = window.setTimeout(() => setFresh(false), FRESH_MS);
    return () => window.clearTimeout(t);
  }, [lastId]);

  const expanded = pinned || fresh;
  const running = events.filter((e) => e.status === "running").length;

  const toggle = (e: ReactMouseEvent) => {
    // The .jhud root carries an onClick that fires the shutter animation.
    // Without this the panel would fire the shutter on every toggle.
    e.stopPropagation();
    const next = !pinned;
    setPinned(next);
    try {
      window.localStorage.setItem(PIN_KEY, next ? "1" : "0");
    } catch {
      /* private mode -- the pin just won't persist */
    }
  };

  return (
    <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        className={`w-full px-4 py-2 flex items-center justify-between text-left
                    focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400/60
                    ${expanded ? "border-b border-cyan-500/20" : ""}`}
      >
        <span className="flex items-center gap-1.5 text-cyan-400 text-xs font-mono tracking-[0.2em]">
          {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          TOOL ACTIVITY
        </span>
        <span className="text-cyan-700 text-[10px] font-mono truncate max-w-[130px]">
          {running > 0 ? `${running} ACTIVE` : last ? last.name : "IDLE"}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="feed"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="p-3 space-y-1.5 max-h-32 overflow-y-auto">
              {events.length === 0 && (
                <div className="text-cyan-700 text-[10px] font-mono italic">
                  No recent activity
                </div>
              )}
              <AnimatePresence>
                {events
                  .slice(-8)
                  .reverse()
                  .map((evt) => (
                    <motion.div
                      key={evt.id}
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      className="flex items-center gap-2 text-xs font-mono"
                    >
                      <StatusIcon status={evt.status} />
                      <span className="text-cyan-300">{evt.name}</span>
                      <span className="text-cyan-700 truncate flex-1">
                        {Object.entries(evt.args)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(" ")}
                      </span>
                      <span className="text-cyan-800 text-[10px]">
                        {evt.timestamp}
                      </span>
                    </motion.div>
                  ))}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function StatusIcon({ status }: { status: ToolEvent["status"] }) {
  if (status === "running")
    return <Loader2 size={12} className="text-cyan-400 animate-spin" />;
  if (status === "success")
    return <CheckCircle2 size={12} className="text-green-400" />;
  return <XCircle size={12} className="text-red-400" />;
}
