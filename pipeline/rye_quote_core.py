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
    "siteName", "mpxn", "updatedEac", "supplyStartDate",
    "unitRate", "dayRate", "nightRate", "weekendRate", "standingCharge",
    "capacityCharge", "networkCharge", "meterCharge", "kva",
]

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
