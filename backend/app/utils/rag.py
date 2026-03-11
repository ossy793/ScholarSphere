"""
RAG (Retrieval-Augmented Generation) pipeline for the Brainstorm feature.

Responsibilities:
  - Generate embeddings using fastembed (ONNX — no PyTorch required)
  - Store chunk text + embeddings in the brainstorm_chunks PostgreSQL table
  - Retrieve top-k relevant chunks via hybrid search:
      primary  → pgvector cosine similarity
      fallback → PostgreSQL full-text search (ts_rank)

The embedding model (BAAI/bge-small-en-v1.5) is downloaded once on first use
and cached at ~/.cache/fastembed.  All subsequent calls are local / offline.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text as sql_text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Embedding model — lazy-loaded once per worker process ─────────────────────

_embed_model = None


def _get_model():
    global _embed_model
    if _embed_model is None:
        from fastembed import TextEmbedding
        _embed_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        logger.info("fastembed model loaded: BAAI/bge-small-en-v1.5")
    return _embed_model


def _embed(texts: list[str]) -> list[list[float]]:
    """Return a float vector for each text in *texts*."""
    model = _get_model()
    return [e.tolist() for e in model.embed(texts)]


def _vec_literal(vec: list[float]) -> str:
    """Format a Python float list as a PostgreSQL vector literal '[x, y, …]'."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


# ── Storage ───────────────────────────────────────────────────────────────────

def store_chunks(
    db: "Session",
    document_id: uuid.UUID,
    session_id:  uuid.UUID,
    chunks: list[dict],
) -> None:
    """
    Embed *chunks* and insert them into brainstorm_chunks.
    Deletes any existing chunks for this document first (idempotent).
    Each chunk dict must have {"index": int, "text": str}.
    """
    # Clear stale chunks for this document
    db.execute(
        sql_text("DELETE FROM brainstorm_chunks WHERE document_id = :did"),
        {"did": str(document_id)},
    )

    if not chunks:
        db.commit()
        return

    texts = [c["text"] for c in chunks]
    try:
        embeddings = _embed(texts)
    except Exception as exc:
        logger.warning("Embedding generation failed — storing chunks without vectors: %s", exc)
        embeddings = [None] * len(chunks)

    for chunk, emb in zip(chunks, embeddings):
        if emb is not None:
            db.execute(
                sql_text("""
                    INSERT INTO brainstorm_chunks
                        (id, document_id, session_id, chunk_index, chunk_text, embedding)
                    VALUES
                        (gen_random_uuid(), :did, :sid, :idx, :txt, CAST(:emb AS vector))
                """),
                {
                    "did": str(document_id),
                    "sid": str(session_id),
                    "idx": chunk["index"],
                    "txt": chunk["text"],
                    "emb": _vec_literal(emb),
                },
            )
        else:
            db.execute(
                sql_text("""
                    INSERT INTO brainstorm_chunks
                        (id, document_id, session_id, chunk_index, chunk_text)
                    VALUES
                        (gen_random_uuid(), :did, :sid, :idx, :txt)
                """),
                {
                    "did": str(document_id),
                    "sid": str(session_id),
                    "idx": chunk["index"],
                    "txt": chunk["text"],
                },
            )

    db.commit()
    logger.info("Stored %d chunks for session %s.", len(chunks), session_id)


# ── Retrieval ─────────────────────────────────────────────────────────────────

def retrieve_chunks(
    db:         "Session",
    session_id: uuid.UUID,
    query:      str,
    top_k:      int = 7,
) -> list[str]:
    """
    Return the top-k chunk texts most relevant to *query*.

    Strategy:
      1. Vector cosine similarity (pgvector <=> operator)
      2. PostgreSQL full-text search as fallback if vector search yields nothing
         or embeddings are unavailable.
    """

    # ── 1. Vector search ──────────────────────────────────────────────────────
    try:
        query_vec = _embed([query])[0]
        rows = db.execute(
            sql_text("""
                SELECT chunk_text
                FROM   brainstorm_chunks
                WHERE  session_id = :sid
                  AND  embedding  IS NOT NULL
                ORDER  BY embedding <=> CAST(:vec AS vector)
                LIMIT  :k
            """),
            {
                "sid": str(session_id),
                "vec": _vec_literal(query_vec),
                "k":   top_k,
            },
        ).fetchall()

        if rows:
            return [r[0] for r in rows]

    except Exception as exc:
        logger.warning("Vector search failed, falling back to keyword search: %s", exc)

    # ── 2. Keyword fallback (PostgreSQL FTS) ──────────────────────────────────
    try:
        rows = db.execute(
            sql_text("""
                SELECT chunk_text
                FROM   brainstorm_chunks
                WHERE  session_id = :sid
                  AND  to_tsvector('english', chunk_text)
                       @@ plainto_tsquery('english', :q)
                ORDER  BY ts_rank(
                              to_tsvector('english', chunk_text),
                              plainto_tsquery('english', :q)
                          ) DESC
                LIMIT  :k
            """),
            {"sid": str(session_id), "q": query, "k": top_k},
        ).fetchall()

        return [r[0] for r in rows]

    except Exception as exc:
        logger.warning("Keyword fallback search failed: %s", exc)
        return []


def chunks_exist(db: "Session", session_id: uuid.UUID) -> bool:
    """Return True if any chunks are stored for this session."""
    try:
        row = db.execute(
            sql_text(
                "SELECT 1 FROM brainstorm_chunks WHERE session_id = :sid LIMIT 1"
            ),
            {"sid": str(session_id)},
        ).fetchone()
        return row is not None
    except Exception:
        return False
