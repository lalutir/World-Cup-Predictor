# Round-Archive Dropdown & URL Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every round's Monte Carlo predictions as a permanent, individually-addressable page (`/round32`, `/round16`, `/quarterfinal`, `/semifinal`, `/final`), with `/current` always mirroring the latest, and a header dropdown to move between them — instead of each rerun overwriting the previous round's site.

**Architecture:** `BracketResolver` gains a `detect_frontier_round()` method that inspects `fixtures.csv` and returns the deepest round whose matches are all fully known (no `W#`/`L#` placeholders left). `build_site()` archives each run's prediction payload permanently under `data/site_archive/<slug>.json`, then re-renders **every** archived round's page plus `/current` from the full set of archives on disk each time — so older pages' dropdowns stay current as new rounds are added. Everything stays a fully static file tree served by Caddy's `file_server`; only `/` gets a one-line `redir` to `/current`.

**Tech Stack:** Python 3.11, pandas, Jinja2, pytest. No new dependencies.

## Global Constraints

- Round names in `fixtures.csv`'s `round` column are exactly: `"Round of 32"`, `"Round of 16"`, `"Quarter-finals"`, `"Semi-finals"`, `"Third place play-off"`, `"Final"` (see `src/bracket/bracket.py::_ROUND_ORDER`). `"Third place play-off"` is intentionally excluded from round-tagging — it has no URL slug of its own and shares its participants' resolution with `"Final"`.
- Dropdown copy is exactly `"Predictions {label}"` where label ∈ {`Round of 32`, `Round of 16`, `Quarter Final`, `Semi Final`, `Final`}, plus a pinned `"Current Predictions"` entry.
- URL slugs are exactly: `round32`, `round16`, `quarterfinal`, `semifinal`, `final`, `current`.
- `data/` is blanket-gitignored today (`.gitignore` line 2: `/data`). Re-including a subdirectory of a wholesale-excluded parent **does not work** in git (verified empirically — `!/data/site_archive` alone still matches the parent `/data` rule and stays ignored). The parent rule itself must change to `/data/*` so git still traverses into `data/` to evaluate the negation.
- `site/` remains fully gitignored, regenerable build output — no top-level `site/index.html` or `site/data/` after this change.
- Deploying to the droplet (`scripts/deploy_site.sh`) is explicitly **out of scope** for this task and must not be run without separate confirmation.

---

## File Structure

```
src/
  bracket/
    bracket.py            # MODIFY: add FRONTIER_ROUNDS + detect_frontier_round()
  site/
    rounds.py              # CREATE: round name <-> slug/label mapping
    build_site.py           # MODIFY: archive I/O, multi-page render loop
    templates/
      index.html.j2         # MODIFY: round-switcher dropdown, round label, title
  simulator/
    montecarlo.py           # MODIFY: pass fixtures_path through to build_site()
  config.py                 # MODIFY: add SITE_ARCHIVE_DIR
tests/
  test_bracket.py           # MODIFY: add detect_frontier_round tests
  test_site_rounds.py       # CREATE: tests for src/site/rounds.py
  test_build_site.py        # CREATE: tests for archiving + multi-page render + template
.gitignore                  # MODIFY: /data -> /data/* + !/data/site_archive
caddy/world-cup.caddy       # MODIFY: add `redir / /current`
```

---

### Task 1: Round-frontier detection in `BracketResolver`

**Files:**
- Modify: `src/bracket/bracket.py`
- Test: `tests/test_bracket.py`

