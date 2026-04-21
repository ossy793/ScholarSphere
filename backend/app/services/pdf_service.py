"""
Quiz PDF generation using ReportLab.

generate_quiz_pdf(questions, quiz_title, answer_placement) → bytes

answer_placement:
  "after_each"  — answer + explanation printed directly below each question
  "end"         — all answers collected in an Answer Key section at the end
"""
import io
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# ── Brand colours (0–1 float scale for ReportLab) ────────────────────────────
_C_PRIMARY = (0 / 255, 119 / 255, 255 / 255)   # #0077FF
_C_DARK    = (15 / 255,  23 / 255,  42 / 255)   # #0f172a
_C_MUTED   = (100 / 255, 116 / 255, 139 / 255)  # #64748b
_C_BORDER  = (226 / 255, 232 / 255, 240 / 255)  # #e2e8f0
_C_LIGHT   = (248 / 255, 250 / 255, 252 / 255)  # #f8fafc

_OPTION_LETTERS = ["A", "B", "C", "D", "E", "F"]

_DIFFICULTY_COLOURS = {
    "easy":   (22 / 255,  163 / 255,  74 / 255),   # green
    "medium": (234 / 255, 179 / 255,   8 / 255),   # amber
    "hard":   (239 / 255,  68 / 255,  68 / 255),   # red
}


