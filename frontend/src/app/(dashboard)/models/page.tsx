"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";

interface Model {
  id: string;
  provider: string;
  name: string;
  context_length?: number;
  pricing?: { prompt: string; completion: string };
}

export default function Models() {
  const [models, setModels] = useState<Model[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/models")
      .then((r) => r.json())
      .then((d) => {
        const raw: Model[] = d.data || d.models || [];
        const seen = new Set<string>();
        const unique: Model[] = [];
        for (const m of raw) {
          const key = `${m.provider}/${m.id}`;
          if (seen.has(key)) continue;
          seen.add(key);
          unique.push(m);
        }
        setModels(unique);
      })
      .catch(() => {});
  }, []);

  const filtered = models.filter(
    (m) =>
      m.id.toLowerCase().includes(search.toLowerCase()) ||
      m.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="p-4 sm:p-6 md:p-8">
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-5 sm:mb-6"
      >
        <h1 className="text-2xl sm:text-3xl font-bold">Models</h1>
        <p className="text-zinc-500 mt-1 text-sm">
          {models.length} models available across all providers
        </p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.15 }}
        className="mb-5 sm:mb-6"
      >
        <input
          type="text"
          placeholder="Search models..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full sm:max-w-md bg-black border border-white/10 px-4 py-2.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-white/30"
        />
      </motion.div>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.25 }}
        className="border border-white/10 overflow-hidden"
      >
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-zinc-500 text-left">
                <th className="px-4 py-3 font-medium">Model ID</th>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Provider</th>
                <th className="px-4 py-3 font-medium">Context</th>
                <th className="px-4 py-3 font-medium">Pricing (per 1M)</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((m, i) => (
                <motion.tr
                  key={`${m.provider}/${m.id}`}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: Math.min(i * 0.02, 0.4) }}
                  onClick={() => setSelected(selected === m.id ? null : m.id)}
                  className={`border-b border-white/5 cursor-pointer transition-colors ${
                    selected === m.id ? "bg-white/10" : "hover:bg-white/5"
                  }`}
                >
                  <td className="px-4 py-3 font-mono text-xs">{m.id}</td>
                  <td className="px-4 py-3 text-zinc-300">{m.name}</td>
                  <td className="px-4 py-3 capitalize">{m.provider}</td>
                  <td className="px-4 py-3 text-zinc-400">
                    {m.context_length ? `${(m.context_length / 1000).toFixed(0)}K` : "—"}
                  </td>
                  <td className="px-4 py-3 text-zinc-400 text-xs">
                    {m.pricing?.prompt ? `$${m.pricing.prompt}` : "—"} /{" "}
                    {m.pricing?.completion ? `$${m.pricing.completion}` : "—"}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile cards */}
        <div className="md:hidden divide-y divide-white/5">
          {filtered.map((m, i) => (
            <motion.div
              key={`${m.provider}/${m.id}`}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(i * 0.03, 0.4) }}
              onClick={() => setSelected(selected === m.id ? null : m.id)}
              className={`p-3 cursor-pointer transition-colors ${
                selected === m.id ? "bg-white/10" : "hover:bg-white/5"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-xs text-white truncate">{m.id}</p>
                  <p className="text-xs text-zinc-400 mt-1 truncate">{m.name}</p>
                </div>
                <span className="text-[10px] text-zinc-500 uppercase border border-white/10 px-1.5 py-0.5 shrink-0">
                  {m.provider}
                </span>
              </div>
              <div className="flex items-center gap-4 mt-2 text-xs text-zinc-500">
                {m.context_length && <span>{(m.context_length / 1000).toFixed(0)}K ctx</span>}
                {m.pricing?.prompt && <span>${m.pricing.prompt}/1M in</span>}
                {m.pricing?.completion && <span>${m.pricing.completion}/1M out</span>}
              </div>
            </motion.div>
          ))}
        </div>

        {filtered.length === 0 && (
          <div className="p-8 text-center text-zinc-500 text-sm">No models match your search</div>
        )}
      </motion.div>
    </div>
  );
}
