# Round-Archive Dropdown & URL Routing — Design Spec

**Date:** 2026-07-04
**Status:** Approved, pending implementation

## Problem

`montecarlo.py` re-runs the full knockout simulation each time a real round concludes and
`fixtures.csv` gets updated with actual results (Round of 32 → Round of 16 → Quarter-finals →
Semi-finals → Final). Today, each re-run overwrites `site/index.html` and
`site/data/results.json` in place — the previous round's predictions are lost the moment a new
round is simulated. We want every round's predictions preserved and reachable, with a dropdown
in the site header to move between them.

## Goals

- Every round's prediction set is archived permanently once produced; nothing gets overwritten
  except a re-run of the *same* round (refreshing that round's numbers is expected and fine).
- URL routing: `/current` (always the latest run), `/round32`, `/round16`, `/quarterfinal`,
  `/semifinal`, `/final`.
- Header dropdown listing every archived round as **"Predictions Round of 32"**,
  **"Predictions Round of 16"**, **"Predictions Quarter Final"**, **"Predictions Semi Final"**,
  **"Predictions Final"**, plus a pinned **"Current Predictions"** entry — but only showing
  entries that actually exist yet (no greyed-out future rounds).
- Which round is "current" is auto-detected from `fixtures.csv` — no manual flag to remember to
  pass.
- This must be applied to the run that's live right now (Round of 32) as part of this work —
  after implementation, the pipeline is actually executed against the real `fixtures.csv` so
  `data/site_archive/round32.json` and the `site/current/` + `site/round32/` pages exist and are
  correct. The *next* deployment to the droplet happens only once real Round of 16 results land
  and the site is rebuilt for that round — not as part of this task.

## Non-goals

- No change to the Monte Carlo engine itself, the predictor, or feature pipeline.
- No manifest file beyond what's implied by the files present in `data/site_archive/` — the
  directory listing *is* the manifest.
- No dynamic/JS-driven router. Every route is a real static directory served by Caddy's
  `file_server`; the dropdown is plain links.
- Deploying to the droplet (`scripts/deploy_site.sh`) is unchanged and is **not** run as part of
  this task — that remains a separate, explicit, manual step.

## Round detection

`fixtures.csv` already lets `BracketResolver` (`src/bracket/bracket.py`) tell a literal team name
apart from a `W<id>`/`L<id>` placeholder (`BracketResolver.is_placeholder`). Add:

```python
_ROUND_TAG_ORDER = ["Round of 32", "Round of 16", "Quarter-finals", "Semi-finals", "Final"]

def detect_frontier_round(self) -> str:
    """Return the deepest round whose matches are all fully known (no W#/L# slots).

    Walks _ROUND_TAG_ORDER; a round only counts once every one of its own
    matches has literal home_slot/away_slot values. Stops at the first round
    that still has any placeholder. "Third place play-off" is intentionally
    excluded — it shares its participants' resolution with "Final" and has no
    URL slug of its own.
    """
```

Given today's `data/knockout_fixtures/fixtures.csv`: Round of 32 (match_id 14, 15) is fully
literal (Argentina/Cabo Verde, Colombia/Ghana) → counts. Round of 16 has 6 literal matches but 2
placeholders (`W14`, `W15`) → does not fully count → detection stops. Result: `"Round of 32"`.
This matches what's live today. Once the real Round-of-32 winners are known and `W14`/`W15` in
`fixtures.csv` get replaced with literal team names, this same function returns `"Round of 16"`
on the next run, with no manual intervention.

Round name → URL slug / dropdown label mapping (lives alongside the detection function or in
`src/config.py`):

| `round` column value | slug | dropdown label |
|---|---|---|
| Round of 32 | `round32` | Predictions Round of 32 |
| Round of 16 | `round16` | Predictions Round of 16 |
| Quarter-finals | `quarterfinal` | Predictions Quarter Final |
| Semi-finals | `semifinal` | Predictions Semi Final |
| Final | `final` | Predictions Final |

## Archive storage

Each build writes a permanent snapshot to `data/site_archive/<slug>.json` — the same payload
shape `build_site.py` already produces (`generated_at`, `n_sims`, `teams`), plus `round_slug` and
`round_label`. `.gitignore` gets a `!/data/site_archive` exception carved out of the existing
blanket `/data` rule, since these snapshots are small and not regenerable once `fixtures.csv`
moves on to the next round's real results (unlike `data/processed/`, which is rebuilt from raw
data).

Re-running for a round that's already archived (e.g. re-simulating Round of 32 again before
Round of 16 starts, perhaps with a retrained model) overwrites that round's snapshot — it is
still "the Round of 32 prediction," just refreshed with new numbers.

## Build behavior

