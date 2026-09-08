"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { gsap } from "gsap";

import {
  ApiError,
  EVIDENCE_TIER,
  getEvidence,
  pageImageUrl,
  type EvidenceBundle,
} from "@/lib/api";
import { DURATION, prefersReducedMotion } from "@/lib/motion";

/**
 * The highlighter. A page image with the source quote boxed on it.
 *
 * The scaling is the whole point, so it is worth being explicit about why this
 * needs almost no arithmetic. The backend returns `bbox` already converted to
 * PIXELS of the image it also serves, along with that image's intrinsic
 * width and height. Converting those to percentages of the intrinsic size
 * gives an overlay that stays correct at ANY rendered size -- the image can be
 * responsive, zoomed, or letterboxed and the box tracks it, because both are
 * expressed in the same fractional space.
 *
 * The mistake this avoids is scaling by the *rendered* pixel size, which is
 * only knowable after layout, changes on every resize, and is subtly wrong
 * while the image is still loading.
 */

type Props = {
  factId: number;
  onClose?: () => void;
};

export function EvidenceViewer({ factId, onClose }: Props) {
  const [evidence, setEvidence] = useState<EvidenceBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [zoomToBox, setZoomToBox] = useState(true);

  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // No state reset here. Callers mount this with key={factId}, so a
    // different fact is a fresh mount with fresh state -- resetting would be
    // both redundant and a cascading render.
    let cancelled = false;

    getEvidence(factId)
      .then((data) => {
        if (!cancelled) setEvidence(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load evidence.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [factId]);

  // 300ms fade-in, per the app-shell motion rule. Nothing more elaborate.
  useLayoutEffect(() => {
    if (!evidence || prefersReducedMotion() || !rootRef.current) return;
    const tween = gsap.fromTo(
      rootRef.current,
      { opacity: 0, y: 8 },
      { opacity: 1, y: 0, duration: DURATION.fast, ease: "power2.out" },
    );
    return () => {
      tween.kill();
    };
  }, [evidence]);

  if (error) {
    return (
      <div className="p-6">
        <p className="text-sm text-contradict">{error}</p>
      </div>
    );
  }

  if (!evidence) {
    return (
      <div className="p-6 font-mono text-xs text-text-dim">loading evidence…</div>
    );
  }

  const { fact, bbox, page_image_width: iw, page_image_height: ih } = evidence;

  // Fractions of the intrinsic image, so the overlay is resolution-independent.
  const box =
    bbox && iw && ih
      ? {
          left: (bbox.x0 / iw) * 100,
          top: (bbox.y0 / ih) * 100,
          width: ((bbox.x1 - bbox.x0) / iw) * 100,
          height: ((bbox.y1 - bbox.y0) / ih) * 100,
        }
      : null;

  return (
    <div ref={rootRef} className="grid h-full grid-rows-[auto_1fr] lg:grid-cols-2 lg:grid-rows-1">
      {/* Left: the fact itself */}
      <div className="min-w-0 overflow-y-auto border-b border-border p-6 lg:border-b-0 lg:border-r">
        <div className="flex items-start justify-between gap-4">
          <span className="rounded bg-surface px-2 py-1 font-mono text-[11px] text-accent">
            {fact.fact_type}
          </span>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="font-mono text-xs text-text-dim transition-colors duration-200 hover:text-text"
            >
              close
            </button>
          )}
        </div>

        <p className="mt-4 text-lg leading-relaxed text-text">{fact.statement}</p>

        <dl className="mt-6 grid grid-cols-[auto_1fr] gap-x-5 gap-y-2 font-mono text-xs">
          {[
            ["subject", fact.subject],
            ["value", fact.normalized_value],
            ["unit", fact.unit],
            ["time scope", fact.time_scope],
            [
              "confidence",
              fact.confidence != null ? fact.confidence.toFixed(2) : null,
            ],
            ["source", evidence.document_title ?? evidence.document_filename],
            ["page", evidence.page_number != null ? String(evidence.page_number) : null],
          ].map(([label, value]) => (
            <div key={String(label)} className="contents">
              <dt className="text-text-dim">{label}</dt>
              <dd className="min-w-0 break-words text-text">
                {value ?? <span className="text-text-dim">—</span>}
              </dd>
            </div>
          ))}
        </dl>

        {fact.attributes.length > 0 && (
          <div className="mt-6">
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim">
              Attributes
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {fact.attributes.map((attr, i) => (
                <span
                  key={`${attr.key}-${i}`}
                  className="rounded border border-border px-2 py-0.5 font-mono text-[11px] text-text-dim"
                >
                  {attr.key}: <span className="text-text">{attr.value}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {evidence.quote && (
          <div className="mt-6">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim">
                Verbatim source
              </p>
              {fact.evidence_strength && (
                <span
                  className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                    EVIDENCE_TIER[fact.evidence_strength]?.className ?? ""
                  }`}
                >
                  {EVIDENCE_TIER[fact.evidence_strength]?.label}
                </span>
              )}
            </div>
            {/* Amber bar, matching the highlight on the page, so the two read
                as the same thing seen twice. */}
            <blockquote className="mt-2 border-l-2 border-accent bg-surface/60 px-4 py-3 text-sm leading-relaxed text-text">
              {evidence.quote}
            </blockquote>

            {/* Saying WHY the evidence is thin is the useful part. A grade on
                its own tells a reviewer nothing they can act on. */}
            {fact.evidence_gaps.length > 0 && (
              <p className="mt-2 text-xs leading-relaxed text-text-dim">
                Read alone, this quote is missing{" "}
                <span className="text-text">
                  {fact.evidence_gaps.join(", ")}
                </span>
                . The claim relies on surrounding context in the document.
              </p>
            )}
          </div>
        )}

        {!evidence.grounded && (
          <p className="mt-6 rounded border border-contradict/40 bg-contradict/10 px-3 py-2 text-xs leading-relaxed text-text">
            This quote could not be located in the PDF, so there is no highlight
            to show. The fact is kept and flagged rather than discarded.
          </p>
        )}
      </div>

      {/* Right: the page, with the box on it */}
      <div className="min-w-0 bg-bg">
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <span className="font-mono text-[11px] text-text-dim">
            page {evidence.page_number} · {iw}×{ih}px · scale{" "}
            {evidence.render_scale}
          </span>
          {box && (
            <button
              type="button"
              onClick={() => setZoomToBox((z) => !z)}
              className="font-mono text-[11px] text-text-dim transition-colors duration-200 hover:text-accent"
            >
              {zoomToBox ? "show full page" : "zoom to highlight"}
            </button>
          )}
        </div>

        <div className="h-[calc(100%-2.5rem)] overflow-auto p-4">
          <div className="relative mx-auto w-full max-w-2xl">
            {!imageLoaded && (
              <div className="absolute inset-0 grid place-items-center font-mono text-xs text-text-dim">
                rendering page…
              </div>
            )}

            {/* eslint-disable-next-line @next/next/no-img-element -- the page
                image is served by the FastAPI backend at a runtime host, not
                from a configured remote pattern; next/image would need the
                backend registered in next.config and buys nothing here. */}
            <img
              src={pageImageUrl(evidence.page_image_url)}
              alt={`Page ${evidence.page_number} of ${evidence.document_filename}`}
              width={iw}
              height={ih}
              onLoad={() => setImageLoaded(true)}
              className={`w-full border border-border transition-opacity duration-300 ${
                imageLoaded ? "opacity-100" : "opacity-0"
              }`}
            />

            {box && imageLoaded && (
              <>
                <div
                  className="pointer-events-none absolute border-2 border-accent bg-accent/25"
                  style={{
                    left: `${box.left}%`,
                    top: `${box.top}%`,
                    width: `${box.width}%`,
                    height: `${box.height}%`,
                  }}
                />
                {/* A faint full-width rule at the same height, so the eye finds
                    the highlight on a dense page without hunting. */}
                <div
                  className="pointer-events-none absolute left-0 right-0 border-t border-accent/25"
                  style={{ top: `${box.top + box.height / 2}%` }}
                />
              </>
            )}
          </div>

          {box && zoomToBox && imageLoaded && (
            <div className="mx-auto mt-4 max-w-2xl">
              <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-text-dim">
                Highlight, enlarged
              </p>
              {/* The same image blown up and offset so only the box shows.
                  Sharper than a second server render, and it cannot disagree
                  with the overlay above because both are driven by the same
                  numbers.

                  If the box is 30% of the image wide, the image must be
                  100/0.30 = 333% wide for the box to fill this frame, and
                  shifted left by (box.left / box.width) of its own width. */}
              <div
                className="relative mt-2 overflow-hidden border border-accent/40 bg-surface"
                style={{
                  aspectRatio: `${(box.width / 100) * iw} / ${(box.height / 100) * ih}`,
                }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- as above */}
                <img
                  src={pageImageUrl(evidence.page_image_url)}
                  alt=""
                  aria-hidden
                  className="absolute max-w-none"
                  style={{
                    width: `${(100 / box.width) * 100}%`,
                    left: `${-(box.left / box.width) * 100}%`,
                    top: `${-(box.top / box.height) * 100}%`,
                    height: `${(100 / box.height) * 100}%`,
                  }}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
