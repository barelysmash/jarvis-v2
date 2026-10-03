// Cockpit data layer: one WebSocket to /ws/cockpit, folded into
// turns, tool calls and an agent/job change feed.
//
// The server tails the cross-process SQLite event log by id, so on
// reconnect we pass ?after_id=<last seen> and get a gap-free resume.

import { useEffect, useReducer, useRef, useState } from "react";

export interface RawEvent {
  id: number;
  ts: number;
  iso: string;
  source: string;
  type: string;
  pid: number | null;
  proc: string | null;
  payload: Record<string, any>;
}

export type ToolStatus = "running" | "success" | "error";

export interface ToolCall {
  key: string;
  name: string;
  agent: string;
  args: any;
  status: ToolStatus;
  startTs: number;
  endTs?: number;
  ms?: number;
  result?: any;
  turnId?: string | null;
  proc: string;
}

export interface LlmStep {
  kind: "llm";
  ts: number;
  iteration: number;
  model: string;
  ms: number;
  stopReason: string | null;
  inTok: number;
  outTok: number;
  text: string;
  error?: string;
}

export interface ToolStep {
  kind: "tool";
  ts: number;
  key: string;
}

export interface Turn {
  id: string;
  origin: string;
  proc: string;
  input: string;
  model?: string;
  startTs: number;
  endTs?: number;
  ms?: number;
  reply?: string;
  error?: string | null;
  steps: (LlmStep | ToolStep)[];
  stats?: Record<string, number>;
}

export interface ChangeItem {
  id: number;
  ts: number;
  type: "agent" | "job";
  source: string;
  kind: string;
  severity: string;
  summary: string;
  data?: any;
  proc: string;
}

export interface FeedState {
  lastId: number;
  raw: RawEvent[];
  turnOrder: string[];
  turns: Record<string, Turn>;
  toolOrder: string[];
  tools: Record<string, ToolCall>;
  changes: ChangeItem[];
  // name+proc -> FIFO of open tool keys, for events without call_id
  openByName: Record<string, string[]>;
}

const RAW_CAP = 3000;
const TURN_CAP = 300;
const TOOL_CAP = 1500;
const CHANGE_CAP = 600;

export const emptyFeed: FeedState = {
  lastId: 0,
  raw: [],
  turnOrder: [],
  turns: {},
  toolOrder: [],
  tools: {},
  changes: [],
  openByName: {},
};

function trimRecord<T>(order: string[], rec: Record<string, T>, cap: number) {
  if (order.length <= cap) return { order, rec };
  const drop = order.slice(0, order.length - cap);
  const next = { ...rec };
  for (const k of drop) delete next[k];
  return { order: order.slice(-cap), rec: next };
}

export function foldEvents(prev: FeedState, incoming: RawEvent[]): FeedState {
  const events = incoming.filter((e) => e.id > prev.lastId);
  if (!events.length) return prev;
  const s: FeedState = {
    ...prev,
    raw: prev.raw.concat(events).slice(-RAW_CAP),
    turnOrder: prev.turnOrder.slice(),
    turns: { ...prev.turns },
    toolOrder: prev.toolOrder.slice(),
    tools: { ...prev.tools },
    changes: prev.changes.slice(),
    openByName: { ...prev.openByName },
  };

  for (const e of events) {
    s.lastId = Math.max(s.lastId, e.id);
    const p = e.payload || {};
    const proc = e.proc || e.source;

    switch (e.type) {
      case "turn.start": {
        const id = String(p.turn_id);
        if (!s.turns[id]) s.turnOrder.push(id);
        s.turns[id] = {
          id,
          origin: p.origin || proc,
          proc,
          input: String(p.input ?? ""),
          model: p.model,
          startTs: e.ts,
          steps: [],
        };
        break;
      }
      case "llm": {
        const t = s.turns[String(p.turn_id)];
        if (!t) break;
        s.turns[t.id] = {
          ...t,
          steps: t.steps.concat({
            kind: "llm",
            ts: e.ts,
            iteration: p.iteration ?? 0,
            model: p.model ?? "",
            ms: p.ms ?? 0,
            stopReason: p.stop_reason ?? null,
            inTok: p.input_tokens ?? 0,
            outTok: p.output_tokens ?? 0,
            text: p.text ?? "",
            error: p.error,
          }),
        };
        break;
      }
      case "turn.end": {
        const id = String(p.turn_id);
        const t = s.turns[id];
        if (!t) break;
        const { turn_id, text, ms, error, origin, ...stats } = p;
        s.turns[id] = {
          ...t,
          endTs: e.ts,
          ms,
          reply: text,
          error,
          stats: stats as Record<string, number>,
        };
        break;
      }
      case "tool": {
        const nameKey = `${p.name}|${proc}`;
        let key: string | undefined;
        if (p.status === "running") {
          key = p.call_id ? String(p.call_id) : `ev${e.id}`;
          s.toolOrder.push(key);
          s.tools[key] = {
            key,
            name: p.name,
            agent: p.agent || guessAgent(p.name),
            args: p.args,
            status: "running",
            startTs: e.ts,
            turnId: p.turn_id,
            proc,
          };
          if (!p.call_id) {
            s.openByName[nameKey] = (s.openByName[nameKey] || []).concat(key);
          }
          const t = p.turn_id ? s.turns[String(p.turn_id)] : undefined;
          if (t) {
            s.turns[t.id] = {
              ...t,
              steps: t.steps.concat({ kind: "tool", ts: e.ts, key }),
            };
          }
        } else {
          if (p.call_id && s.tools[String(p.call_id)]) {
            key = String(p.call_id);
          } else {
            const q = s.openByName[nameKey] || [];
            key = q[0];
            s.openByName[nameKey] = q.slice(1);
          }
          if (key && s.tools[key]) {
            const c = s.tools[key];
            s.tools[key] = {
              ...c,
              status: p.status === "error" ? "error" : "success",
              endTs: e.ts,
              ms: p.ms ?? Math.round((e.ts - c.startTs) * 1000),
              result: p.result,
            };
          } else {
            // Finish without a start inside our window: record standalone.
            key = `ev${e.id}`;
            s.toolOrder.push(key);
            s.tools[key] = {
              key,
              name: p.name,
              agent: p.agent || guessAgent(p.name),
              args: p.args,
              status: p.status === "error" ? "error" : "success",
              startTs: e.ts - (p.ms ?? 0) / 1000,
              endTs: e.ts,
              ms: p.ms,
              result: p.result,
              turnId: p.turn_id,
              proc,
            };
          }
        }
        break;
      }
      case "agent":
        s.changes.push({
          id: e.id,
          ts: e.ts,
          type: "agent",
          source: p.agent || e.source,
          kind: p.kind || "change",
          severity: p.severity || "info",
          summary: p.summary || "",
          data: p.data,
          proc,
        });
        break;
      case "job": {
        const phase = p.phase;
        const failed = phase === "end" && p.ok === false;
        s.changes.push({
          id: e.id,
          ts: e.ts,
          type: "job",
          source: p.job || e.source,
          kind: `job.${phase}`,
          severity: failed ? "error" : phase === "end" ? "notice" : "info",
          summary:
            p.summary ||
            p.error ||
            (phase === "start"
              ? `${p.job} started`
              : phase === "end"
              ? `${p.job} finished in ${fmtMs(p.ms)}`
              : `${p.job} ${phase}`),
          data: p,
          proc,
        });
        break;
      }
      default:
        break;
    }
  }

  const tr = trimRecord(s.turnOrder, s.turns, TURN_CAP);
  s.turnOrder = tr.order;
  s.turns = tr.rec;
  const tl = trimRecord(s.toolOrder, s.tools, TOOL_CAP);
  s.toolOrder = tl.order;
  s.tools = tl.rec;
  s.changes = s.changes.slice(-CHANGE_CAP);
  return s;
}

