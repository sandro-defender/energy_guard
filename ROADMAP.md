# Energy Guard — improvement roadmap

A prioritised, actionable plan to make Energy Guard better. It is written from a
full review of the repository (code, tests, docs, packaging and the GitHub
state) on 2026-09-21, after release 1.0.2 and with 1.1.0 sitting unreleased in
`main`.

The plan is ordered by **trust first, quality second, features third** — the
same philosophy the integration itself follows: never break something people
depend on, prove every change, explain everything.

---

## What is already strong (keep doing this)

* **166 tests in 20 files**, including the six mandated reconnect scenarios,
  contract tests that enforce the safety model, and a Node-based smoke test for
  the panel JS.
* **Docs that are actually good**: `ARCHITECTURE.md`, `SAFETY.md`,
  `SERVICES.md`, `STATISTICS-REPAIR.md`, `DEVELOPMENT.md`, `CONFIGURATION.md`,
  `DASHBOARD.md` — rare for a custom integration.
* **A real safety model**: confirm flags, cost double-confirm, JSON backups
  before every write, read-back verification, read-only services.
* `ruff check` and `ruff format` pass clean on the current code.
* `RestoreSensor` seeds the guard after a restart, migrations exist, the
  device/brand assets and `icons.json` are in place.

The gaps below are almost all *infrastructure and distribution*, not core
logic.

---

## Phase 0 — Trust & release hygiene (quick wins, ~1 day)

### 0.1 Add CI — the tests badge is currently a lie

The README badge points at
`.github/workflows/tests.yml`, but **no `.github/` directory exists in the repo
and GitHub returns 404 for workflows**. 166 tests exist and nothing runs them
automatically. Create `.github/workflows/tests.yml`:

```yaml
name: Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  tests:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.13", "3.14"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: uv venv --python ${{ matrix.python-version }} .venv
      - run: uv pip install --python .venv/bin/python -r requirements_test.txt ruff
      - name: Lint
        run: |
          .venv/bin/ruff check .
          .venv/bin/ruff format --check .
      - name: Tests
        run: .venv/bin/python -m pytest tests/ -q --log-cli-level=CRITICAL

  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master

  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

`hassfest` catches manifest problems (the kind HACS and HA silently punish),
and the HACS action validates `hacs.json` on every push.

### 0.2 Add a `.gitignore`

The repo has **no `.gitignore` at all**. Minimum contents:

```gitignore
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
.coverage
coverage.xml
htmlcov/
# local manual-test scratch dirs (see DEVELOPMENT.md)
ha-config/
# anything the integration writes locally during development
backups/
reports/
```

### 0.3 Ship 1.1.0 and fix the version drift

Right now three different versions are visible:

| Where | Value |
| --- | --- |
| `custom_components/energy_guard/manifest.json` | `1.1.0` |
| `custom_components/energy_guard/const.py` (`VERSION`) | `1.1.0` |
| `pyproject.toml` | `1.0.0` |
| latest git tag / GitHub release | `1.0.2` |

The CHANGELOG announces `[1.1.0] - 2026-09-20` but no `1.1.0` tag exists, so
**HACS users still get 1.0.2** — all of the panel, dashboard and icon work is
invisible to them. Do:

1. Set `pyproject.toml` to `1.1.0` (or drop the field from being authoritative —
   but aligning it is cheaper than explaining it).
2. Tag `1.1.0` on the release commit and publish a GitHub Release with the
   CHANGELOG section as notes (HACS shows release notes in the UI).
3. Add a tiny contract test so this never drifts again:
   `manifest["version"] == const.VERSION == pyproject version`, and that the
   `Unreleased`/current section exists in `CHANGELOG.md`. Put it in
   `tests/test_contracts.py` next to the existing manifest assertions.

### 0.4 Repo hygiene files

* `.pre-commit-config.yaml` with the `ruff` hooks (`ruff` + `ruff-format`) —
  same rules as CI, zero-thought local checking. Pin the ruff version
  (currently 0.16.x) so a ruff release doesn't change CI results.
* `.editorconfig` (2-space JS / 4-space Python is already implied by tooling —
  make it explicit).
* `SECURITY.md` — for a project whose whole pitch is *“we touch your recorded
  energy data”*, a page saying how to report a safety-relevant bug privately is
  directly on-brand.
* `.github/ISSUE_TEMPLATE/`: one bug report form (HA version, installation via
  HACS?, recorder enabled?, relevant log lines) and one feature idea form. This
  alone will cut most back-and-forth in new issues.

### 0.5 Show the product

The config panel is the most impressive feature and the README shows none of
it. Add 1–3 screenshots or a short GIF of the Overview tab and the Repair
preview → confirm cards. This also feeds the HACS quality score (see 3.1).

**Phase 0 definition of done:** green CI badge that tells the truth, a `.gitignore`,
a published 1.1.0 that HACS users actually receive, issue templates, and at
least one screenshot in the README.

---

## Phase 1 — Quality infrastructure (1–2 weekends)

### 1.1 Coverage in CI

`[tool.coverage.run]` is already configured but nothing measures it. Add
`pytest-cov` to `requirements_test.txt`, upload to Codecov (or just print the
report and fail under a floor, e.g. `--cov-fail-under=90`). A coverage badge is
also a strong trust signal for a data-safety tool.

### 1.2 Test the *oldest* supported Home Assistant, not just the newest

* `hacs.json` declares `"homeassistant": "2025.5.0"` as the minimum.
* `requirements_test.txt` pins the suite to HA 2026.9.
* `recorder_io.py` and `statistics.py` are full of version-compatibility
  fallbacks (`try: import … except ImportError:`) that are currently only
  exercised on one HA version.

Either add a second CI job pinned to the oldest supported
`pytest-homeassistant-custom-component`, or honestly raise the minimum version
to what you actually test. Untested compatibility claims are how one-star
issues are born.

### 1.3 Translation infrastructure

Only `en.json` exists. Low effort, high reach:

1. Add a contract test that `translations/en.json` and `strings.json` stay in
   sync (same keys), so adding a string can't silently miss a language.
2. Add languages in order of likely users. Given GEL support, a Georgian
   (`ka.json`) translation is a natural first; `de`, `uk`, `pl`, `ro` are
   typically the next-most-active HA communities. Even partial coverage helps —
   HA falls back to English per-key.

### 1.4 Optional: type checking

`mypy` (or `pyright`) in strict-ish mode on `models.py` first — it is the
type boundary between the UI storage format and the runtime, exactly where a
`from_dict` bug does silent damage. If full-project typing is too noisy, type
the models + selectors and let the rest stay gradual.

**Phase 1 definition of done:** coverage measured and visible, compatibility
claim verified by CI or corrected, a second language shipped with a sync test.

---

## Phase 2 — Product features (ranked by value ÷ effort)

### 2.1 Tell the user *when* something is blocked  ⭐ top pick

Today the protection is silent unless the user watches the dashboard. The
diagnostic log and `sensor.energy_guard_last_anomaly` exist, but nothing pushes.
Add an opt-in per-sensor (or global) notification:

* fire a HA `persistent_notification` / `notify.mobile_app_*` when a reading is
  held back or a confirmed reset/jump is detected,
* include the plain-language `recommended_action` you already generate,
* with a quiet-hours / de-dup window so a flapping meter doesn't spam.

This turns Energy Guard from *invisible bodyguard* into *visible guardian*,
which is also the best possible marketing for the integration. The event data
is all already in `AnomalyEvent` — this is mostly presentation.

### 2.2 One-click rollback in the panel

The panel currently tells users backups are written *“Restore from these if a
repair went wrong”* — but restoring means leaving HA. There is already a
rollback path in the repair code; surface it: a “Backups” list in the Repair
tab with a per-file **Restore** action (admin + confirm, same double-confirm
rules as repair). Read the JSON backup, apply the inverse operation through the
existing recorder APIs, verify, log. Fits the safety model perfectly:
*nothing destructive without explicit confirmation — including undo.*

### 2.3 Beyond kWh: gas, water and other `total_increasing` meters

`units.py::is_energy_unit` gates everything, so the integration is
energy-only. The reconnect-artefact problem is identical for gas (`m³`, `ft³`)
and water (`m³`, `L`, `gal`) meters. The Energy Dashboard consumes those too
(and the cost statistics problem is the same). Path: generalise the unit gate
to unit *classes* (energy / volume), keep detection thresholds per class, extend
cost repair to volume tariffs. This roughly doubles the addressable audience
for a moderate, well-testable refactor.

### 2.4 “Protect your dashboard” advisor

Energy Guard waits for the user to pick sources. Flip it around (read-only,
so it stays inside the safety model): scan the Energy Dashboard config
(`energy` domain data) for `total_increasing` sensors that have no protected
twin, and list them on the panel home with an *“Add to protection”* shortcut
into the options flow. Also flag sources that still point at unprotected
sensors.

### 2.5 Blueprints

Ship 2–3 automations as blueprints in a `blueprints/` folder: “notify when
Energy Guard blocks a reading”, “notify when a repair needs confirmation”,
“daily false-energy digest”. Zero core risk, high visible value, and they
teach users the event model.

### 2.6 Energy Guard’s own long-term statistics

The blocked-readings/false-energy charts in the panel are built from the
bounded in-memory log, so they age out. Register Energy Guard’s own recorder
statistics (one per protected sensor: blocked count, estimated false energy)
so users get months of history for free and you can drop the “logged/bounded”
caveat on the charts.

### 2.7 Detection ideas backlog

* **DST transitions** — clock changes can look like a jump/back-skip; whitelist
  the known transition windows.
* **Meter swap detection** — a *persistent* drop to a lower stable baseline
  (not to zero) with a plausible new slope, distinct from a decrease-glitch.
* **Negative prices in cost repair** — some markets (and solar self-consumption
  accounting) produce negative cost samples; make sure the cost repair path
  handles or explicitly refuses them.
* **Multi-meter correlation** — one gateway outage usually hits *all* meters on
  that gateway at once; correlating simultaneous anomalies across sensors would
  raise confidence from “suspicious” to “reconnect artefact”.

**Phase 2 definition of done:** a user gets told when protection saves them,
can undo a repair from the UI, and owners of gas/water meters can use it too.

---

## Phase 3 — Community & reach

### 3.1 HACS default repository

Once Phase 0 screenshots + CI are in place, submit to the HACS default list.
Requirements: description + topics on the GitHub repo, screenshots in the
README, working CI, releases. Energy Guard’s safety story (“nothing changes
without confirmation, backups first, verified read-back”) is exactly what
reviewers like.

### 3.2 Demo & walkthrough

A 60–90 second GIF or video of the full loop — outage happens → reading
blocked → scan finds the offset → preview → confirm → repaired dashboard —
embedded in the README. This is the single highest-conversion asset the README
can have.

### 3.3 Distribution

* Home Assistant community forum “share your projects” thread (the feedback
  loop that finds real-world meters you don’t own).
* GitHub repo `topics`: `home-assistant`, `hacs`, `energy`, `statistics`,
  `recorder`, `home-automation`.
* After a couple of releases: a pinned “How Energy Guard fixed my dashboard”
  discussion so new users see real numbers (the `38,243.46 kWh` story is
  great — collect more).

### 3.4 Optional polish, only when bored

* Split the 1,485-line panel JS into a couple of `<script>`-module files *only
  if* it starts hurting maintenance — the single dependency-free file is a
  deliberate feature, don’t trade it for a build chain.
* Development docs: add `make test` / `make lint` targets or a `justfile` so
  the README one-liners stop depending on memory.

---

## Suggested order

```
Week 1        Phase 0 (CI, .gitignore, release 1.1.0, issue templates, screenshot)
Weeks 2–3     Phase 1 (coverage, oldest-HA job, ka/de translations)
Weeks 4–6     Phase 2.1 notifications  →  release 1.2.0
Then          Phase 2.2 rollback UI   →  release 1.2.1
Then          Phase 2.3 gas/water     →  release 2.0.0 (new sensor types = minor bump, but it *feels* like a 2.0)
Continuous    Phase 2.4–2.7 and Phase 3 as capacity allows
```

Each phase stands alone — you can stop after any of them and be strictly
better off than today. But if you do only **one** thing: make it **Phase 0.1
(CI)**. A safety-critical integration with 166 tests and no CI is a plane with
one engine that has never been started.
