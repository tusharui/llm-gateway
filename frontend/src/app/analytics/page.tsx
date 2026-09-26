"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import type {
  AnalyticsSummary,
  ModelUsageResponse,
  ProviderUsageResponse,
  RecentRequestRow,
} from "../../types/backend";

interface RecentRequest extends RecentRequestRow {
  status: string;
}

type ModelUsage = ModelUsageResponse["models"][number];
type ProviderBreakdown = ProviderUsageResponse["providers"][number];

const stagger = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.08 } },
};
const fadeUp = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0 },
};

function fmtUsd(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n >= 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(6)}`;
}

export default function Analytics() {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [byModel, setByModel] = useState<ModelUsage[]>([]);
  const [byProvider, setByProvider] = useState<ProviderBreakdown[]>([]);
  const [recent, setRecent] = useState<RecentRequest[]>([]);
  const [live, setLive] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [days, setDays] = useState("7");

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      const q = `?days=${days}`;
      const [s, m, p, r] = await Promise.all([
        fetch(`/api/analytics/summary${q}`).then((x) => x.json()).catch(() => null),
        fetch(`/api/analytics/by-model${q}`).then((x) => x.json()).catch(() => null),
        fetch(`/api/analytics/by-provider${q}`).then((x) => x.json()).catch(() => null),
        fetch("/api/analytics/recent?limit=50").then((x) => x.json()).catch(() => null),
      ]);
      if (cancelled) return;
      if (s && !s.error) {
        setSummary(s);
        setLastUpdated(new Date());
      }
      if (m?.models) setByModel(m.models);
      if (p?.providers) setByProvider(p.providers);
      if (r?.requests) {
        setRecent(r.requests.map((x: RecentRequestRow) => ({ ...x, status: x.success ? "success" : "failed" })));
        setLastUpdated(new Date());
      }
    };

    void load();
    if (!live) return () => { cancelled = true; };

    const id = setInterval(load, 5000);
    // Refresh immediately when the tab regains focus so numbers are never stale.
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [live, days]);

  return (
    <div className="p-4 sm:p-6 md:p-8">
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-6 md:mb-8 flex flex-wrap items-end justify-between gap-3"
      >
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">Analytics</h1>
          <p className="text-zinc-500 mt-1 text-sm">Usage, cost and cache savings</p>
        </div>
        <div className="flex items-center gap-2 sm:gap-3">
          <select
            value={days}
            onChange={(e) => setDays(e.target.value)}
            className="bg-black border border-white/20 px-2 sm:px-3 py-1.5 text-xs sm:text-sm focus:outline-none focus:border-white/50"
            aria-label="Time range"
          >
            <option value="1">Last 24h</option>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="90">Last 90 days</option>
          </select>
          <button
            onClick={() => setLive((v) => !v)}
            className={`flex items-center gap-1.5 border px-2 sm:px-3 py-1.5 text-xs sm:text-sm transition-colors ${
              live ? "border-white/40 text-white bg-white/10" : "border-white/10 text-zinc-500"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${live ? "bg-green-400 animate-pulse" : "bg-zinc-600"}`} />
            {live ? "LIVE" : "PAUSED"}
          </button>
          {lastUpdated && (
            <span className="text-[10px] sm:text-xs text-zinc-600 font-mono hidden sm:inline">
              {lastUpdated.toLocaleTimeString()}
            </span>
          )}
        </div>
      </motion.div>

      <motion.div
        variants={stagger}
        initial="hidden"
        animate="show"
        className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4 mb-6 md:mb-8"
      >
        <motion.div variants={fadeUp}>
          <StatCard label="Total Requests" value={summary?.total_requests?.toString() ?? "0"} />
        </motion.div>
        <motion.div variants={fadeUp}>
          <StatCard label="Total Tokens" value={summary?.total_tokens?.toLocaleString() ?? "0"} />
        </motion.div>
        <motion.div variants={fadeUp}>
          <StatCard label="Avg Latency" value={summary?.avg_latency ? `${summary.avg_latency}ms` : "0ms"} />
        </motion.div>
        <motion.div variants={fadeUp}>
          <StatCard label="Total Cost" value={summary ? fmtUsd(summary.total_cost) : "$0"} highlight />
        </motion.div>
        <motion.div variants={fadeUp}>
          <StatCard label="Cache Savings" value={summary ? fmtUsd(summary.cache_savings_usd) : "$0"} highlight />
        </motion.div>
        <motion.div variants={fadeUp}>
          <StatCard
            label="Cache Hit Rate"
            value={summary ? `${(summary.cache_hit_rate * 100).toFixed(1)}%` : "0%"}
          />
        </motion.div>
      </motion.div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6 mb-6 md:mb-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="border border-white/10 p-4 sm:p-6"
        >
          <h2 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">Top Models</h2>
          {byModel.length > 0 ? (
            <div className="space-y-3">
              {byModel.slice(0, 10).map((m, i) => (
                <motion.div
                  key={`${m.provider}/${m.model}`}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.4 + i * 0.04 }}
                  className="flex items-center justify-between py-2 border-b border-white/5"
                >
                  <div className="min-w-0">
                    <span className="font-mono text-xs text-zinc-300 truncate block max-w-[150px] sm:max-w-[200px]">
                      {m.model}
                    </span>
                    <span className="text-[10px] text-zinc-600 capitalize">{m.provider}</span>
                  </div>
                  <div className="flex items-center gap-3 sm:gap-4 text-xs sm:text-sm text-zinc-400 shrink-0">
                    <span>{m.requests} reqs</span>
                    <span>{m.tokens.toLocaleString()} tok</span>
                    <span className="text-zinc-300">{fmtUsd(m.cost)}</span>
                    {m.saved_cost > 0 && (
                      <span className="text-zinc-500" title="Saved by cache">
                        +{fmtUsd(m.saved_cost)}
                      </span>
                    )}
                  </div>
                </motion.div>
              ))}
            </div>
          ) : (
            <p className="text-zinc-500 text-sm">No data yet</p>
          )}
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="border border-white/10 p-4 sm:p-6"
        >
          <h2 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">Provider Breakdown</h2>
          {byProvider.length > 0 ? (
            <div className="space-y-3">
              {byProvider.map((p, i) => (
                <motion.div
                  key={p.provider}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.5 + i * 0.04 }}
                  className="flex items-center justify-between py-2 border-b border-white/5"
                >
                  <span className="capitalize text-zinc-300 text-sm">{p.provider}</span>
                  <div className="flex items-center gap-3 sm:gap-4 text-xs sm:text-sm text-zinc-400">
                    <span>{p.requests} reqs</span>
                    <span>{p.avg_latency}ms</span>
                    <span className="text-zinc-300">{fmtUsd(p.cost)}</span>
                  </div>
                </motion.div>
              ))}
            </div>
          ) : (
            <p className="text-zinc-500 text-sm">No data yet</p>
          )}
        </motion.div>
      </div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
        className="border border-white/10 p-4 sm:p-6"
      >
        <h2 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">Recent Requests</h2>
        {recent.length > 0 ? (
          <div className="overflow-x-auto -mx-4 sm:mx-0">
            <table className="w-full text-sm min-w-[600px] sm:min-w-0">
              <thead>
                <tr className="border-b border-white/10 text-zinc-500 text-left">
                  <th className="px-3 py-2 font-medium">Time</th>
                  <th className="px-3 py-2 font-medium">Model</th>
                  <th className="px-3 py-2 font-medium">Provider</th>
                  <th className="px-3 py-2 font-medium">Tokens</th>
                  <th className="px-3 py-2 font-medium">Cost</th>
                  <th className="px-3 py-2 font-medium">Latency</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r, i) => (
                  <motion.tr
                    key={i}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 0.6 + i * 0.03 }}
                    className="border-b border-white/5"
                  >
                    <td className="px-3 py-2 text-zinc-400 font-mono text-xs whitespace-nowrap">
                      {r.timestamp ? new Date(r.timestamp).toLocaleTimeString() : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs truncate max-w-[140px]">{r.model}</td>
                    <td className="px-3 py-2 capitalize text-xs">{r.provider}</td>
                    <td className="px-3 py-2 text-zinc-400 text-xs">{r.total_tokens.toLocaleString()}</td>
                    <td className="px-3 py-2 text-zinc-300 text-xs whitespace-nowrap">{fmtUsd(r.cost_usd)}</td>
                    <td className="px-3 py-2 text-zinc-400 text-xs whitespace-nowrap">{r.latency_ms}ms</td>
                    <td className="px-3 py-2">
                      <span className={r.status === "success" ? "text-white text-xs" : "text-zinc-500 text-xs"}>
                        {r.status}
                      </span>
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-zinc-500 text-sm">No requests yet</p>
        )}
      </motion.div>
    </div>
  );
}

function StatCard({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <motion.div
      whileHover={{ scale: 1.02 }}
      transition={{ type: "spring", stiffness: 400 }}
      className={`border p-3 sm:p-4 ${highlight ? "border-white/30 bg-white/5" : "border-white/10"}`}
    >
      <p className="text-[10px] sm:text-xs text-zinc-500 uppercase tracking-wider">{label}</p>
      <p className="text-lg sm:text-2xl font-bold mt-1 truncate">{value}</p>
    </motion.div>
  );
}
