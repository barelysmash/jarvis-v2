// JARVIS Cockpit — the full-detail second screen.
//
// Four live panes, all fed by the cross-process event log:
//   CONVERSATION   every turn: input, each model call, each tool call, reply
//   TOOL ACTIVITY  live tool calls (running pinned, elapsed ticking) + stats
//   SCHEDULE       systemd --user timers: next/last run, result, run-now
//   AGENTS         roster + key changes pushed by sub-agents and jobs

import { type ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ChangeItem,
  FeedState,
  LlmStep,
  ToolCall,
  Turn,
  apiBase,
  fmtMs,
  useCockpitFeed,
  useNow,
  usePolled,
} from "./feed";

// ─── helpers ─────────────────────────────────────────────────

const clock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

const dayClock = (ts: number) => {
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString([], {
        weekday: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
};

function rel(seconds: number): string {
  const a = Math.abs(seconds);
  const s =
    a < 60
      ? `${Math.round(a)}s`
      : a < 3600
      ? `${Math.floor(a / 60)}m`
      : a < 86400
      ? `${Math.floor(a / 3600)}h ${Math.floor((a % 3600) / 60)}m`
      : `${Math.floor(a / 86400)}d ${Math.floor((a % 86400) / 3600)}h`;
  return seconds >= 0 ? `in ${s}` : `${s} ago`;
}

const fmtTok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);

function pretty(v: any): string {
  if (v == null) return "";
  if (typeof v === "string") {
    try {
      return JSON.stringify(JSON.parse(v), null, 2);
    } catch {
      return v;
    }
  }
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function oneLine(v: any, max = 120): string {
  if (v == null) return "";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  if (!s || s === "{}") return "";
  return s.length > max ? s.slice(0, max) + "…" : s;
}

// Keep a scroll pane pinned to the bottom unless the user scrolled up.
function useFollow<T>(dep: T) {
  const ref = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && following) el.scrollTop = el.scrollHeight;
  }, [dep, following]);
  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  };
  const jump = () => {
    setFollowing(true);
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  };
  return { ref, following, onScroll, jump };
}

function Panel(props: {
  title: string;
  right?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`ck-panel ${props.className || ""}`}>
      <header className="ck-panel-head">
        <h2>{props.title}</h2>
        <div className="ck-panel-right">{props.right}</div>
      </header>
      {props.children}
    </section>
  );
}

function StatusPill({ status }: { status: string }) {
  return <span className={`ck-pill ck-pill-${status}`}>{status}</span>;
}

// ─── header ──────────────────────────────────────────────────

function Header({
  feed,
  link,
  now,
}: {
  feed: FeedState;
  link: string;
  now: number;
}) {
  const open = feed.turnOrder
    .map((id) => feed.turns[id])
    .filter((t) => t && !t.endTs && now - t.startTs < 600);
  const running = feed.toolOrder
    .map((k) => feed.tools[k])
    .filter((c) => c && c.status === "running").length;
  const since = now - 24 * 3600;
  const turns24 = feed.turnOrder.filter((id) => feed.turns[id]?.startTs > since);
  const tools24 = feed.toolOrder
    .map((k) => feed.tools[k])
    .filter((c) => c && c.startTs > since);
  const errs24 = tools24.filter((c) => c.status === "error").length;
  const tok = turns24.reduce((acc, id) => {
    const st = feed.turns[id]?.stats;
    return acc + (st?.input_tokens || 0) + (st?.output_tokens || 0);
  }, 0);

  const state = open.length
    ? running
      ? "tool"
      : "thinking"
    : link === "live"
    ? "idle"
    : "offline";

  return (
    <header className="ck-top">
      <div className="ck-brand">
        <span className={`ck-dot ck-dot-${link}`} />
        JARVIS <span className="ck-brand-sub">// COCKPIT</span>
      </div>
      <div className={`ck-state ck-state-${state}`}>
        {state.toUpperCase()}
        {open[0] && (
          <span className="ck-state-detail">
            #{open[0].id} · {open[0].origin} · {fmtMs((now - open[0].startTs) * 1000)}
          </span>
        )}
      </div>
      <div className="ck-kpis">
        <Kpi label="turns 24h" value={turns24.length} />
        <Kpi label="tool calls" value={tools24.length} />
        <Kpi label="errors" value={errs24} warn={errs24 > 0} />
        <Kpi label="tokens" value={fmtTok(tok)} />
        <Kpi label="running" value={running} hot={running > 0} />
      </div>
      <div className="ck-clock">
        {new Date(now * 1000).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })}
        <span className="ck-link">{link === "live" ? "LIVE" : link.toUpperCase()}</span>
      </div>
    </header>
  );
}

