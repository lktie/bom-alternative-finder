#!/usr/bin/env python3
"""
remarks_generator.py — Generate human-readable remarks for each alternative part.

Rules:
  - Silence = match (no remark for identical fields)
  - Only differences get a remark
  - Power (resistors): same or higher is acceptable, but note if different
  - All other fields: note the original vs alternative value

Usage:
    from remarks_generator import generate_remarks
    note = generate_remarks(original, alternative, comp_type)
"""

# ── Power rating order (ascending) ────────────────────────────────────────────

POWER_RATING_ORDER = [
    "0.05w, 1/20w", "0.063w, 1/16w", "0.1w, 1/10w", "0.125w, 1/8w",
    "0.25w, 1/4w", "0.33w, 1/3w", "0.5w, 1/2w", "0.75w, 3/4w", "1w", "2w"
]

def _power_rank(power_str: str) -> int:
    """Return index in POWER_RATING_ORDER, or -1 if not found."""
    p = power_str.strip().lower()
    for i, rating in enumerate(POWER_RATING_ORDER):
        if rating in p:
            return i
    return -1


# ── Field-level remark builders ───────────────────────────────────────────────

def _remark_power(orig_val: str, cand_val: str) -> str | None:
    """
    Power is acceptable if same or higher.
    Only generates a remark if different.
    """
    if orig_val.strip().lower() == cand_val.strip().lower():
        return None  # identical, no remark

    orig_rank = _power_rank(orig_val)
    cand_rank = _power_rank(cand_val)

    if orig_rank == -1 or cand_rank == -1:
        return f"Power: original {orig_val}, alternative {cand_val} (verify compatibility)"

    if cand_rank > orig_rank:
        return f"Power: original {orig_val}, alternative {cand_val} (higher rating — acceptable)"
    elif cand_rank == orig_rank:
        return None  # same rank different string representation, treat as match
    else:
        # Lower power — should not happen since fetcher filters these out, but handle defensively
        return f"Power: original {orig_val}, alternative {cand_val} (LOWER rating — review required)"


def _remark_generic(field: str, orig_val: str, cand_val: str) -> str | None:
    """Generic remark for any field difference."""
    if orig_val.strip().lower() == cand_val.strip().lower():
        return None
    return f"{field.replace('_', ' ').title()}: original '{orig_val}', alternative '{cand_val}'"


# ── Field labels for display ──────────────────────────────────────────────────

FIELD_LABELS = {
    "resistance":    "Resistance",
    "capacitance":   "Capacitance",
    "tolerance":     "Tolerance",
    "power":         "Power",
    "composition":   "Composition",
    "temp_coefficient": "Temp Coefficient",
    "package":       "Package",
    "voltage_rating": "Voltage Rating",
    "dielectric":    "Dielectric",
    "operating_temp": "Operating Temp",
    "features":      "Features",
}

# Fields to compare per component type
COMPARE_FIELDS = {
    "resistor":  ["resistance", "tolerance", "power", "package", "composition", "temp_coefficient"],
    "capacitor": ["capacitance", "tolerance", "voltage_rating", "dielectric", "package", "operating_temp"],
    "unknown":   ["package"],
}


# ── Main function ─────────────────────────────────────────────────────────────

def generate_remarks(original: dict, alternative: dict, comp_type: str) -> str:
    """
    Compare alternative specs against original and generate a remark note.

    Args:
        original:    extracted spec dict from spec_extractor
        alternative: extracted spec dict from spec_extractor
        comp_type:   'resistor', 'capacitor', or 'unknown'

    Returns:
        A single remark string. Empty string if all specs match.
    """
    orig_specs = original.get("specs", {})
    alt_specs  = alternative.get("specs", {})
    fields     = COMPARE_FIELDS.get(comp_type, COMPARE_FIELDS["unknown"])

    remarks = []

    for field in fields:
        orig_val = orig_specs.get(field, "")
        cand_val = alt_specs.get(field, "")

        # Skip if original has no value for this field
        if not orig_val or orig_val == "-":
            continue

        if field == "power":
            remark = _remark_power(orig_val, cand_val)
        else:
            remark = _remark_generic(
                FIELD_LABELS.get(field, field),
                orig_val,
                cand_val or "N/A"
            )

        if remark:
            remarks.append(remark)

    if not remarks:
        return "All key specs match original."

    return "; ".join(remarks)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import requests
    from spec_extractor import extract_specs
    from alternatives_fetcher import get_token, search_mpn, get_substitutions, fetch_alternatives

    CLIENT_ID     = os.getenv("DIGIKEY_CLIENT_ID")
    CLIENT_SECRET = os.getenv("DIGIKEY_CLIENT_SECRET")

    token = get_token()
    print("[token] OK\n")

    TEST_MPNS = [
        "CRCW080510K0FKEA",
        "GRM188R71C104KA01D",
    ]

    for mpn in TEST_MPNS:
        print(f"\n{'═'*60}")
        print(f"  {mpn}")
        print(f"{'═'*60}")

        results = fetch_alternatives(token, mpn)
        if "error" in results:
            print(f"  ERROR: {results['error']}")
            continue

        original  = results["original"]
        comp_type = original["component_type"]

        print(f"  Original: {original['mpn']} ({original['manufacturer']})\n")

        for i, alt in enumerate(results["alternatives"], 1):
            note = generate_remarks(original, alt, comp_type)
            print(f"  [{i}] {alt['mpn']} — {alt['manufacturer']}")
            print(f"       Note: {note}")
