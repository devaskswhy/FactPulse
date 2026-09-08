"""Pipeline step 6: embed fact statements.

Vectors are stored in `embeddings` as a raw float32 BLOB (numpy `.tobytes()`)
with `dim` recorded alongside, so the width can change with the model without a
migration.

Normalisation note, which is load-bearing. Gemini's embedding models return
unit-length vectors at their native 3072 dimensions, but a *truncated* output
(768 here) is not renormalised for you -- `gemini-embedding-001` at 768 comes
back with a norm around 0.58. Cosine similarity computed on un-normalised
vectors is still mathematically fine if you divide by the norms, but every
vector here is normalised on the way in so similarity is a plain dot product
and the retrieval step stays a single matrix multiply.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from google import genai
from google.genai import types

from app.core.config import settings
from app.services.extract import (
    ExtractionError,
    _is_daily_quota,
    _is_retryable,
    _retry_delay,
    build_client,
)

logger = logging.getLogger(__name__)

# Gemini caps how many inputs one embed_content call accepts.
EMBED_BATCH_SIZE = 100


class EmbeddingError(RuntimeError):
    """The embedding call failed or returned an unusable payload."""


def normalize(vector: np.ndarray) -> np.ndarray:
    """L2-normalise, leaving an all-zero vector alone rather than dividing by 0."""
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


def pack_vector(vector: np.ndarray) -> bytes:
    """float32 BLOB, as stored in embeddings.vector."""
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack_vector(blob: bytes, dim: int | None = None) -> np.ndarray:
    """Read a stored BLOB back. `dim` is a sanity check, not a reshape."""
    vector = np.frombuffer(blob, dtype=np.float32)
    if dim is not None and vector.size != dim:
        raise EmbeddingError(
            f"stored vector has {vector.size} values but dim says {dim}"
        )
    return vector


def embed_texts(
    texts: list[str], client: genai.Client | None = None
) -> list[np.ndarray]:
    """Embed a list of strings, returning normalised float32 vectors.

    Batched, because one call per fact is needlessly slow on a document that
    produced twenty of them.
    """
    if not texts:
        return []

    try:
        client = client or build_client()
    except ExtractionError as exc:  # same missing-key condition
        raise EmbeddingError(str(exc)) from exc

    config = types.EmbedContentConfig(
        task_type="SEMANTIC_SIMILARITY",
        output_dimensionality=settings.embedding_dim,
    )

    vectors: list[np.ndarray] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]

        # Retry transient limits, exactly as the generate calls do. Embedding
        # had no retry at first, and a *per-minute* cap (100 embeds/min on the
        # free tier) permanently left a whole document's facts unembedded and
        # therefore unlinkable -- a minute's wait turned into a missing document
        # in the knowledge layer. A per-day quota is still not retried; nothing
        # we would wait through clears it.
        attempts = max(1, settings.extraction_max_attempts)
        response = None
        for attempt in range(attempts):
            try:
                response = client.models.embed_content(
                    model=settings.gemini_embedding_model,
                    contents=batch,
                    config=config,
                )
                break
            except Exception as exc:
                if _is_daily_quota(exc):
                    # Embedding quota is metered separately from generate
                    # quota, so the generate model pool cannot help here.
                    # There is exactly one embedding model configured.
                    raise EmbeddingError(
                        f"daily embedding quota exhausted for "
                        f"{settings.gemini_embedding_model}: {exc}"
                    ) from exc
                if attempt == attempts - 1 or not _is_retryable(exc):
                    raise EmbeddingError(f"embedding call failed: {exc}") from exc
                delay = _retry_delay(exc, attempt)
                logger.warning(
                    "retryable embedding error (attempt %d/%d), sleeping %.1fs",
                    attempt + 1, attempts, delay,
                )
                time.sleep(delay)

        if response is None:  # pragma: no cover - loop breaks or raises
            raise EmbeddingError("embedding call produced no response")
        returned = response.embeddings or []
        if len(returned) != len(batch):
            raise EmbeddingError(
                f"asked for {len(batch)} embeddings, got {len(returned)}"
            )
        for item in returned:
            vectors.append(normalize(np.array(item.values, dtype=np.float32)))

    return vectors


def embed_text(text: str, client: genai.Client | None = None) -> np.ndarray:
    """Embed a single string."""
    result = embed_texts([text], client=client)
    if not result:
        raise EmbeddingError("no embedding returned")
    return result[0]


def cosine_against_matrix(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of one vector against every row of a matrix.

    Both sides are normalised on the way in, so this is a dot product. Rows are
    renormalised defensively in case a vector was written by an older build
    before normalisation was applied.
    """
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)

    query = normalize(np.asarray(query, dtype=np.float32))
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms) @ query
