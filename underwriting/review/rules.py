"""Deterministic R1-R5 checks (no LLM), following rules.json.

Input is one resolved value per field with a state from field_specification.json:
KNOWN, MISSING, CONFLICT or INVALID. A KNOWN value that fails validation here
is treated as INVALID, so the rules never trust an unchecked value.
"""
import math                                 # isfinite() rejects inf / nan
from datetime import date                   # strict calendar-date validation
from typing import Literal, Optional        # fixed choices and nullable values

from pydantic import BaseModel              # typed rule inputs and outputs

FieldState = Literal["KNOWN", "MISSING", "CONFLICT", "INVALID"]   # from field_specification.json
Status = Literal["PASS", "FLAG", "UNKNOWN"]                       # from rules.json

RADIUS_THRESHOLD_MILES = 150                # R3: 150 passes, anything above is flagged


class ResolvedField(BaseModel):
    """The one value per field that the rules look at."""
    state: FieldState
    value: Optional[str | int | float | bool] = None
    note: Optional[str] = None             # e.g. conflict alternatives, attachment inventory


class RuleResult(BaseModel):
    """Outcome of one rule, with the reason and the inputs it used (shown to the reviewer)."""
    id: str                                # "R1" .. "R5"
    name: str
    status: Status
    reason: str
    inputs: dict[str, dict]                # field -> {"state", "value", "human_override"}


# ---------------------------------------------------------------------------
# Validation / normalization (returns None when the value is not usable)
# ---------------------------------------------------------------------------

def parse_name(value):
    """A non-empty string, trimmed."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_date(value):
    """Strict YYYY-MM-DD that is a real calendar date."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        # Round-trip check rejects formats like '20261101' that fromisoformat also accepts
        return text if date.fromisoformat(text).isoformat() == text else None
    except ValueError:
        return None                         # e.g. 2026-02-30


def parse_number(value):
    """150, 150.0 and '150' are all 150.0. Booleans are not numbers."""
    if isinstance(value, bool):             # bool is a subclass of int in Python, so check first
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None                     # e.g. 'about 100'
    else:
        return None
    return number if math.isfinite(number) else None


