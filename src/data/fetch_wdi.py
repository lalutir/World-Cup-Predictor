"""
fetch_wdi.py — Download economic and population indicators from the World Bank WDI API.

Source: World Bank World Development Indicators (WDI)
  https://datahelpdesk.worldbank.org/knowledgebase/articles/898581

Three time-series indicators are fetched, covering 1960–2026:

  SP.POP.TOTL    Population, total
  NY.GDP.MKTP.CD GDP, current US$
  NY.GDP.PCAP.CD GDP per capita, current US$

These are used downstream in features/econ_pop.py as country-level economic
signals for each match.  Year-of-match joins and pre-1960 flat-filling are
handled at the feature-engineering stage, not here.

Data pipeline overview
----------------------
1. Country list  (wb_countries.json)
   Fetched from /v2/country/all.  Includes both genuine country entries and
   aggregate rows (regions, income groups, "World").  Saved in full — the
   downstream crosswalk.py distinguishes real countries (region.value !=
   "Aggregates") from aggregates when building the country crosswalk.

2. Indicator data  (<CODE>.json per indicator)
   Fetched from /v2/country/all/indicator/<CODE>.  The API returns a two-
   element list [metadata_dict, [data_rows]].  If metadata["pages"] > 1 the
   additional pages are fetched automatically and concatenated so each saved
   file is a complete single-page snapshot.

Coverage gaps and proxy policy
-------------------------------
Several footballing nations have no separate World Bank entry:
  - England, Scotland, Wales, Northern Ireland → part of "United Kingdom"
  - Faroe Islands, Gibraltar, Kosovo, etc. → no WDI sovereign entry

These gaps are handled in crosswalk.py (not here) via an explicit proxy-data
mapping (e.g. UK constituent nations inherit United Kingdom's GDP/population).
The raw files downloaded here are saved without any modification.

Known WDI response shape
------------------------
    [
      {"page": 1, "pages": 1, "per_page": 20000, "total": 17822,
       "lastupdated": "2026-05-29"},
      [{"indicator": {"id": "SP.POP.TOTL", "value": "Population, total"},
        "country":   {"id": "FR", "value": "France"},
        "countryiso3code": "FRA",
        "date": "2024",
        "value": 66548530,
        "unit": "", "obs_status": "", "decimal": 0}, ...]
    ]

Usage
-----
    python -m src.data.fetch_wdi           # skip files that already exist
    python -m src.data.fetch_wdi --force   # always re-download

Output files written to data/raw/wdi/:
    wb_countries.json      country metadata list
    SP.POP.TOTL.json       population time series
    NY.GDP.MKTP.CD.json    GDP time series
    NY.GDP.PCAP.CD.json    GDP per capita time series
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Make the repository root importable when this module is run directly.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import (
    WB_COUNTRIES_RAW,
    WB_COUNTRY_LIST_URL,
    WDI_DATE_END,
    WDI_DATE_START,
    WDI_INDICATORS,
    WDI_PER_PAGE,
    WDI_RAW_DIR,
    WDI_URL_TEMPLATE,
)

# ---------------------------------------------------------------------------
# Country metadata
# ---------------------------------------------------------------------------


def _fetch_country_list(dest: Path, *, force: bool) -> list[dict[str, Any]]:
    """Fetch the World Bank country list and write it to *dest* as JSON.

    The saved file contains the raw two-element API response:
    ``[metadata_dict, [country_dicts]]``.  Downstream code can inspect
    ``country["region"]["value"]`` to distinguish real countries from
    aggregate groups; entries where ``region.value == "Aggregates"`` are
    not genuine economies.

    Args:
        dest:  Path where the JSON file is written (typically
               ``data/raw/wdi/wb_countries.json``).
        force: If ``True``, download and overwrite even if *dest* exists.

    Returns:
        The list of country-metadata dicts (second element of the response).

    Raises:
        requests.HTTPError:  Non-2xx response from the API.
        ValueError:          Unexpected response structure.
    """
    if dest.exists() and not force:
        print(
            f"  Skipping country list  (already exists"
            f" at {dest.relative_to(_REPO_ROOT)};  use --force to refresh)"
        )
        with dest.open("r", encoding="utf-8") as fh:
            raw: list[Any] = json.load(fh)
        return raw[1]

    print("  Fetching country list …", end="", flush=True)
    response = requests.get(WB_COUNTRY_LIST_URL, timeout=60)
    response.raise_for_status()
    raw = response.json()

    _validate_wb_response(raw, context="country list")

    dest.parent.mkdir(parents=True, exist_ok=True)
    _write_json(raw, dest)

    n_countries = len(raw[1])
    print(
        f" done  ({n_countries} entries)"
        f"  →  {dest.relative_to(_REPO_ROOT)}"
    )
    return raw[1]


# ---------------------------------------------------------------------------
# Indicator fetching
# ---------------------------------------------------------------------------


def _fetch_indicator(code: str, label: str, dest: Path, *, force: bool) -> None:
    """Fetch one WDI indicator time series and write it to *dest* as JSON.

    The saved file is a unified two-element list ``[metadata, [all_rows]]``.
    If the API response spans multiple pages they are fetched sequentially
    and merged so callers never need to handle pagination.

    WDI API returns data for *all* countries (including aggregates) when
    ``country/all`` is used.  Aggregate rows are not filtered here — that
    responsibility belongs to crosswalk.py, which has the full picture of
    which ISO3 codes represent genuine footballing nations.

    Args:
        code:  WDI indicator code, e.g. ``"SP.POP.TOTL"``.
        label: Human-readable name used in progress output.
        dest:  Path where the JSON file is written.
        force: If ``True``, download and overwrite even if *dest* exists.

    Raises:
        requests.HTTPError:  Non-2xx response from the API.
        ValueError:          Unexpected response structure.
    """
    if dest.exists() and not force:
        print(
            f"  Skipping {code:<20s} ({label})"
            f"  —  already exists;  use --force to refresh"
        )
        return

    url = WDI_URL_TEMPLATE.format(
        code=code,
        per_page=WDI_PER_PAGE,
        start_year=WDI_DATE_START,
        end_year=WDI_DATE_END,
    )

    print(f"  Fetching {code:<20s} ({label}) …", end="", flush=True)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    raw = response.json()

    _validate_wb_response(raw, context=f"indicator {code}")

    metadata: dict[str, Any] = raw[0]
    rows: list[dict[str, Any]] = raw[1]
    total_pages: int = int(metadata.get("pages", 1))

    # Fetch additional pages when the dataset doesn't fit in the first response.
    # per_page=20_000 should be sufficient for ~17 800 country-year rows, but
    # this guard ensures correctness if the WDI dataset grows or per_page ever
    # needs to be lowered.
    if total_pages > 1:
        for page in range(2, total_pages + 1):
            paged_url = url + f"&page={page}"
            print(f" (page {page}/{total_pages})", end="", flush=True)
            paged_resp = requests.get(paged_url, timeout=120)
            paged_resp.raise_for_status()
            _validate_wb_response(paged_resp.json(), context=f"{code} page {page}")
            _, paged_rows = paged_resp.json()
            rows.extend(paged_rows)

    # Rewrite metadata so the merged file looks like a single-page response.
    metadata["pages"] = 1
    metadata["per_page"] = len(rows)
    merged: list[Any] = [metadata, rows]

    dest.parent.mkdir(parents=True, exist_ok=True)
    _write_json(merged, dest)

    size_kb = dest.stat().st_size / 1_024
    print(
        f" done  ({len(rows):,} rows, {size_kb:,.1f} KB)"
        f"  →  {dest.relative_to(_REPO_ROOT)}"
    )


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def _validate_wb_response(raw: Any, *, context: str) -> None:
    """Raise ValueError if *raw* does not look like a valid WB API response.

    A valid response is a two-element list where the first element is a dict
    (metadata) and the second element is a list (data rows or country entries).

    Args:
        raw:     The parsed JSON value returned by the API.
        context: Short description of the call site (used in the error message).

    Raises:
        ValueError: If the shape does not match the expected two-element list.
    """
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(
            f"World Bank API ({context}): expected a 2-element list, "
            f"got {type(raw).__name__} of length "
            f"{len(raw) if isinstance(raw, list) else 'N/A'}."
        )
    if not isinstance(raw[0], dict):
        raise ValueError(
            f"World Bank API ({context}): expected metadata dict at index 0, "
            f"got {type(raw[0]).__name__}."
        )
    if not isinstance(raw[1], list):
        raise ValueError(
            f"World Bank API ({context}): expected data list at index 1, "
            f"got {type(raw[1]).__name__}."
        )


def _write_json(data: Any, dest: Path) -> None:
    """Write *data* as pretty-printed UTF-8 JSON to *dest*.

    Args:
        data: Any JSON-serialisable Python object.
        dest: Destination file path; must already have an existing parent dir.
    """
    with dest.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_all(*, force: bool = False) -> None:
    """Download the World Bank country list and all three WDI indicators.

    Files are written to ``data/raw/wdi/`` under the repository root.
    Any file that already exists on disk is silently skipped unless *force*
    is ``True``.

    Args:
        force: When ``True``, re-download every file regardless of whether it
               already exists on disk.

    Example::

        from src.data.fetch_wdi import fetch_all
        fetch_all()           # first run: downloads all four files
        fetch_all()           # second run: skips all (files present)
        fetch_all(force=True) # always re-downloads
    """
    # Country metadata must come first — other modules need it to filter
    # aggregate rows, though this script saves it without filtering.
    _fetch_country_list(WB_COUNTRIES_RAW, force=force)

    for code, label in WDI_INDICATORS:
        dest = WDI_RAW_DIR / f"{code}.json"
        _fetch_indicator(code, label, dest, force=force)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.data.fetch_wdi",
        description=(
            "Download World Bank WDI economic and population indicators.\n\n"
            "Four files are written to data/raw/wdi/ under the repository root:\n"
            "  wb_countries.json   — country metadata (real countries vs. aggregates)\n"
            "  SP.POP.TOTL.json    — population time series, 1960–2026\n"
            "  NY.GDP.MKTP.CD.json — GDP time series, 1960–2026\n"
            "  NY.GDP.PCAP.CD.json — GDP per capita time series, 1960–2026\n\n"
            "Existing files are skipped unless --force is supplied."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-download all files even if they already exist on disk.  "
            "Use this when the World Bank has released updated annual estimates."
        ),
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print("=" * 60)
    print("Fetching World Bank WDI economic & population data")
    print("=" * 60)
    fetch_all(force=args.force)
    print("\nDone.")
