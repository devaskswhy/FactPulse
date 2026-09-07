"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { gsap } from "gsap";

import {
  ApiError,
  getReviewQueue,
  resolveReviewItem,
  type FactCorrection,
  type ResolveAction,
  type ReviewEntry,
  type ReviewQueue,
} from "@/lib/api";
import { DURATION, STAGGER, prefersReducedMotion } from "@/lib/motion";
import { EvidenceViewer } from "@/components/evidence-viewer";

/**
 * The review queue as a workflow.
 *
 * Every entry arrives with the context needed to judge it -- the fact and its
 * values, or the chunk text a failed call came from -- so a reviewer never has
 * to go looking. Resolving requires a note, because a decision without a reason
 * is not reviewable by the next person.
 */

const ISSUE_BLURB: Record<string, string> = {
  unverified_quote:
    "The model's quote is not a substring of the source text, so this fact is not grounded in the document.",
  ungrounded_quote:
    "The quote checks out against the chunk but could not be located in the PDF, so it has no highlight box.",
  low_confidence: "The model's own confidence fell below the review threshold.",
  ambiguous_unit:
    "A numeric value with no unit recorded — the number cannot be compared or interpreted on its own.",
  borderline_confidence:
    "Confidence landed in the band that is neither clearly wrong nor safe to accept unread.",
  extraction_failed: "The model call failed for this chunk, so its facts are missing.",
  quota_exhausted:
    "The daily model quota ran out mid-document, so this document's facts are incomplete.",
};

const ISSUE_TONE: Record<string, string> = {
  unverified_quote: "text-contradict border-contradict/40 bg-contradict/10",
  extraction_failed: "text-contradict border-contradict/40 bg-contradict/10",
  quota_exhausted: "text-contradict border-contradict/40 bg-contradict/10",
  ambiguous_unit: "text-accent border-accent/40 bg-accent/10",
  low_confidence: "text-accent border-accent/40 bg-accent/10",
  borderline_confidence: "text-accent border-accent/40 bg-accent/10",
  ungrounded_quote: "text-text-dim border-border bg-surface",
};