def parse_count(value):
    """Nonnegative whole number (3 and 3.0 are both 3)."""
    number = parse_number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def parse_bool(value):
    """True/False, or the words yes/no/true/false in any case."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return {"yes": True, "true": True, "no": False, "false": False}.get(value.strip().lower())
    return None                             # anything else is ambiguous


def effective(field, parse):
    """(state, parsed value) after validating a KNOWN value."""
    if field is None:
        return "MISSING", None              # field not supplied at all
    if field.state != "KNOWN":
        return field.state, None            # MISSING / CONFLICT / INVALID pass through
    if field.value is None:
        return "MISSING", None              # KNOWN but empty counts as absent
    parsed = parse(field.value)
    return ("KNOWN", parsed) if parsed is not None else ("INVALID", None)


def _inputs(fields, names):
    """The inputs a rule used, for display next to the result."""
    return {n: {"state": fields[n].state if n in fields else "MISSING",
                "value": fields[n].value if n in fields else None,
                "human_override": getattr(fields.get(n), "human_override", False)}
            for n in names}


def _describe(name, state, field):
    """'operating_radius_miles is CONFLICT (80 vs 180)' style text for reasons."""
    text = f"{name} is {state}"
    if field is not None and field.note:
        text += f" ({field.note})"
    return text


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def rule_r1(fields):
    """R1 Required identity and effective date."""
    checks = {"business_name": parse_name, "requested_effective_date": parse_date}
    states = {n: effective(fields.get(n), parse)[0] for n, parse in checks.items()}
    missing = [n for n, s in states.items() if s == "MISSING"]
    unresolved = [n for n, s in states.items() if s in ("CONFLICT", "INVALID")]

    # FLAG for absent values takes precedence over UNKNOWN
    if missing:
        status = "FLAG"
        reason = "Required field absent: " + ", ".join(missing)
        if unresolved:
            reason += "; also unresolved: " + ", ".join(
                _describe(n, states[n], fields.get(n)) for n in unresolved)
    elif unresolved:
        status = "UNKNOWN"
        reason = "; ".join(_describe(n, states[n], fields.get(n)) for n in unresolved)
    else:
        status, reason = "PASS", "Business name and effective date are present and valid."
    return RuleResult(id="R1", name="Required identity and effective date", status=status,
                      reason=reason, inputs=_inputs(fields, checks))


def rule_r2(fields):
    """R2 Vehicle count reconciliation: declared count vs CSV row count."""
    names = ["declared_vehicle_count", "schedule_vehicle_count"]
    (d_state, declared), (s_state, schedule) = (effective(fields.get(n), parse_count) for n in names)

    # Any count that isn't a valid known number makes the comparison impossible
    if d_state != "KNOWN" or s_state != "KNOWN":
        problems = [_describe(n, s, fields.get(n))
                    for n, s in zip(names, (d_state, s_state)) if s != "KNOWN"]
        status, reason = "UNKNOWN", "; ".join(problems)
    elif declared == schedule:
        status, reason = "PASS", f"Declared count {declared} equals schedule count {schedule}."
    else:
        status, reason = "FLAG", f"Declared count {declared} differs from schedule count {schedule}."
    return RuleResult(id="R2", name="Vehicle count reconciliation", status=status,
                      reason=reason, inputs=_inputs(fields, names))


def rule_r3(fields):
    """R3 Operating radius: 150 miles or less passes."""
    name = "operating_radius_miles"
    state, radius = effective(fields.get(name), parse_number)

    # Conflicts / missing / invalid are UNKNOWN before the threshold is tested
    if state != "KNOWN":
        status, reason = "UNKNOWN", _describe(name, state, fields.get(name))
    elif radius < 0:
        status, reason = "UNKNOWN", f"Radius {radius:g} is negative."
    elif radius > RADIUS_THRESHOLD_MILES:
        status, reason = "FLAG", f"Radius {radius:g} miles is above {RADIUS_THRESHOLD_MILES}."
    else:
        status, reason = "PASS", f"Radius {radius:g} miles is {RADIUS_THRESHOLD_MILES} or less."
    return RuleResult(id="R3", name="Operating radius", status=status,
                      reason=reason, inputs=_inputs(fields, [name]))


def rule_r4(fields):
    """R4 Hazardous materials: explicit No passes, explicit Yes is flagged."""
    name = "hazardous_materials"
    state, hazmat = effective(fields.get(name), parse_bool)

    # Absence is not "No": anything but an explicit answer is UNKNOWN
    if state != "KNOWN":
        status, reason = "UNKNOWN", _describe(name, state, fields.get(name))
    elif hazmat:
        status, reason = "FLAG", "Hazardous materials: explicit Yes."
    else:
        status, reason = "PASS", "Hazardous materials: explicit No."
    return RuleResult(id="R4", name="Hazardous materials", status=status,
                      reason=reason, inputs=_inputs(fields, [name]))


def rule_r5(fields):
    """R5 Loss runs attachment: a readable loss runs report must be attached."""
    name = "loss_runs_present"
    field = fields.get(name)
    state, present = effective(field, parse_bool)
    # The attachment inventory is always shown, as the brief asks
    inventory = f" Attachments: {field.note}" if field is not None and field.note else ""

    if state != "KNOWN":
        status = "UNKNOWN"
        reason = "A potential loss runs attachment could not be read or classified." + inventory
    elif present:
        status, reason = "PASS", "A readable loss runs report is attached." + inventory
    else:
        status, reason = "FLAG", "No loss runs report is attached." + inventory
    return RuleResult(id="R5", name="Loss runs attachment", status=status,
                      reason=reason, inputs=_inputs(fields, [name]))


def evaluate_rules(fields):
    """Run R1-R5 on a dict of field name -> ResolvedField."""
    return [rule(fields) for rule in (rule_r1, rule_r2, rule_r3, rule_r4, rule_r5)]


def overall_status(results):
    """Readiness for human review only; never a coverage or binding decision."""
    return "READY_FOR_REVIEW" if all(r.status == "PASS" for r in results) else "NEEDS_REVIEW"


# ---------------------------------------------------------------------------
# loss_runs_present from the extracted files
# ---------------------------------------------------------------------------

def derive_loss_runs_present(sources):
    """Decide from the actual attachments; what the email says does not count."""
    attachments = [s for s in sources if s.kind != "email"]          # the email body isn't an attachment
    # e.g. "application.pdf (application), vehicles.csv (vehicle_schedule)"
    inventory = ", ".join(
        f"{s.source_file} ({s.document_type or 'unclassified'}{'' if s.readable else ', unreadable'})"
        for s in attachments) or "none"

    # PASS case: a readable PDF that the classifier identified as loss runs
    if any(s.kind == "pdf" and s.readable and s.document_type == "loss_runs" for s in attachments):
        return ResolvedField(state="KNOWN", value=True, note=inventory)
    # A PDF we couldn't read or classify, or a file type we can't read at all (e.g. .docx),
    # might be the loss runs report -> UNKNOWN
    if any((s.kind == "pdf" and (not s.readable or s.document_type in (None, "other")))
           or s.kind == "other"
           for s in attachments):
        return ResolvedField(state="INVALID", note=inventory)
    # Otherwise nothing like a loss runs report was attached -> FLAG
    return ResolvedField(state="KNOWN", value=False, note=inventory)
