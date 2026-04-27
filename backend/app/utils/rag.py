"""
RAG (Retrieval-Augmented Generation) pipeline for the Brainstorm feature.

Responsibilities:
  - Generate embeddings using OpenAI text-embedding-3-small (1536-dim)
  - Store chunk text + embeddings in the brainstorm_chunks PostgreSQL table
  - Retrieve top-k relevant chunks via hybrid search:
      primary  → pgvector cosine similarity
      fallback → PostgreSQL full-text search (ts_rank)

Note: sessions embedded before the OpenAI switch used BAAI/bge-small-en-v1.5
(384-dim, different vector space).  Those chunks automatically fall back to
keyword search since cosine similarity across different spaces is meaningless.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text as sql_text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── pgvector availability flag ────────────────────────────────────────────────
# Set to False on first failure; avoids log spam on every subsequent call.
_pgvector_ok: bool | None = None   # None = not yet checked


def _check_pgvector(db: "Session") -> bool:
    global _pgvector_ok
    if _pgvector_ok is not None:
        return _pgvector_ok
    try:
        db.execute(sql_text("SELECT 1::vector(1)"))
        _pgvector_ok = True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        _pgvector_ok = False
        logger.warning(
            "pgvector extension not available — RAG will fall back to keyword search. "
            "To enable vector search, run: CREATE EXTENSION IF NOT EXISTS vector; "
            "(or enable it in Supabase Dashboard → Database → Extensions → vector)"
        )
    return _pgvector_ok


# ── OpenAI embedding client — lazy-loaded ─────────────────────────────────────

_openai_client = None

_EMBED_MODEL      = "text-embedding-3-small"
_EMBED_DIMENSIONS = 1536


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        from ..config import settings
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        logger.info("OpenAI embedding client initialised (%s, %d-dim)",
                    _EMBED_MODEL, _EMBED_DIMENSIONS)
    return _openai_client


def _embed(texts: list[str]) -> list[list[float]]:
    """Return a 1536-dim float vector for each text using OpenAI embeddings."""
    client = _get_openai()
    response = client.embeddings.create(
        input      = texts,
        model      = _EMBED_MODEL,
        dimensions = _EMBED_DIMENSIONS,
    )
    # Response items are ordered the same as input
    return [item.embedding for item in response.data]


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
    When pgvector is not installed, stores text-only rows for keyword search.
    """
    # Clear stale chunks for this document
    db.execute(
        sql_text("DELETE FROM brainstorm_chunks WHERE document_id = :did"),
        {"did": str(document_id)},
    )

    if not chunks:
        db.commit()
        return

    use_vectors = _check_pgvector(db)

    # Only generate embeddings if pgvector is available
    embeddings: list = [None] * len(chunks)
    if use_vectors:
        texts = [c["text"] for c in chunks]
        try:
            embeddings = _embed(texts)
        except Exception as exc:
            logger.warning("Embedding generation failed — storing chunks without vectors: %s", exc)
            embeddings = [None] * len(chunks)

    for chunk, emb in zip(chunks, embeddings):
        if emb is not None and use_vectors:
            db.execute(
                sql_text("""
                    INSERT INTO brainstorm_chunks
                        (id, document_id, session_id, chunk_index, chunk_text, embedding, created_at)
                    VALUES
                        (gen_random_uuid(), :did, :sid, :idx, :txt, CAST(:emb AS vector), NOW())
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
                        (id, document_id, session_id, chunk_index, chunk_text, created_at)
                    VALUES
                        (gen_random_uuid(), :did, :sid, :idx, :txt, NOW())
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
                ORDER  BY embedding::vector <=> CAST(:vec AS vector)
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
        try:
            db.rollback()
        except Exception:
            pass

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
        try:
            db.rollback()
        except Exception:
            pass
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
        try:
            db.rollback()
        except Exception:
            pass
        return False
