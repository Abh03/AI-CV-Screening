"""Extract overall claims or the union of month-dated employment intervals."""
import re
from datetime import date


def extract_experience(text: str, *, as_of: date | None = None) -> dict | None:
    claims = []
    overall_claim_present = False
    # PDF line wrapping must not interrupt an explicit overall duration.
    for line in re.split(r"\n\s*\n", text):
        line = re.sub(r"\s+", " ", line).strip()
        # Skill-specific durations cannot establish total career experience.
        patterns = (
            r"(?:total|overall)\s+(?:(?:professional|work)\s+)?experience\s*[:=-]?\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b",
            r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:(?:total|overall|professional|work|industry)\s+)+experience\b",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, line, re.I):
                overall_claim_present = True
                # Bounds/ranges are not exact totals; they cannot justify rejection.
                prefix = line[:match.start()]
                suffix = line[match.end():]
                if re.search(r"(?:over|more than|at least|about|approximately|nearly|under|less than|\d\s*[-–])\s*$", prefix, re.I):
                    continue
                if "+" in match.group() or re.match(r"\s*(?:in|with|using|of)\b", suffix, re.I):
                    continue
                claims.append({"years": float(match.group(1)), "excerpt": line.strip()})
    if claims and len({claim["years"] for claim in claims}) == 1:
        return claims[0]
    if overall_claim_present:
        return None  # Conflicting explicit totals require review.
    return dated_work_experience(text, as_of=as_of)


def dated_work_experience(text: str, *, as_of: date | None = None) -> dict | None:
    from app.stage2_retrieval.chunker import parse_cv_sections
    history = parse_cv_sections(text).get("EXPERIENCE", "")
    # Headerless text may mix education and project dates with employment.
    from app.stage2_retrieval.chunker import SECTION_HEADER_PATTERN, normalize_header_to_canonical
    if not any(normalize_header_to_canonical(m.group(1)) == "EXPERIENCE"
               for m in SECTION_HEADER_PATTERN.finditer(text)):
        return None
    months = {name.casefold(): index for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
    endpoint = r"(?:[A-Za-z]+\.?\s+\d{4}|\d{1,2}[/.-]\d{4}|\d{4}[/.-]\d{1,2}|\d{4}|Present|Current|Now)"
    ranges = list(re.finditer(rf"({endpoint})\s*(?:[-–—]|\bto\b)\s*({endpoint})", history, re.I))
    if not ranges:
        return None
    roles = list(re.finditer(r"(?m)^[^\n|]+\|[^\n]+$", history))
    for index, role in enumerate(roles):
        stop = roles[index + 1].start() if index + 1 < len(roles) else len(history)
        if not any(role.start() <= match.start() < stop for match in ranges):
            return None  # An undated role makes the history total incomplete.
    today = as_of or date.today()
    current = today.year * 12 + today.month - 1

    def month_index(value):
        value = value.strip().casefold()
        if value in {"present", "current", "now"}:
            return current
        if re.fullmatch(r"\d{4}", value):
            raise ValueError("Year-only dates cannot establish precise duration")
        if value[0].isalpha():
            name, year = value.split()
            month = months[name[:3]]
            year = int(year)
        else:
            first, second = map(int, re.split(r"[/.-]", value))
            year, month = (first, second) if first >= 1900 else (second, first)
        if not 1 <= month <= 12 or not 1900 <= year <= today.year:
            raise ValueError("Invalid employment date")
        return year * 12 + month - 1

    intervals = []
    try:
        for match in ranges:
            start, end = map(month_index, match.groups())
            if start > end or end > current or match.group(1).casefold() in {"present", "current", "now"}:
                return None
            intervals.append((start, end))
    except (KeyError, ValueError):
        return None
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    total_months = sum(end - start for start, end in merged)
    return {"years": total_months / 12, "months": total_months,
            "years_upper_bound": (total_months + len(merged)) / 12,
            "method": "dated_work_history_union", "as_of": today.isoformat(),
            "excerpt": "\n".join(match.group() for match in ranges),
            "intervals": [{"start_month": start, "end_month": end} for start, end in merged]}
