// WebSocket / API connection helpers.
//
// Defaults are SAME-ORIGIN: whatever host:port serves this page is also
// used for the API and WebSocket. That means the built HUD works wherever
// it's served from — http://100.113.110.44:8765, an SSH tunnel on
// localhost, a hostname via MagicDNS, etc. — with no rebuild.
//
// You can still override at build time for split deployments:
//   VITE_API_BASE=http://my-host:8765 VITE_WS_URL=ws://my-host:8765/ws npm run build

function sameOriginHttp(): string {
  if (typeof window === "undefined") return "http://127.0.0.1:8765";
  return `${window.location.protocol}//${window.location.host}`;
}

function sameOriginWs(): string {
  if (typeof window === "undefined") return "ws://127.0.0.1:8765/ws";
  const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${wsProto}//${window.location.host}/ws`;
}

export const API_BASE =
  (import.meta as any).env?.VITE_API_BASE || sameOriginHttp();

export const WS_URL =
  (import.meta as any).env?.VITE_WS_URL || sameOriginWs();
