"""FastAPI + WebSocket server."""

import os
import asyncio
import logging
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from orchestrator.brain import JarvisBrain
from orchestrator.memory.store import MemoryStore
from orchestrator.tools import ToolRegistry

from .events import bus, emit_jarvis_speech, emit_state, emit_user_speech

logger = logging.getLogger(__name__)

# Publisher log visibility: uvicorn's --log-level configures uvicorn's loggers
# only. Without a root handler, every logger.info() in this app is dropped.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

# Global instances (populated in lifespan)
brain: Optional[JarvisBrain] = None
memory: Optional[MemoryStore] = None
tools: Optional[ToolRegistry] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global brain, memory, tools
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    memory = MemoryStore(data_dir=os.environ.get("JARVIS_DATA_DIR", "./data"))
    tools = ToolRegistry()

    # Optional: register Google Calendar if configured
    if os.path.exists("config/google/token.json"):
        try:
            from tools.integrations.calendar import GoogleCalendar
            calendar = GoogleCalendar(
                timezone_name=os.environ.get(
                    "JARVIS_TIMEZONE", "America/Chicago"
                )
            )
            tools.register_calendar(calendar)
        except Exception as exc:
            print(f"[server] Calendar registration failed: {exc}")

    # Register web search if API key available
    if os.environ.get("TAVILY_API_KEY"):
        try:
            from tools.integrations.web_search import TavilyAdapter
            TavilyAdapter().register(tools)
            print("[server] Web search (Tavily) loaded")
        except Exception as exc:
            print(f"[server] Web search registration failed: {exc}")
    # Register BarelySwingTrade (read book + arm/disarm engine; same-host API)
    try:
        from tools.integrations.barelyswing import BarelySwingAdapter
        BarelySwingAdapter().register(tools)
        print("[server] BarelySwingTrade tools loaded")
    except Exception as exc:
        print(f"[server] BarelySwing registration failed: {exc}")

    brain = JarvisBrain(
        api_key=api_key,
        user_name=os.environ.get("JARVIS_USER_NAME", "Sir"),
        memory=memory,
        tools=tools,
    )

    # Initialize event log table
    from orchestrator import event_log
    event_log.init_db()

    # Start background event poller
    poller_task = asyncio.create_task(_event_log_poller())
    metrics_task = asyncio.create_task(_system_metrics_publisher())
    stocks_task = asyncio.create_task(_stocks_publisher())
    news_task = asyncio.create_task(_news_publisher())
    weather_task = asyncio.create_task(_weather_publisher())
    calendar_task = asyncio.create_task(_calendar_publisher())

    yield

    # Shutdown
    poller_task.cancel()
    metrics_task.cancel()
    stocks_task.cancel()
    news_task.cancel()
    weather_task.cancel()
    calendar_task.cancel()
    for task in (poller_task, metrics_task, stocks_task, news_task, weather_task, calendar_task):
        try:
            await task
        except asyncio.CancelledError:
            pass

# Last-known widget event per widget name; replayed to new WS clients so
# fresh connections render real data immediately instead of waiting out
# each publisher's broadcast cycle (up to 10 min for stocks/news).
last_widget_events: dict[str, dict] = {}

