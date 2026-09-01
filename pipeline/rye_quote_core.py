#!/usr/bin/env python3
"""
rye_quote_core.py — the shared spine of the RYE quote pipeline.

Single home for the logic that MUST be identical everywhere a quote value is
read: the numeric parser and the fixed target-field list. The deterministic
extractor (process_quote.py) and the cost engine (build_dashboard.py) both
import from here, so the two can never disagree on what a cell means — the
"logic divergence" risk called out in the build spec is closed by construction,
not by discipline. map_headers.py imports the field list too, so there is one
authoritative definition of RYE's target schema.

If a parsing rule ever needs to change, it changes HERE, once.
"""
import re

# RYE's fixed target schema, in order. The processed-CSV header order, the
# canonical line fields, and a mapping's column keys all derive from this list.
TARGET_FIELDS = [
    "siteName", "mpxn", "updatedEac", "supplyStartDate", "supplyEndDate",
    "unitRate", "dayRate", "nightRate", "weekendRate", "standingCharge",
    "capacityCharge", "networkCharge", "meterCharge", "kva",
]

# Standing consumption-split defaults. These are HARDCODED, not per-tender inputs
# — a team member never sets them per quote (too much friction, and getting them
# wrong silently mis-ranks offers). Change here, once, if the standing basis ever
# moves.
#   DAY_SPLIT_DEFAULT      day fraction for two-rate (day/night) meters, on the
#                          Economy-7 basis (≈17h day : 7h night). Night = 1 - day.
#   WEEKEND_SPLIT_DEFAULT  flat-week weekend share (Sat+Sun = 2/7 of the week),
#                          applied ONLY to offers that actually quote a weekend
#                          rate band; the weekday remainder is then split day/night
#                          by DAY_SPLIT_DEFAULT (see build_dashboard.compute_offer).
DAY_SPLIT_DEFAULT = 0.7
WEEKEND_SPLIT_DEFAULT = 2 / 7

# Cell contents that mean "blank / not quoted", ignoring surrounding decoration.
_BLANKS = {"", "n/a", "na", "-", "—", "tbc", "none", "null"}


def parse_num(raw):
    """Parse a quote cell into a float, tolerating currency/unit decoration.

    THE one numeric parser for the whole pipeline. Strips £, commas and
    whitespace and a trailing unit token (p/kWh, p/day, pence, kwh, kva, p),
    then floats it. Returns None for blanks / n-a markers, and None (never an
    exception) for anything that still won't parse.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in _BLANKS:
        return None
    s = re.sub(r"[£,\s]", "", s)
    s = re.sub(r"(?i)(p/kwh|p/day|pence|kwh|kva|p)$", "", s)
    try:
        return float(s)
    except ValueError:
        return None


# --- Fuel helpers ---------------------------------------------------------
# Fuel is a per-meter fact (electricity or gas). A single-fuel tender doesn't
# carry it (the whole tender is `utility`); it only matters when one tender mixes
# fuels, where each fuel must be costed and benchmarked on its own -- gas unit
# rates are far lower, so a blended rate/saving across fuels is meaningless.
_FUEL_CANON = {
    "electricity": "electricity", "elec": "electricity", "electric": "electricity",
    "power": "electricity", "e": "electricity", "ele": "electricity",
    "gas": "gas", "g": "gas", "natural gas": "gas", "ng": "gas",
}


def normalise_fuel(raw):
    """Canonicalise a fuel label to 'electricity' | 'gas' | None.

    Tolerant of the common spellings/abbreviations suppliers use. Returns None for
    blanks and anything unrecognised (RYE only tenders electricity and gas), so an
    odd label is dropped rather than mistaken for a third fuel.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in _FUEL_CANON:
        return _FUEL_CANON[s]
    if s.startswith("elec"):
        return "electricity"
    if s.startswith("gas"):
        return "gas"
    return None


def infer_fuel_from_mpxn(mpxn):
    """Best-effort fuel from the meter-point format, or None when unsure.

    A safety net for detecting a mixed gas+electricity tender even when no Fuel
    column was mapped: an electricity MPAN core is 13 digits; a gas MPRN is a
    shorter all-digit id (6-10). Only trusts a purely numeric id -- a header label
    or placeholder returns None rather than a guess.
    """
    s = str(mpxn or "").strip()
    if not s.isdigit():
        return None
    n = len(s)
    if n == 13:
        return "electricity"
    if 6 <= n <= 10:
        return "gas"
    return None
