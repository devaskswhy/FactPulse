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
  /** corroborates / contradicts / reconciled / supersedes -> count. */
  relationships_by_type: Record<string, number>;
  /** full / partial / insufficient -> count. */
  evidence_by_strength: Record<string, number>;
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

// ------------------------------------------------------------ facts & schema

export type BBox = { x0: number; y0: number; x1: number; y1: number };

export type Grounding = {
  quote: string | null;
  page_number: number | null;
  bbox: BBox | null;
};

export type FactAttribute = { key: string; value: string | null };

export type Fact = {
  id: number;
  document_id: number;
  chunk_id: number | null;
  fact_type: string;
  subject: string | null;
  statement: string;
  normalized_value: string | null;
  unit: string | null;
  time_scope: string | null;
  confidence: number | null;
  grounding: Grounding | null;
  attributes: FactAttribute[];
  created_at: string;
  /**
   * How much of the claim the quote actually carries. Distinct from
   * `grounding`, which only proves the quote is a real substring of the
   * source -- a table cell reading "Nil" is grounded and insufficient.
   */
  evidence_strength: "full" | "partial" | "insufficient" | null;
  evidence_gaps: string[];
  /**
   * Matching key for `subject` -- "Acme Corp" and "Acme Corporation" share
   * one. For grouping/filtering, not for display; show `subject` to a person.
   */
  canonical_subject: string | null;
  /** Id of a later fact that replaced this one, or null if still current. */
  superseded_by: number | null;
};

/** Badge styling for an evidence tier. */
export const EVIDENCE_TIER: Record<
  string,
  { label: string; className: string; title: string }
> = {
  full: {
    label: "full evidence",
    className: "border-corroborate/40 bg-corroborate/10 text-corroborate",
    title: "The quote states the claim on its own.",
  },
  partial: {
    label: "partial evidence",
    className: "border-accent/40 bg-accent/10 text-accent",
    title: "The quote carries the value but relies on surrounding context.",
  },
  insufficient: {
    label: "weak evidence",
    className: "border-contradict/40 bg-contradict/10 text-contradict",
    title: "On its own the quote does not support the statement.",
  },
};

export type FactList = {
  total: number;
  scope: "knowledge-layer" | "document";
  /** document_id -> title, so a cross-document list can name each source. */
  documents: Record<string, string>;
  facts: Fact[];
};

export type FactType = {
  name: string;
  first_seen_at: string;
  example_fact_id: number | null;
  fact_count: number;
};

export type SchemaResponse = {
  total_types: number;
  total_facts: number;
  fact_types: FactType[];
};

export type SubjectVariant = {
  subject: string;
  fact_count: number;
};

export type SubjectGroup = {
  /** Matching key from canonicalize_subject -- not for display. */
  canonical: string;
  /** Raw spellings folded into this key, most common first. */
  variants: SubjectVariant[];
  fact_count: number;
  example_fact_id: number;
};

export type SubjectRegistryResponse = {
  total_canonical: number;
  total_facts: number;
  /** Canonical subjects folded from more than one raw spelling. */
  merged_count: number;
  subjects: SubjectGroup[];
};

export type EvidenceBundle = {
  fact: Fact;
  page_image_url: string;
  page_image_width: number;
  page_image_height: number;
  render_scale: number;
  /** Already in PIXELS of the page image -- no conversion needed. */
  bbox: BBox | null;
  bbox_pdf_points: BBox | null;
  quote: string | null;
  document_id: number;
  document_title: string | null;
  document_filename: string;
  page_number: number | null;
  grounded: boolean;
};

export type RelatedFact = {
  relationship_id: number;
  relationship_type: string;
  rationale: string | null;
  relationship_confidence: number | null;
  /**
   * Which end of the stored pair the fact being viewed sits on. Only
   * meaningful for `supersedes`, where "outgoing" means this fact is the
   * current one and "incoming" means it is the one that was replaced -- the
   * same row read from either end is the opposite claim.
   */
  direction: "outgoing" | "incoming";
  fact_id: number;
  fact_type: string;
  subject: string | null;
  statement: string;
  normalized_value: string | null;
  unit: string | null;
  time_scope: string | null;
  confidence: number | null;
  grounding: Grounding | null;
  document_id: number;
  document_title: string | null;
  document_filename: string;
};

export type RelationshipList = {
  fact_id: number;
  total: number;
  relationships: RelatedFact[];
};

export type FactFilters = {
  document_id?: number | null;
  fact_type?: string | null;
  subject?: string | null;
  min_confidence?: number | null;
  limit?: number;
  offset?: number;
};

export function getFacts(filters: FactFilters = {}): Promise<FactList> {
  const params = new URLSearchParams();
  if (filters.document_id != null)
    params.set("document_id", String(filters.document_id));
  if (filters.fact_type) params.set("fact_type", filters.fact_type);
  if (filters.subject) params.set("subject", filters.subject);
  if (filters.min_confidence != null)
    params.set("min_confidence", String(filters.min_confidence));
  params.set("limit", String(filters.limit ?? 200));
  params.set("offset", String(filters.offset ?? 0));
  return request<FactList>(`/facts?${params}`);
}

