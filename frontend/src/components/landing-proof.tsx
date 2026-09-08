"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

import { getDocuments, type KnowledgeLayerTotals } from "@/lib/api";
import { DURATION, EASE, prefersReducedMotion } from "@/lib/motion";

// Idempotent -- see the identical note in landing-explainer.tsx for why this
// is registered here too rather than left solely to the provider.
gsap.registerPlugin(ScrollTrigger);

/**
 * Real numbers from the corpus currently loaded, not stand-ins.
 *
 * Everything here comes from the same GET /documents call that populates the
 * app's own header once you scroll past this section -- one function on the
 * backend (repo.knowledge_layer_totals) computes both, so a number a visitor
 * sees here can never disagree with what the app shows a minute later. If the
 * request fails, the section hides itself rather than showing a stale or
 * fabricated figure.
 */

const VERDICT_COLOR: Record<string, string> = {
  corroborates: "var(--corroborate)",
  contradicts: "var(--contradict)",
  reconciled: "var(--accent)",
  supersedes: "var(--supersede)",
};

const VERDICT_LABEL: Record<string, string> = {
  corroborates: "corroborates",
  contradicts: "contradicts",
  reconciled: "reconciled",
  supersedes: "supersedes",
};

const EVIDENCE_COLOR: Record<string, string> = {
  full: "var(--corroborate)",
  partial: "var(--accent)",
  insufficient: "var(--contradict)",
};

const COMPARISON = [
  {
    naive: "One number in, one number out. No page, no quote, no way to check it.",
    factpulse: "Every fact carries the verbatim quote, its page, and the exact rectangle it sits in.",
  },
  {
    naive: "Two different numbers for the same thing? Flag it a contradiction and move on.",
    factpulse: "Five verdicts, with a rationale citing both sides — including \"reconciled\" when the difference is just period, scope, unit or basis.",
  },
  {
    naive: "\"Acme Corp\" and \"Acme Corporation\" are two different subjects to a keyword search.",
    factpulse: "Mechanically folded to one entity for filtering and grouping — without ever guessing at semantic identity.",
  },
  {
    naive: "A grounded quote is treated as a solved problem.",
    factpulse: "A quote of \"Nil\" under a full sentence is graded insufficient, not silently accepted.",
  },
];

function StatBlock({ value, label }: { value: number | string; label: string }) {
  return (
    <div data-proof-stat className="min-w-0">
      <p className="font-mono text-3xl tabular-nums text-text md:text-4xl">{value}</p>
      <p className="mt-1 text-xs text-text-dim md:text-sm">{label}</p>
    </div>
  );
}

function VerdictBars({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  if (!entries.length) return null;
  const max = Math.max(...entries.map(([, n]) => n));

  return (
    <div className="space-y-2.5">
      {entries.map(([kind, n]) => (
        <div key={kind} className="flex items-center gap-3">
          <span className="w-28 shrink-0 font-mono text-[11px] uppercase tracking-wide text-text-dim">
            {VERDICT_LABEL[kind] ?? kind}
          </span>
          <div className="h-2 flex-1 rounded-full bg-border">
            <div
              data-proof-bar
              className="h-full rounded-full"
              style={{
                width: `${(n / max) * 100}%`,
                backgroundColor: VERDICT_COLOR[kind] ?? "var(--accent)",
              }}
            />
          </div>
          <span className="w-6 shrink-0 text-right font-mono text-[11px] text-text-dim">
            {n}
          </span>
        </div>
      ))}
    </div>
  );
}

function EvidenceBar({ counts }: { counts: Record<string, number> }) {
  const order = ["full", "partial", "insufficient"] as const;
  const total = order.reduce((sum, tier) => sum + (counts[tier] ?? 0), 0);
  if (!total) return null;

  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-border">
        {order.map((tier) =>
          counts[tier] ? (
            <div
              key={tier}
              data-proof-bar
              style={{
                width: `${(counts[tier] / total) * 100}%`,
                backgroundColor: EVIDENCE_COLOR[tier],
              }}
              title={`${tier}: ${counts[tier]}`}
            />
          ) : null,
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-text-dim">
        {order.map((tier) =>
          counts[tier] ? (
            <span key={tier} className="flex items-center gap-1.5">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: EVIDENCE_COLOR[tier] }}
              />
              {tier} {counts[tier]}
            </span>
          ) : null,
        )}
      </div>
    </div>
  );
}

