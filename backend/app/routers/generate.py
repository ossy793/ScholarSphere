import asyncio
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.quiz import SourceType
from ..models.user import User
from ..schemas.quiz import QuizResponse
from ..services import quiz_service
from ..services.ai_service import (
    generate_questions,
    generate_options_for_questions,
    parse_and_generate_from_content,
    generate_questions_from_images,
    parse_questions_from_images,
    parse_questions_from_pdf,
    generate_questions_rag,
)
from ..services.rag_service import build_context_from_file
from ..services.pdf_service import generate_quiz_pdf
from ..utils.file_parser import (
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_from_pptx,
    truncate_text,
    _render_pdf_pages,
)
from ..utils.security import get_current_user
from ..utils.access import check_access, clamp_question_count

router = APIRouter(prefix="/generate", tags=["generate"])


class OptionsRequest(BaseModel):
    questions: list[str]


class ParseRequest(BaseModel):
    content: str
    answer_hint: str = ""


class TextGenerateRequest(BaseModel):
    course_id: UUID
    quiz_title: str
    content: str
    num_questions: int = 10
    question_types: list[str] = ["mcq", "short_answer", "essay"]


def _parse_types(raw: str) -> list[str]:
    """Convert a comma-separated types string into a cleaned list."""
    return [t.strip() for t in raw.split(",") if t.strip()]


_SCANNED_TEXT_THRESHOLD = 300   # chars — below this we treat the doc as scanned


def _run_ai(text: str, num_questions: int, allowed_types: list[str] | None = None) -> list:
    """Sync — for sync routes only (FastAPI threadpool)."""
    truncated = truncate_text(text, max_chars=60000)
    try:
        return generate_questions(truncated, num_questions, allowed_types or ["mcq", "short_answer", "essay"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI generation failed: {exc}",
        )


async def _run_ai_async(text: str, num_questions: int, allowed_types: list[str] | None = None) -> list:
    """Async wrapper — offloads blocking Groq call to threadpool."""
    truncated = truncate_text(text, max_chars=60000)
    _types = allowed_types or ["mcq", "short_answer", "essay"]
    try:
        return await asyncio.to_thread(generate_questions, truncated, num_questions, _types)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI generation failed: {exc}",
        )


def _run_ai_visual(file_bytes: bytes, num_questions: int, allowed_types: list[str] | None = None) -> list:
    """Sync — for sync routes only (FastAPI threadpool)."""
    try:
        page_images = _render_pdf_pages(file_bytes, max_pages=20)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not render PDF pages: {exc}")

    if not page_images:
        raise HTTPException(status_code=422, detail="No pages could be rendered from this PDF.")

    try:
        return generate_questions_from_images(
            page_images,
            num_questions,
            allowed_types or ["mcq", "short_answer", "essay"],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Visual AI generation failed: {exc}",
        )


async def _run_ai_visual_async(file_bytes: bytes, num_questions: int, allowed_types: list[str] | None = None) -> list:
    """Async wrapper — renders pages and runs vision AI without blocking event loop."""
    try:
        page_images = await asyncio.to_thread(_render_pdf_pages, file_bytes, 20)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not render PDF pages: {exc}")

    if not page_images:
        raise HTTPException(status_code=422, detail="No pages could be rendered from this PDF.")

    _types = allowed_types or ["mcq", "short_answer", "essay"]
    try:
        return await asyncio.to_thread(generate_questions_from_images, page_images, num_questions, _types)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Visual AI generation failed: {exc}",
        )


@router.post("/options", status_code=status.HTTP_200_OK)
def generate_options(
    payload: OptionsRequest,
    current_user: User = Depends(get_current_user),
):
    """Auto-generate options, type, and correct answer for a list of bare questions."""
    if not payload.questions:
        raise HTTPException(status_code=400, detail="No questions provided.")
    try:
        return generate_options_for_questions(payload.questions)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI generation failed: {exc}",
        )


