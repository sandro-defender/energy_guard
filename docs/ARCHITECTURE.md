# Energy Guard architecture

This document explains how Energy Guard is built, where every responsibility
lives, and why the safety rules are implemented the way they are.  It is written
for a developer (or an AI agent) who has to change the integration without
breaking data that users care about.

Companion documents:

* [`SAFETY.md`](SAFETY.md) - every data-changing operation, backups, rollback.
* [`SERVICES.md`](SERVICES.md) - the service API, parameter by parameter.
* [`STATISTICS-REPAIR.md`](STATISTICS-REPAIR.md) - what actually gets corrupted
  in Home Assistant and how each layer is repaired.
* [`DEVELOPMENT.md`](DEVELOPMENT.md) - local setup, tests, how to extend.

---

## 1. Design principles

1. **Protection first, repair second.** The integration's primary job is to stop
   bad readings from reaching the recorder. Repair of already-corrupted data is
   a separate, explicit action.
2. **Never destructive by default.** Everything that changes data needs
   `confirm: true`, and money needs a second confirmation (`confirm_cost: true`).
3. **Only own entities.** Energy Guard creates and manages its own entities. It
   never edits user YAML, never rewrites another integration's entity and never
   touches the recorder database directly - it uses the recorder's public APIs.
4. **Prove it.** A repair is not "done" until the recorder has been read back and
   the value matches. Until then the response says `verified: false`/`null`.
5. **Explain it.** Every anomaly is logged with entity, timestamps, values,
   estimated false energy and a recommended action, so a user can decide.

## 2. Module map

```
custom_components/energy_guard/
  __init__.py        async_setup / async_setup_entry / async_unload_entry,
                     service registration, async_migrate_entry re-export
  manifest.json      HACS/HA metadata (no requirements, no loggers key)
  const.py           every constant: option keys, event kinds, reason codes,
                     thresholds, the recommended-action texts, service names
  config_flow.py     first run: pick sources -> name them -> detection defaults
  options_flow.py    menu driven editor for the seven option sections plus the
                     review step and the YAML export
  selectors.py       shared voluptuous/selector schemas for both flows (and the
                     tolerant optional-entity selector those forms need)
  models.py          dataclasses (definitions, detection rules, cost config,
                     events, repair request/outcome) - no Home Assistant imports.
                     Also the *type boundary*: ``from_dict`` normalises whatever
                     the UI stored (number selectors return floats, even for
                     integer fields) to the declared types
  protect.py         the protection engine (SensorGuard): pure logic, no HA
  units.py           unit helpers: is a unit an energy unit, and which factor
                     converts it to kWh (no state, no side effects)
  recorder_io.py     the only module that talks to the recorder (read + wait +
                     before/after comparison)
  backups.py         JSON backups of statistics (write, unique naming, pruning)
  detection.py       read-only scanner: detect_offsets(), async_scan(), ScanResult
  statistics.py      writes: async_repair_statistics(), async_clear_statistics()
  utility_meter.py   async_calibrate_utility_meter() via utility_meter.calibrate
  costs.py           money math: kWh offset -> cost offset, tariff/worked example
  export.py          generated output: YAML templates, JSON/Markdown reports
  entity.py          protected + derived entity classes, source subscriptions
  sensor.py          sensor platform (protected/derived/diagnostic entities)
  binary_sensor.py   binary_sensor.energy_guard_data_issue
  hub.py             per-config-entry runtime state: config, event log, repairs,
                     entity registry bookkeeping, simultaneous failure detection
  coordinator.py     periodic read-only statistics scan -> hub + Repairs issues
  repairs.py         Repairs (issues) integration: create/remove issue entries
  diagnostics.py     downloadable diagnostics (redacted, no paths/secrets)
  migrations.py      normalize/migrate stored config entry options
  services.yaml      user facing service documentation (UI "Developer tools")
  strings.json       UI strings, == translations/en.json
```

### Layering (import direction)

```
const, models, protect, units           <- no Home Assistant imports
        ^
   recorder_io, backups, costs
        ^
   detection, statistics, utility_meter
        ^
   hub, entity, sensor, binary_sensor, coordinator, repairs
        ^
   services, config_flow, options_flow, diagnostics, export
```

Imports only point *upwards*.  That keeps the risky code (recorder writes) at the
bottom of the stack, testable in isolation, and prevents import cycles.

## 3. Data flow: source sensor -> protected entity

