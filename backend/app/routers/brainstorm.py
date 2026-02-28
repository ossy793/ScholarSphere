import uuid as uuid_module
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.user import User
from ..models.brainstorm import BrainstormSession, BrainstormMessage, BrainstormDocument
from ..utils.security import get_current_user
from ..utils.file_parser import extract_text_from_pdf, extract_text_from_docx, truncate_text
from ..services.ai_service import get_groq_client

router = APIRouter(prefix="/brainstorm", tags=["brainstorm"])

BRAINSTORM_SYSTEM = """\
You are a focused, knowledgeable study assistant. The student has uploaded the \
following document for reference:

--- DOCUMENT START ---
{context}
--- DOCUMENT END ---

Guidelines:
- Ground your answers in the document whenever possible.
- Quote or paraphrase specific sections when relevant.
- If a question falls outside the document, answer it helpfully and note it is \
not covered in the uploaded material.
- Use clear, concise language; use bullet points or numbered lists when appropriate.
- Be encouraging and educational in tone.\
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


# ── Combined upload + session creation (stores file bytes) ────────────────────

@router.post("/sessions/from-file", status_code=201)
async def create_session_from_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a file, extract text, create a session and persist the raw file bytes
    all in one request so the PDF/DOCX viewer works in history."""
    fname = file.filename.lower()
    file_bytes = await file.read()

    if fname.endswith(".pdf"):
        try:
            text = extract_text_from_pdf(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        file_type = "pdf"

    elif fname.endswith(".docx"):
        try:
            text = extract_text_from_docx(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        file_type = "docx"

    elif fname.endswith(".txt"):
        text = file_bytes.decode("utf-8", errors="replace")
        if not text.strip():
            raise HTTPException(status_code=422, detail="The text file appears to be empty.")
        file_type = "txt"

    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload a PDF, DOCX, or TXT file.",
        )

    text = truncate_text(text, max_chars=12000)

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
        extracted_text=text,
        file_size_bytes=len(file_bytes),
        file_content=file_bytes,
    )
    db.add(doc)
    db.commit()
    db.refresh(session)

    return {
        "id":         str(session.id),
        "title":      session.title,
        "text":       text,
        "filename":   file.filename,
        "char_count": len(text),
        "size_bytes": len(file_bytes),
        "file_type":  file_type,
    }


# ── Retrieve stored file bytes ─────────────────────────────────────────────────

@router.get("/sessions/{session_id}/file")
def get_session_file(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the raw file bytes stored for a session's document."""
    s = _get_session_or_404(session_id, current_user.id, db)
    if not s.document or not s.document.file_content:
        raise HTTPException(status_code=404, detail="No file stored for this session.")

    content_types = {
        "pdf":  "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt":  "text/plain; charset=utf-8",
    }
    media_type = content_types.get(s.document.file_type, "application/octet-stream")

    return Response(
        content=s.document.file_content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{s.document.filename}"'},
    )


# ── File upload ───────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Extract text from an uploaded PDF, DOCX, or TXT file."""
    fname = file.filename.lower()
    file_bytes = await file.read()

    if fname.endswith(".pdf"):
        try:
            text = extract_text_from_pdf(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    elif fname.endswith(".docx"):
        try:
            text = extract_text_from_docx(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    elif fname.endswith(".txt"):
        text = file_bytes.decode("utf-8", errors="replace")
        if not text.strip():
            raise HTTPException(status_code=422, detail="The text file appears to be empty.")

    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload a PDF, DOCX, or TXT file.",
        )

    text = truncate_text(text, max_chars=12000)
    return {
        "text": text,
        "filename": file.filename,
        "char_count": len(text),
        "size_bytes": len(file_bytes),
    }


# ── Chat ──────────────────────────────────────────────────────────────────────

@router.post("/chat")
def brainstorm_chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a message and receive an AI reply grounded in the document.
    If session_id is provided, messages are persisted to the database."""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if not payload.context.strip():
        raise HTTPException(status_code=400, detail="No document loaded.")

    # Resolve and authorise session (if provided)
    active_session = None
    if payload.session_id:
        try:
            sid = uuid_module.UUID(payload.session_id)
            active_session = db.query(BrainstormSession).filter(
                BrainstormSession.id == sid,
                BrainstormSession.user_id == current_user.id,
            ).first()
        except (ValueError, Exception):
            active_session = None   # silently ignore invalid IDs

    # Build prompt
    client = get_groq_client()
    system_prompt = BRAINSTORM_SYSTEM.format(context=payload.context)
    messages_to_send = [{"role": "system", "content": system_prompt}]
    for item in payload.history[-20:]:
        if item.role in ("user", "assistant"):
            messages_to_send.append({"role": item.role, "content": item.content})
    messages_to_send.append({"role": "user", "content": payload.message})

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

    # Persist messages if session is active
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
