"""Rule tests with hand-made field values; no LLM calls."""
import pytest

from underwriting.extraction.schema import SourceResult
from underwriting.review.rules import (ResolvedField, derive_loss_runs_present, evaluate_rules,
                                       overall_status, rule_r1, rule_r2, rule_r3, rule_r4, rule_r5)


def known(value):
    """A resolved KNOWN field with this value."""
    return ResolvedField(state="KNOWN", value=value)


def state(name, note=None):
    """A resolved field in a non-KNOWN state (MISSING, CONFLICT or INVALID)."""
    return ResolvedField(state=name, note=note)


def clean_fields(**overrides):
    """A submission where every rule passes; override fields per test."""
    fields = {
        "business_name": known("Pacific Harbor Delivery Example LLC"),
        "requested_effective_date": known("2026-11-01"),
        "declared_vehicle_count": known(3),
        "schedule_vehicle_count": known(3),
        "operating_radius_miles": known(150),
        "hazardous_materials": known(False),
        "loss_runs_present": known(True),
    }
    fields.update(overrides)
    return fields


# --- R1 Required identity and effective date --------------------------------

def test_r1_pass():
    """Name and date present and valid: PASS."""
    assert rule_r1(clean_fields()).status == "PASS"


@pytest.mark.parametrize("field", ["business_name", "requested_effective_date"])
def test_r1_missing_field_flags(field):
    """Either required field missing: FLAG, naming the field."""
    result = rule_r1(clean_fields(**{field: state("MISSING")}))
    assert result.status == "FLAG"
    assert field in result.reason


def test_r1_absent_key_counts_as_missing():
    """A field not supplied at all counts as missing."""
    fields = clean_fields()
    del fields["requested_effective_date"]
    assert rule_r1(fields).status == "FLAG"


def test_r1_conflicting_name_is_unknown():
    """A conflicting name: UNKNOWN, with the conflict shown in the reason."""
    result = rule_r1(clean_fields(business_name=state("CONFLICT", "'A LLC' vs 'B LLC'")))
    assert result.status == "UNKNOWN"
    assert "'A LLC' vs 'B LLC'" in result.reason


@pytest.mark.parametrize("bad_date", ["2026-02-30", "11/01/2026", "Not provided", "20261101"])
def test_r1_invalid_date_is_unknown(bad_date):
    """Impossible or wrongly formatted dates: UNKNOWN."""
    assert rule_r1(clean_fields(requested_effective_date=known(bad_date))).status == "UNKNOWN"


def test_r1_missing_takes_precedence_over_conflict():
    """Missing date plus conflicting name: FLAG wins over UNKNOWN."""
    fields = clean_fields(business_name=state("CONFLICT"),
                          requested_effective_date=state("MISSING"))
    assert rule_r1(fields).status == "FLAG"


def test_r1_blank_name_is_invalid():
    """A name made only of spaces is invalid: UNKNOWN."""
    assert rule_r1(clean_fields(business_name=known("   "))).status == "UNKNOWN"


# --- R2 Vehicle count reconciliation -----------------------------------------

def test_r2_equal_counts_pass():
    """Declared 3, schedule 3: PASS."""
    assert rule_r2(clean_fields()).status == "PASS"


def test_r2_unequal_counts_flag():
    """Declared 4, schedule 3: FLAG, with both counts in the reason."""
    result = rule_r2(clean_fields(declared_vehicle_count=known(4)))
    assert result.status == "FLAG"
    assert "4" in result.reason and "3" in result.reason


def test_r2_zero_vehicles_is_valid():
    """0 and 0 is a valid match (no minimum-fleet rule)."""
    fields = clean_fields(declared_vehicle_count=known(0), schedule_vehicle_count=known(0))
    assert rule_r2(fields).status == "PASS"


def test_r2_harmless_float_normalized():
    """3.0 counts as 3."""
    assert rule_r2(clean_fields(declared_vehicle_count=known(3.0))).status == "PASS"


