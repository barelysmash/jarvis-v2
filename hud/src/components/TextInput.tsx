import { useState, KeyboardEvent } from "react";
import { Send, Loader2 } from "lucide-react";
import { API_BASE } from "../lib/ws";
import type { MuseReviewContext } from "../lib/museReview";

interface TextInputProps {
  museReviewContext?: MuseReviewContext | null;
  onMessageSent?: (text: string) => void;
  onResponseReceived?: (text: string) => void;
}

export function TextInput({
  museReviewContext,
  onMessageSent,
  onResponseReceived,
}: TextInputProps) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async () => {
    const text = value.trim();
    if (!text || busy) return;

    const requestMuseReviewContext = museReviewContext;

    setError(null);
    setBusy(true);
    onMessageSent?.(text);
    setValue("");

    try {
      const resp = await fetch(`${API_BASE}/api/text`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          ...(museReviewContext
            ? {
                context: {
                  muse_review: museReviewContext,
                },
              }
            : {}),
        }),
      });

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const data = await resp.json();
      const reply = data.response || "[empty response]";
      onResponseReceived?.(reply);
    } catch (e: any) {
      setError(e.message || "Connection failed");
      onResponseReceived?.(`[error: ${e.message || "connection failed"}]`);
    } finally {
      setBusy(false);
    }
  };

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="bg-black/40 backdrop-blur-sm border border-cyan-500/20 rounded-sm">
      <div className="flex items-center gap-3 px-4 py-3">
        <div className="text-cyan-600 font-mono text-xs tracking-[0.2em] shrink-0">
          {busy ? "PROCESSING" : "INPUT"}
        </div>
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKey}
          disabled={busy}
          placeholder={busy ? "JARVIS is thinking..." : "Speak to JARVIS..."}
          autoFocus
          className="flex-1 bg-transparent text-cyan-100 placeholder-cyan-700 
                     font-mono text-sm outline-none border-b border-cyan-500/20 
                     focus:border-cyan-400 transition-colors py-1
                     disabled:opacity-50"
        />
        <button
          onClick={send}
          disabled={busy || !value.trim()}
          className="text-cyan-400 hover:text-cyan-300 disabled:text-cyan-800 
                     disabled:cursor-not-allowed transition-colors p-1"
          title="Send (Enter)"
        >
          {busy ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Send size={16} />
          )}
        </button>
      </div>
      {error && (
        <div className="px-4 pb-2 text-red-400 text-xs font-mono">
          {error}
        </div>
      )}
    </div>
  );
}
