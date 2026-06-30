#!/usr/bin/env python3
"""
datasheet_resolver.py — Resolve clean, component-specific datasheet URLs.

Rules:
  - Return blank if part is obsolete
  - Return blank if URL is a general catalog (not component-specific)
  - Return blank if URL triggers auto-download (.pdf direct link)
  - Return blank if URL is known to 404
  - Apply per-manufacturer URL construction where Digikey URLs are unreliable
  - Fall back to blank if no clean URL can be resolved
"""

import re
import requests

# ── Manufacturer-specific URL builders ───────────────────────────────────────
# These manufacturers have known issues with Digikey datasheet URLs.
# We construct component-specific URLs directly from MPN where possible.

def _samsung_url(mpn: str) -> str:
    """
    Samsung MLCC product page — constructable from MPN.
    e.g. CL10B104KO8NNNC → https://product.samsungsem.com/mlcc/CL10B104KO8NNN.do
    Samsung product page URLs always drop the last character (packaging code).
    """
    base = mpn[:-1] if len(mpn) > 1 else mpn
    return f"https://product.samsungsem.com/mlcc/{base}.do"


def _taiyo_yuden_url(mpn: str) -> str:
    """
    Taiyo Yuden TYCOMPAS detail URL.
    - Current MPNs: opens product detail page directly.
    - Old/renamed MPNs (e.g. EMK, JMK, UMK series): opens rename notice page
      listing all new part numbers with application categories and status.
      User can select the appropriate new MPN from there.
    Both outcomes are valid and browser-viewable.
    """
    return f"https://ds.yuden.co.jp/TYCOMPAS/ap/detail?pn={mpn}&u=M"


def _murata_search_url(mpn: str) -> str:
    """
    Murata product search page for the MPN.
    Digikey sometimes returns PIM asset URLs which are catalog-level.
    Use Murata's own search instead.
    """
    return f"https://www.murata.com/en-us/products/productdetail?partno={mpn}"


def _yageo_url(mpn: str) -> str:
    """
    Yageo component-specific specsheet page on yageogroup.com.
    Pattern: https://yageogroup.com/download/specsheet/{MPN}
    Returns browser-viewable component-specific content, not a forced download.
    """
    return f"https://yageogroup.com/download/specsheet/{mpn}"


def _kemet_url(mpn: str) -> str:
    """
    KEMET (Yageo Group) component-specific specsheet.
    KEMET acquired by Yageo — specsheets available at yageogroup.com.
    """
    return f"https://yageogroup.com/download/specsheet/{mpn}"


def _tdk_url(mpn: str) -> str:
    """
    TDK component-specific product page.
    """
    return f"https://product.tdk.com/en/search/capacitor/ceramic/mlcc/info?part_no={mpn}"


def _wurth_url(mpn: str) -> str:
    """
    Würth Elektronik component-specific datasheet.
    WE datasheets are component-specific PDFs, browser-viewable.
    """
    return f"https://www.we-online.com/components/products/datasheet/{mpn}.pdf"


def _bourns_url(mpn: str) -> str:
    """
    Bourns component page.
    """
    return f"https://www.bourns.com/products/general-purpose-resistors/chip-resistors"


# Map of manufacturer name fragments → URL builder function
# Applied when Digikey URL is flagged as unreliable for that manufacturer
MANUFACTURER_URL_BUILDERS = {
    "samsung electro": _samsung_url,
    "taiyo yuden":     _taiyo_yuden_url,
    "murata":          _murata_search_url,
    "yageo":           _yageo_url,
    "kemet":           _kemet_url,
    "tdk":             _tdk_url,
    "würth":           _wurth_url,
    "wurth":           _wurth_url,
}

# ── Catalog URL pattern blocklist ─────────────────────────────────────────────
# URL substrings that indicate a general catalog rather than component page

CATALOG_URL_PATTERNS = [
    # Digikey-hosted manufacturer catalog PDFs (not component-specific)
    "mm.digikey.com/Volume0/opasdata",
    # Murata PIM asset URLs (catalog-level)
    "pim.murata.com/asset",
    # TDK generic MLCC catalog
    "mlcc_commercial_general",
    # Yageo group download endpoint (often 404 or catalog)
    "yageogroup.com/component-documentation/download",
    # Cal-chip catalog
    "calchip.com/wp-content/uploads",
    # Walsin general catalog
    "WTC_MLCC_General_Purpose.pdf",
    # Darfon catalog
    "ICM%20File",
    # Any URL that is purely a PDF download trigger
    # (caught separately below by extension check)
]

# Manufacturers whose Digikey-provided URLs are known to be catalog-level
# → always use our own URL builder instead
UNRELIABLE_DK_URL_MANUFACTURERS = {
    "samsung electro-mechanics",
    "taiyo yuden",
    "murata electronics",
    "murata manufacturing",
    "yageo",
    "kemet",
    "tdk",
    "würth elektronik",
    "wurth elektronik",
}


