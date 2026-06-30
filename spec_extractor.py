#!/usr/bin/env python3
# datasheet_resolver imported lazily to avoid circular deps
"""
spec_extractor.py — Extract key specs from a Digikey API v4 product object.

Supports: Chip Resistors, Capacitors (ceramic, electrolytic, film, etc.)
Unknown component types return whatever parameters come back, unfiltered.

Usage (standalone test):
    export DIGIKEY_CLIENT_ID="..."
    export DIGIKEY_CLIENT_SECRET="..."
    python spec_extractor.py
"""

import os
import json
import requests

# ── Spec maps ─────────────────────────────────────────────────────────────────
# Keys = ParameterText values returned by Digikey API
# Values = friendly label used in our output dict

RESISTOR_PARAMS = {
    "Resistance":            "resistance",
    "Tolerance":             "tolerance",
    "Power (Watts)":         "power",
    "Composition":           "composition",
    "Temperature Coefficient": "temp_coefficient",
    "Package / Case":        "package",
}

CAPACITOR_PARAMS = {
    "Capacitance":           "capacitance",
    "Tolerance":             "tolerance",
    "Voltage - Rated":       "voltage_rating",
    "Voltage Rating - AC":   "voltage_rating_ac",
    "Voltage Rating - DC":   "voltage_rating_dc",
    "Temperature Coefficient": "temp_coefficient",
    "Material - Dielectric": "dielectric",           # film caps
    "Dielectric Characteristic": "dielectric",       # ceramic caps (C0G, X7R, etc.)
    "Features":              "features",
    "Package / Case":        "package",
    "Operating Temperature": "operating_temp",
}

# Category name fragments → which param map to use
CATEGORY_MAP = {
    "resistor":   RESISTOR_PARAMS,
    "capacitor":  CAPACITOR_PARAMS,
}

# ── Core extractor ────────────────────────────────────────────────────────────

def detect_component_type(product: dict) -> str:
    """
    Determine component type from category info in the product object.
    Returns 'resistor', 'capacitor', or 'unknown'.
    """
    # Check child category name first (more specific), then parent
    category = product.get("Category", {})
    names_to_check = []

    for child in category.get("ChildCategories", []):
        names_to_check.append(child.get("Name", "").lower())
    names_to_check.append(category.get("Name", "").lower())

    for name in names_to_check:
        for keyword, comp_type in CATEGORY_MAP.items():
            if keyword in name:
                return keyword

    return "unknown"


