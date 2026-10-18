"""File routing tests: unsupported and system files; no LLM calls (no PDF or TXT involved)."""
import asyncio

from underwriting.pipeline import collect_paths, extract_all
from underwriting.review.merge import merge_sources
from underwriting.review.narrative import request_items
from underwriting.review.rules import evaluate_rules

HEADER = "vehicle_id,vin,year,make,model\n"


def make_packet(tmp_path, *extra):
    """A folder with a valid vehicles.csv plus any extra (name, content) files."""
    (tmp_path / "vehicles.csv").write_text(HEADER + "A,1,2022,M,V\n", encoding="utf-8")
    for name, content in extra:
        (tmp_path / name).write_bytes(content)
    return collect_paths([tmp_path])


def test_unsupported_attachment_is_kept_as_unreadable(tmp_path):
    """A .docx is kept as an unreadable 'other' attachment with a clear error."""
    results = asyncio.run(extract_all(make_packet(tmp_path, ("loss_runs.docx", b"PK..."))))
    docx = next(r for r in results if r.source_file == "loss_runs.docx")
    assert docx.kind == "other" and not docx.readable
    assert "Unsupported file type '.docx'" in docx.error


def test_unsupported_attachment_makes_r5_unknown_not_flag(tmp_path):
    """An unreadable attachment makes R5 UNKNOWN, and the broker is asked to resend it."""
    results = asyncio.run(extract_all(make_packet(tmp_path, ("loss_runs.docx", b"PK..."))))
    fields = merge_sources(results)
    r5 = evaluate_rules(fields)[4]
    assert r5.status == "UNKNOWN"
    assert "loss_runs.docx (unclassified, unreadable)" in r5.reason
    # The broker is asked to resend it, naming the file
    assert any("loss_runs.docx" in item for item in request_items(fields, evaluate_rules(fields)))


def test_without_any_extra_attachment_r5_flags(tmp_path):
    """With no possible loss runs attachment at all, R5 is FLAG."""
    results = asyncio.run(extract_all(make_packet(tmp_path)))
    assert evaluate_rules(merge_sources(results))[4].status == "FLAG"


def test_system_and_hidden_files_are_ignored(tmp_path):
    """desktop.ini, .DS_Store and Thumbs.db are ignored and don't affect R5."""
    paths = make_packet(tmp_path, ("desktop.ini", b"x"), (".DS_Store", b"x"), ("Thumbs.db", b"x"))
    assert [p.name for p in paths] == ["vehicles.csv"]
    results = asyncio.run(extract_all(paths))
    assert evaluate_rules(merge_sources(results))[4].status == "FLAG"
