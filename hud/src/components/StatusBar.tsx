import { useEffect, useState } from "react";
import { Activity, Cpu, Database, Wifi } from "lucide-react";

interface StatusBarProps {
  uptime: number;
  latencyMs: number;
  memoryFacts: number;
  online: boolean;
}

export function StatusBar({
  uptime,
  latencyMs,
  memoryFacts,
  online,
}: StatusBarProps) {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const formatUptime = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
  };

  return (
    <div className="flex items-center justify-between px-6 py-3 border-b border-cyan-500/20 bg-black/40 backdrop-blur-sm">
      <div className="flex items-center gap-2">
        <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
        <span className="text-cyan-400 font-mono text-sm tracking-wider">
          J.A.R.V.I.S
        </span>
        <span className="text-cyan-600 text-xs ml-2">v1.0.0</span>
      </div>

      <div className="flex items-center gap-6 text-xs font-mono">
        <StatusItem
          icon={<Activity size={12} />}
          label="UPTIME"
          value={formatUptime(uptime)}
        />
        <StatusItem
          icon={<Cpu size={12} />}
          label="LATENCY"
          value={`${latencyMs}ms`}
        />
        <StatusItem
          icon={<Database size={12} />}
          label="MEMORY"
          value={`${memoryFacts} facts`}
        />
        <StatusItem
          icon={<Wifi size={12} />}
          label="LINK"
          value={online ? "ONLINE" : "OFFLINE"}
          color={online ? "text-cyan-400" : "text-red-400"}
        />
        <div className="text-cyan-400 ml-4">
          {time.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })}
        </div>
      </div>
    </div>
  );
}

function StatusItem({
  icon,
  label,
  value,
  color = "text-cyan-400",
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-cyan-600">{icon}</span>
      <span className="text-cyan-700">{label}</span>
      <span className={color}>{value}</span>
    </div>
  );
}