export function getSchema(): Promise<SchemaResponse> {
  return request<SchemaResponse>("/schema");
}

export function getSubjects(): Promise<SubjectRegistryResponse> {
  return request<SubjectRegistryResponse>("/subjects");
}

export function getEvidence(factId: number): Promise<EvidenceBundle> {
  return request<EvidenceBundle>(`/facts/${factId}/evidence`);
}

export function getRelationships(factId: number): Promise<RelationshipList> {
  return request<RelationshipList>(`/facts/${factId}/relationships`);
}

/** Absolute URL for a page image; the API returns a relative path. */
export function pageImageUrl(path: string): string {
  return path.startsWith("http") ? path : `${API_BASE_URL}${path}`;
}

// ------------------------------------------------------------- review queue

export type ReviewFactContext = {
  fact_id: number;
  fact_type: string;
  subject: string | null;
  statement: string;
  normalized_value: string | null;
  unit: string | null;
  time_scope: string | null;
  confidence: number | null;
  quote: string | null;
  page_number: number | null;
  document_id: number;
  document_title: string | null;
};

export type ReviewChunkContext = {
  chunk_id: number;
  page_start: number | null;
  page_end: number | null;
  text_excerpt: string;
  document_id: number;
  document_title: string | null;
};

export type ReviewEntry = {
  id: number;
  issue_type: string;
  note: string | null;
  resolved: boolean;
  created_at: string;
  resolution_action: string | null;
  resolution_note: string | null;
  resolved_at: string | null;
  fact: ReviewFactContext | null;
  chunk: ReviewChunkContext | null;
  relationship: unknown | null;
  evidence_url: string | null;
};

export type ReviewQueue = {
  total: number;
  by_issue_type: Record<string, number>;
  entries: ReviewEntry[];
};

export type ResolveAction = "accepted" | "rejected" | "edited";

export type FactCorrection = {
  fact_type?: string;
  subject?: string;
  statement?: string;
  normalized_value?: string;
  unit?: string;
  time_scope?: string;
};

export function getReviewQueue(issueType?: string | null): Promise<ReviewQueue> {
  const params = new URLSearchParams({ resolved: "false", limit: "200" });
  if (issueType) params.set("issue_type", issueType);
  return request<ReviewQueue>(`/review-queue?${params}`);
}

export function resolveReviewItem(
  id: number,
  body: {
    action: ResolveAction;
    resolution_note: string;
    correction?: FactCorrection;
  },
): Promise<unknown> {
  return request(`/review-queue/${id}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ------------------------------------------------------------------ upload

export type UploadResponse = {
  document: DocumentSummary & { chunk_count?: number };
  chunk_count: number;
  deduplicated: boolean;
  self_check: Record<string, number>;
  linking: { relationships_created: number; pool_size: number } | null;
  extraction: {
    facts_inserted: number;
    facts_grounded: number;
    chunks_processed: number;
    chunks_failed: number;
    quota_exhausted: boolean;
    fact_types: string[];
  } | null;
};

/**
 * Upload a PDF.
 *
 * Deliberately not using `request()`: this posts FormData, needs no
 * Content-Type (the browser sets the multipart boundary), and can run for
 * minutes on a large document, so the caller wants the raw promise to race
 * against the SSE stream.
 */
export async function uploadDocument(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/documents`, {
      method: "POST",
      body: form,
    });
  } catch {
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
      /* status line is all we have */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as UploadResponse;
}

// ---------------------------------------------------------------- progress

export type ProgressSnapshot = {
  document_id: number | null;
  filename: string;
  phase: string;
  message: string;
  current: number;
  total: number;
  percent: number | null;
  pages: number;
  chunks: number;
  facts: number;
  relationships: number;
  elapsed: number;
  error: string | null;
};

export function progressStreamUrl(documentId: number): string {
  return `${API_BASE_URL}/documents/${documentId}/progress/stream`;
}

export function getProgress(documentId: number): Promise<ProgressSnapshot> {
  return request<ProgressSnapshot>(`/documents/${documentId}/progress`);
}

/** Human-readable label for a pipeline phase. */
export const PHASE_LABEL: Record<string, string> = {
  queued: "queued",
  parsing: "reading pages",
  chunking: "splitting into chunks",
  extracting: "extracting facts",
  embedding: "embedding facts",
  linking: "classifying relationships",
  checking: "running checks",
  done: "complete",
  failed: "failed",
};

/** Order the pipeline runs in, for the stage tracker. */
export const PHASE_ORDER = [
  "parsing",
  "chunking",
  "extracting",
  "embedding",
  "linking",
  "checking",
] as const;
