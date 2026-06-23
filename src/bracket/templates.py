"""
templates.py — Official FIFA 2026 match-number skeleton (matches 73–104).

FIFA publishes the full 48-team + 32-team knockout schedule using match
numbers 73–104.  This module holds that skeleton as a reference so that a
hand-entered ``fixtures.csv`` (which uses internal 1-based match_ids) can be
validated against the real bracket shape.

Usage
-----
    from src.bracket.templates import FIFA_2026_TEMPLATE, validate_fixtures
    from src.config import FIXTURES_PATH

    issues = validate_fixtures(FIXTURES_PATH)
    for issue in issues:
        print(issue)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# FIFA official match-number skeleton (matches 73–104 = 32 knockout matches)
# ---------------------------------------------------------------------------
# Format: {internal_match_id: {"fifa_id": int, "round": str, "home": str, "away": str}}
# home/away are FIFA's own placeholder strings (e.g. "Winner Match 73").
# These are taken from FIFA's published bracket sheet for the 2026 World Cup.

FIFA_2026_TEMPLATE: dict[int, dict] = {
    # Round of 32 (matches 73–88 in FIFA numbering, 1–16 internally)
    1:  {"fifa_id": 73,  "round": "Round of 32",    "home": "Group A runners-up",        "away": "Group B runners-up"},
    2:  {"fifa_id": 74,  "round": "Round of 32",    "home": "Group E winners",            "away": "Best 3rd ABCDF"},
    3:  {"fifa_id": 75,  "round": "Round of 32",    "home": "Group F winners",            "away": "Group C runners-up"},
    4:  {"fifa_id": 76,  "round": "Round of 32",    "home": "Group C winners",            "away": "Group F runners-up"},
    5:  {"fifa_id": 77,  "round": "Round of 32",    "home": "Group I winners",            "away": "Best 3rd CDFGH"},
    6:  {"fifa_id": 78,  "round": "Round of 32",    "home": "Group E runners-up",         "away": "Group I runners-up"},
    7:  {"fifa_id": 79,  "round": "Round of 32",    "home": "Group A winners",            "away": "Best 3rd CEFHI"},
    8:  {"fifa_id": 80,  "round": "Round of 32",    "home": "Group L winners",            "away": "Best 3rd EHIJK"},
    9:  {"fifa_id": 81,  "round": "Round of 32",    "home": "Group D winners",            "away": "Best 3rd BEFIJ"},
    10: {"fifa_id": 82,  "round": "Round of 32",    "home": "Group G winners",            "away": "Best 3rd AEHIJ"},
    11: {"fifa_id": 83,  "round": "Round of 32",    "home": "Group K runners-up",         "away": "Group L runners-up"},
    12: {"fifa_id": 84,  "round": "Round of 32",    "home": "Group H winners",            "away": "Group J runners-up"},
    13: {"fifa_id": 85,  "round": "Round of 32",    "home": "Group B winners",            "away": "Best 3rd EFGIJ"},
    14: {"fifa_id": 86,  "round": "Round of 32",    "home": "Group J winners",            "away": "Group H runners-up"},
    15: {"fifa_id": 87,  "round": "Round of 32",    "home": "Group K winners",            "away": "Best 3rd DEIJL"},
    16: {"fifa_id": 88,  "round": "Round of 32",    "home": "Group D runners-up",         "away": "Group G runners-up"},
    # Round of 16 (matches 89–96 in FIFA numbering, 17–24 internally)
    17: {"fifa_id": 89,  "round": "Round of 16",    "home": "Winner Match 74",            "away": "Winner Match 77"},
    18: {"fifa_id": 90,  "round": "Round of 16",    "home": "Winner Match 73",            "away": "Winner Match 75"},
    19: {"fifa_id": 91,  "round": "Round of 16",    "home": "Winner Match 76",            "away": "Winner Match 78"},
    20: {"fifa_id": 92,  "round": "Round of 16",    "home": "Winner Match 79",            "away": "Winner Match 80"},
    21: {"fifa_id": 93,  "round": "Round of 16",    "home": "Winner Match 83",            "away": "Winner Match 84"},
    22: {"fifa_id": 94,  "round": "Round of 16",    "home": "Winner Match 81",            "away": "Winner Match 82"},
    23: {"fifa_id": 95,  "round": "Round of 16",    "home": "Winner Match 86",            "away": "Winner Match 88"},
    24: {"fifa_id": 96,  "round": "Round of 16",    "home": "Winner Match 85",            "away": "Winner Match 87"},
    # Quarter-finals (matches 97–100 in FIFA numbering, 25–28 internally)
    25: {"fifa_id": 97,  "round": "Quarter-finals", "home": "Winner Match 89",            "away": "Winner Match 90"},
    26: {"fifa_id": 98,  "round": "Quarter-finals", "home": "Winner Match 93",            "away": "Winner Match 94"},
    27: {"fifa_id": 99,  "round": "Quarter-finals", "home": "Winner Match 91",            "away": "Winner Match 92"},
    28: {"fifa_id": 100, "round": "Quarter-finals", "home": "Winner Match 95",            "away": "Winner Match 96"},
    # Semi-finals (matches 101–102, 29–30 internally)
    29: {"fifa_id": 101, "round": "Semi-finals",    "home": "Winner Match 97",            "away": "Winner Match 98"},
    30: {"fifa_id": 102, "round": "Semi-finals",    "home": "Winner Match 99",            "away": "Winner Match 100"},
    # Third-place and Final (matches 103–104, 31–32 internally)
    31: {"fifa_id": 103, "round": "Third place play-off", "home": "Loser Match 101",      "away": "Loser Match 102"},
    32: {"fifa_id": 104, "round": "Final",           "home": "Winner Match 101",          "away": "Winner Match 102"},
}

# Expected round sizes (number of matches per round) for a 32-team bracket.
EXPECTED_ROUND_SIZES: dict[str, int] = {
    "Round of 32":          16,
    "Round of 16":           8,
    "Quarter-finals":        4,
    "Semi-finals":           2,
    "Third place play-off":  1,
    "Final":                 1,
}


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------


def validate_fixtures(path: Path | str) -> list[str]:
    """Validate ``fixtures.csv`` against the official FIFA 2026 bracket shape.

    Checks:
    1. All 32 match_ids (1–32) are present.
    2. Each match's ``round`` string matches the template.
    3. Round sizes match expected counts.

    Does NOT validate team names — those change as the group stage resolves.

    Returns:
        List of validation-failure messages.  Empty list means the file is
        structurally valid.
    """
    issues: list[str] = []

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return [f"Cannot read fixtures file: {exc}"]

    required_cols = {"match_id", "round", "home_team", "away_team", "stadium", "date"}
    missing = required_cols - set(df.columns)
    if missing:
        issues.append(f"Missing columns: {sorted(missing)}")
        return issues

    present_ids = set(df["match_id"].astype(int))
    expected_ids = set(range(1, 33))
    missing_ids = expected_ids - present_ids
    extra_ids = present_ids - expected_ids
    if missing_ids:
        issues.append(f"Missing match_ids: {sorted(missing_ids)}")
    if extra_ids:
        issues.append(f"Unexpected match_ids: {sorted(extra_ids)}")

    round_counts: dict[str, int] = df["round"].value_counts().to_dict()
    for round_name, expected_count in EXPECTED_ROUND_SIZES.items():
        actual = round_counts.get(round_name, 0)
        if actual != expected_count:
            issues.append(
                f"Round '{round_name}': expected {expected_count} matches, found {actual}"
            )

    for _, row in df.iterrows():
        mid = int(row["match_id"])
        if mid not in FIFA_2026_TEMPLATE:
            continue
        expected_round = FIFA_2026_TEMPLATE[mid]["round"]
        actual_round = str(row["round"]).strip()
        if actual_round != expected_round:
            issues.append(
                f"Match {mid}: expected round '{expected_round}', got '{actual_round}'"
            )

    return issues


# ---------------------------------------------------------------------------
# CLI — print validation report
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    from src.config import FIXTURES_PATH

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURES_PATH
    print(f"Validating {path} …")
    problems = validate_fixtures(path)
    if not problems:
        print("  OK — fixtures.csv matches the official 2026 bracket shape.")
    else:
        for p in problems:
            print(f"  FAIL: {p}")
        sys.exit(1)
