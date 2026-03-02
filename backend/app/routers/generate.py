from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session
from uuid import UUID
from pydantic import BaseModel

from ..database import get_db
from ..models.quiz import SourceType
from ..models.user import User
from ..schemas.quiz import QuizResponse
from ..services import quiz_service
from ..services.ai_service import generate_questions, generate_options_for_questions, parse_and_generate_from_content
from ..utils.file_parser import extract_text_from_pdf, extract_text_from_docx, extract_text_from_pptx, truncate_text
from ..utils.security import get_current_user

router = APIRouter(prefix="/generate", tags=["generate"])


class OptionsRequest(BaseModel):
    questions: list[str]


class ParseRequest(BaseModel):
    content: str


class TextGenerateRequest(BaseModel):
    course_id: UUID
    quiz_title: str
    content: str
    num_questions: int = 10
    question_types: list[str] = ["mcq", "short_answer", "essay"]


def _parse_types(raw: str) -> list[str]:
    """Convert a comma-separated types string into a cleaned list."""
    return [t.strip() for t in raw.split(",") if t.strip()]


def _run_ai(text: str, num_questions: int, allowed_types: list[str] | None = None) -> list:
    """Truncate input and call the AI service; raises 500 on failure."""
    truncated = truncate_text(text)
    try:
        return generate_questions(truncated, num_questions, allowed_types or ["mcq", "short_answer", "essay"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI generation failed: {exc}",
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
        truncated = truncate_text(payload.content)
        return parse_and_generate_from_content(truncated)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI generation failed: {exc}",
        )


@router.post("/from-text", response_model=QuizResponse, status_code=status.HTTP_201_CREATED)
def generate_from_text(
    payload: TextGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw_questions = _run_ai(payload.content, payload.num_questions, payload.question_types)
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
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    file_bytes = await file.read()
    try:
        text = extract_text_from_pdf(file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    raw_questions = _run_ai(text, num_questions, _parse_types(question_types))
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
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only DOCX files are accepted")

    file_bytes = await file.read()
    try:
        text = extract_text_from_docx(file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    raw_questions = _run_ai(text, num_questions, _parse_types(question_types))
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
    if not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="Only PPTX files are accepted")

    file_bytes = await file.read()
    try:
        text = extract_text_from_pptx(file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    raw_questions = _run_ai(text, num_questions, _parse_types(question_types))
    return quiz_service.save_generated_quiz(
        db, current_user.id, course_id,
        quiz_title, SourceType.ai_pptx, raw_questions,
    )
