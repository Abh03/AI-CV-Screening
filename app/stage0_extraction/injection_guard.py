import re

# Targeted imperative override patterns (avoids blocking ML engineers discussing AI systems)
INJECTION_OVERRIDE_PATTERNS = [
    re.compile(r"(?i)\b(?:ignore|disregard|forget|bypass)\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|directions|rules)\b"),
    re.compile(r"(?i)\b(?:system\s+override|new\s+system\s+prompt|you\s+are\s+now\s+(?:an?\s+)?(?:evaluator|admin|hiring\s+manager|system))\b"),
    re.compile(r"(?i)\b(?:rate|score|give)\s+(?:this\s+candidate|me|them)\s+(?:a\s+)?(?:100|10/10|top\s+score|perfect\s+score)\b"),
    re.compile(r"(?i)\b(?:output|respond\s+with)\s+(?:only\s+)?\{\s*\"category_scores\""),
]

# Structural delimiter collision patterns
DELIMITER_INJECTION_PATTERN = re.compile(
    r"(?i)<\s*/?\s*(?:candidate_data|candidate_evidence|evaluation_request|snippet|category|job_description|system_prompt|system|instruction|context)(?:\s[^<>]*)?\s*>"
)

# Invisible / zero-width characters used to hide injection payloads
ZERO_WIDTH_CHARS = set("\u200B\u200C\u200D\uFEFF")


def scan_for_injection_anomalies(text: str, max_zero_width_threshold: int = 5) -> dict:
    """
    Scans CV text for adversarial prompt-injection heuristics and structural anomalies.
    Returns anomaly status and specific detection reasons.
    """
    if not text:
        return {"is_flagged": False, "detected_patterns": []}

    detected_patterns: list[str] = []

    # 1. Check for imperative instruction overrides
    for pattern in INJECTION_OVERRIDE_PATTERNS:
        if pattern.search(text):
            detected_patterns.append(f"Prompt override heuristic matched: {pattern.pattern}")

    # 2. Check for XML tag escaping attacks
    if DELIMITER_INJECTION_PATTERN.search(text):
        detected_patterns.append("Delimiter forgery: candidate text contains reserved structural XML tags")

    # 3. Check for hidden zero-width character flooding
    zero_width_count = sum(1 for char in text if char in ZERO_WIDTH_CHARS)
    if zero_width_count > max_zero_width_threshold:
        detected_patterns.append(
            f"Invisible character anomaly: found {zero_width_count} zero-width characters (threshold: {max_zero_width_threshold})"
        )

    return {
        "is_flagged": len(detected_patterns) > 0,
        "detected_patterns": detected_patterns
    }


def encapsulate_candidate_data(redacted_text: str) -> str:
    """
    Escapes internal XML delimiter collisions and encapsulates CV text
    within strict structural boundaries.
    """
    if not redacted_text:
        return "<candidate_data>\n</candidate_data>"

    from xml.sax.saxutils import escape
    return f"<candidate_data>\n{escape(redacted_text.strip())}\n</candidate_data>"