function Kpi(p: { label: string; value: ReactNode; warn?: boolean; hot?: boolean }) {
  return (
    <div className={`ck-kpi ${p.warn ? "is-warn" : ""} ${p.hot ? "is-hot" : ""}`}>
      <span className="ck-kpi-v">{p.value}</span>
      <span className="ck-kpi-l">{p.label}</span>
    </div>
  );
}

// ─── conversation ────────────────────────────────────────────

function Conversation({ feed, now }: { feed: FeedState; now: number }) {
  const [originFilter, setOriginFilter] = useState("all");
  const origins = useMemo(() => {
    const set = new Set<string>();
    feed.turnOrder.forEach((id) => set.add(feed.turns[id]?.origin));
    return ["all", ...Array.from(set).filter(Boolean).sort()];
  }, [feed.turnOrder, feed.turns]);
  const turns = feed.turnOrder
    .map((id) => feed.turns[id])
    .filter((t) => t && (originFilter === "all" || t.origin === originFilter));
  const follow = useFollow(feed.lastId);

  return (
    <Panel
      title="Conversation"
      className="ck-conv"
      right={
        <>
          <Seg value={originFilter} options={origins} onChange={setOriginFilter} />
          {!follow.following && (
            <button className="ck-btn" onClick={follow.jump}>
              ↓ latest
            </button>
          )}
        </>
      }
    >
      <div className="ck-scroll" ref={follow.ref} onScroll={follow.onScroll}>
        {turns.length === 0 && (
          <Empty>No turns yet. Anything said to JARVIS — HUD, voice, briefing — lands here.</Empty>
        )}
        {turns.map((t) => (
          <TurnCard key={t.id} turn={t} tools={feed.tools} now={now} />
        ))}
      </div>
    </Panel>
  );
}

function TurnCard({
  turn,
  tools,
  now,
}: {
  turn: Turn;
  tools: Record<string, ToolCall>;
  now: number;
}) {
  const running = !turn.endTs;
  const st = turn.stats || {};
  const status = running ? "running" : turn.error || (st.errors ?? 0) > 0 ? "error" : "done";
  const elapsed = running ? (now - turn.startTs) * 1000 : turn.ms;
  return (
    <article className={`ck-turn is-${status}`}>
      <div className="ck-turn-meta">
        <span className="ck-turn-id">#{turn.id}</span>
        <span className="ck-tag">{turn.origin}</span>
        <span>{clock(turn.startTs)}</span>
        <span>{fmtMs(elapsed)}</span>
        {!running && (
          <>
            <span>{st.iterations ?? 0} calls</span>
            <span>{st.tools ?? 0} tools</span>
            <span>
              {fmtTok(st.input_tokens ?? 0)}→{fmtTok(st.output_tokens ?? 0)} tok
            </span>
          </>
        )}
        <StatusPill status={status} />
      </div>

      <div className="ck-msg ck-msg-user">
        <span className="ck-who">YOU</span>
        <p>{turn.input}</p>
      </div>

      <ol className="ck-steps">
        {turn.steps.map((step, i) =>
          step.kind === "llm" ? (
            <LlmRow key={i} step={step} />
          ) : tools[step.key] ? (
            <ToolRow key={i} call={tools[step.key]} now={now} nested />
          ) : null
        )}
        {running && <li className="ck-step ck-step-wait">working…</li>}
      </ol>

      {turn.reply != null && turn.reply !== "" && (
        <div className="ck-msg ck-msg-jarvis">
          <span className="ck-who">JARVIS</span>
          <p>{turn.reply}</p>
        </div>
      )}
      {turn.error && <div className="ck-msg ck-msg-err">{turn.error}</div>}
    </article>
  );
}

