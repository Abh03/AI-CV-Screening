import fitz
from app.stage0_extraction.parser import extract_pdf_text_layout_aware


def generate_mock_two_column_pdf() -> bytes:
    """Generates an in-memory two-column PDF document for extraction testing."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)

    # Header spanning across the top
    page.insert_textbox(
        fitz.Rect(50, 40, 550, 90),
        "Jane Doe\nPrincipal Software Engineer",
        fontsize=14
    )

    # Left Column (Core Skills)
    page.insert_textbox(
        fitz.Rect(50, 110, 280, 400),
        "TECHNICAL SKILLS:\n• Java 17\n• Spring Boot\n• PostgreSQL",
        fontsize=10
    )

    # Right Column (Work Experience)
    page.insert_textbox(
        fitz.Rect(320, 110, 550, 400),
        "WORK EXPERIENCE:\nSenior Backend Developer at CloudCorp.\nLed microservices migration.",
        fontsize=10
    )

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_layout_aware_extraction_reading_order():
    pdf_bytes = generate_mock_two_column_pdf()
    pages = extract_pdf_text_layout_aware(pdf_bytes)

    assert len(pages) == 1
    extracted_text = pages[0]["text"]

    # Assert that all left column content appears BEFORE right column content
    skills_index = extracted_text.find("PostgreSQL")
    exp_index = extracted_text.find("Senior Backend Developer")

    assert skills_index != -1
    assert exp_index != -1
    assert skills_index < exp_index, "Left column text must precede right column text"