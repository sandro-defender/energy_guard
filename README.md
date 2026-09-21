<p align="center">
  <img src="custom_components/energy_guard/brand/logo.png" alt="Energy Guard - Protect your Energy Dashboard" width="600">
</p>

# Energy Guard

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/sandro-defender/energy_guard)
[![GitHub Release](https://img.shields.io/github/release/sandro-defender/energy_guard.svg)](https://github.com/sandro-defender/energy_guard/releases)
[![Tests](https://img.shields.io/github/actions/workflow/status/sandro-defender/energy_guard/tests.yml?label=tests)](https://github.com/sandro-defender/energy_guard/actions)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-2026.3+-41BDF5.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Energy Guard protects your Home Assistant Energy Dashboard from reconnect
artefacts and repairs statistics that were already corrupted.**

When an energy meter or gateway reconnects it often publishes `unavailable`,
then `0`, and then its lifetime value again. Home Assistant's recorder counts
that as real energy, so a single 5 minute outage can add the whole lifetime
value of your meter to the Energy Dashboard (`38,243.46 kWh`, and a matching
monetary offset, is a typical amount).

Energy Guard gives you:

* **Protected sensors** - drop-in copies of your `total_increasing` energy
  sensors (`device_class: energy`, `state_class: total_increasing`, kWh) that
  stay `unavailable` instead of publishing a false `0`.
* **Derived sensors** - sums, differences and phase-split totals.
* **A statistics scanner** that finds offsets which are already in the
  recorder, with evidence, and **only repairs them when you explicitly confirm
  it** (a JSON backup of every changed statistic is written first).
* **Monetary repair** for the cost statistics of the Energy Dashboard,
  including currencies such as GEL.
* **Diagnostics and an optional dashboard** - six diagnostic entities plus a
  ready-made, read-only Lovelace dashboard (`dashboards/energy_guard.yaml`).
* **A configuration page in the sidebar** - every option, every sensor and the
  complete repair workflow in one admin-only page, so the Home Assistant
  settings pages are optional (`/energy-guard-config`).

Nothing is ever changed automatically. `scan_statistics`, `export_repair_report`
and `export_templates` are strictly read-only, `repair_statistics`,
`clear_statistics` and `calibrate_utility_meter` need `confirm: true` and live
behind Home Assistant's admin services. Energy Guard never reads or edits your
YAML, your templates or any entity that it did not create itself.

---

## Table of contents

- [Installation (HACS)](#installation-hacs)
- [Quick start](#quick-start)
- [How protection works](#how-protection-works)
- [Full example: grid import, phases, DP2 and a monthly meter](#full-example-grid-import-phases-dp2-and-a-monthly-meter)
- [Services](#services)
- [Statistics repair in detail](#statistics-repair-in-detail)
- [Cost (monetary) repair](#cost-monetary-repair)
- [Utility meter calibration](#utility-meter-calibration)
- [Options flow](#options-flow)
- [Diagnostics and dashboards](#diagnostics-and-dashboards)
- [Configuration page (optional)](#configuration-page-optional)
- [Dashboard (optional)](#dashboard-optional)
- [Safety model](#safety-model)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)
- [Development](#development)

---

## Installation (HACS)

1. HACS -> three-dot menu -> **Custom repositories**.
2. Add `https://github.com/sandro-defender/energy_guard` as an **Integration** (repository of @sandro-defender).
3. Install **Energy Guard** and restart Home Assistant.
4. **Settings -> Devices & Services -> Add integration -> Energy Guard**.
5. Pick the cumulative energy sensors you want to protect. Energy Guard creates
   one protected sensor per source, for example `sensor.grid_import` ->
   `sensor.grid_import_protected`. The default name keeps the `protected`
   suffix; a name whose entity id already exists is refused (instead of being
   silently renamed to `sensor.grid_import_2`).

No YAML is required. The recorder is required (statistics repair uses the
official recorder APIs); without it the protection still works and the
scan/repair services report that they are unavailable.

## Quick start

```text
Settings -> Devices & Services -> Energy Guard -> Configure
  Protected Sensors   : add every energy sensor you want to protect
  Derived Sensors     : add sums / differences / phase splits
  Utility Meters      : register the utility meters you calibrate
  Detection Rules     : thresholds for zero resets, jumps and grace periods
  Statistics Repair   : lookback window, thresholds, cost repair settings
  Cost Repair         : tariff and cost statistic (energy -> GEL)
  Backups and Reports : how many JSON backups to keep, where reports go
```

Then point the Energy Dashboard at the **protected** sensors:

```text
Settings -> Dashboards -> Energy -> Grid consumption -> sensor.grid_import_protected
```

## How protection works

| Source state | Protected sensor publishes |
| --- | --- |
| `38243.46` (normal) | `38243.46` |
| `unavailable` / `unknown` / `none` / non-numeric | `unavailable` (never `0`) |
| `0` while the last valid value was `>= zero_min_previous` | `unavailable` (never `0`) |
| `0` after a confirmed, persistent reset | `0` (configurable) |
| a value below the last valid value | held back if *do not decrease* is on |
| a value far above the last valid value | reported as a large jump, held back when *reject large jumps* is on |
| the real restored lifetime value | the real value |

Every decision is written to the diagnostic log with the source entity, the
protected entity, timestamps, the previous value, the temporary invalid value,
the restored value and the estimated false energy.

Settings per protected sensor: baseline `offset`, `do_not_decrease`,
`accept_real_reset`, `zero_min_previous`, `confirm_scans`,
`recovery_hold_scans`, `grace_period`, `large_jump`, `large_jump_ratio`,
`reject_large_jumps`, `precision`, `max_value`.

## Full example: grid import, phases, DP2 and a monthly meter

A typical three-part installation: a grid import meter, two phases, a two-phase
device ("DP2") with its own phase meters, a monthly utility meter for billing
and a fixed tariff of **0.235 GEL/kWh**.

### 1. Protected sensors (`Protected Sensors` -> add)

| Name | Source entity | Result |
| --- | --- | --- |
| Grid import protected | `sensor.grid_import` | `sensor.grid_import_protected` |
| Phase A protected | `sensor.phase_a` | `sensor.phase_a_protected` |
| Phase B protected | `sensor.phase_b` | `sensor.phase_b_protected` |
| DP2 Phase A protected | `sensor.dp2_phase_a` | `sensor.dp2_phase_a_protected` |
| DP2 Phase B protected | `sensor.dp2_phase_b` | `sensor.dp2_phase_b_protected` |

Defaults are fine for all five (offset `0`, do not decrease on, confirm scans
`2`, large jump `100000`).

### 2. Derived sensors (`Derived Sensors` -> add)

| Name | Mode | Sources | Result |
| --- | --- | --- | --- |
| Phase total | sum | `sensor.phase_a_protected`, `sensor.phase_b_protected` | `sensor.phase_total` |
| DP2 total | sum | `sensor.dp2_phase_a_protected`, `sensor.dp2_phase_b_protected` | `sensor.dp2_total` |
| Not measured by DP2 | difference | `sensor.grid_import_protected`, `sensor.dp2_total` | `sensor.not_measured_by_dp2` |

A phase-split derived sensor keeps the total and one or more phase sensors in
sync (it derives the missing phase when exactly one phase is missing).

### 3. Monthly utility meter (`Utility Meters` -> add)

| Name | Utility meter entity | Cycle | Protected source | Baseline |
| --- | --- | --- | --- | --- |
| Grid import monthly | `sensor.grid_import_monthly` | monthly | `sensor.grid_import_protected` | `0` |

Energy Guard does not replace the `utility_meter` integration - it registers
the meter so that `calibrate_utility_meter` can compute the right target value
for you.

### 4. Cost repair (`Cost Repair`)

| Setting | Value |
| --- | --- |
| Enabled | yes |
| Energy statistic | `sensor.grid_import` |
| Cost statistic | `sensor.grid_import_cost` |
| Price | `0.235` |
| Currency | `GEL` |

### 5. Energy Dashboard

```text
Grid consumption   : sensor.grid_import_protected
Return to grid     : -
Grid carbon        : your CO2 sensor (optional)
Solar production   : -
Home battery       : -
Individual devices : sensor.phase_a_protected, sensor.phase_b_protected,
                     sensor.dp2_total, sensor.not_measured_by_dp2
Use an entity with current price: off (fixed price 0.235 GEL/kWh)
```

### 6. When the reconnect already happened

```yaml
# 1. Look (read-only). Nothing is modified.
action: energy_guard.scan_statistics
data:
  statistic_ids:
    - sensor.grid_import
  include_cost_suggestions: true
```

Scan without naming a sensor - useful when you install Energy Guard into an
installation whose Energy Dashboard was wrong long before:

```yaml
action: energy_guard.scan_statistics
data:
  scope: all        # linked (default) | energy | all
```

| `scope` | Scans |
| --- | --- |
| `linked` (default) | only the statistics Energy Guard manages |
| `energy` | every energy statistic in the recorder (kWh, Wh, MWh, ...) |
| `all` | every cumulative statistic, whatever its unit (water, gas, ...) |

A wide scan is capped at 500 statistics, reports what it read
(`statistic_count`, `last_scan_scope` on `sensor.energy_guard_statistics_issues`)
and is exactly as read-only as a normal one: nothing is repaired, ever. Set the
default scope once in **Options -> Statistics Repair -> Default scan scope**, or
press one of the three scan buttons on the dashboard's *Repair centre* view.

```json
{
  "candidates": [
    {
      "statistic_id": "sensor.grid_import",
      "start_time": "2026-09-18T04:00:00+00:00",
      "observed_delta": 38243.46,
      "unit": "kWh",
      "offset": -38243.46,
      "estimated_false_energy": 38243.46,
      "severity": "error",
      "evidence": ["zero_then_restore", "source_was_zero_in_previous_period"],
      "fingerprint": "245d3942605e4993"
    }
  ],
  "cost_suggestions": [
    {
      "statistic_id": "sensor.grid_import_cost",
      "energy_statistic_id": "sensor.grid_import",
      "offset": -8987.2131,
      "unit": "GEL",
      "price": 0.235,
      "false_energy_kwh": 38243.46,
      "worked_example": "38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL",
      "separate_confirmation_required": true
    }
  ],
  "read_only": true
}
```

```yaml
# 2. Repair the kWh statistic (preview first, then confirm).
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import
      start_time: "2026-09-18T04:00:00+00:00"
      offset: -38243.46
      unit: kWh
  confirm: true
```

```yaml
# 3. Repair the money with a *separate* confirmation.
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import_cost
      start_time: "2026-09-18T04:00:00+00:00"
      offset: -8987.2131
      unit: GEL
  confirm: true
  confirm_cost: true
```

Both calls write a JSON backup of the affected rows into
`config/energy_guard/backups/` before the recorder API is used, verify the new
values after the recorder queue has drained, and return a `rollback` entry with
the inverse adjustment for every change.

## Services

All services accept an optional `config_entry_id` and return their result as
service response data (Developer Tools -> Actions shows it, automations can use
`response_variable`).

### `energy_guard.scan_statistics` (read-only)

| Field | Type | Description |
| --- | --- | --- |
| `start_time` | datetime | Optional start of the scan window (default: lookback hours) |
| `end_time` | datetime | Optional end of the scan window (default: now) |
| `statistic_ids` | list | Statistic ids to scan (default: every Energy Guard statistic) |
| `entity_ids` | list | Convenience alias for `statistic_ids` |
| `include_cost_suggestions` | bool | Add monetary suggestions (default `true`) |
| `config_entry_id` | string | Choose a specific Energy Guard entry |

```yaml
action: energy_guard.scan_statistics
data:
  start_time: "2026-09-01T00:00:00+04:00"
  statistic_ids: [sensor.grid_import, sensor.grid_import_cost]
response_variable: scan
```

### `energy_guard.repair_statistics` (admin, needs confirmation)

| Field | Type | Description |
| --- | --- | --- |
| `repairs` | list | Required. `statistic_id`, `start_time`, `offset`, optional `unit`, `reason`, `fingerprint` |
| `cost_repairs` | list | Monetary items, same shape |
| `confirm` | bool | `true` applies the repair (default `false` = preview) |
| `confirm_cost` | bool | Extra confirmation for money (default `false`) |
| `dry_run` | bool | Build the full report but change nothing |
| `create_backup` | bool | Write the JSON backup (default `true`) |
| `verify` | bool | Re-read the statistic after the recorder queue drained (default `true`) |

```yaml
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import
      start_time: "2026-09-18T04:00:00+00:00"
      offset: -38243.46
      unit: kWh
      reason: "reconnect 2026-09-18"
  confirm: true
  create_backup: true
  verify: true
```

### `energy_guard.calibrate_utility_meter` (admin, needs confirmation)

Calibrate a `utility_meter` entity with an exact value, or compute it from a
protected cumulative source minus a baseline / cycle start value.

| Field | Type | Description |
| --- | --- | --- |
| `entity_id` | entity | Required. The `utility_meter` sensor |
| `value` | float | Exact value to set |
| `source_entity_id` | entity | Cumulative source (usually the protected sensor) |
| `baseline_value` | float | Source value at the start of the cycle |
| `cycle_start` | datetime | Read the baseline from the recorded history at this time |
| `cycle` | string | Name of the cycle (informational) |
| `confirm` | bool | `true` applies the calibration |
| `dry_run` | bool | Compute everything, change nothing |

```yaml
# The protected grid import meter shows 38243.46 kWh and the month started at
# 38000 kWh, so the monthly meter must read 243.46 kWh.
action: energy_guard.calibrate_utility_meter
data:
  entity_id: sensor.grid_import_monthly
  source_entity_id: sensor.grid_import_protected
  baseline_value: 38000.0
  cycle_start: "2026-09-01T00:00:00+04:00"
  confirm: true
```

### `energy_guard.clear_statistics` (admin, needs `confirm: true`)

Clears statistics of **exactly** the listed statistic ids after writing
backups - the "start from scratch" option for hopeless data.

```yaml
action: energy_guard.clear_statistics
data:
  statistic_ids: [sensor.grid_import]
  confirm: true
```

### `energy_guard.export_repair_report`

Writes a Markdown + JSON report (anomalies, scan candidates, repairs,
calibrations) into `config/energy_guard/reports/`.

```yaml
action: energy_guard.export_repair_report
data:
  name: reconnect_2026_09
  hours: 168
```

### `energy_guard.export_templates`

Returns (and optionally writes) YAML template sensors that mirror your Energy
Guard definitions, for users who prefer plain templates. Energy Guard never
edits that file for you.

```yaml
action: energy_guard.export_templates
data:
  write_file: true
response_variable: templates
```

## Statistics repair in detail

The scanner compares the recorded `sum` deltas of a statistic with the recorded
`state` deltas of the underlying sensor and flags:

* a `sum` jump that is far larger than the real source increase
  (`stat_jump_kwh`, `stat_jump_ratio`, `stat_sum_state_ratio`),
* a `state` that drops to `0` and then returns to the previous high value
  (the classic reconnect signature),
* several sensors failing inside one window (`simultaneous_threshold`,
  `simultaneous_window_minutes`).

Each candidate contains the statistic id, the timestamp, the observed and the
expected delta, the suggested `offset`, the estimated false energy, a severity, a
deduplicated `fingerprint` and the machine-readable `evidence` codes
(`zero_then_restore`, `source_was_zero_in_previous_period`,
`state_flat_while_sum_grew`, `sum_state_mismatch`, `large_jump`,
`last_reset_changed`) - see
[docs/STATISTICS-REPAIR.md](docs/STATISTICS-REPAIR.md) for what each one means.
Repairs are only ever applied when
you pass the exact `statistic_id`, `start_time` and `offset` with
`confirm: true`; the integration calls the official recorder
`async_adjust_statistics` API (no direct database writes), waits for the
recorder queue, re-reads the statistic and reports whether the change is
visible. Statistics that are already correct within `0.001` are reported as
`already_repaired` and skipped.

## Cost (monetary) repair

Energy Guard mirrors a kWh offset onto the matching cost statistic from the
Energy Dashboard (`<energy statistic>_cost`) using the tariff you configure:

```text
38243.46 kWh x 0.235 GEL/kWh = 8987.21 GEL
```

Monetary changes always need their own confirmation (`confirm_cost: true` in
addition to `confirm: true`). If the cost statistic is not configured, the
suggestion is still returned so you can decide manually.

## Utility meter calibration

A monthly meter that was reset or drifted can be calibrated in one call: pass
the exact value, or let Energy Guard compute `source value - baseline` from your
protected cumulative sensor. The baseline either comes from `baseline_value` or
from the recorded history at `cycle_start`. Calibration uses the official
`utility_meter.calibrate` entity service, verifies the new value and is written
to the calibration history.

## Options flow

| Section | What it manages |
| --- | --- |
| Protected Sensors | add / edit / enable / disable / delete protected sensors |
| Derived Sensors | add / edit / enable / disable / delete derived sensors |
| Utility Meters | register meters for calibration |
| Detection Rules | thresholds, grace periods, issue window, scan interval |
| Statistics Repair | lookback window, jump thresholds, default scan scope (`linked`/`energy`/`all`), cost repair settings |
| Cost Repair | tariff, currency, energy and cost statistic ids |
| Backups and Reports | number of backups, report retention |
| Review current issues | the current candidates and the exact service calls |
| Export YAML templates | opt-in copy of the generated templates |

## Diagnostics and dashboards

| Entity | Meaning |
| --- | --- |
| `sensor.energy_guard_last_anomaly` | Last detected event (state = kind) |
| `binary_sensor.energy_guard_data_issue` | `on` while an anomaly or statistics issue is open |
| `sensor.energy_guard_anomalies` | Number of anomalies in the issue window |
| `sensor.energy_guard_estimated_false_energy` | Sum of the blocked false energy (kWh) |
| `sensor.energy_guard_statistics_issues` | Number of suspicious statistics offsets |
| `sensor.energy_guard_last_scan` | Timestamp of the last statistics scan |

Every guard decision (held value, rejected jump, accepted reset, repaired
statistic, calibrated meter) is published as an `energy_guard_anomaly` event and
annotated with the source entity, the source state, the reason
(`zero_reset_blocked`, `decrease_blocked`, `source_invalid`, `jump_held`, ...),
the resulting `guard_status` and a plain-language `recommended_action`. The
diagnostic log and the anomalies are also shown in
**Settings -> Repairs** (Energy Guard never "fixes" them silently: the issue
text tells you which service call to run) and in the diagnostics download
(`Settings -> Devices & Services -> Energy Guard -> Download diagnostics`),
which contains no tokens, secrets or database paths.

## Configuration page (optional)

Energy Guard adds a sidebar entry **Energy Guard** (`/energy-guard-config`,
administrators only) with every setting of the integration:

| Tab | Content |
| --- | --- |
| Overview | status, three read-only scan buttons, counters |
| Protected / Derived / Meters | add, edit, enable/disable, delete - every field of a definition |
| Detection | scan interval, lookback, issue window, simultaneous-failure rules, log retention |
| Statistics | default scan scope (`linked` / `energy` / `all`) and the scanner thresholds |
| Cost | tariff, currency, statistic ids, optional live price entity |
| Backups | retention, existing backup/report files, "export a report now" |
| Repair | the candidates of the last scan and the preview → confirm → apply workflow (money separately confirmed) |
| Templates | the read-only YAML copy of your configuration |

It is admin-only, uses the same validation and storage layer as the options flow,
sends `confirm: true` only after an explicit confirmation click, and is optional:
the options flow (*Settings → Devices & Services → Energy Guard → Configure*) and
every service keep working without it.  See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Dashboard (optional)

A read-only Lovelace dashboard ships with the integration:
[`dashboards/energy_guard.yaml`](dashboards/energy_guard.yaml).

| View | Shows |
| --- | --- |
| Status | Outcome headline, the six diagnostic entities, the currently active issue, the detection settings in use |
| Sources vs protected | Raw source vs protected sensor, derived sensors, the utility meter, 48 h history and 12 month statistics (README example entities - replace them with your own) |
| Repair centre | Safety model, three read-only scan buttons (my sensors / all energy statistics / all statistics), the scan candidates and cost suggestions, and the exact manual service calls |

It uses core cards only, calls nothing but the read-only
`energy_guard.scan_statistics` and has no repair/calibrate/clear button - every
data-changing step stays a manual, confirmation-gated service call. HACS cannot
install a dashboard, so the file is a one-time copy and paste (or a YAML-mode
dashboard): see [docs/DASHBOARD.md](docs/DASHBOARD.md). The integration works
exactly the same without it.

## Documentation

| Document | Content |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, import layering, how a source becomes a protected sensor, detection and repair flow, migration model, test map |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | The sidebar configuration page: what each tab does, the WebSocket commands behind it, its safety rules, troubleshooting |
| [docs/DASHBOARD.md](docs/DASHBOARD.md) | The optional dashboard: what it shows, the two install methods, how to adapt the example entities, why it has no repair buttons, troubleshooting |
| [docs/SAFETY.md](docs/SAFETY.md) | What is read-only and what changes data, confirmation semantics, backup files, verification and rollback, the never-do list, privacy rules |
| [docs/SERVICES.md](docs/SERVICES.md) | Every service with all parameters, real response shapes, dry-run/confirmation behaviour and possible errors |
| [docs/STATISTICS-REPAIR.md](docs/STATISTICS-REPAIR.md) | The corruption patterns, detection evidence, the step-by-step repair playbook, repair-vs-clear, troubleshooting |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, test suite and its recorder rules, linting, where to add a rule/service/option, releasing, definition of done |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to report a bug, the five rules a change may not weaken, PR checklist and style |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

## Safety model

* Read-only by default: scanning never modifies anything.
* Every modifying action needs an explicit service call and a confirmation
  flag; money needs a second one.
* A JSON backup of every changed statistic is written before the change.
* Only recorder/WebSocket APIs are used, never direct database access.
* Only entities and configuration created by Energy Guard are managed; your
  YAML and template files are never touched.
* Backups and reports live in `config/energy_guard/{backups,reports}`.
* Errors return clear messages with rollback instructions (the inverse offset
  and the backup file name).

## Troubleshooting

**The scan finds nothing but the Energy Dashboard is wrong.**
Widen the lookback (`Statistics Repair` -> lookback hours) and make sure the
statistic has been compiled (long term statistics appear within ~5 minutes after
the meter reported a value). The `warnings` field of the scan response explains
what was checked.

**"Recorder is not available".**
The recorder integration is required for scanning and repairing. Protection of
live sensor values works without it.

**"No recorded value ... at <time>. Provide baseline_value instead."**
The history for that cycle start is older than the recorder keeps. Pass
`baseline_value` explicitly (the meter reading at the start of the cycle).

**A repair reports `verification_warning`.**
The recorder may need another minute to apply the adjustment. Re-run
`scan_statistics` for that statistic; if the values are already right, the
candidate disappears. To undo a repair, apply the `rollback` offset (the inverse
value returned by the repair) or restore the affected rows from the JSON backup.

## Development

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest tests/ -q --log-cli-level=CRITICAL   # 183 tests
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format --check custom_components tests
```

`--log-cli-level=CRITICAL` keeps SQLAlchemy's per-statement INFO logging out of
the test output. The suite covers the whole configuration surface (every options-flow section
adds, edits, toggles and deletes definitions, and stores what the user picked
including the type normalisation of stored values), the reconnect sequence and
its edge cases,
false cumulative resets, multiple simultaneous failures, utility meter
calibration (exact and computed), kWh and GEL statistics repair, backup
handling, config-entry migration and backwards compatibility, the config/options
flows, diagnostics and the refusal to modify anything without confirmation.
Repository contracts (manifest, `hacs.json`, service definitions, translations,
documentation, no direct database access, no secrets) are enforced by
`tests/test_contracts.py`, `tests/test_dashboard.py` keeps the shipped dashboard
valid YAML, read-only, free of dead entity references and renders all of its
templates, and `tests/test_panel.py` plus `tests/test_websocket_api.py` keep the
configuration page admin-only, confirmation-gated and free of credential or
path handling, while `tests/test_dashboard_data.py` keeps the Overview
aggregation read-only and bounded to the kept diagnostic log.

Contributors: read [CONTRIBUTING.md](CONTRIBUTING.md) (the five rules a change
may not weaken) and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (setup, test map,
where to add a rule/service/option, release steps) first.

## License

MIT - see [LICENSE](LICENSE).
