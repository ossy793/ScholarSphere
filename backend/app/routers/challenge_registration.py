"""
Challenge Registration Router
------------------------------
POST /challenge-reg/register  — public: submit a registration
GET  /challenge-reg/          — admin: list all registrations
GET  /challenge-reg/export/pdf — admin: download as PDF
"""
from datetime import datetime
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, field_validator
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.challenge_registration import ChallengeRegistration
from ..utils.security import get_current_admin

router = APIRouter(prefix="/challenge-reg", tags=["challenge-registration"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    full_name:  str
    email:      EmailStr
    university: str
    rating:     int

    @field_validator("full_name", "university")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field is required.")
        return v

    @field_validator("rating")
    @classmethod
    def valid_rating(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError("Rating must be between 1 and 10.")
        return v


# ── Public endpoint ───────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Submit a general-knowledge challenge registration."""
    # Prevent duplicate email registrations
    existing = (
        db.query(ChallengeRegistration)
        .filter(func.lower(ChallengeRegistration.email) == payload.email.lower())
        .first()
    )
    if existing:
        raise HTTPException(400, "This email has already been registered.")

    reg = ChallengeRegistration(
        full_name  = payload.full_name.strip(),
        email      = payload.email.lower().strip(),
        university = payload.university.strip(),
        rating     = payload.rating,
    )
    db.add(reg)
    db.commit()
    return {"message": "Registration successful."}


# ── Admin endpoints ───────────────────────────────────────────────────────────

@router.get("/")
def list_registrations(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),   # pending | approved | rejected
    page:   int = Query(1, ge=1),
    limit:  int = Query(50, ge=1, le=200),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    q = db.query(ChallengeRegistration)
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.filter(or_(
            ChallengeRegistration.full_name.ilike(term),
            ChallengeRegistration.email.ilike(term),
            ChallengeRegistration.university.ilike(term),
        ))
    if status and status in ("pending", "approved", "rejected"):
        q = q.filter(ChallengeRegistration.status == status)

    total = q.count()
    rows  = q.order_by(ChallengeRegistration.created_at.desc()).offset((page - 1) * limit).limit(limit).all()

    return {
        "total": total,
        "page":  page,
        "pages": max(1, -(-total // limit)),
        "registrations": [
            {
                "id":         str(r.id),
                "full_name":  r.full_name,
                "email":      r.email,
                "university": r.university,
                "rating":     r.rating,
                "status":     r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/export/pdf")
def export_pdf(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Download all registrations as a formatted PDF."""
    rows = (
        db.query(ChallengeRegistration)
        .order_by(ChallengeRegistration.created_at.desc())
        .all()
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "cr_title", parent=styles["Heading1"],
        fontSize=16, alignment=TA_CENTER,
        spaceAfter=4, textColor=colors.HexColor("#0077FF"),
    )
    sub_style = ParagraphStyle(
        "cr_sub", parent=styles["Normal"],
        fontSize=9, alignment=TA_CENTER,
        textColor=colors.HexColor("#888888"), spaceAfter=14,
    )
    generated_at = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    elements = [
        Paragraph("General Knowledge Challenge Registrations", title_style),
        Paragraph(f"Generated: {generated_at}  •  {len(rows)} entries", sub_style),
        Spacer(1, 0.3 * cm),
    ]

    col_widths = [0.8*cm, 5.5*cm, 7*cm, 7*cm, 1.8*cm, 2.5*cm, 2.8*cm]
    header = ["#", "Full Name", "Email", "University", "Rating", "Status", "Date Submitted"]
    data = [header]
    for i, r in enumerate(rows, 1):
        data.append([
            str(i),
            r.full_name or "—",
            r.email or "—",
            (r.university or "—")[:55],
            str(r.rating) + "/10",
            r.status.capitalize(),
            r.created_at.strftime("%d %b %Y") if r.created_at else "—",
        ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#0077FF")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("TOPPADDING",    (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("LINEBELOW",     (0, 0), (-1, 0), 1.5, colors.HexColor("#0055CC")),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, -1), 8),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F7FF")]),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("ALIGN",         (0, 0), (0, -1), "CENTER"),
        ("ALIGN",         (4, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    filename = f"challenge-registrations-{datetime.utcnow().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
