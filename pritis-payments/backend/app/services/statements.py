"""Accountant exports for a chosen period (design section 10): every payment as CSV, and a
PDF statement with the totals, payments and payouts. Built on demand, never stored."""

import csv
import io
from datetime import date, datetime
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.models import Class, ClassDue, Payment, PayoutRequest, User
from app.services import reports

LAGOS = ZoneInfo("Africa/Lagos")


def _naira(kobo: int | None) -> str:
    if kobo is None:
        return ""
    sign = "-" if kobo < 0 else ""
    whole, rest = divmod(abs(kobo), 100)
    return f"{sign}{whole:,}.{rest:02d}"


def _when(dt: datetime | None) -> str:
    return dt.astimezone(LAGOS).strftime("%Y-%m-%d %H:%M") if dt else ""


async def _payments(db: AsyncSession, start: date, end: date):
    lo, hi = reports.period(start, end)
    return (await db.execute(
        select(Payment, User.full_name, User.email, Class.name, ClassDue.title)
        .join(User, User.id == Payment.user_id)
        .join(ClassDue, ClassDue.id == Payment.due_id).join(Class, Class.id == ClassDue.class_id)
        .where(reports.RECEIVED, Payment.paid_at >= lo, Payment.paid_at < hi)
        .order_by(Payment.paid_at)
    )).all()


async def _payouts(db: AsyncSession, start: date, end: date):
    lo, hi = reports.period(start, end)
    return (await db.execute(
        select(PayoutRequest, User.full_name)
        .join(User, User.id == PayoutRequest.rep_user_id)
        .where(PayoutRequest.status == "paid", PayoutRequest.paid_at >= lo, PayoutRequest.paid_at < hi)
        .order_by(PayoutRequest.paid_at)
    )).all()


async def payments_csv(db: AsyncSession, start: date, end: date) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Paid at (Lagos)", "Reference", "Receipt", "Student", "Email", "Class", "Due",
                "Due (NGN)", "Service charge (NGN)", "Paystack part (NGN)", "Platform part (NGN)",
                "Paystack fee actual (NGN)", "Platform revenue (NGN)", "Total paid (NGN)", "Channel",
                "Settled at (Lagos)", "Settlement reference", "Refunded at (Lagos)"])
    for p, name, email, class_name, title in await _payments(db, start, end):
        fee = p.paystack_fee_actual_kobo or 0
        w.writerow([_when(p.paid_at), p.reference, p.receipt_no, name, email, class_name, title,
                    _naira(p.base_kobo), _naira(p.service_charge_kobo), _naira(p.paystack_part_kobo),
                    _naira(p.platform_part_kobo), _naira(p.paystack_fee_actual_kobo),
                    _naira(p.service_charge_kobo - fee), _naira(p.total_kobo), p.channel or "",
                    _when(p.settled_at), p.settlement_reference or "", _when(p.refunded_at)])
    return out.getvalue()


