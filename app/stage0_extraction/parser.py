import fitz  # PyMuPDF
from typing import BinaryIO


def sort_page_blocks(blocks: list[tuple]) -> list[dict]:
    """
    Sorts PyMuPDF blocks into proper reading order.
    Handles full-width headers, two-column body sections, and footers.
    
    PyMuPDF block tuple format:
    (x0, y0, x1, y1, text, block_no, block_type)
    block_type 0 = text, 1 = image
    """
    text_blocks = [
        {
            "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3],
            "text": b[4].strip()
        }
        for b in blocks if b[6] == 0 and b[4].strip()
    ]

    if not text_blocks:
        return []

    # Calculate page horizontal bounds to detect column structures
    min_x = min(b["x0"] for b in text_blocks)
    max_x = max(b["x1"] for b in text_blocks)
    mid_x = (min_x + max_x) / 2.0

    full_width_blocks = []
    left_column_blocks = []
    right_column_blocks = []

    # Partition blocks into structural regions
    for block in text_blocks:
        # Full-width blocks spanning past the center line
        is_full_width = (block["x0"] < mid_x - 40) and (block["x1"] > mid_x + 40)

        if is_full_width:
            full_width_blocks.append(block)
        elif block["x1"] <= mid_x + 40:
            left_column_blocks.append(block)
        else:
            right_column_blocks.append(block)

    # Full-width headings divide column runs; footers follow their body.
    full_width_blocks.sort(key=lambda b: (b["y0"], b["x0"]))
    left_column_blocks.sort(key=lambda b: b["y0"])
    right_column_blocks.sort(key=lambda b: b["y0"])
    result = []
    previous_y = float("-inf")
    for divider in full_width_blocks + [None]:
        next_y = divider["y0"] if divider else float("inf")
        result.extend(b for b in left_column_blocks if previous_y <= b["y0"] < next_y)
        result.extend(b for b in right_column_blocks if previous_y <= b["y0"] < next_y)
        if divider:
            result.append(divider)
        previous_y = next_y
    return result


def extract_pdf_text_layout_aware(pdf_source: str | bytes | BinaryIO) -> list[dict]:
    """
    Extracts text page-by-page preserving spatial column order.
    Returns a list of dictionaries with page metadata and sorted block contents.
    """
    if isinstance(pdf_source, bytes):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    elif isinstance(pdf_source, str):
        doc = fitz.open(pdf_source)
    else:
        doc = fitz.open(stream=pdf_source.read(), filetype="pdf")

    extracted_pages = []

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            # Retrieve bounding-box block tuples
            raw_blocks = page.get_text("blocks")
            ordered_blocks = sort_page_blocks(raw_blocks)
            for block_num, block in enumerate(ordered_blocks):
                block["block_number"] = block_num

            page_text = "\n\n".join(b["text"] for b in ordered_blocks)

            extracted_pages.append({
                "page_number": page_num + 1,
                "text": page_text,
                "blocks": ordered_blocks,
                "block_count": len(ordered_blocks)
            })
    finally:
        doc.close()

    return extracted_pages
