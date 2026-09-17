import re
import spacy

nlp = spacy.load("en_core_web_sm")

TECHNICAL_ENTITIES_WHITELIST: set[str] = {
    "julia", "delphi", "cassandra", "clojure", "solr", "spark", "hadoop",
    "jenkins", "postman", "haskell", "erlang", "elixir", "neo4j", "tableau",
    "spring", "ruby", "rust", "redis", "linux", "darwin", "oracle", "grafana"
}

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")

PHONE_PATTERN = re.compile(
    r"(?:\+?977[-.\s]?)?(?:9[78]\d{8}|0\d{1,2}[-.\s]?\d{6,7})\b|"
    r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"
)

NEPAL_GEO_TERMS = re.compile(
    r"(?i)\b(?:kathmandu|lalitpur|bhaktapur|pokhara|kavre|kavrepalanchok|chitwan|"
    r"butwal|biratnagar|dharan|nepalgunj|banepa|dhulikhel|ward\s*(?:no\.?)?\s*\d+|"
    r"metro(?:politan)?|sub-metro|municipality|gaupalika|nagarpalika|tole|marga|chowk)\b"
)

# Permits periods inside abbreviations while strictly prohibiting cross-line matches
GRADUATION_YEAR_PATTERN = re.compile(
    r"(?i)(?:\b(?:graduated|graduation|degree|batch\s+of|completed|class\s+of|bachelor(?:'s)?|master(?:'s)?|phd|doctorate|bca|bit|slc|see|\+2|b\.?e\b\.?|b\.?sc\b\.?|b\.?tech\b\.?|m\.?tech\b\.?)[^\n;]{0,60}?\b(19\d{2}|20[01]\d)\b)|"
    r"(?:\b(19\d{2}|20[01]\d)\b[^\n;]{0,40}?\b(?:graduated|graduation|degree|batch\s+of|completed|class\s+of|bachelor(?:'s)?|master(?:'s)?|phd|doctorate|bca|bit|slc|see|\+2|b\.?e\b\.?|b\.?sc\b\.?|b\.?tech\b\.?|m\.?tech\b\.?))"
)

SECTION_SPLIT_PATTERN = re.compile(
    r"(?i)\n(?=(?:summary|professional\s+summary|experience|work\s+history|education|skills|technical\s+skills)\b)"
)


def extract_header_and_body(text: str) -> tuple[str, str]:
    match = SECTION_SPLIT_PATTERN.search(text)
    if match and match.start() > 0:
        split_idx = match.start()
        return text[:split_idx], text[split_idx:]

    cutoff = min(len(text), 400)
    return text[:cutoff], text[cutoff:]


def mask_header_zone(header_text: str) -> str:
    """Masks personal identifiers line-by-line to prevent multi-line entity bleed."""
    if not header_text.strip():
        return ""

    masked = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", header_text)
    masked = PHONE_PATTERN.sub("[REDACTED_PHONE]", masked)
    masked = NEPAL_GEO_TERMS.sub("[REDACTED_LOCATION]", masked)

    lines = masked.splitlines(keepends=True)
    redacted_lines = []

    for line in lines:
        doc = nlp(line)
        replacements: list[tuple[int, int, str]] = []
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                replacements.append((ent.start_char, ent.end_char, "[REDACTED_NAME]"))
            elif ent.label_ in ("GPE", "LOC", "FAC"):
                replacements.append((ent.start_char, ent.end_char, "[REDACTED_LOCATION]"))

        line_masked = line
        for start, end, label in sorted(replacements, key=lambda x: x[0], reverse=True):
            line_masked = line_masked[:start] + label + line_masked[end:]
        redacted_lines.append(line_masked)

    return "".join(redacted_lines)


def mask_body_zone(body_text: str) -> str:
    """Masks direct contact details and education years while preserving body technologies."""
    if not body_text.strip():
        return ""

    masked = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", body_text)
    masked = PHONE_PATTERN.sub("[REDACTED_PHONE]", masked)

    def redact_edu_match(m: re.Match) -> str:
        full_match = m.group(0)
        year_match = re.search(r"\b(19\d{2}|20[01]\d)\b", full_match)
        if year_match:
            year_str = year_match.group(1)
            return full_match.replace(year_str, "[PREVIOUS_ERA_YEAR]")
        return full_match

    masked = GRADUATION_YEAR_PATTERN.sub(redact_edu_match, masked)

    doc = nlp(masked)
    name_replacements: list[tuple[int, int, str]] = []

    for ent in doc.ents:
        if ent.label_ == "PERSON":
            tokens = [tok.text.lower() for tok in ent]
            if any(tok in TECHNICAL_ENTITIES_WHITELIST for tok in tokens):
                continue
            name_replacements.append((ent.start_char, ent.end_char, "[REDACTED_NAME]"))

    for start, end, label in sorted(name_replacements, key=lambda x: x[0], reverse=True):
        masked = masked[:start] + label + masked[end:]

    return masked


def mask_pii_runtime_view(text: str) -> str:
    if not text:
        return ""

    header, body = extract_header_and_body(text)
    return mask_header_zone(header) + mask_body_zone(body)