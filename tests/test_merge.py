"""Normalization, merge and override tests with hand-made extraction results; no LLM calls."""
import pytest

from underwriting.extraction.schema import LocatedField, SourceResult, contains_excerpt
from underwriting.review.merge import apply_override, merge_sources, value_in_quote
from underwriting.review.normalize import normalize
from underwriting.review.rules import evaluate_rules, overall_status


# --- helpers -----------------------------------------------------------------

def stated(value, excerpt=None, as_written=None):
    """A model answer with an explicit value and a quote containing it."""
    return LocatedField(status="stated", value=value, as_written=as_written or str(value),
                        excerpt=excerpt or f"... {value}", locator="x", excerpt_verified=True)


def said(status, as_written=None, note=None):
    """A model answer with a non-'stated' status (not_provided, conflicting, ...) and no value."""
    return LocatedField(status=status, as_written=as_written, note=note,
                        excerpt=as_written, locator="x", excerpt_verified=as_written is not None)


NOT_MENTIONED = LocatedField(status="not_mentioned")

BASE = {
    "business_name": stated("Metro Route Example LLC"),
    "requested_effective_date": stated("2026-11-01"),
    "garaging_state": stated("CA"),
    "operations_description": stated("Local delivery of packaged retail goods"),
    "declared_vehicle_count": stated(3),
    "operating_radius_miles": stated(150.0),
    "hazardous_materials": stated(False),
}


def application(**fields):
    """An application.pdf result with the BASE values, overridable per field."""
    return SourceResult(source_file="application.pdf", kind="pdf", readable=True,
                        document_type="application", fields={**BASE, **fields})


def email(**fields):
    """A broker_email.txt result with the BASE values, overridable per field."""
    return SourceResult(source_file="broker_email.txt", kind="email", readable=True,
                        document_type="broker_email", fields={**BASE, **fields})


def csv(count=3):
    """A vehicles.csv result with the given schedule count."""
    return SourceResult(source_file="vehicles.csv", kind="csv", readable=True,
                        document_type="vehicle_schedule",
                        fields={"schedule_vehicle_count": LocatedField(
                            status="stated", value=count, locator="CSV data rows 1-3 (header excluded)",
                            excerpt_verified=True)})


LOSS_RUNS = SourceResult(source_file="loss_runs.pdf", kind="pdf", readable=True,
                         document_type="loss_runs",
                         document_type_evidence=LocatedField(value="loss_runs",
                                                             excerpt="Loss runs report",
                                                             locator="PDF page 1",
                                                             excerpt_verified=True))


# --- normalization -----------------------------------------------------------

@pytest.mark.parametrize("name, raw, expected", [
    ("garaging_state", "California", "CA"),
    ("garaging_state", "ca", "CA"),
    ("garaging_state", "Calif", None),
    ("business_name", "  Metro   Route LLC ", "Metro Route LLC"),
    ("requested_effective_date", "2026-02-30", None),
    ("operating_radius_miles", "150.0", 150.0),
    ("operating_radius_miles", -5, None),
    ("declared_vehicle_count", "4", 4),
    ("declared_vehicle_count", 2.5, None),
    ("hazardous_materials", "No", False),
    ("hazardous_materials", "occasionally", None),
])
def test_normalize(name, raw, expected):
    """Each field's normalizer accepts harmless variants and rejects invalid values."""
    assert normalize(name, raw) == expected


# --- quote check (whole words only) --------------------------------------------

def test_excerpt_must_match_whole_words():
    """The quote check ignores spacing and '|', but '80' must not match inside '180'."""
    assert contains_excerpt("Operating radius miles: 180", "radius miles: 180")
    assert contains_excerpt("Business name | Acme  LLC", "Business name Acme LLC")
    assert not contains_excerpt("Operating radius miles: 180", "80")
    assert not contains_excerpt("Hazardous materials: Not provided", "Hazardous materials: No")


# --- merging -------------------------------------------------------------------

def test_agreeing_sources_are_known_with_evidence_from_both():
    """Same value in both files: KNOWN, with evidence from each file."""
    f = merge_sources([application(), email(), csv()])["declared_vehicle_count"]
    assert (f.state, f.value) == ("KNOWN", 3)
    assert [e.source_file for e in f.evidence] == ["application.pdf", "broker_email.txt"]


