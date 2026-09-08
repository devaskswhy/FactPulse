"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { gsap } from "gsap";

import {
  getSchema,
  getSubjects,
  type SchemaResponse,
  type SubjectRegistryResponse,
} from "@/lib/api";
import { DURATION, STAGGER, prefersReducedMotion } from "@/lib/motion";

/**
 * Two registries, one panel: discovered fact types, and resolved entities.
 *
 * The labels matter in both cases. The types were not designed by anyone --
 * each was invented by the model when it met a fact it had no name for, and
 * the list grows as documents arrive. The entities are not a directory either;
 * they are whatever subjects the corpus produced, after canonicalize_subject
 * folds mechanical spelling differences together, which is why "Delhivery" and
 * "Delhivery Limited" appear as one row with a "2 spellings" note rather than
 * as two entries a filter would have to be run twice to cover.
 *
 * Tabs rather than two stacked panels: the previous layout already duplicated
 * the type list as a wall of chips above the fact list, and the fix for a
 * crowded screen is not a third copy of the same information.
 */

type Tab = "types" | "entities";

export function TaxonomyPanel({
  selectedType,
  onSelectType,
  selectedSubject,
  onSelectSubject,
  refreshKey,
}: {
  selectedType: string | null;
  onSelectType: (factType: string | null) => void;
  selectedSubject: string | null;
  onSelectSubject: (subject: string | null) => void;
  /** Bump to re-fetch after an ingest, so growth is visible. */
  refreshKey?: number;
}) {
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [subjects, setSubjects] = useState<SubjectRegistryResponse | null>(null);
  const [tab, setTab] = useState<Tab>("types");
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
    getSubjects()
      .then((data) => {
        if (!cancelled) setSubjects(data);
      })
      .catch(() => {
        /* as above */
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  useLayoutEffect(() => {
    if (prefersReducedMotion() || !listRef.current || !open) return;
    const rows = listRef.current.querySelectorAll("[data-registry-row]");
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
  }, [schema, subjects, tab, open]);

  if (!schema) return null;

  const grew =
    previousCount != null && schema.total_types > previousCount
      ? schema.total_types - previousCount
      : 0;

  const typeMax = Math.max(1, ...schema.fact_types.map((t) => t.fact_count));
  const subjectMax = Math.max(
    1,
    ...(subjects?.subjects ?? []).map((s) => s.fact_count),
  );

  const count = tab === "types" ? schema.total_types : (subjects?.total_canonical ?? 0);
  const selected = tab === "types" ? selectedType : selectedSubject;
  const clear = tab === "types" ? () => onSelectType(null) : () => onSelectSubject(null);

  return (
    <aside className="w-full shrink-0 border-t border-border bg-surface/30 lg:w-64 lg:border-l lg:border-t-0">
      <div className="flex items-center justify-between px-4 pt-3">
        <div className="flex items-center gap-1">
          {(["types", "entities"] as const).map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={`rounded px-2 py-1 font-mono text-[11px] transition-colors duration-200 ${
                tab === id ? "bg-surface text-text" : "text-text-dim hover:text-text"
              }`}
            >
              {id}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="font-mono text-[11px] text-text-dim transition-colors duration-200 hover:text-text"
        >
          {count} {open ? "−" : "+"}
        </button>
      </div>

      {open && (
        <>
          <p className="px-4 pb-3 pt-2 text-[11px] leading-relaxed text-text-dim">
            {tab === "types" ? (
              <>
                Invented by the model as it met each kind of fact. Nothing here
                was predefined.
                {grew > 0 && <span className="ml-1 text-accent">+{grew} new</span>}
              </>
            ) : (
              <>
                Subjects the corpus produced, with spelling variants folded
                together. Click one to see only its facts.
              </>
            )}
          </p>

          <div
            ref={listRef}
            className="max-h-[45vh] overflow-y-auto pb-3 lg:max-h-[calc(100vh-13rem)]"
          >
            {selected && (
              <button
                type="button"
                onClick={clear}
                className="mx-4 mb-2 block max-w-[calc(100%-2rem)] truncate rounded border border-accent/40 px-2 py-1 text-left font-mono text-[10px] text-accent"
              >
                clear filter: {selected} ×
              </button>
            )}

            {tab === "types" &&
              schema.fact_types.map((type) => {
                const active = selectedType === type.name;
                return (
                  <button
                    key={type.name}
                    data-registry-row
                    type="button"
                    onClick={() => onSelectType(active ? null : type.name)}
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
                        count-1 types is what an evolving vocabulary looks
                        like. */}
                    <div className="mt-1 h-px w-full bg-border">
                      <div
                        className={active ? "h-full bg-accent" : "h-full bg-text-dim/40"}
                        style={{ width: `${(type.fact_count / typeMax) * 100}%` }}
                      />
                    </div>
                  </button>
                );
              })}

            {tab === "entities" &&
              (subjects?.subjects ?? []).map((entity) => {
                const active = selectedSubject === entity.canonical;
                const label = entity.variants[0]?.subject ?? entity.canonical;
                return (
                  <button
                    key={entity.canonical}
                    data-registry-row
                    type="button"
                    onClick={() =>
                      onSelectSubject(active ? null : entity.canonical)
                    }
                    className={`block w-full px-4 py-1.5 text-left transition-colors duration-200 ${
                      active ? "bg-surface" : "hover:bg-surface/60"
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span
                        className={`truncate font-mono text-[11px] ${
                          active ? "text-accent" : "text-text"
                        }`}
                        title={entity.variants.map((v) => v.subject).join(" · ")}
                      >
                        {label}
                      </span>
                      <span className="font-mono text-[10px] text-text-dim">
                        {entity.fact_count}
                      </span>
                    </div>
                    {entity.variants.length > 1 && (
                      <span className="mt-0.5 block font-mono text-[10px] text-accent/80">
                        {entity.variants.length} spellings
                      </span>
                    )}
                    <div className="mt-1 h-px w-full bg-border">
                      <div
                        className={active ? "h-full bg-accent" : "h-full bg-text-dim/40"}
                        style={{ width: `${(entity.fact_count / subjectMax) * 100}%` }}
                      />
                    </div>
                  </button>
                );
              })}

            {tab === "entities" && !subjects && (
              <p className="px-4 font-mono text-[11px] text-text-dim">loading…</p>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