@pytest.mark.parametrize("declared", [
    state("MISSING"), state("CONFLICT"), state("INVALID"), known(-1), known(2.5), known(True),
])
def test_r2_bad_declared_count_is_unknown(declared):
    """Missing, conflicting, invalid, negative, fractional or yes/no counts: UNKNOWN."""
    assert rule_r2(clean_fields(declared_vehicle_count=declared)).status == "UNKNOWN"


def test_r2_unparseable_csv_is_unknown():
    # csv_extract gives no schedule count when the CSV can't be parsed
    """An unreadable CSV: UNKNOWN."""
    assert rule_r2(clean_fields(schedule_vehicle_count=state("INVALID"))).status == "UNKNOWN"


def test_r2_conflict_is_unknown_even_if_counts_would_differ():
    """A conflicting declared count is UNKNOWN, not compared."""
    fields = clean_fields(declared_vehicle_count=state("CONFLICT", "3 vs 5"))
    assert rule_r2(fields).status == "UNKNOWN"


# --- R3 Operating radius (150-mile threshold) --------------------------------

@pytest.mark.parametrize("radius, expected", [
    (0, "PASS"), (80, "PASS"), (150, "PASS"), (150.0, "PASS"), ("150", "PASS"),
    (150.1, "FLAG"), (151, "FLAG"), (180, "FLAG"),
])
def test_r3_threshold(radius, expected):
    """150 and below pass; anything above 150 is flagged."""
    assert rule_r3(clean_fields(operating_radius_miles=known(radius))).status == expected


@pytest.mark.parametrize("radius", [
    state("MISSING"), state("CONFLICT"), state("INVALID"), known(-10), known("about 100"),
    known(True),
])
def test_r3_unusable_radius_is_unknown(radius):
    """Missing, conflicting, invalid, negative, non-numeric or yes/no radius: UNKNOWN."""
    assert rule_r3(clean_fields(operating_radius_miles=radius)).status == "UNKNOWN"


def test_r3_conflict_is_unknown_before_threshold():
    # 80 vs 180: one side is above 150, but an unresolved conflict is UNKNOWN, not FLAG
    """80 vs 180 is UNKNOWN before the 150 threshold is tested."""
    fields = clean_fields(operating_radius_miles=state("CONFLICT", "80 (application) vs 180 (email)"))
    result = rule_r3(fields)
    assert result.status == "UNKNOWN"
    assert "80 (application) vs 180 (email)" in result.reason


# --- R4 Hazardous materials --------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (False, "PASS"), ("No", "PASS"), (True, "FLAG"), ("Yes", "FLAG"),
])
def test_r4_explicit_answers(value, expected):
    """Explicit No passes; explicit Yes is flagged."""
    assert rule_r4(clean_fields(hazardous_materials=known(value))).status == expected


@pytest.mark.parametrize("value", [
    state("MISSING"), state("CONFLICT"), known("occasionally"), known(1),
])
def test_r4_absent_ambiguous_or_conflicting_is_unknown(value):
    """Missing, conflicting or ambiguous answers are UNKNOWN (absence is not No)."""
    assert rule_r4(clean_fields(hazardous_materials=value)).status == "UNKNOWN"


# --- R5 Loss runs attachment -------------------------------------------------

def test_r5_states():
    """Report attached: PASS; none: FLAG; unreadable or unclassified: UNKNOWN."""
    assert rule_r5(clean_fields(loss_runs_present=known(True))).status == "PASS"
    assert rule_r5(clean_fields(loss_runs_present=known(False))).status == "FLAG"
    assert rule_r5(clean_fields(loss_runs_present=state("INVALID"))).status == "UNKNOWN"


def src(name, kind, document_type, readable=True):
    """A minimal SourceResult for building attachment lists."""
    return SourceResult(source_file=name, kind=kind, document_type=document_type, readable=readable)


EMAIL = src("broker_email.txt", "email", "broker_email")
APPLICATION = src("application.pdf", "pdf", "application")
CSV = src("vehicles.csv", "csv", "vehicle_schedule")


def test_derive_readable_loss_runs_report_attached():
    """A readable PDF classified as loss runs means a report is attached."""
    field = derive_loss_runs_present([EMAIL, APPLICATION, CSV, src("loss_runs.pdf", "pdf", "loss_runs")])
    assert (field.state, field.value) == ("KNOWN", True)


