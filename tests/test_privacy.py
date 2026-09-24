from app.stage0_extraction.pii_masker import mask_pii_runtime_view


def test_masks_contact_and_education_year_preserves_screening_evidence():
    text = ("Jane Doe\njane.doe@example.com\n+977-9812345678\nKathmandu\n"
            "SKILLS\nPython Redis PostgreSQL\n"
            "EXPERIENCE\nBuilt Python APIs from 2020 to 2024.\n"
            "EDUCATION\nBachelor of Science 2018\n")
    masked = mask_pii_runtime_view(text)
    assert "jane.doe@example.com" not in masked
    assert "9812345678" not in masked
    assert "Kathmandu" not in masked
    assert "2018" not in masked
    assert "[PREVIOUS_ERA_YEAR]" in masked
    assert all(term in masked for term in ("Python", "Redis", "PostgreSQL", "2020", "2024"))


def test_body_contact_details_are_redacted():
    masked = mask_pii_runtime_view("Alex Doe\nEXPERIENCE\nContact alex@example.com or 9812345678")
    assert "alex@example.com" not in masked
    assert "9812345678" not in masked
