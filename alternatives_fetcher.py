#!/usr/bin/env python3
"""
alternatives_fetcher.py — Fetch and filter Digikey-curated substitutes.

Strategy:
  1. Search MPN → extract specs (via spec_extractor)
  2. Call Substitutions endpoint using the Cut Tape DigiKey PN
  3. Extract specs for each substitute
  4. Filter: must match package + tolerance + value + dielectric/voltage/power
  5. Return up to 10 matches

Usage (standalone test):
    export DIGIKEY_CLIENT_ID="..."
    export DIGIKEY_CLIENT_SECRET="..."
    python alternatives_fetcher.py
"""

import os
import json
import requests
from spec_extractor import extract_specs, format_specs

# ── Config ────────────────────────────────────────────────────────────────────

CLIENT_ID     = os.getenv("DIGIKEY_CLIENT_ID")
CLIENT_SECRET = os.getenv("DIGIKEY_CLIENT_SECRET")
SANDBOX       = False
BASE_URL      = "https://sandbox-api.digikey.com" if SANDBOX else "https://api.digikey.com"
TOKEN_URL     = f"{BASE_URL}/v1/oauth2/token"
SEARCH_URL    = f"{BASE_URL}/products/v4/search/keyword"
SUBS_URL      = f"{BASE_URL}/products/v4/search/{{dk_pn}}/substitutions"

MAX_ALTERNATIVES = 10

# Manufacturers to deprioritise — only included if slots remain after all others
DEPRIORITY_MANUFACTURERS = {
    "vishay vitramon",
}

# Manufacturers to exclude from alternatives entirely (none currently)
EXCLUDE_MANUFACTURERS = set()

# ── Critical match fields per component type ──────────────────────────────────
# All listed fields must match exactly (case-insensitive) to pass the filter

MATCH_FIELDS = {
    "resistor":  ["package", "tolerance", "resistance"],  # power checked separately (same or better)
    "capacitor": ["package", "tolerance", "capacitance", "voltage_rating", "dielectric"],
    "unknown":   ["package"],
}

# Power ratings in ascending order for "same or better" comparison
POWER_RATING_ORDER = [
    "0.05w, 1/20w", "0.063w, 1/16w", "0.1w, 1/10w", "0.125w, 1/8w",
    "0.25w, 1/4w", "0.33w, 1/3w", "0.5w, 1/2w", "0.75w, 3/4w", "1w", "2w"
]

def power_ok(orig_power: str, cand_power: str) -> bool:
    """Candidate power must be >= original (same or better)."""
    orig = orig_power.strip().lower()
    cand = cand_power.strip().lower()
    if not orig or orig == "-":
        return True  # no constraint
    try:
        orig_idx = next(i for i, p in enumerate(POWER_RATING_ORDER) if p in orig)
        cand_idx = next(i for i, p in enumerate(POWER_RATING_ORDER) if p in cand)
        return cand_idx >= orig_idx
    except StopIteration:
        return orig == cand  # fallback to exact match if not in our list

# ── API helpers ───────────────────────────────────────────────────────────────

def get_token():
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type":    "client_credentials",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def auth_headers(token):
    return {
        "X-DIGIKEY-Client-Id": CLIENT_ID,
        "Authorization":       f"Bearer {token}",
        "Content-Type":        "application/json",
        "Accept":              "application/json",
    }


# Mutable token store so refresh propagates across all callers
_token_store = {"token": None}


def refresh_token():
    """Refresh and store token globally."""
    _token_store["token"] = get_token()
    print("[token] Refreshed")
    return _token_store["token"]


def current_token():
    return _token_store["token"]


import time as _time

def _request_with_refresh(method, url, **kwargs):
    """Make a request, auto-refreshing token on 401, backing off on 429."""
    token = _token_store["token"]
    kwargs.setdefault("headers", {}).update(auth_headers(token))
    resp = getattr(requests, method)(url, **kwargs)

    if resp.status_code == 401:
        token = refresh_token()
        kwargs["headers"].update(auth_headers(token))
        resp = getattr(requests, method)(url, **kwargs)

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 10))
        if retry_after > 120:
            # Quota-level limit, not a short throttle — don't block, fail fast
            print(f"  [rate limit] 429 — Retry-After={retry_after}s exceeds quota cap, aborting")
            resp.raise_for_status()
        print(f"  [rate limit] 429 — waiting {retry_after}s")
        _time.sleep(retry_after)
        resp = getattr(requests, method)(url, **kwargs)

    resp.raise_for_status()
    return resp


