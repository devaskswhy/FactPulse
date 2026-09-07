"use client";

import { ScreenPlaceholder } from "@/components/screens/placeholder";

export function ReviewQueueScreen({ openCount }: { openCount: number }) {
  return (
    <ScreenPlaceholder
      title="Review Queue"
      lead="Nothing doubtful is discarded. A fact whose quote will not verify, or whose value has no unit to interpret it by, is still stored — and queued here with the context to judge it."
      endpoints={[
        "GET  /review-queue",
        "POST /review-queue/{id}/resolve   (accepted | rejected | edited)",
        "GET  /facts/{id}/evidence",
      ]}
    >
      <p className="mt-5 text-sm text-text-dim">
        {openCount > 0 ? (
          <>
            <span className="text-accent">{openCount}</span> item
            {openCount === 1 ? "" : "s"} currently open.
          </>
        ) : (
          "Queue is empty."
        )}
      </p>
    </ScreenPlaceholder>
  );
}
