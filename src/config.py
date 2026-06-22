"""
config.py — Single source of truth for all paths, URLs, and numeric constants.

Every other module imports from here instead of hard-coding values.  This
makes it safe to move the repository, change data directories, or tune
constants without hunting across multiple files.

Path resolution is relative to this file's location so the project is
portable across machines and OS path separators — the hard-coded Windows
path in CLAUDE.md applies only to this particular checkout.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Repository layout
# ---------------------------------------------------------------------------

# Two levels up from src/config.py is the repository root.
REPO_ROOT: Path = Path(__file__).resolve().parents[1]

# Top-level data directory and its sub-directories.
DATA_DIR: Path = REPO_ROOT / "data"
RAW_DIR: Path = DATA_DIR / "raw"
WDI_RAW_DIR: Path = RAW_DIR / "wdi"      # raw JSON dumps from the World Bank API
PROCESSED_DIR: Path = DATA_DIR / "processed"
BRACKET_DIR: Path = DATA_DIR / "bracket"  # test bracket used by unit tests
KNOCKOUT_DIR: Path = DATA_DIR / "knockout_fixtures"  # real 2026 bracket

# ---------------------------------------------------------------------------
# Raw file paths (written by the fetch_* scripts, read by processing modules)
# ---------------------------------------------------------------------------

RESULTS_RAW: Path = RAW_DIR / "results.csv"
SHOOTOUTS_RAW: Path = RAW_DIR / "shootouts.csv"
FORMER_NAMES_RAW: Path = RAW_DIR / "former_names.csv"
WB_COUNTRIES_RAW: Path = WDI_RAW_DIR / "wb_countries.json"

# ---------------------------------------------------------------------------
# Processed file paths (written by feature/crosswalk modules)
# ---------------------------------------------------------------------------

MATCHES_PROCESSED: Path = PROCESSED_DIR / "matches.csv"
ELO_HISTORY_PATH: Path = PROCESSED_DIR / "elo_history.csv"
TOURNAMENT_TIERS_PATH: Path = PROCESSED_DIR / "tournament_tiers.csv"
COUNTRY_CROSSWALK_PATH: Path = PROCESSED_DIR / "country_crosswalk.csv"
FEATURES_PATH: Path = PROCESSED_DIR / "features.parquet"

# Real 2026 knockout bracket.
# Note: the repository currently has a fixture file at data/brackets/fixtures.csv
# (different sub-directory name).  The canonical location per the project spec
# is data/knockout_fixtures/fixtures.csv.  Reconcile by moving or symlinking
# the file before running the simulator.
FIXTURES_PATH: Path = KNOCKOUT_DIR / "fixtures.csv"

# Test bracket used by test_bracket.py (small synthetic 8-team bracket).
TEST_BRACKET_PATH: Path = BRACKET_DIR / "test_bracket.csv"

# ---------------------------------------------------------------------------
# Remote data sources
# ---------------------------------------------------------------------------

# martj42/international_results — confirmed live as of 2026-06-22.
RESULTS_URL: str = (
    "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
)
SHOOTOUTS_URL: str = (
    "https://raw.githubusercontent.com/martj42/international_results/master/shootouts.csv"
)
FORMER_NAMES_URL: str = (
    "https://raw.githubusercontent.com/martj42/international_results/master/former_names.csv"
)

# World Bank: full country list (used to strip out aggregate/regional entries).
WB_COUNTRY_LIST_URL: str = (
    "https://api.worldbank.org/v2/country/all?format=json&per_page=400"
)

# World Bank WDI indicator codes and their human-readable labels.
# Order determines which file is fetched first, but all three are always fetched.
WDI_INDICATORS: list[tuple[str, str]] = [
    ("SP.POP.TOTL",   "Population, total"),
    ("NY.GDP.MKTP.CD", "GDP, current US$"),
    ("NY.GDP.PCAP.CD", "GDP per capita, current US$"),
]

# URL template for a single indicator; {code}, {per_page}, {start_year},
# {end_year} are substituted at fetch time.
WDI_URL_TEMPLATE: str = (
    "https://api.worldbank.org/v2/country/all/indicator/{code}"
    "?format=json&per_page={per_page}&date={start_year}:{end_year}"
)

# Date range passed to the WDI API.  WDI has no data before 1960; for
# pre-1960 matches the feature layer flat-fills from the 1960 value.
WDI_DATE_START: int = 1960
WDI_DATE_END: int = 2026

# per_page value large enough to fit all country-years in a single page
# (~266 economies × 67 years ≈ 17,800 rows).  The fetch code checks the
# `pages` field in the metadata block and paginates automatically if this
# ever proves insufficient.
WDI_PER_PAGE: int = 20_000

# ---------------------------------------------------------------------------
# Elo constants
#
# ⚠️  VERIFY against the source repo (mar-antaya/world_cup_predictions) before
#     finalising elo.py.  The values below follow the standard eloratings.net
#     approach and are a safe starting point, but may differ from that repo's
#     exact K-tiers, goal multiplier, or home-advantage constant.
# ---------------------------------------------------------------------------

# Every team starts at ELO_SEED_RATING on the date of the first row in
# results.csv (Scotland v England, 1872-11-30).
ELO_SEED_DATE: str = "1872-11-30"
ELO_SEED_RATING: float = 1500.0

# Home advantage in Elo points.  Set to 0 when the `neutral` column is TRUE.
HOME_ADVANTAGE: float = 100.0

# K-base values by tournament tier.  The same tier table is reused for the
# `match_importance` context feature so there is only one notion of
# "how much this match matters" in the whole codebase.
#
# Key:   internal tier name (also used in tournament_tiers.csv).
# Value: K_base used in the Elo update formula.
ELO_K_BASE: dict[str, int] = {
    "world_cup_final":           60,
    "continental_championship":  60,
    "confederations_cup":        50,
    "nations_league_knockout":   50,
    "world_cup_qualifier":       40,
    "continental_qualifier":     40,
    "regional_cup":              40,
    "minor_tournament":          30,
    "friendly":                  20,
}

# ---------------------------------------------------------------------------
# Feature constants
# ---------------------------------------------------------------------------

# Windows (number of matches) used for recent-form features.
FORM_WINDOWS: tuple[int, int] = (5, 10)

# Exponential-decay half-life for head-to-head recency weighting.
# A match played 10 years ago has weight 0.5 relative to a match today.
H2H_HALF_LIFE_YEARS: float = 10.0

# ---------------------------------------------------------------------------
# Simulation constants
# ---------------------------------------------------------------------------

# Number of parallel Monte Carlo runs.
N_SIMS: int = 1_000_000

# Fixed seed for numpy.random.Generator(PCG64(RNG_SEED)) — ensures
# reproducible outputs when the same feature snapshot is used.
RNG_SEED: int = 42