```
 source sensor (sensor.grid_import, total_increasing, kWh)
        │  state change event
        ▼
 EnergyGuardProtectedSensor._handle_source_event   (entity.py)
        │  raw state string, e.g. "38243.46" / "unavailable" / "0.0" / "nan"
        ▼
 protect.parse_float            -> float | None     (rejects "", unknown,
        │                                             unavailable, nan, inf,
        │                                             booleans, "1,5")
        ▼
 protect.SensorGuard.evaluate_value(value, now)     (protect.py, pure)
        │  GuardOutcome(publish, value, reason, event, last_good, held_since)
        ▼
 entity._async_evaluate: publish value  OR  self._attr_available = False
        │                                    (state "unavailable")
        ├─ hub.log_event(event)  -> diagnostic log + dashboard entities
        └─ async_write_ha_state()
                                  ▼
              recorder long term statistics of sensor.grid_import_protected
                                   (only valid numbers are ever recorded)
```

Key rules inside `SensorGuard` (see `protect.py`):

| Situation | Result |
| --- | --- |
| first ever reading | accepted (the only sane baseline) |
| `None` (unavailable/unknown/non-numeric/NaN) | `publish=False`, reason `source_invalid` |
| value `0` while `last_good >= zero_min_previous` | held, reason `zero_held`, event `zero_reset_blocked` |
| value `< last_good` and `do_not_decrease` | held, reason `decrease_held` |
| value `> last_good` by more than `large_jump`/`large_jump_ratio` | event `large_jump`; held when `reject_large_jumps` |
| value `> max_value` | held, reason `max_held` |
| zero that persists for `confirm_scans` readings and `accept_real_reset` | accepted as a real reset |
| recovery after a gap `>= grace_period` | `recovery_hold_scans` readings must agree first |

Derived sensors (`EnergyGuardDerivedSensor`) read their sources, apply
`mode` (`sum`, `difference`, `phase_split`) and `scale`, and then run **the same
guard** - so a derived total can never publish a partial sum that looks like a
drop: if a source is missing and `require_all_sources` is set, the derived value
becomes `None` (→ `unavailable`).

## 4. Detection flow (read-only)

```
coordinator (every scan_interval)            services.scan_statistics (on demand)
        └──────────────┬────────────────────────────────┘
                       ▼
        detection.async_scan(hass, start, end, statistic_ids, detection, cost...)
                       │
   recorder_io.async_statistic_metadata   (which units? has_sum?)
   recorder_io.async_statistics_rows      (hourly rows: state, sum, change)
                       ▼
        detection.detect_offsets(rows, statistic_id, unit, detection)
                       │  compares sum deltas with state deltas:
                       │   * jump: sum grew by more than statistics_jump_threshold
                       │     and more than statistics_jump_ratio * median increase
                       │   * zero-reset: state 0 -> back to the old high value
                       │   * sum/state ratio: sum grew much faster than state
                       ▼
        ScanCandidate(statistic_id, start_time, unit, offset, observed_delta,
                      expected_delta, estimated_false_energy, evidence,
                      severity, fingerprint)
                       ▼
   hub.scan_candidates ──► sensor.energy_guard_statistics_issues (dashboard)
                       ├──► repairs.async_sync_repair_issues()  (Repairs page)
                       └──► event log entry with a recommended action
                       ▼
        costs.build_cost_suggestions(...)  -> cost previews, flagged
                                              separate_confirmation_required
```

Simultaneous failures are detected on the *live* side: `hub.log_event()` calls
`hub._detect_simultaneous()`, which looks at `FAILURE_KINDS` events inside
`simultaneous_window_minutes` and logs one `simultaneous_anomaly` event when at
least `simultaneous_threshold` different sources failed together.

The scanner never writes: no backup, no repair, no state change.  That is
asserted by `tests/test_detection.py::test_scan_service_does_not_modify_anything`.

## 5. Repair flow (explicit, confirmed)

```
energy_guard.repair_statistics(repairs=[...], confirm=true, confirm_cost=?, ...)
        │  (admin-only service; see services.yaml)
        ▼
services.async_handle_repair
        ├─ cost_requests without confirm_cost -> ServiceValidationError (nothing changes)
        ▼
statistics.async_repair_statistics(requests, confirm, dry_run, create_backup, verify)
        │
        ├─ 1. validate   statistic exists? has_sum? unit compatible?
        │                fingerprint matches (when supplied)?
        ├─ 2. preview    read rows, remember before-value and anchors
        │                (previews return before/after and the rollback offset)
        ├─ 3. backup     backups.async_backup_statistics  -> JSON in
        │                <config>/energy_guard/backups/statistics_backup_*.json
        ├─ 4. write      statistics._async_call_recorder_write ->
        │                recorder.async_adjust_statistics (official API)
        │                fallback: the recorder WebSocket handler
        │                (recorder/adjust_sum_statistics)
        ├─ 5. wait       recorder_io.async_wait_for_recorder (bounded)
        ├─ 6. verify     recorder_io.verify_adjustment_applied: re-read the
        │                statistic (up to VERIFY_ATTEMPTS times, 0.2 s apart) and
        │                compare the anchor values with before + adjustment
        └─ 7. report     RepairReport: status, applied/preview/skipped, backups,
                         rollback (inverse offset + backup file), message
        ▼
services: hub.record_repair(), hub.log_event(... recommended_action ...)
        ▼
Repairs issues refreshed (candidates that are gone disappear from the page)
```

