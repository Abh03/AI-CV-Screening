import re
from typing import List, Dict, Any

# Map raw section headers to standard category buckets
CATEGORY_MAP: Dict[str, str] = {
    "SUMMARY": "EXPERIENCE",
    "EXPERIENCE": "EXPERIENCE",
    "SKILLS": "SKILLS",
    "PROJECTS": "PROJECTS",
    "EDUCATION": "EDUCATION",
    "CERTIFICATIONS": "EDUCATION"
}

SECTION_ALIASES: Dict[str, List[str]] = {
    "SUMMARY": [
        r"PROFESSIONAL\s+SUMMARY", r"EXECUTIVE\s+SUMMARY", r"SUMMARY\s+OF\s+QUALIFICATIONS",
        r"SUMMARY", r"PROFILE", r"PERSONAL\s+PROFILE", r"ABOUT\s+ME", r"OBJECTIVE", r"OVERVIEW"
    ],
    "EXPERIENCE": [
        r"WORK\s+EXPERIENCE", r"PROFESSIONAL\s+EXPERIENCE", r"RELEVANT\s+EXPERIENCE",
        r"EMPLOYMENT\s+HISTORY", r"WORK\s+HISTORY", r"CAREER\s+HISTORY", r"EXPERIENCE"
    ],
    "SKILLS": [
        r"TECHNICAL\s+SKILLS", r"CORE\s+COMPETENCIES", r"KEY\s+COMPETENCIES",
        r"TECHNOLOGIES", r"TECHNICAL\s+EXPERTISE", r"TECH\s+STACK", r"SKILLS"
    ],
    "PROJECTS": [
        r"KEY\s+PROJECTS", r"MAJOR\s+PROJECTS", r"PERSONAL\s+PROJECTS",
        r"ACADEMIC\s+PROJECTS", r"PROJECTS"
    ],
    "EDUCATION": [
        r"ACADEMIC\s+QUALIFICATIONS", r"ACADEMIC\s+BACKGROUND", r"EDUCATIONAL\s+BACKGROUND",
        r"QUALIFICATIONS", r"EDUCATION"
    ],
    "CERTIFICATIONS": [
        r"LICENSES\s*(?:&|AND)\s*CERTIFICATIONS", r"PROFESSIONAL\s+CERTIFICATIONS",
        r"CERTIFICATIONS", r"LICENSES", r"COURSES"
    ]
}

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
    clean_raw = raw_header.strip().upper()
    for canonical_key, patterns in SECTION_ALIASES.items():
        for pattern in patterns:
            if re.fullmatch(r"(?i)" + pattern, clean_raw):
                return canonical_key
    return "EXPERIENCE"


def parse_cv_sections(text: str) -> Dict[str, str]:
    if not text:
        return {}

    matches = list(SECTION_HEADER_PATTERN.finditer(text))
    if not matches:
        return {"EXPERIENCE": text.strip()}

    sections: Dict[str, str] = {}
    if matches[0].start() > 0:
        leading_text = text[:matches[0].start()].strip()
        if leading_text:
            sections["SUMMARY"] = leading_text

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


def split_text_into_structural_blocks(text: str) -> List[str]:
    """
    Splits text on bullet points, numbered lists, or double line breaks.
    """
    raw_blocks = re.split(r"(?:\n\s*[-•*]\s*|\n\n+|\n(?=\d+\.\s+))", text)
    cleaned_blocks = [b.strip() for b in raw_blocks if b and len(b.strip()) > 5]
    return cleaned_blocks


def chunk_section_structurally(
    section_name: str,
    content: str,
    min_chunk_chars: int = 150,
    max_chunk_chars: int = 600
) -> List[Dict[str, Any]]:
    category = CATEGORY_MAP.get(section_name, "EXPERIENCE")
    raw_blocks = split_text_into_structural_blocks(content)
    if not raw_blocks:
        return []

    chunks = []
    accumulated_text = ""
    chunk_idx = 0

    for block in raw_blocks:
        if not accumulated_text:
            accumulated_text = block
        elif len(accumulated_text) + len(block) + 1 <= min_chunk_chars:
            accumulated_text += f" {block}"
        elif len(accumulated_text) + len(block) + 1 <= max_chunk_chars:
            accumulated_text += f" {block}"
            chunks.append({
                "section": section_name,
                "category": category,
                "chunk_index": chunk_idx,
                "text": f"[Section: {section_name}] {accumulated_text.strip()}",
                "char_length": len(accumulated_text.strip())
            })
            chunk_idx += 1
            accumulated_text = ""
        else:
            chunks.append({
                "section": section_name,
                "category": category,
                "chunk_index": chunk_idx,
                "text": f"[Section: {section_name}] {accumulated_text.strip()}",
                "char_length": len(accumulated_text.strip())
            })
            chunk_idx += 1
            accumulated_text = block

    if accumulated_text:
        # Sentence-fallback split if final remaining block is oversized
        if len(accumulated_text) > max_chunk_chars:
            sentences = re.split(r"(?<=[.!?])\s+", accumulated_text)
            sub_chunk = ""
            for sent in sentences:
                if len(sub_chunk) + len(sent) + 1 <= max_chunk_chars:
                    sub_chunk += f" {sent}"
                else:
                    if sub_chunk:
                        chunks.append({
                            "section": section_name,
                            "category": category,
                            "chunk_index": chunk_idx,
                            "text": f"[Section: {section_name}] {sub_chunk.strip()}",
                            "char_length": len(sub_chunk.strip())
                        })
                        chunk_idx += 1
                    sub_chunk = sent
            if sub_chunk:
                chunks.append({
                    "section": section_name,
                    "category": category,
                    "chunk_index": chunk_idx,
                    "text": f"[Section: {section_name}] {sub_chunk.strip()}",
                    "char_length": len(sub_chunk.strip())
                })
        else:
            chunks.append({
                "section": section_name,
                "category": category,
                "chunk_index": chunk_idx,
                "text": f"[Section: {section_name}] {accumulated_text.strip()}",
                "char_length": len(accumulated_text.strip())
            })

    return chunks


def generate_cv_chunks(redacted_cv_text: str) -> List[Dict[str, Any]]:
    sections = parse_cv_sections(redacted_cv_text)
    all_chunks = []
    global_id = 0

    for section_name, content in sections.items():
        sec_chunks = chunk_section_structurally(section_name, content)
        for chunk in sec_chunks:
            chunk["global_chunk_id"] = global_id
            chunk["chunk_id"] = f"chk_{global_id}"
            all_chunks.append(chunk)
            global_id += 1

    return all_chunks