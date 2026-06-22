"""
build_dataset.py — Combine all raw data sources into a single processed dataset.

Always run without arguments — raw files are force-re-downloaded on every
invocation so the output stays in sync with the upstream sources.

    python -m src.data.build_dataset

Pipeline
--------
1. Force-refresh all raw files (results, shootouts, former_names, WDI).
2. Load results.csv; drop unplayed fixtures (NaN scores); coerce types.
3. Normalize team names using former_names.csv (historical → current name).
4. Merge shootout outcomes from shootouts.csv on (date, home_team, away_team).
5. Load the World Bank country list; identify non-aggregate ISO2 codes.
6. Load the three WDI indicator JSON files; build a forward-filled annual panel.
7. Resolve each team name to an ISO3 code; join economic data to every match.
8. Write data/processed/matches.csv.

Output schema (data/processed/matches.csv)
------------------------------------------
date                : ISO date (YYYY-MM-DD)
home_team           : canonical current team name (former names resolved)
away_team           : canonical current team name (former names resolved)
home_score          : integer goals in 90 + extra time (penalties not included)
away_score          : integer goals in 90 + extra time
tournament          : original tournament label from results.csv
city                : match city
country             : host country
neutral             : True/False (from results.csv; authoritative for real matches)
shootout_winner     : team name if the match went to penalties, else NaN
home_iso3           : ISO 3166-1 alpha-3 for home team (NaN if unresolvable)
away_iso3           : ISO 3166-1 alpha-3 for away team (NaN if unresolvable)
is_proxy_home       : True when home economic data is inherited from a parent state
is_proxy_away       : True when away economic data is inherited from a parent state
home_population     : WDI SP.POP.TOTL for home team's country in the match year
away_population     : WDI SP.POP.TOTL for away team's country in the match year
home_gdp            : WDI NY.GDP.MKTP.CD for home team's country in the match year
away_gdp            : WDI NY.GDP.MKTP.CD for away team's country in the match year
home_gdp_per_capita : WDI NY.GDP.PCAP.CD for home team's country in the match year
away_gdp_per_capita : WDI NY.GDP.PCAP.CD for away team's country in the match year

Data-quality notes
------------------
* WDI starts in 1960.  Pre-1960 match rows use the earliest available WDI
  value for each country (flat-fill per spec — no extrapolation).
* Non-sovereign football nations (England, Scotland, Wales, Northern Ireland,
  and a few others) have no independent WDI entry; they inherit economic data
  from their parent sovereign state (e.g. United Kingdom) and are flagged with
  is_proxy_home / is_proxy_away = True.
* Extinct states (Yugoslavia, Czechoslovakia, West Germany, etc.) resolve to
  NaN economic columns unless former_names normalization maps them to a still-
  existing entity (e.g. "West Germany" → "Germany").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import (
    ELO_HISTORY_PATH,
    FORMER_NAMES_RAW,
    MATCHES_PROCESSED,
    PROCESSED_DIR,
    RESULTS_RAW,
    SHOOTOUTS_RAW,
    WB_COUNTRIES_RAW,
    WDI_DATE_END,
    WDI_DATE_START,
    WDI_INDICATORS,
    WDI_RAW_DIR,
)
from src.data.fetch_results import fetch_all as _fetch_results
from src.data.fetch_wdi import fetch_all as _fetch_wdi
from src.features.elo import compute_elo

# ---------------------------------------------------------------------------
# Name mapping constants
# ---------------------------------------------------------------------------

# Football team names that differ from the World Bank's official country names,
# or that represent non-sovereign entities with no WDI entry of their own.
# Applied on top of the WB name → ISO3 mapping extracted from the indicator data.
#
# None values indicate extinct states for which no WDI proxy is appropriate;
# these rows get NaN economic columns in the output.
_FOOTBALL_TO_ISO3: dict[str, str | None] = {
    # UK constituent nations — no separate WDI entry; inherit United Kingdom
    "England":                   "GBR",
    "Scotland":                  "GBR",
    "Wales":                     "GBR",
    "Northern Ireland":          "GBR",
    # Korea — WB uses "Korea, Rep." and "Korea, Dem. People's Rep."
    "Korea Republic":            "KOR",
    "South Korea":               "KOR",
    "Korea DPR":                 "PRK",
    "North Korea":               "PRK",
    # Ivory Coast — both spellings appear across the historical dataset
    "Ivory Coast":               "CIV",
    "Côte d'Ivoire":             "CIV",
    # WB names carry political/formal qualifiers not used in football
    "Bolivia":                   "BOL",   # WB: "Bolivia"
    "Venezuela":                 "VEN",   # WB: "Venezuela, RB"
    "Iran":                      "IRN",   # WB: "Iran, Islamic Rep."
    "Syria":                     "SYR",   # WB: "Syrian Arab Republic"
    "Yemen":                     "YEM",   # WB: "Yemen, Rep."
    "Laos":                      "LAO",   # WB: "Lao PDR"
    "Vietnam":                   "VNM",   # WB: "Viet Nam"
    "Viet Nam":                  "VNM",
    "Kyrgyzstan":                "KGZ",   # WB: "Kyrgyz Republic"
    "Gambia":                    "GMB",   # WB: "Gambia, The"
    "The Gambia":                "GMB",
    "Bahamas":                   "BHS",   # WB: "Bahamas, The"
    "Micronesia":                "FSM",   # WB: "Micronesia, Fed. Sts."
    "DR Congo":                  "COD",   # WB: "Congo, Dem. Rep."
    "Congo DR":                  "COD",
    "Congo":                     "COG",   # WB: "Congo, Rep."
    "North Macedonia":           "MKD",
    "Macedonia":                 "MKD",   # pre-2019 name used in football records
    "Eswatini":                  "SWZ",
    "Swaziland":                 "SWZ",   # pre-2018 name
    "East Timor":                "TLS",   # WB: "Timor-Leste"
    "Palestine":                 "PSE",   # WB: "West Bank and Gaza"
    "Brunei":                    "BRN",   # WB: "Brunei Darussalam"
    "Czech Republic":            "CZE",   # pre-2016 official English name
    "Czechia":                   "CZE",
    "Cape Verde":                "CPV",
    "São Tomé and Príncipe":     "STP",
    "Sao Tome and Principe":     "STP",
    "Russia":                    "RUS",   # WB: "Russian Federation"
    "Burma":                     "MMR",   # WB: "Myanmar"
    "Myanmar":                   "MMR",
    "Curacao":                   "CUW",
    "São Paulo":                 None,    # city, not a country — guard against data quirks
    # Extinct states (after former_names normalization these mostly vanish; None
    # here is a safety net for any rows that slip through normalization)
    "West Germany":              None,    # → "Germany" via former_names → DEU
    "East Germany":              None,    # absorbed 1990; no surviving entity
    "Yugoslavia":                None,
    "Czechoslovakia":            None,
    "Soviet Union":              None,
    "United Arab Republic":      None,   # Egypt + Syria union 1958–1961
    "Saarland":                  None,
    "Serbian and Montenegrin":   None,
    "Zaire":                     None,
    "Rhodesia":                  None,
    "British Guiana":            None,
    "Dahomey":                   None,   # → "Benin" via former_names
    "Upper Volta":               None,   # → "Burkina Faso" via former_names
}

# Teams whose economic data is borrowed from a parent state.
# Rows involving these teams get is_proxy_home / is_proxy_away = True.
_PROXY_TEAMS: frozenset[str] = frozenset({
    "England", "Scotland", "Wales", "Northern Ireland",
})

# Friendly label used in console output for each WDI indicator column.
_INDICATOR_FRIENDLY: dict[str, str] = {
    "SP.POP.TOTL":    "population",
    "NY.GDP.MKTP.CD": "gdp",
    "NY.GDP.PCAP.CD": "gdp_per_capita",
}

# ---------------------------------------------------------------------------
# Step 1 — Re-download raw data
# ---------------------------------------------------------------------------


def _refresh_raw_data() -> None:
    """Force-download all raw files from their upstream sources.

    Always uses force=True so the output dataset reflects the latest data,
    including group-stage results played after the last run.
    """
    print("\n[1/6] Refreshing raw data …")
    print("  ── Results / shootouts / former names:")
    _fetch_results(force=True)
    print("  ── WDI economic indicators:")
    _fetch_wdi(force=True)


# ---------------------------------------------------------------------------
# Step 2 — Load and clean match results
# ---------------------------------------------------------------------------


def _load_results() -> pd.DataFrame:
    """Load results.csv and remove unplayed fixture rows (NaN scores).

    As of 2026-06-22 the upstream file contains schedule placeholders for
    future matches where home_score / away_score are NaN.  These must be
    dropped before any computation — they carry no outcome information.

    Returns:
        DataFrame with columns: date (Timestamp), home_team, away_team,
        home_score (int), away_score (int), tournament, city, country,
        neutral (bool).
    """
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
    # results.csv stores neutral as the string "True"/"False"
    df["neutral"] = df["neutral"].str.strip().str.upper() == "TRUE"

    print(f"    Loaded {len(df):,} played matches  ({df['date'].min().date()} – {df['date'].max().date()})")
    return df


# ---------------------------------------------------------------------------
# Step 3 — Normalize team names via former_names.csv
# ---------------------------------------------------------------------------


def _load_former_names() -> pd.DataFrame:
    """Load former_names.csv with start_date / end_date parsed as Timestamps."""
    df = pd.read_csv(FORMER_NAMES_RAW, parse_dates=["start_date", "end_date"])
    print(f"    Loaded {len(df):,} name-change entries covering {df['former'].nunique():,} distinct former names")
    return df


def _normalize_names(
    results: pd.DataFrame,
    former_names: pd.DataFrame,
) -> pd.DataFrame:
    """Replace historical team names with their canonical current names.

    For every match row, checks whether home_team or away_team was used as a
    former name during the relevant date range (start_date ≤ match_date ≤
    end_date in former_names.csv) and substitutes the canonical current name.

    This is what lets "West Germany" contribute to Germany's Elo history rather
    than appearing as a separate entity after 1990.

    Args:
        results:      Match DataFrame with a parsed ``date`` column.
        former_names: DataFrame with columns current, former, start_date, end_date.

    Returns:
        A copy of *results* with home_team and away_team updated in place.
    """
    # Build lookup dict: former_name → [(start, end, current), ...]
    lookup: dict[str, list[tuple[pd.Timestamp, pd.Timestamp, str]]] = {}
    for row in former_names.itertuples(index=False):
        key: str = row.former
        if key not in lookup:
            lookup[key] = []
        lookup[key].append((row.start_date, row.end_date, row.current))

    def resolve(name: str, match_date: pd.Timestamp) -> str:
        """Return the canonical name for *name* at *match_date*."""
        if name not in lookup:
            return name
        for start, end, current in lookup[name]:
            if start <= match_date <= end:
                return current
        return name  # name not within any recorded range; keep as-is

    results = results.copy()
    dates = results["date"]
    results["home_team"] = [resolve(t, d) for t, d in zip(results["home_team"], dates)]
    results["away_team"] = [resolve(t, d) for t, d in zip(results["away_team"], dates)]

    return results


# ---------------------------------------------------------------------------
# Step 4 — Merge shootout outcomes
# ---------------------------------------------------------------------------


def _load_shootouts() -> pd.DataFrame:
    """Load shootouts.csv; drop first_shooter per spec (sparsely populated)."""
    df = pd.read_csv(SHOOTOUTS_RAW, parse_dates=["date"])
    df = df[["date", "home_team", "away_team", "winner"]]
    print(f"    Loaded {len(df):,} shootout rows")
    return df


def _merge_shootouts(
    results: pd.DataFrame,
    shootouts: pd.DataFrame,
) -> pd.DataFrame:
    """Add a shootout_winner column to the results DataFrame.

    Performs a left join on (date, home_team, away_team).  Matches with no
    corresponding shootout row receive NaN in shootout_winner.

    Note: the join uses the already-normalized team names from *results*.
    The shootouts.csv team names may not all be normalized (they come from
    the same upstream source and use the same naming conventions), so
    unmatched names here will silently leave NaN.  A future improvement
    would normalize shootouts.csv names through the same former_names pass.

    Args:
        results:   Normalized match results DataFrame.
        shootouts: Shootout DataFrame with columns date, home_team, away_team, winner.

    Returns:
        *results* with a new ``shootout_winner`` column appended.
    """
    merged = results.merge(
        shootouts.rename(columns={"winner": "shootout_winner"}),
        on=["date", "home_team", "away_team"],
        how="left",
    )
    n_matched = merged["shootout_winner"].notna().sum()
    print(f"    Matched {n_matched:,} shootout outcomes to match rows")
    return merged


# ---------------------------------------------------------------------------
# Steps 5–6 — Load WDI data
# ---------------------------------------------------------------------------


def _load_wb_countries() -> set[str]:
    """Return the set of WB country codes for genuine (non-aggregate) entries.

    The World Bank country-list endpoint mixes real economies with regional
    and income-group aggregates (e.g. "Africa Eastern and Southern").
    Genuine countries have ``region.value != "Aggregates"``.

    The ``id`` field in the country-list response is the World Bank's own
    3-letter code (ISO 3166-1 alpha-3 for sovereign states; custom codes such
    as "EAS" or "EMU" for aggregate groups).  These 3-letter codes match the
    ``countryiso3code`` field in the WDI indicator data, which is what
    ``_load_wdi_raw`` uses for filtering.

    Returns:
        Set of 3-letter WB country codes for non-aggregate entries.
    """
    with WB_COUNTRIES_RAW.open("r", encoding="utf-8") as fh:
        _, countries = json.load(fh)

    valid: set[str] = {
        c["id"]
        for c in countries
        if c.get("region", {}).get("value", "") != "Aggregates"
    }
    print(f"    {len(valid):,} non-aggregate WB country entries identified")
    return valid


def _load_wdi_raw(
    valid_country_codes: set[str],
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Load the three WDI indicator JSON files into one long DataFrame.

    Also builds a WB country name → ISO3 mapping as a by-product of reading
    the JSON, so the caller doesn't have to open the files a second time.

    Aggregate rows are excluded by checking ``countryiso3code`` (3-letter)
    against *valid_country_codes*, which also contains 3-letter WB codes.
    This is the correct field to use — ``country.id`` in the indicator data
    is the 2-letter ISO2 code and does NOT match the 3-letter codes returned
    by ``_load_wb_countries``.

    Args:
        valid_country_codes: Set of 3-letter WB codes from _load_wb_countries.

    Returns:
        Tuple of:
        - DataFrame with columns iso3, year, SP.POP.TOTL, NY.GDP.MKTP.CD,
          NY.GDP.PCAP.CD (outer-joined across the three files).
        - Dict mapping WB country names to ISO3 codes (used to resolve
          football team names that match WB official names exactly).
    """
    indicator_codes = [code for code, _ in WDI_INDICATORS]
    frames: list[pd.DataFrame] = []
    wb_name_to_iso3: dict[str, str] = {}

    for code, label in WDI_INDICATORS:
        path = WDI_RAW_DIR / f"{code}.json"
        with path.open("r", encoding="utf-8") as fh:
            _, rows = json.load(fh)

        records: list[dict[str, Any]] = []
        for row in rows:
            iso3 = row["countryiso3code"]   # 3-letter — matches valid_country_codes
            wb_name = row["country"]["value"]
            # Exclude aggregate entries and rows with missing ISO3 codes
            if not iso3 or iso3 not in valid_country_codes:
                continue
            # Collect name → ISO3 mapping (first encounter wins)
            if wb_name and wb_name not in wb_name_to_iso3:
                wb_name_to_iso3[wb_name] = iso3
            records.append({"iso3": iso3, "year": int(row["date"]), code: row["value"]})

        df = pd.DataFrame(records)
        frames.append(df)
        print(f"    {code} ({label}): {len(df):,} data rows")

    if not frames:
        empty = pd.DataFrame(columns=["iso3", "year"] + indicator_codes)
        return empty, wb_name_to_iso3

    # Outer-join all three indicators on (iso3, year)
    combined = frames[0]
    for df in frames[1:]:
        combined = combined.merge(df, on=["iso3", "year"], how="outer")

    combined = combined.sort_values(["iso3", "year"]).reset_index(drop=True)
    print(f"    WDI combined: {combined['iso3'].nunique():,} countries, {len(combined):,} country-year rows")
    return combined, wb_name_to_iso3


