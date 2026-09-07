"use client";

/* ==========================================================================
   APP SHELL — MOTION RULES. Please read before adding animation here.

   The landing explainer above this pins, scrubs and scroll-jacks. That is
   correct THERE and wrong HERE, and the distinction is deliberate.

   The explainer is a pitch. Someone is deciding whether this product is worth
   their time, they will scroll it once, and pacing them through four ideas in
   sequence IS the argument.

   Everything below is a tool. Someone is trying to find a fact, check a quote,
   or clear a review queue — probably for the tenth time today. Motion here has
   exactly one job: make a change in state legible. Anything beyond that is a
   tax charged on every repetition.

   So, in this subtree:
     - 200-300ms only (DURATION.fast). No DURATION.base, no DURATION.slow.
     - Stagger reveals on lists, capped so a long list never crawls.
     - NO pinning. NO scrub. NO scroll-jacking. Never hijack the wheel.
     - Nothing animates on a route change that would delay reading content.

   If a future screen seems to want a cinematic moment, it belongs in the
   explainer, not here.
   ========================================================================== */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { gsap } from "gsap";

import {
  ApiError,
  documentLabel,
  getDocuments,
  statusTone,
  type DocumentList,
} from "@/lib/api";
import { DURATION, STAGGER, prefersReducedMotion } from "@/lib/motion";
import { UploadScreen } from "@/components/screens/upload-screen";
import { FactExplorerScreen } from "@/components/screens/fact-explorer-screen";
import { ReviewQueueScreen } from "@/components/screens/review-queue-screen";

export type ScreenId = "upload" | "facts" | "review";

const SCREENS: { id: ScreenId; label: string; hint: string }[] = [
  { id: "facts", label: "Fact Explorer", hint: "Browse and filter the layer" },
  { id: "review", label: "Review Queue", hint: "Resolve flagged extractions" },
  { id: "upload", label: "Upload", hint: "Add a document" },
];

const TONE_CLASS: Record<string, string> = {
  done: "bg-corroborate",
  working: "bg-accent animate-pulse",
  warn: "bg-contradict",
  idle: "bg-border",
};

