"""Bounded, ephemeral PDF ingestion. Only redacted blocks leave this module."""
from dataclasses import dataclass, field
import subprocess
from threading import BoundedSemaphore

import fitz

from app.config import settings

_ocr_slots = BoundedSemaphore(settings.OCR_CONCURRENCY_LIMIT)
from app.stage0_extraction.integrity import assess_extraction_integrity
from app.stage0_extraction.parser import sort_page_blocks
from app.stage0_extraction.pii_masker import mask_header_zone, mask_body_zone


@dataclass
class PDFIngestionResult:
    status: str
    code: str
    pages: list[dict] = field(default_factory=list)
    redacted_text: str = ""


def _ocr_page(page: fitz.Page) -> str:
    # Scale down large pages before rasterization; never hand an unbounded bitmap to OCR.
    pixels = page.rect.width * page.rect.height
    scale = min(2.0, (settings.PDF_MAX_PAGE_PIXELS / max(pixels, 1)) ** 0.5)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    if pixmap.width * pixmap.height > settings.PDF_MAX_PAGE_PIXELS:
        raise ValueError("OCR_PIXEL_LIMIT")
    with _ocr_slots:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", settings.PDF_OCR_LANGUAGE],
            input=pixmap.tobytes("png"), capture_output=True,
            timeout=settings.PDF_OCR_TIMEOUT_SECONDS, check=False,
        )
    if result.returncode:
        raise RuntimeError("OCR_FAILED")
    return result.stdout.decode("utf-8", errors="replace").strip()


def ingest_pdf(data: bytes) -> PDFIngestionResult:
    if not data.startswith(b"%PDF-"):
        return PDFIngestionResult("failure", "INVALID_PDF")
    if len(data) > settings.PDF_MAX_BYTES:
        return PDFIngestionResult("failure", "PDF_TOO_LARGE")
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except (fitz.FileDataError, RuntimeError, ValueError):
        return PDFIngestionResult("failure", "MALFORMED_PDF")
    try:
        if doc.needs_pass:
            return PDFIngestionResult("failure", "ENCRYPTED_PDF")
        if not doc.page_count or doc.page_count > settings.PDF_MAX_PAGES:
            return PDFIngestionResult("failure", "PDF_PAGE_LIMIT")
        raw_pages = []
        ocr_count = 0
        total_text_chars = 0
        for index in range(doc.page_count):
            try:
                page = doc[index]
                if page.rect.width * page.rect.height > 10_000_000:
                    return PDFIngestionResult("failure", "PDF_PAGE_DIMENSIONS")
                blocks = sort_page_blocks(page.get_text("blocks"))
                if len(blocks) > 2000:
                    return PDFIngestionResult("failure", "PDF_BLOCK_LIMIT")
                raw_text = "\n\n".join(block["text"] for block in blocks)
                if len(raw_text) > 200_000:
                    return PDFIngestionResult("failure", "PDF_TEXT_LIMIT")
                assessment = assess_extraction_integrity(raw_text, min_density=0.35)
                if assessment["requires_ocr"]:
                    if ocr_count >= settings.PDF_OCR_MAX_PAGES:
                        return PDFIngestionResult("review", "OCR_PAGE_LIMIT")
                    ocr_count += 1
                    try:
                        ocr_text = _ocr_page(page)
                    except subprocess.TimeoutExpired:
                        return PDFIngestionResult("review", "OCR_TIMEOUT")
                    except (OSError, RuntimeError, ValueError):
                        return PDFIngestionResult("review", "OCR_FAILED")
                    if len(ocr_text) > 200_000 or not assess_extraction_integrity(ocr_text, min_density=0.35)["passed"]:
                        return PDFIngestionResult("review", "EXTRACTION_UNRELIABLE")
                    blocks = [{"x0": 0, "y0": 0, "x1": page.rect.width,
                               "y1": page.rect.height, "text": ocr_text}]
                    raw_text = ocr_text
                total_text_chars += len(raw_text)
                if total_text_chars > 500_000:
                    return PDFIngestionResult("failure", "PDF_TEXT_LIMIT")
                raw_pages.append((index + 1, blocks, bool(assessment["requires_ocr"])))
            except (fitz.FileDataError, RuntimeError, ValueError):
                return PDFIngestionResult("failure", "MALFORMED_PDF")

        redacted_pages = []
        header = True
        for page_number, blocks, used_ocr in raw_pages:
            redacted_blocks = []
            for block_number, block in enumerate(blocks):
                source = block["text"]
                if header and any(line.strip().upper() in {"SUMMARY", "EXPERIENCE", "WORK EXPERIENCE", "SKILLS", "TECHNICAL SKILLS", "EDUCATION", "PROJECTS"}
                                  for line in source.splitlines()):
                    header = False
                redacted = mask_header_zone(source) if header else mask_body_zone(source)
                if header and page_number == 1 and block_number == 0:
                    first_line, separator, rest = redacted.partition("\n")
                    if ("[REDACTED" not in first_line and
                            1 <= len(first_line.split()) <= 4 and
                            all(part.replace("-", "").isalpha() for part in first_line.split())):
                        redacted = "[REDACTED_NAME]" + separator + rest
                redacted_blocks.append({"block_number": block_number, "text": redacted,
                                        "bbox": [block[k] for k in ("x0", "y0", "x1", "y1")]})
            redacted_pages.append({"page_number": page_number, "ocr_used": used_ocr,
                                   "blocks": redacted_blocks})
        text = "\n\n".join(block["text"] for page in redacted_pages for block in page["blocks"])
        return PDFIngestionResult("success", "OK", redacted_pages, text)
    except Exception:
        # A malformed PDF, rasterizer, or masking failure must not leak input
        # through a framework exception response or ordinary logs.
        return PDFIngestionResult("failure", "PDF_PROCESSING_FAILED")
    finally:
        doc.close()