export function guessAgent(name: string): string {
  if (!name) return "jarvis";
  const prefix = name.split("_")[0];
  return prefix || "jarvis";
}

export function fmtMs(ms?: number | null): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const sec = Math.round((ms % 60_000) / 1000);
  return `${m}m ${sec}s`;
}

// ─── Socket hook ─────────────────────────────────────────────

function cockpitWsUrl(afterId: number): string {
  const env = (import.meta as any).env || {};
  let base: string = env.VITE_WS_URL || "";
  if (base) base = base.replace(/\/ws\/?$/, "/ws/cockpit");
  else {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    base = `${proto}//${window.location.host}/ws/cockpit`;
  }
  return afterId > 0 ? `${base}?after_id=${afterId}` : `${base}?backfill=800`;
}

export type LinkState = "connecting" | "live" | "down";

type Action = { type: "events"; events: RawEvent[] } | { type: "reset" };

function reducer(state: FeedState, action: Action): FeedState {
  if (action.type === "reset") return emptyFeed;
  return foldEvents(state, action.events);
}

export function useCockpitFeed() {
  const [feed, dispatch] = useReducer(reducer, emptyFeed);
  const [link, setLink] = useState<LinkState>("connecting");
  const lastIdRef = useRef(0);
  lastIdRef.current = feed.lastId;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;
    let ping: number | undefined;
    let backoff = 1000;

    const connect = () => {
      setLink("connecting");
      ws = new WebSocket(cockpitWsUrl(lastIdRef.current));
      ws.onopen = () => {
        setLink("live");
        backoff = 1000;
        ping = window.setInterval(() => {
          try {
            ws?.send("ping");
          } catch {
            /* closing */
          }
        }, 20_000);
      };
      ws.onmessage = (m) => {
        let msg: any;
        try {
          msg = JSON.parse(m.data);
        } catch {
          return;
        }
        if (msg.type === "backfill" || msg.type === "events") {
          dispatch({ type: "events", events: msg.events || [] });
        }
      };
      ws.onclose = () => {
        window.clearInterval(ping);
        setLink("down");
        if (!closed) {
          retry = window.setTimeout(connect, backoff);
          backoff = Math.min(backoff * 2, 15_000);
        }
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(ping);
      ws?.close();
    };
  }, []);

  return { feed, link };
}

// ─── Polled REST resources ───────────────────────────────────

export function apiBase(): string {
  const env = (import.meta as any).env || {};
  return env.VITE_API_BASE || `${window.location.protocol}//${window.location.host}`;
}

export function usePolled<T>(path: string, everyMs: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        const j = (await r.json()) as T;
        if (alive) {
          setData(j);
          setError(null);
        }
      } catch (err: any) {
        if (alive) setError(String(err?.message || err));
      }
    };
    load();
    const t = window.setInterval(load, everyMs);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [path, everyMs, nonce]);
  return { data, error, refresh: () => setNonce((n) => n + 1) };
}

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}
