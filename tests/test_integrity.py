from app.stage0_extraction.integrity import (
    calculate_shannon_entropy,
    calculate_dictionary_density,
    assess_extraction_integrity
)


def test_clean_technical_resume_text():
    clean_text = (
        "Senior Software Engineer with 6 years of experience designing and deploying "
        "microservices using Java, Spring Boot, and PostgreSQL on AWS and Kubernetes. "
        "Built scalable data pipelines and maintained production systems with high availability."
    )
    result = assess_extraction_integrity(clean_text, min_density=0.60)

    assert result["passed"] is True
    assert result["requires_ocr"] is False
    assert 3.5 <= result["shannon_entropy"] <= 5.0
    assert result["dictionary_density"] >= 0.60


def test_corrupted_font_substitution_text():
    # Synthetic Canva/Photoshop font corruption: repetitive glyphs & non-dictionary noise
    corrupted_text = "â‚¬â‚¬â‚¬â‚¬â‚¬ Ã¿Ã¿Ã¿Ã¿Ã¿ xqztplm vbnmzxc qwrtyp â€œâ€œâ€œ 1293810238"
    result = assess_extraction_integrity(corrupted_text)

    assert result["passed"] is False
    assert result["requires_ocr"] is True
    assert len(result["failure_reasons"]) > 0


def test_empty_string_handling():
    result = assess_extraction_integrity("")
    assert result["passed"] is False
    assert result["requires_ocr"] is True