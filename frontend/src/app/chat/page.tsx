"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface Message {
  role: "user" | "assistant";
  content: string;
  routing?: string;
  cache_hit?: boolean;
  cache_similarity?: number;
  token_count?: number;
}

interface ChatSession {
  id: string;
  title: string;
  provider: string;
  model: string;
  messages: Message[];
  created_at: string | null;
  updated_at: string | null;
}

interface ModelItem {
  id: string;
  provider: string;
}

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loadingSession, setLoadingSession] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState("llama-3.3-70b-versatile");
  const [provider, setProvider] = useState("groq");
  const [showHistory, setShowHistory] = useState(false);
  const [allModels, setAllModels] = useState<ModelItem[]>([]);
  const [modelSearch, setModelSearch] = useState("");
  const [showModelDropdown, setShowModelDropdown] = useState(false);
  const [autoRoute, setAutoRoute] = useState(true);
  const [liveTokenCount, setLiveTokenCount] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const modelDropdownRef = useRef<HTMLDivElement>(null);

  const activeSession = sessions.find((s) => s.id === activeId);
  const messages = activeSession?.messages ?? [];

  const filteredModels = modelSearch
    ? allModels.filter(
        (m) =>
          m.id.toLowerCase().includes(modelSearch.toLowerCase()) ||
          m.provider.toLowerCase().includes(modelSearch.toLowerCase())
      )
    : allModels;

  useEffect(() => {
    fetchSessions();
    fetch("/api/models")
      .then((r) => r.json())
      .then((data) => {
        const modelList = data.data || [];
        if (modelList.length === 0) return;
        const models: ModelItem[] = [];
        const seen = new Set<string>();
        for (const m of modelList) {
          const key = `${m.provider}/${m.id}`;
          if (seen.has(key)) continue;
          seen.add(key);
          models.push({
            id: m.id,
            provider: (m.provider || "unknown").toString(),
          });
        }
        models.sort((a, b) => a.provider.localeCompare(b.provider) || a.id.localeCompare(b.id));
        setAllModels(models);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target as Node)) {
        setShowModelDropdown(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const fetchSessions = async () => {
    try {
      const res = await fetch("/api/chat-history");
      const data = await res.json();
      const list: ChatSession[] = (data.sessions || []).map((s: Record<string, unknown>) => ({
        id: s.id as string,
        title: s.title as string,
        provider: (s.provider as string) || "groq",
        model: (s.model as string) || "llama-3.3-70b-versatile",
        messages: [],
        created_at: s.created_at as string | null,
        updated_at: s.updated_at as string | null,
      }));
      setSessions(list);
      if (list.length > 0) {
        const firstId = list[0].id;
        setActiveId(firstId);
        loadSessionDetail(firstId);
      }
    } catch {}
  };

  const loadSessionDetail = async (id: string) => {
    setLoadingSession(true);
    try {
      const res = await fetch(`/api/chat-history/${id}`);
      const data = await res.json();
      setSessions((prev) =>
        prev.map((s) =>
          s.id === id ? { ...s, messages: data.messages || [], provider: data.provider, model: data.model } : s
        )
      );
    } catch {}
    setLoadingSession(false);
  };

  const switchSession = (id: string) => {
    setActiveId(id);
    setShowHistory(false);
    const session = sessions.find((s) => s.id === id);
    if (session && session.messages.length === 0) {
      loadSessionDetail(id);
    }
    if (session) {
      setProvider(session.provider || "groq");
      setModel(session.model || "llama-3.3-70b-versatile");
    }
  };

  const selectModel = (m: ModelItem) => {
    setModel(m.id);
    setProvider(m.provider);
    setShowModelDropdown(false);
    setModelSearch("");
  };

  const createNewChat = async () => {
    try {
      const res = await fetch("/api/chat-history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New chat", provider, model }),
      });
      const data = await res.json();
      const newSession: ChatSession = {
        id: data.id,
        title: data.title,
        provider: data.provider,
        model: data.model,
        messages: [],
        created_at: data.created_at,
        updated_at: data.updated_at,
      };
      setSessions((prev) => [newSession, ...prev]);
      setActiveId(newSession.id);
      setShowHistory(false);
    } catch {}
  };

  const deleteSession = async (id: string) => {
    try {
      await fetch(`/api/chat-history/${id}`, { method: "DELETE" });
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== id);
        if (activeId === id) {
          const newActive = next.length > 0 ? next[0].id : null;
          setActiveId(newActive);
          if (newActive) loadSessionDetail(newActive);
        }
        return next;
      });
    } catch {}
  };

  const sendMessage = useCallback(async () => {
    if (!input.trim() || loading) return;

    let currentId = activeId;

    if (!currentId) {
      try {
        const res = await fetch("/api/chat-history", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "New chat", provider, model }),
        });
        const data = await res.json();
        currentId = data.id;
        const newSession: ChatSession = {
          id: data.id,
          title: data.title,
          provider: data.provider,
          model: data.model,
          messages: [],
          created_at: data.created_at,
          updated_at: data.updated_at,
        };
        setSessions((prev) => [newSession, ...prev]);
        setActiveId(data.id);
      } catch {
        return;
      }
    }

    const userMessage = input.trim();
    setInput("");
    const userMsg: Message = { role: "user", content: userMessage };

    setSessions((prev) =>
      prev.map((s) =>
        s.id === currentId
          ? {
              ...s,
              messages: [...s.messages, userMsg],
              title: s.messages.length === 0 ? userMessage.slice(0, 50) + (userMessage.length > 50 ? "..." : "") : s.title,
            }
          : s
      )
    );

    setLoading(true);
    setLiveTokenCount(0);

    try {
      await fetch(`/api/chat-history/${currentId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "user", content: userMessage }),
      });

      const currentMessages = sessions.find((s) => s.id === currentId)?.messages ?? [];
      const allMessages = [...currentMessages, userMsg];

      const headers: Record<string, string> = { "Content-Type": "application/json" };

      const res = await fetch("/api/chat", {
        method: "POST",
        headers,
        body: JSON.stringify({
          model: autoRoute ? "auto" : model,
          messages: allMessages,
          stream: true,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Request failed" }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let fullContent = "";
      let routing = "";
      let finalTokenCount = 0;

      if (reader) {
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("event: chunk")) {
              continue;
            }
            if (line.startsWith("data: ")) {
              const jsonStr = line.slice(6);
              if (!jsonStr || jsonStr === "{}") continue;
              try {
                const parsed = JSON.parse(jsonStr);
                if (parsed.token_count) {
                  finalTokenCount = parsed.token_count;
                  setLiveTokenCount(parsed.token_count);
                }
                const delta = parsed.choices?.[0]?.delta?.content;
                if (delta) {
                  fullContent += delta;
                  setSessions((prev) =>
                    prev.map((s) => {
                      if (s.id !== currentId) return s;
                      const msgs = [...s.messages];
                      const lastMsg = msgs[msgs.length - 1];
                      if (lastMsg && lastMsg.role === "assistant" && !lastMsg.routing) {
                        msgs[msgs.length - 1] = { ...lastMsg, content: fullContent };
                      } else {
                        msgs.push({ role: "assistant", content: fullContent });
                      }
                      return { ...s, messages: msgs };
                    })
                  );
                }
                if (parsed.routing_decision) {
                  routing = parsed.routing_decision;
                }
              } catch {}
            }
            if (line.startsWith("event: done")) {
              continue;
            }
            if (line.startsWith("event: error")) {
              continue;
            }
          }
        }
      }

      const assistantMsg: Message = {
        role: "assistant",
        content: fullContent || "No response",
        routing: routing || undefined,
        token_count: finalTokenCount || undefined,
      };

      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== currentId) return s;
          const msgs = s.messages.map((m) =>
            m.role === "assistant" && m.content === fullContent ? assistantMsg : m
          );
          if (!msgs.some((m) => m.role === "assistant")) {
            msgs.push(assistantMsg);
          }
          return { ...s, messages: msgs };
        })
      );

      await fetch(`/api/chat-history/${currentId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "assistant", content: fullContent || "No response" }),
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Unknown error";
      const error_msg: Message = { role: "assistant", content: `Error: ${errorMsg}` };
      setSessions((prev) =>
        prev.map((s) =>
          s.id === currentId ? { ...s, messages: [...s.messages, error_msg] } : s
        )
      );
    } finally {
      setLoading(false);
      setLiveTokenCount(0);
    }
  }, [input, loading, activeId, model, provider, sessions, autoRoute]);

  const formatDate = (iso: string | null) => {
    if (!iso) return "";
    const d = new Date(iso);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  };

  return (
    <div className="flex h-[calc(100vh-0px)]">
      <AnimatePresence>
        {showHistory && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setShowHistory(false)}
              className="md:hidden fixed inset-0 bg-white/10 z-40 backdrop-blur-sm"
            />
            <motion.div
              initial={{ x: -288 }}
              animate={{ x: 0 }}
              exit={{ x: -288 }}
              transition={{ type: "spring", stiffness: 400, damping: 30 }}
              className="fixed md:relative left-0 top-0 h-full w-72 md:w-72 border-r border-white/10 flex flex-col bg-black z-40"
            >
              <div className="p-4 border-b border-white/10">
                <motion.button
                  whileTap={{ scale: 0.97 }}
                  onClick={createNewChat}
                  className="w-full bg-white text-black px-4 py-2.5 text-sm font-medium hover:bg-zinc-200 transition-colors"
                >
                  + New Chat
                </motion.button>
              </div>
              <div className="flex-1 overflow-y-auto">
                <AnimatePresence>
                  {sessions.length === 0 ? (
                    <div className="p-4 text-sm text-zinc-600 text-center">No chats yet</div>
                  ) : (
                    sessions.map((s, idx) => (
                      <motion.div
                        key={s.id || idx}
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -20 }}
                        transition={{ delay: idx * 0.02 }}
                        onClick={() => switchSession(s.id)}
                        className={`flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-white/5 transition-colors group ${
                          activeId === s.id ? "bg-white/10" : ""
                        }`}
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-sm truncate">{s.title}</p>
                          <p className="text-xs text-zinc-600 mt-0.5">
                            {formatDate(s.updated_at)}
                          </p>
                        </div>
                        <button
                          onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }}
                          className="opacity-0 group-hover:opacity-100 text-zinc-600 hover:text-white text-xs ml-2 transition-opacity"
                        >
                          ×
                        </button>
                      </motion.div>
                    ))
                  )}
                </AnimatePresence>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="border-b border-white/10 p-3 sm:p-4">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-3 sm:gap-4 min-w-0">
              <motion.button
                whileTap={{ scale: 0.95 }}
                onClick={() => setShowHistory(!showHistory)}
                className="text-xs sm:text-sm text-zinc-400 hover:text-white border border-white/20 px-2 sm:px-3 py-1.5 transition-colors shrink-0"
              >
                {showHistory ? "Hide" : "History"}
              </motion.button>
              <div className="min-w-0">
                <h1 className="text-base sm:text-xl font-bold truncate">
                  {activeSession?.title || "New Chat"}
                </h1>
                <p className="text-[10px] sm:text-xs text-zinc-500 hidden sm:block">
                  {allModels.length} models available
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 sm:gap-3 shrink-0">
              <button
                onClick={() => setAutoRoute(!autoRoute)}
                className={`text-[10px] sm:text-xs px-2 py-1 border transition-colors ${
                  autoRoute ? "border-white/40 text-white bg-white/10" : "border-white/10 text-zinc-500"
                }`}
              >
                {autoRoute ? "AUTO" : "MANUAL"}
              </button>
              <div className="relative" ref={modelDropdownRef}>
                <button
                  onClick={() => setShowModelDropdown(!showModelDropdown)}
                  disabled={autoRoute}
                  className={`bg-black border px-2 sm:px-3 py-1.5 text-sm focus:outline-none focus:border-white/50 text-left min-w-0 sm:min-w-[280px] flex items-center justify-between gap-2 transition-colors ${
                    autoRoute ? "border-white/5 text-zinc-600 cursor-not-allowed" : "border-white/20"
                  }`}
                >
                  <span className="truncate font-mono text-xs">
                    <span className="text-zinc-500">{provider}/</span>{model}
                  </span>
                  <span className="text-zinc-600 text-xs">▼</span>
                </button>
                <AnimatePresence>
                  {showModelDropdown && !autoRoute && (
                    <motion.div
                      initial={{ opacity: 0, y: -8 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -8 }}
                      transition={{ duration: 0.15 }}
                      className="absolute right-0 top-full mt-1 w-[90vw] sm:w-[400px] max-w-[400px] bg-black border border-white/20 z-50 max-h-[60vh] sm:max-h-[400px] flex flex-col"
                    >
                      <div className="p-2 border-b border-white/10">
                        <input
                          type="text"
                          placeholder="Search models..."
                          value={modelSearch}
                          onChange={(e) => setModelSearch(e.target.value)}
                          className="w-full bg-transparent border border-white/10 px-3 py-1.5 text-sm focus:outline-none focus:border-white/50"
                          autoFocus
                        />
                      </div>
                      <div className="overflow-y-auto flex-1">
                        {filteredModels.length === 0 ? (
                          <div className="p-3 text-sm text-zinc-600 text-center">No models found</div>
                        ) : (
                          filteredModels.map((m) => (
                            <motion.button
                              key={`${m.provider}/${m.id}`}
                              whileHover={{ x: 4 }}
                              onClick={() => selectModel(m)}
                              className={`w-full text-left px-3 py-2 text-sm hover:bg-white/10 transition-colors flex items-center justify-between ${
                                model === m.id && provider === m.provider ? "bg-white/10" : ""
                              }`}
                            >
                              <span className="font-mono text-xs truncate">{m.id}</span>
                              <span className="text-xs text-zinc-500 ml-2 shrink-0">{m.provider}</span>
                            </motion.button>
                          ))
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-3 sm:p-4 space-y-3 sm:space-y-4">
          {loadingSession ? (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex flex-col items-center justify-center h-full text-zinc-600"
            >
              <p className="text-sm">Loading messages...</p>
            </motion.div>
          ) : messages.length === 0 ? (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.3 }}
              className="flex flex-col items-center justify-center h-full text-zinc-600"
            >
              <p className="text-lg mb-2">AI Inference Gateway</p>
              <p className="text-sm">Type a message to start chatting</p>
            </motion.div>
          ) : (
            <AnimatePresence>
              {messages.map((msg, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 12, scale: 0.98 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  transition={{ type: "spring", stiffness: 500, damping: 30 }}
                  className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
                >
                  <div
                    className={`max-w-[85%] sm:max-w-2xl px-3 sm:px-4 py-2.5 sm:py-3 text-sm ${
                      msg.role === "user" ? "bg-white text-black" : "border border-white/20"
                    }`}
                  >
                    <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                  </div>
                  {msg.role === "assistant" && msg.routing && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="flex items-center gap-3 mt-1 px-1"
                    >
                      <span className="text-[10px] text-zinc-600 font-mono">
                        {msg.routing}
                      </span>
                      {msg.cache_hit && (
                        <span className="text-[10px] text-zinc-500 border border-white/10 px-1.5 py-0.5">
                          CACHED {msg.cache_similarity ? `${(msg.cache_similarity * 100).toFixed(0)}%` : ""}
                        </span>
                      )}
                      {msg.token_count !== undefined && msg.token_count > 0 && (
                        <span className="text-[10px] text-zinc-500">
                          {msg.token_count} chunks
                        </span>
                      )}
                    </motion.div>
                  )}
                </motion.div>
              ))}
            </AnimatePresence>
          )}
          {loading && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex justify-start"
            >
              <div className="border border-white/20 px-4 py-3 text-sm text-zinc-400">
                <span className="animate-pulse">Thinking...</span>
                {liveTokenCount > 0 && (
                  <span className="ml-2 text-zinc-500 text-xs">
                    {liveTokenCount} chunks streamed
                  </span>
                )}
              </div>
            </motion.div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="border-t border-white/10 p-3 sm:p-4">
          <div className="flex gap-2 sm:gap-4">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage()}
              placeholder="Type a message..."
              className="flex-1 bg-transparent border border-white/20 px-3 sm:px-4 py-2.5 sm:py-3 text-sm focus:outline-none focus:border-white/50 min-w-0"
              disabled={loading}
            />
            <motion.button
              whileTap={{ scale: 0.95 }}
              onClick={sendMessage}
              disabled={loading || !input.trim()}
              className="bg-white text-black px-4 sm:px-6 py-2.5 sm:py-3 text-sm font-medium hover:bg-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors shrink-0"
            >
              Send
            </motion.button>
          </div>
        </div>
      </div>
    </div>
  );
}
