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


def _detect_section_heading(text: str) -> str | None:
    """Return the first line if it looks like a chapter/section heading, else None."""
    first_line = text.strip().split("\n")[0].strip()
    if not first_line or len(first_line) > 100:
        return None
    if re.match(r"^(chapter|section|unit|part|module|topic)\s", first_line, re.IGNORECASE):
        return first_line[:80]
    if re.match(r"^\d+[\.\)]\s+\S.{2,60}$", first_line):
        return first_line[:80]
    if first_line == first_line.upper() and len(first_line) >= 4:
        return first_line[:80]
    return None


def chunk_pages_aware(
    pages: list[dict],
    chunk_size: int = _CHUNK_SIZE,
    overlap: int = _OVERLAP,
) -> list[dict]:
    """
    Page-aware chunking. Each chunk is prefixed with
    '[Document Viewer Page N | section]' so the AI can cite viewer-based page numbers.

    pages: list of {"page": int, "text": str} from extract_pages_from_*
    Returns list of {"index": int, "text": str}
    """
    chunks: list[dict] = []
    idx = 0
    current_heading: str | None = None

    for page_info in pages:
        page_num = page_info["page"]
        text = page_info["text"].strip()
        if not text:
            continue

        heading = _detect_section_heading(text)
        if heading:
            current_heading = heading

        section = current_heading or f"Page {page_num}"
        prefix = f"[Document Viewer Page {page_num} | {section}]\n"

        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))

            if end < len(text):
                search_start = start + chunk_size // 2
                para = text.rfind("\n\n", search_start, end)
                if para != -1:
                    end = para + 2
                else:
                    best = -1
                    for m in re.finditer(r"(?<=[.!?])\s", text[search_start:end]):
                        best = search_start + m.start() + 1
                    if best != -1:
                        end = best

            chunk = text[start:end].strip()
            if len(chunk) >= _MIN_CHUNK:
                chunks.append({"index": idx, "text": f"{prefix}{chunk}"})
                idx += 1

            next_start = end - overlap
            if next_start <= start:
                next_start = start + max(1, chunk_size)
            start = next_start

    return chunks


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
