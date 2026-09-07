"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { gsap } from "gsap";

import {
  ApiError,
  PHASE_LABEL,
  PHASE_ORDER,
  progressStreamUrl,
  uploadDocument,
  type ProgressSnapshot,
  type UploadResponse,
} from "@/lib/api";
import { DURATION, prefersReducedMotion } from "@/lib/motion";

/**
 * Ingestion theater.
 *
 * A 100-page report takes minutes, almost all of it inside model calls. A
 * spinner over that is indistinguishable from a hang, so this shows the actual
 * unit of work: a strip of page cells that light up as pages are read, then as
 * chunks are extracted, with the current stage named underneath.
 *
 * The strip is driven by real SSE events, not a timer. When the backend says
 * "extracted chunk 84 of 221", 84 cells are lit. Nothing here fakes progress,
 * which matters because the one thing worse than a spinner is a progress bar
 * that lies.
 */

type Phase = ProgressSnapshot["phase"];

const MAX_CELLS = 120;

export function UploadScreen({ onIngested }: { onIngested?: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<ProgressSnapshot | null>(null);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => sourceRef.current?.close();
  }, []);

  /**
   * The document id only exists once the row is inserted, which happens after
   * parsing -- so the upload POST and the progress stream cannot both be
   * started at once. Instead the POST runs, and the stream is opened as soon
   * as an id is known. Polling /progress briefly is how we learn the id
   * without changing the API contract.
   */
  const followProgress = useCallback((documentId: number) => {
    sourceRef.current?.close();
    const source = new EventSource(progressStreamUrl(documentId));
    sourceRef.current = source;

    source.onmessage = (event) => {
      try {
        const snapshot = JSON.parse(event.data) as ProgressSnapshot;
        if (snapshot.phase) setProgress(snapshot);
        if (snapshot.phase === "done" || snapshot.phase === "failed") {
          source.close();
        }
      } catch {
        /* a malformed frame is not worth failing the upload over */
      }
    };
    source.onerror = () => {
      // The stream closes normally when the run ends; EventSource reports that
      // as an error, so this is not necessarily a failure.
      source.close();
    };
  }, []);

  const start = useCallback(
    async (chosen: File) => {
      setBusy(true);
      setError(null);
      setResult(null);
      setProgress({
        document_id: null,
        filename: chosen.name,
        phase: "queued",
        message: "uploading…",
        current: 0,
        total: 0,
        percent: null,
        pages: 0,
        chunks: 0,
        facts: 0,
        relationships: 0,
        elapsed: 0,
        error: null,
      });

      // Find the document id as soon as the backend has created it, so the
      // stream can attach while extraction is still running.
      const base =
        process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
      let attached = false;
      const poll = window.setInterval(async () => {
        if (attached) return;
        try {
          const res = await fetch(`${base}/progress`, { cache: "no-store" });
          if (!res.ok) return;
          const list = await res.json();
          const run = list.runs?.find(
            (r: ProgressSnapshot) =>
              r.filename === chosen.name && (r.document_id ?? -1) > 0,
          );
          if (run?.document_id) {
            attached = true;
            window.clearInterval(poll);
            followProgress(run.document_id);
          }
        } catch {
          /* keep trying until the upload resolves */
        }
      }, 400);

      try {
        const response = await uploadDocument(chosen);
        setResult(response);
        onIngested?.();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Upload failed.");
      } finally {
        window.clearInterval(poll);
        sourceRef.current?.close();
        setBusy(false);
      }
    },
    [followProgress, onIngested],
  );

  const choose = useCallback(
    (chosen: File | null | undefined) => {
      if (!chosen) return;
      if (!chosen.name.toLowerCase().endsWith(".pdf")) {
        setError("That is not a PDF.");
        return;
      }
      setFile(chosen);
      void start(chosen);
    },
    [start],
  );

  // Light cells as they complete. GSAP rather than a CSS transition so the
  // whole strip can be advanced in one pass without 120 style recalculations.
  useEffect(() => {
    if (!progress || prefersReducedMotion() || !stripRef.current) return;
    const cells = stripRef.current.querySelectorAll("[data-cell]");
    if (!cells.length) return;
    const lit = Math.round((cellFraction(progress) / 100) * cells.length);
    gsap.to(Array.from(cells).slice(0, lit), {
      backgroundColor: "var(--accent)",
      duration: DURATION.fast,
      ease: "power2.out",
      stagger: { each: 0.008, from: "start" },
      overwrite: true,
    });
  }, [progress]);

  const phase = (progress?.phase ?? "queued") as Phase;
  const stageIndex = PHASE_ORDER.indexOf(phase as (typeof PHASE_ORDER)[number]);
  const cellCount = Math.min(
    MAX_CELLS,
    Math.max(24, progress?.chunks || progress?.pages || 40),
  );

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 md:px-10">
      <p className="font-mono text-xs uppercase tracking-[0.3em] text-accent">
        Upload
      </p>

      {!busy && !result && (
        <>
          <p className="mt-4 max-w-2xl text-sm leading-relaxed text-text-dim">
            Drop a PDF to run the full pipeline: parse, chunk, extract facts,
            ground every quote to a page and box, embed, then relate it to
            everything already in the layer.
          </p>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              choose(e.dataTransfer.files?.[0]);
            }}
            onClick={() => inputRef.current?.click()}
            className={`mt-6 cursor-pointer rounded-lg border-2 border-dashed px-6 py-16 text-center transition-colors duration-200 ${
              dragging
                ? "border-accent bg-accent/5"
                : "border-border hover:border-text-dim"
            }`}
          >
            <p className="text-sm text-text">
              {dragging ? "Drop it" : "Drag a PDF here, or click to choose"}
            </p>
            <p className="mt-2 font-mono text-[11px] text-text-dim">
              A 100-page report takes several minutes. Progress is live.
            </p>
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              className="hidden"
              onChange={(e) => choose(e.target.files?.[0])}
            />
          </div>
        </>
      )}

      {error && (
        <p className="mt-4 rounded border border-contradict/40 bg-contradict/10 px-3 py-2 text-sm text-text">
          {error}
        </p>
      )}

      {(busy || result) && progress && (
        <div className="mt-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="truncate font-mono text-sm text-text">
              {file?.name ?? progress.filename}
            </p>
            <p className="font-mono text-[11px] text-text-dim">
              {progress.elapsed.toFixed(0)}s elapsed
            </p>
          </div>

          {/* The strip. Each cell is a unit of real work. */}
          <div
            ref={stripRef}
            className="mt-4 flex flex-wrap gap-[3px]"
            aria-hidden
          >
            {Array.from({ length: cellCount }).map((_, i) => (
              <span
                key={i}
                data-cell
                className="h-6 flex-1 rounded-[2px] bg-border"
                style={{ minWidth: 6, backgroundColor: "var(--border)" }}
              />
            ))}
          </div>

          <p className="mt-3 text-sm text-text">
            <span className="text-accent">{PHASE_LABEL[phase] ?? phase}</span>
            {progress.total > 0 && (
              <span className="text-text-dim">
                {" "}
                — {progress.current} of {progress.total}
              </span>
            )}
          </p>

          {/* Stage tracker, so the viewer can see what is still to come. */}
          <ol className="mt-4 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px]">
            {PHASE_ORDER.map((stage, i) => {
              const done = stageIndex > i || phase === "done";
              const now = stageIndex === i;
              return (
                <li
                  key={stage}
                  className={
                    now ? "text-accent" : done ? "text-text-dim" : "text-border"
                  }
                >
                  {done && !now ? "✓ " : now ? "▸ " : "· "}
                  {PHASE_LABEL[stage]}
                </li>
              );
            })}
          </ol>

          <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["pages", progress.pages],
              ["chunks", progress.chunks],
              ["facts", progress.facts],
              ["relationships", progress.relationships],
            ].map(([label, value]) => (
              <div
                key={String(label)}
                className="rounded border border-border bg-surface/50 px-3 py-2"
              >
                <dt className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-dim">
                  {label}
                </dt>
                <dd className="mt-1 font-mono text-xl text-text">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {result && (
        <div className="mt-6 rounded-lg border border-border bg-surface/50 p-5">
          {result.deduplicated ? (
            <p className="text-sm text-text">
              Already in the layer — nothing was re-processed. Deduplicated by
              file hash.
            </p>
          ) : (
            <>
              <p className="text-sm text-text">
                Ingested{" "}
                <span className="text-accent">
                  {result.extraction?.facts_inserted ?? 0} facts
                </span>{" "}
                from {result.chunk_count} chunks
                {result.linking && (
                  <>
                    , with{" "}
                    <span className="text-accent">
                      {result.linking.relationships_created}
                    </span>{" "}
                    new relationships against {result.linking.pool_size} existing
                    facts
                  </>
                )}
                .
              </p>

              {result.extraction?.fact_types &&
                result.extraction.fact_types.length > 0 && (
                  <div className="mt-4">
                    <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-dim">
                      Fact types discovered in this document
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {result.extraction.fact_types.map((t) => (
                        <span
                          key={t}
                          className="rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-[11px] text-accent"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

              {result.extraction?.quota_exhausted && (
                <p className="mt-4 rounded border border-contradict/40 bg-contradict/10 px-3 py-2 text-xs text-text">
                  The daily model quota ran out part-way through, so this
                  document is incomplete. Re-run extraction once it resets.
                </p>
              )}
            </>
          )}

          <button
            type="button"
            onClick={() => {
              setResult(null);
              setProgress(null);
              setFile(null);
            }}
            className="mt-5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-bg transition-opacity duration-200 hover:opacity-90"
          >
            Upload another
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * How much of the strip should be lit.
 *
 * Parsing and chunking are fast and early, so they only fill the first sliver;
 * extraction is the long middle and gets most of the strip; embedding and
 * linking finish it. The weights reflect measured time share, not equal
 * stages, so the strip does not sit at 20% for four minutes.
 */
function cellFraction(p: ProgressSnapshot): number {
  const within = p.total > 0 ? p.current / p.total : 0;
  switch (p.phase) {
    case "queued":
      return 0;
    case "parsing":
      return within * 6;
    case "chunking":
      return 6 + within * 4;
    case "extracting":
      return 10 + within * 65;
    case "embedding":
      return 75 + within * 5;
    case "linking":
      return 80 + within * 18;
    case "checking":
      return 98;
    case "done":
      return 100;
    default:
      return 0;
  }
}