def _safe_xml(text: str) -> str:
    """Escape XML special chars so ReportLab's parser doesn't choke."""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_quiz_pdf(
    questions:        List[Dict[str, Any]],
    quiz_title:       str,
    answer_placement: str = "after_each",
) -> bytes:
    """
    Build a formatted quiz PDF and return raw bytes.
    Raises RuntimeError if reportlab is not installed.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer,
            HRFlowable, KeepTogether,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        raise RuntimeError(
            "reportlab is not installed. "
            "Run: pip install reportlab"
        )

    # ── ReportLab colour objects ──────────────────────────────────────────────
    c_primary = colors.Color(*_C_PRIMARY)
    c_dark    = colors.Color(*_C_DARK)
    c_muted   = colors.Color(*_C_MUTED)
    c_border  = colors.Color(*_C_BORDER)

    # ── Styles ────────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()

    def _style(name, **kwargs) -> ParagraphStyle:
        return ParagraphStyle(name, **kwargs)

    s_logo = _style(
        "Logo", fontSize=32, leading=38,
        textColor=c_primary, fontName="Helvetica-Bold", alignment=TA_CENTER,
    )
    s_brand = _style(
        "Brand", fontSize=10, leading=13,
        textColor=c_muted, fontName="Helvetica", alignment=TA_CENTER,
    )
    s_title = _style(
        "Title", fontSize=18, leading=22,
        textColor=c_dark, fontName="Helvetica-Bold",
        alignment=TA_CENTER, spaceAfter=3,
    )
    s_subtitle = _style(
        "Subtitle", fontSize=10, leading=13,
        textColor=c_muted, fontName="Helvetica", alignment=TA_CENTER,
    )
    s_q_num = _style(
        "QNum", fontSize=11, leading=14,
        textColor=c_primary, fontName="Helvetica-Bold",
    )
    s_q_text = _style(
        "QText", fontSize=11, leading=16,
        textColor=c_dark, fontName="Helvetica", spaceAfter=5,
    )
    s_option = _style(
        "Option", fontSize=10, leading=14,
        textColor=c_dark, fontName="Helvetica",
        leftIndent=18, spaceAfter=2,
    )
    s_ans_label = _style(
        "AnsLabel", fontSize=9, leading=12,
        textColor=c_primary, fontName="Helvetica-Bold", spaceBefore=4,
    )
    s_ans_text = _style(
        "AnsText", fontSize=9, leading=13,
        textColor=c_dark, fontName="Helvetica", leftIndent=12,
    )
    s_expl = _style(
        "Expl", fontSize=9, leading=13,
        textColor=c_muted, fontName="Helvetica-Oblique", leftIndent=12,
    )
    s_source = _style(
        "Source", fontSize=8, leading=11,
        textColor=c_muted, fontName="Helvetica", leftIndent=12,
    )
    s_section = _style(
        "Section", fontSize=14, leading=18,
        textColor=c_primary, fontName="Helvetica-Bold",
        spaceBefore=14, spaceAfter=6,
    )

    # ── Document ──────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=quiz_title,
        author="Pritis · AI-Powered Learning",
    )

    story = []

    # ── Header: logo + title ──────────────────────────────────────────────────
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("P", s_logo))
    story.append(Paragraph("Pritis · AI-Powered Learning", s_brand))
    story.append(Spacer(1, 0.4 * cm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_primary))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(_safe_xml(quiz_title), s_title))

    total = len(questions)
    story.append(Paragraph(
        f"{total} Question{'s' if total != 1 else ''}",
        s_subtitle,
    ))
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=c_border))
    story.append(Spacer(1, 0.3 * cm))

    end_answers: List[Dict] = []

    # ── Questions ─────────────────────────────────────────────────────────────
    for idx, q in enumerate(questions, 1):
        q_text     = q.get("question_text", "")
        q_type     = q.get("question_type", "mcq")
        options    = q.get("options") or []
        answer     = q.get("correct_answer", "")
        expl       = q.get("explanation", "")
        source     = q.get("source") or {}
        difficulty = (q.get("difficulty") or "").lower()

        # Resolve display answer (e.g. "B. The mitochondria")
        answer_display = answer
        if q_type == "mcq" and options:
            for i, opt in enumerate(options):
                if opt.strip().lower() == answer.strip().lower():
                    answer_display = f"{_OPTION_LETTERS[i]}. {answer}"
                    break

        # Difficulty badge text
        diff_tag = f"  [{difficulty.upper()}]" if difficulty else ""

        elements = []

        # Question number + text
        elements.append(Paragraph(
            f"Q{idx}.{_safe_xml(diff_tag)}", s_q_num
        ))
        elements.append(Paragraph(_safe_xml(q_text), s_q_text))

        # MCQ options
        if q_type == "mcq" and options:
            for i, opt in enumerate(options[:6]):
                elements.append(Paragraph(
                    f"{_OPTION_LETTERS[i]}.  {_safe_xml(opt)}", s_option
                ))

        # Answer block
        if answer_placement == "after_each":
            elements.append(Spacer(1, 0.15 * cm))
            elements.append(Paragraph("Answer:", s_ans_label))
            elements.append(Paragraph(_safe_xml(answer_display), s_ans_text))
            if expl:
                elements.append(Paragraph(
                    f"Explanation: {_safe_xml(expl)}", s_expl
                ))
            _append_source(elements, source, s_source)
        else:
            end_answers.append({
                "num":         idx,
                "answer":      answer_display,
                "explanation": expl,
                "source":      source,
            })

        elements.append(Spacer(1, 0.3 * cm))
        elements.append(HRFlowable(width="100%", thickness=0.3, color=c_border))
        elements.append(Spacer(1, 0.25 * cm))

        story.append(KeepTogether(elements))

    # ── Answer key (end mode) ─────────────────────────────────────────────────
    if answer_placement == "end" and end_answers:
        story.append(Spacer(1, 0.4 * cm))
        story.append(HRFlowable(width="100%", thickness=1.5, color=c_primary))
        story.append(Paragraph("Answer Key", s_section))

        for item in end_answers:
            ak = []
            ak.append(Paragraph(
                f"Q{item['num']}.  {_safe_xml(item['answer'])}", s_ans_text
            ))
            if item["explanation"]:
                ak.append(Paragraph(
                    f"Explanation: {_safe_xml(item['explanation'])}", s_expl
                ))
            _append_source(ak, item["source"], s_source)
            ak.append(Spacer(1, 0.12 * cm))
            story.append(KeepTogether(ak))

    doc.build(story)
    return buf.getvalue()


def _append_source(elements: list, source: dict, style) -> None:
    """Append a source line (page / chapter) to an elements list if data exists."""
    if not source:
        return
    parts = []
    if source.get("page"):
        parts.append(f"Page {source['page']}")
    if source.get("chapter"):
        parts.append(_safe_xml(source["chapter"]))
    if parts:
        from reportlab.platypus import Paragraph
        elements.append(Paragraph(f"Source: {', '.join(parts)}", style))