app = FastAPI(lifespan=lifespan, title="JARVIS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    queue = bus.subscribe()

    await ws.send_json(
        {
            "type": "snapshot",
            "data": {
                "state": "idle",
                "uptime_seconds": 0,
                "version": "1.0.0",
                "name": "JARVIS",
            },
        }
    )

    # Replay last-known widget states to the new client so it renders
    # real data immediately instead of waiting out publisher cycles.
    for cached in list(last_widget_events.values()):
        try:
            await ws.send_json(cached)
        except Exception:
            break
    try:
        while True:
            event = await queue.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(queue)


@app.post("/api/text")
async def text_input(payload: dict):
    """Type a message to JARVIS instead of speaking."""
    import asyncio

    text = payload.get("text", "")
    await emit_user_speech(text)
    await emit_state("thinking")

    # Run brain in a thread so we don't block the event loop -
    # this lets emit_tool_event events flow through while the brain runs.
    response = await asyncio.to_thread(brain.think_and_act, text)

    await emit_state("speaking")
    await emit_jarvis_speech(response)

    # Hold "speaking" briefly so it's visible in the HUD orb
    await asyncio.sleep(0.8)
    await emit_state("idle")

    return {"response": response}


@app.get("/api/status")
async def status():
    return {
        "brain": "online" if brain else "offline",
        "memory": {
            "facts": (
                len(memory.semantic.collection.get()["ids"])
                if memory
                else 0
            ),
            "episodes": (
                memory.episodic.count() if memory else 0
            ),
        },
        "tools": list(tools._tools.keys()) if tools else [],
    }

@app.get("/api/memory")
async def memory_recent(facts: int = 5, episodes: int = 5):
    """Return recent memory contents for the HUD widget."""
    if not memory:
        return {"facts": [], "episodes": []}

    # Latest facts (newest first)
    try:
        all_facts = memory.semantic.collection.get()
        # Sort by created_at if present, else by insertion order
        fact_pairs = list(zip(
            all_facts.get("documents", []),
            all_facts.get("metadatas", []),
        ))
        # Sort descending by created_at
        fact_pairs.sort(
            key=lambda p: (p[1] or {}).get("created_at", ""),
            reverse=True,
        )
        recent_facts = [doc for doc, _ in fact_pairs[:facts]]
    except Exception:
        recent_facts = []

    # Latest episodes
    try:
        import sqlite3
        with sqlite3.connect(memory.episodic.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT timestamp, user_input, response FROM episodes "
                "ORDER BY timestamp DESC LIMIT ?",
                (episodes,),
            ).fetchall()
            recent_episodes = [
                {
                    "time": r["timestamp"][11:16],
                    "user": r["user_input"][:60],
                    "jarvis": r["response"][:60],
                }
                for r in rows
            ]
    except Exception:
        recent_episodes = []

    return {"facts": recent_facts, "episodes": recent_episodes}

@app.get("/api/health")
async def health():
    return {"status": "healthy"}

async def _event_log_poller():
    """Poll the SQLite event log and broadcast new entries to WS subscribers."""
    import time
    from orchestrator import event_log
    from server.events import bus

    last_ts = time.time()  # Only broadcast events after server start

    while True:
        try:
            await asyncio.sleep(0.5)
            new_events = event_log.fetch_since(last_ts, limit=50)
            if not new_events:
                continue

            for evt in new_events:
                last_ts = max(last_ts, evt["ts"])

                # Only broadcast events that didn't originate in this process
                # (the brain emits to bus directly already).
                if evt["source"] == "brain":
                    continue

                bus_event = {
                    "type": evt["type"],
                    "timestamp": evt["iso"],
                    "data": evt["payload"],
                }
                for q in list(bus.subscribers):
                    try:
                        q.put_nowait(bus_event)
                    except asyncio.QueueFull:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Event poller error: %s", e)
            await asyncio.sleep(1.0)


async def _system_metrics_publisher():
    """Broadcast CPU / RAM / DISK metrics every 2s for the HUD system rings.

    Reads psutil and pushes a widget event keyed `system` with payload
    shape `{cpu, ram, disk}`. The HUD's CPURamRings and DiskEnergy
    components are already wired to consume `widgets.system` and will
    swap from their random-walk fallback to live values as soon as the
    first event arrives.
    """
    import psutil
    from datetime import datetime, timezone

    # First call to cpu_percent(None) returns 0.0; subsequent calls return
    # the delta since the previous call. Prime it before the loop.
    psutil.cpu_percent(None)

    while True:
        try:
            await asyncio.sleep(2.0)
            payload = {
                "cpu":  round(psutil.cpu_percent(None), 1),
                "ram":  round(psutil.virtual_memory().percent, 1),
                "disk": round(psutil.disk_usage("/").percent, 1),
            }
            event = {
                "type": "widget",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {"widget": "system", "data": payload},
            }
            last_widget_events["system"] = event
            for q in list(bus.subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("System metrics publisher error: %s", exc)
            await asyncio.sleep(2.0)


async def _stocks_publisher():
    """Broadcast market quotes + 5-day bar series every 10 min.

    Equity index proxies (SPY/QQQ/DIA/etc) come from Alpaca IEX with auth.
    Spot crypto (BTC/USD, ETH/USD) comes from Alpaca's free crypto endpoint
    which needs no auth but is conventionally called with the same client init.
    """
    import asyncio
    import os
    from datetime import date, datetime, timedelta, timezone

    api_key = os.environ.get("ALPACA_API_KEY")
    api_secret = os.environ.get("ALPACA_API_SECRET")
    if not api_key or not api_secret:
        logger.warning("Alpaca credentials missing — stocks publisher disabled")
        return

    from alpaca.data.historical import (
        StockHistoricalDataClient,
        CryptoHistoricalDataClient,
    )
    from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    stock_client = StockHistoricalDataClient(api_key, api_secret)
    crypto_client = CryptoHistoricalDataClient()  # crypto data is unauthenticated
    logger.info("_stocks_publisher started")

    # Display label → ETF/ticker. Crypto pairs are in their own map because they
    # use a different fetch endpoint.
    STOCK_TICKER_MAP = [
        ("S&P",  "SPY"),
        ("NDX",  "QQQ"),
        ("DJI",  "DIA"),
        ("VIX",  "VIXY"),
        ("RUT",  "IWM"),
        ("10Y",  "TLT"),
        ("WTI",  "USO"),
        ("GOLD", "GLD"),
    ]
    CRYPTO_TICKER_MAP = [
        ("BTC", "BTC/USD"),
        ("ETH", "ETH/USD"),
    ]
    STOCK_CHART_MAP = [
        ("S&P", "SPY"),
        ("NDX", "QQQ"),
        ("DJI", "DIA"),
        ("NKY", "EWJ"),
    ]
    CRYPTO_CHART_MAP = [
        ("BTC", "BTC/USD"),
    ]

    all_stocks = sorted({s for _, s in STOCK_TICKER_MAP} | {s for _, s in STOCK_CHART_MAP})
    all_crypto = sorted({s for _, s in CRYPTO_TICKER_MAP} | {s for _, s in CRYPTO_CHART_MAP})

    while True:
        try:
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=14)
            start_dt = datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc)
            end_dt = datetime.combine(end, datetime.max.time()).replace(tzinfo=timezone.utc)

            # Equities: IEX feed, "yesterday or earlier" only
            stock_req = StockBarsRequest(
                symbol_or_symbols=all_stocks,
                timeframe=TimeFrame.Day,
                start=start_dt,
                end=end_dt,
                feed=DataFeed.IEX,
            )
            stock_resp = await asyncio.to_thread(stock_client.get_stock_bars, stock_req)

            # Crypto trades 24/7, so we can ask for up to "right now"
            crypto_end = datetime.now(timezone.utc)
            crypto_start = crypto_end - timedelta(days=14)
            crypto_req = CryptoBarsRequest(
                symbol_or_symbols=all_crypto,
                timeframe=TimeFrame.Day,
                start=crypto_start,
                end=crypto_end,
            )
            crypto_resp = await asyncio.to_thread(crypto_client.get_crypto_bars, crypto_req)

            closes_by_sym: dict[str, list[float]] = {}
            last_bar_by_sym: dict[str, str] = {}
            for sym in all_stocks:
                bars = stock_resp.data.get(sym, [])
                ordered = sorted([(b.timestamp, float(b.close)) for b in bars], key=lambda p: p[0])
                closes_by_sym[sym] = [c for _, c in ordered]
                last_bar_by_sym[sym] = ordered[-1][0].date().isoformat() if ordered else "NONE"
            for sym in all_crypto:
                bars = crypto_resp.data.get(sym, [])
                ordered = sorted([(b.timestamp, float(b.close)) for b in bars], key=lambda p: p[0])
                closes_by_sym[sym] = [c for _, c in ordered]
                last_bar_by_sym[sym] = ordered[-1][0].date().isoformat() if ordered else "NONE"

            diag = []
            for s in (all_stocks + all_crypto):
                closes = closes_by_sym.get(s, [])
                lastc = f"{closes[-1]:.2f}" if closes else "NA"
                diag.append(f"{s}:{last_bar_by_sym.get(s, '?')} n={len(closes)} last={lastc}")
            logger.info("stocks diag: %s", "; ".join(diag))

            # Scrolling ticker payload
            stocks_payload = []
            for label, sym in (STOCK_TICKER_MAP + CRYPTO_TICKER_MAP):
                closes = closes_by_sym.get(sym, [])
                if len(closes) < 2:
                    continue
                current, prev = closes[-1], closes[-2]
                change_pct = ((current - prev) / prev * 100.0) if prev else 0.0
                stocks_payload.append({
                    "symbol": label,
                    "value": f"{current:,.2f}",
                    "changePct": round(change_pct, 2),
                })

            # Bar-chart panel payload
            markets_payload = []
            for label, sym in (STOCK_CHART_MAP + CRYPTO_CHART_MAP):
                closes = closes_by_sym.get(sym, [])
                if len(closes) < 2:
                    continue
                bars = closes[-5:] if len(closes) >= 5 else closes
                current, prev = closes[-1], closes[-2]
                change_pct = ((current - prev) / prev * 100.0) if prev else 0.0
                markets_payload.append({
                    "symbol": label,
                    "value": f"{current:,.2f}",
                    "changePct": round(change_pct, 2),
                    "bars": bars,
                })

            ts = datetime.now(timezone.utc).isoformat()
            for widget_name, payload in (("stocks", stocks_payload),
                                          ("markets", markets_payload)):
                if not payload:
                    continue
                event = {
                    "type": "widget",
                    "timestamp": ts,
                    "data": {"widget": widget_name, "data": payload},
                }
                last_widget_events[widget_name] = event
                for q in list(bus.subscribers):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

            logger.info(
                "Stocks broadcast: %d ticker, %d markets, %d subscribers",
                len(stocks_payload), len(markets_payload), len(bus.subscribers),
            )
            await asyncio.sleep(600)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Stocks publisher error: %s", exc)
            await asyncio.sleep(60)


