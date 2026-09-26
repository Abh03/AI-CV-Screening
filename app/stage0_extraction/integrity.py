import math
import re
from collections import Counter

# Core vocabulary of standard English terms and common technical keywords
COMMON_VOCABULARY: set[str] = {
    # Common English words
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for",
    "not", "on", "with", "he", "as", "you", "do", "at", "this", "but", "his",
    "by", "from", "they", "we", "say", "her", "she", "or", "an", "will", "my",
    "one", "all", "would", "there", "their", "what", "so", "up", "out", "if",
    "about", "who", "get", "which", "go", "me", "when", "make", "can", "like",
    "time", "no", "just", "him", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "see", "other", "than", "then", "now", "look",
    "only", "come", "its", "over", "think", "also", "back", "after", "use", "two",
    "how", "our", "work", "first", "well", "way", "even", "new", "want", "because",
    "any", "these", "give", "day", "most", "us", "lead", "manage", "team", "engineer",
    "developer", "software", "system", "systems", "experience", "years", "education",
    "university", "bachelor", "master", "science", "technology", "project", "projects",
    "skills", "technical", "worked", "built", "implemented", "designed", "maintained",
    "deployed", "production", "cloud", "database", "data", "pipeline", "service",
    # Technical track terms & tools
    "java", "spring", "boot", "python", "fastapi", "django", "postgres", "postgresql",
    "sql", "nosql", "redis", "docker", "kubernetes", "aws", "azure", "gcp", "kafka",
    "rest", "api", "apis", "git", "ci", "cd", "linux", "backend", "frontend", "fullstack",
    "microservices", "unit", "testing", "agile", "scrum", "architecture", "scale",
    "distributed", "performance", "optimization", "security", "dotnet", "csharp"
}


def calculate_shannon_entropy(text: str) -> float:
    """
    Computes character-level Shannon entropy in bits per character.
    H(X) = -sum(p(x) * log2(p(x)))
    Standard natural language English text falls between 3.5 and 5.0.
    """
    if not text:
        return 0.0

    counts = Counter(text)
    total_chars = len(text)
    entropy = 0.0

    for count in counts.values():
        p_x = count / total_chars
        entropy -= p_x * math.log2(p_x)

    return round(entropy, 2)


def calculate_dictionary_density(text: str, custom_vocab: set[str] | None = None) -> float:
    """
    Computes the proportion of recognized dictionary words in the extracted text.
    Returns a float between 0.0 and 1.0.
    """
    vocab = custom_vocab or COMMON_VOCABULARY
    # Extract alphanumeric words normalized to lowercase
    tokens = re.findall(r"\b[a-zA-Z]{2,}\b", text.lower())

    if not tokens:
        return 0.0

    recognized = sum(1 for token in tokens if token in vocab)
    return round(recognized / len(tokens), 2)


def assess_extraction_integrity(
    text: str,
    min_density: float = 0.70,
    min_entropy: float = 3.5,
    max_entropy: float = 5.0
) -> dict:
    """
    Evaluates extracted text against entropy and dictionary density gates.
    Flags document for OCR fallback if either gate fails.
    """
    entropy = calculate_shannon_entropy(text)
    density = calculate_dictionary_density(text)

    passed_entropy = min_entropy <= entropy <= max_entropy
    passed_density = density >= min_density

    requires_ocr = not (passed_entropy and passed_density)

    failure_reasons = []
    if not passed_entropy:
        failure_reasons.append(f"Entropy {entropy} outside [{min_entropy}, {max_entropy}]")
    if not passed_density:
        failure_reasons.append(f"Dictionary density {density} below threshold {min_density}")

    return {
        "passed": not requires_ocr,
        "requires_ocr": requires_ocr,
        "shannon_entropy": entropy,
        "dictionary_density": density,
        "failure_reasons": failure_reasons
    }


def assess_jd_extraction_integrity(text: str) -> dict:
    """Check text quality without treating domain vocabulary as corruption.

    JDs routinely contain terms absent from the small CV vocabulary. Keep the
    entropy gate and require usable words with few decoding/control artifacts.
    This is an extraction check; recruiters still review the JD's meaning.
    """
    assessment = assess_extraction_integrity(text, min_density=0)
    words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
    artifacts = sum(char == "\ufffd" or (not char.isprintable() and not char.isspace()
                    and char not in {"\u200b", "\u200c", "\u200d", "\ufeff"}) for char in text)
    usable = (len(words) >= 5 and sum(len(word) for word in words) >= 30
              and artifacts / max(len(text), 1) <= 0.01)
    if not usable:
        assessment["failure_reasons"].append("Insufficient readable text or excessive decoding artifacts")
    assessment["passed"] = assessment["passed"] and usable
    assessment["requires_ocr"] = not assessment["passed"]
    return assessment