`build_site()` now:
1. Detects the current frontier round from `fixtures_path` (parameter, defaults to
   `FIXTURES_PATH`).
2. Writes/overwrites `data/site_archive/<slug>.json` for that round.
3. Globs **every** `data/site_archive/*.json` file present (not just the one just written).
4. Renders one HTML page per archived round into `site/<slug>/index.html` +
   `site/<slug>/data/results.json`, each with a dropdown built from the *full* set of archived
   rounds. This is what keeps old archived pages' dropdowns up to date once a newer round is
   added — every build regenerates every page, not just the newest.
5. Renders `site/current/index.html` + `site/current/data/results.json` as the same content as
   the latest (highest bracket-order) archived round.

Output layout:
```
site/
  current/index.html + data/results.json
  round32/index.html + data/results.json
  round16/...     (appears once Round of 16 is detected)
  quarterfinal/... 
  semifinal/...
  final/...
```
No more top-level `site/index.html` or `site/data/results.json`.

## Root URL

One-time addition to `caddy/world-cup.caddy`:
```
world-cup-simulation.lalutir.com {
    root * /home/lalutir/world-cup-predictor
    redir / /current
    file_server
}
```
This never needs touching again as rounds advance — only the site files get redeployed.

## Header dropdown

A `<details>/<summary>` element (no JS framework, matches the existing pill/badge styling),
reading e.g. "▾ Predictions Round of 32", placed in the header next to the title. Menu order,
top to bottom:
1. **Current Predictions** → `/current` (always present, always first)
2. Archived rounds in **descending bracket order** (most advanced first), each shown only if
   archived — e.g. once Round of 16 exists: "Predictions Round of 16 *(current)*", then
   "Predictions Round of 32".

Two independent flags per nav entry:
- **is_latest** — a data property of the round itself (is this the most advanced archived
  round); drives the "(current)" suffix text. True on exactly one round entry (and implicitly on
  the pinned Current Predictions entry).
- **active** — a per-rendered-page property (is this nav entry the page currently being viewed);
  drives the highlighted/active CSS state.

Because `/current` and the latest round's own page (e.g. `/round32` today) are rendered
separately to set `active` correctly, they are **not quite byte-identical** — everything (data,
charts, tables, "(current)" tag) is the same; only which single dropdown entry shows the active
highlight differs (Current Predictions vs. Predictions Round of 32). This is a deliberate,
minor refinement of "current is a static copy of latest," called out explicitly since it wasn't
in the original framing.

`<title>` also gets the round label appended (e.g. "2026 FIFA World Cup Predictor — Round of
32"), so browser tabs are distinguishable when `/current` and `/round32` are open side by side.

## Pipeline / file changes

- `src/bracket/bracket.py` — add `detect_frontier_round()` + the slug/label mapping.
- `src/site/build_site.py` — accept `fixtures_path`; read/write `data/site_archive/`; loop over
  all archived rounds to render each page + `/current`; extend the Jinja template context with
  `round_slug`, `round_label`, `nav_items`, `active_slug`.
- `src/site/templates/index.html.j2` — add the dropdown markup + CSS, round label in header and
  `<title>`.
- `src/simulator/montecarlo.py` — pass `fixtures_path` (already available as `args.fixtures`)
  through to `build_site()`; no new CLI flags.
- `src/config.py` — add `SITE_ARCHIVE_DIR = DATA_DIR / "site_archive"` and the slug/label
  mapping constant.
- `.gitignore` — add `!/data/site_archive`.
- `caddy/world-cup.caddy` — add the `redir` line.

## Testing

- `tests/test_bracket.py` — unit tests for `detect_frontier_round()` against hand-built fixture
  states: all-literal Round of 32 only → `"Round of 32"`; Round of 16 partially resolved (mirrors
  today's real `fixtures.csv`) → `"Round of 32"`; Round of 16 fully literal but QF still
  placeholders → `"Round of 16"`; all rounds through Final fully literal → `"Final"`.
- Manual verification after implementation: run the real pipeline against the current
  `fixtures.csv`, confirm `data/site_archive/round32.json` is written, confirm `/current` and
  `/round32` render correctly (including the active-nav distinction above), confirm the dropdown
  shows exactly "Current Predictions" and "Predictions Round of 32" (nothing else, since no other
  round is archived yet).

## Execution note for this task

Implementation must conclude by actually running the updated pipeline against the live
`data/knockout_fixtures/fixtures.csv`, so the real Round-of-32 archive and `/current` +
`/round32` pages exist on disk in `site/` and `data/site_archive/round32.json` — ready for
whenever the next deploy happens. Deploying to the droplet (`scripts/deploy_site.sh`) is *not*
part of this task and requires separate explicit confirmation.
