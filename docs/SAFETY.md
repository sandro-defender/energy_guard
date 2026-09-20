# Energy Guard safety model

This is the contract Energy Guard makes to the people who install it.  Every
rule here is implemented in code and covered by a test; changing one of them
requires changing this document.

**Short version: nothing changes unless you call a service with `confirm: true`,
money additionally needs `confirm_cost: true`, and every statistic that is about
to change is backed up first.**

---

## 1. Read-only by default

| Operation | Reads | Writes |
| --- | --- | --- |
| Protected sensors publishing states | source states | only their own entity state |
| `sensor.energy_guard_*`, `binary_sensor.energy_guard_data_issue` | hub state | - |
| Background coordinator scan (every `scan_interval`) | long term statistics | - |
| `energy_guard.scan_statistics` | long term statistics + metadata | - |
| `energy_guard.export_repair_report` | hub state | a report file in `energy_guard/reports/` |
| `energy_guard.export_templates` | configuration | a YAML file **only** with `write_file: true` |

The scan is guaranteed read-only: it only calls
`recorder.statistics_during_period` and `async_list_statistic_ids`, and
`tests/test_detection.py` asserts that a scan leaves the rows, the backups
directory and the repair history untouched.

## 2. Every data-changing operation

| Operation | Service / flow | Confirmation | Backup | Verification | Rollback |
| --- | --- | --- | --- | --- | --- |
| Repair a statistic offset | `energy_guard.repair_statistics` | `confirm: true` | yes, per statistic, before the change | re-read + compare | `rollback` offset in the response, backup file name |
| Repair a **cost** statistic | `energy_guard.repair_statistics` (`cost_repairs`) | `confirm: true` **and** `confirm_cost: true` | yes | re-read + compare | as above |
| Clear statistics | `energy_guard.clear_statistics` | `confirm: true` + explicit `statistic_ids` | yes, all rows of each statistic | statistic must be gone | backup file contains all old rows |
| Calibrate a utility meter | `energy_guard.calibrate_utility_meter` | `confirm: true`, target entity must be `utility_meter` | no (the meter has no long term statistics) | meter read back | `current_value` in the response lets you calibrate back |
| Delete/disable a protected sensor | options flow (`Protected Sensors -> delete/toggle`) **or the configuration panel** | yes, a confirmation step per item (a dialog in the panel, `confirm: true` on the command) | n/a | entity registry cleaned by Home Assistant | re-add the definition; the historic statistics of the protected entity stay |
| Write the YAML export | options flow (`Export YAML templates`) or `export_templates` with `write_file: true` | explicit choice of the write step | n/a | path returned | delete the generated file |

Everything else in Energy Guard is read-only.

**The optional configuration panel is the same safety model, not a shortcut.**
It is an administrator-only sidebar page; every write goes through the same
validation and storage layer as the options flow; it sends `confirm: true` only
after an explicit confirmation dialog (and `confirm_cost: true` only after a
second, separate one); the *Apply* button of a repair candidate stays disabled
until that candidate was previewed with a dry run.  If the panel cannot be
registered, nothing else changes - see [CONFIGURATION.md](CONFIGURATION.md).

Calling a modifying service **without** confirmation never raises away the
information: it returns a preview (what would change, from which value to which
value, in which unit, with which backup file) and `status: "confirmation_required"`
or `status: "preview"`.

## 3. Confirmation semantics

| Field | Service | Meaning |
| --- | --- | --- |
| `confirm: true` | `repair_statistics`, `clear_statistics`, `calibrate_utility_meter` | "apply this change" |
| `confirm_cost: true` | `repair_statistics` | "and also change money" (required whenever `cost_repairs` is non-empty) |
| `dry_run: true` | `repair_statistics`, `calibrate_utility_meter` | "compute everything, change nothing" (wins over `confirm`) |

Rules:

* The kWh and the money decision are separate calls/fields on purpose: a correct
  energy repair does **not** imply the user wants the cost statistic rewritten.
