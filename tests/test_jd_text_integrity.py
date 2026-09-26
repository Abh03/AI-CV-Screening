import fitz
import pytest

from app.stage0_extraction.integrity import assess_extraction_integrity, assess_jd_extraction_integrity
from app.stage0_extraction.pipeline import ingest_pdf


DOMAIN_JD = (
    "Associate AI Engineer\nResponsibilities include evaluating conversational assistants,\n"
    "curating datasets, annotating utterances, benchmarking embeddings,\n"
    "fine tuning transformers, orchestrating inference, monitoring hallucinations,\n"
    "and collaborating across multidisciplinary stakeholders.\n"
    "Qualifications include familiarity with vectorization, tokenization,\n"
    "observability, experimentation, multilingual linguistics and analytics."
)


def test_specialized_jd_text_does_not_trigger_ocr(monkeypatch):
    assert not assess_extraction_integrity(DOMAIN_JD, min_density=0.35)["passed"]
    assert assess_jd_extraction_integrity(DOMAIN_JD)["passed"]
    def unexpected_ocr(page):
        pytest.fail("Readable domain terms should not require OCR")
    monkeypatch.setattr("app.stage0_extraction.pipeline._ocr_page", unexpected_ocr)
    with fitz.open() as doc:
        doc.new_page().insert_text((40, 40), DOMAIN_JD)
        result = ingest_pdf(doc.tobytes(), redact=False)
    assert result.status == "success"
    assert not result.pages[0]["ocr_used"]
    assert "tokenization" in result.redacted_text


@pytest.mark.parametrize("text", ["", "x" * 1000, "\ufffd" * 100 + DOMAIN_JD])
def test_unreadable_jd_text_still_requires_ocr(text):
    assert assess_jd_extraction_integrity(text)["requires_ocr"]


def test_unreadable_ocr_still_blocks_jd(monkeypatch):
    monkeypatch.setattr("app.stage0_extraction.pipeline._ocr_page", lambda page: "\ufffd" * 200)
    with fitz.open() as doc:
        doc.new_page()
        result = ingest_pdf(doc.tobytes(), redact=False)
    assert result.code == "EXTRACTION_UNRELIABLE"
