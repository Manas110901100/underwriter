"""Vehicle schedule reader: derive schedule_vehicle_count from the CSV (no LLM).

Spec: header is vehicle_id,vin,year,make,model. Count nonblank data rows,
header excluded. No VIN decoding, deduplication or validation.
"""
import asyncio                              # file reading runs in a thread
import csv                                  # standard CSV parser (handles quoted commas)
import io                                   # lets csv.reader read from a string
from pathlib import Path                    # file names and paths

from underwriting.extraction.schema import LocatedField, SourceResult

# The header the spec promises; anything else is noted, not rejected
EXPECTED_HEADER = ["vehicle_id", "vin", "year", "make", "model"]


def count_schedule(text):
    """Return (count, locator, note) for CSV text. Raises ValueError if there is no header."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or not any(cell.strip() for cell in rows[0]):
        raise ValueError("CSV has no header row")

    # Compare the header loosely (case and spaces don't matter)
    header = [cell.strip().lower() for cell in rows[0]]
    notes = []
    if header != EXPECTED_HEADER:
        notes.append(f"Header {rows[0]} differs from expected {EXPECTED_HEADER}")

    # Data row numbers start at 1 after the header; blank or comma-only rows are skipped
    data_rows = [(number, row) for number, row in enumerate(rows[1:], start=1)
                 if any(cell.strip() for cell in row)]
    # Rows with a different number of columns are still counted, but noted
    ragged = [number for number, row in data_rows if len(row) != len(rows[0])]
    if ragged:
        notes.append(f"Data rows {ragged} have a different number of columns than the header")

    count = len(data_rows)
    if count:
        first, last = data_rows[0][0], data_rows[-1][0]
        locator = f"CSV data rows {first}-{last} (header excluded)"
        ids = [row[0].strip() for _, row in data_rows if row]          # first column = vehicle_id
        notes.insert(0, f"Counted {count} nonblank data rows (vehicle_id: {', '.join(ids)})")
    else:
        # Zero vehicles is a valid count for this exercise
        locator = "CSV data rows: none (header only)"
        notes.insert(0, "Header present, no nonblank data rows")
    return count, locator, "; ".join(notes)


async def extract_csv(path):
    """Read one CSV file and return its SourceResult with schedule_vehicle_count."""
    path = Path(path)
    result = SourceResult(source_file=path.name, kind="csv", readable=False,
                          document_type="vehicle_schedule")
    try:
        # utf-8-sig drops a byte-order mark if the CSV was saved from Excel
        text = await asyncio.to_thread(path.read_text, encoding="utf-8-sig")
        count, locator, note = count_schedule(text)
    except Exception as e:
        result.error = f"Could not parse CSV: {type(e).__name__}: {e}"
        return result

    result.readable = True
    # A derived value: the "excerpt" is our observation, so it is verified by construction
    result.fields = {"schedule_vehicle_count": LocatedField(
        status="stated", value=count, locator=locator, excerpt_verified=True, note=note)}
    return result
