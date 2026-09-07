"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { gsap } from "gsap";

import {
  ApiError,
  getFacts,
  getSchema,
  type Fact,
  type FactList,
  type KnowledgeLayerTotals,
  type SchemaResponse,
} from "@/lib/api";
import { DURATION, STAGGER, prefersReducedMotion } from "@/lib/motion";
import { ComparisonCard } from "@/components/comparison-card";
import { EvidenceViewer } from "@/components/evidence-viewer";
import { TaxonomyPanel } from "@/components/taxonomy-panel";

type GroupMode = "none" | "document" | "type";

export function FactExplorerScreen({
  documentId,
  totals,
  refreshKey,
}: {
  documentId: number | null;
  totals?: KnowledgeLayerTotals;
  refreshKey?: number;
}) {
  const [data, setData] = useState<FactList | null>(null);
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [factType, setFactType] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [group, setGroup] = useState<GroupMode>("document");
  const [open, setOpen] = useState<Fact | null>(null);
  const [tab, setTab] = useState<"evidence" | "relationships">("evidence");
  const [relationCounts, setRelationCounts] = useState<Record<number, number>>({});

  const listRef = useRef<HTMLDivElement>(null);

  // Facts. Re-fetched when the document or type filter changes; free-text
  // search filters client-side because the list is already in memory and a
  // round trip per keystroke would be slower and noisier.
  useEffect(() => {
    // The error is cleared on success rather than up front: clearing it
    // synchronously here is a cascading render, and a stale error vanishing
    // the instant new data lands reads the same to a user.
    let cancelled = false;
    getFacts({ document_id: documentId, fact_type: factType, limit: 300 })
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load facts.");
          setData(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, factType, refreshKey]);

  useEffect(() => {
    getSchema()
      .then(setSchema)
      .catch(() => {
        /* chips degrade to absent */
      });
  }, [refreshKey]);

  // Relationship badges. One request for the whole corpus rather than one per
  // fact: /facts already tells us which document each fact is in, and the
  // badge only needs a count.
  useEffect(() => {
    if (!data) return;
    let cancelled = false;
    const ids = data.facts.map((f) => f.id);
    if (!ids.length) return;

    // The API has no bulk relationship-count endpoint, so this asks per fact
    // but only for what is on screen, and tolerates failures silently -- a
    // missing badge is much better than a failed screen.
    const budget = ids.slice(0, 60);
    Promise.all(
      budget.map((id) =>
        fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/facts/${id}/relationships`)
          .then((r) => (r.ok ? r.json() : null))
          .then((j) => [id, j?.total ?? 0] as const)
          .catch(() => [id, 0] as const),
      ),
    ).then((pairs) => {
      if (cancelled) return;
      setRelationCounts(Object.fromEntries(pairs));
    });

    return () => {
      cancelled = true;
    };
  }, [data]);

  const visible = useMemo(() => {
    if (!data) return [];
    const needle = search.trim().toLowerCase();
    if (!needle) return data.facts;
    return data.facts.filter(
      (f) =>
        f.statement.toLowerCase().includes(needle) ||
        (f.subject ?? "").toLowerCase().includes(needle) ||
        f.fact_type.toLowerCase().includes(needle) ||
        (f.grounding?.quote ?? "").toLowerCase().includes(needle),
    );
  }, [data, search]);

  const groups = useMemo(() => {
    if (group === "none") return [{ key: "", label: "", facts: visible }];
    const map = new Map<string, Fact[]>();
    for (const fact of visible) {
      const key =
        group === "document"
          ? (data?.documents[String(fact.document_id)] ?? `document ${fact.document_id}`)
          : fact.fact_type;
      const bucket = map.get(key);
      if (bucket) bucket.push(fact);
      else map.set(key, [fact]);
    }
    return [...map.entries()]
      .sort((a, b) => b[1].length - a[1].length)
      .map(([key, facts]) => ({ key, label: key, facts }));
  }, [visible, group, data]);

  // Stagger on load and on every filter change, capped so a 300-fact list does
  // not crawl. 300ms total budget, per the app-shell motion rule.
  useLayoutEffect(() => {
    if (prefersReducedMotion() || !listRef.current) return;
    const rows = listRef.current.querySelectorAll("[data-fact-row]");
    if (!rows.length) return;
    const tween = gsap.fromTo(
      rows,
      { opacity: 0, y: 6 },
      {
        opacity: 1,
        y: 0,
        duration: DURATION.fast,
        ease: "power2.out",
        stagger: Math.min(STAGGER.tight, 0.3 / rows.length),
        clearProps: "all",
      },
    );
    return () => {
      tween.kill();
    };
  }, [groups]);

  if (error) {
    return (
      <div className="p-6">
        <p className="text-sm text-contradict">{error}</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col lg:flex-row">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Controls */}
        <div className="border-b border-border px-5 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search statements, subjects, quotes…"
              className="min-w-0 flex-1 rounded-md border border-border bg-surface px-3 py-1.5 text-sm text-text placeholder:text-text-dim focus:border-accent focus:outline-none"
            />
            <div className="flex items-center gap-1 font-mono text-[11px]">
              <span className="text-text-dim">group</span>
              {(["document", "type", "none"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setGroup(mode)}
                  className={`rounded px-2 py-1 transition-colors duration-200 ${
                    group === mode ? "bg-surface text-text" : "text-text-dim hover:text-text"
                  }`}
                >
                  {mode}
                </button>
              ))}
            </div>
          </div>

          {/* Filter chips, generated from the live schema -- never hardcoded,
              because the vocabulary grows with every document. */}
          {schema && schema.fact_types.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {schema.fact_types.slice(0, 18).map((type) => {
                const active = factType === type.name;
                return (
                  <button
                    key={type.name}
                    type="button"
                    onClick={() => setFactType(active ? null : type.name)}
                    className={`rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors duration-200 ${
                      active
                        ? "border-accent bg-accent/15 text-accent"
                        : "border-border text-text-dim hover:border-text-dim hover:text-text"
                    }`}
                  >
                    {type.name}
                    <span className="ml-1.5 opacity-60">{type.fact_count}</span>
                  </button>
                );
              })}
              {schema.fact_types.length > 18 && (
                <span className="self-center font-mono text-[11px] text-text-dim">
                  +{schema.fact_types.length - 18} more in the panel →
                </span>
              )}
            </div>
          )}

          <p className="mt-3 font-mono text-[11px] text-text-dim">
            {visible.length} of {data?.total ?? 0} facts ·{" "}
            {data?.scope === "document" ? "this document" : "whole knowledge layer"}
            {factType && <span className="text-accent"> · {factType}</span>}
            {totals && data?.scope !== "document" && (
              <span> · {totals.cross_document_relationships} cross-document links</span>
            )}
          </p>
        </div>

        {/* List */}
        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
          {!data && (
            <p className="px-5 py-4 font-mono text-xs text-text-dim">loading facts…</p>
          )}

          {data && visible.length === 0 && (
            <p className="px-5 py-6 text-sm text-text-dim">
              No facts match. {search && "Try a different search, or "}
              {factType ? "clear the type filter." : "upload a document to start."}
            </p>
          )}

          {groups.map((bucket) => (
            <section key={bucket.key || "all"}>
              {bucket.label && (
                <h3 className="sticky top-0 z-10 border-b border-border bg-bg/95 px-5 py-2 font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim backdrop-blur">
                  {bucket.label}
                  <span className="ml-2 opacity-60">{bucket.facts.length}</span>
                </h3>
              )}
              <ul>
                {bucket.facts.map((fact) => {
                  const active = open?.id === fact.id;
                  const links = relationCounts[fact.id] ?? 0;
                  return (
                    <li key={fact.id} data-fact-row>
                      <button
                        type="button"
                        onClick={() => {
                          setOpen(fact);
                          setTab("evidence");
                        }}
                        className={`w-full border-b border-border/60 border-l-2 px-5 py-3 text-left transition-colors duration-200 ${
                          active
                            ? "border-l-accent bg-surface"
                            : "border-l-transparent hover:bg-surface/50"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <p className="min-w-0 text-sm leading-relaxed text-text">
                            {fact.statement}
                          </p>
                          {links > 0 && (
                            <span
                              className="mt-0.5 shrink-0 rounded-full border border-accent/40 bg-accent/10 px-1.5 py-0.5 font-mono text-[10px] text-accent"
                              title={`${links} relationship${links === 1 ? "" : "s"}`}
                            >
                              {links} ⇄
                            </span>
                          )}
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-text-dim">
                          <span className="text-accent">{fact.fact_type}</span>
                          {fact.subject && <span className="truncate">{fact.subject}</span>}
                          {fact.normalized_value && (
                            <span>
                              {fact.normalized_value}
                              {fact.unit ? ` ${fact.unit}` : ""}
                            </span>
                          )}
                          {fact.time_scope && <span>{fact.time_scope}</span>}
                          {fact.grounding?.page_number != null && (
                            <span>p{fact.grounding.page_number}</span>
                          )}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      </div>

      {/* Detail pane */}
      {open && (
        <div className="flex min-w-0 flex-col border-t border-border lg:w-[46%] lg:border-l lg:border-t-0">
          <div className="flex items-center justify-between border-b border-border px-4 py-2">
            <div className="flex items-center gap-1">
              {(["evidence", "relationships"] as const).map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTab(id)}
                  className={`rounded px-2 py-1 font-mono text-[11px] transition-colors duration-200 ${
                    tab === id ? "bg-surface text-text" : "text-text-dim hover:text-text"
                  }`}
                >
                  {id}
                  {id === "relationships" && (relationCounts[open.id] ?? 0) > 0 && (
                    <span className="ml-1 text-accent">
                      {relationCounts[open.id]}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setOpen(null)}
              className="font-mono text-[11px] text-text-dim transition-colors duration-200 hover:text-text"
            >
              close
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {tab === "evidence" ? (
              <EvidenceViewer key={open.id} factId={open.id} />
            ) : (
              <ComparisonCard
                key={open.id}
                fact={open}
                onOpenFact={(id) => {
                  const next = data?.facts.find((f) => f.id === id);
                  if (next) {
                    setOpen(next);
                    setTab("evidence");
                  }
                }}
              />
            )}
          </div>
        </div>
      )}

      {!open && (
        <TaxonomyPanel
          selected={factType}
          onSelect={setFactType}
          refreshKey={refreshKey}
        />
      )}
    </div>
  );
}
