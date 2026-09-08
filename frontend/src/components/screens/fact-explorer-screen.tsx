"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { gsap } from "gsap";

import {
  ApiError,
  EVIDENCE_TIER,
  getFacts,
  type Fact,
  type FactList,
  type KnowledgeLayerTotals,
} from "@/lib/api";
import { DURATION, STAGGER, prefersReducedMotion } from "@/lib/motion";
import { ComparisonCard } from "@/components/comparison-card";
import { EvidenceViewer } from "@/components/evidence-viewer";
import { TaxonomyPanel } from "@/components/taxonomy-panel";

type GroupMode = "none" | "document" | "type" | "subject";

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
  const [error, setError] = useState<string | null>(null);
  const [factType, setFactType] = useState<string | null>(null);
  const [subject, setSubject] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [group, setGroup] = useState<GroupMode>("document");
  // Which group headers are open. Everything starts closed: 335 facts
  // expanded on load is a wall to scroll past, not a list to read.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
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
    getFacts({ document_id: documentId, fact_type: factType, subject, limit: 300 })
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
  }, [documentId, factType, subject, refreshKey]);

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
    if (group === "none") return [{ key: "", label: "", variants: 0, facts: visible }];

    if (group === "subject") {
      // Grouped on canonical_subject so "Acme Corp" and "Acme Corporation"
      // land in one bucket, but the raw `subject` strings are still counted
      // per bucket -- that count is what proves two spellings actually
      // merged, rather than a filter silently missing one of them.
      const map = new Map<string, Fact[]>();
      for (const fact of visible) {
        const key = fact.canonical_subject ?? fact.subject ?? "(no subject)";
        const bucket = map.get(key);
        if (bucket) bucket.push(fact);
        else map.set(key, [fact]);
      }
      return [...map.entries()]
        .sort((a, b) => b[1].length - a[1].length)
        .map(([key, facts]) => {
          const counts = new Map<string, number>();
          for (const fact of facts) {
            const raw = fact.subject ?? "(no subject)";
            counts.set(raw, (counts.get(raw) ?? 0) + 1);
          }
          const byCount = [...counts.entries()].sort((a, b) => b[1] - a[1]);
          return { key, label: byCount[0][0], variants: byCount.length, facts };
        });
    }

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
      .map(([key, facts]) => ({ key, label: key, variants: 0, facts }));
  }, [visible, group, data]);

  // Switching the grouping dimension, the document, or a filter makes
  // previously-open sections meaningless, so those reset. A plain data
  // refresh does not -- that would slam shut whatever the reader had just
  // opened. Done during render rather than in an effect: an effect would
  // render once with the stale set and then again to correct it.
  //
  // The reset opens the largest group rather than none of them. All-closed
  // was the honest reading of "keep it clean initially", but it lands the
  // reader on a screen with nothing on it to read.
  const filterSignature = `${group}|${documentId}|${factType}|${subject}`;
  const [previousSignature, setPreviousSignature] = useState<string | null>(null);
  if (filterSignature !== previousSignature && groups.length) {
    setPreviousSignature(filterSignature);
    setExpanded(new Set(groups[0].key ? [groups[0].key] : []));
  }

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
              {(["document", "type", "subject", "none"] as const).map((mode) => (
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

          <p className="mt-3 font-mono text-[11px] text-text-dim">
            {visible.length} of {data?.total ?? 0} facts ·{" "}
            {data?.scope === "document" ? "this document" : "whole knowledge layer"}
            {factType && <span className="text-accent"> · {factType}</span>}
            {subject && <span className="text-accent"> · {subject}</span>}
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
              {factType || subject
                ? "clear the filter in the panel."
                : "upload a document to start."}
            </p>
          )}

          {groups.map((bucket) => {
            // A single unlabelled bucket is group="none": there is no header
            // to click, so it is always open.
            const isOpen = !bucket.label || expanded.has(bucket.key);
            return (
            <section key={bucket.key || "all"}>
              {bucket.label && (
                <h3 className="sticky top-0 z-10 border-b border-border bg-bg/95 backdrop-blur">
                  <button
                    type="button"
                    onClick={() =>
                      setExpanded((prev) => {
                        const next = new Set(prev);
                        if (next.has(bucket.key)) next.delete(bucket.key);
                        else next.add(bucket.key);
                        return next;
                      })
                    }
                    className="flex w-full items-center gap-2 px-5 py-2 text-left font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim transition-colors duration-200 hover:text-text"
                  >
                    <span className="text-accent">{isOpen ? "−" : "+"}</span>
                    <span className="truncate">{bucket.label}</span>
                    <span className="opacity-60">{bucket.facts.length}</span>
                    {/* Proof the fold did something: this entity was written
                        more than one way and the group still landed as one. */}
                    {bucket.variants > 1 && (
                      <span
                        className="rounded border border-accent/40 px-1.5 py-0.5 text-[10px] normal-case tracking-normal text-accent"
                        title="Raw subject spellings merged into this group"
                      >
                        {bucket.variants} spellings merged
                      </span>
                    )}
                  </button>
                </h3>
              )}
              {isOpen && (
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
                          {/* Evidence tier. Shown on every row because
                              "grounded" without "sufficient" is misleading. */}
                          {fact.evidence_strength &&
                            fact.evidence_strength !== "full" && (
                              <span
                                className={`rounded border px-1.5 py-0.5 text-[10px] ${
                                  EVIDENCE_TIER[fact.evidence_strength]?.className ?? ""
                                }`}
                                title={
                                  EVIDENCE_TIER[fact.evidence_strength]?.title ?? ""
                                }
                              >
                                {EVIDENCE_TIER[fact.evidence_strength]?.label}
                              </span>
                            )}
                          {/* A fact a later document has replaced. Kept in
                              the list rather than filtered out -- it was true
                              when it was written, and hiding it would lose the
                              history the supersession records. */}
                          {fact.superseded_by != null && (
                            <span
                              className="rounded border border-supersede/40 bg-supersede/10 px-1.5 py-0.5 text-[10px] text-supersede"
                              title={`Replaced by fact ${fact.superseded_by}.`}
                            >
                              superseded
                            </span>
                          )}
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
              )}
            </section>
            );
          })}
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
          selectedType={factType}
          onSelectType={setFactType}
          selectedSubject={subject}
          onSelectSubject={setSubject}
          refreshKey={refreshKey}
        />
      )}
    </div>
  );
}
