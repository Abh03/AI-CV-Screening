"""Readable, point-in-time PDF export for a single CV assessment."""

import re
import unicodedata

import pymupdf

from app.stage3_evaluation.scoring import CATEGORY_WEIGHTS


def _pdf_text(value):
    # Base PDF fonts cover ASCII. Transliterate unsupported glyphs instead of
    # leaving blank squares in recruiter-facing reports.
    value = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"\s+", " ", value.encode("ascii", "replace").decode("ascii")).strip()


def render_single_cv_report(row: dict) -> bytes:
    doc = pymupdf.open()
    page = None
    y = 0.0
    width, height = pymupdf.paper_size("a4")
    margin = 46

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=width, height=height)
        y = margin

    def line(value="", *, size=10, bold=False, indent=0, gap=0):
        nonlocal y
        text = _pdf_text(value)
        if not text:
            y += gap or 8
            return
        font = "hebo" if bold else "helv"
        max_width = width - 2 * margin - indent
        parts = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}" if current else word
            if pymupdf.get_text_length(candidate, fontname=font, fontsize=size) <= max_width:
                current = candidate
                continue
            if current:
                parts.append(current)
                current = ""
            for char in word:
                if pymupdf.get_text_length(current + char, fontname=font, fontsize=size) > max_width:
                    parts.append(current)
                    current = ""
                current += char
        if current:
            parts.append(current)
        for part in parts:
            if y + size * 1.5 > height - margin:
                new_page()
            page.insert_text((margin + indent, y), part, fontsize=size, fontname=font,
                             color=(0.08, 0.2, 0.24))
            y += size * 1.45
        y += gap

    def section(title):
        nonlocal y
        if y > height - margin - 65:
            new_page()
        y += 13
        line(title, size=13, bold=True, gap=6)

    def label(value):
        return str(value or "").replace("_", " ").title()

    new_page()
    line("Single CV analysis", size=19, bold=True, gap=8)
    line(f"CV: {row.get('source_filename', '')}", bold=True)
    line(f"Job: {row.get('role_title', '')}")
    line(f"Outcome: {label(row.get('status'))}")
    if row.get("score") is not None:
        line(f"Overall fit score: {row['score']:.1f} / 100", size=14, bold=True, gap=6)
        line("Overall score is calculated from the four category scores: " + ", ".join(
            f"{name.title()} {int(weight * 100)}%" for name, weight in CATEGORY_WEIGHTS.items()) + ".")
        line("The category explanations below are from the detailed assessment. The overall score is calculated by the scoring policy.")
    if row.get("summary"):
        section("Assessment summary")
        line(row["summary"])
    if row.get("failure_code") or row.get("error_message"):
        section("Why this analysis stopped")
        line(row.get("failure_code") or row.get("error_message"))
    checks = [c for c in row.get("stage1_checks", []) if c.get("status") in {"FAIL", "REVIEW"}]
    if checks:
        section("Eligibility findings")
        for check in checks:
            line(f"{label(check.get('rule'))} - {label(check.get('status'))}: {check.get('message', '')}")
    if row.get("flags") or row.get("review_reasons"):
        section("Assessment review findings")
        for flag in row.get("flags", []):
            line(f"{label(flag.get('severity'))} {label(flag.get('type'))}: {flag.get('description', '')}")
        if not row.get("flags"):
            for reason in row.get("review_reasons", []):
                line(label(reason))
    if row.get("score") is not None:
        section("Category scores and reasons")
        for name in CATEGORY_WEIGHTS:
            assessment = (row.get("assessments") or {}).get(name) or {}
            category_score = row.get("category_scores", {}).get(name)
            line(f"{name.title()}: {category_score:.1f} / 100" if category_score is not None else name.title(), bold=True, gap=3)
            line(assessment.get("rationale") or "No category explanation was stored.", indent=10, gap=5)
            for claim in assessment.get("claims") or []:
                line(f"Evidence: {claim.get('claim', '')} - {claim.get('quote', '')}", size=9, indent=10, gap=3)
    if row.get("improvement_suggestions"):
        section("How the CV could document a stronger match")
        for item in row["improvement_suggestions"]:
            line(f"- {item}")
    section("Stage trace")
    for stage in row.get("stage_history", []):
        line(f"{stage.get('stage', '')}: {label(stage.get('state'))}", bold=True)
        if stage.get("reason"):
            line(stage["reason"], indent=10, gap=3)
    if row.get("stage2_score") is not None:
        section("Job requirement matching")
        line(f"Supported JD relevance coverage: {row['stage2_score'] * 100:.1f}% (retrieval coverage, not the final fit score)")
        for target in row.get("stage2_target_assessments", []):
            line(f"{target.get('target_text') or target.get('target_id')}: {label(target.get('status'))}", bold=True)
            line(target.get("supporting_text") or "No supporting passage found", indent=10, gap=3)
    for number, report_page in enumerate(doc, 1):
        report_page.insert_text((margin, height - 24), f"Single CV analysis  |  Page {number} of {len(doc)}",
                                fontsize=8, color=(0.35, 0.4, 0.44))
    result = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return result
