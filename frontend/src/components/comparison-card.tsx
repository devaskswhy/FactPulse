"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { gsap } from "gsap";

import {
  ApiError,
  getFacts,
  getRelationships,
  type Fact,
  type RelatedFact,
} from "@/lib/api";
import { DURATION, STAGGER, prefersReducedMotion } from "@/lib/motion";

/**
 * Evidence-first comparison, not a graph.
 *
 * A node-and-edge diagram would show that two facts are connected but not
 * WHY, and the why is the entire product: the rationale naming which axis the
 * two differ on. So the primary view is two evidence cards side by side with
 * the verdict between them, readable without interaction.
 *
 * The optional network tab exists because it is genuinely useful for seeing
 * that one fact is a hub, but it is secondary and stays that way.
 */

const VERDICT: Record<
  string,
  { label: string; ring: string; text: string; bg: string; bar: string }
> = {
  corroborates: {
    label: "CORROBORATES",
    ring: "border-corroborate/50",
    text: "text-corroborate",
    bg: "bg-corroborate/10",
    bar: "bg-corroborate",
  },
  contradicts: {
    label: "CONTRADICTS",
    ring: "border-contradict/50",
    text: "text-contradict",
    bg: "bg-contradict/10",
    bar: "bg-contradict",
  },
  reconciled: {
    label: "RECONCILED",
    ring: "border-accent/50",
    text: "text-accent",
    bg: "bg-accent/10",
    bar: "bg-accent",
  },
};

function verdictStyle(kind: string) {
  return (
    VERDICT[kind] ?? {
      label: kind.toUpperCase(),
      ring: "border-border",
      text: "text-text-dim",
      bg: "bg-surface",
      bar: "bg-border",
    }
  );
}

/**
 * The backend prefixes a reconciled rationale with the axis in brackets, e.g.
 * "[basis] Fact A states...". Split it out so the axis can be shown as its own
 * emphasised chip rather than buried at the front of a paragraph.
 */
function splitRationale(rationale: string | null): {
  dimension: string | null;
  body: string;
} {
  if (!rationale) return { dimension: null, body: "" };
  // [\s\S] rather than the `s` flag, which needs an es2018 target.
  const match = rationale.match(/^\[([^\]]+)\]\s*([\s\S]*)$/);
  if (!match) return { dimension: null, body: rationale };
  return { dimension: match[1], body: match[2] };
}