def _build_name_to_iso3(
    wb_name_to_iso3: dict[str, str],
) -> dict[str, str | None]:
    """Merge WB name→ISO3 mapping with football-specific overrides.

    The WB mapping covers most standard country names (e.g. "France" → "FRA").
    The _FOOTBALL_TO_ISO3 override table handles:
      - FIFA naming conventions that differ from WB names
        (e.g. "Korea Republic" → "KOR" instead of WB's "Korea, Rep.")
      - Non-sovereign football nations with no WDI entry
        (e.g. "England" → "GBR" for proxy UK data)
      - Extinct states that have no valid WDI equivalent (mapped to None)

    Args:
        wb_name_to_iso3: Name→ISO3 dict extracted from the WDI indicator data.

    Returns:
        Merged dict; football-specific overrides take precedence over WB names.
    """
    result: dict[str, str | None] = dict(wb_name_to_iso3)
    result.update(_FOOTBALL_TO_ISO3)  # overrides win on key collision
    return result


def _build_wdi_panel(wdi_df: pd.DataFrame) -> pd.DataFrame:
    """Create a complete annual panel for every ISO3 code with forward-filled values.

    Fills the range WDI_DATE_START (1960) through WDI_DATE_END (2026) for each
    country, carrying each year's last known value forward to cover gaps in WDI
    publication.  Pre-1960 matches will clamp their lookup year to
    WDI_DATE_START, so they receive the 1960 (or earliest available) value —
    consistent with the flat-fill-backwards rule in the project spec.

    Args:
        wdi_df: Raw WDI DataFrame with columns iso3, year, <indicator codes>.

    Returns:
        DataFrame with the same columns plus complete year coverage, one row
        per (iso3, year) pair in [WDI_DATE_START, WDI_DATE_END].
    """
    indicator_cols = [code for code, _ in WDI_INDICATORS]
    all_years = list(range(WDI_DATE_START, WDI_DATE_END + 1))

    frames: list[pd.DataFrame] = []
    for iso3, group in wdi_df.groupby("iso3"):
        indexed = (
            group
            .set_index("year")[indicator_cols]
            .sort_index()
        )
        # Reindex to every year in the target range; carry values forward
        filled = indexed.reindex(all_years).ffill()
        filled.index.name = "year"
        filled = filled.reset_index()
        filled.insert(0, "iso3", iso3)
        frames.append(filled)

    if not frames:
        return pd.DataFrame(columns=["iso3", "year"] + indicator_cols)

    panel = pd.concat(frames, ignore_index=True)
    return panel[["iso3", "year"] + indicator_cols]