function LlmRow({ step }: { step: LlmStep }) {
  // The final end_turn text is shown as the reply; show interim text only.
  const interim = step.stopReason !== "end_turn" ? step.text : "";
  return (
    <li className={`ck-step ck-step-llm ${step.error ? "is-error" : ""}`}>
      <div className="ck-step-line">
        <span className="ck-glyph">◇</span>
        <span>model call {step.iteration}</span>
        <span className="ck-dim">{step.model}</span>
        <span className="ck-dim">{fmtMs(step.ms)}</span>
        <span className="ck-dim">
          {fmtTok(step.inTok)}→{fmtTok(step.outTok)}
        </span>
        <span className="ck-tag ck-tag-soft">{step.stopReason || "?"}</span>
      </div>
      {interim && <p className="ck-interim">{interim}</p>}
      {step.error && <p className="ck-err-text">{step.error}</p>}
    </li>
  );
}

function ToolRow({
  call,
  now,
  nested,
}: {
  call: ToolCall;
  now: number;
  nested?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ms = call.status === "running" ? (now - call.startTs) * 1000 : call.ms;
  const Tag = nested ? "li" : "div";
  return (
    <Tag className={`ck-step ck-tool is-${call.status}`}>
      <button className="ck-step-line ck-tool-line" onClick={() => setOpen(!open)}>
        <span className="ck-glyph">{open ? "▾" : "▸"}</span>
        <span className="ck-tool-name">{call.name}</span>
        <span className="ck-tag">{call.agent}</span>
        {!nested && <span className="ck-dim">{clock(call.startTs)}</span>}
        {!nested && call.proc !== "api" && <span className="ck-dim">{call.proc}</span>}
        <span className="ck-args">{oneLine(call.args, 90)}</span>
        <span className="ck-ms">{fmtMs(ms)}</span>
        <StatusPill status={call.status} />
      </button>
      {open && (
        <div className="ck-io">
          <div>
            <h4>args</h4>
            <pre>{pretty(call.args) || "{}"}</pre>
          </div>
          <div>
            <h4>result</h4>
            <pre>{call.status === "running" ? "…" : pretty(call.result) || "(not recorded)"}</pre>
          </div>
        </div>
      )}
    </Tag>
  );
}

// ─── tool activity ───────────────────────────────────────────

function ToolActivity({ feed, now }: { feed: FeedState; now: number }) {
  const [agent, setAgent] = useState("all");
  const [tab, setTab] = useState<"live" | "stats" | "raw">("live");
  const calls = feed.toolOrder.map((k) => feed.tools[k]).filter(Boolean);
  const agents = ["all", ...Array.from(new Set(calls.map((c) => c.agent))).sort()];
  const visible = calls.filter((c) => agent === "all" || c.agent === agent);
  const running = visible.filter((c) => c.status === "running").reverse();
  const done = visible.filter((c) => c.status !== "running").reverse().slice(0, 250);

  return (
    <Panel
      title="Tool activity"
      className="ck-tools"
      right={
        <>
          <Seg value={tab} options={["live", "stats", "raw"]} onChange={(v) => setTab(v as any)} />
          {tab !== "raw" && <Seg value={agent} options={agents} onChange={setAgent} />}
        </>
      }
    >
      {tab === "live" && (
        <div className="ck-scroll">
          {running.length > 0 && (
            <div className="ck-running">
              {running.map((c) => (
                <ToolRow key={c.key} call={c} now={now} />
              ))}
            </div>
          )}
          {done.map((c) => (
            <ToolRow key={c.key} call={c} now={now} />
          ))}
          {visible.length === 0 && <Empty>No tool calls in the loaded window.</Empty>}
        </div>
      )}
      {tab === "stats" && <ToolStats calls={visible} />}
      {tab === "raw" && <RawTail feed={feed} />}
    </Panel>
  );
}

function ToolStats({ calls }: { calls: ToolCall[] }) {
  const rows = useMemo(() => {
    const by: Record<string, { name: string; agent: string; n: number; err: number; ms: number[]; last: number }> = {};
    for (const c of calls) {
      const r = (by[c.name] ||= { name: c.name, agent: c.agent, n: 0, err: 0, ms: [], last: 0 });
      r.n += 1;
      if (c.status === "error") r.err += 1;
      if (c.ms != null) r.ms.push(c.ms);
      r.last = Math.max(r.last, c.startTs);
    }
    return Object.values(by)
      .map((r) => {
        const sorted = r.ms.slice().sort((a, b) => a - b);
        const avg = sorted.length ? sorted.reduce((a, b) => a + b, 0) / sorted.length : null;
        const p95 = sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] : null;
        return { ...r, avg, p95 };
      })
      .sort((a, b) => b.n - a.n);
  }, [calls]);
  return (
    <div className="ck-scroll">
      <table className="ck-table">
        <thead>
          <tr>
            <th>tool</th>
            <th>agent</th>
            <th className="num">calls</th>
            <th className="num">err</th>
            <th className="num">avg</th>
            <th className="num">p95</th>
            <th className="num">last</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className={r.err ? "is-error" : ""}>
              <td className="ck-tool-name">{r.name}</td>
              <td>{r.agent}</td>
              <td className="num">{r.n}</td>
              <td className="num">{r.err || ""}</td>
              <td className="num">{fmtMs(r.avg)}</td>
              <td className="num">{fmtMs(r.p95)}</td>
              <td className="num">{clock(r.last)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <Empty>No tool calls yet.</Empty>}
    </div>
  );
}

