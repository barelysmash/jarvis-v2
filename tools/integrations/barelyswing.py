"""BarelySwingTrade tool: lets JARVIS read the swing-trade book and arm/disarm
the autonomous auto-confirm engine.

The barelyswingtrade API runs on the same host (guildenstern), bound to
127.0.0.1:8421, so JARVIS reaches it directly over localhost — no auth, no
Tailscale. Read + arm-toggle only by design: position-mutating operations
(confirm/cancel/adjust-stop) are deliberately NOT exposed to the conversational
agent, matching the dashboard's restraint.

Register with BarelySwingAdapter().register(tools).
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("jarvis.barelyswing")

BASE_URL = os.environ.get("BARELYSWING_API", "http://127.0.0.1:8421")
TIMEOUT = 8.0


class BarelySwingAdapter:
    """Read + arm-toggle access to the barelyswingtrade engine."""

    def __init__(self, base_url: str | None = None):
        self.base = (base_url or BASE_URL).rstrip("/")

    # ─── helpers ──────────────────────────────────────────────
    def _get(self, path: str) -> dict:
        r = httpx.get(self.base + path, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = httpx.post(self.base + path, json=body, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _money(n) -> str:
        if n is None:
            return "n/a"
        return f"{'-' if n < 0 else ''}${abs(n):,.2f}"

    @staticmethod
    def _signed(n) -> str:
        if n is None:
            return "n/a"
        return f"{'+' if n >= 0 else '-'}${abs(n):,.2f}"

    # ─── tool: status (read) ──────────────────────────────────
    def status(self) -> str:
        """Compact summary of the swing book: equity, open positions with
        unrealized P&L, regime, and whether the engine is armed."""
        try:
            acc = self._get("/account")
            pos = self._get("/positions")
            reg = self._get("/regime")
            arm = self._get("/autoconfirm")
        except httpx.HTTPError as e:
            logger.warning("barelyswing API unreachable: %s", e)
            return (f"BarelySwingTrade API is unreachable ({e}). The VM or the "
                    "barelyswing-api service may be down.")

        lines = []
        eq = acc.get("equity")
        rp = acc.get("realized_pnl", 0) or 0
        up = acc.get("unrealized_pnl", 0) or 0
        day = (rp + up)
        lines.append(
            f"Equity: {self._money(eq)} "
            f"(realized {self._signed(rp)}, unrealized {self._signed(up)})."
        )

        plist = pos.get("positions", [])
        if plist:
            marks = {m["symbol"]: m for m in acc.get("open_marks", [])}
            parts = []
            for p in plist:
                ur = marks.get(p["symbol"], {}).get("unrealized")
                parts.append(f"{p['symbol']} {self._signed(ur)}" if ur is not None
                             else p["symbol"])
            lines.append(f"{len(plist)} open: " + ", ".join(parts) + ".")
        else:
            lines.append("No open positions.")

        tradeable = reg.get("is_tradeable")
        lines.append(
            f"Regime: {'tradeable' if tradeable else 'suppressed'} "
            f"(SPY ADX {reg.get('spy_adx')}, VIX {reg.get('vix')})."
        )
        lines.append(
            f"Auto-confirm engine: {'ARMED' if arm.get('enabled') else 'DISARMED'}."
        )
        return " ".join(lines)

    # ─── tool: set autoconfirm (arm/disarm) ───────────────────
    def set_autoconfirm(self, enabled: bool) -> str:
        """Arm (enabled=true) or disarm (enabled=false) the autonomous
        auto-confirm engine. When armed, it fills/cancels pending entries
        automatically on each scan."""
        try:
            res = self._post("/autoconfirm", {"enabled": bool(enabled)})
        except httpx.HTTPError as e:
            logger.warning("barelyswing autoconfirm toggle failed: %s", e)
            return f"Could not change the engine state ({e})."
        state = "ARMED" if res.get("enabled") else "DISARMED"
        return f"Auto-confirm engine is now {state}."

    # ─── registration (Tavily-style) ──────────────────────────
    def register(self, tools):
        tools.register(
            name="swing_status",
            description=(
                "Get the BarelySwingTrade paper-trading book status: live equity "
                "(realized + unrealized P&L), open positions with per-symbol "
                "unrealized P&L, market regime, and whether the autonomous "
                "auto-confirm engine is armed. Use for 'how's my swing book', "
                "'what's my trading account doing', 'is the engine armed', "
                "'how are my positions'."
            ),
            schema={"type": "object", "properties": {}},
            handler=lambda: self.status(),
        )
        tools.register(
            name="swing_set_autoconfirm",
            description=(
                "Arm or disarm the BarelySwingTrade autonomous auto-confirm "
                "engine. When ARMED, the engine automatically fills or cancels "
                "pending paper-trade entries on each scan. When DISARMED, no "
                "automatic action is taken. This controls live (paper) trade "
                "automation — confirm the user's intent before disarming or "
                "arming. Use for 'arm/disarm the engine', 'pause auto-trading', "
                "'turn the swing engine on/off'."
            ),
            schema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "true to arm, false to disarm.",
                    }
                },
                "required": ["enabled"],
            },
            handler=lambda enabled: self.set_autoconfirm(enabled),
        )
