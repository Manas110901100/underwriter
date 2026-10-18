"""Merge per-file extraction into one value per field (no LLM).

Follows field_specification.json:
- states KNOWN / MISSING / CONFLICT / INVALID
- evidence (source_file, locator, excerpt_or_observation) for every value
- missing fields record which sources were checked, without inventing a quote
- if the email and application disagree, keep both alternatives, value null,
  state CONFLICT; a missing mention is not a conflicting value
- a human override sets the value the rules use, keeping the original evidence
"""
from typing import Optional                 # nullable fields

from pydantic import BaseModel              # typed merged output

from underwriting.extraction.schema import FIELD_NAMES, contains_excerpt
from underwriting.review.normalize import US_STATES, compare_key, normalize
from underwriting.review.rules import FieldState, ResolvedField, derive_loss_runs_present

# The seven extracted fields plus the two derived ones
ALL_FIELDS = FIELD_NAMES + ["loss_runs_present", "schedule_vehicle_count"]

# Documents whose stated values are compared for the submission fields
FIELD_SOURCE_TYPES = ("application", "broker_email")

Value = Optional[str | int | float | bool]


class Evidence(BaseModel):
    """Where one source said something about a field."""
    source_file: str
    locator: Optional[str] = None
    excerpt_or_observation: Optional[str] = None
    value: Value = None                    # normalized value from this source
    status: Optional[str] = None           # what the source said: stated, not_provided, ...
    verified: bool = False                 # excerpt was found in the source text
    # The quote actually contains this value (None = nothing to check, e.g. no value)
    value_in_quote: Optional[bool] = None


class Alternative(BaseModel):
    """One side of a conflict: a value and the file that stated it."""
    value: Value
    source_file: str


class MergedField(ResolvedField):
    """A ResolvedField (what the rules read) plus everything the reviewer needs to see."""
    name: str
    evidence: list[Evidence] = []
    alternatives: list[Alternative] = []   # filled for CONFLICT
    sources_checked: list[str] = []        # files that could have stated this field
    human_override: bool = False
    # What extraction produced, kept when a human override is applied
    extracted_state: Optional[FieldState] = None
    extracted_value: Value = None
    extracted_note: Optional[str] = None


def fmt(value):
    """150.0 -> '150' and True -> 'Yes' for display."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---------------------------------------------------------------------------
# Value-in-quote check: does the quote really support the extracted value?
# ---------------------------------------------------------------------------

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
STATE_NAMES = {code: name.title() for name, code in US_STATES.items()}   # "CA" -> "California"


def written_forms(name, value):
    """Common ways a normalized value can appear in a document."""
    if isinstance(value, bool):
        return ["yes", "true"] if value else ["no", "false"]
    if isinstance(value, (int, float)):
        # "150" also matches "150.0" and "$12,500" matches "12,500" (punctuation is ignored)
        return [fmt(value), f"{value:,.0f}"] if float(value).is_integer() else [fmt(value)]
    if name == "garaging_state":
        return [value, STATE_NAMES.get(value, value)]               # "CA" or "California"
    if name == "requested_effective_date":
        year, month, day = value.split("-")
        m, d = int(month), int(day)
        month_name = MONTHS[m - 1]
        return [value, f"{month}/{day}/{year}", f"{m}/{d}/{year}",  # 2026-11-01, 11/01/2026, 11/1/2026
                f"{month_name} {d} {year}", f"{month_name[:3]} {d} {year}",   # November 1, 2026
                f"{d} {month_name} {year}"]                         # 1 November 2026
    return [value]                                                  # names, descriptions


def value_in_quote(name, value, field):
    """True if the quote supports the value, False if not, None if there is nothing to check."""
    if value is None or not field.excerpt:
        return None
    # 1. The wording the model reported is in the quote and means the same value
    if field.as_written and contains_excerpt(field.excerpt, field.as_written):
        from_wording = normalize(name, field.as_written)
        if from_wording is not None and compare_key(name, from_wording) == compare_key(name, value):
            return True
    # 2. The value itself appears in the quote in a common written form
    return any(contains_excerpt(field.excerpt, str(form)) for form in written_forms(name, value))


# ---------------------------------------------------------------------------
# Submission fields (application + email)
# ---------------------------------------------------------------------------

def candidate(name, field):
    """(state, normalized value, note) for one source, or None if it doesn't mention the field."""
    status = field.status                   # always set by the readers
    if status == "not_mentioned":
        return None                         # silence is not a candidate value
    if status == "not_provided":
        return "MISSING", None, None        # explicitly blank / N/A
    if status == "conflicting":
        return "CONFLICT", None, field.note or "the document gives different answers"
    if status in ("ambiguous", "unparseable"):
        return "INVALID", None, field.note or f"answer is {status}: '{field.as_written}'"

    # stated: normalize; fall back to the wording if the model left value empty
    value = normalize(name, field.value)
    if value is None and field.value is None:
        value = normalize(name, field.as_written)
    if value is None:
        return "INVALID", None, f"'{field.as_written or field.value}' is not a valid {name}"
    return "KNOWN", value, None