async def _news_publisher():
    """Broadcast top headlines every 10 min from AP / Reuters / BBC RSS."""
    import asyncio
    from datetime import datetime, timezone

    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser not installed — news publisher disabled")
        return

    FEEDS = [
        ("AP",   "https://feeds.apnews.com/rss/apf-topnews"),
        ("AP",   "https://feeds.apnews.com/rss/apf-business"),
        ("RTRS", "https://feeds.reuters.com/reuters/topNews"),
        ("RTRS", "https://feeds.reuters.com/reuters/businessNews"),
        ("BBC",  "http://feeds.bbci.co.uk/news/rss.xml"),
        ("BBC",  "http://feeds.bbci.co.uk/news/business/rss.xml"),
        ("BBC",  "http://feeds.bbci.co.uk/news/technology/rss.xml"),
    ]
    MAX_HEADLINES = 12
    FEED_TIMEOUT_SEC = 20
    logger.info("_news_publisher started")

    while True:
        try:
            seen: set[str] = set()
            items: list[dict] = []

            for category, url in FEEDS:
                try:
                    async with asyncio.timeout(FEED_TIMEOUT_SEC):
                        feed = await asyncio.to_thread(feedparser.parse, url)
                    if feed.get("bozo") and not feed.entries:
                        continue
                    for entry in feed.entries[:5]:
                        title = (entry.get("title") or "").strip()
                        if len(title) < 10:
                            continue
                        key = title.lower()[:80]
                        if key in seen:
                            continue
                        seen.add(key)
                        items.append({"category": category, "headline": title})
                except (asyncio.TimeoutError, TimeoutError):
                    logger.info("RSS timeout: %s", url)
                except Exception as exc:
                    logger.warning("RSS feed %s failed: %s", url, exc)

            items = items[:MAX_HEADLINES]
            if items:
                event = {
                    "type": "widget",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {"widget": "news", "data": items},
                }
                last_widget_events["news"] = event
                for q in list(bus.subscribers):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

            logger.info(
                "News broadcast: %d headlines, %d subscribers",
                len(items), len(bus.subscribers),
            )
            await asyncio.sleep(600)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("News publisher error: %s", exc)
            await asyncio.sleep(60)
            
