import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import type { ToolEvent } from "../hooks/useJarvisState";

export function ToolFeed({ events }: { events: ToolEvent[] }) {
  return (
    <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm">
      <div className="px-4 py-2 border-b border-cyan-500/20 flex items-center justify-between">
        <div className="text-cyan-400 text-xs font-mono tracking-[0.2em]">
          TOOL ACTIVITY
        </div>
        <div className="text-cyan-700 text-[10px] font-mono">
          {events.filter((e) => e.status === "running").length} ACTIVE
        </div>
      </div>

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