`async_clear_statistics` follows the same five-step safety order (preview →
backup → `recorder.async_clear_statistics` → wait → verify that the statistic is
really gone) and requires `confirm: true` plus an explicit `statistic_ids` list.

`utility_meter.async_calibrate_utility_meter` computes the target (exact value,
or `source - baseline`, reading the baseline from history when needed), refuses
entities that are not `utility_meter`, requires confirmation, calls the official
`utility_meter.calibrate` entity service and reads the value back.

## 6. Backup and verification flow

* Backups are written **before** the recorder is asked to change anything, in
  `<config>/energy_guard/backups/`.  Every file contains the metadata block
  (`version`, `created_at`, `reason`, `statistic_id`, `unit`, `start_time`,
  `end_time`, `rows`, `rows_truncated`, `checksum`, `restore_hint`) and the rows
  (`start`, `sum`, `state`, `last_reset`) as they were at that moment.
* File names are `statistics_backup_<UTC timestamp>_<statistic id>.json`;
  `backups.write_unique_json()` adds a numeric suffix instead of overwriting an
  existing file, so two repairs inside the same second cannot destroy evidence.
* `backups.prune_backups()` keeps the newest `Backups and Reports -> keep
  backups` files (`25` by default).
* Verification never reports success from the write call alone: it re-reads the
  statistic and compares the anchor rows.  `verified: null` means "could not be
  compared", which is reported as an explicit warning, never as success.
* Reports (`energy_guard.export_repair_report`) are written to
  `<config>/energy_guard/reports/` as JSON **and** Markdown, generated from the
  hub's event log, the scan candidates, the repair history and the calibration
  history.

## 7. Configuration and migration

* All settings live in the **config entry options** (`EnergyGuardConfig` in
  `models.py`): `protected_sensors`, `derived_sensors`, `utility_meters`,
  `detection_rules`, `cost_repair`, `backups_reports`.
* `migrations.normalize_options()` runs on every load: unknown keys are kept,
  missing keys get their documented default, unusable definitions are dropped or
  disabled *with a log line*.  It is idempotent and unit tested.
* `migrations.async_migrate_entry()` is the Home Assistant entry point for an
  entry whose stored version is older than the flow's
  `EnergyGuardConfigFlow.VERSION`.  Bump `CURRENT_VERSION` only when a payload
  must be **persisted** in a new shape; the normalization above already keeps
  older payloads working.
* Runtime state (event log, repair/calibration history, reported fingerprints) is
  stored with `Store` in `<config>/.storage/energy_guard.<entry_id>` and never
  contains user configuration or statistics values.

## 8. Why arbitrary YAML is never edited

Energy Guard is installed through HACS and configured through the UI.  Editing a
user's `configuration.yaml`, `templates.yaml` or `packages/` would:

* fight with the user's own edits and with Home Assistant's config checker,
* make a rollback impossible without file-level backups of files Energy Guard
  does not own,
* break the "only own entities" rule: Energy Guard would have to guess which
  template belongs to it.

Therefore the integration manages **only** the entities and configuration it
created itself.  `energy_guard.export_templates` and the options flow step
`Export YAML templates` *generate* a YAML snippet - written into
`<config>/energy_guard/energy_guard_templates.yaml` or returned as service
response data - and it is the user who decides whether to copy it anywhere.  The
generated file is never read back by the integration.

## 9. Test map

| Test file | Protects |
| --- | --- |
| `test_protect.py` | the pure engine: parsing, every guard branch, reason codes |
| `test_reconnect_sequence.py` | live entity behaviour: unavailable→0→restored, non-numeric states, decreases, resets, simultaneous failures, derived sensors, offsets |
| `test_detection.py` | the scanner: signatures, thresholds, read-only guarantee, warnings |
| `test_statistics_repair.py` | scan → repair → verify → clear, kWh + GEL, confirmation gates |
| `test_backups.py` | backup format/location/pruning, backup-before-change, rollback, dry run |
| `test_calibrate.py` | utility meter calibration (exact value and computed target) |
| `test_migrations.py` | backwards compatibility of stored configuration |
| `test_flows_and_diagnostics.py` | config/options flow, diagnostics, Repairs issues, unload |
| `test_dashboard.py` | the shipped dashboard: valid YAML, read-only card actions, real entity ids, rendering templates |
| `test_websocket_api.py` | the configuration API: admin-only commands, validation, storage, subscriptions |
| `test_panel.py` | panel registration (optional, admin-only, removed on unload) and the served JavaScript asset |
| `test_contracts.py` | packaging, service/docs/translation contracts, no DB access, no secrets |

Run everything with `pytest tests/ -q` (see [`DEVELOPMENT.md`](DEVELOPMENT.md)).
