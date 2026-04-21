import asyncio
import base64
import io
import json
import os

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..models.user import User
from ..utils.security import get_current_user
from ..services.ai_service import get_groq_client
from ..services.rag_service import build_context_from_file
from ..utils.file_parser import _GROQ_VISION_MODELS

router = APIRouter(prefix="/assistant", tags=["assistant"])

_SYSTEM = (
    "You are Pritis AI — a friendly, knowledgeable academic companion built into the "
    "Pritis learning platform. You help students:\n"
    "• Understand complex academic topics clearly\n"
    "• Answer questions about their study material\n"
    "• Analyse uploaded documents and images\n"
    "• Offer study tips, mnemonics, and clear explanations\n\n"
    "Keep answers concise, friendly, and encouraging. Use simple language. "
    "If you are unsure about something, say so honestly. "
    "Unless the student clearly needs a detailed walkthrough, keep replies under 300 words."
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_DOC_EXTS   = {".pdf", ".docx", ".pptx"}
_MEDIA_MAP  = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".gif":  "image/gif", ".webp": "image/webp",
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatMsg(BaseModel):
    role: str      # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    message: str
    history: list[ChatMsg] = []
    context: Optional[str] = None


# ── Text chat ─────────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    """Plain text / context-enriched chat."""
    client   = get_groq_client()
    messages = [{"role": "system", "content": _SYSTEM}]

    if payload.context:
        messages.append({
            "role": "system",
            "content": f"Relevant document context:\n{payload.context[:4000]}",
        })

    for msg in payload.history[-12:]:
        if msg.role in ("user", "assistant"):
            messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": payload.message})

    def _call():
        return client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
        )

    try:
        resp = await asyncio.to_thread(_call)
        return {"reply": resp.choices[0].message.content}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI error: {exc}")


# ── File upload + chat ────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_and_chat(
    message: str = Form("Analyse this file and explain the key points."),
    history: str = Form("[]"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Upload a document or image and chat about it."""
    file_bytes = await file.read()
    if len(file_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit.")

    filename = file.filename or ""
    ext      = os.path.splitext(filename)[1].lower()

    if ext in _IMAGE_EXTS:
        return await _vision_reply(file_bytes, ext, message)

    if ext not in _DOC_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Supported: PDF, DOCX, PPTX, JPG, PNG, WEBP.",
        )

    # ── Document → RAG retrieval (non-blocking) ───────────────────────────────
    try:
        _, context_text = await asyncio.to_thread(
            build_context_from_file,
            file_bytes,
            ext.lstrip("."),
            message,
            8,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not process document: {exc}")

    if not context_text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from this document.")

    try:
        hist = json.loads(history)
    except Exception:
        hist = []

    client = get_groq_client()
    msgs = [
        {"role": "system", "content": _SYSTEM},
        {"role": "system", "content": f"The student uploaded a document. Relevant excerpts:\n{context_text[:4000]}"},
    ]
    for h in hist[-8:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
            msgs.append({"role": h["role"], "content": str(h["content"])})
    msgs.append({"role": "user", "content": message})

    def _call():
        return client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=msgs,
            temperature=0.5,
            max_tokens=1024,
        )

    try:
        resp = await asyncio.to_thread(_call)
        return {"reply": resp.choices[0].message.content}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI error: {exc}")


async def _vision_reply(file_bytes: bytes, ext: str, message: str) -> dict:
    media_type = _MEDIA_MAP.get(ext, "image/jpeg")
    data_url   = f"data:{media_type};base64,{base64.b64encode(file_bytes).decode()}"
    client     = get_groq_client()

    for model in _GROQ_VISION_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text",      "text": message},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]},
                ],
                temperature=0.5,
                max_tokens=1024,
            )
            return {"reply": resp.choices[0].message.content}
        except Exception:
            continue

    raise HTTPException(status_code=500, detail="Image analysis failed. Please try again.")


# ── Voice transcription ───────────────────────────────────────────────────────

@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Transcribe a voice recording (webm/mp3/wav) using Groq Whisper."""
    file_bytes = await file.read()
    if len(file_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio exceeds 25 MB limit.")
    if len(file_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio recording is too short.")

    client   = get_groq_client()
    filename = file.filename or "audio.webm"
    try:
        result = client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=(filename, io.BytesIO(file_bytes)),
        )
        return {"text": result.text}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcription error: {exc}")
