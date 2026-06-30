# BOM Alternative Parts Finder

A Python tool that automates finding component alternatives for electronics BOMs using the DigiKey API. Given a list of original parts, it returns ranked, spec-verified alternatives — each one a real, currently active, in-catalogue part with a working datasheet link.

## Why

Sourcing alternates for passive components (resistors, capacitors) is a routine but tedious part of BOM management — especially under part shortages or when qualifying second sources. Doing it manually means cross-checking specs by hand across dozens of manufacturer datasheets, with no guarantee the part you land on is still in production.

General-purpose AI tools don't solve this reliably either — they're prone to hallucinating part numbers, returning catalog PDFs instead of component-specific datasheets, and they have no way to verify real-time stock or obsolescence status.

This tool instead queries DigiKey's official Product Information API directly, so every result is grounded in real, current catalogue data.

## What it does

Given a BOM (or multiple BOMs) listing original components by manufacturer part number, the tool:

1. **Filters** to whichever item-number prefixes you configure (e.g. separate ranges for capacitors vs. resistors, matching your own BOM numbering convention)
2. **Deduplicates** items across multiple input BOM files, merging reference designators per file while keeping one row per unique item number
3. **Extracts key specs** for each original part (value, tolerance, package, dielectric, voltage/power rating) from DigiKey's parametric data
4. **Finds alternatives** by combining DigiKey's curated Substitutions endpoint with a description-style keyword search fallback, filtered against the original part's exact specs
5. **Resolves datasheet URLs** to component-specific pages where possible, correcting for manufacturers whose DigiKey-listed datasheet is actually a generic series catalog
6. **Generates remarks** flagging any spec that differs from the original (e.g. a higher power rating), so the engineer doesn't have to re-derive that themselves
7. **Writes a single Excel output** with dynamic manufacturer-brand columns — built from whichever brands actually appear in the results, not a fixed list — so results stay easy to scan across rows

## Why this was harder than it sounds

A few things that looked simple on paper and weren't:

- **DigiKey's Substitutions endpoint returns lightweight objects** (MPN, manufacturer, price, stock) with no parametric data, so each substitute needs a follow-up KeywordSearch to get comparable specs.
- **ParametricFilters in KeywordSearch reliably returns 400 errors** when constructed from scratch — DigiKey expects internal `ValueId` references from a prior search response, not raw spec values. Worked around this by building search keywords in DigiKey's own product-description format (e.g. `"RES 2K OHM 1% 0201"`) instead, which proved far more reliable.
- **Datasheet URLs from the API are inconsistent.** Several manufacturers (Samsung, Taiyo Yuden, Murata, Yageo, KEMET, TDK) return generic catalog PDFs through DigiKey rather than component-specific pages. The tool maintains manufacturer-specific URL builders for these cases, falling back to the DigiKey-provided URL elsewhere.
- **DigiKey's standard API tier rate-limits at roughly 1,000 requests/day.** A single multi-hundred-line BOM run can consume several hundred calls (search → substitutions → per-substitute spec lookup → parametric fallback), so the tool checkpoints progress after every item and resumes automatically on the next run rather than starting over.

## Architecture

```
bom_ingestion.py          ← entry point: reads BOM(s), orchestrates the pipeline, writes output
├── spec_extractor.py     ← MPN → clean spec dict (resistor/capacitor specific)
├── alternatives_fetcher.py  ← MPN → up to 10 filtered, ranked alternatives
├── datasheet_resolver.py ← resolves component-specific datasheet URLs
├── remarks_generator.py  ← field-by-field diff → human-readable note
└── excel_output.py       ← (also used standalone for single-MPN testing)
```

Each module was built and tested independently before being wired together — `spec_extractor` and `alternatives_fetcher` can be imported and used standalone for ad-hoc single-part lookups.

## Setup

```bash
pip install requests pandas openpyxl
export DIGIKEY_CLIENT_ID="your_production_client_id"
export DIGIKEY_CLIENT_SECRET="your_production_client_secret"
```

Requires a DigiKey developer account with a **production** app subscribed to the **Product Information V4** API product. (Sandbox credentials do not return real substitution data and are subject to additional restrictions — see notes below.)

## Usage

```bash
python bom_ingestion.py bom1.xlsx bom2.xlsx --output alternatives_output.xlsx
```

- Accepts one or more BOM Excel files as input
- `--output` is optional, defaults to `bom_alternatives_output.xlsx`
- Progress is saved to `.bom_checkpoint.json` after every item — if the run is interrupted (network drop, rate limit, manual stop), re-running the exact same command resumes from where it left off and automatically retries any items that previously failed
- The checkpoint is cleared automatically once a run completes with zero failures

### Input format

Each input file should have, at minimum:

| Column | Field |
|---|---|
| A | Item Number |
| B | Description |
| C | Ref Des |
| E | Original Manufacturer |
| F | Original MPN |
| G | Original Characteristics |

Example row:

| Item Number | Description | Ref Des | Original Manufacturer | Original MPN | Original Characteristics |
|---|---|---|---|---|---|
| CAP-00142 | 10µF ±10% 16V Ceramic Capacitor X5R 0603 | C12, C14 | ManufacturerA | ABC0603X5R106M16V | Capacitor, 10uF, ±10%, 16V, X5R, 0603 |

The filename (without extension) is used as the BOM identifier for the Ref Des column header in the output, so name input files meaningfully (e.g. `assembly_rev_a.xlsx`, `assembly_rev_b.xlsx`).

### Output format

A single Excel file with the original BOM columns preserved, an extracted Key Spec column, and dynamic per-manufacturer column groups (MPN / Datasheet URL / Manufacturer), built in the order each brand first appears across the processed items. Datasheet cells that may point to a catalog rather than a component-specific page are highlighted yellow for manual verification.

## Known limitations

- Currently tuned for **resistors and capacitors only** — the spec extraction and matching logic would need extending for other component types (inductors, diodes, ICs, etc.)
- Subject to DigiKey's standard-tier rate limit (~1,000 calls/day); larger BOMs may need to run across multiple sessions using the built-in checkpoint/resume
- A small number of manufacturers (e.g. Taiyo Yuden) have datasheet hosting that can't be reliably resolved to a component-specific URL via a constructable pattern — these are flagged for manual checking rather than guessed at

## Stack

Python · DigiKey Product Information API v4 (2-legged OAuth2) · pandas · openpyxl
