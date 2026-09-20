# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-09-20

### Added

- **Config-entry migration layer** (`migrations.py`, `async_migrate_entry`):
  saved configurations of earlier versions are normalised on load, so option
  keys, entity ids and statistic ids keep working. Covered by
  `tests/test_migrations.py`, including the old payload shape as a literal.
- **Event annotations**: every anomaly and every repair/calibration/clear event
  now carries `reason`, `source_state`, `guard_status` and a plain-language
  `recommended_action` (see `const.RECOMMENDED_ACTIONS`), so the log, the
  `sensor.energy_guard_last_anomaly` history and the Repairs issues all explain
  what happened and what to do next.
- **Repository contract tests** (`tests/test_contracts.py`): manifest vs
  constants, `hacs.json`, service definitions vs `services.yaml` vs
  `strings.json` vs `translations/en.json`, confirmation flags documented only on
  data-changing services, options-flow sections, translated issues, README and
  docs presence, no direct database access anywhere and no credential-like
  strings in the component.
- **Documentation set**: `docs/ARCHITECTURE.md`, `docs/SAFETY.md`,
  `docs/SERVICES.md`, `docs/STATISTICS-REPAIR.md`, `docs/DEVELOPMENT.md`,
  `CONTRIBUTING.md`, and a documentation index in the README.

### Changed

- The config and options flows refuse a protected or derived sensor name whose
  entity id is already taken (the source sensor itself, another protected
  sensor or another derived sensor). Home
  Assistant would otherwise silently create ``sensor.grid_import_2``, which is
  easy to pick by accident in the Energy Dashboard. The error message says what
  to do; ``Grid import protected`` is the suggested default.
- The repair pipeline was split into focused modules (`recorder_io.py`,
  `detection.py`, `costs.py`, `backups.py`, `utility_meter.py`), leaving
  `statistics.py` with the repair/clear orchestration only. No public behaviour
  change; import layering is documented in `docs/ARCHITECTURE.md`.
- Service and scan responses carry `recommended_action`; backups are written
  with a collision-free name when two of them land in the same second.
- Multi-entry responses are consistent: results of several config entries are
  always returned under `config_entries` (previously `export_repair_report` used
  `reports`).

### Fixed

- **The options flow forms could not be used**: five of the seven sections raised
  a selector validation error as soon as the form was opened, so a protected
  sensor, a derived sensor, a utility meter, the statistics thresholds and the
  tariff could not be configured from the UI at all.
  * an unset optional number (`max=None`) is now omitted from the number
    selector config instead of being passed as an empty maximum;
  * an *optional* entity field may now be left empty (an omitted "total" or
    "utility meter source"); a wrong entity id is still rejected.
- **A sensor configured through the UI never came up.** The number selectors
  store floats, even for integer fields (`precision: 3.0`), and
  `round(value, 3.0)` raised `TypeError`, which aborted the entity setup and
  silently left the protected sensor without a state. Stored configuration is
  now normalised to the declared field types when it is loaded (and non-finite
  numbers are refused instead of being passed on).
- `keep_backups` arriving as a float from the UI broke backup pruning (the
  value is used for slicing). It is normalised to an integer too.
- A statistic whose **backup file cannot be written is no longer modified**:
  `repair_statistics` skips that item with `reason: backup_failed` and
  `clear_statistics` aborts with `status: failed`, leaving the data untouched.
- Non-finite numbers (`nan`, `inf`) are rejected by `protect.parse_float` instead
  of being stored as a valid `0.0`, which is exactly the corruption this
  integration exists to prevent.
- Backup file names no longer overwrite each other when two backups are created
  within the same second.

## [1.0.0] - 2026-09-20

### Added

- **Protected sensors** (`device_class: energy`, `state_class: total_increasing`,
  kWh) that never publish a reconnect artefact: the source state
  `unavailable` / `unknown` / `none` / non-numeric, a `0` while the last valid
  value was substantially higher, a decrease, or an implausible jump are held
  back, and the sensor reports `unavailable` instead of a false `0`.
- Configurable per-sensor behaviour: baseline offset, do-not-decrease guard,
  real-reset confirmation (`confirm_scans`), recover hold scans, grace period,
  large-jump thresholds, precision and maximum value.
- **Derived sensors**: sum, difference and phase-split meters built from
  protected or raw sources.
- **Statistics scanner** (`energy_guard.scan_statistics`, read-only) that finds
  `sum` jumps which are far larger than the real source increase, false zero
  resets and simultaneous failures across several sensors, with evidence and a
  deduplicated fingerprint per candidate.
- **Statistics repair** (`energy_guard.repair_statistics`, admin, `confirm:
  true`) that previews entities, timestamps, units, offsets and before/after
  totals, writes a JSON backup of every affected statistic, applies the offset
  through the official recorder API, waits for the recorder queue and verifies
  the result. Rolling back uses the returned inverse offset.
- **Monetary repair** for Energy Dashboard cost statistics with a fixed tariff,
  requiring a separate `confirm_cost: true` confirmation
  (e.g. 38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL).
- **Utility meter calibration** (`energy_guard.calibrate_utility_meter`) with an
  exact value or a value computed from a protected cumulative source and a
  cycle-start baseline (supplied directly or read from the recorded history).
- **`energy_guard.clear_statistics`** for explicitly listed statistic ids only,
  always with `confirm: true` and a backup.
- **Reports and exports**: `energy_guard.export_repair_report` (Markdown +
  JSON), `energy_guard.export_templates` (opt-in YAML copy of the generated
  templates) and a diagnostics download without secrets or database paths.
- **Options flow** with sections for Protected Sensors, Derived Sensors,
  Utility Meters, Detection Rules, Statistics Repair, Cost Repair, Backups and
  Reports, a review step and the YAML export.
- **Diagnostics for the dashboard**: `sensor.energy_guard_last_anomaly`,
  `binary_sensor.energy_guard_data_issue`, anomaly count, estimated false
  energy, statistics issues and last scan, plus Repairs issues that point at
  the service call needed to fix what was found.
- 55 tests covering the reconnect sequence, false cumulative reset, multiple
  simultaneous failures, utility meter calibration (exact and computed), kWh and
  GEL statistics repair, confirmation requirements, flows and diagnostics.

### Notes

- Uses only official Home Assistant recorder / WebSocket APIs; no direct
  database access.
- Read-only by default: no scan, repair, calibration or clear action happens
  without an explicit, confirmed service call.
- Never reads or edits user YAML or template files; only integration-owned
  entities and configuration are managed.

[1.1.0]: https://github.com/your-github-username/energy-guard/releases/tag/v1.1.0
[1.0.0]: https://github.com/your-github-username/energy-guard/releases/tag/v1.0.0