def search_mpn(token, mpn):
    _time.sleep(0.3)  # throttle to avoid 429s on bulk runs
    resp = _request_with_refresh(
        "post", SEARCH_URL,
        json={"Keywords": mpn, "Limit": 5, "Offset": 0},
    )
    return resp.json()


def get_substitutions(token, dk_pn):
    url = SUBS_URL.format(dk_pn=requests.utils.quote(dk_pn, safe=""))
    resp = _request_with_refresh("get", url)
    return resp.json()

# ── Filtering ─────────────────────────────────────────────────────────────────

def normalize(value: str) -> str:
    """Lowercase and strip for comparison."""
    return value.strip().lower() if value else ""


def specs_match(original: dict, candidate: dict, comp_type: str) -> tuple[bool, list]:
    """
    Compare candidate specs against original.
    Returns (passed: bool, mismatches: list of str).
    """
    fields = MATCH_FIELDS.get(comp_type, MATCH_FIELDS["unknown"])
    mismatches = []

    orig_specs = original.get("specs", {})
    cand_specs = candidate.get("specs", {})

    for field in fields:
        orig_val = normalize(orig_specs.get(field, ""))
        cand_val = normalize(cand_specs.get(field, ""))

        # Skip comparison if original doesn't have this field
        if not orig_val or orig_val == "-":
            continue

        if orig_val != cand_val:
            mismatches.append(
                f"{field}: expected '{orig_specs.get(field)}', got '{cand_specs.get(field, 'N/A')}'"
            )

    # For resistors: power must be same or better (not exact match)
    if comp_type == "resistor":
        orig_power = orig_specs.get("power", "")
        cand_power = cand_specs.get("power", "")
        if orig_power and orig_power != "-" and not power_ok(orig_power, cand_power):
            mismatches.append(f"power: '{orig_power}' not met by '{cand_power}'")

    return (len(mismatches) == 0, mismatches)

# ── Main fetcher ──────────────────────────────────────────────────────────────


# ── Parametric search fallback ────────────────────────────────────────────────

def _resistance_to_dk(r: str) -> str:
    """Convert API resistance value to Digikey description format.
    '2 kOhms' → '2K OHM', '100 Ohms' → '100 OHM', '1 MOhms' → '1M OHM'
    '0 Ohms' → '0 OHM'
    Order matters: check MOhms before kOhms, both before plain Ohms.
    """
    r = r.strip()
    rl = r.lower()
    if "mohms" in rl or "mohm" in rl:
        val = rl.replace("mohms","").replace("mohm","").strip()
        return f"{val}M OHM"
    elif "kohms" in rl or "kohm" in rl:
        val = rl.replace("kohms","").replace("kohm","").strip()
        # Preserve original case for the numeric value
        return f"{val}K OHM"
    else:
        val = rl.replace("ohms","").replace("ohm","").strip()
        return f"{val} OHM"


def _capacitance_to_dk(c: str) -> str:
    """Convert API capacitance value to Digikey description format.
    '0.1 µF' → '0.1UF', '100 pF' → '100PF', '10 nF' → '10000PF'
    """
    c = c.strip()
    if "µf" in c.lower() or "uf" in c.lower():
        val = c.lower().replace("µf","").replace("uf","").strip()
        return f"{val}UF"
    elif "nf" in c.lower():
        val = c.lower().replace("nf","").strip()
        return f"{val}NF"
    elif "pf" in c.lower():
        val = c.lower().replace("pf","").strip()
        return f"{val}PF"
    return c


