"""Command-line entry point: review one submission end to end.

Extract (PDFs, broker email, vehicle CSV) concurrently, merge into one value
per field, then run rules R1-R5.

Usage:
    python main.py <folder_or_files...> [--set field=value ...] [--out results.json]

--set applies a human override (e.g. --set operating_radius_miles=80) and
re-runs the rules; the extracted value and evidence are kept.
"""
import argparse                             # command-line options
import asyncio                              # runs the async extraction
import json                                 # --out file
import sys                                  # exit codes and UTF-8 console output
from pathlib import Path                    # output path

from dotenv import load_dotenv              # loads OPENAI_API_KEY from .env

from underwriting.extraction.schema import FIELD_NAMES
from underwriting.pipeline import collect_paths, extract_all
from underwriting.review.merge import ALL_FIELDS, apply_override, fmt, merge_sources
from underwriting.review.rules import evaluate_rules, overall_status

TABLE_FIELDS = FIELD_NAMES + ["schedule_vehicle_count"]     # rows of the per-file table
WIDTH = max(len(n) for n in ALL_FIELDS) + 2                 # first-column width


def print_sources(results):
    """One row per field, one column per source file."""
    names = [r.source_file for r in results]
    print("EXTRACTED PER FILE")
    print("field".ljust(WIDTH) + " | ".join(n.ljust(28) for n in names))
    # Document type row, marking types whose evidence quote wasn't found
    types = []
    for r in results:
        t = r.document_type or "?"
        if r.document_type_evidence and not r.document_type_evidence.excerpt_verified:
            t += " (unverified)"
        types.append(t[:28])
    print("document_type".ljust(WIDTH) + " | ".join(t.ljust(28) for t in types))
    # One row per field
    for field in TABLE_FIELDS:
        cells = []
        for r in results:
            if r.error:
                cells.append("ERROR")
                continue
            if field not in r.fields:
                cells.append("-")   # this source type doesn't provide the field
                continue
            f = r.fields[field]
            cell = fmt(f.value) if f.value is not None else (f.status or "null")
            if f.excerpt and not f.excerpt_verified:
                cell += " (unverified)"
            cells.append(cell[:28])
        print(field.ljust(WIDTH) + " | ".join(c.ljust(28) for c in cells))
    # Loss runs values, if a report was attached
    for r in results:
        if r.loss_runs_fields:
            print(f"\nLoss runs report: {r.source_file}")
            for name, f in r.loss_runs_fields.items():
                cell = "null" if f.value is None else fmt(f.value)
                if f.excerpt and not f.excerpt_verified:
                    cell += " (unverified)"
                print(f"  {name.ljust(WIDTH)}{cell}   [{f.locator or 'no excerpt'}]")
    # Per-file errors at the end
    for r in results:
        if r.error:
            print(f"\n{r.source_file}: {r.error}")


def print_merged(fields):
    """Resolved value, state and evidence for every field."""
    print("\nRESOLVED FIELDS")
    for name in ALL_FIELDS:
        f = fields[name]
        value = "null" if f.value is None else fmt(f.value)
        tag = "  [HUMAN OVERRIDE]" if f.human_override else ""
        print(f"{name.ljust(WIDTH)}{f.state.ljust(9)}{value[:40]}{tag}")
        for e in f.evidence:
            check = "" if e.verified else "  (excerpt not found in source)"
            if e.value_in_quote is False:
                check += "  (value not found in this quote)"
            print(f"{'':{WIDTH}}  - {e.source_file}, {e.locator or 'no locator'}: "
                  f"{(e.excerpt_or_observation or '')[:70]}{check}")
        if f.note:
            print(f"{'':{WIDTH}}  note: {f.note[:160]}")


def print_rules(rules):
    """R1-R5 results and the overall status."""
    print("\nRULES")
    for r in rules:
        print(f"{r.id}  {r.status.ljust(8)} {r.name}: {r.reason[:150]}")
    print(f"\nOVERALL: {overall_status(rules)}")


def main():
    """Parse arguments, run the pipeline, print everything, optionally save JSON."""
    parser = argparse.ArgumentParser(description="Review one commercial auto submission.")
    parser.add_argument("inputs", nargs="+", help="Submission folder(s) or individual files")
    parser.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE",
                        help="Human override, can be repeated")
    parser.add_argument("--out", help="Also write the full JSON (with evidence) here")
    args = parser.parse_args()

    # Validate --set FIELD=VALUE pairs before spending any model calls
    overrides = []
    for item in args.set:
        name, sep, value = item.partition("=")
        if not sep or name not in ALL_FIELDS:
            parser.error(f"--set needs FIELD=VALUE with FIELD one of: {', '.join(ALL_FIELDS)}")
        overrides.append((name, value))

    load_dotenv()                           # OPENAI_API_KEY / OPENAI_MODEL from .env
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    results = asyncio.run(extract_all(collect_paths(args.inputs)))
    if not results:
        sys.exit("No .pdf, .txt or .csv files found.")

    # Merge, apply reviewer corrections, then run the rules
    fields = merge_sources(results)
    for name, value in overrides:
        fields = apply_override(fields, name, value)
    rules = evaluate_rules(fields)

    print_sources(results)
    print_merged(fields)
    print_rules(rules)

    if args.out:
        output = {
            "sources": [r.model_dump() for r in results],
            "fields": {n: f.model_dump() for n, f in fields.items()},
            "rules": [r.model_dump() for r in rules],
            "overall_status": overall_status(rules),
        }
        Path(args.out).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull JSON with evidence written to {args.out}")


if __name__ == "__main__":
    main()
