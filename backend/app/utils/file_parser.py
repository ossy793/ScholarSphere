import base64
import io
import logging
import os

logger = logging.getLogger(__name__)

# Candidate Tesseract executable paths — checked in order when PATH lookup fails.
_TESSERACT_CANDIDATES = [
    # Linux (Render / Ubuntu)
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    # Windows (local dev)
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Tesseract-OCR\tesseract.exe",
]

# Groq vision models tried in order — first available wins.
_GROQ_VISION_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",   # Llama 4 Scout — best, current
    "meta-llama/llama-4-maverick-17b-128e-instruct", # Llama 4 Maverick — fallback
    "llama-3.2-11b-vision-preview",                  # Llama 3.2 — legacy fallback
]


def _find_tesseract_cmd() -> str | None:
    """Return the first candidate tesseract.exe path that exists, or None."""
    for candidate in _TESSERACT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


_MAX_OCR_PAGES = 5   # cap to keep memory under 512 MB on Render free tier

def _render_pdf_pages(file_bytes: bytes) -> list:
    """
    Render each PDF page as a JPEG image using pymupdf (no Poppler required).
    Returns a list of raw JPEG bytes, one per page (capped at _MAX_OCR_PAGES).
    Uses 2× zoom (144 DPI) — good OCR quality while keeping memory low.
    """
    import fitz  # pymupdf

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    mat = fitz.Matrix(2, 2)  # 2× zoom ≈ 144 DPI — adequate OCR quality, half the RAM of 3×
    images = []
    page_count = min(len(doc), _MAX_OCR_PAGES)
    for page_num in range(page_count):
        pix = doc[page_num].get_pixmap(matrix=mat)
        images.append(pix.tobytes("jpeg", jpg_quality=85))
        del pix  # free pixmap memory immediately
    doc.close()
    return images


def _ocr_with_tesseract(page_images: list) -> str:
    """
    Try pytesseract OCR on pre-rendered page images.
    Returns '' if Tesseract is not installed or pytesseract is missing.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""

    tesseract_cmd = _find_tesseract_cmd()
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    parts = []
    for i, img_bytes in enumerate(page_images):
        try:
            img = Image.open(io.BytesIO(img_bytes))
            text = pytesseract.image_to_string(img)
            img.close()
            if text.strip():
                parts.append(text.strip())
        except Exception as exc:
            logger.warning("Tesseract OCR failed on page %d: %s", i + 1, exc)

    return "\n\n".join(parts)


def _ocr_with_groq(page_images: list) -> str:
    """
    Cloud OCR fallback: send page images to a Groq vision model one at a time.
    Processes pages sequentially and releases each image from memory after use.
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        logger.warning("GROQ_API_KEY not set — Groq vision OCR unavailable")
        return ""

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except Exception as exc:
        logger.warning("Groq client init failed: %s", exc)
        return ""

    def _ocr_page(model: str, img_bytes: bytes, page_num: int) -> str:
        img_b64 = base64.b64encode(img_bytes).decode()
        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract all text from this document page exactly as it appears. "
                            "Output only the extracted text, preserving structure where possible."
                        ),
                    },
                ],
            }],
            max_tokens=2048,
        )
        del img_b64  # free the large base64 string immediately
        return response.choices[0].message.content.strip()

    for model in _GROQ_VISION_MODELS:
        parts = []
        failed = False
        for i, img_bytes in enumerate(page_images):
            try:
                text = _ocr_page(model, img_bytes, i + 1)
                if text:
                    parts.append(text)
            except Exception as exc:
                logger.warning("Groq vision OCR failed on page %d with %s: %s", i + 1, model, exc)
                failed = True
                break
        if parts and not failed:
            logger.info("Groq vision OCR succeeded with model %s", model)
            return "\n\n".join(parts)

    return ""


def _ocr_pdf(file_bytes: bytes) -> str:
    """
    OCR fallback for scanned / image-only PDFs.

    Pipeline:
    1. Render pages to images via pymupdf  (no Poppler system package needed)
    2. Try pytesseract                     (fast, local — requires Tesseract binary)
    3. Fall back to Groq vision API        (cloud — zero system dependencies)

    Returns empty string if all strategies fail.
    """
    try:
        page_images = _render_pdf_pages(file_bytes)
    except Exception as exc:
        logger.warning("PDF page rendering failed: %s", exc)
        return ""

    if not page_images:
        return ""

    # Strategy 1: local Tesseract
    text = _ocr_with_tesseract(page_images)
    if text:
        logger.info("Tesseract OCR succeeded — %d chars extracted.", len(text))
        return text

    # Strategy 2: Groq vision API
    logger.info("Tesseract unavailable — trying Groq vision OCR.")
    text = _ocr_with_groq(page_images)
    if text:
        logger.info("Groq vision OCR succeeded — %d chars extracted.", len(text))
    return text


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract text from a PDF.
    Strategy 1: pdfplumber  (fast, works for PDFs with a text layer)
    Strategy 2: OCR         (for scanned / image-only PDFs — pymupdf + Tesseract or Groq)
    """
    # --- Strategy 1: pdfplumber ---
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        text = "\n\n".join(text_parts).strip()
    except Exception as e:
        raise ValueError(f"Failed to open PDF: {str(e)}")

    if text:
        return text

    # --- Strategy 2: OCR fallback ---
    logger.info("pdfplumber returned no text — attempting OCR fallback.")
    text = _ocr_pdf(file_bytes).strip()

    if text:
        logger.info("OCR succeeded — extracted %d characters.", len(text))
        return text

    raise ValueError(
        "Could not extract text from this PDF. "
        "If it is a scanned document, please ensure your internet connection is active "
        "so the AI-powered OCR can process it, then try again."
    )


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text content from a DOCX file."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs).strip()
        if not text:
            raise ValueError(
                "This DOCX file contains no extractable text. "
                "Please ensure the document has readable text content."
            )
        return text
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to parse DOCX: {str(e)}")


def extract_text_from_pptx(file_bytes: bytes) -> str:
    """Extract text content from a PPTX (PowerPoint) file."""
    try:
        from pptx import Presentation
        prs = Presentation(io.BytesIO(file_bytes))
        parts = []
        for slide in prs.slides:
            slide_parts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        line = " ".join(run.text for run in para.runs if run.text.strip())
                        if line.strip():
                            slide_parts.append(line.strip())
            if slide_parts:
                parts.append("\n".join(slide_parts))
        text = "\n\n".join(parts).strip()
        if not text:
            raise ValueError(
                "This PowerPoint file contains no extractable text. "
                "Please ensure the presentation has readable text content."
            )
        return text
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to parse PPTX: {str(e)}")


def truncate_text(text: str, max_chars: int = 8000) -> str:
    """Truncate text to avoid exceeding LLM context limits."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [content truncated]"
