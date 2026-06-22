"""
elo.py — Calculate Elo ratings for all national football teams.

Processes the full match history chronologically, updating each team's Elo
rating after every played match.  Two artefacts are produced:

  data/processed/elo_history.csv
    Long-format rating snapshots after each match.
    Columns: team, date, elo   (rating *after* the match)

  home_elo_before / away_elo_before columns
    Pre-match ratings injected into matches.csv by build_dataset.py.
    These are the values used as model features (no lookahead).

Elo update formula (standard eloratings.net / World Football Elo approach)
---------------------------------------------------------------------------
  expected_home  = 1 / (1 + 10 ** (-(elo_home + H - elo_away) / 400))
  H              = HOME_ADVANTAGE (100) when neutral=False, else 0
  K              = K_base(tournament) * G(|goal_diff|)
  actual_result  = 1.0 (home win) | 0.5 (draw) | 0.0 (away win)

  Δ              = K * (actual_result - expected_home)
  elo_home_new   = elo_home + Δ
  elo_away_new   = elo_away - Δ   # zero-sum

Goal-difference multiplier G
  |diff| 0-1  →  1.000
  |diff| 2    →  1.500
  |diff| 3    →  1.750
  |diff| 4+   →  1.750 + (|diff| - 3) / 8

Tournament K-base tiers
  60  FIFA World Cup finals; continental championship finals
      (UEFA Euro, Copa América, AFCON, AFC Asian Cup)
  50  Confederations Cup; UEFA/CONCACAF Nations League finals
  40  World Cup qualifiers; continental qualifiers; Gold Cup; regional cups
  30  CONIFA, Viva World Cup, other non-FIFA / minor tournaments  [default]
  20  Friendlies; ceremonial invitational cups

All teams start at ELO_SEED_RATING (1500) from the first match in
results.csv (1872-11-30).  Ratings are updated for every played match
regardless of whether it later appears in the knockout bracket.

⚠️  Verify K-tiers and goal-multiplier against mar-antaya/world_cup_predictions
    before treating these values as final — see CLAUDE.md for details.

Standalone usage
----------------
    python -m src.features.elo           # reads data/raw/, writes elo_history.csv
    python -m src.features.elo --force   # re-downloads raw data first

Library usage (called by build_dataset.py)
------------------------------------------
    from src.features.elo import compute_elo
    matches, elo_history = compute_elo(matches_df)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import (
    ELO_HISTORY_PATH,
    ELO_SEED_RATING,
    FORMER_NAMES_RAW,
    HOME_ADVANTAGE,
    PROCESSED_DIR,
    RESULTS_RAW,
)

# ---------------------------------------------------------------------------
# Tournament tier → K-base mapping
# ---------------------------------------------------------------------------

# Patterns are checked in order; the FIRST match wins.
# Each entry is (list_of_substrings_to_match, k_base).
# The tournament string is lowercased before matching.
#
# Ordering rules:
#  1. Friendlies first — many tournament names incidentally contain other words.
#  2. Non-FIFA bodies (CONIFA, Viva) before any "world cup" pattern.
#  3. Qualifiers before finals — "FIFA World Cup qualification" must not match
#     the Tier-60 "FIFA World Cup" rule.
#  4. High-tier finals last among the explicit checks.
#  5. Default Tier 30 for anything unrecognised.
_TIER_RULES: list[tuple[list[str], int]] = [
    # Tier 20 — Friendlies / low-prestige invitationals
    (["friendly", "king's cup", "kings cup", "four nations", "three nations",
      "kirin cup", "china cup", "japan cup"], 20),

    # Tier 30 — Non-FIFA bodies (checked BEFORE generic "world cup")
    (["conifa", "viva world cup", "nf board", "eadp", "island games",
      "ksf ", "cpi "], 30),

    # Tier 50 — Confederations Cup; Nations League finals
    (["confederations cup"], 50),
    (["nations league"], 50),  # qualifiers caught at Tier 40 below

    # Tier 40 — ALL qualifiers (FIFA WCQ, Euro Q, Copa Q, etc.)
    (["qualification", "qualifier", "qualifying", "world cup q"], 40),

    # Tier 60 — Major continental & global finals (no qualifiers remain after above)
    (["fifa world cup"], 60),
    (["european championship", "uefa european", "uefa euro"], 60),
    (["copa am"], 60),               # "Copa América" / "Copa America"
    (["africa cup of nations", "african cup of nations", "cup of nations"], 60),
    (["afc asian cup", "asian cup of nations", "asian cup"], 60),
    (["ofc nations cup"], 60),
    (["concacaf championship"], 60),  # pre-Gold Cup name (1963–1989)

    # Tier 40 — Named regional cups
    (["gold cup", "cosafa", "cecafa", "wafu", "saff", "aff "], 40),
]


def k_base(tournament: str) -> int:
    """Return the Elo K-base factor for the given tournament string.

    Matches the lowercased *tournament* string against ``_TIER_RULES`` in order;
    returns the K-base of the first matching rule.  Returns 30 (minor/unknown
    tournament) if nothing matches.

    Args:
        tournament: Tournament name exactly as it appears in results.csv.

    Returns:
        Integer K-base: 20, 30, 40, 50, or 60.

    Examples:
        >>> k_base("FIFA World Cup")
        60
        >>> k_base("FIFA World Cup qualification")
        40
        >>> k_base("CONIFA World Cup qualification")
        30
        >>> k_base("Friendly")
        20
    """
    t = tournament.lower()
    for keywords, k in _TIER_RULES:
        if any(kw in t for kw in keywords):
            return k
    return 30  # default: minor / unrecognised tournament


# ---------------------------------------------------------------------------
# Goal-difference multiplier
# ---------------------------------------------------------------------------


def g_factor(goal_diff: int) -> float:
    """Return the goal-difference Elo multiplier for an absolute score gap.

    Args:
        goal_diff: Absolute difference between home and away goals scored
                   in 90 + extra time (never negative).

    Returns:
        Float multiplier applied to K before the Elo update.

    Examples:
        >>> g_factor(0)
        1.0
        >>> g_factor(2)
        1.5
        >>> g_factor(4)
        1.875
    """
    n = abs(goal_diff)
    if n <= 1:
        return 1.0
    if n == 2:
        return 1.5
    if n == 3:
        return 1.75
    return 1.75 + (n - 3) / 8.0


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def compute_elo(matches: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute Elo ratings for all teams from a processed match history.

    Processes rows chronologically (sorting by ``date`` internally).  For each
    match the pre-match ratings are recorded before the update so the returned
    ``home_elo_before`` / ``away_elo_before`` columns have no lookahead.

    Teams that appear for the first time in any match start at ELO_SEED_RATING
    (1500), which matches the global starting point at the first ever
    international in 1872.

    Args:
        matches: DataFrame with columns: date (Timestamp), home_team, away_team,
                 home_score (int), away_score (int), tournament (str),
                 neutral (bool).  Must contain only played matches (no NaN
                 scores) — unplayed placeholders must be filtered upstream.

    Returns:
        Tuple of:
        - *matches* sorted chronologically with two new columns:
            home_elo_before (float) — home team's Elo entering the match
            away_elo_before (float) — away team's Elo entering the match
        - elo_history DataFrame with columns: team, date, elo
            One row per (team, match): the team's rating *after* the match.
    """
    # Sort chronologically; stable sort preserves tie-break order within a day
    sorted_df = (
        matches
        .sort_values("date", kind="stable")
        .reset_index(drop=True)
    )

    current_elo: dict[str, float] = {}
    home_elos: list[float] = []
    away_elos: list[float] = []
    history_rows: list[dict] = []

    for row in sorted_df.itertuples(index=False):
        home: str = row.home_team
        away: str = row.away_team

        h_elo = current_elo.get(home, ELO_SEED_RATING)
        a_elo = current_elo.get(away, ELO_SEED_RATING)

        # Record pre-match ratings (the feature values — no lookahead)
        home_elos.append(h_elo)
        away_elos.append(a_elo)

        # Home advantage: zeroed when the match is played at a neutral venue
        h_adj = 0.0 if row.neutral else float(HOME_ADVANTAGE)

        # Expected result from the home team's perspective
        expected_home = 1.0 / (1.0 + 10.0 ** (-(h_elo + h_adj - a_elo) / 400.0))

        # Actual result: 1 = home win, 0.5 = draw, 0 = away win
        if row.home_score > row.away_score:
            actual = 1.0
        elif row.home_score < row.away_score:
            actual = 0.0
        else:
            actual = 0.5

        # K factor = K_base × goal-difference multiplier
        goal_diff = abs(row.home_score - row.away_score)
        k = k_base(row.tournament) * g_factor(goal_diff)

        # Zero-sum Elo update
        delta = k * (actual - expected_home)
        h_elo_new = h_elo + delta
        a_elo_new = a_elo - delta

        current_elo[home] = h_elo_new
        current_elo[away] = a_elo_new

        # History: one row per team, rating AFTER the match
        history_rows.append({"team": home, "date": row.date, "elo": round(h_elo_new, 4)})
        history_rows.append({"team": away, "date": row.date, "elo": round(a_elo_new, 4)})

    sorted_df["home_elo_before"] = home_elos
    sorted_df["away_elo_before"] = away_elos

    elo_history = pd.DataFrame(history_rows)

    n_teams = len(current_elo)
    print(f"    Elo computed for {n_teams:,} distinct teams across {len(sorted_df):,} matches")

    return sorted_df, elo_history