def extract_specs(product: dict) -> dict:
    """
    Given a single product object from Digikey v4 KeywordSearch response,
    return a clean spec dict with only the relevant parameters.

    Returns:
        {
            "mpn": str,
            "manufacturer": str,
            "component_type": str,
            "digikey_pn": str,          # Cut Tape variant preferred
            "product_url": str,
            "specs": { label: value, ... }
        }
    """
    comp_type = detect_component_type(product)
    param_map = CAPACITOR_PARAMS if comp_type == "capacitor" else RESISTOR_PARAMS
    # For unknown types, grab all params
    if comp_type == "unknown":
        param_map = None

    # Build specs dict from Parameters array
    specs = {}
    spec_value_ids = {}  # stores ValueId for each spec field (needed for parametric search)
    for param in product.get("Parameters", []):
        param_text  = param.get("ParameterText", "")
        value_text  = param.get("ValueText", "")
        value_id    = param.get("ValueId", "")

        if param_map is None:
            specs[param_text] = value_text
        elif param_text in param_map:
            label = param_map[param_text]
            if label not in specs or specs[label] == "-":
                specs[label] = value_text
                spec_value_ids[label] = value_id

    # Ceramic cap quirk: Digikey stores dielectric class (X7R, C0G, X5R, etc.)
    # under "Temperature Coefficient" — reclassify if it looks like a dielectric code
    if comp_type == "capacitor" and "temp_coefficient" in specs:
        DIELECTRIC_CODES = {"X7R", "X5R", "X8R", "C0G", "NP0", "Y5V", "Z5U", "X7S", "X6S"}
        val = specs["temp_coefficient"].strip().upper()
        if val in DIELECTRIC_CODES:
            # Rebuild dict with dielectric in correct position (after voltage_rating)
            reordered = {}
            for k, v in specs.items():
                if k == "temp_coefficient":
                    reordered["dielectric"] = val
                else:
                    reordered[k] = v
            specs = reordered

    # Get Cut Tape DigiKey PN (preferred for substitution lookup)
    dk_pn = None
    for variant in product.get("ProductVariations", []):
        pkg = variant.get("PackageType", {}).get("Name", "")
        if "Cut Tape" in pkg:
            dk_pn = variant.get("DigiKeyProductNumber")
            break
    if not dk_pn and product.get("ProductVariations"):
        dk_pn = product["ProductVariations"][0].get("DigiKeyProductNumber")

    from datasheet_resolver import resolve_datasheet_url
    mpn_val      = product.get("ManufacturerProductNumber", "")
    mfr_val      = product.get("Manufacturer", {}).get("Name", "")
    status_val   = product.get("ProductStatus", {}).get("Status", "Active")
    dk_ds_url    = product.get("DatasheetUrl", "")
    resolved_url, needs_verify = resolve_datasheet_url(mpn_val, mfr_val, dk_ds_url, status_val)

    return {
        "mpn":                  mpn_val,
        "manufacturer":         mfr_val,
        "component_type":       comp_type,
        "digikey_pn":           dk_pn,
        "product_url":          product.get("ProductUrl", ""),
        "datasheet_url":        resolved_url,
        "datasheet_verify":     needs_verify,
        "specs":                specs,
        "spec_value_ids":       spec_value_ids,
    }


def format_specs(extracted: dict) -> str:
    """Pretty-print an extracted spec dict for console output."""
    lines = [
        f"  MPN:        {extracted['mpn']}",
        f"  Mfr:        {extracted['manufacturer']}",
        f"  Type:       {extracted['component_type']}",
        f"  DK PN:      {extracted['digikey_pn']}",
        f"  URL:        {extracted['product_url']}",
        f"  Specs:",
    ]
    for k, v in extracted["specs"].items():
        lines.append(f"    {k:<22} {v}")
    return "\n".join(lines)


# ── Standalone test ───────────────────────────────────────────────────────────

CLIENT_ID     = os.getenv("DIGIKEY_CLIENT_ID")
CLIENT_SECRET = os.getenv("DIGIKEY_CLIENT_SECRET")
SANDBOX       = False
BASE_URL      = "https://sandbox-api.digikey.com" if SANDBOX else "https://api.digikey.com"
TOKEN_URL     = f"{BASE_URL}/v1/oauth2/token"
SEARCH_URL    = f"{BASE_URL}/products/v4/search/keyword"

# Test both a resistor and a capacitor
TEST_MPNS = [
    "CRCW080510K0FKEA",   # Vishay resistor 10K 1% 0805
    "GRM188R71C104KA01D", # Murata ceramic cap 100nF 0402 X7R 16V
]


def get_token():
    resp = requests.post(
        TOKEN_URL,
        data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "client_credentials"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def search_mpn(token, mpn):
    resp = requests.post(
        SEARCH_URL,
        headers={
            "X-DIGIKEY-Client-Id": CLIENT_ID,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"Keywords": mpn, "Limit": 5, "Offset": 0},
    )
    resp.raise_for_status()
    return resp.json()


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: set DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET")
        return

    token = get_token()
    print(f"[token] OK\n")

    for mpn in TEST_MPNS:
        print(f"{'─'*60}")
        print(f"Testing MPN: {mpn}")
        result = search_mpn(token, mpn)

        exact = result.get("ExactMatches", [])
        products = result.get("Products", [])
        product = exact[0] if exact else (products[0] if products else None)

        if not product:
            print(f"  [!] No results for {mpn}")
            continue

        extracted = extract_specs(product)
        print(format_specs(extracted))
        print()


if __name__ == "__main__":
    main()
