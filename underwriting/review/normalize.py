"""Per-field normalization from field_specification.json (no LLM).

normalize(name, value) returns the normalized value, or None if the value is
not valid for that field. compare_key(name, value) is what conflict detection
compares, so harmless differences (case, 'California' vs 'CA', 150 vs 150.0,
a trailing period) are not conflicts.
"""
from underwriting.review.rules import parse_bool, parse_count, parse_date, parse_number

# Full state names -> USPS codes (spec only requires California, the rest is free)
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
STATE_CODES = set(US_STATES.values())       # valid two-letter codes


def normalize_text(value):
    """Trim and collapse inner whitespace; original wording and case are kept."""
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def normalize_state(value):
    """'California' / 'CA' / 'ca' -> 'CA'."""
    text = normalize_text(value)
    if text is None:
        return None
    text = text.rstrip(".")                 # "Calif." style trailing dot
    if text.upper() in STATE_CODES:
        return text.upper()                 # already a code
    return US_STATES.get(text.lower())      # full name -> code, else None (invalid)


def normalize_radius(value):
    """Miles as a number; negative is not a valid radius."""
    number = parse_number(value)
    return number if number is not None and number >= 0 else None


# Which normalizer each field uses
NORMALIZERS = {
    "business_name": normalize_text,
    "requested_effective_date": parse_date,
    "garaging_state": normalize_state,
    "operations_description": normalize_text,
    "declared_vehicle_count": parse_count,
    "operating_radius_miles": normalize_radius,
    "hazardous_materials": parse_bool,
    "loss_runs_present": parse_bool,
    "schedule_vehicle_count": parse_count,
}


def normalize(name, value):
    """Normalized value for this field, or None if it isn't valid."""
    if value is None:
        return None
    return NORMALIZERS[name](value)


def compare_key(name, value):
    """Names and descriptions compare case-insensitively, ignoring trailing punctuation."""
    if name in ("business_name", "operations_description"):
        return value.casefold().rstrip(".!;, ")
    return value                            # other fields are already normalized
