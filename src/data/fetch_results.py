"""
fetch_results.py — Download raw historical international football results.

Source: martj42/international_results (GitHub)
  https://github.com/martj42/international_results

Three CSV files are fetched and written byte-for-byte to data/raw/:

  results.csv
    ~49 500 rows covering every official international from 1872-11-30 to
    present day.  Schema (confirmed 2026-06-22):
        date, home_team, away_team, home_score, away_score,
        tournament, city, country, neutral
    The `neutral` column is TRUE/FALSE and is authoritative — use it as-is.
    As of 2026-06-22 the file already contains unplayed 2026 fixtures with
    NA scores (schedule placeholders); downstream code must filter these out
    before computing Elo, form, H2H, or any other feature.

  shootouts.csv
    Penalty shootout outcomes for matches that finished level after 90 + ET.
    Schema: date, home_team, away_team, winner, first_shooter
    The `first_shooter` column is dropped at the processing stage (sparsely
    populated and not needed to determine who advanced).

  former_names.csv
    Historical team-name changes (e.g. "Dahomey" → "Benin").  Used by
    crosswalk.py to unify a team's Elo history across name changes.
    Schema: current, former, start_date, end_date
    Contains 327+ distinct team identities including defunct nations.

Data is intentionally kept raw here.  All cleaning (merging shootouts into
match results, resolving former names, filtering NA-score rows) is performed
by downstream processing modules so that data/raw/ stays byte-for-byte what
the source provided.

Usage
-----
    python -m src.data.fetch_results           # skip files that already exist
    python -m src.data.fetch_results --force   # always re-download

After real matches are played, run with --force to refresh the dataset from
the upstream repository, which is updated within hours of each kick-off.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

import requests

# ---------------------------------------------------------------------------
# Make the repository root importable when this module is run directly.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import (
    FORMER_NAMES_RAW,
    FORMER_NAMES_URL,
    RAW_DIR,
    RESULTS_RAW,
    RESULTS_URL,
    SHOOTOUTS_RAW,
    SHOOTOUTS_URL,
)

# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


class _FileSpec(NamedTuple):
    """Pairs a remote URL with its local destination path and a display label."""

    url: str
    dest: Path
    label: str


# Declaration order also determines download order.
_SOURCES: list[_FileSpec] = [
    _FileSpec(RESULTS_URL,      RESULTS_RAW,      "results"),
    _FileSpec(SHOOTOUTS_URL,    SHOOTOUTS_RAW,    "shootouts"),
    _FileSpec(FORMER_NAMES_URL, FORMER_NAMES_RAW, "former_names"),
]

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def _download_file(url: str, dest: Path, label: str) -> None:
    """Stream *url* to *dest*, replacing any existing file.

    Uses HTTP streaming with 64 KiB chunks so large files (results.csv is
    several MB) are never fully buffered in memory.

    Args:
        url:   The remote URL to fetch.
        dest:  Local file path to write.  Parent directories are created
               automatically if they do not exist.
        label: Short name printed in progress output.

    Raises:
        requests.HTTPError: If the server responds with a non-2xx status code.
        requests.Timeout:   If the server does not respond within 60 seconds.
    """
    print(f"  Downloading {label} …", end="", flush=True)

    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        for chunk in response.iter_content(chunk_size=65_536):
            fh.write(chunk)

    size_kb = dest.stat().st_size / 1_024
    print(f" done  ({size_kb:,.1f} KB)  →  {dest.relative_to(_REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_all(*, force: bool = False) -> None:
    """Download results.csv, shootouts.csv, and former_names.csv.

    Args:
        force: When ``True``, re-download every file regardless of whether it
               already exists on disk.  When ``False`` (the default), any file
               that is already present in ``data/raw/`` is silently skipped —
               this makes the first-run case fast and idempotent while still
               allowing a deliberate refresh after new matches are played.

    Example::

        from src.data.fetch_results import fetch_all
        fetch_all()           # first run: downloads all three
        fetch_all()           # second run: skips all (files present)
        fetch_all(force=True) # always re-downloads
    """
    skipped_all = True
    for spec in _SOURCES:
        if spec.dest.exists() and not force:
            print(
                f"  Skipping {spec.label:15s} (already exists"
                f" at {spec.dest.relative_to(_REPO_ROOT)};  use --force to refresh)"
            )
        else:
            _download_file(spec.url, spec.dest, spec.label)
            skipped_all = False

    if skipped_all:
        print(
            "\nAll three files are already present in data/raw/.\n"
            "Run with --force to re-download after new matches are played."
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.data.fetch_results",
        description=(
            "Download historical international football results from "
            "martj42/international_results.\n\n"
            "Files are written to data/raw/ under the repository root.\n"
            "Existing files are skipped unless --force is supplied."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-download all files even if they already exist on disk.  "
            "Use this after real matches have been played to refresh results.csv."
        ),
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print("=" * 60)
    print("Fetching historical results data from martj42/international_results")
    print("=" * 60)
    fetch_all(force=args.force)
    print("\nDone.")
