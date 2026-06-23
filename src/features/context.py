"""
context.py — Match context features.

Adds two columns to a match DataFrame:

  is_neutral       : 1 if the match is at a neutral venue, 0 otherwise.
                     Taken directly from the `neutral` boolean column in matches.csv —
                     that column is authoritative for real matches (see CLAUDE.md).

  match_importance : normalised K-base for the tournament tier, in [0, 1].
                     Derived from the same tier table used by elo.py so there is
                     a single source of truth for "how much this match matters".
                     Scale: friendly (20/60 ≈ 0.33) → World Cup final (60/60 = 1.0).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.features.elo import k_base as _k_base


def compute_context(matches: pd.DataFrame) -> pd.DataFrame:
    """Add is_neutral and match_importance columns.

    Args:
        matches: DataFrame with columns: neutral (bool or int), tournament (str).

    Returns:
        A copy with two new columns appended.
    """
    df = matches.copy()
    df["is_neutral"] = df["neutral"].astype(int)
    df["match_importance"] = df["tournament"].apply(_k_base) / 60.0
    print(f"    context: added is_neutral and match_importance to {len(df):,} rows")
    return df