async def statement_pdf(db: AsyncSession, start: date, end: date) -> bytes:
    figures = await reports.period_figures(db, start, end)
    payments = await _payments(db, start, end)
    payouts = await _payouts(db, start, end)

    buffer = io.BytesIO()
    page = landscape(A4)
    width, height = page
    pdf = canvas.Canvas(buffer, pagesize=page)
    pdf.setTitle(f"Statement {start} to {end}")
    blue, navy, muted, border = (colors.HexColor(h) for h in ("#0077FF", "#0A1628", "#4A6080", "#D6E4F7"))

    def header() -> float:
        pdf.setFillColor(blue)
        pdf.rect(0, height - 20 * mm, width, 20 * mm, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(14 * mm, height - 11 * mm, "Pritis Class Dues · Statement")
        pdf.setFont("Helvetica", 9)
        pdf.drawString(14 * mm, height - 16.5 * mm,
                       f"{start:%d %b %Y} to {end:%d %b %Y} (Lagos time) · generated {_when(utcnow())}")
        return height - 30 * mm

    def table(title: str, cols: list[tuple[str, float, str]], rows: list[list[str]], y: float) -> float:
        def head(y: float) -> float:
            pdf.setFillColor(navy)
            pdf.setFont("Helvetica-Bold", 11)
            pdf.drawString(14 * mm, y, title)
            y -= 6 * mm
            pdf.setFillColor(muted)
            pdf.setFont("Helvetica-Bold", 7.5)
            x = 14 * mm
            for label, w, align in cols:
                (pdf.drawRightString if align == "r" else pdf.drawString)(x + (w * mm - 2 * mm if align == "r" else 0), y, label.upper())
                x += w * mm
            pdf.setStrokeColor(border)
            pdf.line(14 * mm, y - 1.5 * mm, width - 14 * mm, y - 1.5 * mm)
            return y - 5.5 * mm

        y = head(y)
        pdf.setFont("Helvetica", 8)
        for r in rows or [["None in this period."] + [""] * (len(cols) - 1)]:
            if y < 15 * mm:
                pdf.showPage()
                y = head(header())
                pdf.setFont("Helvetica", 8)
            pdf.setFillColor(navy)
            x = 14 * mm
            for (label, w, align), value in zip(cols, r):
                text = str(value)[: int(w / 1.6)]
                (pdf.drawRightString if align == "r" else pdf.drawString)(x + (w * mm - 2 * mm if align == "r" else 0), y, text)
                x += w * mm
            y -= 5 * mm
        return y - 6 * mm

    y = header()
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(14 * mm, y, "Totals")
    y -= 7 * mm
    totals = [
        ("Payments", str(figures["payments_count"])),
        ("Gross volume (students paid)", f"NGN {_naira(figures['gross_volume_kobo'])}"),
        ("Dues (owed to reps)", f"NGN {_naira(figures['dues_kobo'])}"),
        ("Service charges", f"NGN {_naira(figures['service_charges_kobo'])}"),
        ("Paystack fees (actual)", f"NGN {_naira(figures['paystack_fees_kobo'])}"),
        ("Service charge revenue", f"NGN {_naira(figures['service_charge_revenue_kobo'])}"),
        ("Payout fee revenue", f"NGN {_naira(figures['payout_fee_revenue_kobo'])}"),
        ("Platform revenue", f"NGN {_naira(figures['service_charge_revenue_kobo'] + figures['payout_fee_revenue_kobo'])}"),
        ("Payouts paid", f"{figures['payouts_paid_count']} · NGN {_naira(figures['paid_out_kobo'])} sent"),
        ("Refunds", f"{figures['refunds_count']} · NGN {_naira(figures['refunded_kobo'])} returned"),
        ("Paystack fees on refunds (Pritis)", f"NGN {_naira(figures['refund_fee_expense_kobo'])}"),
    ]
    pdf.setFont("Helvetica", 9)
    for i, (label, value) in enumerate(totals):
        col_x = 14 * mm + (i % 3) * 90 * mm
        row_y = y - (i // 3) * 6 * mm
        pdf.setFillColor(muted)
        pdf.drawString(col_x, row_y, label)
        pdf.setFillColor(navy)
        pdf.drawRightString(col_x + 85 * mm, row_y, value)
    y -= 26 * mm

    y = table(
        "Payments",
        [("Paid", 30, "l"), ("Reference", 52, "l"), ("Class", 45, "l"), ("Due", 38, "l"),
         ("Due NGN", 24, "r"), ("Charge NGN", 22, "r"), ("Fee NGN", 20, "r"), ("Total NGN", 24, "r")],
        [[_when(p.paid_at), p.reference, c, t, _naira(p.base_kobo), _naira(p.service_charge_kobo),
          _naira(p.paystack_fee_actual_kobo), _naira(p.total_kobo)] for p, _, _, c, t in payments],
        y,
    )
    table(
        "Payouts paid",
        [("Paid", 30, "l"), ("Rep", 50, "l"), ("To account", 80, "l"), ("Payout NGN", 26, "r"),
         ("Fees NGN", 22, "r"), ("Sent NGN", 26, "r")],
        [[_when(p.paid_at), name, p.bank_label or "", _naira(p.amount_kobo),
          _naira(p.platform_fee_kobo + p.transfer_fee_kobo), _naira(p.net_kobo)] for p, name in payouts],
        y,
    )
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
