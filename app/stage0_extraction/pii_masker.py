import re
import spacy

nlp = spacy.load("en_core_web_sm")

TECHNICAL_ENTITIES_WHITELIST: set[str] = {
    "julia", "delphi", "cassandra", "clojure", "solr", "spark", "hadoop",
    "jenkins", "postman", "haskell", "erlang", "elixir", "neo4j", "tableau",
    "spring", "ruby", "rust", "redis", "linux", "darwin", "oracle", "grafana"
}

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")

# Handles Nepal formats (+977-98XXXXXXXX, 98XXXXXXXX, 97XXXXXXXX, 01-XXXXXXX) & international patterns
PHONE_PATTERN = re.compile(
    r"(?:\+?977[-.\s]?)?(?:9[78]\d{8}|0\d{1,2}[-.\s]?\d{6,7})\b|"
    r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"
)

# Nepal administrative regions, districts, and urban division keywords
NEPAL_GEO_TERMS = re.compile(
    r"(?i)\b(?:kathmandu|lalitpur|bhaktapur|pokhara|kavre|kavrepalanchok|chitwan|"
    r"butwal|biratnagar|dharan|nepalgunj|banepa|dhulikhel|ward\s*(?:no\.?)?\s*\d+|"
    r"metro(?:politan)?|sub-metro|municipality|gaupalika|nagarpalika|tole|marga|chowk)\b"
)

# Masks graduation year ONLY when qualified by education keywords (BS, B.E., Degree, Passed, etc.)
GRADUATION_YEAR_PATTERN = re.compile(
    r"(?i)\b(?:graduated|graduation|degree|batch\s+of|completed|class\s+of|bachelor|master|b\.?e\.?|b\.?sc|bca|bit|slc|see|\+2)\b[^\n\.\;]{0,40}\b(19\d{2}|20[01]\d)\b"
)

SECTION_SPLIT_PATTERN = re.compile(
    r"(?i)\n(?=(?:summary|professional\s+summary|experience|work\s+history|education|skills|technical\s+skills)\b)"
)


def extract_header_and_body(text: str) -> tuple[str, str]:
    """
    Splits the CV into header (contact details) and body.
    Finds the first recognized section heading, or defaults to the first 400 characters.
    """
    match = SECTION_SPLIT_PATTERN.search(text)
    if match and match.start() > 0:
        split_idx = match.start()
        return text[:split_idx], text[split_idx:]

    cutoff = min(len(text), 400)
    return text[:cutoff], text[cutoff:]


def mask_header_zone(header_text: str) -> str:
    """
    Applies aggressive scrubbing to the candidate contact header.
    Masks names, contact info, and addresses via localized patterns and spaCy GPE/LOC.
    """
    if not header_text.strip():
        return ""

    # Scrub direct patterns
    masked = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", header_text)
    masked = PHONE_PATTERN.sub("[REDACTED_PHONE]", masked)
    masked = NEPAL_GEO_TERMS.sub("[REDACTED_LOCATION]", masked)

    # Scoped NLP entity pass over header zone only
    doc = nlp(masked)
    replacements: list[tuple[int, int, str]] = []

    for ent in doc.ents:
        if ent.label_ == "PERSON":
            replacements.append((ent.start_char, ent.end_char, "[REDACTED_NAME]"))
        elif ent.label_ in ("GPE", "LOC", "FAC"):
            replacements.append((ent.start_char, ent.end_char, "[REDACTED_LOCATION]"))

    for start, end, label in sorted(replacements, key=lambda x: x[0], reverse=True):
        masked = masked[:start] + label + masked[end:]

    return masked


def mask_body_zone(body_text: str) -> str:
    """
    Masks secondary PII in body while preserving technologies and project locations.
    """
    if not body_text.strip():
        return ""

    masked = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", body_text)
    masked = PHONE_PATTERN.sub("[REDACTED_PHONE]", masked)

    # Context-bound graduation year redaction
    def redact_edu_match(m: re.Match) -> str:
        full_match = m.group(0)
        year_str = m.group(1)
        return full_match.replace(year_str, "[PREVIOUS_ERA_YEAR]")

    masked = GRADUATION_YEAR_PATTERN.sub(redact_edu_match, masked)

    # Mask Person names in body unless whitelisted as a technology
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
    """
    Produces an in-memory redacted string representation of candidate CV text.
    Preserves technical toolings, work tenure years, and body project context.
    """
    if not text:
        return ""

    header, body = extract_header_and_body(text)
    return mask_header_zone(header) + mask_body_zone(body)