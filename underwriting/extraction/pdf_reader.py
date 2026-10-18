"""PDF reader.

Every PDF is classified from its content (application / loss runs / other) in
one model call, whatever its file name:
  application -> also extract the seven submission fields
  loss runs   -> also extract the loss runs values
  other       -> nothing else (makes rule R5 UNKNOWN)
"""
import asyncio                              # pdfplumber is blocking, so it runs in a thread
from pathlib import Path                    # file names and paths
from typing import Literal, Optional        # type hints for the classification schema

import pdfplumber                           # reads text and tables from text-based PDFs
from pydantic import BaseModel, Field       # schemas the model must return

from underwriting.extraction.llm import ask_model
from underwriting.extraction.schema import (BASE_PROMPT, FIELD_NAMES, IntField, LocatedField,
                                            NumberField, SourceResult, SubmissionFields,
                                            TextField, contains_excerpt, located)

# Extra prompt lines that explain how PDF text is laid out
PDF_RULES = """
- The document is a PDF. Pages are marked '--- Page N ---'; report the page where each excerpt appears.
- Tables are shown between [TABLE] and [END TABLE]; cells in a row may be separated by ' | '."""


class LossRunsFields(BaseModel):
    """Values read from a loss runs report (kept for the summary; not used by the rules)."""
    business_name: TextField
    experience_period: TextField = Field(
        description="Period the report covers, as written, e.g. '2023-01-01 through 2025-12-31'.")
    reported_claims: IntField = Field(description="Number of reported claims.")
    # Without this hint the model refused to turn '$0' into a number
    total_incurred_losses: NumberField = Field(
        description="Total incurred losses as a plain number. Dollar amounts ARE numbers: drop the "
                    "currency symbol and thousands separators, e.g. '$0' -> 0, '$12,500' -> 12500.")
    report_status: TextField


# Loss runs field names in order, used to loop over the model's answer
LOSS_RUNS_FIELD_NAMES = list(LossRunsFields.model_fields)


class PdfExtraction(BaseModel):
    """What the model returns for any PDF: its type, plus the values that type carries."""
    # The type decides which values are used and whether this PDF satisfies rule R5
    document_type: Literal["application", "loss_runs", "other"] = Field(
        description="application: a commercial auto application completed for the applicant. "
                    "loss_runs: a loss runs / loss history / claims history report (one stating "
                    "zero claims still counts). other: anything else, or the type can't be determined.")
    # A quote proving the type, checked against the PDF text
    document_type_excerpt: Optional[str] = Field(
        description="Verbatim title or phrase from the document that shows its type. Null if none.")
    # The seven business fields, only from applications
    fields: Optional[SubmissionFields] = Field(
        description="Fill only when document_type is application; otherwise null.")
    loss_runs: Optional[LossRunsFields] = Field(
        description="Fill only when document_type is loss_runs; otherwise null.")


PDF_PROMPT = BASE_PROMPT + PDF_RULES + """
- First classify the document from its content, not its file name; if unclear, use other.
- Only if it is an application, fill fields; otherwise return fields as null.
- Only if it is a loss runs report, fill loss_runs; otherwise return loss_runs as null."""


# ---------------------------------------------------------------------------
# Reading text out of the PDF
# ---------------------------------------------------------------------------

def inside_any_table(obj, table_boxes):
    """True if the object's center is inside one of the table boxes."""
    x = (obj["x0"] + obj["x1"]) / 2         # horizontal center of the character/line
    y = (obj["top"] + obj["bottom"]) / 2    # vertical center
    return any(x0 <= x <= x1 and top <= y <= bottom
               for x0, top, x1, bottom in table_boxes)


