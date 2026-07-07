// WebSocket connection helpers
export const WS_URL =
  (import.meta as any).env?.VITE_WS_URL || "ws://127.0.0.1:28765/ws";

export const API_BASE =
  (import.meta as any).env?.VITE_API_BASE || "http://127.0.0.1:28765";