**Interfaces:**
- Produces: `FRONTIER_ROUNDS: list[str]` (module-level constant, bracket order, excludes `"Third place play-off"`); `BracketResolver.detect_frontier_round(self) -> str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_bracket.py`. First add `import pandas as pd` near the top (it's currently only imported locally inside two test functions) and a small helper, then the new test functions at the end of the file:

```python
import pandas as pd
```
(add this alongside the existing `import pytest` / `import sys` block at the top of the file)

```python
# ---------------------------------------------------------------------------
# detect_frontier_round
# ---------------------------------------------------------------------------


def _make_match(match_id, round_name, home, away):
    return Match(
        match_id=match_id,
        round=round_name,
        home_slot=home,
        away_slot=away,
        stadium="Test Stadium",
        date=pd.Timestamp("2026-07-01"),
    )


def test_frontier_stops_at_round_of_32_when_round_of_16_has_placeholders():
    matches = [
        _make_match(14, "Round of 32", "Argentina", "Cabo Verde"),
        _make_match(15, "Round of 32", "Colombia", "Ghana"),
        _make_match(1, "Round of 16", "Paraguay", "France"),
        _make_match(7, "Round of 16", "W14", "Egypt"),
        _make_match(8, "Round of 16", "Switzerland", "W15"),
    ]
    resolver = BracketResolver(matches)
    assert resolver.detect_frontier_round() == "Round of 32"


def test_frontier_advances_to_round_of_16_once_fully_literal():
    matches = [
        _make_match(14, "Round of 32", "Argentina", "Cabo Verde"),
        _make_match(1, "Round of 16", "Paraguay", "France"),
        _make_match(2, "Round of 16", "Canada", "Morocco"),
        _make_match(9, "Quarter-finals", "W1", "W2"),
    ]
    resolver = BracketResolver(matches)
    assert resolver.detect_frontier_round() == "Round of 16"


def test_frontier_reaches_final_once_all_rounds_literal():
    matches = [
        _make_match(1, "Round of 32", "Argentina", "Cabo Verde"),
        _make_match(2, "Round of 16", "Argentina", "France"),
        _make_match(3, "Quarter-finals", "Argentina", "Brazil"),
        _make_match(4, "Semi-finals", "Argentina", "Spain"),
        _make_match(5, "Final", "Argentina", "England"),
    ]
    resolver = BracketResolver(matches)
    assert resolver.detect_frontier_round() == "Final"


def test_frontier_ignores_third_place_playoff():
    """Third place play-off has no URL slug and shouldn't block detection."""
    matches = [
        _make_match(1, "Round of 32", "Argentina", "Cabo Verde"),
        _make_match(2, "Round of 16", "Argentina", "France"),
        _make_match(3, "Quarter-finals", "Argentina", "Brazil"),
        _make_match(4, "Semi-finals", "Argentina", "Spain"),
        _make_match(5, "Final", "Argentina", "England"),
        _make_match(6, "Third place play-off", "L4", "L4"),
    ]
    resolver = BracketResolver(matches)
    assert resolver.detect_frontier_round() == "Final"


def test_frontier_defaults_to_round_of_32_when_nothing_resolved():
    matches = [
        _make_match(1, "Round of 32", "W99", "L99"),
    ]
    resolver = BracketResolver(matches)
    assert resolver.detect_frontier_round() == "Round of 32"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bracket.py -k frontier -v`
Expected: FAIL with `AttributeError: 'BracketResolver' object has no attribute 'detect_frontier_round'`

- [ ] **Step 3: Implement `FRONTIER_ROUNDS` and `detect_frontier_round()`**

In `src/bracket/bracket.py`, add this constant right after `_PLACEHOLDER_RE = re.compile(r"^[WL]\d+$")` (currently line 74):

```python
# Rounds that get their own archived prediction page / URL slug on the
# results site. Bracket order matters here (used by detect_frontier_round
# and by the site's round-switcher sort order). "Third place play-off" is
# intentionally excluded -- it shares its participants' resolution with
# "Final" and has no URL slug of its own.
FRONTIER_ROUNDS: list[str] = [
    "Round of 32", "Round of 16", "Quarter-finals", "Semi-finals", "Final",
]
```

Then add this method to `BracketResolver`, placed right after `all_initial_teams()` (currently ending at line 235) and before the `# Neutral venue logic` section comment:

```python
    def detect_frontier_round(self) -> str:
        """Return the deepest round whose matches are all fully known.

        Walks FRONTIER_ROUNDS in bracket order. A round only counts once
        every one of its own matches has literal (non-placeholder) home_slot
        and away_slot values. Stops at the first round that still has any
        placeholder slot, or that isn't present in the fixtures at all.
        Defaults to "Round of 32" if even that round isn't fully resolved
        yet (shouldn't happen in practice -- Round of 32 participants are a
        given input, not something this project simulates).
        """
        grouped = dict(self.rounds_ordered())
        frontier = FRONTIER_ROUNDS[0]
        for round_name in FRONTIER_ROUNDS:
            matches = grouped.get(round_name)
            if not matches:
                break
            fully_resolved = all(
                not self.is_placeholder(m.home_slot) and not self.is_placeholder(m.away_slot)
                for m in matches
            )
            if fully_resolved:
                frontier = round_name
            else:
                break
        return frontier
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bracket.py -v`
Expected: PASS (all tests, including the 5 new `frontier` tests and every pre-existing test in the file)

- [ ] **Step 5: Commit**

```bash
git add src/bracket/bracket.py tests/test_bracket.py
git commit -m "$(cat <<'EOF'
Add BracketResolver.detect_frontier_round() for round auto-tagging

Determines the deepest bracket round whose matchups are fully known from
fixtures.csv, so the site builder can tag each rerun's predictions with
the correct round automatically.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Round slug/label mapping module

**Files:**
- Create: `src/site/rounds.py`
- Test: `tests/test_site_rounds.py`

**Interfaces:**
- Consumes: `FRONTIER_ROUNDS` from `src.bracket.bracket` (Task 1).
- Produces: `RoundMeta` dataclass (`round_name: str`, `slug: str`, `label: str`); `ROUND_META: list[RoundMeta]` (bracket order); `meta_for_round_name(round_name: str) -> RoundMeta`; `meta_for_slug(slug: str) -> RoundMeta`; `sort_key(round_name: str) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_site_rounds.py`:

```python
"""test_site_rounds.py — Unit tests for the round slug/label mapping."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.site.rounds import ROUND_META, meta_for_round_name, meta_for_slug, sort_key


def test_round_meta_has_five_entries():
    assert len(ROUND_META) == 5


def test_round_meta_order_matches_bracket_order():
    assert [m.round_name for m in ROUND_META] == [
        "Round of 32", "Round of 16", "Quarter-finals", "Semi-finals", "Final",
    ]


def test_meta_for_round_name():
    meta = meta_for_round_name("Round of 32")
    assert meta.slug == "round32"
    assert meta.label == "Round of 32"


def test_meta_for_slug():
    meta = meta_for_slug("quarterfinal")
    assert meta.round_name == "Quarter-finals"
    assert meta.label == "Quarter Final"


def test_sort_key_ascending_with_bracket_order():
    assert sort_key("Round of 32") < sort_key("Round of 16")
    assert sort_key("Round of 16") < sort_key("Quarter-finals")
    assert sort_key("Quarter-finals") < sort_key("Semi-finals")
    assert sort_key("Semi-finals") < sort_key("Final")


def test_labels_match_dropdown_convention():
    """Dropdown copy is "Predictions {label}" -- confirm exact wording."""
    expected = {
        "round32": "Round of 32",
        "round16": "Round of 16",
        "quarterfinal": "Quarter Final",
        "semifinal": "Semi Final",
        "final": "Final",
    }
    for slug, label in expected.items():
        assert meta_for_slug(slug).label == label
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_site_rounds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.site.rounds'`

- [ ] **Step 3: Create `src/site/rounds.py`**

```python
"""
rounds.py — Round name <-> URL slug / dropdown label mapping for the site.

Bridges the bracket's round names (as used in fixtures.csv and
BracketResolver, e.g. "Round of 32") to the static site's URL slugs
(e.g. "round32") and dropdown copy (e.g. "Round of 32" -> "Predictions
Round of 32"). Bracket order matters for sorting the round-switcher
dropdown -- reuses FRONTIER_ROUNDS from bracket.py as the single source of
truth for that order.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.bracket.bracket import FRONTIER_ROUNDS


@dataclass(frozen=True)
class RoundMeta:
    round_name: str   # e.g. "Round of 32" (matches fixtures.csv `round` column)
    slug: str         # e.g. "round32" (URL path segment)
    label: str        # e.g. "Round of 32" (used in "Predictions {label}")


_SLUGS_AND_LABELS: list[tuple[str, str]] = [
    ("round32",      "Round of 32"),
    ("round16",      "Round of 16"),
    ("quarterfinal", "Quarter Final"),
    ("semifinal",    "Semi Final"),
    ("final",        "Final"),
]

assert len(FRONTIER_ROUNDS) == len(_SLUGS_AND_LABELS)

ROUND_META: list[RoundMeta] = [
    RoundMeta(round_name, slug, label)
    for round_name, (slug, label) in zip(FRONTIER_ROUNDS, _SLUGS_AND_LABELS)
]

_BY_ROUND_NAME: dict[str, RoundMeta] = {m.round_name: m for m in ROUND_META}
_BY_SLUG: dict[str, RoundMeta] = {m.slug: m for m in ROUND_META}
_ORDER: dict[str, int] = {m.round_name: i for i, m in enumerate(ROUND_META)}


def meta_for_round_name(round_name: str) -> RoundMeta:
    """Return the RoundMeta for a fixtures.csv round name (e.g. "Round of 32")."""
    return _BY_ROUND_NAME[round_name]


def meta_for_slug(slug: str) -> RoundMeta:
    """Return the RoundMeta for a URL slug (e.g. "round32")."""
    return _BY_SLUG[slug]


def sort_key(round_name: str) -> int:
    """Bracket order index for sorting -- lower is earlier in the bracket."""
    return _ORDER[round_name]
```

Also create `src/site/__init__.py` if it doesn't already exist (check first — it already exists per the repo's current structure, so this step is likely a no-op).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_site_rounds.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/site/rounds.py tests/test_site_rounds.py
git commit -m "$(cat <<'EOF'
Add round name <-> URL slug/label mapping module

Single source of truth for the five archived-round URL slugs and their
dropdown copy, keyed off the bracket's own round ordering.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Config and `.gitignore` additions

**Files:**
- Modify: `src/config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `SITE_ARCHIVE_DIR: Path` (importable from `src.config`).

- [ ] **Step 1: Add `SITE_ARCHIVE_DIR` to `src/config.py`**

In the `# Site constants` section (currently lines 165–171), add this line right after `SITE_TEMPLATES_DIR`:

```python
# Permanent per-round prediction snapshots. Unlike the rest of data/
# (raw/processed -- gitignored and regenerable from source), these can't be
# reconstructed once fixtures.csv moves on to the next round's real
# results, so .gitignore carves out an exception for this one subdirectory.
SITE_ARCHIVE_DIR: Path = DATA_DIR / "site_archive"
```

- [ ] **Step 2: Verify the constant imports cleanly**

Run: `python -c "from src.config import SITE_ARCHIVE_DIR; print(SITE_ARCHIVE_DIR)"`
Expected: prints the absolute path ending in `data\site_archive` (or `data/site_archive` depending on OS), no errors.

- [ ] **Step 3: Update `.gitignore`**

Change the top of `.gitignore` from:

```
# Ignore the data directory
/data
```

to:

```
# Ignore the data directory, except the permanent per-round prediction
# archive (data/site_archive/ -- see src/config.py SITE_ARCHIVE_DIR).
# NOTE: this must be /data/* (not /data) for the negation below to work --
# git cannot re-include a child of a wholesale-excluded parent directory.
/data/*
!/data/site_archive
```

- [ ] **Step 4: Verify the exception works and everything else is still ignored**

Run:
```bash
mkdir -p data/site_archive && echo '{}' > data/site_archive/_verify.json
git check-ignore -v data/site_archive/_verify.json; echo "exit:$?"
git check-ignore -v data/raw/results.csv; echo "exit:$?"
rm data/site_archive/_verify.json
```
Expected: first `check-ignore` exits `1` (not ignored — trackable); second exits `0` (still ignored, matched by `/data/*`).

- [ ] **Step 5: Commit**

```bash
git add src/config.py .gitignore
git commit -m "$(cat <<'EOF'
Add SITE_ARCHIVE_DIR and carve out a .gitignore exception for it

Per-round prediction snapshots aren't regenerable the way data/processed/
is, so they get committed despite the blanket /data ignore rule.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `build_site.py` — round archiving + multi-page render backend

**Files:**
- Modify: `src/site/build_site.py`
- Test: `tests/test_build_site.py`

**Interfaces:**
- Consumes: `BracketResolver.detect_frontier_round()` (Task 1); `ROUND_META`, `meta_for_round_name`, `meta_for_slug`, `sort_key` from `src.site.rounds` (Task 2); `SITE_ARCHIVE_DIR` from `src.config` (Task 3).
- Produces: `build_site(results: pd.DataFrame, n_sims: int = N_SIMS, fixtures_path: Path | None = None, output_dir: Path | None = None, archive_dir: Path | None = None) -> Path`; `_build_nav_items(archived_slugs: set[str], latest_slug: str, active_slug: str) -> list[dict]`; `_load_archives(archive_dir: Path) -> dict[str, dict]` — these two are used directly by Task 5's template-content tests and are also exercised standalone here.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build_site.py`:

```python
"""
test_build_site.py — Unit/integration tests for the static site builder.

Covers round archiving (data/site_archive/<slug>.json), the round-switcher
dropdown nav list, and the multi-page render loop (/current + one page per
archived round) -- all against temp directories, no real project data
touched.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.site.build_site import _build_nav_items, _load_archives, build_site


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIXTURES_ROUND32 = """match_id,round,home_team,away_team,stadium,date
1,Round of 32,TeamA,TeamB,Test Stadium,2026-07-01
2,Round of 32,TeamC,TeamD,Test Stadium,2026-07-01
3,Round of 16,W1,W2,Test Stadium,2026-07-04
4,Quarter-finals,W3,W3,Test Stadium,2026-07-09
5,Semi-finals,W4,W4,Test Stadium,2026-07-14
6,Third place play-off,L5,L5,Test Stadium,2026-07-18
7,Final,W5,W5,Test Stadium,2026-07-19
"""

_FIXTURES_ROUND16 = """match_id,round,home_team,away_team,stadium,date
1,Round of 32,TeamA,TeamB,Test Stadium,2026-07-01
2,Round of 32,TeamC,TeamD,Test Stadium,2026-07-01
3,Round of 16,TeamA,TeamC,Test Stadium,2026-07-04
4,Quarter-finals,W3,W3,Test Stadium,2026-07-09
5,Semi-finals,W4,W4,Test Stadium,2026-07-14
6,Third place play-off,L5,L5,Test Stadium,2026-07-18
7,Final,W5,W5,Test Stadium,2026-07-19
"""


def _tiny_results_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"team": "TeamA", "exit_r32": 10.0, "exit_r16": 20.0, "exit_qf": 20.0,
         "exit_sf": 15.0, "third_place": 10.0, "runner_up": 10.0, "champion": 15.0},
        {"team": "TeamB", "exit_r32": 5.0, "exit_r16": 15.0, "exit_qf": 25.0,
         "exit_sf": 20.0, "third_place": 10.0, "runner_up": 15.0, "champion": 10.0},
    ])


@pytest.fixture
def site_dirs(tmp_path):
    return {
        "fixtures": tmp_path / "fixtures.csv",
        "site": tmp_path / "site",
        "archive": tmp_path / "site_archive",
    }


# ---------------------------------------------------------------------------
# _build_nav_items
# ---------------------------------------------------------------------------


def test_nav_items_single_archived_round():
    items = _build_nav_items({"round32"}, latest_slug="round32", active_slug="round32")
    assert [i["label"] for i in items] == ["Current Predictions", "Predictions Round of 32"]
    assert items[0]["active"] is False
    assert items[1]["active"] is True
    assert items[1]["show_current_tag"] is True


def test_nav_items_current_active():
    items = _build_nav_items({"round32"}, latest_slug="round32", active_slug="current")
    assert items[0]["active"] is True
    assert items[1]["active"] is False


def test_nav_items_two_rounds_newest_first():
    items = _build_nav_items(
        {"round32", "round16"}, latest_slug="round16", active_slug="round16"
    )
    labels = [i["label"] for i in items]
    assert labels == [
        "Current Predictions",
        "Predictions Round of 16",
        "Predictions Round of 32",
    ]
    tag_map = {i["label"]: i["show_current_tag"] for i in items}
    assert tag_map["Predictions Round of 16"] is True
    assert tag_map["Predictions Round of 32"] is False


# ---------------------------------------------------------------------------
# build_site -- archiving + multi-page render
# ---------------------------------------------------------------------------


def test_build_site_archives_round32(site_dirs):
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")

    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    archive_file = site_dirs["archive"] / "round32.json"
    assert archive_file.exists()
    payload = json.loads(archive_file.read_text(encoding="utf-8"))
    assert payload["round_slug"] == "round32"
    assert payload["round_label"] == "Round of 32"
    assert len(payload["teams"]) == 2


def test_build_site_writes_current_and_round_pages(site_dirs):
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")

    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    assert (site_dirs["site"] / "current" / "index.html").exists()
    assert (site_dirs["site"] / "current" / "data" / "results.json").exists()
    assert (site_dirs["site"] / "round32" / "index.html").exists()
    assert (site_dirs["site"] / "round32" / "data" / "results.json").exists()


def test_build_site_second_round_creates_both_archives(site_dirs):
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    site_dirs["fixtures"].write_text(_FIXTURES_ROUND16, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    archives = _load_archives(site_dirs["archive"])
    assert set(archives) == {"round32", "round16"}
    assert (site_dirs["site"] / "round32" / "index.html").exists()
    assert (site_dirs["site"] / "round16" / "index.html").exists()
    assert (site_dirs["site"] / "current" / "index.html").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_build_site.py -v`
Expected: FAIL — `ImportError: cannot import name '_build_nav_items' from 'src.site.build_site'` (the current `build_site.py` has neither this function nor the new `build_site()` signature yet).

- [ ] **Step 3: Rewrite `src/site/build_site.py`**

Replace the entire file with:

```python
"""
build_site.py — Generate the static World Cup predictor dashboard.

Called automatically after montecarlo.py completes, or standalone:
    python -m src.site.build_site results.csv
    python -m src.site.build_site results.csv --n-sims 1000000 --output-dir /tmp/site

Each run detects which round of the bracket has fully known participants
(via BracketResolver.detect_frontier_round), archives that round's
predictions permanently under data/site_archive/<slug>.json, and rebuilds
every archived round's page plus /current -- so old archived pages' round
switcher stays up to date as new rounds get simulated.

Output: site/<slug>/index.html + data/results.json for every archived
round, plus site/current/ mirroring the latest one.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.bracket.bracket import BracketResolver
from src.config import (
    FIXTURES_PATH,
    N_SIMS,
    PAST_WORLD_CUP_WINNERS,
    SITE_ARCHIVE_DIR,
    SITE_DIR,
    SITE_TEMPLATES_DIR,
)
from src.site.rounds import ROUND_META, meta_for_round_name, meta_for_slug, sort_key

# ---------------------------------------------------------------------------
# Bucket metadata
# ---------------------------------------------------------------------------

_BUCKET_ORDER = [
    "exit_r32", "exit_r16", "exit_qf", "exit_sf",
    "third_place", "runner_up", "champion",
]

_BUCKET_LABELS: dict[str, str] = {
    "exit_r32":    "Round of 32",
    "exit_r16":    "Round of 16",
    "exit_qf":     "Quarter-final",
    "exit_sf":     "Semi-final",
    "third_place": "3rd Place",
    "runner_up":   "Runner-up",
    "champion":    "Champion",
}


# ---------------------------------------------------------------------------
# Stat derivation
# ---------------------------------------------------------------------------

def _compute_team_stats(df: pd.DataFrame) -> list[dict]:
    """Derive display-ready stats from the raw simulation output DataFrame."""
    teams: list[dict] = []
    for _, row in df.iterrows():
        name = str(row["team"])
        r32  = float(row["exit_r32"])
        r16  = float(row["exit_r16"])
        qf   = float(row["exit_qf"])
        sf   = float(row["exit_sf"])
        tp   = float(row["third_place"])
        ru   = float(row["runner_up"])
        ch   = float(row["champion"])

        bucket_vals = {
            "exit_r32": r32, "exit_r16": r16, "exit_qf": qf,
            "exit_sf": sf, "third_place": tp, "runner_up": ru, "champion": ch,
        }
        best_bucket = max(bucket_vals, key=lambda k: bucket_vals[k])

        teams.append({
            "team":          name,
            "is_past_winner": name in PAST_WORLD_CUP_WINNERS,
            "exit_r32":    round(r32, 4),
            "exit_r16":    round(r16, 4),
            "exit_qf":     round(qf,  4),
            "exit_sf":     round(sf,  4),
            "third_place": round(tp,  4),
            "runner_up":   round(ru,  4),
            "champion":    round(ch,  4),
            "p_reach_r16":   round(100.0 - r32,              4),
            "p_reach_qf":    round(100.0 - r32 - r16,        4),
            "p_reach_sf":    round(100.0 - r32 - r16 - qf,   4),
            "p_reach_final": round(ru + ch,                   4),
            "most_likely_exit":     _BUCKET_LABELS[best_bucket],
            "most_likely_exit_pct": round(bucket_vals[best_bucket], 4),
        })
    return teams


# ---------------------------------------------------------------------------
# Round archive I/O
# ---------------------------------------------------------------------------

def _archive_path(archive_dir: Path, slug: str) -> Path:
    return archive_dir / f"{slug}.json"


def _write_archive(archive_dir: Path, slug: str, payload: dict) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    _archive_path(archive_dir, slug).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _load_archives(archive_dir: Path) -> dict[str, dict]:
    """Return {slug: payload} for every archived round found on disk, in
    bracket order."""
    archives: dict[str, dict] = {}
    if not archive_dir.exists():
        return archives
    for meta in ROUND_META:
        path = _archive_path(archive_dir, meta.slug)
        if path.exists():
            archives[meta.slug] = json.loads(path.read_text(encoding="utf-8"))
    return archives


def _latest_slug(archived_slugs: set[str]) -> str:
    """Return the slug with the deepest bracket order among archived_slugs."""
    return max(archived_slugs, key=lambda s: sort_key(meta_for_slug(s).round_name))


# ---------------------------------------------------------------------------
# Round-switcher nav
# ---------------------------------------------------------------------------

def _build_nav_items(
    archived_slugs: set[str], latest_slug: str, active_slug: str
) -> list[dict]:
    """Build the round-switcher dropdown: "Current Predictions" pinned
    first, then archived rounds in descending bracket order (most advanced
    first). Only slugs present in archived_slugs are included."""
    items: list[dict] = [{
        "label": "Current Predictions",
        "url": "/current/",
        "active": active_slug == "current",
        "show_current_tag": False,
    }]
    for meta in reversed(ROUND_META):
        if meta.slug not in archived_slugs:
            continue
        items.append({
            "label": f"Predictions {meta.label}",
            "url": f"/{meta.slug}/",
            "active": active_slug == meta.slug,
            "show_current_tag": meta.slug == latest_slug,
        })
    return items


def _format_generated_at(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_site(
    results: pd.DataFrame,
    n_sims: int = N_SIMS,
    fixtures_path: Path | None = None,
    output_dir: Path | None = None,
    archive_dir: Path | None = None,
) -> Path:
    """Generate the static dashboard from simulation results.

    Detects which round is the current "frontier" (deepest round whose
    matches are all fully known) from fixtures_path, archives this run's
    predictions under that round's slug, then rebuilds every archived
    round's page plus /current from the full set of archives on disk.

    Args:
        results:       DataFrame returned by MonteCarloSimulator.run().
        n_sims:        Number of simulations (used in page metadata).
        fixtures_path: Bracket CSV used to detect the current round
                       (default: FIXTURES_PATH).
        output_dir:    Root directory for the generated site (default:
                       SITE_DIR).
        archive_dir:   Directory for permanent per-round JSON snapshots
                       (default: SITE_ARCHIVE_DIR).

    Returns:
        Path to the generated site/current/index.html.
    """
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError as exc:
        raise ImportError(
            "Jinja2 is required to build the site.  Install it with: pip install jinja2"
        ) from exc

    fixtures_path = fixtures_path or FIXTURES_PATH
    out_dir      = output_dir or SITE_DIR
    archive_dir  = archive_dir or SITE_ARCHIVE_DIR

    resolver   = BracketResolver.from_csv(fixtures_path)
    round_name = resolver.detect_frontier_round()
    meta       = meta_for_round_name(round_name)

    team_stats = _compute_team_stats(results)
    now = datetime.now(timezone.utc)

    payload: dict = {
        "generated_at": now.isoformat(),
        "n_sims":       n_sims,
        "round_slug":   meta.slug,
        "round_label":  meta.label,
        "teams":        team_stats,
    }
    _write_archive(archive_dir, meta.slug, payload)

    archives       = _load_archives(archive_dir)
    archived_slugs = set(archives)
    latest_slug    = _latest_slug(archived_slugs)

    env = Environment(
        loader=FileSystemLoader(str(SITE_TEMPLATES_DIR)),
        autoescape=False,
    )
    template = env.get_template("index.html.j2")

    def _render_and_write(slug_payload: dict, target_dir: Path, active_slug: str) -> None:
        nav_items = _build_nav_items(archived_slugs, latest_slug, active_slug)
        active_label = next(item["label"] for item in nav_items if item["active"])

        html = template.render(
            data_json=json.dumps(slug_payload, separators=(",", ":")),
            n_sims_fmt=f"{slug_payload['n_sims']:,}",
            generated_at=_format_generated_at(slug_payload["generated_at"]),
            round_label=slug_payload["round_label"],
            nav_items=nav_items,
            active_nav_label=active_label,
        )

        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "index.html").write_text(html, encoding="utf-8")

        data_dir = target_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "results.json").write_text(
            json.dumps(slug_payload, indent=2), encoding="utf-8"
        )

    for slug, slug_payload in archives.items():
        _render_and_write(slug_payload, out_dir / slug, active_slug=slug)

    _render_and_write(archives[latest_slug], out_dir / "current", active_slug="current")

    current_index = out_dir / "current" / "index.html"
    print(f"\nDashboard built -> {current_index}  (latest round: {latest_slug})")
    return current_index


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        prog="python -m src.site.build_site",
        description="Build the static World Cup predictor dashboard from a simulation results CSV.",
    )
    p.add_argument("results", help="CSV file produced by montecarlo.py --output")
    p.add_argument(
        "--n-sims",
        type=int,
        default=N_SIMS,
        metavar="N",
        help=f"Number of simulations that produced the CSV (default: {N_SIMS:,})",
    )
    p.add_argument(
        "--fixtures",
        type=str,
        default=str(FIXTURES_PATH),
        metavar="PATH",
        help=f"Bracket fixtures CSV used to detect the current round (default: {FIXTURES_PATH})",
    )
    p.add_argument(
        "--output-dir",
        type=str,
        default=None,
        metavar="PATH",
        help="Root directory for the generated site (default: site/ in repo root)",
    )
    args = p.parse_args()

    df = pd.read_csv(args.results)
    build_site(
        df,
        args.n_sims,
        fixtures_path=Path(args.fixtures),
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_build_site.py -v`
Expected: PASS (all 6 tests)

Also re-run the full suite to confirm nothing else broke:
Run: `pytest -v`
Expected: PASS (all tests across test_bracket.py, test_site_rounds.py, test_build_site.py, test_elo.py, test_features.py, test_simulator.py)

- [ ] **Step 5: Commit**

```bash
git add src/site/build_site.py tests/test_build_site.py
git commit -m "$(cat <<'EOF'
Archive every round's predictions and rebuild all pages on each run

build_site() now detects the current bracket round, writes a permanent
snapshot to data/site_archive/<slug>.json, and re-renders every archived
round's page plus /current from the full archive set -- so old pages'
round-switcher stays in sync as new rounds are simulated.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Round-switcher dropdown in the template

**Files:**
- Modify: `src/site/templates/index.html.j2`
- Test: `tests/test_build_site.py` (append)

**Interfaces:**
- Consumes: `round_label: str`, `nav_items: list[dict]` (each with `label`, `url`, `active`, `show_current_tag`), `active_nav_label: str`, `generated_at: str`, `n_sims_fmt: str`, `data_json: str` — all already produced by `build_site()`'s `_render_and_write` (Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_build_site.py`:

```python
# ---------------------------------------------------------------------------
# Template rendering -- round label, title, dropdown markup
# ---------------------------------------------------------------------------


def test_rendered_page_shows_round_label_and_title(site_dirs):
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    html = (site_dirs["site"] / "round32" / "index.html").read_text(encoding="utf-8")
    assert "<title>2026 FIFA World Cup Predictor — Round of 32</title>" in html
    assert 'class="round-switcher"' in html


def test_dropdown_updates_on_older_page_after_new_round_archived(site_dirs):
    """Archiving Round of 16 must also rebuild the Round of 32 page's nav
    so its dropdown includes the newly archived round, with the
    "current" tag moved onto Round of 16."""
    site_dirs["fixtures"].write_text(_FIXTURES_ROUND32, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    site_dirs["fixtures"].write_text(_FIXTURES_ROUND16, encoding="utf-8")
    build_site(
        _tiny_results_df(),
        n_sims=1000,
        fixtures_path=site_dirs["fixtures"],
        output_dir=site_dirs["site"],
        archive_dir=site_dirs["archive"],
    )

    round32_html = (site_dirs["site"] / "round32" / "index.html").read_text(encoding="utf-8")
    assert "Predictions Round of 16" in round32_html
    assert 'href="/round16/"' in round32_html
    assert 'href="/round32/"' in round32_html

    current_html = (site_dirs["site"] / "current" / "index.html").read_text(encoding="utf-8")
    assert "Predictions Round of 16" in current_html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_build_site.py -k "round_label_and_title or dropdown_updates" -v`
Expected: FAIL — `assert '<title>2026 FIFA World Cup Predictor — Round of 32</title>' in html` fails because the current template's `<title>` is the static `2026 FIFA World Cup Predictor` with no round suffix, and there is no `round-switcher` element yet.

- [ ] **Step 3: Update `src/site/templates/index.html.j2`**

Change the `<title>` tag (currently line 6):

```html
  <title>2026 FIFA World Cup Predictor</title>
```
to:
```html
  <title>2026 FIFA World Cup Predictor — {{ round_label }}</title>
```

Add the round-switcher CSS. Insert this block right before the closing `</style>` tag (currently line 300), i.e. right after the existing `@media (max-width: 640px) { ... }` block:

```css
    /* ── Round switcher ───────────────────────────────────────── */
    .round-switcher {
      position: relative;
      display: inline-block;
    }

    .round-switcher summary {
      list-style: none;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 999px;
      background: rgba(59,130,246,.12);
      border: 1px solid rgba(59,130,246,.35);
      color: var(--text);
      font-size: .82rem;
      font-weight: 600;
      user-select: none;
      white-space: nowrap;
    }

    .round-switcher summary::-webkit-details-marker { display: none; }
    .round-switcher summary::marker { content: ""; }
    .round-switcher summary .chevron { font-size: .7rem; color: var(--blue); transition: transform .15s; }
    .round-switcher[open] summary .chevron { transform: rotate(180deg); }

    .round-switcher-menu {
      position: absolute;
      top: calc(100% + 6px);
      right: 0;
      min-width: 230px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      box-shadow: 0 12px 24px rgba(0,0,0,.35);
      overflow: hidden;
      z-index: 20;
    }

    .round-switcher-menu a {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 14px;
      color: var(--text-muted);
      text-decoration: none;
      font-size: .83rem;
      font-weight: 500;
      border-bottom: 1px solid rgba(51,65,85,.5);
    }

    .round-switcher-menu a:last-child { border-bottom: none; }
    .round-switcher-menu a:hover { background: rgba(255,255,255,.04); color: var(--text); }
    .round-switcher-menu a.active { color: var(--text); background: rgba(59,130,246,.08); }

    .round-switcher-menu .current-tag {
      font-size: .68rem;
      font-weight: 700;
      color: var(--green);
      background: rgba(34,197,94,.12);
      border: 1px solid rgba(34,197,94,.3);
      padding: 1px 7px;
      border-radius: 999px;
      white-space: nowrap;
    }

    @media (max-width: 640px) {
      .round-switcher-menu { right: auto; left: 0; }
    }
```

Add the dropdown markup and the round-label pill in the header. Replace the existing `<div class="header-meta">` block (currently lines 316–320):

```html
      <div class="header-meta">
        <div>Generated</div>
        <strong>{{ generated_at }}</strong>
        <div style="margin-top:4px;font-size:.72rem">world-cup-simulation.lalutir.com</div>
      </div>
```

with:

```html
      <div class="header-meta">
        <details class="round-switcher">
          <summary>{{ active_nav_label }} <span class="chevron">&#9662;</span></summary>
          <div class="round-switcher-menu">
            {% for item in nav_items %}
            <a href="{{ item.url }}" class="{{ 'active' if item.active else '' }}">
              <span>{{ item.label }}</span>
              {% if item.show_current_tag %}<span class="current-tag">current</span>{% endif %}
            </a>
            {% endfor %}
          </div>
        </details>
        <div style="margin-top:10px">Generated</div>
        <strong>{{ generated_at }}</strong>
        <div style="margin-top:4px;font-size:.72rem">world-cup-simulation.lalutir.com</div>
      </div>
```

Add the round-label pill alongside the existing pills. Replace the existing `.header-pills` block (currently lines 311–314):

```html
        <div class="header-pills">
          <span class="pill pill-green">{{ n_sims_fmt }} simulations</span>
          <span class="pill pill-gold">&#9733; = past World Cup winner</span>
        </div>
```

with:

```html
        <div class="header-pills">
          <span class="pill pill-blue">{{ round_label }}</span>
          <span class="pill pill-green">{{ n_sims_fmt }} simulations</span>
          <span class="pill pill-gold">&#9733; = past World Cup winner</span>
        </div>
```

Add the `.pill-blue` style next to the existing `.pill-gold` rule (currently lines 96–100):

```css
    .pill-gold {
      background: rgba(245,158,11,.12);
      border: 1px solid rgba(245,158,11,.3);
      color: var(--gold);
    }
```

becomes:

```css
    .pill-gold {
      background: rgba(245,158,11,.12);
      border: 1px solid rgba(245,158,11,.3);
      color: var(--gold);
    }

    .pill-blue {
      background: rgba(59,130,246,.12);
      border: 1px solid rgba(59,130,246,.3);
      color: var(--blue);
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_build_site.py -v`
Expected: PASS (all 8 tests in the file)

Run the full suite once more:
Run: `pytest -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/site/templates/index.html.j2 tests/test_build_site.py
git commit -m "$(cat <<'EOF'
Add round-switcher dropdown and round label to the site header

Native <details>/<summary> dropdown listing every archived round
("Predictions Round of 32", etc.) plus a pinned Current Predictions
entry, with the latest round tagged "current" and the active page
highlighted. Title and a header pill now show the round label too.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire `montecarlo.py` to pass `fixtures_path` through

**Files:**
- Modify: `src/simulator/montecarlo.py`

**Interfaces:**
- Consumes: `build_site(results, n_sims, fixtures_path=..., output_dir=...)` (Task 4's new signature — `fixtures_path` is a new optional kwarg, backward compatible).

- [ ] **Step 1: Update the `build_site` call**

In `src/simulator/montecarlo.py`, find the `__main__` block's site-building step (currently lines 517–525):

```python
    # Step 5: Build static dashboard (skippable with --no-site)
    if not args.no_site:
        print("\n[Pipeline 4/4] Building static dashboard …")
        try:
            from src.site.build_site import build_site
            build_site(results, n_sims=args.n)
        except ImportError as exc:
            print(f"  Skipped (missing dependency): {exc}")
            print("  Install with: pip install jinja2")
```

Change the `build_site(...)` call to pass `fixtures_path`:

```python
    # Step 5: Build static dashboard (skippable with --no-site)
    if not args.no_site:
        print("\n[Pipeline 4/4] Building static dashboard …")
        try:
            from src.site.build_site import build_site
            build_site(results, n_sims=args.n, fixtures_path=Path(args.fixtures))
        except ImportError as exc:
            print(f"  Skipped (missing dependency): {exc}")
            print("  Install with: pip install jinja2")
```

- [ ] **Step 2: Run the existing simulator test suite to confirm no regression**

Run: `pytest tests/test_simulator.py -v`
Expected: PASS (unchanged — `test_simulator.py` only exercises `MonteCarloSimulator` directly, never the CLI `__main__` block or `build_site`)

- [ ] **Step 3: Commit**

```bash
git add src/simulator/montecarlo.py
git commit -m "$(cat <<'EOF'
Pass fixtures_path through to build_site() from the CLI

montecarlo.py already has the fixtures path on hand via args.fixtures --
forwarding it lets build_site() auto-detect the current round instead of
needing a separate flag.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Caddy root redirect

**Files:**
- Modify: `caddy/world-cup.caddy`

**Interfaces:** None (server config only).

- [ ] **Step 1: Add the redirect line**

Change `caddy/world-cup.caddy` from:

```
world-cup-simulation.lalutir.com {
    root * /home/lalutir/world-cup-predictor
    file_server
}
```

to:

```
world-cup-simulation.lalutir.com {
    root * /home/lalutir/world-cup-predictor
    redir / /current
    file_server
}
```

- [ ] **Step 2: Verify the file is valid**

Run: `cat "caddy/world-cup.caddy"`
Expected: shows the 4-line block above, confirming the edit landed correctly. (No local Caddy install assumed — this file only takes effect on the droplet the next time it's deployed and Caddy is reloaded, which is out of scope for this task.)

- [ ] **Step 3: Commit**

```bash
git add caddy/world-cup.caddy
git commit -m "$(cat <<'EOF'
Redirect root URL to /current in the Caddy config

One-time addition so the bare domain always lands on the latest round's
predictions -- never needs touching again as new rounds are archived.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Run the real pipeline against live `fixtures.csv` and verify

**Files:** None modified — this task executes the already-implemented pipeline against real project data and verifies the output by hand.

**Interfaces:** None new.

- [ ] **Step 1: Run the full test suite one more time**

Run: `pytest -v`
Expected: PASS — every test across `test_bracket.py`, `test_site_rounds.py`, `test_build_site.py`, `test_elo.py`, `test_features.py`, `test_simulator.py`.

- [ ] **Step 2: Run the real pipeline against `data/knockout_fixtures/fixtures.csv`**

Run: `python -m src.simulator.montecarlo`

(This reuses already-downloaded data and already-trained models if present — it will only redo the parts that are missing. Takes a while for the full 1,000,000-simulation run.)

Expected: pipeline completes, ends with:
```
[Pipeline 4/4] Building static dashboard …

Dashboard built -> <repo>\site\current\index.html  (latest round: round32)

Done.
```

- [ ] **Step 3: Verify the archive and site output on disk**

Run:
```bash
ls data/site_archive/
ls site/
ls site/current/ site/round32/
git status --porcelain data/site_archive
```
Expected:
- `data/site_archive/` contains exactly `round32.json`.
- `site/` contains `current/` and `round32/` (no top-level `index.html` or `data/`).
- both `site/current/` and `site/round32/` contain `index.html` and `data/results.json`.
- `git status --porcelain data/site_archive` shows `round32.json` as untracked (ready to be added), confirming the `.gitignore` exception works against real data, not just the test's temp directories.

- [ ] **Step 4: Spot-check the rendered page in a browser or via curl**

Run: `grep -o '<title>[^<]*</title>' site/current/index.html`
Expected: `<title>2026 FIFA World Cup Predictor — Round of 32</title>`

Run: `grep -c 'round-switcher' site/current/index.html`
Expected: a nonzero count (the dropdown markup is present).

Open `site/current/index.html` directly in a browser (or `start site/current/index.html` on Windows) and confirm:
- The header shows a "Round of 32" pill and a dropdown reading "Predictions Round of 32 ▾".
- Opening the dropdown shows exactly two entries: "Current Predictions" and "Predictions Round of 32 (current tag)".
- The championship-odds chart and tables render with real data (Argentina, Brazil, etc.), not the tiny test fixture.

- [ ] **Step 5: Commit the archived round-32 snapshot**

```bash
git add data/site_archive/round32.json
git status --porcelain
git commit -m "$(cat <<'EOF'
Archive Round of 32 predictions as the first entry in the round history

First run of the new archiving pipeline against the live 2026 bracket.
site/ (current/ + round32/) is regenerated from this snapshot but stays
gitignored as build output -- only the permanent JSON snapshot is
committed here.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

**Do not run `scripts/deploy_site.sh`** as part of this task — deploying the rebuilt site to the droplet is a separate, explicit step for later (when Round of 16 predictions are ready, per the design spec's execution note).

---

## Plan Self-Review

**Spec coverage:**
- Round auto-detection from fixtures.csv → Task 1.
- Slug/label mapping (`round32` … `final`, dropdown copy) → Task 2.
- `data/site_archive/` storage + `.gitignore` exception (with the empirically-verified `/data/*` fix) → Task 3.
- Permanent archiving + regenerating every page on each build → Task 4.
- Dropdown UI, round label, title → Task 5.
- `montecarlo.py` wiring → Task 6.
- Root `/` → `/current` redirect → Task 7.
- "Apply to the current run (Round of 32) now; next deployment is for Round of 16" → Task 8 (execution only, no droplet deploy).

**Placeholder scan:** No TBD/TODO markers; every step has complete, runnable code or exact commands with expected output.

**Type consistency:** `build_site()` signature (`results, n_sims, fixtures_path, output_dir, archive_dir`) is identical between Task 4's implementation and Task 6's call site. `_build_nav_items(archived_slugs, latest_slug, active_slug)` signature matches across Task 4's implementation and Task 4/5's tests. `RoundMeta.label`/`.slug`/`.round_name` field names match between Task 2's implementation and Task 4's usage (`meta.slug`, `meta.label`).