def test_derive_email_promise_is_not_an_attachment():
    # Email says "we will send them separately"; only the application and CSV are attached
    """An email promising loss runs later is not an attachment: FLAG."""
    field = derive_loss_runs_present([EMAIL, APPLICATION, CSV])
    assert (field.state, field.value) == ("KNOWN", False)
    assert rule_r5({"loss_runs_present": field}).status == "FLAG"


def test_derive_inventory_shown_when_missing():
    """When the report is missing, R5's reason lists the attachments (not the email)."""
    field = derive_loss_runs_present([EMAIL, APPLICATION, CSV])
    reason = rule_r5({"loss_runs_present": field}).reason
    assert "application.pdf" in reason and "vehicles.csv" in reason
    assert "broker_email.txt" not in reason      # the email body isn't an attachment


@pytest.mark.parametrize("unclear_pdf", [
    src("scan.pdf", "pdf", None, readable=False),   # unreadable
    src("report.pdf", "pdf", "other"),              # can't tell what it is
    src("history.pdf", "pdf", None),                # classification failed
])
def test_derive_unreadable_or_unclassified_pdf_is_unknown(unclear_pdf):
    """An unreadable, unclassified or failed PDF could be the report: UNKNOWN."""
    field = derive_loss_runs_present([EMAIL, APPLICATION, CSV, unclear_pdf])
    assert rule_r5({"loss_runs_present": field}).status == "UNKNOWN"


def test_derive_readable_report_wins_over_unclear_pdf():
    """A readable report gives PASS even if another PDF is unclear."""
    field = derive_loss_runs_present([APPLICATION, src("loss_runs.pdf", "pdf", "loss_runs"),
                                      src("other.pdf", "pdf", "other")])
    assert field.value is True


# --- Overall status ----------------------------------------------------------

def test_overall_ready_only_when_all_pass():
    """READY_FOR_REVIEW only when all five rules PASS."""
    assert overall_status(evaluate_rules(clean_fields())) == "READY_FOR_REVIEW"


@pytest.mark.parametrize("override", [
    {"operating_radius_miles": known(151)},          # one FLAG
    {"hazardous_materials": state("CONFLICT")},      # one UNKNOWN
])
def test_overall_needs_review_on_any_flag_or_unknown(override):
    """Any FLAG or UNKNOWN gives NEEDS_REVIEW."""
    assert overall_status(evaluate_rules(clean_fields(**override))) == "NEEDS_REVIEW"


# --- The three sample packets, from the values seen in their files -----------

def statuses(fields):
    """The five rule statuses for these fields, in order."""
    return [r.status for r in evaluate_rules(fields)]


def test_sample_sub_a_all_pass():
    """SUB-A's values: all five rules PASS."""
    assert statuses(clean_fields()) == ["PASS"] * 5


def test_sample_sub_b_missing_values_and_no_loss_runs():
    """SUB-B's values: missing date, radius and hazmat, and no loss runs."""
    fields = clean_fields(
        business_name=known("Canyon Parcel Example LLC"),
        requested_effective_date=state("MISSING"),
        declared_vehicle_count=known(2), schedule_vehicle_count=known(2),
        operating_radius_miles=state("MISSING"),
        hazardous_materials=state("MISSING"),
        loss_runs_present=known(False),
    )
    assert statuses(fields) == ["FLAG", "PASS", "UNKNOWN", "UNKNOWN", "FLAG"]


def test_sample_sub_c_conflicts_and_unequal_counts():
    """SUB-C's values: 4 vs 3 vehicles, radius and hazmat conflicts, no loss runs."""
    fields = clean_fields(
        business_name=known("Metro Route Example LLC"),
        declared_vehicle_count=known(4), schedule_vehicle_count=known(3),
        operating_radius_miles=state("CONFLICT", "80 vs 180"),
        hazardous_materials=state("CONFLICT", "No vs Yes"),
        loss_runs_present=known(False),
    )
    assert statuses(fields) == ["PASS", "FLAG", "UNKNOWN", "UNKNOWN", "FLAG"]
