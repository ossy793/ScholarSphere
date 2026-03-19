import logging
import uuid as uuid_module
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models.user import User
from ..models.brainstorm import BrainstormSession, BrainstormMessage, BrainstormDocument
from ..utils.security import get_current_user
from ..utils.file_parser import (
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_from_pptx,
    extract_text_from_image,
    truncate_text,
    _GROQ_VISION_MODELS,
    _render_pdf_page_range,
)
from ..utils.pdf_converter import convert_to_pdf, IMAGE_TYPES
from ..utils.chunker import chunk_text
from ..utils.rag import store_chunks, retrieve_chunks, chunks_exist
from ..services.ai_service import get_groq_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brainstorm", tags=["brainstorm"])

_MAX_UPLOAD     = 50 * 1024 * 1024  # 50 MB
_MAX_STORED_CHARS = 200_000          # ~50 000 tokens — full document stored for RAG

# All image extensions the system accepts
_IMAGE_EXTS = {f".{t}" for t in IMAGE_TYPES}

# ── System prompt (RAG-aware) ──────────────────────────────────────────────────

BRAINSTORM_SYSTEM = """\
You are UrPadi, a friendly study assistant helping a student understand their uploaded document.

--- RELEVANT DOCUMENT SECTIONS ---
{context}
--- END ---

The sections above were retrieved from the document as most relevant to the student's question.

Rules:

1. ACCURACY: Base your answer on the retrieved sections above. \
If the answer is clearly present, explain it thoroughly. \
Never say information is missing if it appears in the retrieved sections.

2. PARTIAL INFO: If the retrieved sections do not fully cover the question, \
just answer directly using what you can see plus your knowledge. \
Do NOT say "this is not in the document" or "based on general knowledge" — \
just give the student a clear, helpful answer.

3. UNCLEAR DOCUMENT: If the document text appears garbled, incomplete, or \
seems to be from a scanned/handwritten source that wasn't fully captured, \
say ONE brief line like: "I can't clearly make this out in your document, but here's what I know:" \
then immediately give a full, helpful answer. Never leave the student without an answer.

4. NO COPYING: Rephrase and explain ideas in your own words — do not copy sentences verbatim.

5. SIMPLIFY: Break down complex ideas into plain, easy-to-understand language. \
Explain as if talking to a smart friend, not writing an essay.

6. BE CONCISE: Give short, focused answers. No long paragraphs. \
Use bullet points only if they genuinely help.

7. TONE: Supportive and clear. You are a helpful study companion, not a textbook.\
"""


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class HistoryItem(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    context: str
    history: List[HistoryItem] = []
    session_id: Optional[str] = None
    current_page: int = 1   # PDF viewer's current page — used for visual Q&A page selection


class SummaryRequest(BaseModel):
    context: str
    filename: str = "Document"


class CreateSessionRequest(BaseModel):
    title: str
    filename: str
    file_type: str
    extracted_text: str
    file_size_bytes: Optional[int] = None


class RenameSessionRequest(BaseModel):
    title: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_session_or_404(session_id: str, user_id, db: Session) -> BrainstormSession:
    try:
        sid = uuid_module.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    session = db.query(BrainstormSession).filter(
        BrainstormSession.id == sid,
        BrainstormSession.user_id == user_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


def _session_list_item(s: BrainstormSession) -> dict:
    return {
        "id": str(s.id),
        "title": s.title,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        "message_count": len(s.messages),
        "document": {
            "id": str(s.document.id),
            "filename": s.document.filename,
            "file_type": s.document.file_type,
            "file_size_bytes": s.document.file_size_bytes,
            "created_at": s.document.created_at.isoformat(),
        } if s.document else None,
    }


def _detect_file_type(filename: str) -> str | None:
    """Return a normalised file_type string, or None if unsupported."""
    fname = filename.lower()
    if fname.endswith(".pdf"):
        return "pdf"
    if fname.endswith(".docx"):
        return "docx"
    if fname.endswith(".pptx"):
        return "pptx"
    if fname.endswith(".txt"):
        return "txt"
    for ext in _IMAGE_EXTS:
        if fname.endswith(ext):
            return ext.lstrip(".")   # e.g. "png", "jpg"
    return None


# ── Background tasks ──────────────────────────────────────────────────────────

def _convert_and_store_pdf(session_id_str: str, file_bytes: bytes,
                            file_type: str, filename: str) -> None:
    """
    Run in a FastAPI BackgroundTask after the upload response is sent.
    Converts the uploaded file to PDF and stores it in brainstorm_documents.
    Uses its own DB session so the request session is already closed.
    """
    db = SessionLocal()
    try:
        pdf_bytes = convert_to_pdf(file_bytes, file_type, filename)
        if pdf_bytes is None:
            logger.info("No PDF conversion needed/available for %s (type=%s).", filename, file_type)
            return

        session_id = uuid_module.UUID(session_id_str)
        doc = db.query(BrainstormDocument).filter(
            BrainstormDocument.session_id == session_id
        ).first()
        if doc:
            doc.viewer_pdf = pdf_bytes
            doc.pdf_ready  = True
            db.commit()
            logger.info("PDF conversion stored for session %s (%d bytes).", session_id_str, len(pdf_bytes))
        else:
            logger.warning("Background PDF task: document not found for session %s.", session_id_str)
    except Exception as exc:
        logger.warning("Background PDF conversion failed for session %s: %s", session_id_str, exc)
    finally:
        db.close()


def _chunk_and_embed(session_id_str: str, document_id_str: str, text: str) -> None:
    """
    Background task: chunk the document text and store embeddings via RAG pipeline.
    Runs after the upload response is sent so the user can start chatting immediately.
    Falls back gracefully — if embedding fails, keyword search still works.
    """
    if not text or not text.strip():
        return
    db = SessionLocal()
    try:
        chunks = chunk_text(text)
        if not chunks:
            return
        store_chunks(
            db,
            uuid_module.UUID(document_id_str),
            uuid_module.UUID(session_id_str),
            chunks,
        )
        logger.info(
            "RAG: %d chunks embedded for session %s.", len(chunks), session_id_str
        )
    except Exception as exc:
        logger.warning("RAG chunking/embedding failed for session %s: %s", session_id_str, exc)
    finally:
        db.close()


# ── Session CRUD ──────────────────────────────────────────────────────────────

@router.get("/sessions")
def list_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all brainstorm sessions for the current user, newest-updated first."""
    sessions = (
        db.query(BrainstormSession)
        .filter(BrainstormSession.user_id == current_user.id)
        .order_by(BrainstormSession.updated_at.desc())
        .all()
    )
    return [_session_list_item(s) for s in sessions]


@router.post("/sessions", status_code=201)
def create_session(
    payload: CreateSessionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a brainstorm session with its associated document."""
    if not payload.extracted_text.strip():
        raise HTTPException(status_code=422, detail="Document text cannot be empty.")

    session = BrainstormSession(
        user_id=current_user.id,
        title=payload.title[:255],
    )
    db.add(session)
    db.flush()   # populate session.id

    doc = BrainstormDocument(
        session_id=session.id,
        filename=payload.filename[:255],
        file_type=payload.file_type[:20],
        extracted_text=payload.extracted_text,
        file_size_bytes=payload.file_size_bytes,
    )
    db.add(doc)
    db.commit()
    db.refresh(session)
    db.refresh(doc)

    # Kick off RAG chunking in the background
    background_tasks.add_task(
        _chunk_and_embed,
        str(session.id),
        str(doc.id),
        payload.extracted_text,
    )

    return {"id": str(session.id), "title": session.title}


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a full session with all messages and the document context."""
    s = _get_session_or_404(session_id, current_user.id, db)
    return {
        "id": str(s.id),
        "title": s.title,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in s.messages
        ],
        "document": {
            "id": str(s.document.id),
            "filename": s.document.filename,
            "file_type": s.document.file_type,
            "extracted_text": s.document.extracted_text,
            "file_size_bytes": s.document.file_size_bytes,
            "pdf_ready": s.document.pdf_ready,
            "created_at": s.document.created_at.isoformat(),
        } if s.document else None,
    }


@router.put("/sessions/{session_id}")
def rename_session(
    session_id: str,
    payload: RenameSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename a brainstorm session."""
    s = _get_session_or_404(session_id, current_user.id, db)
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="Title cannot be empty.")
    s.title = payload.title.strip()[:255]
    s.updated_at = datetime.utcnow()
    db.commit()
    return {"id": str(s.id), "title": s.title}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a brainstorm session and all associated messages / document."""
    s = _get_session_or_404(session_id, current_user.id, db)
    db.delete(s)
    db.commit()


# ── Combined upload + session creation ────────────────────────────────────────

@router.post("/sessions/from-file", status_code=201)
async def create_session_from_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload a document, extract text (Pipeline A), create a session,
    then kick off PDF conversion + RAG chunking in the background.

    The response is returned immediately so the user can start chatting
    while the viewer PDF and embeddings are being prepared asynchronously.
    """
    file_bytes = await file.read()

    if len(file_bytes) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Cannot upload file, it exceeds the 50MB limit.")

    file_type = _detect_file_type(file.filename)
    if file_type is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Please upload a PDF, DOCX, PPTX, TXT, "
                "or image file (PNG, JPG, JPEG, WEBP, GIF)."
            ),
        )

    # ── Pipeline A: text extraction ───────────────────────────────────────────
    ocr_failed = False
    ocr_error  = None

    if file_type == "pdf":
        try:
            text = extract_text_from_pdf(file_bytes)
        except ValueError as e:
            ocr_failed = True
            ocr_error  = str(e)
            text       = ""

    elif file_type == "docx":
        try:
            text = extract_text_from_docx(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    elif file_type == "pptx":
        try:
            text = extract_text_from_pptx(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    elif file_type == "txt":
        text = file_bytes.decode("utf-8", errors="replace")
        if not text.strip():
            raise HTTPException(status_code=422, detail="The text file appears to be empty.")

    elif file_type in IMAGE_TYPES:
        text = extract_text_from_image(file_bytes)
        if not text:
            ocr_failed = True
            ocr_error  = "No text could be extracted from this image."

    else:
        text = ""

    # Store the full text for RAG (up to _MAX_STORED_CHARS), but return a
    # shorter preview to the frontend to keep the response fast.
    stored_text   = truncate_text(text, max_chars=_MAX_STORED_CHARS) if text else ""
    response_text = truncate_text(text, max_chars=12000)             if text else ""

    # ── PDFs are immediately ready (no conversion needed) ─────────────────────
    pdf_ready = file_type == "pdf"

    # ── Persist session + document ────────────────────────────────────────────
    session = BrainstormSession(
        user_id=current_user.id,
        title=file.filename[:255],
    )
    db.add(session)
    db.flush()

    doc = BrainstormDocument(
        session_id=session.id,
        filename=file.filename[:255],
        file_type=file_type,
        extracted_text=stored_text,
        file_size_bytes=len(file_bytes),
        file_content=file_bytes,
        viewer_pdf=file_bytes if pdf_ready else None,
        pdf_ready=pdf_ready,
    )
    db.add(doc)
    db.commit()
    db.refresh(session)
    db.refresh(doc)

    # ── Pipeline B: async PDF conversion for non-PDF types ───────────────────
    if not pdf_ready and file_type != "txt":
        background_tasks.add_task(
            _convert_and_store_pdf,
            str(session.id),
            file_bytes,
            file_type,
            file.filename,
        )

    # ── Pipeline C: async RAG chunking + embedding ────────────────────────────
    if stored_text:
        background_tasks.add_task(
            _chunk_and_embed,
            str(session.id),
            str(doc.id),
            stored_text,
        )

    return {
        "id":         str(session.id),
        "title":      session.title,
        "text":       response_text,
        "filename":   file.filename,
        "char_count": len(stored_text),
        "size_bytes": len(file_bytes),
        "file_type":  file_type,
        "pdf_ready":  pdf_ready,
        "ocr_failed": ocr_failed,
        "ocr_error":  ocr_error,
    }


# ── PDF viewer endpoints ───────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/pdf-status")
def get_pdf_status(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check whether the viewer PDF is ready for a session."""
    s = _get_session_or_404(session_id, current_user.id, db)
    if not s.document:
        return {"ready": False}
    return {"ready": s.document.pdf_ready}


@router.get("/sessions/{session_id}/document.pdf")
def get_session_pdf(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return the viewer PDF for a session.

    - 200: PDF bytes ready — serve to the iframe viewer.
    - 202: Conversion still in progress — frontend should retry.
    - 404: No PDF available (e.g. TXT file, or conversion failed).
    """
    s = _get_session_or_404(session_id, current_user.id, db)
    if not s.document:
        raise HTTPException(status_code=404, detail="No document for this session.")

    if s.document.pdf_ready and s.document.viewer_pdf:
        return Response(
            content=s.document.viewer_pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{s.document.filename}.pdf"',
                "Cache-Control": "private, max-age=3600",
            },
        )

    # TXT files never get a PDF viewer
    if s.document.file_type in ("txt", "paste"):
        raise HTTPException(status_code=404, detail="No PDF viewer for text files.")

    # Still converting
    return JSONResponse(status_code=202, content={"status": "converting"})


# ── Retrieve raw original file bytes ──────────────────────────────────────────

@router.get("/sessions/{session_id}/file")
def get_session_file(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the original raw file bytes stored for a session's document."""
    s = _get_session_or_404(session_id, current_user.id, db)
    if not s.document or not s.document.file_content:
        raise HTTPException(status_code=404, detail="No file stored for this session.")

    content_types = {
        "pdf":  "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "txt":  "text/plain; charset=utf-8",
    }
    media_type = content_types.get(s.document.file_type, "application/octet-stream")

    return Response(
        content=s.document.file_content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{s.document.filename}"'},
    )


# ── Text-only upload (no session creation) ────────────────────────────────────

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Extract text from an uploaded file (Pipeline A only, no session created)."""
    file_bytes = await file.read()

    if len(file_bytes) > _MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Cannot upload file, it exceeds the 50MB limit.")

    file_type = _detect_file_type(file.filename)
    if file_type is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Please upload a PDF, DOCX, PPTX, TXT, "
                "or image file (PNG, JPG, JPEG, WEBP, GIF)."
            ),
        )

    if file_type == "pdf":
        try:
            text = extract_text_from_pdf(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    elif file_type == "docx":
        try:
            text = extract_text_from_docx(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    elif file_type == "pptx":
        try:
            text = extract_text_from_pptx(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    elif file_type == "txt":
        text = file_bytes.decode("utf-8", errors="replace")
        if not text.strip():
            raise HTTPException(status_code=422, detail="The text file appears to be empty.")

    elif file_type in IMAGE_TYPES:
        text = extract_text_from_image(file_bytes)
        if not text:
            raise HTTPException(
                status_code=422,
                detail="No text could be extracted from this image.",
            )

    else:
        text = ""

    text = truncate_text(text, max_chars=60000)
    return {
        "text": text,
        "filename": file.filename,
        "char_count": len(text),
        "size_bytes": len(file_bytes),
    }


# ── Visual Q&A helpers ────────────────────────────────────────────────────────

def _is_scanned_doc(doc_record) -> bool:
    """
    Detect whether a PDF has little/no extractable text (i.e. it is scanned).
    Uses text length vs file size as a simple heuristic.
    """
    if not doc_record or doc_record.file_type.lower() != "pdf":
        return False
    text_len = len(doc_record.extracted_text or "")
    if text_len < 300:
        return True
    size_bytes = doc_record.file_size_bytes or 0
    if size_bytes > 200_000 and text_len / size_bytes < 0.003:
        return True
    return False


def _visual_qa_chat(doc_record, question: str, history: list, current_page: int = 1) -> str | None:
    """
    Visual Q&A for scanned PDFs:
    - Renders up to 5 pages centred on current_page from the stored file bytes.
    - Sends the page images + question to a Groq vision model.
    - Returns the AI reply, or None if vision Q&A is unavailable.
    """
    import os, base64 as b64

    if not doc_record or not doc_record.file_content:
        return None

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return None

    # Page window: up to 5 pages centred on current_page (1-indexed → 0-indexed)
    try:
        import fitz
        pdf_doc = fitz.open(stream=doc_record.file_content, filetype="pdf")
        total = len(pdf_doc)
        pdf_doc.close()
    except Exception as exc:
        logger.warning("Visual Q&A: could not open PDF: %s", exc)
        return None

    p0 = max(0, current_page - 3)
    p1 = min(total, p0 + 5)
    p0 = max(0, p1 - 5)   # adjust start if near end of doc
    pages_label = f"pages {p0 + 1}–{p1}" if p1 > p0 + 1 else f"page {p0 + 1}"

    try:
        page_images = _render_pdf_page_range(doc_record.file_content, p0, p1)
    except Exception as exc:
        logger.warning("Visual Q&A: page render failed: %s", exc)
        return None

    if not page_images:
        return None

    # Build image content blocks
    image_blocks = []
    for img_bytes in page_images:
        img_b64 = b64.b64encode(img_bytes).decode()
        image_blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
        del img_b64

    # Include recent chat history as text
    history_text = ""
    if history:
        recent = [f"{'User' if h['role'] == 'user' else 'UrPadi'}: {h['content']}" for h in history[-6:]]
        history_text = "\nPrevious conversation:\n" + "\n".join(recent) + "\n\n"

    text_block = {
        "type": "text",
        "text": (
            f"You are UrPadi, an AI academic tutor. The images show {pages_label} "
            f"from the student's uploaded document '{doc_record.filename}'."
            f"{history_text}"
            f"Student's question: {question}\n\n"
            f"Read the pages carefully and answer accurately. "
            f"If the answer is visible on these pages, give a detailed response. "
            f"If the relevant content is on a different page, tell the student which page to navigate to."
        ),
    }

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except Exception:
        return None

    for model in _GROQ_VISION_MODELS:
        try:
            # Llama 3.2 supports only 1 image — send just the current page
            if "3.2" in model:
                mid = len(image_blocks) // 2
                content = [image_blocks[mid], text_block]
            else:
                content = image_blocks + [text_block]

            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                max_tokens=1024,
                temperature=0.3,
            )
            reply = response.choices[0].message.content.strip()
            if reply:
                logger.info("Visual Q&A answered with %s (%s)", model, pages_label)
                return reply
        except Exception as exc:
            logger.warning("Visual Q&A failed with %s: %s", model, exc)
            continue

    return None


# ── Chat ──────────────────────────────────────────────────────────────────────

@router.post("/chat")
def brainstorm_chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Send a message and receive an AI reply grounded in the document.

    RAG pipeline:
      1. If session_id is provided and chunks exist → retrieve top-7 relevant
         chunks via vector similarity and use them as context.
      2. If chunks are not ready yet (still being embedded in background) →
         fall back to payload.context (the truncated text from upload).
      3. If no context at all → reject with 400.

    Messages are persisted when session_id is valid.
    """
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # ── Resolve session ───────────────────────────────────────────────────────
    active_session = None
    session_uuid   = None
    if payload.session_id:
        try:
            session_uuid = uuid_module.UUID(payload.session_id)
            active_session = db.query(BrainstormSession).filter(
                BrainstormSession.id == session_uuid,
                BrainstormSession.user_id == current_user.id,
            ).first()
        except (ValueError, Exception):
            active_session = None

    # ── RAG retrieval ─────────────────────────────────────────────────────────
    context = ""
    rag_used = False

    if session_uuid and chunks_exist(db, session_uuid):
        try:
            retrieved = retrieve_chunks(db, session_uuid, payload.message, top_k=7)
            if retrieved:
                context  = "\n\n---\n\n".join(retrieved)
                rag_used = True
        except Exception as exc:
            logger.warning("RAG retrieval error for session %s: %s", session_uuid, exc)
            try:
                db.rollback()
            except Exception:
                pass

    # ── Fallback: load full extracted_text from DB ────────────────────────────
    # payload.context is only the first 12k chars (upload preview). When RAG
    # is unavailable we load the complete stored text from BrainstormDocument
    # so the AI can answer questions about any part of the document.
    if not context:
        if session_uuid:
            try:
                doc_record = db.query(BrainstormDocument).filter(
                    BrainstormDocument.session_id == session_uuid
                ).first()
                if doc_record and doc_record.extracted_text:
                    context = truncate_text(doc_record.extracted_text, max_chars=60000)
            except Exception as exc:
                logger.warning("Could not load document text from DB: %s", exc)
                try:
                    db.rollback()
                except Exception:
                    pass

    # Last resort: use whatever the frontend sent
    if not context:
        context = payload.context.strip()

    # ── Visual Q&A for scanned documents ──────────────────────────────────────
    # If context is still too short (scanned PDF with no extractable text),
    # skip the text pipeline and answer directly from rendered page images.
    if len(context) < 300 and session_uuid:
        try:
            doc_record = db.query(BrainstormDocument).filter(
                BrainstormDocument.session_id == session_uuid
            ).first()
            if _is_scanned_doc(doc_record):
                logger.info("Scanned doc detected — attempting visual Q&A for session %s", session_uuid)
                visual_reply = _visual_qa_chat(
                    doc_record,
                    payload.message,
                    [h.dict() for h in payload.history],
                    payload.current_page,
                )
                if visual_reply:
                    # Persist messages and return immediately
                    if active_session:
                        try:
                            db.add(BrainstormMessage(session_id=active_session.id, role="user", content=payload.message))
                            db.add(BrainstormMessage(session_id=active_session.id, role="assistant", content=visual_reply))
                            active_session.updated_at = datetime.utcnow()
                            db.commit()
                        except Exception:
                            db.rollback()
                    return {"reply": visual_reply}
        except Exception as exc:
            logger.warning("Visual Q&A pipeline error: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass

    if not context:
        raise HTTPException(status_code=400, detail="No document loaded.")

    if not rag_used:
        logger.info(
            "RAG not available for session %s — using full DB text fallback.",
            payload.session_id,
        )

    # ── Build prompt ──────────────────────────────────────────────────────────
    system_prompt    = BRAINSTORM_SYSTEM.format(context=context)
    messages_to_send = [{"role": "system", "content": system_prompt}]
    for item in payload.history[-20:]:
        if item.role in ("user", "assistant"):
            messages_to_send.append({"role": item.role, "content": item.content})
    messages_to_send.append({"role": "user", "content": payload.message})

    # ── Call Groq ─────────────────────────────────────────────────────────────
    try:
        response = get_groq_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages_to_send,
            temperature=0.3,
            max_tokens=1024,
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI error: {str(e)}",
        )

    # ── Persist messages ──────────────────────────────────────────────────────
    if active_session:
        db.add(BrainstormMessage(
            session_id=active_session.id,
            role="user",
            content=payload.message,
        ))
        db.add(BrainstormMessage(
            session_id=active_session.id,
            role="assistant",
            content=reply,
        ))
        active_session.updated_at = datetime.utcnow()
        db.commit()

    return {"reply": reply}


_SUMMARY_SYSTEM_PROMPT = (
    "You are an expert academic tutor, educator, and summarizer. "
    "Your summaries are known for being clear, engaging, well-structured, and deeply useful for students. "
    "You write in a warm, intelligent, and informative tone — never robotic or vague. "
    "You faithfully represent the source material without adding information that is not in the document. "
    "You use plain text with clear ALL-CAPS section headers, numbered lists, and dashes. "
    "No markdown, no asterisks, no hashtags, no bullet symbols — use numbers and dashes only."
)

_SHORT_DOC_PROMPT = """\
You are summarizing a SHORT document (1–5 pages). Because the document is concise, your summary must be thorough and capture every meaningful idea, definition, and example the author included.

Use EXACTLY these section headers in this exact order (write each header in ALL CAPS on its own line):

DOCUMENT TITLE
Extract or infer the document title. Write it on one line.

OVERVIEW
Write 3–4 sentences describing the main topic, purpose, and who the document is written for. Make it engaging — like you are introducing the topic to a student for the first time.

KEY CONCEPTS
Identify every major concept discussed. For each one, write:
  [Number]. [Concept Name] - [Clear explanation in 1–2 simple sentences]
Aim for all significant concepts, not just the first few.

DEFINITIONS AND KEY TERMS
List every important term, definition, formula, or technical vocabulary item. Format:
  [Term] - [Definition]
Do not skip any defined terms.

IMPORTANT POINTS
List all the important ideas, arguments, facts, or findings from the document as numbered points. Be specific — include figures, names, dates, or conditions where relevant.

EXAMPLES OR APPLICATIONS
Summarize any examples, analogies, or real-world applications given. If none exist, write: None provided in this document.

STEP-BY-STEP PROCESSES
If the document explains any method, procedure, algorithm, or process, break it down step by step. If not applicable, write: Not applicable.

EXAM AND REVISION HIGHLIGHTS
List the most exam-worthy facts, concepts, definitions, or principles. These are the things a student absolutely must remember. Number each one.

TL;DR - KEY TAKEAWAYS
Provide 4–6 concise takeaways — the core lessons a student should walk away with after reading this document. Start each with a dash.

IMPORTANT KEYWORDS
List 8–12 important keywords or phrases from the document, separated by commas.

Quality rules:
- Be thorough — short documents deserve full coverage, not a one-paragraph gloss.
- Preserve the logical flow and original meaning of the material.
- Use plain text only. No markdown. No asterisks. No hashtags.
- Write at a level suitable for a university or senior secondary student.
- Avoid fluff, repetition, and vague statements. Every sentence must add value.

Document:
{content}"""

_LONG_DOC_PROMPT = """\
You are summarizing a LONG document (10+ pages). The document is extensive, so your job is to intelligently prioritize and organize the most important content — not repeat everything, but ensure no major idea is missed.

Use EXACTLY these section headers in this exact order (write each header in ALL CAPS on its own line):

DOCUMENT TITLE
Extract or infer the document title. Write it on one line.

OVERVIEW
Write 4–5 sentences describing the main topic, purpose, scope, and intended audience. Capture the big picture clearly and engagingly.

MAIN TOPICS AND STRUCTURE
Identify the major sections or chapters in the document and briefly describe what each one covers. Format:
  [Number]. [Section/Topic Name] - [1–2 sentence description]

KEY CONCEPTS
Identify and explain the most important concepts from across the entire document. For each one:
  [Number]. [Concept Name] - [Clear explanation in 2–3 simple sentences]
Cover all significant concepts, not just early ones.

IMPORTANT POINTS
Extract the most important arguments, findings, rules, principles, or facts from across all sections. Number each point. Be specific — include figures, criteria, conditions, or names where relevant.

DEFINITIONS AND KEY TERMS
List all important terms, definitions, formulas, laws, or technical vocabulary. Format:
  [Term] - [Definition]

EXAMPLES OR APPLICATIONS
Summarize significant examples, case studies, scenarios, or real-world applications discussed. If none exist, write: None provided in this document.

STEP-BY-STEP PROCESSES
If the document describes any methods, procedures, or processes, break each one down into numbered steps. If not applicable, write: Not applicable.

EXAM AND REVISION HIGHLIGHTS
List the most exam-critical facts, definitions, concepts, and principles from the document. These are what a student must memorize or deeply understand. Number each one.

TL;DR - KEY TAKEAWAYS
Provide 6–10 concise takeaways — the essential lessons and conclusions from the entire document. Start each with a dash.

IMPORTANT KEYWORDS
List 12–20 important keywords or phrases from the document, separated by commas.

Quality rules:
- Cover the ENTIRE document — not just the first few pages or chapters.
- Prioritize academically and practically relevant content.
- Preserve original meaning; never fabricate or infer beyond what is written.
- Use plain text only. No markdown. No asterisks. No hashtags.
- Write at a level suitable for a university student.
- Be comprehensive but not repetitive. Every sentence must earn its place.

Document:
{content}"""


@router.post("/summary")
def generate_summary(
    payload: SummaryRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Generate a structured, study-ready summary of the uploaded document.
    Automatically selects short-doc or long-doc prompt based on content length.
    """
    if not payload.context.strip():
        raise HTTPException(status_code=400, detail="No document context provided.")

    truncated = truncate_text(payload.context, max_chars=60000)

    # ~1500 chars per page → short = under ~7500 chars (5 pages), long = above
    is_long = len(truncated) > 7500
    template = _LONG_DOC_PROMPT if is_long else _SHORT_DOC_PROMPT
    prompt = template.format(content=truncated)
    temperature = 0.25 if is_long else 0.35  # more precise for long, slightly warmer for short

    try:
        response = get_groq_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=4096,
        )
        summary = response.choices[0].message.content.strip()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI error: {str(e)}",
        )

    return {"summary": summary, "filename": payload.filename}
