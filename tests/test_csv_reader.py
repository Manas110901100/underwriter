"""schedule_vehicle_count tests; no LLM calls."""
import asyncio

import pytest

from underwriting.extraction.csv_reader import count_schedule, extract_csv

HEADER = "vehicle_id,vin,year,make,model\n"


def test_counts_data_rows_excluding_header():
    """Three data rows are counted, the header is not, and the locator gives the row range."""
    count, locator, _ = count_schedule(HEADER + "A,1,2022,M,V\nB,2,2023,M,V\nC,3,2024,M,V\n")
    assert count == 3
    assert locator == "CSV data rows 1-3 (header excluded)"


def test_blank_and_comma_only_rows_are_skipped():
    """Empty lines and rows of only commas are not vehicles."""
    count, _, _ = count_schedule(HEADER + "A,1,2022,M,V\n\n,,,,\nB,2,2023,M,V\n")
    assert count == 2


def test_header_only_is_zero():
    """A CSV with only the header is a valid count of 0."""
    assert count_schedule(HEADER)[0] == 0


def test_duplicates_are_not_removed():
    """The spec says no deduplication: identical rows are both counted."""
    assert count_schedule(HEADER + "A,1,2022,M,V\nA,1,2022,M,V\n")[0] == 2


def test_unexpected_header_is_noted():
    """A different header still counts the rows but explains the difference in the note."""
    count, _, note = count_schedule("id,vin\nA,1\n")
    assert count == 1
    assert "differs from expected" in note


def test_empty_file_raises():
    """An empty file has no header, so it can't be parsed."""
    with pytest.raises(ValueError):
        count_schedule("")


def test_extract_csv_reads_file_with_bom(tmp_path):
    """A CSV saved by Excel (with a byte-order mark) is read correctly."""
    path = tmp_path / "vehicles.csv"
    path.write_text(HEADER + "A,1,2022,M,V\n", encoding="utf-8-sig")
    result = asyncio.run(extract_csv(path))
    assert result.readable and result.fields["schedule_vehicle_count"].value == 1


def test_extract_csv_unparseable_reports_error(tmp_path):
    """An unreadable CSV reports an error and gives no count (so R2 becomes UNKNOWN)."""
    path = tmp_path / "vehicles.csv"
    path.write_text("", encoding="utf-8")
    result = asyncio.run(extract_csv(path))
    assert not result.readable and "Could not parse CSV" in result.error
    assert "schedule_vehicle_count" not in result.fields