def parametric_search_fallback(token, original: dict, comp_type: str,
                                exclude_mpns: set, limit: int = 50) -> list:
    """
    Search Digikey using description-style keywords matching Digikey's own
    product description format. Much more reliable than raw spec values.
    """
    specs = original.get("specs", {})

    if comp_type == "resistor":
        resistance_raw = specs.get("resistance", "")
        tolerance      = specs.get("tolerance", "").replace("±","")
        package        = specs.get("package", "").split(" ")[0]
        # Special case for jumper/0-ohm resistors
        if "0" in resistance_raw and ("jumper" in tolerance.lower() or resistance_raw.strip().startswith("0")):
            keyword = f"RES 0 OHM JUMPER {package}"
        else:
            resistance = _resistance_to_dk(resistance_raw)
            keyword    = f"RES {resistance} {tolerance} {package}"
    elif comp_type == "capacitor":
        capacitance = _capacitance_to_dk(specs.get("capacitance", ""))
        voltage     = specs.get("voltage_rating", "")
        dielectric  = specs.get("dielectric", "")
        package     = specs.get("package", "").split(" ")[0]
        keyword     = f"CAP CER {capacitance} {voltage} {dielectric} {package}"
    else:
        return []

    print(f"  [parametric] keyword: {keyword}")

    payload = {
        "Keywords": keyword,
        "Limit":    limit,
        "Offset":   0,
        "FilterOptionsRequest": {
            "ManufacturerFilter": [],
            "StatusFilter": [{"Id": 0, "Value": "Active"}],
        },
    }

    try:
        resp = _request_with_refresh("post", SEARCH_URL, json=payload)
        result = resp.json()
    except Exception as e:
        print(f"  [warn] parametric search failed: {e}")
        return []

    products = (result.get("Products") or []) + (result.get("ExactMatches") or [])
    print(f"  [parametric] {len(products)} results (total matches: {result.get('ProductsCount', 0)})")

    candidates = []
    seen = set()

    for product in products:
        mpn = product.get("ManufacturerProductNumber", "")
        if not mpn or mpn in exclude_mpns or mpn in seen:
            continue
        seen.add(mpn)

        status = product.get("ProductStatus", {}).get("Status", "Active")
        if status.lower() not in ("active", ""):
            continue

        candidate = extract_specs(product)
        passed, _ = specs_match(original, candidate, comp_type)
        if passed:
            candidates.append(candidate)

    return candidates

def fetch_alternatives(token, mpn: str) -> dict:
    """
    Full pipeline for one MPN:
      search → extract original specs → get substitutes → filter → return results

    Returns:
        {
            "original": extracted spec dict,
            "alternatives": [ extracted spec dict + "match_mismatches" key, ... ],
            "filtered_out": int,
            "total_substitutes": int,
        }
    """
    # Step 1: Search and extract original
    search_result = search_mpn(token, mpn)
    exact   = search_result.get("ExactMatches", [])
    products = search_result.get("Products", [])
    product = exact[0] if exact else (products[0] if products else None)

    if not product:
        return {"error": f"MPN not found: {mpn}"}

    original = extract_specs(product)
    comp_type = original["component_type"]
    dk_pn = original["digikey_pn"]

    if not dk_pn:
        return {"error": f"Could not resolve DigiKey PN for {mpn}"}

    print(f"  [original]  {original['mpn']} ({original['manufacturer']}) — {comp_type}")
    print(f"  [dk_pn]     {dk_pn}")

    # Step 2: Get substitutions
    subs_result = get_substitutions(token, dk_pn)
    substitutes = subs_result.get("ProductSubstitutes", [])
    total = subs_result.get("ProductSubstitutesCount", len(substitutes))
    print(f"  [subs]      {total} substitutes returned by Digikey")

    # Step 3: For each substitute, fetch full parametric data via KeywordSearch,
    # then filter against original specs.
    # ProductSubstitutes only returns a lightweight object (no Parameters/Category),
    # so we must search each substitute MPN individually to get full specs.
    # Collect all passing candidates first, then apply diversity ranking
    passed_candidates = []
    seen_mpns = set()
    filtered_out = 0

    for sub in substitutes:
        sub_mpn = sub.get("ManufacturerProductNumber", "")
        if not sub_mpn or sub_mpn == original["mpn"]:
            continue
        if sub_mpn in seen_mpns:
            continue
        seen_mpns.add(sub_mpn)

        try:
            sub_search = search_mpn(token, sub_mpn)
        except Exception as e:
            print(f"  [warn] search failed for {sub_mpn}: {e}")
            continue

        sub_exact = sub_search.get("ExactMatches", [])
        sub_products = sub_search.get("Products", [])
        sub_product = sub_exact[0] if sub_exact else (sub_products[0] if sub_products else None)

        if not sub_product:
            continue

        # Skip obsolete parts
        status = sub_product.get("ProductStatus", {}).get("Status", "Active")
        if status.lower() not in ("active", ""):
            filtered_out += 1
            continue

        candidate = extract_specs(sub_product)
        candidate["substitute_type"] = sub.get("SubstituteType", "")
        candidate["quantity_available"] = sub.get("QuantityAvailable", 0)

        passed, mismatches = specs_match(original, candidate, comp_type)

        if passed:
            candidate["match_mismatches"] = []
            passed_candidates.append(candidate)
        else:
            filtered_out += 1

    # Apply manufacturer diversity ranking:
    # 1. One per manufacturer (excluding depriority), preserving order
    # 2. Fill remaining slots with depriority manufacturers
    # 3. If still slots remain, add same-manufacturer duplicates (shouldn't happen after dedup)
    orig_mfr = original["manufacturer"].lower()
    seen_mfrs = set()
    priority = []    # unique manufacturer, non-depriority, non-same-as-original
    same_mfr = []    # same manufacturer as original
    depriority = []  # depriority manufacturers

    for c in passed_candidates:
        mfr = c["manufacturer"].lower()
        if mfr in DEPRIORITY_MANUFACTURERS:
            depriority.append(c)
        elif mfr == orig_mfr:
            same_mfr.append(c)
        elif mfr not in seen_mfrs:
            seen_mfrs.add(mfr)
            priority.append(c)

    # Combine: diverse first, same-mfr next, depriority last
    ranked = priority + same_mfr + depriority
    alternatives = ranked[:MAX_ALTERNATIVES]

    # Parametric fallback — if still below target, search by description keyword
    if len(alternatives) < MAX_ALTERNATIVES:
        already_seen = {a["mpn"] for a in alternatives} | {original["mpn"]}
        print(f"  [parametric] only {len(alternatives)} found, running fallback...")
        fallback = parametric_search_fallback(token, original, comp_type, already_seen)

        seen_fallback_mfrs = {a["manufacturer"].lower() for a in alternatives}

        for c in fallback:
            if len(alternatives) >= MAX_ALTERNATIVES:
                break
            mfr = c["manufacturer"].lower()
            if mfr in DEPRIORITY_MANUFACTURERS:
                continue
            if mfr not in seen_fallback_mfrs:
                seen_fallback_mfrs.add(mfr)
                c["match_mismatches"] = []
                alternatives.append(c)
                print(f"  [fallback pass] {c['mpn']} ({c['manufacturer']})")

        # Fill remaining with depriority if still needed
        if len(alternatives) < MAX_ALTERNATIVES:
            for c in fallback:
                if len(alternatives) >= MAX_ALTERNATIVES:
                    break
                if c["manufacturer"].lower() in DEPRIORITY_MANUFACTURERS:
                    c["match_mismatches"] = []
                    alternatives.append(c)

    for alt in alternatives:
        print(f"  [pass] {alt['mpn']} ({alt['manufacturer']})")
    print(f"  [filter]    {len(alternatives)} passed, {filtered_out} filtered out")

    return {
        "original":         original,
        "alternatives":     alternatives,
        "filtered_out":     filtered_out,
        "total_substitutes": total,
    }