export function LandingProof() {
  const [totals, setTotals] = useState<KnowledgeLayerTotals | null>(null);
  const rootRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let cancelled = false;
    getDocuments()
      .then((data) => {
        if (!cancelled) setTotals(data.totals);
      })
      .catch(() => {
        /* the section hides itself below rather than showing stale numbers */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useLayoutEffect(() => {
    if (!totals || prefersReducedMotion() || !rootRef.current) return;
    const context = gsap.context(() => {
      gsap.from("[data-proof-stat]", {
        opacity: 0,
        y: 16,
        duration: DURATION.base,
        ease: EASE,
        stagger: 0.06,
        scrollTrigger: { trigger: rootRef.current, start: "top 80%" },
      });
      gsap.from("[data-proof-bar]", {
        scaleX: 0,
        transformOrigin: "left center",
        duration: DURATION.base,
        ease: EASE,
        stagger: 0.05,
        scrollTrigger: { trigger: rootRef.current, start: "top 70%" },
      });
      gsap.from("[data-proof-compare]", {
        opacity: 0,
        y: 16,
        duration: DURATION.base,
        ease: EASE,
        stagger: 0.08,
        scrollTrigger: { trigger: "[data-proof-compare-list]", start: "top 80%" },
      });
    }, rootRef);
    return () => context.revert();
  }, [totals]);

  if (!totals) return null;

  return (
    <section ref={rootRef} className="border-t border-border bg-surface/20">
      <div className="mx-auto max-w-4xl px-6 py-16 md:px-10 md:py-20">
        <p className="font-mono text-xs uppercase tracking-[0.3em] text-accent">
          Not a mockup
        </p>
        <h2 className="mt-3 max-w-xl text-2xl tracking-tight text-text md:text-3xl">
          These numbers are read live from the running database below.
        </h2>

        {/* Headline stats */}
        <div className="mt-10 grid grid-cols-2 gap-x-6 gap-y-8 sm:grid-cols-4">
          <StatBlock value={totals.documents} label="source documents" />
          <StatBlock value={totals.facts} label="facts extracted" />
          <StatBlock
            value={totals.cross_document_relationships}
            label="cross-document links"
          />
          <StatBlock value={totals.fact_types} label="fact types discovered" />
        </div>

        <div className="mt-14 grid gap-10 md:grid-cols-2">
          {/* Verdict distribution */}
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim">
              How facts relate, across documents
            </p>
            <div className="mt-4">
              <VerdictBars counts={totals.relationships_by_type ?? {}} />
            </div>
          </div>

          {/* Evidence honesty */}
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim">
              Evidence strength, graded on every fact
            </p>
            <div className="mt-4">
              <EvidenceBar counts={totals.evidence_by_strength ?? {}} />
            </div>
            <p className="mt-3 text-xs leading-relaxed text-text-dim">
              A verified quote is not the same as a sufficient one — a table
              cell reading &ldquo;Nil&rdquo; under a full sentence is real, and
              still not enough. FactPulse says so instead of hiding it behind
              a green checkmark.
            </p>
          </div>
        </div>

        {/* The naive-vs-FactPulse comparison */}
        <div className="mt-16">
          <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim">
            What most extraction pipelines skip
          </p>
          <div data-proof-compare-list className="mt-5 space-y-4">
            {COMPARISON.map((row, i) => (
              <div
                key={i}
                data-proof-compare
                className="grid gap-3 rounded-lg border border-border bg-bg/40 p-4 md:grid-cols-2"
              >
                <p className="text-sm leading-relaxed text-text-dim">
                  <span className="mr-2 font-mono text-[10px] uppercase tracking-wide text-text-dim/70">
                    Naive
                  </span>
                  {row.naive}
                </p>
                <p className="text-sm leading-relaxed text-text">
                  <span className="mr-2 font-mono text-[10px] uppercase tracking-wide text-accent">
                    FactPulse
                  </span>
                  {row.factpulse}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