def test_harmless_differences_are_not_conflicts():
    """Case, 'California' vs 'CA', 150 vs 150.0 and a trailing period are not conflicts."""
    fields = merge_sources([
        application(garaging_state=stated("California"),
                    business_name=stated("METRO ROUTE EXAMPLE LLC"),
                    operating_radius_miles=stated(150)),
        email(operations_description=stated("Local delivery of packaged retail goods.")),
    ])
    for name in ("garaging_state", "business_name", "operating_radius_miles",
                 "operations_description"):
        assert fields[name].state == "KNOWN", name
    assert fields["garaging_state"].value == "CA"


def test_source_conflict_keeps_both_alternatives_and_null_value():
    """80 vs 180: CONFLICT, value null, both values and both quotes kept."""
    f = merge_sources([application(operating_radius_miles=stated(80.0)),
                       email(operating_radius_miles=stated(180.0))])["operating_radius_miles"]
    assert f.state == "CONFLICT" and f.value is None
    assert {(a.value, a.source_file) for a in f.alternatives} == {
        (80.0, "application.pdf"), (180.0, "broker_email.txt")}
    assert "80 (application.pdf) vs 180 (broker_email.txt)" in f.note
    assert len(f.evidence) == 2


def test_missing_mention_is_not_a_conflict():
    """A file that doesn't mention the field doesn't contradict the other file."""
    f = merge_sources([application(), email(operating_radius_miles=NOT_MENTIONED)])
    assert f["operating_radius_miles"].state == "KNOWN"


def test_not_provided_in_one_source_is_not_a_conflict():
    """'Not provided' in one file plus a value in the other gives that value."""
    f = merge_sources([application(),
                       email(operating_radius_miles=said("not_provided", "Not provided"))])
    assert (f["operating_radius_miles"].state, f["operating_radius_miles"].value) == ("KNOWN", 150.0)


def test_not_provided_everywhere_is_missing_with_sources_checked():
    """'Not provided' everywhere: MISSING, listing the files that were checked."""
    nope = said("not_provided", "Not provided")
    f = merge_sources([application(requested_effective_date=nope),
                       email(requested_effective_date=nope)])["requested_effective_date"]
    assert f.state == "MISSING" and f.value is None
    assert f.sources_checked == ["application.pdf", "broker_email.txt"]
    assert "stated as not provided in: application.pdf, broker_email.txt" in f.note


def test_not_mentioned_anywhere_has_no_invented_quote():
    """Never mentioned: MISSING with no evidence, so no quote is invented."""
    f = merge_sources([application(hazardous_materials=NOT_MENTIONED),
                       email(hazardous_materials=NOT_MENTIONED)])["hazardous_materials"]
    assert f.state == "MISSING" and f.evidence == []
    assert "not mentioned in: application.pdf, broker_email.txt" in f.note


def test_conflict_within_one_document_is_conflict():
    """One document giving two different answers is a CONFLICT too."""
    f = merge_sources([application(),
                       email(operating_radius_miles=said("conflicting", "120", "120 vs 200"))])
    assert f["operating_radius_miles"].state == "CONFLICT"
    assert "120 vs 200" in f["operating_radius_miles"].note


@pytest.mark.parametrize("field", [
    said("unparseable", "02/30/2026"),
    said("ambiguous", "next month"),
    stated("2026-02-30"),                      # model claims stated, but the date is not real
])
def test_unusable_date_is_invalid(field):
    """Unparseable, ambiguous or impossible dates are INVALID, not KNOWN."""
    f = merge_sources([application(requested_effective_date=field), email()])
    assert f["requested_effective_date"].state == "INVALID"


def test_stated_value_falls_back_to_wording():
    # model left value empty but quoted '$0'-style wording; normalization recovers it
    """If the model left the value empty, the quoted wording is normalized instead."""
    field = LocatedField(status="stated", value=None, as_written="150", excerpt="150",
                         excerpt_verified=True)
    f = merge_sources([application(operating_radius_miles=field), email()])
    assert (f["operating_radius_miles"].state, f["operating_radius_miles"].value) == ("KNOWN", 150.0)


def test_failed_extraction_is_not_reported_as_missing():
    """If no file could be checked, the field is INVALID (UNKNOWN), never MISSING."""
    failed = SourceResult(source_file="broker_email.txt", kind="email", readable=True,
                          document_type="broker_email", error="Model call failed")
    broken_app = SourceResult(source_file="application.pdf", kind="pdf", readable=True,
                              document_type="application", error="Model call failed")
    f = merge_sources([broken_app, failed])["business_name"]
    assert f.state == "INVALID"
    assert "could not be checked" in f.note


