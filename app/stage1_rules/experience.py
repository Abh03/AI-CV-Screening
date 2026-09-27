"""Extract explicit overall experience claims; never sum overlapping job durations."""
import re


def extract_experience(text: str) -> dict | None:
    claims = []
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
    return None