function EvidenceCard({
  eyebrow,
  factType,
  statement,
  subject,
  value,
  unit,
  scope,
  quote,
  source,
  page,
  accent,
}: {
  eyebrow: string;
  factType: string;
  statement: string;
  subject: string | null;
  value: string | null;
  unit: string | null;
  scope: string | null;
  quote: string | null;
  source: string;
  page: number | null;
  accent?: boolean;
}) {
  return (
    <div
      className={`flex min-w-0 flex-col rounded-lg border bg-surface/50 p-4 ${
        accent ? "border-accent/40" : "border-border"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-dim">
          {eyebrow}
        </span>
        <span className="truncate rounded bg-bg px-1.5 py-0.5 font-mono text-[10px] text-accent">
          {factType}
        </span>
      </div>

      <p className="mt-3 text-sm leading-relaxed text-text">{statement}</p>

      {/* The three axes the verdict actually turns on, always shown together
          so a reader can compare them across the two cards at a glance. */}
      <div className="mt-3 grid grid-cols-3 gap-2 border-t border-border pt-3 font-mono text-[11px]">
        {[
          ["value", value && unit ? `${value} ${unit}` : (value ?? "—")],
          ["scope", scope ?? "—"],
          ["subject", subject ?? "—"],
        ].map(([label, text]) => (
          <div key={label} className="min-w-0">
            <div className="text-text-dim">{label}</div>
            <div className="truncate text-text" title={String(text)}>
              {text}
            </div>
          </div>
        ))}
      </div>

      {quote && (
        <blockquote className="mt-3 border-l-2 border-border pl-3 text-xs leading-relaxed text-text-dim">
          {quote}
        </blockquote>
      )}

      <div className="mt-3 flex items-center gap-2 font-mono text-[10px] text-text-dim">
        <span className="truncate" title={source}>
          {source}
        </span>
        {page != null && <span>· p{page}</span>}
      </div>
    </div>
  );
}

export function ComparisonCard({
  fact,
  onOpenFact,
}: {
  fact: Fact;
  onOpenFact?: (factId: number) => void;
}) {
  const [related, setRelated] = useState<RelatedFact[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"cards" | "network">("cards");
  const [sourceTitle, setSourceTitle] = useState<string>("");

  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Mounted with key={fact.id}; see the note in evidence-viewer.
    let cancelled = false;

    getRelationships(fact.id)
      .then((data) => {
        if (!cancelled) setRelated(data.relationships);
      })
      .catch((err) => {
        if (!cancelled)
          setError(
            err instanceof ApiError ? err.message : "Could not load relationships.",
          );
      });

    // The fact itself does not carry its document's title, and the comparison
    // is meaningless without naming both sources.
    getFacts({ document_id: fact.document_id, limit: 1 })
      .then((data) => {
        if (!cancelled)
          setSourceTitle(data.documents[String(fact.document_id)] ?? "");
      })
      .catch(() => {
        /* the card degrades to no title, which is survivable */
      });

    return () => {
      cancelled = true;
    };
  }, [fact.id, fact.document_id]);

  useLayoutEffect(() => {
    if (!related?.length || prefersReducedMotion() || !listRef.current) return;
    const items = listRef.current.querySelectorAll("[data-pair]");
    const tween = gsap.fromTo(
      items,
      { opacity: 0, y: 10 },
      {
        opacity: 1,
        y: 0,
        duration: DURATION.fast,
        ease: "power2.out",
        stagger: STAGGER.tight,
        clearProps: "all",
      },
    );
    return () => {
      tween.kill();
    };
  }, [related, tab]);

  if (error) return <p className="p-6 text-sm text-contradict">{error}</p>;
  if (!related)
    return <p className="p-6 font-mono text-xs text-text-dim">loading relationships…</p>;

  if (related.length === 0) {
    return (
      <div className="p-6">
        <p className="text-sm text-text-dim">
          No relationships yet. This fact has not matched anything in another
          document above the similarity threshold.
        </p>
      </div>
    );
  }

  const counts = related.reduce<Record<string, number>>((acc, r) => {
    acc[r.relationship_type] = (acc[r.relationship_type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {Object.entries(counts).map(([kind, n]) => {
            const style = verdictStyle(kind);
            return (
              <span
                key={kind}
                className={`rounded border px-2 py-0.5 font-mono text-[10px] ${style.ring} ${style.text} ${style.bg}`}
              >
                {n} {style.label.toLowerCase()}
              </span>
            );
          })}
        </div>

        <div className="flex items-center gap-1">
          {(["cards", "network"] as const).map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={`rounded px-2 py-1 font-mono text-[11px] transition-colors duration-200 ${
                tab === id ? "bg-surface text-text" : "text-text-dim hover:text-text"
              }`}
            >
              {id === "cards" ? "comparison" : "network"}
            </button>
          ))}
        </div>
      </div>

      <div ref={listRef} className="mt-5 space-y-6">
        {tab === "cards" &&
          related.map((rel) => {
            const style = verdictStyle(rel.relationship_type);
            const { dimension, body } = splitRationale(rel.rationale);

            return (
              <article
                key={rel.relationship_id}
                data-pair
                className={`overflow-hidden rounded-lg border ${style.ring}`}
              >
                <div className="grid gap-3 p-3 md:grid-cols-[1fr_auto_1fr] md:items-stretch md:gap-0">
                  <EvidenceCard
                    eyebrow="this fact"
                    factType={fact.fact_type}
                    statement={fact.statement}
                    subject={fact.subject}
                    value={fact.normalized_value}
                    unit={fact.unit}
                    scope={fact.time_scope}
                    quote={fact.grounding?.quote ?? null}
                    source={sourceTitle || `document ${fact.document_id}`}
                    page={fact.grounding?.page_number ?? null}
                    accent
                  />

                  {/* Verdict ribbon. Vertical on wide screens so it reads as a
                      join between the two cards rather than a heading above
                      them; horizontal when they stack. */}
                  <div className="flex items-center justify-center md:px-3">
                    <div
                      className={`flex w-full items-center justify-center gap-2 rounded px-3 py-2 md:h-full md:w-auto md:flex-col ${style.bg}`}
                    >
                      <span className={`h-px w-6 md:h-6 md:w-px ${style.bar}`} />
                      <span
                        className={`whitespace-nowrap font-mono text-[10px] font-medium tracking-[0.18em] ${style.text} md:[writing-mode:vertical-rl]`}
                      >
                        {style.label}
                      </span>
                      <span className={`h-px w-6 md:h-6 md:w-px ${style.bar}`} />
                    </div>
                  </div>

                  <EvidenceCard
                    eyebrow="related fact"
                    factType={rel.fact_type}
                    statement={rel.statement}
                    subject={rel.subject}
                    value={rel.normalized_value}
                    unit={rel.unit}
                    scope={rel.time_scope}
                    quote={rel.grounding?.quote ?? null}
                    source={rel.document_title ?? rel.document_filename}
                    page={rel.grounding?.page_number ?? null}
                  />
                </div>

                {/* The rationale. This is the product -- a bare verdict is not
                    actionable, the explanation is. */}
                <div className="border-t border-border bg-bg/60 px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-dim">
                      Why
                    </span>
                    {dimension && (
                      <span
                        className={`rounded px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide ${style.bg} ${style.text}`}
                      >
                        differs by {dimension}
                      </span>
                    )}
                    {rel.relationship_confidence != null && (
                      <span className="font-mono text-[10px] text-text-dim">
                        confidence {rel.relationship_confidence.toFixed(2)}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-text-dim">
                    {body || "No rationale recorded."}
                  </p>
                  {onOpenFact && (
                    <button
                      type="button"
                      onClick={() => onOpenFact(rel.fact_id)}
                      className="mt-3 font-mono text-[11px] text-text-dim transition-colors duration-200 hover:text-accent"
                    >
                      open related fact →
                    </button>
                  )}
                </div>
              </article>
            );
          })}

        {tab === "network" && (
          <NetworkView fact={fact} related={related} onOpenFact={onOpenFact} />
        )}
      </div>
    </div>
  );
}

/**
 * Secondary view: this fact as a hub with its neighbours around it.
 *
 * Plain SVG on a radial layout. There is no force simulation because there is
 * nothing to simulate -- this is always one centre and its direct neighbours,
 * so the positions are known analytically and a physics engine would only add
 * jitter and a dependency.
 */
function NetworkView({
  fact,
  related,
  onOpenFact,
}: {
  fact: Fact;
  related: RelatedFact[];
  onOpenFact?: (factId: number) => void;
}) {
  const size = 460;
  const centre = size / 2;
  const radius = size * 0.36;

  return (
    <div data-pair className="rounded-lg border border-border bg-surface/30 p-4">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="mx-auto h-auto w-full max-w-md"
        role="img"
        aria-label={`${related.length} facts related to fact ${fact.id}`}
      >
        {related.map((rel, i) => {
          const angle = (i / related.length) * Math.PI * 2 - Math.PI / 2;
          const x = centre + Math.cos(angle) * radius;
          const y = centre + Math.sin(angle) * radius;
          const stroke =
            rel.relationship_type === "corroborates"
              ? "var(--corroborate)"
              : rel.relationship_type === "contradicts"
                ? "var(--contradict)"
                : "var(--accent)";
          return (
            <g key={rel.relationship_id}>
              <line
                x1={centre}
                y1={centre}
                x2={x}
                y2={y}
                stroke={stroke}
                strokeWidth={1.5}
                opacity={0.55}
              />
              <circle
                cx={x}
                cy={y}
                r={9}
                fill="var(--surface)"
                stroke={stroke}
                strokeWidth={2}
                className="cursor-pointer"
                onClick={() => onOpenFact?.(rel.fact_id)}
              />
              <title>
                {rel.relationship_type}: {rel.statement}
              </title>
            </g>
          );
        })}
        <circle
          cx={centre}
          cy={centre}
          r={13}
          fill="var(--accent)"
          stroke="var(--bg)"
          strokeWidth={3}
        />
      </svg>
      <p className="mt-3 text-center font-mono text-[11px] text-text-dim">
        centre is this fact · {related.length} related · click a node to open it
      </p>
    </div>
  );
}