* A repair item is applied only if it names the exact `statistic_id`,
  `start_time` and `offset`.  Energy Guard never repairs "whatever looks wrong"
  on its own.
* An optional `fingerprint` (returned by `scan_statistics`) ties the repair to the
  exact candidate the user reviewed; a mismatch skips the item.
* A repair is skipped when the value is already correct within
  `ALREADY_REPAIRED_TOLERANCE` (0.001) - reported as `already_correct` /
  `already_repaired`, not applied twice.

## 4. Backup format and location

```
<config>/energy_guard/backups/statistics_backup_20260918T040000Z_sensor_grid_import.json
<config>/energy_guard/reports/energy_guard_report_20260918T040500Z.json
<config>/energy_guard/reports/energy_guard_report_20260918T040500Z.md
<config>/energy_guard/energy_guard_templates.yaml        (only if requested)
```

Backup file content:

```json
{
  "energy_guard": {
    "version": "1.1.0",
    "created_at": "2026-09-18T04:00:00+00:00",
    "reason": "offset -38243.46 kWh at 2026-09-18T04:00:00+00:00 (reconnect)",
    "statistic_id": "sensor.grid_import",
    "unit": "kWh",
    "start_time": "2026-09-18T04:00:00+00:00",
    "end_time": "2026-09-18T04:10:00+00:00",
    "rows": 30,
    "rows_truncated": false,
    "checksum": "sha256:…",
    "restore_hint": "Apply the inverse adjustment with energy_guard.repair_statistics to roll this statistic back."
  },
  "rows": [
    { "start": "2026-09-17T22:00:00+00:00", "sum": 38243.46, "state": 38243.46, "last_reset": null }
  ]
}
```

* Backups are plain JSON: readable, copyable and restorable without Energy Guard.
* `write_unique_json()` appends `_1`, `_2`, … instead of overwriting a file that
  already exists.
* Only the newest `keep backups` (default `25`) files are kept per directory;
  set `keep_backups: 0` in `Backups and Reports` to disable pruning.
* At most `MAX_BACKUP_ROWS` (100 000) rows are written per file; a truncated
  backup sets `rows_truncated: true` (so the file is honest about what it holds).
* Backups are written **before** the recorder call and are never modified
  afterwards, so they always describe the pre-change state.

## 5. Verification (never claim success too early)

1. The change is requested through the recorder's official API
   (`recorder.async_adjust_statistics`, `recorder.async_clear_statistics`, or the
   recorder WebSocket handlers as a compatibility fallback).
2. `recorder_io.async_wait_for_recorder()` waits for the recorder queue, bounded
   by a timeout so a stuck recorder cannot block an automation forever.
3. `recorder_io.verify_adjustment_applied()` re-reads the statistic up to
   `VERIFY_ATTEMPTS` (5) times, `VERIFY_DELAY` (0.2 s) apart, and compares the
   anchor rows with `before + adjustment` (tolerance
   `max(0.001, |adjustment| * 1e-6)`).
4. The result reports one of `verified: true`, `verified: false`,
   `verified: null` ("could not be compared").  `false`/`null` add a
   `verification_warning` to the response with instructions - they are **not**
   reported as success.

Utility meter calibration is verified the same way by reading the meter state
back after `utility_meter.calibrate`.

## 6. Rollback process

Every repair response contains a `rollback` list:

```json
{
  "statistic_id": "sensor.grid_import",
  "start_time": "2026-09-18T04:00:00+00:00",
  "offset": 38243.46,
  "unit": "kWh",
  "backup_file": "/config/energy_guard/backups/statistics_backup_…json",
  "message": "To undo this repair, call energy_guard.repair_statistics with this inverse offset (and confirm: true). The backup file contains the original rows."
}
```

To undo:

```yaml
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import
      start_time: "2026-09-18T04:00:00+00:00"
      offset: 38243.46          # the inverse offset from the rollback entry
      unit: kWh
  confirm: true
```

