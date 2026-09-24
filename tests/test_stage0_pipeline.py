import fitz
import pytest
import subprocess
from cryptography.fernet import Fernet

from app.config import settings
from app.core.security import encrypt_payload, decrypt_payload, validate_encryption_configuration
from app.stage0_extraction import pipeline
from app.stage0_extraction.parser import sort_page_blocks
from app.stage2_retrieval.chunker import generate_cv_chunks
from app.stage3_evaluation.evidence import build_evidence_registry


def make_pdf(*, scan=False, pages=1):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        if not scan:
            page.insert_text((40, 40), "Jane Doe")
            page.insert_text((40, 80), "EXPERIENCE")
            page.insert_text((40, 110), "software engineer with experience in python and java systems")
    result = doc.tobytes()
    doc.close()
    return result


def test_pdf_success_redaction_and_provenance(monkeypatch):
    monkeypatch.setattr(pipeline, "assess_extraction_integrity", lambda text, **kwargs: {"requires_ocr": False, "passed": True})
    result = pipeline.ingest_pdf(make_pdf())
    assert result.status == "success"
    assert result.pages[0]["blocks"][0]["block_number"] == 0
    assert "Jane Doe" not in result.redacted_text
    chunks = generate_cv_chunks(result.redacted_text, result.pages)
    assert chunks
    assert chunks[0]["source_location"]["page_number"] == 1
    assert "bbox" in chunks[0]["source_location"]
    chunk = dict(chunks[0], candidate_id="candidate", category="EXPERIENCE")
    registry = build_evidence_registry("candidate", {"candidate_id": "candidate",
                      "evidence_by_category": {"EXPERIENCE": [chunk]}})
    assert registry["EXPERIENCE:1"].source_location.page_number == 1
    assert registry["EXPERIENCE:1"].source_location.block_index is not None


def test_scan_ocr_only_when_required(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "_ocr_page", lambda page: seen.append(page.number) or "software engineer with experience in python and java systems")
    monkeypatch.setattr(pipeline, "assess_extraction_integrity", lambda text, **kwargs: {"requires_ocr": not bool(text), "passed": bool(text)})
    result = pipeline.ingest_pdf(make_pdf(scan=True))
    assert result.status == "success"
    assert result.pages[0]["ocr_used"] is True
    assert seen == [0]


def test_pdf_failure_and_review_states(monkeypatch):
    assert pipeline.ingest_pdf(b"bad").code == "INVALID_PDF"
    assert pipeline.ingest_pdf(b"%PDF-1.7\nbad").code == "MALFORMED_PDF"
    monkeypatch.setattr(settings, "PDF_MAX_BYTES", 5)
    assert pipeline.ingest_pdf(make_pdf()).code == "PDF_TOO_LARGE"
    monkeypatch.setattr(settings, "PDF_MAX_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(settings, "PDF_MAX_PAGES", 1)
    assert pipeline.ingest_pdf(make_pdf(pages=2)).code == "PDF_PAGE_LIMIT"
    monkeypatch.setattr(settings, "PDF_MAX_PAGES", 20)
    monkeypatch.setattr(pipeline, "_ocr_page", lambda page: (_ for _ in ()).throw(OSError()))
    assert pipeline.ingest_pdf(make_pdf(scan=True)).code == "OCR_FAILED"
    monkeypatch.setattr(pipeline, "_ocr_page", lambda page: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("tesseract", 1)))
    assert pipeline.ingest_pdf(make_pdf(scan=True)).code == "OCR_TIMEOUT"


def test_encrypted_pdf_rejected():
    doc = fitz.open()
    doc.new_page()
    encrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()
    assert pipeline.ingest_pdf(encrypted).code == "ENCRYPTED_PDF"


def test_full_width_intermediate_heading_and_footer_order():
    blocks = [(40, 10, 560, 30, "HEADER", 0, 0), (40, 50, 250, 70, "LEFT ONE", 1, 0),
              (320, 50, 550, 70, "RIGHT ONE", 2, 0), (40, 100, 560, 120, "SECTION", 3, 0),
              (40, 140, 250, 160, "LEFT TWO", 4, 0), (320, 140, 550, 160, "RIGHT TWO", 5, 0),
              (40, 750, 560, 770, "FOOTER", 6, 0)]
    assert [b["text"] for b in sort_page_blocks(blocks)] == [
        "HEADER", "LEFT ONE", "RIGHT ONE", "SECTION", "LEFT TWO", "RIGHT TWO", "FOOTER"]


def test_encryption_requires_real_fernet_key(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", None)
    with pytest.raises(ValueError, match="ENCRYPTION_SECRET_KEY"):
        validate_encryption_configuration()
    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", "bad")
    with pytest.raises(ValueError, match="Fernet"):
        encrypt_payload("private")
    monkeypatch.setattr(settings, "ENCRYPTION_SECRET_KEY", Fernet.generate_key().decode())
    assert decrypt_payload(encrypt_payload("private")) == "private"
