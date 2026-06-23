# 2026 FIFA World Cup Knockout Predictor

A Python simulator for the knockout phase of the 2026 FIFA World Cup. Group-stage outcomes are treated as a fixed input — the project picks up from the Round of 32 and plays the bracket forward 1,000,000 times to produce per-team probability distributions across seven outcome buckets: Round of 32 exit through champion.

Live results: [world-cup-simulator.lalutir.com](https://world-cup-simulator.lalutir.com)

---

## How it works

### Pipeline overview

```
1. Fetch raw data       → data/raw/
2. Build dataset        → data/processed/matches.csv + elo_history.csv
3. Build features       → data/processed/features.parquet
4. Train models         → data/processed/model.pkl + shootout_model.pkl
5. Run simulation       → per-team outcome percentages
6. Build static site    → site/index.html
```

The full pipeline runs automatically on first use:

```bash
python -m src.simulator.montecarlo
```

### Elo ratings

Every team starts at 1500 on 1872-11-30 (the first international, Scotland vs England). Ratings update after every played match in `results.csv` using the standard World Football Elo formula:

```
expected_home = 1 / (1 + 10^(-(elo_home + H - elo_away) / 400))

H = 100 if home team is playing at home, 0 at a neutral venue

K = K_base(tournament) × G(|goal_diff|)

G multiplier:
  |diff| 0–1  →  1.000
  |diff| 2    →  1.500
  |diff| 3    →  1.750
  |diff| 4+   →  1.750 + (|diff| − 3) / 8

Δ = K × (actual − expected_home)
```

Tournament K-base tiers:

| K | Tournaments |
|---|---|
| 60 | FIFA World Cup, UEFA Euro, Copa América, AFCON, AFC Asian Cup |
| 50 | Confederations Cup, Nations League |
| 40 | World Cup qualifiers, continental qualifiers, Gold Cup, regional cups |
| 30 | CONIFA, Viva World Cup, other non-FIFA tournaments (default) |
| 20 | Friendlies |

### Match outcome model

A multinomial classifier (`outcome ∈ {home win, draw, away win}`) is trained on ~150 years of international results using the following feature vector:

| Feature | Description |
|---|---|
| `elo_gap` | Home Elo minus away Elo at match date |
| `home_elo`, `away_elo` | Absolute pre-match Elo ratings |
| `log_gdp_ratio` | log(home GDP / away GDP) at year of match |
| `log_pop_ratio` | log(home population / away population) |
| `log_gdp_per_capita_ratio` | log(home GDP per capita / away GDP per capita) |
| `home_win_rate_5/10` | Win rate over last 5 and 10 matches |
| `away_win_rate_5/10` | Win rate over last 5 and 10 matches |
| `home_goal_diff_5/10` | Average goal difference over last 5 and 10 matches |
| `away_goal_diff_5/10` | Average goal difference over last 5 and 10 matches |
| `home_rest_days`, `away_rest_days` | Days since each team's previous match |
| `h2h_home_win_rate` | Recency-weighted all-time head-to-head win rate |
| `h2h_total_weight` | Total weight of H2H evidence (proxy for sample size) |
| `is_neutral` | 1 if neutral venue, 0 if home-team advantage applies |
| `match_importance` | K-base tier normalised to [0, 1] |

Two candidates are compared on a chronological validation set (2022–2024): `LogisticRegression(multinomial)` and `HistGradientBoostingClassifier`. The one with lower log-loss on the validation set is kept. The chronological split (train < 2022, val 2022–2024, hold-out 2025+) prevents leakage.

**Important**: `results.csv` records the final score including extra time. A draw in the data effectively means *level after 90+ET*, which maps directly to how the simulator handles it: any draw outcome is immediately routed to penalty shootout with no separate extra-time simulation.

### Shootout model

A separate `LogisticRegression(C=0.01)` is fit on historical shootout outcomes as a function of the Elo gap between the two teams. Strong regularisation deliberately keeps the slope very flat — shootouts are historically close to a coin flip, and the model reflects that rather than over-fitting the Elo signal.

### Monte Carlo simulation

The simulator represents the full tournament as 1,000,000 parallel bracket runs. Each slot in the bracket holds an integer array of shape `(1_000_000,)` — one team index per simulated run.

For each match:
1. Find the set of unique `(home_idx, away_idx, is_neutral)` triplets that actually occur across the million simulations (far fewer than one million in practice — determined by which teams won prior rounds).
2. Call `predict_proba` **once per unique triplet**.
3. Vectorize the random draw across all simulations sharing that matchup.
4. Route drawn outcomes to the shootout model.
5. Accumulate per-team outcome-bucket counts.

Elo ratings are frozen at their pre-tournament snapshot for the full duration of each simulation run — no intra-simulation Elo updates as simulated rounds progress.

### Outcome buckets

Each team's simulation results are partitioned into exactly seven buckets (summing to 100%):

| Bucket | Meaning |
|---|---|
| `exit_r32` | Eliminated in Round of 32 |
| `exit_r16` | Eliminated in Round of 16 |
| `exit_qf` | Eliminated in Quarter-finals |
| `exit_sf` | Eliminated in Semi-finals and lost Third-place play-off |
| `third_place` | Eliminated in Semi-finals but won Third-place play-off |
| `runner_up` | Lost the Final |
| `champion` | Won the Final |

---

## Setup

**Requirements**: Python 3.11+

```bash
# Create and activate a virtual environment (optional but recommended)
python -m venv wc-predictor
source wc-predictor/bin/activate   # Linux/macOS
wc-predictor\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt
```

`requirements.txt`:
```
pandas>=2.0
numpy>=1.26
requests>=2.31
scikit-learn>=1.4
joblib>=1.3
pytest>=8.0
tqdm>=4.66
python-dateutil>=2.9
jinja2>=3.1      # required for the static site build
```

---

## Usage

### Run the full pipeline (recommended)

Runs everything — data fetch, model training, simulation, and site build — in one command:

```bash
python -m src.simulator.montecarlo
```

On first run this downloads ~50 MB of raw data and may take several minutes to train. Subsequent runs skip steps whose outputs already exist on disk.

### Options

```
--n N            Number of simulations (default: 1,000,000)
--fixtures PATH  Bracket CSV (default: data/knockout_fixtures/fixtures.csv)
--output PATH    Save results table to a CSV file
--rebuild        Force re-download of all raw data and retrain all models
--seed N         RNG seed (default: 42)
--no-site        Skip building the static HTML dashboard
```

### Run individual pipeline steps

```bash
# 1. Download/refresh raw match data (skip if already present)
python -m src.data.fetch_results

# 1a. Force re-download (e.g. after new real-world results are published)
python -m src.data.fetch_results --force

# 2. Download World Bank economic and population data
python -m src.data.fetch_wdi

# 3. Build processed dataset (Elo + economic features → matches.csv)
python -m src.data.build_dataset

# 4. Compute Elo history standalone (optional; also done inside build_dataset)
python -m src.features.elo

# 5. Train the match outcome model
python -m src.predictor.model

# 5a. Inspect validation metrics from a trained model
python -m src.predictor.model --eval

# 6. Train the shootout model
python -m src.predictor.shootout

# 7. Build the static site from an existing results CSV
python -m src.site.build_site results.csv
```

### Run tests

```bash
pytest
```

---

## Repository structure

```
.
├── README.md
├── CLAUDE.md                         # project spec and architecture notes
├── requirements.txt
├── data/
│   ├── raw/                          # untouched downloads — never modified after fetch
│   │   ├── results.csv               # ~49,500 internationals from 1872
│   │   ├── shootouts.csv             # penalty shootout outcomes
│   │   ├── former_names.csv          # historical team name changes
│   │   └── wdi/                      # World Bank JSON dumps + country list
│   ├── processed/                    # cleaned, joined, feature-enriched tables
│   │   ├── matches.csv               # results + shootouts + Elo + economic data
│   │   ├── elo_history.csv           # long-format Elo snapshots (team, date, elo)
│   │   ├── features.parquet          # model-ready feature table
│   │   ├── model.pkl                 # trained match-outcome model (joblib)
│   │   └── shootout_model.pkl        # trained shootout model (joblib)
│   ├── bracket/
│   │   └── test_bracket.csv          # synthetic 8-team bracket for unit tests
│   └── knockout_fixtures/
│       └── fixtures.csv              # real 2026 Round-of-32-onward bracket
├── src/
│   ├── config.py                     # all paths, URLs, and numeric constants
│   ├── data/
│   │   ├── fetch_results.py          # downloads results/shootouts/former_names
│   │   ├── fetch_wdi.py              # downloads World Bank GDP/population data
│   │   └── build_dataset.py          # joins raw sources into matches.csv
│   ├── features/
│   │   ├── elo.py                    # Elo computation + tournament tier rules
│   │   ├── form.py                   # win rate and goal difference windows
│   │   ├── h2h.py                    # recency-weighted head-to-head record
│   │   └── context.py                # neutral venue flag + match importance
│   ├── predictor/
│   │   ├── model.py                  # train() / predict_proba() / Predictor class
│   │   └── shootout.py               # train_shootout() / resolve_shootout()
│   ├── bracket/
│   │   ├── bracket.py                # Match dataclass + BracketResolver
│   │   └── templates.py              # official FIFA match-number skeleton (73–104)
│   ├── simulator/
│   │   └── montecarlo.py             # vectorized 1,000,000-run Monte Carlo engine
│   └── site/
│       ├── build_site.py             # reads simulation output, renders static site
│       └── templates/
│           └── index.html.j2         # Jinja2 template; Chart.js via CDN
├── site/                             # generated output (gitignored) — what gets deployed
│   ├── index.html
│   └── data/
│       └── results.json
├── scripts/
│   └── deploy_site.sh                # rsync to droplet over SSH
└── tests/
    ├── test_bracket.py               # resolver correctness against test_bracket.csv
    ├── test_elo.py                   # zero-sum updates, K-scaling, rating gap monotonicity
    ├── test_features.py              # schema checks + no-lookahead leakage assertions
    └── test_simulator.py             # per-team bucket sums; round advancement totals
```

---

## Data sources

### Match results — martj42/international_results

Three CSV files from [github.com/martj42/international_results](https://github.com/martj42/international_results):

- **`results.csv`** — ~49,500 international matches from 1872-11-30 through the live 2026 tournament. The `neutral` column is used as-is: FIFA World Cup matches where a host nation plays are correctly marked `neutral=FALSE`.
- **`shootouts.csv`** — date/home/away/winner for every match decided on penalties. Only the `winner` column is used; `first_shooter` is dropped (sparsely populated and not needed).
- **`former_names.csv`** — bridges historical name changes (e.g. Upper Volta → Burkina Faso, Dahomey → Benin) so a team's full Elo history isn't split across identities.

Unplayed 2026 fixture rows (NaN scores) are filtered out before any computation.

### Economic and population data — World Bank WDI

Three indicators pulled from the World Bank API:

| Code | Indicator |
|---|---|
| `SP.POP.TOTL` | Population, total |
| `NY.GDP.MKTP.CD` | GDP, current US$ |
| `NY.GDP.PCAP.CD` | GDP per capita, current US$ |

Data spans 1960–2026. For matches before 1960, each country's 1960 value is used (flat-fill backwards). Non-sovereign footballing nations without a World Bank entry (England, Scotland, Wales, Northern Ireland, etc.) inherit their parent state's economic data.

### 2026 knockout bracket

`data/knockout_fixtures/fixtures.csv` contains the full 32-match knockout bracket (matches 1–32, covering Round of 32 through the Final on 2026-07-19). Later rounds use `W<match_id>` / `L<match_id>` placeholders that the simulator resolves as each prior match is played.

As of 2026-06-23, the Round of 32 slots still contain group-stage placeholders (e.g. "Group A runners-up") — replace these with actual team names once the group stage concludes for accurate predictions.

---

## Key constants

All constants live in [`src/config.py`](src/config.py) — edit there to change behaviour globally.

| Constant | Value | Effect |
|---|---|---|
| `ELO_SEED_DATE` | `1872-11-30` | Date all teams start at 1500 |
| `ELO_SEED_RATING` | `1500.0` | Starting Elo for every team |
| `HOME_ADVANTAGE` | `100.0` | Elo points added for home side |
| `H2H_HALF_LIFE_YEARS` | `10.0` | Recency decay for head-to-head |
| `FORM_WINDOWS` | `(5, 10)` | Match windows for form features |
| `N_SIMS` | `1_000_000` | Monte Carlo iterations |
| `RNG_SEED` | `42` | NumPy PCG64 seed for reproducibility |
| `PAST_WORLD_CUP_WINNERS` | 8 nations | Argentina, Brazil, England, France, Germany, Italy, Spain, Uruguay |

---

## Results website

The static dashboard at [world-cup-simulator.lalutir.com](https://world-cup-simulator.lalutir.com) is built from the simulation output:

- **Championship odds** — `P(team wins the tournament)` for the 8 nations that have previously won a World Cup.
- **Full bracket table** — all 32 teams with their probability in each of the seven outcome buckets, sorted by championship %, plus a "Most Likely Exit" column.
- **Top-15 bar chart** — horizontal bar chart of championship % for the most competitive teams.
- **Footer** — simulation count, data-as-of date, and last-updated timestamp.

To rebuild and deploy after new real results:

```bash
python -m src.data.fetch_results --force   # pull latest results.csv
python -m src.simulator.montecarlo --rebuild
bash scripts/deploy_site.sh
```

---

## Design decisions

**No group-stage simulation.** The 32 teams entering the Round of 32 are a fixed input. This scope boundary keeps the model focused on what it does well (knockout match outcomes) and avoids the substantially harder problem of simulating group-stage dynamics.

**Draw = level after extra time.** `results.csv` records final scores inclusive of extra time, with penalty outcomes in a separate file. The model's "draw" class therefore maps cleanly to *"level after extra time, goes to penalties"* — no separate extra-time simulation is needed.

**Elo frozen during simulation.** Elo ratings are snapshotted before the tournament begins and held constant across all 1,000,000 runs. Intra-simulation Elo updates would require re-computing probabilities after each round for every distinct bracket path, adding complexity without materially improving accuracy for a 32-team single-elimination bracket.

**Vectorized NumPy, not an agent framework.** Simulating a fixed-shape bracket tree 1,000,000 times is a textbook NumPy operation — shape `(N_SIMS,)` integer arrays per slot, one `predict_proba` call per unique matchup per round, one vectorized random draw. SimPy/Mesa would add overhead with no benefit.

**Chronological train/val/test split.** The dataset is time-series data. Random splits would leak future information into training. The split is: train through 2021, validate 2022–2024, hold out 2025+.

---

## Inspiration

Inspired by [mar-antaya/world_cup_predictions](https://github.com/mar-antaya/world_cup_predictions) for the overall approach (Elo from history → feature model → Monte Carlo bracket). No code from that repository is reused.
