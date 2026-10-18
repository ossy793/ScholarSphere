"""What a rep sees per due (design sections 4 and 10): who paid, who hasn't, collected and
outstanding, and the paid list as PDF or CSV (regenerated on every download)."""

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.models import Class, ClassDue, ClassMembership, Payment, User

LAGOS = ZoneInfo("Africa/Lagos")


@dataclass(frozen=True)
class Student:
    user_id: uuid.UUID
    name: str
    matric_no: str | None
    paid: bool
    amount_kobo: int | None = None
    paid_at: datetime | None = None
    receipt_no: str | None = None


@dataclass(frozen=True)
class Collection:
    students: int
    paid_count: int
    collected_kobo: int
    outstanding_kobo: int
    paid: list[Student]
    unpaid: list[Student]


async def summary(db: AsyncSession, due: ClassDue) -> tuple[int, int, int, int]:
    """(students, paid, collected, outstanding) without loading the lists."""
    students = await db.scalar(
        select(func.count()).select_from(ClassMembership).where(ClassMembership.class_id == due.class_id)
    ) or 0
    paid_count, collected = (await db.execute(
        select(func.count(func.distinct(Payment.user_id)), func.coalesce(func.sum(Payment.base_kobo), 0))
        .where(Payment.due_id == due.id, Payment.status == "success")
    )).one()
    outstanding = max(students - paid_count, 0) * due.amount_kobo if due.status == "approved" else 0
    return students, paid_count, int(collected), outstanding


async def due_collection(db: AsyncSession, due: ClassDue) -> Collection:
    members = (await db.execute(
        select(User.id, User.full_name, User.matric_no)
        .join(ClassMembership, ClassMembership.user_id == User.id)
        .where(ClassMembership.class_id == due.class_id)
    )).all()
    payments = {
        p.user_id: p for p in (await db.scalars(
            select(Payment).where(Payment.due_id == due.id, Payment.status == "success").order_by(Payment.paid_at)
        )).all()
    }
    paid, unpaid = [], []
    for user_id, name, matric in members:
        p = payments.get(user_id)
        if p:
            paid.append(Student(user_id, name, p.matric_no or matric, True, p.base_kobo, p.paid_at, p.receipt_no))
        else:
            unpaid.append(Student(user_id, name, matric, False))
    paid.sort(key=lambda s: s.paid_at)
    unpaid.sort(key=lambda s: s.name.casefold())
    collected = sum(s.amount_kobo for s in paid)
    outstanding = len(unpaid) * due.amount_kobo if due.status == "approved" else 0
    return Collection(len(members), len(paid), collected, outstanding, paid, unpaid)


def _naira(kobo: int) -> str:
    whole, rest = divmod(kobo, 100)
    return f"{whole:,}.{rest:02d}"


def _when(dt: datetime) -> str:
    return dt.astimezone(LAGOS).strftime("%d %b %Y %H:%M")


def paid_list_csv(due: ClassDue, klass: Class, c: Collection) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([f"{due.title} — {klass.name}", f"Generated {_when(utcnow())} (Lagos)"])
    header = ["No.", "Name"] + (["Matric number"] if due.require_matric else []) + ["Amount paid (NGN)", "Date paid", "Receipt"]
    writer.writerow(header)
    for i, s in enumerate(c.paid, 1):
        writer.writerow([i, s.name] + ([s.matric_no or ""] if due.require_matric else [])
                        + [_naira(s.amount_kobo), _when(s.paid_at), s.receipt_no])
    writer.writerow([])
    writer.writerow(["Total paid", c.paid_count, "Collected (NGN)", _naira(c.collected_kobo),
                     "Not yet paid", len(c.unpaid)])
    return out.getvalue()


def paid_list_pdf(due: ClassDue, klass: Class, c: Collection) -> bytes:
    buffer = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"Paid list - {due.title}")
    blue, navy, muted, border = (colors.HexColor(h) for h in ("#0077FF", "#0A1628", "#4A6080", "#D6E4F7"))
    columns = [("No.", 12), ("Name", 70)] + ([("Matric number", 40)] if due.require_matric else []) + \
              [("Amount (NGN)", 28), ("Date paid", 34), ("Receipt", 30)]

    def header() -> float:
        pdf.setFillColor(blue)
        pdf.rect(0, height - 22 * mm, width, 22 * mm, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(14 * mm, height - 12 * mm, f"Paid list: {due.title}"[:70])
        pdf.setFont("Helvetica", 9)
        pdf.drawString(14 * mm, height - 18 * mm, f"{klass.name} · generated {_when(utcnow())} (Lagos)"[:110])
        y = height - 32 * mm
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica-Bold", 8)
        x = 14 * mm
        for title, w in columns:
            pdf.drawString(x, y, title.upper())
            x += w * mm
        pdf.setStrokeColor(border)
        pdf.line(14 * mm, y - 2 * mm, width - 14 * mm, y - 2 * mm)
        return y - 7 * mm

    y = header()
    pdf.setFont("Helvetica", 9)
    for i, s in enumerate(c.paid, 1):
        if y < 25 * mm:
            pdf.showPage()
            y = header()
            pdf.setFont("Helvetica", 9)
        values = [str(i), s.name[:40]] + ([s.matric_no or "—"] if due.require_matric else []) + \
                 [_naira(s.amount_kobo), _when(s.paid_at), s.receipt_no or ""]
        pdf.setFillColor(navy)
        x = 14 * mm
        for (_, w), value in zip(columns, values):
            pdf.drawString(x, y, value)
            x += w * mm
        y -= 6 * mm

    if not c.paid:
        pdf.setFillColor(muted)
        pdf.drawString(14 * mm, y, "Nobody has paid yet.")
        y -= 6 * mm
    y -= 4 * mm
    pdf.setStrokeColor(border)
    pdf.line(14 * mm, y + 3 * mm, width - 14 * mm, y + 3 * mm)
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(14 * mm, y - 2 * mm,
                   f"{c.paid_count} paid of {c.students} students · collected NGN {_naira(c.collected_kobo)} · "
                   f"{len(c.unpaid)} not yet paid")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