# ---------------------------------------------------------------------------
# Step 7 — Join WDI economic data to each match
# ---------------------------------------------------------------------------


def _add_wdi_columns(
    matches: pd.DataFrame,
    panel: pd.DataFrame,
    name_to_iso3: dict[str, str | None],
) -> pd.DataFrame:
    """Attach ISO3 codes and WDI economic data to every match row.

    For each match:
    - Resolves home_team and away_team to ISO3 codes via *name_to_iso3*.
    - Clamps the match year to WDI_DATE_START (1960) so pre-1960 matches
      receive the earliest available WDI value rather than a NaN.
    - Looks up population, GDP, and GDP-per-capita from the forward-filled
      *panel* for both teams.
    - Flags rows where the economic data is inherited from a parent state.

    Args:
        matches:      Normalized match DataFrame with date, home_team, away_team.
        panel:        Forward-filled WDI panel indexed by iso3 and year.
        name_to_iso3: Football team name → ISO3 code (None if unresolvable).

    Returns:
        A copy of *matches* with home_iso3, away_iso3, is_proxy_home/away,
        and six WDI metric columns appended.
    """
    matches = matches.copy()

    # Resolve team names → ISO3 codes; teams absent from the dict get NaN
    matches["home_iso3"] = matches["home_team"].map(name_to_iso3)
    matches["away_iso3"] = matches["away_team"].map(name_to_iso3)

    # Proxy flag: True when the WDI data belongs to a parent state, not the team itself
    matches["is_proxy_home"] = matches["home_team"].isin(_PROXY_TEAMS)
    matches["is_proxy_away"] = matches["away_team"].isin(_PROXY_TEAMS)

    # WDI lookup year: clamp pre-1960 matches to the earliest WDI year
    lookup_years = matches["date"].dt.year.clip(lower=WDI_DATE_START, upper=WDI_DATE_END)

    # Build a nested dict for O(1) lookups: {iso3: {year: {indicator: value}}}
    indicator_codes = [code for code, _ in WDI_INDICATORS]
    panel_lookup: dict[str, dict[int, dict[str, float]]] = {}
    for record in panel.to_dict("records"):
        iso3: str = record["iso3"]
        yr: int = int(record["year"])
        if iso3 not in panel_lookup:
            panel_lookup[iso3] = {}
        panel_lookup[iso3][yr] = {code: record.get(code) for code in indicator_codes}

    null_row: dict[str, float] = {code: np.nan for code in indicator_codes}

    def _lookup(iso3: Any, year: int) -> dict[str, float]:
        """Return WDI indicator values for *iso3* at *year*, or all-NaN."""
        if not isinstance(iso3, str):
            return null_row
        return panel_lookup.get(iso3, {}).get(year, null_row)

    # Vectorized lookups — one pass per team side
    home_values = [_lookup(iso3, yr) for iso3, yr in zip(matches["home_iso3"], lookup_years)]
    away_values = [_lookup(iso3, yr) for iso3, yr in zip(matches["away_iso3"], lookup_years)]

    # Write a column per indicator, using the human-readable friendly name
    for code in indicator_codes:
        friendly = _INDICATOR_FRIENDLY[code]
        matches[f"home_{friendly}"] = [v[code] for v in home_values]
        matches[f"away_{friendly}"] = [v[code] for v in away_values]

    # Diagnostics
    n_home_econ = matches["home_population"].notna().sum()
    n_away_econ = matches["away_population"].notna().sum()
    n_total = len(matches)
    print(f"    home economic data: {n_home_econ:,}/{n_total:,} rows resolved")
    print(f"    away economic data: {n_away_econ:,}/{n_total:,} rows resolved")

    return matches


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def build() -> None:
    """Run the full dataset-combination pipeline and write matches.csv.

    Always force-refreshes upstream raw data before processing.  Both output
    files are overwritten on every run.

    Outputs:
        data/processed/elo_history.csv  — per-team Elo rating after each match
        data/processed/matches.csv      — full combined dataset with Elo + WDI
    """
    # Step 1
    _refresh_raw_data()

    # Step 2
    print("\n[2/7] Loading and cleaning match results …")
    results = _load_results()

    # Step 3
    print("\n[3/7] Normalizing team names via former_names.csv …")
    former_names = _load_former_names()
    results = _normalize_names(results, former_names)

    # Step 4
    print("\n[4/7] Merging shootout outcomes …")
    shootouts = _load_shootouts()
    matches = _merge_shootouts(results, shootouts)

    # Step 5 — Elo ratings
    # compute_elo sorts matches chronologically and returns them in that order.
    # The two new columns (home_elo_before, away_elo_before) use only data
    # from matches that preceded each row — no lookahead.
    print("\n[5/7] Computing Elo ratings …")
    matches, elo_history = compute_elo(matches)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    elo_history.to_csv(ELO_HISTORY_PATH, index=False, date_format="%Y-%m-%d")
    print(f"    Written {len(elo_history):,} rows → {ELO_HISTORY_PATH.relative_to(_REPO_ROOT)}")

    # Steps 6–7 — WDI economic data
    print("\n[6/7] Loading WDI economic data …")
    valid_country_codes = _load_wb_countries()
    wdi_raw, wb_name_to_iso3 = _load_wdi_raw(valid_country_codes)
    name_to_iso3 = _build_name_to_iso3(wb_name_to_iso3)
    panel = _build_wdi_panel(wdi_raw)

    print("\n[7/7] Joining economic data to matches …")
    matches = _add_wdi_columns(matches, panel, name_to_iso3)

    # Write final output
    matches.to_csv(MATCHES_PROCESSED, index=False, date_format="%Y-%m-%d", encoding="utf-8")

    n_rows = len(matches)
    n_shootouts = matches["shootout_winner"].notna().sum()
    print(f"\n{'='*60}")
    print(f"  Written:      {MATCHES_PROCESSED.relative_to(_REPO_ROOT)}")
    print(f"  Rows:         {n_rows:,}")
    print(f"  Shootouts:    {n_shootouts:,} matches went to penalties")
    print(f"  Date range:   {matches['date'].min().date()} – {matches['date'].max().date()}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    print("=" * 60)
    print("Building combined dataset  (always re-downloads raw data)")
    print("=" * 60)
    build()