async def _weather_publisher():
    """Broadcast current conditions + forecast every 15 min (Open-Meteo, keyless)."""
    import json
    import urllib.request
    from datetime import datetime, timezone

    lat = os.environ.get("JARVIS_WEATHER_LAT", "30.2672")
    lon = os.environ.get("JARVIS_WEATHER_LON", "-97.7431")
    label = os.environ.get("JARVIS_WEATHER_LABEL", "Austin")
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,is_day,weather_code,"
        "wind_speed_10m,wind_direction_10m"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,"
        "precipitation_probability_max"
        "&temperature_unit=fahrenheit&wind_speed_unit=mph"
        "&timezone=auto&forecast_days=5"
    )

    WMO = {0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
           45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
           61: "Rain", 63: "Rain", 65: "Heavy Rain", 66: "Freezing Rain",
           67: "Freezing Rain", 71: "Snow", 73: "Snow", 75: "Heavy Snow",
           77: "Snow", 80: "Showers", 81: "Showers", 82: "Heavy Showers",
           85: "Snow Showers", 86: "Snow Showers", 95: "Thunderstorm",
           96: "Thunderstorm", 99: "Thunderstorm"}

    def _compass(deg: float) -> str:
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[int((deg + 22.5) // 45) % 8]

    logger.info("_weather_publisher started (%s)", label)
    while True:
        try:
            def _fetch():
                with urllib.request.urlopen(url, timeout=20) as r:
                    return json.loads(r.read().decode())
            d = await asyncio.to_thread(_fetch)
            cur = d["current"]
            daily = d["daily"]
            precip = daily.get("precipitation_probability_max") or [None] * 5
            days = []
            for i in range(len(daily["time"])):
                dt = datetime.fromisoformat(daily["time"][i])
                days.append({
                    "day": dt.strftime("%a").upper(),
                    "high": round(daily["temperature_2m_max"][i]),
                    "low": round(daily["temperature_2m_min"][i]),
                    "condition": WMO.get(daily["weather_code"][i], "?"),
                    "precip_pct": precip[i],
                })
            payload = {
                "location": label,
                "temp": round(cur["temperature_2m"]),
                "condition": WMO.get(cur["weather_code"], "?"),
                "humidity": cur["relative_humidity_2m"],
                "wind_mph": round(cur["wind_speed_10m"]),
                "wind_dir": _compass(cur["wind_direction_10m"]),
                "is_day": bool(cur["is_day"]),
                "high": days[0]["high"] if days else None,
                "low": days[0]["low"] if days else None,
                "forecast": days[1:5],
            }
            event = {
                "type": "widget",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {"widget": "weather", "data": payload},
            }
            last_widget_events["weather"] = event
            for q in list(bus.subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
            logger.info("Weather broadcast: %s %sF, %d subscribers",
                        payload["condition"], payload["temp"],
                        len(bus.subscribers))
            await asyncio.sleep(900)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Weather publisher error: %s", exc)
            await asyncio.sleep(120)


async def _calendar_publisher():
    """Broadcast today's schedule every 15 min for the HUD Schedule panel.

    Foundation-layer plumbing per JAM: presents calendar Observations,
    no reasoning. Reuses the registered calendar tool so OAuth and error
    normalization stay in one place. Disabled cleanly if the calendar
    integration isn't configured on this host.
    """
    from datetime import datetime, timezone

    await asyncio.sleep(2.0)  # let lifespan finish registration
    if tools is None or "calendar_list_events" not in tools._tools:
        logger.info("_calendar_publisher: calendar tool not registered -- disabled")
        return

    logger.info("_calendar_publisher started")
    while True:
        try:
            result, is_error = await asyncio.to_thread(
                tools.execute, "calendar_list_events", {"days_ahead": 7}
            )
            if is_error:
                logger.warning("Calendar publisher: tool error: %s", result)
                await asyncio.sleep(300)
                continue
            events = []
            for e in (result or [])[:8]:
                if not isinstance(e, dict):
                    continue
                events.append({
                    "title": e.get("title") or e.get("summary") or "(untitled)",
                    "human_time": e.get("human_time") or e.get("start") or "",
                    "location": e.get("location"),
                })
            event = {
                "type": "widget",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {"widget": "schedule", "data": {"events": events}},
            }
            last_widget_events["schedule"] = event
            for q in list(bus.subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
            logger.info("Schedule broadcast: %d events, %d subscribers",
                        len(events), len(bus.subscribers))
            await asyncio.sleep(900)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Calendar publisher error: %s", exc)
            await asyncio.sleep(300)


# Serve built HUD. Mounted last so /ws and /api/* match first.
HUD_DIST = Path(__file__).resolve().parents[1] / "hud" / "dist"
if HUD_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(HUD_DIST), html=True), name="hud")
else:
    logger.warning("HUD dist not found at %s — serving API only", HUD_DIST)