function RawTail({ feed }: { feed: FeedState }) {
  const [q, setQ] = useState("");
  const follow = useFollow(feed.lastId);
  const needle = q.trim().toLowerCase();
  const rows = feed.raw
    .slice(-600)
    .filter(
      (e) =>
        !needle ||
        e.type.toLowerCase().includes(needle) ||
        e.source.toLowerCase().includes(needle) ||
        JSON.stringify(e.payload).toLowerCase().includes(needle)
    );
  return (
    <>
      <input
        className="ck-search"
        placeholder="filter: type, source or text…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="ck-scroll ck-raw" ref={follow.ref} onScroll={follow.onScroll}>
        {rows.map((e) => (
          <div key={e.id} className="ck-raw-row">
            <span className="ck-dim">{e.id}</span> <span className="ck-dim">{clock(e.ts)}</span>{" "}
            <span className="ck-raw-type">{e.type}</span> <span className="ck-tag">{e.proc || e.source}</span>{" "}
            <span className="ck-raw-body">{oneLine(e.payload, 400)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

// ─── schedule ────────────────────────────────────────────────

interface TimerRow {
  timer: string;
  service: string;
  description: string;
  enabled: string;
  active: string;
  schedule: string;
  next: number | null;
  next_text: string;
  last: number | null;
  last_text: string;
  service_state: string;
  result: string;
  exit_status: number | null;
  run_started: number | null;
  run_ended: number | null;
}

function Schedule({ feed, now }: { feed: FeedState; now: number }) {
  const { data, error, refresh } = usePolled<{ timers: TimerRow[]; error: string | null }>(
    "/api/cockpit/schedule",
    30_000
  );
  const [armed, setArmed] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(null), 4000);
    return () => window.clearTimeout(t);
  }, [armed]);

  // Latest job.end per job name, from the event log (has summaries).
  const lastJob = useMemo(() => {
    const m: Record<string, ChangeItem> = {};
    for (const c of feed.changes) if (c.type === "job" && c.kind === "job.end") m[c.source] = c;
    return m;
  }, [feed.changes]);

  const run = async (timer: string) => {
    if (armed !== timer) {
      setArmed(timer);
      return;
    }
    setArmed(null);
    setBusy(timer);
    setMsg(null);
    try {
      const r = await fetch(`${apiBase()}/api/cockpit/schedule/${encodeURIComponent(timer)}/run`, {
        method: "POST",
      });
      const j = await r.json().catch(() => ({}));
      setMsg(r.ok ? `started ${j.service}` : `failed: ${j.detail || r.status}`);
    } catch (e: any) {
      setMsg(`failed: ${e?.message || e}`);
    } finally {
      setBusy(null);
      window.setTimeout(refresh, 1500);
    }
  };

  const timers = data?.timers || [];
  return (
    <Panel
      title="Scheduled"
      className="ck-sched"
      right={
        <button className="ck-btn" onClick={refresh}>
          refresh
        </button>
      }
    >
      <div className="ck-scroll">
        {(error || data?.error) && <div className="ck-banner">schedule unavailable: {error || data?.error}</div>}
        {msg && <div className="ck-banner ck-banner-ok">{msg}</div>}
        {timers.map((t) => {
          const name = t.timer.replace(/\.timer$/, "");
          const job = lastJob[name.replace(/^jarvis-/, "")] || lastJob[name];
          const runningNow = t.service_state === "activating" || t.service_state === "active";
          const result = runningNow ? "running" : t.result === "success" ? "success" : t.result ? "error" : "idle";
          return (
            <div key={t.timer} className={`ck-timer ${t.enabled !== "enabled" ? "is-disabled" : ""}`}>
              <div className="ck-timer-main">
                <div className="ck-timer-name">
                  {name}
                  {t.enabled !== "enabled" && <span className="ck-tag ck-tag-soft">{t.enabled}</span>}
                </div>
                <div className="ck-dim ck-timer-desc">{t.description}</div>
                <div className="ck-timer-sched">{t.schedule || "—"}</div>
              </div>
              <div className="ck-timer-times">
                <div>
                  <span className="ck-dim">next</span>{" "}
                  {t.next ? (
                    <>
                      <b>{rel(t.next - now)}</b> <span className="ck-dim">{dayClock(t.next)}</span>
                    </>
                  ) : (
                    <span className="ck-dim">{t.next_text || "—"}</span>
                  )}
                </div>
                <div>
                  <span className="ck-dim">last</span>{" "}
                  {t.last ? rel(t.last - now) : <span className="ck-dim">{t.last_text || "never"}</span>}{" "}
                  <StatusPill status={result} />
                  {t.run_started && t.run_ended && t.run_ended >= t.run_started && (
                    <span className="ck-dim"> {fmtMs((t.run_ended - t.run_started) * 1000)}</span>
                  )}
                </div>
                {job && <div className="ck-timer-job">{job.summary}</div>}
              </div>
              <button
                className={`ck-btn ck-run ${armed === t.timer ? "is-armed" : ""}`}
                disabled={busy === t.timer}
                onClick={() => run(t.timer)}
                title={`systemctl --user start ${t.service}`}
              >
                {busy === t.timer ? "…" : armed === t.timer ? "confirm" : "run now"}
              </button>
            </div>
          );
        })}
        {data && timers.length === 0 && !data.error && <Empty>No timers matched.</Empty>}
      </div>
    </Panel>
  );
}

// ─── agents + changes ────────────────────────────────────────

interface AgentInfo {
  status: string;
  transport: string;
  description: string;
  detail: string;
  tools: string[];
}

function Agents({ feed }: { feed: FeedState }) {
  const { data } = usePolled<{ agents: Record<string, AgentInfo>; brain: any }>(
    "/api/cockpit/agents",
    60_000
  );
  const [focus, setFocus] = useState<string | null>(null);
  const [minSev, setMinSev] = useState("all");
  const follow = useFollow(feed.changes.length);
  const sevRank: Record<string, number> = { debug: 0, info: 1, notice: 2, warning: 3, error: 4, critical: 5 };
  const changes = feed.changes.filter(
    (c) =>
      (!focus || c.source === focus) &&
      (minSev === "all" || (sevRank[c.severity] ?? 1) >= (sevRank[minSev] ?? 0))
  );
  const lastBy: Record<string, number> = {};
  for (const c of feed.changes) lastBy[c.source] = c.ts;

  const agents = Object.entries(data?.agents || {}).sort(([a], [b]) => a.localeCompare(b));
  return (
    <Panel
      title="Agents & changes"
      className="ck-agents"
      right={<Seg value={minSev} options={["all", "notice", "warning", "error"]} onChange={setMinSev} />}
    >
      <div className="ck-roster">
        {agents.map(([name, a]) => (
          <button
            key={name}
            className={`ck-agent is-${a.status} ${focus === name ? "is-focus" : ""}`}
            title={[a.description, a.detail, a.tools.join(", ")].filter(Boolean).join("\n")}
            onClick={() => setFocus(focus === name ? null : name)}
          >
            <span className={`ck-dot ck-dot-${a.status}`} />
            {name}
            <span className="ck-dim">{a.tools.length}</span>
          </button>
        ))}
        {data?.brain && (
          <span className="ck-dim ck-brain">
            brain {data.brain.model} · ctx {data.brain.history} msgs
          </span>
        )}
      </div>
      <div className="ck-scroll" ref={follow.ref} onScroll={follow.onScroll}>
        {changes.map((c) => (
          <ChangeRow key={c.id} item={c} />
        ))}
        {changes.length === 0 && (
          <Empty>
            No changes yet. Sub-agents report here by POSTing to <code>/api/events</code>.
          </Empty>
        )}
      </div>
    </Panel>
  );
}

function ChangeRow({ item }: { item: ChangeItem }) {
  const [open, setOpen] = useState(false);
  const hasData = item.data != null && item.type === "agent";
  return (
    <div className={`ck-change sev-${item.severity}`}>
      <div className="ck-change-line" onClick={() => hasData && setOpen(!open)}>
        <span className="ck-dim">{clock(item.ts)}</span>
        <span className="ck-tag">{item.source}</span>
        <span className="ck-kind">{item.kind}</span>
        <span className="ck-summary">{item.summary}</span>
        {hasData && <span className="ck-glyph">{open ? "▾" : "▸"}</span>}
      </div>
      {open && <pre className="ck-change-data">{pretty(item.data)}</pre>}
    </div>
  );
}

// ─── small bits ──────────────────────────────────────────────

function Seg(p: { value: string; options: string[]; onChange: (v: string) => void }) {
  if (p.options.length <= 1) return null;
  return (
    <div className="ck-seg">
      {p.options.map((o) => (
        <button key={o} className={o === p.value ? "is-on" : ""} onClick={() => p.onChange(o)}>
          {o}
        </button>
      ))}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="ck-empty">{children}</div>;
}

// ─── root ────────────────────────────────────────────────────

export default function Cockpit() {
  const { feed, link } = useCockpitFeed();
  const now = useNow(1000);
  useEffect(() => {
    document.title = "JARVIS · Cockpit";
  }, []);
  return (
    <div className="ck-root">
      <Header feed={feed} link={link} now={now} />
      <main className="ck-grid">
        <Conversation feed={feed} now={now} />
        <ToolActivity feed={feed} now={now} />
        <div className="ck-col">
          <Schedule feed={feed} now={now} />
          <Agents feed={feed} />
        </div>
      </main>
    </div>
  );
}
