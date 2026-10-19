import { uid } from "../lib/uid";
import { useEffect, useState } from "react";
import { WS_URL } from "../lib/ws";

export interface Message {
  id: string;
  role: "user" | "jarvis";
  text: string;
  timestamp: string;
}

export interface ToolEvent {
  id: string;
  name: string;
  status: "running" | "success" | "error";
  args: Record<string, any>;
  timestamp: string;
  /** Owning subagent (from agents.yaml), or null for a JARVIS-native tool. */
  agent: string | null;
}

/** One declared subagent, as reported by orchestrator/agents.py. */
export interface AgentInfo {
  name: string;
  description: string;
  transport: "adapter" | "mcp-stdio" | string;
  status: "loaded" | "skipped" | "disabled" | "failed" | string;
  reason: string | null;
  tools: string[];
}

export interface JarvisState {
  state: "idle" | "listening" | "thinking" | "speaking";
  messages: Message[];
  toolEvents: ToolEvent[];
  /** Agent registry from the WS snapshot, in agents.yaml declaration order. */
  agents: AgentInfo[];
  audioLevel: number;
  status: {
    uptime: number;
    latencyMs: number;
    memoryFacts: number;
    online: boolean;
  };
  widgets: Record<string, any>;
}

/**
 * Fallback owner lookup for tool events that arrive without an `agent`
 * field (older server, or replayed from the SQLite event log). Matches
 * against the tool lists the registry snapshot reported.
 */
function ownerOf(toolName: string, agents: AgentInfo[]): string | null {
  for (const a of agents) {
    if (a.tools.includes(toolName)) return a.name;
  }
  return null;
}

export function useJarvisState(): JarvisState {
  const [state, setState] = useState<JarvisState>({
    state: "idle",
    messages: [],
    toolEvents: [],
    agents: [],
    audioLevel: 0,
    status: { uptime: 0, latencyMs: 0, memoryFacts: 0, online: false },
    widgets: {},
  });

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let closed = false;

    const connect = () => {
      ws = new WebSocket(WS_URL);

      ws.onopen = () =>
        setState((s) => ({
          ...s,
          status: { ...s.status, online: true },
        }));

      ws.onclose = () => {
        setState((s) => ({
          ...s,
          status: { ...s.status, online: false },
        }));
        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 2000);
        }
      };

      ws.onmessage = (e) => {
        let evt: any;
        try {
          evt = JSON.parse(e.data);
        } catch {
          return;
        }

        setState((s) => {
          switch (evt.type) {
            case "state":
              return { ...s, state: evt.data.state };
            case "audio":
              return { ...s, audioLevel: evt.data.level };
            case "conversation":
              return {
                ...s,
                messages: [
                  ...s.messages,
                  {
                    id: uid(),
                    role: evt.data.role,
                    text: evt.data.text,
                    timestamp: new Date(evt.timestamp).toLocaleTimeString(
                      [],
                      { hour: "2-digit", minute: "2-digit" }
                    ),
                  },
                ].slice(-50),
              };
            case "tool":
              return {
                ...s,
                toolEvents: [
                  ...s.toolEvents,
                  {
                    id: uid(),
                    name: evt.data.name,
                    args: evt.data.args || {},
                    status: evt.data.status,
                    agent:
                      evt.data.agent ??
                      ownerOf(evt.data.name, s.agents),
                    timestamp: new Date(evt.timestamp).toLocaleTimeString(
                      [],
                      {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      }
                    ),
                  },
                ].slice(-20),
              };
            case "widget":
              return {
                ...s,
                widgets: { ...s.widgets, [evt.data.widget]: evt.data.data },
              };
            case "snapshot":
              return Array.isArray(evt.data?.agents)
                ? { ...s, agents: evt.data.agents }
                : s;
            default:
              return s;
          }
        });
      };
    };

    connect();

    // Tick uptime locally
    const uptimeTimer = window.setInterval(() => {
      setState((s) =>
        s.status.online
          ? { ...s, status: { ...s.status, uptime: s.status.uptime + 1 } }
          : s
      );
    }, 1000);

    return () => {
      closed = true;
      if (ws) ws.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      clearInterval(uptimeTimer);
    };
  }, []);

  return state;
}
