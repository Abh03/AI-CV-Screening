import re
from typing import List, Dict, Any

# Alias definitions mapped to Canonical Section Keys
SECTION_ALIASES: Dict[str, List[str]] = {
    "SUMMARY": [
        r"PROFESSIONAL\s+SUMMARY", r"EXECUTIVE\s+SUMMARY", r"SUMMARY\s+OF\s+QUALIFICATIONS",
        r"SUMMARY", r"PROFILE", r"PERSONAL\s+PROFILE", r"ABOUT\s+ME", r"OBJECTIVE",
        r"CAREER\s+OBJECTIVE", r"PROFESSIONAL\s+PROFILE", r"OVERVIEW"
    ],
    "EXPERIENCE": [
        r"WORK\s+EXPERIENCE", r"PROFESSIONAL\s+EXPERIENCE", r"RELEVANT\s+EXPERIENCE",
        r"PRACTICAL\s+EXPERIENCE", r"EMPLOYMENT\s+HISTORY", r"WORK\s+HISTORY",
        r"CAREER\s+HISTORY", r"EMPLOYMENT\s+RECORD", r"WORK\s+BACKGROUND", r"EXPERIENCE"
    ],
    "SKILLS": [
        r"TECHNICAL\s+SKILLS", r"CORE\s+COMPETENCIES", r"KEY\s+COMPETENCIES",
        r"TECHNOLOGIES", r"TECHNICAL\s+EXPERTISE", r"TECH\s+STACK", r"AREAS\s+OF\s+EXPERTISE",
        r"PROFESSIONAL\s+SKILLS", r"HARD\s+SKILLS", r"TOOLS\s*(?:&|AND)\s*TECHNOLOGIES",
        r"CORE\s+STRENGTHS", r"SKILLS"
    ],
    "PROJECTS": [
        r"KEY\s+PROJECTS", r"MAJOR\s+PROJECTS", r"PERSONAL\s+PROJECTS",
        r"ACADEMIC\s+PROJECTS", r"SELECTED\s+PROJECTS", r"PROJECT\s+HISTORY", r"PROJECTS"
    ],
    "EDUCATION": [
        r"ACADEMIC\s+QUALIFICATIONS", r"ACADEMIC\s+BACKGROUND", r"EDUCATIONAL\s+BACKGROUND",
        r"EDUCATION\s*(?:&|AND)\s*TRAINING", r"ACADEMIC\s+PROFILE", r"QUALIFICATIONS",
        r"EDUCATION"
    ],
    "CERTIFICATIONS": [
        r"LICENSES\s*(?:&|AND)\s*CERTIFICATIONS", r"PROFESSIONAL\s+CERTIFICATIONS",
        r"CERTIFICATIONS", r"LICENSES", r"COURSES", r"TRAININGS", r"ACCOMPLISHMENTS"
    ]
}

# Flatten and sort aliases by character length descending to prevent greedy prefix collisions
ALL_HEADER_PATTERNS = [pat for patterns in SECTION_ALIASES.values() for pat in patterns]
ALL_HEADER_PATTERNS.sort(key=len, reverse=True)

COMBINED_HEADER_REGEX = "|".join(ALL_HEADER_PATTERNS)

SECTION_HEADER_PATTERN = re.compile(
    r"(?m)^(?:[#*=\-\s]*)"
    rf"({COMBINED_HEADER_REGEX})"
    r"(?:[:\s\-*=#]*)$",
    re.IGNORECASE
)


def normalize_header_to_canonical(raw_header: str) -> str:
    """
    Maps matched raw header text to its standardized canonical key.
    """
    clean_raw = raw_header.strip().upper()

    for canonical_key, patterns in SECTION_ALIASES.items():
        for pattern in patterns:
            if re.fullmatch(r"(?i)" + pattern, clean_raw):
                return canonical_key

    return clean_raw


def parse_cv_sections(text: str) -> Dict[str, str]:
    """
    Splits redacted CV text into structured section blocks based on header matches.
    Normalizes headers to canonical keys and defaults unclassified leading content to 'GENERAL'.
    """
    if not text:
        return {}

    matches = list(SECTION_HEADER_PATTERN.finditer(text))
    if not matches:
        return {"GENERAL": text.strip()}

    sections: Dict[str, str] = {}

    # Handle pre-header leading text
    if matches[0].start() > 0:
        leading_text = text[:matches[0].start()].strip()
        if leading_text:
            sections["GENERAL"] = leading_text

    for i, match in enumerate(matches):
        raw_header = match.group(1)
        canonical_name = normalize_header_to_canonical(raw_header)

        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        content = text[start_idx:end_idx].strip()
        if content:
            if canonical_name in sections:
                sections[canonical_name] += f"\n\n{content}"
            else:
                sections[canonical_name] = content

    return sections


def chunk_section_content(
    section_name: str,
    content: str,
    max_chunk_chars: int = 400,
    overlap_chars: int = 50
) -> List[Dict[str, Any]]:
    """
    Splits section content into semantic sliding window chunks with context headers.
    """
    if not content:
        return []

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    chunks = []
    current_chunk = f"[Section: {section_name}]"
    chunk_index = 0

    for line in lines:
        if len(current_chunk) + len(line) + 1 <= max_chunk_chars:
            current_chunk += f" {line}"
        else:
            chunks.append({
                "section": section_name,
                "chunk_index": chunk_index,
                "text": current_chunk.strip(),
                "char_length": len(current_chunk.strip())
            })
            chunk_index += 1

            overlap_text = current_chunk[-overlap_chars:] if len(current_chunk) > overlap_chars else ""
            current_chunk = f"[Section: {section_name}] {overlap_text} {line}".strip()

    if len(current_chunk) > len(f"[Section: {section_name}]"):
        chunks.append({
            "section": section_name,
            "chunk_index": chunk_index,
            "text": current_chunk.strip(),
            "char_length": len(current_chunk.strip())
        })

    return chunks


def generate_cv_chunks(
    redacted_cv_text: str,
    max_chunk_chars: int = 400,
    overlap_chars: int = 50
) -> List[Dict[str, Any]]:
    """
    Main entry point: parses CV into sections and produces context-aware chunks.
    """
    sections = parse_cv_sections(redacted_cv_text)
    all_chunks = []
    global_index = 0

    for section_name, content in sections.items():
        section_chunks = chunk_section_content(
            section_name=section_name,
            content=content,
            max_chunk_chars=max_chunk_chars,
            overlap_chars=overlap_chars
        )
        for chunk in section_chunks:
            chunk["global_chunk_id"] = global_index
            all_chunks.append(chunk)
            global_index += 1

    return all_chunks