"use client";

import { ScreenPlaceholder } from "@/components/screens/placeholder";
import type { KnowledgeLayerTotals } from "@/lib/api";

export function FactExplorerScreen({
  documentId,
  totals,
}: {
  documentId: number | null;
  totals?: KnowledgeLayerTotals;
}) {
  return (
    <ScreenPlaceholder
      title="Fact Explorer"
      lead="Every fact in the layer, filterable by document, type, subject and confidence — each with its verbatim quote, the page it came from, and the facts it corroborates, contradicts or reconciles with."
      endpoints={[
        "GET /facts                      (cross-document by default)",
        "GET /facts/{id}/evidence        (page image + pixel bbox)",
        "GET /facts/{id}/relationships   (verdict + rationale)",
        "GET /schema                     (fact_type registry)",
      ]}
    >
      <div className="mt-5 space-y-2 text-sm text-text-dim">
        <p>
          Scope:{" "}
          <span className="text-text">
            {documentId ? `document ${documentId}` : "whole knowledge layer"}
          </span>
          {documentId === null && " — select a document in the rail to narrow it"}
        </p>
        {totals && (
          <p>
            <span className="text-text">{totals.facts}</span> facts across{" "}
            <span className="text-text">{totals.fact_types}</span> model-invented
            types, with{" "}
            <span className="text-text">
              {totals.cross_document_relationships}
            </span>{" "}
            cross-document links.
          </p>
        )}
      </div>
    </ScreenPlaceholder>
  );
}
