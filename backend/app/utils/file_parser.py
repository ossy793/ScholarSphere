import io
import logging
import os

logger = logging.getLogger(__name__)

# Candidate Poppler bin directories — checked in order when PATH lookup fails.
_POPPLER_CANDIDATES = [
    r"C:\poppler\Library\bin\poppler-25.12.0\Library\bin",
    r"C:\poppler\Library\bin",
    r"C:\poppler\bin",
    r"C:\Program Files\poppler\Library\bin",
    r"C:\Program Files\poppler\bin",
]

# Candidate Tesseract executable paths — checked in order when PATH lookup fails.
_TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Tesseract-OCR\tesseract.exe",
]


def _find_poppler_path() -> str | None:
    """Return the first candidate directory that contains pdfinfo.exe, or None."""
    for candidate in _POPPLER_CANDIDATES:
        if os.path.isfile(os.path.join(candidate, "pdfinfo.exe")):
            return candidate
    return None


def _find_tesseract_cmd() -> str | None:
    """Return the first candidate tesseract.exe path that exists, or None."""
    for candidate in _TESSERACT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def _ocr_pdf(file_bytes: bytes) -> str:
    """
    Fallback: convert each PDF page to an image and run Tesseract OCR on it.
    Requires:  Poppler binaries  +  Tesseract installed.
    Returns empty string if either library / binary is missing.
    """
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except Exception as import_err:
        logger.warning("pdf2image or pytesseract import failed: %s", import_err)
        return ""

    poppler_path = _find_poppler_path()
    if poppler_path:
        logger.info("Using Poppler at: %s", poppler_path)

    tesseract_cmd = _find_tesseract_cmd()
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        logger.info("Using Tesseract at: %s", tesseract_cmd)

    try:
        # dpi=200 gives a good balance between quality and speed
        kwargs = {"dpi": 200}
        if poppler_path:
            kwargs["poppler_path"] = poppler_path
        images = convert_from_bytes(file_bytes, **kwargs)
    except Exception as exc:
        # Poppler not found or other conversion error
        logger.warning("pdf2image conversion failed: %s", exc)
        return ""

    parts = []
    for i, img in enumerate(images):
        try:
            text = pytesseract.image_to_string(img)
            if text.strip():
                parts.append(text.strip())
        except Exception as exc:
            logger.warning("Tesseract OCR failed on page %d: %s", i + 1, exc)
            continue

    return "\n\n".join(parts)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract text from a PDF.
    Strategy 1: pdfplumber  (fast, works for PDFs with a text layer)
    Strategy 2: OCR via pdf2image + pytesseract  (for scanned / image PDFs)
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
        "This PDF contains no extractable text and OCR also failed. "
        "Possible causes:\n"
        "• The PDF is image-based and Tesseract/Poppler are not installed.\n"
        "• The images are too low-resolution for OCR.\n"
        "Solutions:\n"
        "1. Install Tesseract (https://github.com/UB-Mannheim/tesseract/wiki) "
        "and Poppler (https://github.com/oschwartz10612/poppler-windows/releases), "
        "then restart the server.\n"
        "2. Or paste the text manually using the Text tab."
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
