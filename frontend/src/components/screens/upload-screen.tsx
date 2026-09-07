"use client";

import { ScreenPlaceholder } from "@/components/screens/placeholder";

export function UploadScreen({ onIngested }: { onIngested?: () => void }) {
  void onIngested; // wired up when the screen is built
  return (
    <ScreenPlaceholder
      title="Upload"
      lead="Drop a PDF to run it through the pipeline: parse, chunk, extract facts, ground each quote to a page and box, embed, then relate it to everything already in the layer."
      endpoints={[
        "POST /documents",
        "GET  /documents/{id}/progress/stream  (live phase + N-of-M)",
        "POST /documents/{id}/extract",
        "POST /documents/{id}/link",
      ]}
    >
      <p className="mt-5 text-sm leading-relaxed text-text-dim">
        A 100-page report takes minutes, so this screen will follow the SSE
        progress stream rather than showing a spinner.
      </p>
    </ScreenPlaceholder>
  );
}