@router.post("/parse", status_code=status.HTTP_200_OK)
def parse_content_and_generate(
    payload: ParseRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Parse any unstructured educational content (past papers, notes, mixed Q&A)
    and return complete quiz questions with detected types, options, and correct answers.
    """
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty.")
    try:
        truncated = truncate_text(payload.content, max_chars=60000)
        return parse_and_generate_from_content(truncated, answer_hint=payload.answer_hint)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI generation failed: {exc}",
        )


@router.post("/parse-pdf", status_code=status.HTTP_200_OK)
async def parse_pdf_and_generate(
    answer_hint: str = Form(""),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Input Questions — extract questions from a PDF via text extraction and Groq parsing.
    """
    check_access(db, current_user, "input_questions", increment=True)

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    file_bytes = await file.read()
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Cannot upload file, it exceeds the 50MB limit.")

    try:
        results = await asyncio.to_thread(parse_questions_from_pdf, file_bytes, answer_hint)
        # Cap to plan's max_q for input_questions (free: 15)
        capped = clamp_question_count(current_user, len(results), "input_questions")
        return results[:capped]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF question parsing failed: {exc}",
        )


@router.post("/from-text", response_model=QuizResponse, status_code=status.HTTP_201_CREATED)
def generate_from_text(
    payload: TextGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_access(db, current_user, "ai_generate", increment=True)
    num_q = clamp_question_count(current_user, payload.num_questions)
    raw_questions = _run_ai(payload.content, num_q, payload.question_types)
    return quiz_service.save_generated_quiz(
        db, current_user.id, payload.course_id,
        payload.quiz_title, SourceType.ai_text, raw_questions,
    )


@router.post("/from-pdf", response_model=QuizResponse, status_code=status.HTTP_201_CREATED)
async def generate_from_pdf(
    course_id: UUID = Form(...),
    quiz_title: str = Form(...),
    num_questions: int = Form(10),
    question_types: str = Form("mcq,short_answer,essay"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_access(db, current_user, "ai_generate", increment=True)
    num_questions = clamp_question_count(current_user, num_questions)

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    file_bytes = await file.read()
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Cannot upload file, it exceeds the 50MB limit.")

    try:
        text = await asyncio.to_thread(extract_text_from_pdf, file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read PDF: {exc}")

    allowed = _parse_types(question_types)
    if len(text.strip()) >= _SCANNED_TEXT_THRESHOLD:
        raw_questions = await _run_ai_async(text, num_questions, allowed)
    else:
        raw_questions = await _run_ai_visual_async(file_bytes, num_questions, allowed)

    return quiz_service.save_generated_quiz(
        db, current_user.id, course_id,
        quiz_title, SourceType.ai_pdf, raw_questions,
    )


@router.post("/from-docx", response_model=QuizResponse, status_code=status.HTTP_201_CREATED)
async def generate_from_docx(
    course_id: UUID = Form(...),
    quiz_title: str = Form(...),
    num_questions: int = Form(10),
    question_types: str = Form("mcq,short_answer,essay"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_access(db, current_user, "ai_generate", increment=True)
    num_questions = clamp_question_count(current_user, num_questions)

    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only DOCX files are accepted")

    file_bytes = await file.read()
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Cannot upload file, it exceeds the 50MB limit.")
    try:
        text = await asyncio.to_thread(extract_text_from_docx, file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read DOCX: {exc}")

    raw_questions = await _run_ai_async(text, num_questions, _parse_types(question_types))
    return quiz_service.save_generated_quiz(
        db, current_user.id, course_id,
        quiz_title, SourceType.ai_docx, raw_questions,
    )


@router.post("/from-pptx", response_model=QuizResponse, status_code=status.HTTP_201_CREATED)
async def generate_from_pptx(
    course_id: UUID = Form(...),
    quiz_title: str = Form(...),
    num_questions: int = Form(10),
    question_types: str = Form("mcq,short_answer,essay"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_access(db, current_user, "ai_generate", increment=True)
    num_questions = clamp_question_count(current_user, num_questions)

    if not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="Only PPTX files are accepted")

    file_bytes = await file.read()
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Cannot upload file, it exceeds the 50MB limit.")
    try:
        text = await asyncio.to_thread(extract_text_from_pptx, file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read PPTX: {exc}")

    raw_questions = await _run_ai_async(text, num_questions, _parse_types(question_types))
    return quiz_service.save_generated_quiz(
        db, current_user.id, course_id,
        quiz_title, SourceType.ai_pptx, raw_questions,
    )


# ── RAG-enhanced generation ───────────────────────────────────────────────────

_ALLOWED_RAG_EXTS = {".pdf", ".docx", ".pptx"}


@router.post("/rag", status_code=status.HTTP_200_OK)
async def generate_from_rag(
    quiz_title:       str  = Form(...),
    question_type:    str  = Form("mcq"),       # "mcq" | "short_answer" | "theory"
    difficulty:       str  = Form("medium"),    # "easy" | "medium" | "hard" | "mixed"
    count:            int  = Form(10),
    output_format:    str  = Form("interactive"),  # "interactive" | "pdf"
    answer_placement: str  = Form("after_each"),   # "after_each" | "end"  (PDF only)
    course_id:        str  = Form(None),        # optional — used to save quiz
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    RAG-enhanced quiz generation.

    Flow:
      1. Extract text page-by-page from the uploaded document
      2. Chunk + embed the pages (fastembed, local)
      3. Retrieve top relevant chunks for the query "key concepts for quiz"
      4. Generate questions via Groq function calling with structured output
         (includes difficulty + page/chapter source per question)
      5. Return JSON (interactive) or a downloadable PDF

    Supported file types: PDF, DOCX, PPTX
    """
    # ── Validation ────────────────────────────────────────────────────────────
    import os
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_RAG_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Use PDF, DOCX, or PPTX.",
        )

    if question_type not in ("mcq", "short_answer", "theory"):
        raise HTTPException(status_code=400, detail="question_type must be mcq, short_answer, or theory.")
    if difficulty not in ("easy", "medium", "hard", "mixed"):
        raise HTTPException(status_code=400, detail="difficulty must be easy, medium, hard, or mixed.")
    if not (1 <= count <= 50):
        raise HTTPException(status_code=400, detail="count must be between 1 and 50.")
    if output_format not in ("interactive", "pdf"):
        raise HTTPException(status_code=400, detail="output_format must be interactive or pdf.")

    file_bytes = await file.read()
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File exceeds the 50 MB limit.")

    check_access(db, current_user, "ai_generate", increment=True)
    count = clamp_question_count(current_user, count)

    # ── RAG pipeline ──────────────────────────────────────────────────────────
    # Scale retrieved chunks with requested count so the AI has enough material
    top_k = min(20, max(8, count))

    try:
        retrieved_chunks, context_text = await asyncio.to_thread(
            build_context_from_file,
            file_bytes,
            ext.lstrip("."),
            "key concepts, definitions, and important facts for a quiz",
            top_k,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Document processing failed: {exc}")

    if not context_text.strip():
        raise HTTPException(
            status_code=422,
            detail="Could not extract any readable text from this document.",
        )

    # ── Question generation ───────────────────────────────────────────────────
    try:
        questions = await asyncio.to_thread(
            generate_questions_rag,
            retrieved_chunks,
            context_text,
            question_type,
            difficulty,
            count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"AI generation failed: {exc}",
        )

    # ── Output ────────────────────────────────────────────────────────────────
    if output_format == "pdf":
        try:
            pdf_bytes = generate_quiz_pdf(
                questions=questions,
                quiz_title=quiz_title,
                answer_placement=answer_placement,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in quiz_title)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}.pdf"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )

    # Interactive: return structured JSON
    # Optionally save to DB if course_id provided
    if course_id:
        try:
            course_uuid = UUID(course_id)
            saved = quiz_service.save_generated_quiz(
                db, current_user.id, course_uuid,
                quiz_title, SourceType.ai_pdf, questions,
            )
            return {"quiz": saved, "questions": questions}
        except Exception:
            pass  # if save fails, still return the questions

    return {
        "quiz_title": quiz_title,
        "question_type": question_type,
        "difficulty": difficulty,
        "count": len(questions),
        "questions": questions,
    }