def merge_field(name, sources):
    """Combine one field across the application and the email."""
    providers = [s for s in sources if s.document_type in FIELD_SOURCE_TYPES]
    merged = MergedField(name=name, state="MISSING",
                         sources_checked=[s.source_file for s in providers])
    candidates, failed = [], []
    for s in providers:
        # A file whose model call failed was not checked at all
        if s.error or name not in s.fields:
            failed.append(s.source_file)
            continue
        field = s.fields[name]
        found = candidate(name, field)
        if found is None:
            continue
        state, value, note = found
        # Keep the evidence from every source that said anything
        merged.evidence.append(Evidence(
            source_file=s.source_file, locator=field.locator, excerpt_or_observation=field.excerpt,
            value=value, status=field.status, verified=field.excerpt_verified,
            value_in_quote=value_in_quote(name, value, field)))
        candidates.append((s.source_file, state, value, note))

    known = [(src, value) for src, state, value, _ in candidates if state == "KNOWN"]
    notes = [f"{src}: {note}" for src, _, _, note in candidates if note]
    if failed:
        notes.append("could not be checked: " + ", ".join(failed))
    # Flag values whose quote doesn't contain them, so the reviewer double-checks them
    unsupported = [e.source_file for e in merged.evidence if e.value_in_quote is False]
    if unsupported:
        notes.append("check: the value was not found in its quote from " + ", ".join(unsupported))

    # Decide the state, strongest problem first: CONFLICT > INVALID > KNOWN > MISSING
    distinct = {compare_key(name, value) for _, value in known}
    if len(distinct) > 1 or any(state == "CONFLICT" for _, state, _, _ in candidates):
        merged.state = "CONFLICT"           # value stays None: no source is preferred
        merged.alternatives = [Alternative(value=v, source_file=src) for src, v in known]
        if len(distinct) > 1:
            notes.insert(0, "sources disagree: " + " vs ".join(
                f"{fmt(v)} ({src})" for src, v in known))
    elif any(state == "INVALID" for _, state, _, _ in candidates):
        merged.state = "INVALID"
    elif known:
        merged.state, merged.value = "KNOWN", known[0][1]   # all sources agree
    elif failed:
        # Not checked is not the same as absent, so don't report it as MISSING
        merged.state = "INVALID"
    elif not providers:
        notes.append("no application or broker email was supplied")
    else:
        # Say which files explicitly said "not provided" and which were silent
        said_missing = [e.source_file for e in merged.evidence if e.status == "not_provided"]
        silent = [s for s in merged.sources_checked if s not in said_missing]
        if said_missing:
            notes.append("stated as not provided in: " + ", ".join(said_missing))
        if silent:
            notes.append("not mentioned in: " + ", ".join(silent))

    merged.note = "; ".join(notes) or None
    return merged


# ---------------------------------------------------------------------------
# Derived fields (CSV, attachments)
# ---------------------------------------------------------------------------

