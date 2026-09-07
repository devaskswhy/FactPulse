/** Base URL of the FactPulse FastAPI backend. */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

// ---------------------------------------------------------------- types
// Mirrors the pydantic schemas the backend serves. Hand-written rather than
// generated from the OpenAPI spec: only a handful of shapes are consumed so
// far, and a generator would be more machinery than the problem needs yet.

export type DocumentSummary = {
  id: number;
  filename: string;
  title: string | null;
  sha256: string;
  uploaded_at: string;
  page_count: number | null;
  status: string;
  chunk_count: number;
  fact_count: number;
  embedded_count: number;
  /** Counts either end of a pair, so cross-document links appear on both. */
  relationship_count: number;
  open_review_count: number;
};

export type KnowledgeLayerTotals = {
  documents: number;
  facts: number;
  fact_types: number;
  embeddings: number;
  relationships: number;
  cross_document_relationships: number;
  open_review_items: number;
};

export type DocumentList = {
  total: number;
  totals: KnowledgeLayerTotals;
  documents: DocumentSummary[];
};

export type Health = {
  status: string;
  version: string;
  database: string;
  tables: string[];
  gemini_configured: boolean;
};

// ---------------------------------------------------------------- client

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      cache: "no-store",
      ...init,
    });
  } catch {
    // A dead backend is the common case in development, and "Failed to fetch"
    // tells the reader nothing about what to do. The underlying cause is always
    // some form of network failure and adds nothing here.
    throw new ApiError(
      `Cannot reach the FactPulse API at ${API_BASE_URL}. Is the backend running?`,
    );
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // Not JSON; the status line is all we have.
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export function getDocuments(): Promise<DocumentList> {
  return request<DocumentList>("/documents");
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

// ---------------------------------------------------------------- display

/**
 * What to call a document.
 *
 * The backend deliberately returns a null title rather than guessing one from
 * body text -- see _infer_title in the backend. So the filename is the normal
 * label, not a fallback for an error case.
 */
export function documentLabel(doc: DocumentSummary): string {
  return doc.title?.trim() || doc.filename;
}

/** Pipeline stage a document has reached, for the rail's status dot. */
export function statusTone(
  status: string,
): "idle" | "working" | "done" | "warn" {
  switch (status) {
    case "linked":
      return "done";
    case "extracted":
    case "chunked":
    case "uploaded":
      return "idle";
    case "extracting":
    case "parsing":
      return "working";
    case "failed":
    case "extraction_failed":
    case "extraction_incomplete":
    case "extracted_with_errors":
      return "warn";
    default:
      return "idle";
  }
}
