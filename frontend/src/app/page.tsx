"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";

interface GatewayStatus {
  service: string;
  version: string;
  endpoints: Record<string, string>;
}

interface HealthCheck {
  status: string;
  providers: { provider: string; status: string; latency_ms: number }[];
}

interface Summary {
  total_requests: number;
  total_tokens: number;
  avg_latency: number;
  successful: number;
  failed: number;
}

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.1 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
};

export default function Dashboard() {
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [health, setHealth] = useState<HealthCheck | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);

  useEffect(() => {
    fetch("/api/gateway")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setStatus(d))
      .catch(() => {});
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setHealth(d))
      .catch(() => {});
    fetch("/api/analytics/summary")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setSummary(d))
      .catch(() => {});
  }, []);

  return (
    <div className="p-4 sm:p-6 md:p-8">
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-6 md:mb-8"
      >
        <h1 className="text-2xl sm:text-3xl font-bold">Dashboard</h1>
        <p className="text-zinc-500 mt-1 text-sm">AI Inference Gateway overview</p>
      </motion.div>

      <motion.div
        variants={container}
        initial="hidden"
        animate="show"
        className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4 mb-6 md:mb-8"
      >
        <motion.div variants={item} whileHover={{ scale: 1.02 }} transition={{ type: "spring", stiffness: 400 }}>
          <StatCard label="Total Requests" value={summary?.total_requests?.toString() ?? "0"} />
        </motion.div>
        <motion.div variants={item} whileHover={{ scale: 1.02 }} transition={{ type: "spring", stiffness: 400 }}>
          <StatCard label="Total Tokens" value={summary?.total_tokens?.toLocaleString() ?? "0"} />
        </motion.div>
        <motion.div variants={item} whileHover={{ scale: 1.02 }} transition={{ type: "spring", stiffness: 400 }}>
          <StatCard label="Providers" value={health?.providers?.length?.toString() ?? "0"} />
        </motion.div>
        <motion.div variants={item} whileHover={{ scale: 1.02 }} transition={{ type: "spring", stiffness: 400 }}>
          <StatCard label="Avg Latency" value={summary?.avg_latency ? `${summary.avg_latency}ms` : "0ms"} />
        </motion.div>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
        className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6"
      >
        <div className="border border-white/10 p-4 sm:p-6">
          <h2 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">Gateway Status</h2>
          {status && typeof status.endpoints === "object" ? (
            <div className="space-y-3">
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="flex items-center gap-2"
              >
                <span className="w-2 h-2 bg-white rounded-full shrink-0"></span>
                <span className="text-zinc-300 text-sm truncate">{status.service} v{status.version}</span>
              </motion.div>
              <div className="mt-4">
                <h3 className="text-sm font-medium text-zinc-400 mb-2">Endpoints</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {Object.entries(status.endpoints).map(([key, value]) => (
                    <div key={key} className="flex items-center gap-2 text-sm">
                      <span className="text-zinc-500 shrink-0">{key}:</span>
                      <code className="text-white font-mono text-xs truncate">{value}</code>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-zinc-500 text-sm">
              <span className="w-2 h-2 bg-zinc-600 rounded-full shrink-0"></span>
              Backend offline
            </div>
          )}
        </div>

        <div className="border border-white/10 p-4 sm:p-6">
          <h2 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">Provider Health</h2>
          {health && Array.isArray(health.providers) && health.providers.length > 0 ? (
            <div className="space-y-3">
              {health.providers.map((p) => (
                <div key={p.provider} className="flex items-center justify-between py-2 border-b border-white/5">
                  <div className="flex items-center gap-3">
                    <span className={`w-2 h-2 rounded-full shrink-0 ${p.status === "healthy" ? "bg-white" : "bg-zinc-600"}`}></span>
                    <span className="capitalize text-sm">{p.provider}</span>
                  </div>
                  <div className="flex items-center gap-3 sm:gap-4 text-xs sm:text-sm text-zinc-400">
                    <span>{p.status}</span>
                    <span>{p.latency_ms}ms</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-zinc-500 text-sm">Backend offline — start it to see provider health</div>
          )}
        </div>
      </motion.div>

      {summary && summary.total_requests > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.7 }}
          className="mt-4 md:mt-6 border border-white/10 p-4 sm:p-6"
        >
          <h2 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">Request Summary</h2>
          <div className="grid grid-cols-3 gap-3 sm:gap-4">
            <div>
              <p className="text-xs text-zinc-500 uppercase">Successful</p>
              <p className="text-lg sm:text-xl font-bold mt-1">{summary.successful}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Failed</p>
              <p className="text-lg sm:text-xl font-bold mt-1">{summary.failed}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 uppercase">Success Rate</p>
              <p className="text-lg sm:text-xl font-bold mt-1">
                {summary.total_requests > 0
                  ? ((summary.successful / summary.total_requests) * 100).toFixed(1)
                  : 0}%
              </p>
            </div>
          </div>
        </motion.div>
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-white/10 p-3 sm:p-4">
      <p className="text-[10px] sm:text-xs text-zinc-500 uppercase tracking-wider">{label}</p>
      <p className="text-lg sm:text-2xl font-bold mt-1 truncate">{value}</p>
    </div>
  );
}
