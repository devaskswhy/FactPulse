"use client";

import { useEffect, useState } from "react";

import { API_BASE_URL } from "@/lib/api";

type Health = {
  status: string;
  version: string;
  database: string;
  tables: string[];
  gemini_configured: boolean;
};

type State =
  | { kind: "loading" }
  | { kind: "ok"; health: Health }
  | { kind: "down"; message: string };

/** Small dev affordance: shows whether the FastAPI backend is reachable. */
export function ApiStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    fetch(`${API_BASE_URL}/health`, { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<Health>;
      })
      .then((health) => {
        if (!cancelled) setState({ kind: "ok", health });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            kind: "down",
            message: err instanceof Error ? err.message : "unreachable",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const dot =
    state.kind === "ok"
      ? "bg-green-500"
      : state.kind === "down"
        ? "bg-red-500"
        : "bg-neutral-400";

  return (
    <div className="rounded-lg border border-black/10 p-4 text-sm dark:border-white/15">
      <div className="flex items-center gap-2">
        <span className={`inline-block size-2 rounded-full ${dot}`} />
        <span className="font-medium">API</span>
        <code className="text-xs opacity-60">{API_BASE_URL}</code>
      </div>

      <div className="mt-2 text-xs opacity-70">
        {state.kind === "loading" && "checking…"}
        {state.kind === "ok" && (
          <>
            v{state.health.version} · db {state.health.database} ·{" "}
            {state.health.tables.length} tables · gemini{" "}
            {state.health.gemini_configured ? "configured" : "not configured"}
          </>
        )}
        {state.kind === "down" && (
          <>not reachable ({state.message}) — start the backend on port 8000</>
        )}
      </div>
    </div>
  );
}