def merge_schedule(sources):
    """schedule_vehicle_count from the CSV file(s)."""
    name = "schedule_vehicle_count"
    csvs = [s for s in sources if s.kind == "csv"]
    merged = MergedField(name=name, state="MISSING", sources_checked=[s.source_file for s in csvs])
    if not csvs:
        merged.note = "no vehicle schedule CSV was supplied"
        return merged

    counts, errors = [], []
    for s in csvs:
        if s.error:
            # Unparseable CSV: R2 must become UNKNOWN
            errors.append(f"{s.source_file}: {s.error}")
            merged.evidence.append(Evidence(source_file=s.source_file,
                                            excerpt_or_observation=s.error))
            continue
        field = s.fields[name]
        counts.append((s.source_file, field.value))
        merged.evidence.append(Evidence(
            source_file=s.source_file, locator=field.locator, excerpt_or_observation=field.note,
            value=field.value, status="stated", verified=True))

    if errors:
        merged.state, merged.note = "INVALID", "CSV could not be parsed: " + "; ".join(errors)
    elif len({count for _, count in counts}) > 1:
        # Several CSVs with different counts: let the reviewer decide
        merged.state = "CONFLICT"
        merged.alternatives = [Alternative(value=c, source_file=src) for src, c in counts]
        merged.note = "CSVs disagree: " + " vs ".join(f"{c} ({src})" for src, c in counts)
    else:
        merged.state, merged.value = "KNOWN", counts[0][1]
    return merged


def merge_loss_runs(sources):
    """loss_runs_present from the attachment inventory, with the type evidence."""
    derived = derive_loss_runs_present(sources)
    attachments = [s.source_file for s in sources if s.kind != "email"]
    merged = MergedField(name="loss_runs_present", state=derived.state, value=derived.value,
                         note=derived.note, sources_checked=attachments)
    # Evidence 1: the inventory itself (an observation, not a quote)
    merged.evidence.append(Evidence(
        source_file="uploaded files", locator="uploaded attachment inventory",
        excerpt_or_observation=derived.note, value=derived.value, verified=True))
    # Evidence 2: the quote that made us classify a PDF as loss runs
    for s in sources:
        if s.document_type == "loss_runs" and s.document_type_evidence:
            e = s.document_type_evidence
            merged.evidence.append(Evidence(
                source_file=s.source_file, locator=e.locator, excerpt_or_observation=e.excerpt,
                value=True, status="stated", verified=e.excerpt_verified))
    return merged


def merge_sources(sources):
    """dict of field name -> MergedField for all nine fields."""
    merged = {name: merge_field(name, sources) for name in FIELD_NAMES}
    merged["loss_runs_present"] = merge_loss_runs(sources)
    merged["schedule_vehicle_count"] = merge_schedule(sources)
    return merged


# ---------------------------------------------------------------------------
# Human overrides
# ---------------------------------------------------------------------------

def apply_override(fields, name, raw_value):
    """Return a new fields dict where `name` uses the reviewer's value.

    Evidence, alternatives and the extracted state/value/note are kept.
    """
    old = fields[name]
    new = old.model_copy(deep=True)         # never modify the caller's dict
    if not old.human_override:
        # Remember what extraction produced (only the first time)
        new.extracted_state, new.extracted_value, new.extracted_note = old.state, old.value, old.note
    new.human_override = True

    # e.g. "extracted CONFLICT: sources disagree: 80 (application.pdf) vs 180 (broker_email.txt)"
    extracted = f"extracted {new.extracted_state}"
    if new.extracted_value is not None:
        extracted += f" {fmt(new.extracted_value)}"
    if new.extracted_note:
        extracted += f": {new.extracted_note}"

    value = normalize(name, raw_value)
    if value is None:
        # An invalid correction does not resolve anything
        new.state, new.value = "INVALID", None
        new.note = f"HUMAN OVERRIDE '{raw_value}' is not a valid {name} ({extracted})"
    else:
        new.state, new.value = "KNOWN", value
        new.note = f"HUMAN OVERRIDE ({extracted})"
    return {**fields, name: new}