To restore the exact rows instead, read the `rows` of the backup file (each row
has `start`, `sum`, `state`, `last_reset`) and re-import them with the Developer
Tools import interface or by calling `energy_guard.repair_statistics` per
affected row.  A cleared statistic can only be restored from a backup file -
there is no other copy, which is why `clear_statistics` always backs up first
unless `create_backup: false` is passed explicitly.

`tests/test_backups.py::test_repair_can_be_rolled_back_with_the_returned_offset`
proves that the documented rollback restores the original values.

## 7. What Energy Guard will never do

* Never repair, calibrate, clear or delete anything automatically - not on
  startup, not on a state change, not on a schedule.
* Never write to the recorder database directly, never use `sqlite3`, never
  look for `home-assistant_v2.db` (enforced by
  `tests/test_contracts.py::test_no_direct_database_access_anywhere`).
* Never edit `configuration.yaml`, `templates.yaml`, `packages/` or any other
  user file.  The YAML export is a one-way output.
* Never modify an entity it does not own: no `homeassistant.update_entity`, no
  foreign entity registry edits, no `python_script`, no shell commands.
* Never disable a user's Energy Dashboard configuration.  It only creates
  sensors the user may point the dashboard at.
* Never store or log credentials: Energy Guard has no authentication, and
  `tests/test_contracts.py::test_no_secrets_or_tokens_in_the_component` fails if
  a credential-like identifier ever appears in the code.
* Never send data anywhere.  There is no network client in the integration.
* Never weaken the confirmation rules for scheduled/automation use: an
  automation must pass `confirm: true` (and `confirm_cost: true`) exactly like a
  human clicking through the UI.

## 8. Repairs, diagnostics and privacy

* **Diagnostics** (`Settings -> Devices & Services -> Energy Guard -> Download
  diagnostics`) contain the configuration, the runtime state, the scan
  candidates, the diagnostic log and the repair/calibration history.  They do
  **not** contain tokens, credentials, or absolute filesystem paths: the backup
  and report folders are reported as `energy_guard/backups` and
  `energy_guard/reports`.  There is nothing else to redact, because Energy Guard
  stores no secrets.  Statistics *values* are included - they are the user's own
  energy data and the reason the diagnostics exist; review before publishing.
* **Repairs issues** point at the data that needs attention and name the exact
  service call.  They are `is_fixable=False` on purpose: a "Fix" button that
  silently changes statistics would break rule 2.
* **Logs** contain entity ids, timestamps, values and reason codes at `info`
  level for anomalies.  `_LOGGER.debug` is used for anything verbose.  No log
  line contains a statistic's raw row dump, a filesystem path of the recorder or
  any credential.

## 9. Handling secrets and tokens (for users)

Energy Guard needs no tokens.  If you are asked for one while filing an issue:

* never paste an access token, a password, a `secrets.yaml` snippet or a recorder
  database path into a GitHub issue;
* use `Download diagnostics` and check the file before uploading;
* if a statistics value is sensitive, replace numbers before posting - the reason
  codes (`zero_reset_blocked`, `offset`, …) are what a maintainer needs.

## 10. Failure modes and what to expect

| Situation | Behaviour |
| --- | --- |
| Recorder not running / not set up | Protection still works; scan/repair/calibrate report `recorder_unavailable` and change nothing |
| Recorder rejects the adjustment (unknown statistic, bad unit) | item skipped with `reason: recorder_error` / `unknown_statistic_id` / `incompatible_unit`; other items continue |
| Verification cannot read the statistic | `verified: null` + `verification_warning`; the rollback offset is still returned |
| A backup cannot be written (disk full, permissions) | the item is skipped **before** the change is requested |
| Home Assistant stops mid-repair | the backup file is already on disk; the recorder either applied the offset or not - re-run `scan_statistics` to see the current state |
| Integration disabled/unloaded | Repairs issues of that entry are removed, no data is touched |
