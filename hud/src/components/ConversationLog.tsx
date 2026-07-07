import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useRef } from "react";
import type { Message } from "../hooks/useJarvisState";

export function ConversationLog({ messages }: { messages: Message[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  return (
    <div className="h-full flex flex-col bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm">
      <div className="px-4 py-2 border-b border-cyan-500/20">
        <div className="text-cyan-400 text-xs font-mono tracking-[0.2em]">
          CONVERSATION
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-cyan-700 text-xs font-mono italic">
            Awaiting input...
          </div>
        )}
        <AnimatePresence>
          {messages.map((msg) => (
            <motion.div
              key={msg.id}
              initial={{ opacity: 0, x: msg.role === "user" ? 20 : -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.3 }}
              className={msg.role === "user" ? "text-right" : ""}
            >
              <div className="text-[10px] font-mono text-cyan-700 mb-1">
                {msg.role === "user" ? "USER" : "JARVIS"} · {msg.timestamp}
              </div>
              <div
                className={`inline-block max-w-[90%] px-3 py-2 text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-cyan-500/10 text-cyan-100 border-l-2 border-cyan-400"
                    : "bg-amber-500/5 text-amber-100 border-l-2 border-amber-400"
                }`}
              >
                {msg.text}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
