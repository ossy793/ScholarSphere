"""
PDF Converter — Pipeline B (Document Viewer)

Converts uploaded files to PDF so the frontend can use a single PDF viewer
for all document types.  Completely independent from the AI text-extraction
pipeline (Pipeline A).

Supported conversions:
  pdf   → returned as-is (no work needed)
  docx  → LibreOffice headless
  pptx  → LibreOffice headless
  image → Pillow (PNG/JPG/JPEG/WEBP/GIF)
  txt   → returns None  (rendered as plain text in the viewer)
"""

import io
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "gif"}

# Candidate soffice (LibreOffice) executable paths — checked in order.
_SOFFICE_CANDIDATES = [
    # Linux (Render / Ubuntu)
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/usr/local/bin/soffice",
    # Windows (local dev)
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def _find_soffice() -> str | None:
    """Return the first soffice path that exists, or None."""
    # Try PATH first
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in _SOFFICE_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def _image_to_pdf(file_bytes: bytes) -> bytes | None:
    """Convert an image (any Pillow-supported format) to a single-page PDF."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(file_bytes))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PDF")
        return buf.getvalue()
    except Exception as exc:
        logger.warning("Image→PDF conversion failed: %s", exc)
        return None


def _libreoffice_convert(file_bytes: bytes, filename: str) -> bytes | None:
    """
    Convert a DOCX or PPTX to PDF using LibreOffice headless.

    Strategy:
      1. Write file to a temp directory.
      2. Run: soffice --headless --convert-to pdf --outdir <tmpdir> <file>
      3. Read the output <basename>.pdf.
      4. Clean up.
    Returns PDF bytes on success, None on failure.
    """
    soffice = _find_soffice()
    if not soffice:
        logger.warning("LibreOffice (soffice) not found — cannot convert %s to PDF.", filename)
        return None

    # Use a unique temp dir to avoid collisions under concurrent requests
    tmpdir = tempfile.mkdtemp(prefix="pritis_lo_")
    try:
        src_path = os.path.join(tmpdir, filename)
        with open(src_path, "wb") as f:
            f.write(file_bytes)

        result = subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--convert-to", "pdf",
                "--outdir", tmpdir,
                src_path,
            ],
            timeout=120,          # generous timeout for large files
            capture_output=True,
        )

        if result.returncode != 0:
            logger.warning(
                "LibreOffice conversion failed (rc=%d): %s",
                result.returncode,
                result.stderr.decode(errors="replace")[:500],
            )
            return None

        # LibreOffice writes <basename>.pdf next to the source
        base = os.path.splitext(filename)[0]
        pdf_path = os.path.join(tmpdir, base + ".pdf")
        if not os.path.isfile(pdf_path):
            logger.warning("LibreOffice ran but output PDF not found at %s", pdf_path)
            return None

        with open(pdf_path, "rb") as f:
            return f.read()

    except subprocess.TimeoutExpired:
        logger.warning("LibreOffice conversion timed out for %s", filename)
        return None
    except Exception as exc:
        logger.warning("LibreOffice conversion error for %s: %s", filename, exc)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def convert_to_pdf(file_bytes: bytes, file_type: str, filename: str) -> bytes | None:
    """
    Convert uploaded file bytes to PDF.

    Returns:
      bytes  — PDF content ready to serve / store.
      None   — Conversion not applicable (txt) or failed (soft failure).

    This function never raises — callers treat None as "PDF unavailable".
    """
    ft = file_type.lower()

    if ft == "pdf":
        return file_bytes                          # already PDF — no conversion needed

    if ft in IMAGE_TYPES:
        return _image_to_pdf(file_bytes)           # fast Pillow conversion

    if ft in ("docx", "pptx"):
        return _libreoffice_convert(file_bytes, filename)   # LibreOffice

    # txt / paste → no PDF viewer; caller will render as plain text
    return None