# ---------------------------------------------------------------------------
# Standalone data loading
# ---------------------------------------------------------------------------
# This section intentionally re-implements the minimal loading logic needed to
# run elo.py independently, without depending on build_dataset.py.
# When crosswalk.py is built, the normalisation functions below should be
# consolidated there and imported by both modules.


def _load_and_prepare() -> pd.DataFrame:
    """Load raw results and apply name normalisation for standalone Elo computation.

    Reads directly from ``data/raw/`` so the standalone script can run
    between the fetch scripts and build_dataset.py without requiring
    ``data/processed/matches.csv`` to exist first.

    Returns:
        Filtered, name-normalised match DataFrame with columns:
        date, home_team, away_team, home_score, away_score, tournament, neutral.
    """
    # --- Load results.csv ---
    df = pd.read_csv(
        RESULTS_RAW,
        parse_dates=["date"],
        dtype={"neutral": str},
    )
    n_total = len(df)
    df = df.dropna(subset=["home_score", "away_score"])
    n_dropped = n_total - len(df)
    if n_dropped:
        print(f"    Dropped {n_dropped:,} unplayed fixture rows (NaN scores)")
    df["home_score"] = df["home_score"].astype(int)
    df["away_score"] = df["away_score"].astype(int)
    df["neutral"] = df["neutral"].str.strip().str.upper() == "TRUE"
    print(f"    Loaded {len(df):,} played matches")

    # --- Load former_names.csv and normalise team names ---
    former = pd.read_csv(FORMER_NAMES_RAW, parse_dates=["start_date", "end_date"])

    # Build lookup: former_name → [(start, end, current), ...]
    lookup: dict[str, list[tuple[pd.Timestamp, pd.Timestamp, str]]] = {}
    for row in former.itertuples(index=False):
        key: str = row.former
        if key not in lookup:
            lookup[key] = []
        lookup[key].append((row.start_date, row.end_date, row.current))

    def resolve(name: str, match_date: pd.Timestamp) -> str:
        if name not in lookup:
            return name
        for start, end, current in lookup[name]:
            if start <= match_date <= end:
                return current
        return name

    dates = df["date"]
    df["home_team"] = [resolve(t, d) for t, d in zip(df["home_team"], dates)]
    df["away_team"] = [resolve(t, d) for t, d in zip(df["away_team"], dates)]
    print(f"    Applied name normalisation via former_names.csv ({len(former):,} entries)")

    return df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.features.elo",
        description=(
            "Compute Elo ratings for all national football teams from the\n"
            "full match history and write data/processed/elo_history.csv.\n\n"
            "Run this script between fetch_results / fetch_wdi and build_dataset:\n"
            "  1. python -m src.data.fetch_results  [--force]\n"
            "  2. python -m src.features.elo        [--force]\n"
            "  3. python -m src.data.build_dataset\n\n"
            "build_dataset.py also computes Elo internally — running this script\n"
            "separately produces the same elo_history.csv as a standalone output."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download results data before computing Elo.",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()

    print("=" * 60)
    print("Computing Elo ratings from full match history")
    print("=" * 60)

    if args.force:
        print("\nRefreshing raw data …")
        from src.data.fetch_results import fetch_all as _fetch_results
        _fetch_results(force=True)

    print("\nLoading match data …")
    raw_matches = _load_and_prepare()

    print("\nComputing Elo …")
    _, elo_history = compute_elo(raw_matches)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    elo_history.to_csv(ELO_HISTORY_PATH, index=False, date_format="%Y-%m-%d")

    n_teams = elo_history["team"].nunique()
    print(f"\n{'='*60}")
    print(f"  Written:  {ELO_HISTORY_PATH.relative_to(_REPO_ROOT)}")
    print(f"  Rows:     {len(elo_history):,}  ({n_teams:,} distinct teams)")
    print(f"{'='*60}")