def test_one_failed_source_still_uses_the_other():
    """One failed file: the other file's value is used and the failure is noted."""
    failed = SourceResult(source_file="broker_email.txt", kind="email", readable=True,
                          document_type="broker_email", error="Model call failed")
    f = merge_sources([application(), failed])["business_name"]
    assert f.state == "KNOWN" and "broker_email.txt" in f.note


def test_loss_runs_pdf_fields_are_ignored():
    """Only the application and the email supply the business fields."""
    stray = SourceResult(source_file="loss_runs.pdf", kind="pdf", readable=True,
                         document_type="loss_runs",
                         fields={"business_name": stated("Someone Else LLC")})
    f = merge_sources([application(), email(), stray])["business_name"]
    assert f.state == "KNOWN"


# --- derived fields ------------------------------------------------------------

def test_schedule_count_from_csv():
    """schedule_vehicle_count comes from the CSV, with its row-range locator."""
    f = merge_sources([application(), csv(3)])["schedule_vehicle_count"]
    assert (f.state, f.value) == ("KNOWN", 3)
    assert f.evidence[0].locator == "CSV data rows 1-3 (header excluded)"


def test_no_csv_is_missing_and_bad_csv_is_invalid():
    """No CSV gives MISSING; an unparseable CSV gives INVALID."""
    assert merge_sources([application()])["schedule_vehicle_count"].state == "MISSING"
    bad = SourceResult(source_file="vehicles.csv", kind="csv", readable=False,
                       document_type="vehicle_schedule", error="Could not parse CSV")
    assert merge_sources([application(), bad])["schedule_vehicle_count"].state == "INVALID"


def test_loss_runs_present_has_inventory_evidence():
    """loss_runs_present carries the attachment inventory and the quote that identified the report."""
    f = merge_sources([application(), email(), csv(), LOSS_RUNS])["loss_runs_present"]
    assert (f.state, f.value) == ("KNOWN", True)
    assert f.evidence[0].locator == "uploaded attachment inventory"
    assert any(e.excerpt_or_observation == "Loss runs report" for e in f.evidence)


# --- human overrides -------------------------------------------------------------

def sub_c_like():
    """Merged fields shaped like SUB-C: 4 declared vs 3 scheduled, radius and hazmat conflicts."""
    return merge_sources([
        application(declared_vehicle_count=stated(4), operating_radius_miles=stated(80.0),
                    hazardous_materials=stated(False)),
        email(declared_vehicle_count=stated(4), operating_radius_miles=stated(180.0),
              hazardous_materials=stated(True)),
        csv(3),
    ])


def test_override_resolves_conflict_and_keeps_original():
    """A correction makes R3 PASS, is labelled, and keeps the original conflict and quotes."""
    fields = sub_c_like()
    assert evaluate_rules(fields)[2].status == "UNKNOWN"            # R3 before

    fields = apply_override(fields, "operating_radius_miles", "80")
    f = fields["operating_radius_miles"]
    assert (f.state, f.value, f.human_override) == ("KNOWN", 80.0, True)
    assert f.extracted_state == "CONFLICT" and len(f.alternatives) == 2 and len(f.evidence) == 2
    assert "HUMAN OVERRIDE" in f.note and "80 (application.pdf) vs 180" in f.note

    r3 = evaluate_rules(fields)[2]
    assert r3.status == "PASS" and r3.inputs["operating_radius_miles"]["human_override"]


def test_invalid_override_stays_unresolved():
    """An invalid correction leaves the field INVALID and R3 UNKNOWN."""
    fields = apply_override(sub_c_like(), "operating_radius_miles", "far")
    assert fields["operating_radius_miles"].state == "INVALID"
    assert evaluate_rules(fields)[2].status == "UNKNOWN"


def test_second_override_keeps_first_extracted_value():
    """Correcting twice still remembers what extraction originally produced."""
    fields = apply_override(sub_c_like(), "operating_radius_miles", "80")
    fields = apply_override(fields, "operating_radius_miles", "180")
    f = fields["operating_radius_miles"]
    assert f.value == 180.0 and f.extracted_state == "CONFLICT"


def test_override_does_not_change_other_fields_or_original_dict():
    """apply_override returns a new dict and leaves its input untouched."""
    original = sub_c_like()
    apply_override(original, "operating_radius_miles", "80")
    assert original["operating_radius_miles"].state == "CONFLICT"


# --- end to end: merge -> rules for the sample packet shapes -------------------------

def test_sub_c_shape_end_to_end():
    """Merge then rules gives SUB-C's expected results."""
    rules = evaluate_rules(sub_c_like())
    assert [r.status for r in rules] == ["PASS", "FLAG", "UNKNOWN", "UNKNOWN", "FLAG"]
    assert overall_status(rules) == "NEEDS_REVIEW"