# ── Formatting ────────────────────────────────────────────────────────────────

def print_results(results: dict):
    if "error" in results:
        print(f"  ERROR: {results['error']}")
        return

    orig = results["original"]
    alts = results["alternatives"]

    print(f"\n{'━'*60}")
    print(f"  ORIGINAL PART")
    print(f"{'━'*60}")
    print(format_specs(orig))

    print(f"\n{'━'*60}")
    print(f"  ALTERNATIVES ({len(alts)} found, target {MAX_ALTERNATIVES})")
    print(f"{'━'*60}")

    if not alts:
        print("  No alternatives passed all spec filters.")
        print("  Consider relaxing filter criteria (e.g. allow different power rating).")
        return

    for i, alt in enumerate(alts, 1):
        print(f"\n  [{i}] {alt['mpn']} — {alt['manufacturer']}")
        print(f"      DK PN:   {alt['digikey_pn']}")
        print(f"      URL:     {alt['product_url']}")
        print(f"      Specs:")
        for k, v in alt["specs"].items():
            print(f"        {k:<22} {v}")

# ── Standalone test ───────────────────────────────────────────────────────────

TEST_MPNS = [
    "CRCW080510K0FKEA",    # Vishay resistor 10K 1% 0805 1/4W
    "GRM188R71C104KA01D",  # Murata cap 100nF 0603 X7R 16V
]


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: set DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET")
        return

    token = get_token()
    print(f"[token] OK\n")

    for mpn in TEST_MPNS:
        print(f"\n{'═'*60}")
        print(f"  Processing: {mpn}")
        print(f"{'═'*60}")
        results = fetch_alternatives(token, mpn)
        print_results(results)


if __name__ == "__main__":
    main()
