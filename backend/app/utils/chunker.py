"""
Text chunking for the Brainstorm RAG pipeline.

Splits documents into overlapping chunks at natural boundaries
(paragraphs → sentences → character limit) to preserve context.
"""

from __future__ import annotations

import re

_CHUNK_SIZE = 1500   # chars (~375 tokens with standard tokenizer)
_OVERLAP    = 200    # chars overlap between adjacent chunks (~50 tokens)
_MIN_CHUNK  = 80     # discard tiny leftover fragments


def chunk_text(
    text: str,
    chunk_size: int = _CHUNK_SIZE,
    overlap: int = _OVERLAP,
) -> list[dict]:
    """
    Split *text* into overlapping chunks.

    Boundary preference (highest → lowest priority):
      1. Double newline (paragraph break)
      2. Sentence-ending punctuation followed by whitespace
      3. Hard character limit (chunk_size)

    Returns a list of dicts: {"index": int, "text": str}
    """
    text = text.strip()
    if not text:
        return []

    chunks: list[dict] = []
    start = 0
    idx   = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        if end < len(text):
            # Search window: from midpoint of chunk to end
            search_start = start + chunk_size // 2

            # 1. Paragraph boundary (blank line)
            para = text.rfind("\n\n", search_start, end)
            if para != -1:
                end = para + 2
            else:
                # 2. Sentence boundary (.  !  ?)
                best = -1
                for m in re.finditer(r"(?<=[.!?])\s", text[search_start:end]):
                    best = search_start + m.start() + 1
                if best != -1:
                    end = best

        chunk = text[start:end].strip()
        if len(chunk) >= _MIN_CHUNK:
            chunks.append({"index": idx, "text": chunk})
            idx += 1

        next_start = end - overlap
        if next_start <= start:      # guard against infinite loop on tiny texts
            next_start = start + max(1, chunk_size)
        start = next_start

    return chunks