export function ReviewQueueScreen({
  openCount,
  onResolved,
}: {
  openCount: number;
  onResolved?: () => void;
}) {
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [active, setActive] = useState<ReviewEntry | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const load = useCallback(
    async (issueType: string | null) => {
      try {
        setQueue(await getReviewQueue(issueType));
        setError(null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not load the queue.");
      }
    },
    [],
  );

  useEffect(() => {
    // Every setState inside `load` happens after an await, so none is
    // synchronous with this effect; the rule cannot see that.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(filter);
  }, [load, filter]);

  useLayoutEffect(() => {
    if (!queue || prefersReducedMotion() || !listRef.current) return;
    const rows = listRef.current.querySelectorAll("[data-review-row]");
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
  }, [queue]);

  const resolve = useCallback(
    async (
      entry: ReviewEntry,
      action: ResolveAction,
      note: string,
      correction?: FactCorrection,
    ) => {
      setPending(entry.id);
      try {
        await resolveReviewItem(entry.id, {
          action,
          resolution_note: note,
          ...(correction ? { correction } : {}),
        });
        setActive(null);
        await load(filter);
        onResolved?.();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not resolve.");
      } finally {
        setPending(null);
      }
    },
    [filter, load, onResolved],
  );

  if (error && !queue) {
    return <p className="p-6 text-sm text-contradict">{error}</p>;
  }

  return (
    <div className="flex h-full flex-col lg:flex-row">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-border px-5 py-3">
          <p className="font-mono text-xs uppercase tracking-[0.3em] text-accent">
            Review Queue
          </p>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-dim">
            Nothing doubtful is discarded. A fact that fails a check is still
            stored, and queued here with the context to judge it.
          </p>

          {queue && Object.keys(queue.by_issue_type).length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setFilter(null)}
                className={`rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors duration-200 ${
                  filter === null
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-border text-text-dim hover:text-text"
                }`}
              >
                all {openCount || queue.total}
              </button>
              {Object.entries(queue.by_issue_type).map(([issue, n]) => (
                <button
                  key={issue}
                  type="button"
                  onClick={() => setFilter(issue === filter ? null : issue)}
                  className={`rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors duration-200 ${
                    filter === issue
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-border text-text-dim hover:text-text"
                  }`}
                >
                  {issue} <span className="opacity-60">{n}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
          {!queue && (
            <p className="px-5 py-4 font-mono text-xs text-text-dim">loading queue…</p>
          )}

          {queue && queue.entries.length === 0 && (
            <p className="px-5 py-6 text-sm text-text-dim">
              Nothing open{filter ? ` under ${filter}` : ""}. The pipeline was
              confident about everything it kept.
            </p>
          )}

          {queue?.entries.map((entry) => (
            <ReviewRow
              key={entry.id}
              entry={entry}
              busy={pending === entry.id}
              expanded={active?.id === entry.id}
              onToggle={() => setActive(active?.id === entry.id ? null : entry)}
              onResolve={resolve}
            />
          ))}
        </div>
      </div>

      {active?.fact && (
        <div className="min-w-0 border-t border-border lg:w-[45%] lg:border-l lg:border-t-0">
          <div className="border-b border-border px-4 py-2 font-mono text-[11px] text-text-dim">
            evidence for fact {active.fact.fact_id}
          </div>
          <div className="h-[calc(100%-2.25rem)] overflow-y-auto">
            <EvidenceViewer key={active.fact.fact_id} factId={active.fact.fact_id} />
          </div>
        </div>
      )}
    </div>
  );
}

function ReviewRow({
  entry,
  busy,
  expanded,
  onToggle,
  onResolve,
}: {
  entry: ReviewEntry;
  busy: boolean;
  expanded: boolean;
  onToggle: () => void;
  onResolve: (
    entry: ReviewEntry,
    action: ResolveAction,
    note: string,
    correction?: FactCorrection,
  ) => void;
}) {
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [correction, setCorrection] = useState<FactCorrection>({});

  const tone = ISSUE_TONE[entry.issue_type] ?? "text-text-dim border-border bg-surface";
  const canEdit = entry.fact != null;

  return (
    <article
      data-review-row
      className={`border-b border-border/60 px-5 py-4 ${expanded ? "bg-surface/40" : ""}`}
    >
      <button type="button" onClick={onToggle} className="block w-full text-left">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded border px-2 py-0.5 font-mono text-[10px] ${tone}`}>
            {entry.issue_type}
          </span>
          {entry.fact && (
            <span className="font-mono text-[11px] text-text-dim">
              fact {entry.fact.fact_id} · {entry.fact.fact_type}
              {entry.fact.confidence != null &&
                ` · conf ${entry.fact.confidence.toFixed(2)}`}
            </span>
          )}
          {entry.chunk && (
            <span className="font-mono text-[11px] text-text-dim">
              chunk {entry.chunk.chunk_id} · pages {entry.chunk.page_start}–
              {entry.chunk.page_end}
            </span>
          )}
        </div>

        {entry.fact && (
          <p className="mt-2 text-sm leading-relaxed text-text">
            {entry.fact.statement}
          </p>
        )}

        <p className="mt-2 text-xs leading-relaxed text-text-dim">
          {ISSUE_BLURB[entry.issue_type] ?? entry.note}
        </p>
      </button>

      {expanded && (
        <div className="mt-4">
          {entry.note && (
            <p className="rounded border border-border bg-bg px-3 py-2 font-mono text-[11px] leading-relaxed text-text-dim">
              {entry.note}
            </p>
          )}

          {entry.chunk && (
            <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-border bg-bg px-3 py-2 font-mono text-[11px] leading-relaxed text-text-dim">
              {entry.chunk.text_excerpt}
            </pre>
          )}

          {editing && entry.fact && (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {(
                [
                  ["unit", entry.fact.unit],
                  ["normalized_value", entry.fact.normalized_value],
                  ["time_scope", entry.fact.time_scope],
                  ["subject", entry.fact.subject],
                ] as const
              ).map(([field, current]) => (
                <label key={field} className="block">
                  <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-text-dim">
                    {field}
                  </span>
                  <input
                    defaultValue={current ?? ""}
                    onChange={(e) =>
                      setCorrection((c) => ({ ...c, [field]: e.target.value }))
                    }
                    className="mt-1 w-full rounded border border-border bg-bg px-2 py-1 font-mono text-xs text-text focus:border-accent focus:outline-none"
                  />
                </label>
              ))}
              <p className="col-span-full font-mono text-[10px] text-text-dim">
                Quote, page and bbox are not editable — they are derived from the
                document. A fact whose quote is wrong should be rejected, not
                patched into claiming evidence the PDF does not support.
              </p>
            </div>
          )}

          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why this decision? (required)"
            className="mt-3 w-full rounded border border-border bg-bg px-3 py-1.5 text-sm text-text placeholder:text-text-dim focus:border-accent focus:outline-none"
          />

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy || !note.trim()}
              onClick={() => onResolve(entry, "accepted", note)}
              className="rounded border border-corroborate/50 bg-corroborate/10 px-3 py-1 text-xs text-corroborate transition-opacity duration-200 hover:opacity-80 disabled:opacity-40"
            >
              Accept
            </button>
            <button
              type="button"
              disabled={busy || !note.trim()}
              onClick={() => onResolve(entry, "rejected", note)}
              className="rounded border border-contradict/50 bg-contradict/10 px-3 py-1 text-xs text-contradict transition-opacity duration-200 hover:opacity-80 disabled:opacity-40"
            >
              Reject{entry.fact ? " (deletes fact)" : ""}
            </button>
            {canEdit &&
              (editing ? (
                <button
                  type="button"
                  disabled={
                    busy || !note.trim() || Object.keys(correction).length === 0
                  }
                  onClick={() => onResolve(entry, "edited", note, correction)}
                  className="rounded border border-accent/50 bg-accent/10 px-3 py-1 text-xs text-accent transition-opacity duration-200 hover:opacity-80 disabled:opacity-40"
                >
                  Save edit
                </button>
              ) : (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setEditing(true)}
                  className="rounded border border-border px-3 py-1 text-xs text-text-dim transition-colors duration-200 hover:text-text"
                >
                  Edit
                </button>
              ))}
            {busy && (
              <span className="font-mono text-[11px] text-text-dim">saving…</span>
            )}
            {!note.trim() && (
              <span className="font-mono text-[11px] text-text-dim">
                a note is required
              </span>
            )}
          </div>
        </div>
      )}
    </article>
  );
}