def pdf_pages(path):
    """Return one text string per page, with tables kept in reading order."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.find_tables()                  # detect tables on this page
            table_boxes = [t.bbox for t in tables]       # their bounding boxes

            # Text that is NOT inside a table, so table text isn't repeated
            text_page = page.filter(lambda obj: not inside_any_table(obj, table_boxes))

            # (top, text) pairs so text and tables can be sorted top to bottom
            items = [(line["top"], line["text"]) for line in text_page.extract_text_lines()]
            for table in tables:
                rows = []
                for row in table.extract():
                    # Skip empty cells (from inner-table grid lines)
                    cells = [cell.replace("\n", " ") for cell in row if cell]
                    if cells:
                        rows.append(" | ".join(cells))   # one table row per line
                items.append((table.bbox[1], "[TABLE]\n" + "\n".join(rows) + "\n[END TABLE]"))

                # Safety net: text inside the table area that is not in any
                # detected cell (e.g. irregular inner tables) is kept as text
                leftover = page.filter(
                    lambda obj, box=table.bbox, cells=table.cells:
                        inside_any_table(obj, [box]) and not inside_any_table(obj, cells)
                )
                for line in leftover.extract_text_lines():
                    items.append((line["top"], line["text"]))

            items.sort(key=lambda item: item[0])          # reading order: top to bottom
            pages.append("\n".join(text for _, text in items))
    return pages


# ---------------------------------------------------------------------------
# Checking where each quote really is
# ---------------------------------------------------------------------------

def find_page(pages, excerpt, claimed=None):
    """Page number containing the excerpt (claimed page checked first), or None."""
    # Ignore a claimed page number that doesn't exist in this PDF
    claimed = claimed if claimed and 1 <= claimed <= len(pages) else None
    # Try the model's page first, then every other page
    order = ([claimed] if claimed else []) + [n for n in range(1, len(pages) + 1) if n != claimed]
    return next((n for n in order if contains_excerpt(pages[n - 1], excerpt)), None)


def locate_in_pages(field, pages):
    """LocatedField with the page the excerpt is actually on."""
    if not field.excerpt:
        return located(field)                             # nothing to check
    page = find_page(pages, field.excerpt, field.page)
    if page:
        return located(field, f"PDF page {page}", verified=True)
    # Quote not found anywhere: keep the model's page but mark it unverified
    return located(field, f"PDF page {field.page}" if field.page else None)


# ---------------------------------------------------------------------------
# Reader entry point
# ---------------------------------------------------------------------------

async def extract_pdf(path, llm):
    """Read one PDF and return its SourceResult: type, plus fields or loss runs values."""
    path = Path(path)
    result = SourceResult(source_file=path.name, kind="pdf", readable=False)

    # 1. Read the text (pdfplumber is blocking, so read in a thread)
    try:
        pages = await asyncio.to_thread(pdf_pages, path)
    except Exception as e:
        result.error = f"Could not read PDF: {type(e).__name__}: {e}"
        return result
    result.page_count = len(pages)
    if not any(text.strip() for text in pages):
        result.error = "No extractable text"              # scanned / image-only PDF
        return result
    result.readable = True

    # 2. Ask the model, with page markers so it can report pages
    text = "\n\n".join(f"--- Page {n} ---\n{p}" for n, p in enumerate(pages, start=1))
    try:
        extraction = await ask_model(llm, PdfExtraction, PDF_PROMPT, path.name, text)
    except Exception as e:
        # Model failure: report it, never fake values
        result.error = f"Model call failed: {type(e).__name__}: {e}"
        return result

    # 3. Record the type and check the quote that proves it
    result.document_type = extraction.document_type
    excerpt = extraction.document_type_excerpt
    page = find_page(pages, excerpt) if excerpt else None
    result.document_type_evidence = LocatedField(
        value=extraction.document_type, excerpt=excerpt,
        locator=f"PDF page {page}" if page else None, excerpt_verified=page is not None)

    # 4. Keep only the values that belong to this type, each with its real page
    if extraction.document_type == "application" and extraction.fields:
        result.fields = {name: locate_in_pages(getattr(extraction.fields, name), pages)
                         for name in FIELD_NAMES}
    elif extraction.document_type == "loss_runs" and extraction.loss_runs:
        # Kept for the summary; not one of the submission fields
        result.loss_runs_fields = {name: locate_in_pages(getattr(extraction.loss_runs, name), pages)
                                   for name in LOSS_RUNS_FIELD_NAMES}
    return result
