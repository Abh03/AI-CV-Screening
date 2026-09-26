from app.stage0_extraction.pii_masker import mask_contact_lines


def test_contact_line_removes_address_without_entity_recognition():
    value = 'Synthetic Candidate\nprivate@example.invalid | 212-555-0199x12 | Unknowncity, XY\nJava 17, Spring Boot\n'
    assert mask_contact_lines(value) == 'Synthetic Candidate\n[REDACTED_CONTACT]\nJava 17, Spring Boot\n'


def test_work_history_line_preserves_technical_and_location_evidence():
    value = '2019-2024: Backend engineer, Java 17, London office\n'
    assert mask_contact_lines(value) == value