# ── Validation helpers ────────────────────────────────────────────────────────

def _is_catalog_url(url: str) -> bool:
    """Returns True if URL matches a known catalog pattern."""
    for pattern in CATALOG_URL_PATTERNS:
        if pattern.lower() in url.lower():
            return True
    return False


def _url_is_reachable(url: str, timeout: int = 5) -> bool:
    """
    HEAD request to check if URL returns 2xx or 3xx.
    Returns False on 4xx/5xx or connection error.
    """
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0"})
        return resp.status_code < 400
    except Exception:
        return False


# ── Main resolver ─────────────────────────────────────────────────────────────

# Manufacturers whose datasheet URLs need manual verification (highlight yellow)
# Manufacturers whose URLs should always be flagged yellow for manual verification
# regardless of whether they match catalog patterns
NEEDS_VERIFICATION_MANUFACTURERS = {
    "taiyo yuden",
}


def resolve_datasheet_url(mpn: str, manufacturer: str, dk_datasheet_url: str,
                           product_status: str = "Active",
                           validate_url: bool = False) -> tuple[str, bool]:
    """
    Resolve the best available datasheet URL for a component.

    Returns:
        Tuple of (url, needs_verification):
          - url: resolved URL string, or "" if none available
          - needs_verification: True = flag cell yellow for manual verification
                                (catalog URL or manufacturer with known URL issues)
    """
    # Rule 1: blank for obsolete parts
    if product_status and product_status.lower() not in ("active", ""):
        return ("", False)

    mfr_lower = manufacturer.lower()

    # Rule 2: manufacturers with known unreliable DK URLs → use our own builder
    for mfr_key, builder in MANUFACTURER_URL_BUILDERS.items():
        if mfr_key in mfr_lower:
            url = builder(mpn)
            if validate_url and not _url_is_reachable(url):
                return ("", False)
            needs_verify = mfr_key in NEEDS_VERIFICATION_MANUFACTURERS
            return (url, needs_verify)

    # Rule 3: no DK URL available
    if not dk_datasheet_url:
        return ("", False)

    # Rule 4: optionally validate the URL is reachable
    if validate_url and not _url_is_reachable(dk_datasheet_url):
        return ("", False)

    # Rule 5: return DK URL as-is — catalog is acceptable for unknown manufacturers
    return (dk_datasheet_url, False)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_cases = [
        # (mpn, manufacturer, dk_url, status)
        ("CL10B104KO8NNNC",   "Samsung Electro-Mechanics",
         "https://mm.digikey.com/...", "Active"),
        ("EMK107B7104KA-T",   "Taiyo Yuden",
         "https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/8412/587_mlcc01hqAUTOe.pdf", "Active"),
        ("GRM188R71C104KA01D","Murata Electronics",
         "https://pim.murata.com/asset/pim4/ceramicCapacitorSMD/...", "Active"),
        ("CC0603KRX7R7BB104", "YAGEO",
         "https://yageogroup.com/component-documentation/download/specsheet/CC0603KRX7R7BB104", "Active"),
        ("CRCW080510K0FKEA",  "Vishay Dale",
         "https://www.vishay.com/docs/20035/dcrcwe3.pdf", "Active"),
        ("SOME_PART",         "Any Mfr",
         "https://example.com/datasheet.pdf", "Obsolete"),
        ("RC0805FR-0710KL",   "YAGEO",
         "https://yageogroup.com/download/specsheet/RC0805FR-0710KL", "Active"),
        ("CRS0805-FX-1002ELF","Bourns Inc.",
         "https://bourns.com/docs/product-datasheets/cr.pdf?sfvrsn=574d41f6_14", "Active"),
        ("C0603C104K4RACTU",  "KEMET",
         "https://yageogroup.com/content/datasheet/asset/file/KEM_C1002_X7R_SMD", "Active"),
        ("CGJ3E2X7R1C104K080AA", "TDK",
         "https://product.tdk.com/system/files/dam/doc/product/capacitor/ceramic/mlcc/catalog/mlcc_commercial_general_en.pdf", "Active"),
        ("885012206046",      "Würth Elektronik",
         "https://www.we-online.com/components/products/datasheet/885012206046.pdf", "Active"),
    ]

    print(f"{'MPN':<30} {'Manufacturer':<30} {'Resolved URL'}")
    print("─" * 100)
    for mpn, mfr, dk_url, status in test_cases:
        url, needs_verify = resolve_datasheet_url(mpn, mfr, dk_url, status, validate_url=False)
        flag = " ⚠ verify" if needs_verify else ""
        print(f"{mpn:<30} {mfr:<30} {url or '(blank)'}{flag}")
