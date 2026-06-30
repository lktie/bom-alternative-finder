#!/usr/bin/env python3
"""
Digikey API v4 - Proof of Concept
Tests: OAuth2 token → KeywordSearch → Substitutions

Usage:
    export DIGIKEY_CLIENT_ID="your_client_id"
    export DIGIKEY_CLIENT_SECRET="your_client_secret"
    python digikey_api_poc.py
"""

import os
import json
import requests

# ── Config ────────────────────────────────────────────────────────────────────

CLIENT_ID     = os.getenv("DIGIKEY_CLIENT_ID")
CLIENT_SECRET = os.getenv("DIGIKEY_CLIENT_SECRET")

SANDBOX = False  # flip to False when you move to production

BASE_URL  = "https://sandbox-api.digikey.com" if SANDBOX else "https://api.digikey.com"
TOKEN_URL = f"{BASE_URL}/v1/oauth2/token"
SEARCH_URL        = f"{BASE_URL}/products/v4/search/keyword"
SUBSTITUTIONS_URL = f"{BASE_URL}/products/v4/search/{{digikey_pn}}/substitutions"

# Test MPN — a common resistor, well-catalogued, sandbox should return data
TEST_MPN = "CRCW080510K0FKEA"

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_token(client_id, client_secret):
    """2-legged OAuth2 client credentials flow."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "grant_type":    "client_credentials",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    token_data = resp.json()
    print(f"[token] OK — expires_in: {token_data.get('expires_in')}s")
    return token_data["access_token"]


def auth_headers(token):
    return {
        "X-DIGIKEY-Client-Id": CLIENT_ID,
        "Authorization":       f"Bearer {token}",
        "Content-Type":        "application/json",
        "Accept":              "application/json",
    }


def keyword_search(token, mpn, limit=5):
    """Search by MPN keyword. Returns list of matching products."""
    payload = {
        "Keywords":    mpn,
        "Limit":       limit,
        "Offset":      0,
        "FilterOptionsRequest": {
            "ManufacturerFilter": [],
            "StatusFilter":       [{"Id": 0, "Value": "Active"}],  # active parts only
        },
    }
    resp = requests.post(SEARCH_URL, headers=auth_headers(token), json=payload)
    resp.raise_for_status()
    data = resp.json()
    print(f"[search] {data.get('ProductsCount', 0)} products, {len(data.get('ExactMatches', []))} exact match(es)")
    return data


def get_substitutions(token, digikey_pn):
    """Fetch Digikey-curated substitutions for a given Digikey part number."""
    url = SUBSTITUTIONS_URL.format(digikey_pn=digikey_pn)
    resp = requests.get(url, headers=auth_headers(token))
    resp.raise_for_status()
    return resp.json()


def pp(label, data):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print('='*60)
    print(json.dumps(data, indent=2))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: Set DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET env vars")
        return

    print(f"[config] {'SANDBOX' if SANDBOX else 'PRODUCTION'} mode")
    print(f"[config] Test MPN: {TEST_MPN}")

    # Step 1: get token
    token = get_token(CLIENT_ID, CLIENT_SECRET)

    # Step 2: keyword search
    search_result = keyword_search(token, TEST_MPN)

    # Step 3: if we got a hit, pull substitutions using Digikey PN
    products = search_result.get("Products", [])
    if not products:
        print("\n[!] No products returned from keyword search.")
        print("    Sandbox may return limited data — try with production credentials.")
        return

    # Find exact match first, fall back to first product
    exact = search_result.get("ExactMatches", [])
    first = exact[0] if exact else products[0]

    # v4 response: DigiKey PN is inside ProductVariations[].DigiKeyProductNumber
    # We want the Cut Tape (CT) variant preferably, otherwise just take the first
    dk_pn = None
    for variant in first.get("ProductVariations", []):
        pkg = variant.get("PackageType", {}).get("Name", "")
        if "Cut Tape" in pkg:
            dk_pn = variant.get("DigiKeyProductNumber")
            break
    if not dk_pn:
        dk_pn = first.get("ProductVariations", [{}])[0].get("DigiKeyProductNumber")

    if not dk_pn:
        print("\n[!] Could not extract DigiKey part number from search result.")
        pp("First product (inspect manually)", first)
        return

    print(f"\n[substitutions] Using DigiKey PN: {dk_pn}")
    subs = get_substitutions(token, dk_pn)
    pp("Substitutions result", subs)


if __name__ == "__main__":
    main()
