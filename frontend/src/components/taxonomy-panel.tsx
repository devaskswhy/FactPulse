"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { gsap } from "gsap";

import { getSchema, type SchemaResponse } from "@/lib/api";
import { DURATION, STAGGER, prefersReducedMotion } from "@/lib/motion";

/**
 * Discovered fact types.
 *
 * The label matters. These are not a taxonomy someone designed and the
 * extractor picked from -- there is no enum anywhere in the system. Each
 * label was invented by the model when it met a fact it had no name for, and
 * the list grows as documents arrive. Calling it "discovered" rather than
 * "categories" is the difference between describing what this is and
 * misrepresenting it.
 */

export function TaxonomyPanel({
  selected,
  onSelect,
  refreshKey,
}: {
  selected: string | null;
  onSelect: (factType: string | null) => void;
  /** Bump to re-fetch after an ingest, so growth is visible. */
  refreshKey?: number;
}) {
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [open, setOpen] = useState(true);
  const [previousCount, setPreviousCount] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    getSchema()
      .then((data) => {
        if (cancelled) return;
        setSchema((prev) => {
          if (prev) setPreviousCount(prev.total_types);
          return data;
        });
      })
      .catch(() => {
        /* the panel is supplementary; a failure should not break the screen */
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  useLayoutEffect(() => {
    if (!schema || prefersReducedMotion() || !listRef.current || !open) return;
    const rows = listRef.current.querySelectorAll("[data-type-row]");
    if (!rows.length) return;
    const tween = gsap.fromTo(
      rows,
      { opacity: 0, x: -6 },
      {
        opacity: 1,
        x: 0,
        duration: DURATION.fast,
        ease: "power2.out",
        stagger: Math.min(STAGGER.tight, 0.3 / rows.length),
        clearProps: "all",
      },
    );
    return () => {
      tween.kill();
    };
  }, [schema, open]);

  if (!schema) return null;

  const grew =
    previousCount != null && schema.total_types > previousCount
      ? schema.total_types - previousCount
      : 0;

  const max = Math.max(1, ...schema.fact_types.map((t) => t.fact_count));

  return (
    <aside className="w-full shrink-0 border-t border-border bg-surface/30 lg:w-64 lg:border-l lg:border-t-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim">
          Discovered fact types
        </span>
        <span className="font-mono text-[11px] text-text-dim">
          {schema.total_types} {open ? "−" : "+"}
        </span>
      </button>

      {open && (
        <>
          <p className="px-4 pb-3 text-[11px] leading-relaxed text-text-dim">
            Invented by the model as it met each kind of fact. Nothing here was
            predefined.
            {grew > 0 && (
              <span className="ml-1 text-accent">+{grew} new</span>
            )}
          </p>

          <div ref={listRef} className="max-h-[45vh] overflow-y-auto pb-3 lg:max-h-none">
            {selected && (
              <button
                type="button"
                onClick={() => onSelect(null)}
                className="mx-4 mb-2 block rounded border border-accent/40 px-2 py-1 font-mono text-[10px] text-accent"
              >
                clear filter: {selected} ×
              </button>
            )}

            {schema.fact_types.map((type) => {
              const active = selected === type.name;
              return (
                <button
                  key={type.name}
                  data-type-row
                  type="button"
                  onClick={() => onSelect(active ? null : type.name)}
                  className={`block w-full px-4 py-1.5 text-left transition-colors duration-200 ${
                    active ? "bg-surface" : "hover:bg-surface/60"
                  }`}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span
                      className={`truncate font-mono text-[11px] ${
                        active ? "text-accent" : "text-text"
                      }`}
                      title={type.name}
                    >
                      {type.name}
                    </span>
                    <span className="font-mono text-[10px] text-text-dim">
                      {type.fact_count}
                    </span>
                  </div>
                  {/* A bar rather than just a number: the shape of the
                      distribution is the interesting part -- a long tail of
                      count-1 types is what an evolving vocabulary looks like. */}
                  <div className="mt-1 h-px w-full bg-border">
                    <div
                      className={active ? "h-full bg-accent" : "h-full bg-text-dim/40"}
                      style={{ width: `${(type.fact_count / max) * 100}%` }}
                    />
                  </div>
                </button>
              );
            })}
          </div>
        </>
      )}
    </aside>
  );
}
