"""Payment receipts as PDF (design section 10). Built on demand, never stored."""

import io
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Class, ClassDue, DueRevision, Payment, User

LAGOS = ZoneInfo("Africa/Lagos")
BLUE = colors.HexColor("#0077FF")
NAVY = colors.HexColor("#0A1628")
MUTED = colors.HexColor("#4A6080")
BORDER = colors.HexColor("#D6E4F7")

CHANNELS = {
    "card": "Card", "bank": "Bank", "bank_transfer": "Bank transfer", "ussd": "USSD",
    "qr": "QR", "mobile_money": "Mobile money", "apple_pay": "Apple Pay",
}


def naira(kobo: int) -> str:
    # ReportLab's built-in fonts have no ₦ glyph, so receipts write "NGN".
    whole, rest = divmod(kobo, 100)
    return f"NGN {whole:,}.{rest:02d}"


def lagos_time(when: datetime) -> str:
    return when.astimezone(LAGOS).strftime("%d %b %Y, %I:%M %p")


async def receipt_pdf(db: AsyncSession, payment_id: uuid.UUID) -> tuple[str, bytes]:
    payment = await db.get(Payment, payment_id)
    if payment is None or payment.status != "success" or not payment.receipt_no:
        raise AppError("no_receipt", "There's no receipt for this payment yet", 404)
    user = await db.get(User, payment.user_id)
    due = await db.get(ClassDue, payment.due_id)
    revision = await db.get(DueRevision, payment.revision_id)
    klass = await db.get(Class, due.class_id)
    title = revision.snapshot["title"]  # the terms that were paid, even if the due changed later

    buffer = io.BytesIO()
    width, height = A5
    pdf = canvas.Canvas(buffer, pagesize=A5)
    pdf.setTitle(f"Receipt {payment.receipt_no}")
    pdf.setAuthor("Pritis Class Dues")

    # Header band with the Pritis "P" mark.
    pdf.setFillColor(BLUE)
    pdf.rect(0, height - 28 * mm, width, 28 * mm, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.roundRect(12 * mm, height - 21 * mm, 12 * mm, 12 * mm, 2.5 * mm, stroke=0, fill=1)
    pdf.setFillColor(BLUE)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(18 * mm, height - 17.5 * mm, "P")
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(28 * mm, height - 14 * mm, "Pritis Class Dues")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(28 * mm, height - 19.5 * mm, "Payment receipt")

    y = height - 40 * mm
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(12 * mm, y, title[:60])
    y -= 6 * mm
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(12 * mm, y, f"{klass.name} · {klass.school}"[:90])

    def row(label: str, value: str, bold: bool = False) -> None:
        nonlocal y
        y -= 7 * mm
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(12 * mm, y, label)
        pdf.setFillColor(NAVY)
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        pdf.drawRightString(width - 12 * mm, y, value[:60])

    y -= 4 * mm
    row("Receipt number", payment.receipt_no)
    row("Date paid", lagos_time(payment.paid_at))
    row("Student", user.full_name)
    if payment.matric_no or user.matric_no:
        row("Matric number", payment.matric_no or user.matric_no)
    row("Payment reference", payment.reference)
    row("Paid with", CHANNELS.get(payment.channel or "", (payment.channel or "Paystack").title()))

    # Only the total: students don't see a due / service charge breakdown (owner's decision).
    y -= 5 * mm
    pdf.setStrokeColor(BORDER)
    pdf.line(12 * mm, y, width - 12 * mm, y)
    row("Total paid", naira(payment.total_kobo), bold=True)

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(12 * mm, 14 * mm, "Paid through Paystack to Pritis for your class rep. Keep this receipt for your records.")
    pdf.drawString(12 * mm, 10 * mm, "Questions about this payment? Contact Pritis support with the receipt number.")
    pdf.showPage()
    pdf.save()
    return f"receipt-{payment.receipt_no}.pdf", buffer.getvalue()