export function AppShell() {
  const [screen, setScreen] = useState<ScreenId>("facts");
  const [data, setData] = useState<DocumentList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);

  const railRef = useRef<HTMLUListElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);

  /**
   * `showSpinner` is false for the mount fetch and true for an explicit
   * refresh. `loading` already initialises to true, so flipping it again on
   * mount would be a redundant synchronous setState inside the effect -- an
   * extra render before any data can possibly have arrived.
   */
  const load = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    try {
      setData(await getDocuments());
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not load documents.",
      );
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // The rule cannot see that every setState inside `load` happens after an
    // await, so none of them is synchronous with this effect. The one that
    // genuinely was -- setLoading(true) on mount -- has been removed rather
    // than suppressed; see the note on `load`.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  // Stagger the rail in when documents arrive. Capped total so a corpus of
  // fifty documents does not take two seconds to become readable.
  useLayoutEffect(() => {
    if (!data || prefersReducedMotion() || !railRef.current) return;
    const items = railRef.current.querySelectorAll("[data-rail-item]");
    if (!items.length) return;

    const stagger = Math.min(STAGGER.list, 0.4 / items.length);
    const tween = gsap.fromTo(
      items,
      { opacity: 0, x: -8 },
      {
        opacity: 1,
        x: 0,
        duration: DURATION.fast,
        ease: "power2.out", // a plain decay; nothing here needs an in-out
        stagger,
        clearProps: "all",
      },
    );
    return () => {
      tween.kill();
    };
  }, [data]);

  // Route change: a short cross-fade so the swap is visible, nothing that
  // delays reading the new screen.
  useLayoutEffect(() => {
    if (prefersReducedMotion() || !mainRef.current) return;
    const tween = gsap.fromTo(
      mainRef.current,
      { opacity: 0, y: 6 },
      { opacity: 1, y: 0, duration: DURATION.fast, ease: "power2.out" },
    );
    return () => {
      tween.kill();
    };
  }, [screen]);

  const totals = data?.totals;

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      {/* Top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between gap-4 border-b border-border bg-bg/85 px-5 py-3 backdrop-blur">
        <div className="flex items-center gap-3">
          <span className="size-2 rounded-full bg-accent" />
          <span className="font-mono text-xs uppercase tracking-[0.28em] text-text-dim">
            FactPulse
          </span>
          {totals && (
            <span className="hidden font-mono text-xs text-text-dim md:inline">
              · {totals.documents} docs · {totals.facts} facts ·{" "}
              {totals.relationships} links
            </span>
          )}
        </div>

        <nav className="flex items-center gap-1">
          {SCREENS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setScreen(item.id)}
              title={item.hint}
              aria-current={screen === item.id ? "page" : undefined}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors duration-200 ${
                screen === item.id
                  ? "bg-surface text-text"
                  : "text-text-dim hover:text-text"
              }`}
            >
              {item.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setScreen("upload")}
            className="ml-2 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg transition-opacity duration-200 hover:opacity-90"
          >
            Upload
          </button>
        </nav>
      </header>

      <div className="flex flex-1">
        {/* Left rail */}
        <aside className="hidden w-72 shrink-0 border-r border-border bg-surface/40 md:flex md:flex-col">
          <div className="flex items-baseline justify-between border-b border-border px-5 py-3">
            <span className="font-mono text-xs uppercase tracking-[0.25em] text-text-dim">
              Knowledge layer
            </span>
            <button
              type="button"
              onClick={() => void load(true)}
              className="font-mono text-xs text-text-dim transition-colors duration-200 hover:text-accent"
            >
              refresh
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {loading && (
              <p className="px-5 py-4 font-mono text-xs text-text-dim">
                loading…
              </p>
            )}

            {error && !loading && (
              <div className="px-5 py-4">
                <p className="text-sm text-contradict">Backend unreachable</p>
                <p className="mt-2 text-xs leading-relaxed text-text-dim">
                  {error}
                </p>
                <code className="mt-3 block rounded bg-bg px-2 py-1.5 font-mono text-[11px] text-text-dim">
                  cd backend && python run.py
                </code>
              </div>
            )}

            {data && !error && data.documents.length === 0 && (
              <p className="px-5 py-4 text-sm leading-relaxed text-text-dim">
                No documents yet. Upload a PDF to start the layer.
              </p>
            )}

            {data && !error && data.documents.length > 0 && (
              <ul ref={railRef} className="py-2">
                {data.documents.map((doc) => {
                  const active = selected === doc.id;
                  return (
                    <li key={doc.id} data-rail-item>
                      <button
                        type="button"
                        onClick={() => setSelected(active ? null : doc.id)}
                        className={`group w-full border-l-2 px-5 py-3 text-left transition-colors duration-200 ${
                          active
                            ? "border-accent bg-surface"
                            : "border-transparent hover:bg-surface/60"
                        }`}
                      >
                        <div className="flex items-start gap-2">
                          <span
                            className={`mt-1.5 size-1.5 shrink-0 rounded-full ${
                              TONE_CLASS[statusTone(doc.status)]
                            }`}
                            title={doc.status}
                          />
                          <span className="truncate text-sm text-text">
                            {documentLabel(doc)}
                          </span>
                        </div>
                        {/* Two lines rather than one wrapping line: at this
                            rail width the counts and the review badge do not
                            fit together, and letting them wrap breaks the
                            badge across lines mid-phrase. */}
                        <div className="mt-1.5 space-y-0.5 pl-3.5 font-mono text-[11px] text-text-dim">
                          <div className="truncate">
                            {doc.page_count ?? "?"}p · {doc.fact_count} facts ·{" "}
                            {doc.relationship_count} links
                          </div>
                          {doc.open_review_count > 0 && (
                            <div className="text-accent">
                              {doc.open_review_count} to review
                            </div>
                          )}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {totals && (
            <div className="border-t border-border px-5 py-3 font-mono text-[11px] leading-relaxed text-text-dim">
              {totals.cross_document_relationships} cross-document links
              <br />
              {totals.open_review_items} open review items
            </div>
          )}
        </aside>

        {/* Main */}
        <main className="min-w-0 flex-1">
          <div ref={mainRef} className="h-full">
            {screen === "upload" && (
              <UploadScreen onIngested={() => void load(true)} />
            )}
            {screen === "facts" && (
              <FactExplorerScreen documentId={selected} totals={totals} />
            )}
            {screen === "review" && (
              <ReviewQueueScreen openCount={totals?.open_review_items ?? 0} />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
