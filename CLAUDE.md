# CLAUDE.md

Guidance for Claude Code (and future-me) when working in this repository.

## Project Snapshot

A Python simulator for the **knockout phase only** of the 2026 FIFA World Cup. Group-stage
outcomes are treated as a given input (the 32 literal team names that emerge into the Round of
32) — this project does not simulate groups. For each knockout match the simulator estimates
win/draw/loss probabilities from a feature-based model, resolves draws via penalty shootout, and
plays the bracket forward 1,000,000 times to produce "how far does each country get" percentages.

As of today (2026-06-22) the real tournament is mid-group-stage, so `results.csv` (see below)
already contains the 2026 group games and a handful of *unplayed, NA-score* future fixtures —
that matters for the data pipeline, see [Known Data Quirks](#known-data-quirks).

## Inspiration & Non-Goals

- Inspired by [mar-antaya/world_cup_predictions](https://github.com/mar-antaya/world_cup_predictions) — **no code from that repo is reused**, it's a reference for the overall approach (Elo from history → feature model → Monte Carlo bracket).
- ⚠️ **Verify the Elo constants before finalizing `elo.py`.** I could not pull the actual source of that repo through available tools (it's a small repo that didn't surface via search/fetch). The formula in [ELO Methodology](#elo-methodology) below is the standard "World Football Elo Ratings" (eloratings.net-style) approach that virtually every basic Python implementation of this kind replicates — it's a safe, well-documented default. Open the real repo, diff the K-values / goal-multiplier / home-advantage constant against what's below, and adjust if they differ. Don't ship this assuming it's already a match.

## Data Sources

### 1. Historical results — `martj42/international_results`

Three files, all from the same GitHub repo. Confirmed live schemas (2026-06-22):

```text
results.csv       date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
shootouts.csv     date,home_team,away_team,winner,first_shooter
former_names.csv  current,former,start_date,end_date
```

- **results.csv**: ~49,500 rows, 1872-11-30 (Scotland v England, the first official international) through the live 2026 tournament. `neutral` is `TRUE`/`FALSE` and is already correct for host-nation games — e.g. USA-Belgium in Atlanta is `neutral=FALSE` because the US is playing at home, even at a World Cup. Use this column as-is rather than re-deriving it from city/country, **except** for matches you simulate yourself (see [Knockout Bracket](#knockout-bracket--fixtures)), where it must be computed.
- **shootouts.csv**: use `date` + `home_team` + `away_team` + `winner` to resolve matches that ended level. Per your spec, **drop `first_shooter`** — it's sparsely populated and isn't needed to determine who advanced.
- **former_names.csv**: bridges historical name changes (e.g. Dahomey → Benin, Upper Volta → Burkina Faso) so a team's full Elo history isn't split across two identities. There are 327 distinct `home_team` values in `results.csv` (includes defunct entities like Czechoslovakia, West Germany, Yugoslavia) — name normalization is real work here, not a formality.

**Fetch/refresh logic** (`src/data/fetch_results.py`):
- On first run, check whether the raw files exist in `data/raw/`. If not, download all three from the repo and write them.
- Provide a manual refresh path (e.g. `python -m src.data.fetch_results --force`) that re-downloads regardless of what's on disk, since `martj42/international_results` updates after real matches are played.
- Source files to pull:
  - `https://raw.githubusercontent.com/martj42/international_results/master/results.csv`
  - `https://raw.githubusercontent.com/martj42/international_results/master/shootouts.csv`
  - `https://raw.githubusercontent.com/martj42/international_results/master/former_names.csv`

### 2. Economic & population data — World Bank WDI

Confirmed call shape: `https://api.worldbank.org/v2/country/all/indicator/{CODE}?format=json&per_page=20000&date=1960:2026`

Response is `[metadata, data]`:
```json
[
  {"page": 1, "pages": 1, "per_page": 20000, "total": 17822, "lastupdated": "..."},
  [{"indicator": {"id": "SP.POP.TOTL", "value": "Population, total"},
    "country": {"id": "FR", "value": "France"},
    "countryiso3code": "FRA", "date": "2024", "value": 66548530,
    "unit": "", "obs_status": "", "decimal": 0}, ...]
]
```

Indicator codes to pull (one fetch per code, same URL pattern):

| Code | Meaning |
|---|---|
| `SP.POP.TOTL` | Population, total |
| `NY.GDP.MKTP.CD` | GDP, current US$ |
| `NY.GDP.PCAP.CD` | GDP per capita, current US$ |

Implementation notes:
- `country/all` returns **aggregates** too (regions, income groups, "World") mixed in with actual countries — e.g. "Africa Eastern and Southern" shows up as a `country` entry. Filter these out by cross-referencing `https://api.worldbank.org/v2/country/all?format=json&per_page=400`, which has a `region.value` field; keep only rows where `region.value != "Aggregates"`.
- `per_page=20000` should fit ~266 economies × 67 years in one page, but check the `pages` field in the metadata block and loop if it's ever `> 1` — don't assume one page forever.
- **Pre-1960 rule**: WDI has no data before 1960. For any match before 1960, use each country's 1960 value (forward-fill backwards, i.e. flat-line the earliest available year). Don't extrapolate or interpolate a trend.
- **Coverage gap, not a bug**: several footballing nations are not World Bank economies — England, Scotland, Wales, Northern Ireland have no separate WDI entry (they're part of "United Kingdom"); similar issues exist for Faroe Islands, Gibraltar, etc. Decide a fallback per case in `crosswalk.py` (e.g. UK constituent nations inherit United Kingdom's GDP/population, flagged with an `is_proxy_economic_data` column) rather than silently leaving NaNs that propagate into the model.

### 3. Knockout Bracket & Fixtures

Real bracket: `data/knockout_fixtures/fixtures.csv` (project root: `C:\Users\larsl\Documents\School\GenAI\Portfolio\World Cup Predictor\`).
Test bracket: `data/bracket/test_bracket.csv` — a small (e.g. 8-team / 7-match) synthetic bracket for unit tests, same schema as the real one. *(Your structure draft mentions `test_bracket.json` once in a code comment — standardizing on `.csv` everywhere, since `.json` never reappears elsewhere.)*

The actual 2026 knockout format (confirmed against FIFA's own published bracket): 32 teams enter the
Round of 32, then Round of 16 → Quarter-finals → Semi-finals → Third-place match + Final = **32
knockout matches total**. FIFA numbers these matches 73–104 and publishes later rounds using
exactly the placeholder style below (e.g. "Match 90 – Winner match 73 v Winner match 75") — so this
convention isn't invented, it's copying FIFA's own bracket sheets, which makes `fixtures.csv` easy
to fill in by hand from any official bracket graphic.

Proposed schema (used by both `fixtures.csv` and `test_bracket.csv`):

```text
match_id,round,home_team,away_team,stadium,date
1,Round of 32,Group A runners-up,Group B runners-up,Los Angeles Stadium,28-06-2026
2,Round of 32,Group E winners,Group A/B/C/D/F third place,Boston Stadium,29-06-2026
3,Round of 32,Group F winners,Group C runners-up,Estadio Monterrey,29-06-2026
4,Round of 32,Group C winners,Group F runners-up,Houston Stadium,29-06-2026
```

- `home_team`/`away_team`: either a literal team name (known for Round of 32, since the group stage is already decided) or a placeholder `W<match_id>` (winner of that match) / `L<match_id>` (loser — needed only for the third-place match).
- `is_neutral` is **not** a stored column — `bracket.py` derives it at resolution time as `venue_country not in {home_team's country, away_team's country}`, because for host nations (USA/Canada/Mexico) some knockout matches genuinely aren't neutral, and which team occupies a `W<id>` slot isn't known until that prior match is simulated.
- `templates.py` holds the official match-number skeleton (73–104, with FIFA's placeholder strings) — useful for validating a hand-entered `fixtures.csv` against the real bracket shape, and as a second template if you ever want to backtest the classic 32-team/Round-of-16 format against 2018/2022.

## Feature Specification

| Feature group | Module | Notes |
|---|---|---|
| Match results | `crosswalk.py` + `src/data/fetch_results.py` | Canonicalized via `former_names.csv`; shootout winners merged in (no `first_shooter`) |
| Elo rating + gap | `features/elo.py` | See below |
| Economic + population | `features/econ_pop.py` | Year-of-match join, pre-1960 flat-fill, proxy flag for nations without their own WDI entry |
| Recent form | `features/form.py` | Win rate and goal difference, separately over each team's last 5 and last 10 matches, **as of the match date** (no lookahead) |
| Rest days | `features/form.py` (or a small `rest.py`) | Days since each team's previous match of any kind, any tournament |
| Head-to-head | `features/h2h.py` | All-time H2H record between the two teams, recency-weighted (see constants below) |
| Context flags | `features/context.py` | `neutral` (straight from data, or derived for simulated matches); `match_importance` (reuse the same tournament-tier table that drives the Elo K-factor — don't build two separate notions of "how much this match matters") |

## ELO Methodology

⚠️ See the verification callout above — implement this, then diff against the source repo.

```text
expected_home = 1 / (1 + 10 ** (-(elo_home + H - elo_away) / 400))
# H = 100 if home_team is the true home side, 0 if neutral venue (use the `neutral` column)

K = K_base(tournament) * G(goal_difference)

G(0 or 1 goal diff)  = 1
G(2 goal diff)       = 1.5
G(3 goal diff)       = 1.75
G(N >= 4 goal diff)  = 1.75 + (N - 3) / 8

elo_home_new = elo_home + K * (actual_result - expected_home)   # actual_result: 1 win / 0.5 draw / 0 loss
elo_away_new = elo_away - K * (actual_result - expected_home)
```

- Start every team at **1500**, seeded at the start of `results.csv` (1872-11-30) — per your spec, *not* from 2006 like the inspiration repo.
- `K_base(tournament)` needs a tournament → tier lookup table — `results.csv` has ~200 distinct `tournament` strings (everything from `"FIFA World Cup"` to defunct regional cups like `"Copa Newton"`). Build this table once in `features/elo.py` (or a `data/processed/tournament_tiers.csv` it loads) and reuse it for the `match_importance` context feature too. A reasonable starting tier mapping:

| Tier | K_base | Examples |
|---|---|---|
| 60 | World Cup finals, continental championship finals (Euro, Copa América, AFCON, Asian Cup) | exact string `"FIFA World Cup"` — not `"...qualification"` |
| 50 | Confederations Cup, Nations League knockout | |
| 40 | World Cup qualifiers, continental qualifiers, regional cups (Gold Cup, etc.) | `"FIFA World Cup qualification"` |
| 30 | Minor tournaments / minor intercontinental | `"CONIFA World Cup qualification"`, `"Viva World Cup"` — these are **not** FIFA-sanctioned, match on the literal string |
| 20 | Friendlies | `"Friendly"` |

## Predictor Model

`predictor/model.py` exposes `train()` and `predict_proba(team_a, team_b, asof)` → `(p_home_win, p_draw, p_away_win)`.

- Recommend a multinomial classifier over the engineered feature vector (Elo gap, econ/pop, form, rest days, H2H, context flags) — e.g. `LogisticRegression(multi_class="multinomial")` as a baseline, or a gradient-boosted tree if it beats it on a held-out chronological split. This is a better fit than a pure Elo→Poisson goal model given you explicitly want econ/population/form/rest/H2H as model inputs, not just Elo.
- **No leakage**: every feature for a match on date `d` must only use data with date `< d`. Elo, form, rest days, and H2H are all naturally "as-of" if computed in chronological order — write the no-leakage check as an actual test (see [Testing](#testing-strategy)), not just a code comment.
- Split **chronologically** (e.g. train through 2021, validate 2022–2024, hold out 2025–2026), never randomly — this is time-series data.
- Important subtlety: `results.csv` final scores already include extra time for knockout matches (penalties aren't reflected in the scoreline; `shootouts.csv` is the separate source of truth for who won on penalties). So the model's "draw" class, trained on this data, effectively means *"level after extra time"* — that maps directly onto how `montecarlo.py` should use it: a simulated draw in a knockout match goes straight to `shootout.py`, with no separate extra-time goal simulation needed.

## Simulation Engine

**Recommendation: a single vectorized NumPy Monte Carlo engine — not a generic agent-based framework
like SimPy or Mesa.** This problem is "sample a categorical outcome 1,000,000 times for each match
in a small, fixed-shape bracket tree" — that's a textbook fit for NumPy vectorization across the
simulation axis, and a dedicated simulation framework would add overhead for no real benefit here.

- Represent the tournament as `n_sims = 1_000_000` parallel runs. For each bracket slot, hold an
  array of shape `(n_sims,)` of which team currently occupies it (object/categorical array or integer team-index array — prefer integer indices into a team list for speed).
- Process round by round (Round of 32 → ... → Final). Within a round, for each match: compute
  `predict_proba` **once** per distinct matchup that actually occurs across the million sims (not once per sim — group by the (home_team_idx, away_team_idx) pairs that resulted from the previous round, which will be far fewer than 1,000,000 in practice), then draw outcomes for all sims sharing that matchup with one vectorized `rng.random(k) < threshold` call.
- Draws route to `shootout.py`. Recommend calibrating shootout win probability from the actual
  historical `shootouts.csv` win rate as a function of Elo gap (shootouts are famously closer to a
  coin flip than open play — fit a much flatter logistic than the main model, not a 50/50 coin flip and not the same slope as `predict_proba`).
- **Design decision, stated explicitly so it isn't accidentally assumed away**: Elo ratings are
  frozen at their pre-tournament snapshot for the full duration of a single simulation run (no
  intra-tournament Elo updates as simulated rounds "happen"). This matches how comparable public
  models in this space operate and keeps the engine simple. If you want dynamic intra-simulation
  Elo updates later, that's a deliberate scope increase, not a bug fix.
- Output: per team, % of the 1,000,000 runs in which they were eliminated in the Round of 32 /
  Round of 16 / lost in the QF / lost in the SF / lost the Final / won the Final. These should sum
  to 100% per team (every team's runs partition exactly into one of those buckets) — assert this in tests.
- Use a seeded `numpy.random.Generator` (PCG64) for reproducibility; don't rely on the legacy global `numpy.random` state.

## Repository Structure

```text
.
├── CLAUDE.md
├── README.md
├── requirements.txt
├── data/
│   ├── raw/                       # untouched downloads
│   │   ├── results.csv
│   │   ├── shootouts.csv
│   │   ├── former_names.csv
│   │   └── wdi/                   # raw per-indicator JSON dumps + wb_countries.json
│   ├── processed/                 # name-normalized, joined feature tables
│   │   ├── matches.csv            # results + former_names + shootouts merged
│   │   ├── elo_history.csv        # long format: team, date, elo
│   │   ├── tournament_tiers.csv   # shared by elo.py and context.py
│   │   ├── country_crosswalk.csv  # team name <-> ISO3 <-> WDI entry (with proxy flags)
│   │   └── features.parquet       # final model-ready feature table
│   ├── bracket/
│   │   └── test_bracket.csv
│   └── knockout_fixtures/
│       └── fixtures.csv           # the real 2026 Round-of-32-onward bracket
├── src/
│   ├── config.py                  # paths & constants in one place (see note below)
│   ├── data/
│   │   ├── fetch_results.py       # results.csv + shootouts.csv + former_names.csv
│   │   └── fetch_wdi.py           # World Bank indicators
│   ├── crosswalk.py               # canonical name / ISO3 mapping, proxy-data fallbacks
│   ├── features/
│   │   ├── elo.py
│   │   ├── econ_pop.py
│   │   ├── form.py
│   │   ├── h2h.py
│   │   └── context.py
│   ├── predictor/
│   │   ├── model.py               # train() / predict_proba(team_a, team_b, asof)
│   │   └── shootout.py            # resolve_shootout(team_a, team_b)
│   ├── bracket/
│   │   ├── bracket.py             # Match dataclass + W<id>/L<id> resolver
│   │   └── templates.py           # official 73-104 match-number skeleton
│   └── simulator/
│       └── montecarlo.py          # the vectorized 1,000,000-run engine
└── tests/
    ├── test_bracket.py            # validates resolver against data/bracket/test_bracket.csv
    ├── test_elo.py                # zero-sum updates, monotonic in rating gap
    ├── test_features.py           # schema checks + no-lookahead-leakage assertions
    └── test_simulator.py          # per-team outcome buckets sum to 1; totals add up
```

**Deviations from your draft, and why:** I dropped `fifa_rank.py`, `league_strength.py`, and the
`clubelo/spi` raw-data mention from your structure sketch — they weren't in your explicit feature
list (results, Elo, econ/population, form, rest days, H2H, context flags), so I didn't want to
silently scope-creep the project. Easy to add back as a deliberate extension later if you want a
FIFA-ranking or club-strength feature. I added `src/config.py` (see below) and two test files
(`test_elo.py`, `test_features.py`) that weren't in your draft but earn their place given how easy
Elo and leakage bugs are to introduce silently.

## Key Constants & Decisions

Single source of truth, so these don't drift between modules:

- Elo seed date: `1872-11-30` (first row of `results.csv`). Seed rating: `1500` for every team.
- Home advantage: `+100` Elo points, zeroed when `neutral=TRUE`.
- Pre-1960 economic data: flat-fill from each country's 1960 WDI value.
- H2H recency weighting: exponential decay, default half-life **10 years** (`weight = 0.5 ** (years_ago / 10)`) — treat as a tunable hyperparameter, not a fixed truth.
- Recent form windows: last **5** and last **10** matches.
- Simulation count: **1,000,000** runs per invocation.
- `src/config.py` should centralize `DATA_DIR`, `RAW_DIR`, `PROCESSED_DIR`, `BRACKET_PATH`, `N_SIMS`, `ELO_SEED_DATE`, `ELO_SEED_RATING`, `HOME_ADVANTAGE`, etc. — resolve paths relative to the repo root (e.g. via `pathlib.Path(__file__).resolve().parents[1]`), rather than hardcoding the Windows path anywhere in source files. The absolute path is just where the repo happens to live on this machine right now.

## Conventions

- **Canonical team key**: ISO3 where one exists; for non-sovereign footballing nations without an ISO3 (England, Scotland, Wales, Northern Ireland, etc.) maintain a small manual registry in `crosswalk.py` rather than inventing pseudo-ISO3 codes ad hoc.
- **Dates**: store as ISO `YYYY-MM-DD`, parse once at the data layer, work with `pandas.Timestamp` everywhere downstream.
- **No lookahead**: any function that produces a feature for match date `d` takes `asof=d` (or is fed only data already filtered to `< d`) — make this explicit in function signatures, don't rely on call-site discipline.
- Keep `data/raw/` byte-for-byte what was downloaded; all cleaning happens on the way into `data/processed/`.

## Known Data Quirks

- `results.csv` already contains rows for unplayed 2026 fixtures (`home_score`/`away_score` = `NA`, dated this week as of 2026-06-22) — filter these out before computing Elo, form, H2H, or anything else; they're schedule placeholders, not results.
- `tournament` has ~200 distinct strings. Match on exact strings for tiering (e.g. `"FIFA World Cup"` vs `"FIFA World Cup qualification"` vs `"CONIFA World Cup qualification"` vs `"Viva World Cup"` — the latter two are not FIFA-sanctioned despite the name).
- `home_team`/`away_team` use full country names (e.g. `"United States"`, not `"USA"`) — keep `crosswalk.py`'s canonical-name mapping consistent with that convention rather than introducing abbreviations.

## Setup & Common Commands

Suggested `requirements.txt`:
```text
pandas>=2.0
numpy>=1.26
requests>=2.31
scikit-learn>=1.4
pytest>=8.0
tqdm>=4.66
python-dateutil>=2.9
```

Expected entry points (build these as you go, keep this list honest):
```bash
python -m src.data.fetch_results          # downloads results/shootouts/former_names if missing
python -m src.data.fetch_results --force  # always re-download
python -m src.data.fetch_wdi              # downloads GDP/GDP-per-capita/population
python -m src.simulator.montecarlo --n 1000000 --fixtures data/knockout_fixtures/fixtures.csv
pytest
```

## Testing Strategy

- `test_bracket.py`: build the small synthetic bracket in `data/bracket/test_bracket.csv`, hand-compute the expected winner propagation, assert the resolver matches it.
- `test_elo.py`: a single match's combined rating change is zero-sum; a fixed rating gap produces a higher win probability for the higher-rated side; K scales with goal margin as specified.
- `test_features.py`: schema/dtype checks on the processed feature table; assert no feature for match `d` was computed using any row with date `>= d`.
- `test_simulator.py`: for every team, the six outcome-bucket percentages from a run sum to ~1 (within Monte Carlo noise); total advancement counts across all teams per round match the bracket's actual round sizes (16 winners out of Round of 32, 8 out of Round of 16, etc.).

## Open Questions / Before You Go Deep

- Confirm the real mar-antaya Elo constants (K-tiers, goal multiplier, home advantage) against the source repo and reconcile with the table above.
- Decide the proxy-data policy per non-WDI footballing nation (inherit parent state's economic data? confederation average? leave null and let the model handle missingness?) — `crosswalk.py` needs one consistent rule, not ad hoc per-team choices.
- Decide whether group-stage-resolved team names get fed into `fixtures.csv` by hand each time, or whether a future version should ingest a live group-stage results feed — out of scope for now, but worth a one-line note in `README.md` so it's not mistaken for an oversight.