def test_sub_b_shape_end_to_end():
    """Merge then rules gives SUB-B's expected results."""
    nope = said("not_provided", "Not provided")
    fields = merge_sources([
        application(declared_vehicle_count=stated(2), requested_effective_date=nope,
                    operating_radius_miles=nope, hazardous_materials=nope),
        email(declared_vehicle_count=stated(2), requested_effective_date=nope,
              operating_radius_miles=nope, hazardous_materials=nope),
        csv(2),
    ])
    assert [r.status for r in evaluate_rules(fields)] == ["FLAG", "PASS", "UNKNOWN", "UNKNOWN", "FLAG"]


def test_sub_a_shape_end_to_end():
    """Merge then rules gives SUB-A's expected results (all PASS)."""
    fields = merge_sources([application(), email(), csv(3), LOSS_RUNS])
    rules = evaluate_rules(fields)
    assert [r.status for r in rules] == ["PASS"] * 5
    assert overall_status(rules) == "READY_FOR_REVIEW"


# --- value-in-quote check --------------------------------------------------------


def quote(value, as_written, excerpt):
    """A stated model answer with the given value, wording and quote."""
    return LocatedField(status="stated", value=value, as_written=as_written, excerpt=excerpt,
                        excerpt_verified=True)


@pytest.mark.parametrize("name, value, as_written, excerpt", [
    ("operating_radius_miles", 150.0, "150", "Operating radius miles 150"),
    ("operating_radius_miles", 150.0, None, "Operating radius miles: 150.0"),
    ("garaging_state", "CA", "California", "Garaging state California"),
    ("garaging_state", "CA", None, "Garaging state: California"),        # code -> state name
    ("hazardous_materials", False, "No", "Hazardous materials No"),
    ("hazardous_materials", True, None, "Hazmat: true"),
    ("requested_effective_date", "2026-11-01", "11/01/2026", "Effective date 11/01/2026"),
    ("requested_effective_date", "2026-11-01", None, "Effective November 1, 2026"),
    ("declared_vehicle_count", 4, "four", "Declared vehicle count 4"),     # wording wrong, value right
    ("business_name", "Metro Route Example LLC", None, "Business name: METRO ROUTE EXAMPLE LLC"),
])
def test_value_found_in_quote(name, value, as_written, excerpt):
    """Values that are in their quote, in various written forms, pass the check."""
    assert value_in_quote(name, value, quote(value, as_written, excerpt)) is True


@pytest.mark.parametrize("name, value, as_written, excerpt", [
    ("operating_radius_miles", 180.0, "80", "Operating radius miles 80"),   # 180 not in quote
    ("operating_radius_miles", 80.0, None, "Operating radius miles: 180"),  # whole words only
    ("hazardous_materials", True, "No", "Hazardous materials No"),
    ("garaging_state", "NV", "California", "Garaging state California"),
    ("declared_vehicle_count", 5, None, "Declared vehicle count 4"),
    ("requested_effective_date", "2026-12-01", None, "Effective date 2026-11-01"),
])
def test_value_not_found_in_quote(name, value, as_written, excerpt):
    """Values missing from their quote are flagged (whole words only)."""
    assert value_in_quote(name, value, quote(value, as_written, excerpt)) is False


def test_nothing_to_check_without_value_or_quote():
    """No value or no quote means the check doesn't apply (None)."""
    assert value_in_quote("operating_radius_miles", None, quote(None, None, "x")) is None
    assert value_in_quote("operating_radius_miles", 150.0, quote(150.0, "150", None)) is None


def test_mismatch_is_flagged_on_evidence_and_note_but_state_unchanged():
    """A value not in its quote is flagged on the evidence and in the note, but stays KNOWN."""
    bad = LocatedField(status="stated", value=180.0, as_written="80",
                       excerpt="Operating radius miles 80", locator="PDF page 1", excerpt_verified=True)
    f = merge_sources([application(operating_radius_miles=bad),
                       email(operating_radius_miles=NOT_MENTIONED)])["operating_radius_miles"]
    assert f.state == "KNOWN" and f.value == 180.0
    assert f.evidence[0].value_in_quote is False
    assert "value was not found in its quote from application.pdf" in f.note


def test_matching_sources_have_no_flag():
    """Normal agreeing evidence is confirmed and adds no note."""
    f = merge_sources([application(), email()])["declared_vehicle_count"]
    assert all(e.value_in_quote is True for e in f.evidence)
    assert f.note is None
