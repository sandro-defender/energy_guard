# Energy Guard services

There are six services.  Three never change anything, three do - and those three
ask for confirmation, write a backup first and verify the result afterwards.

| Service | Changes data? | Needs confirmation |
| --- | --- | --- |
| `energy_guard.scan_statistics` | no - read-only | - |
| `energy_guard.export_repair_report` | no (writes a new report file) | - |
| `energy_guard.export_templates` | no, unless `write_file: true` | `write_file: true` |
| `energy_guard.repair_statistics` | **yes** | `confirm: true`; `confirm_cost: true` for money |
| `energy_guard.clear_statistics` | **yes** | `confirm: true` |
| `energy_guard.calibrate_utility_meter` | **yes** | `confirm: true` |

Rules that apply to every service:

* `repair_statistics`, `clear_statistics` and `calibrate_utility_meter` are
  registered as **admin services** (`async_register_admin_service`), so only
  administrators can call them.
* All six support service responses, so automations can use
  `response_variable` and the Developer Tools show the result.
* All six accept an optional `config_entry_id` to address one Energy Guard
  config entry when several are set up.  Without it, `scan_statistics`,
  `repair_statistics`, `clear_statistics`, `calibrate_utility_meter` and
  `export_repair_report` handle **every** entry and wrap their per-entry results
  in a `config_entries` list; `export_templates` uses the first matching entry
  because the exported template file is a single file (pass `config_entry_id`
  for a different one).
* With exactly one matching entry the response is the entry's own result
  object (as shown in this document), and it contains `config_entry_id`.  Energy
  Guard declares `single_config_entry` in its manifest, so a normal installation
  has exactly one entry and always gets that flat response.
* Field names are stable; new fields are only ever *added*.  The parameter sets
  are checked against `services.yaml` and `strings.json` by
  `tests/test_contracts.py`.
* Every response carries a human-readable `message` and a machine-readable
  `status`.  Nothing is ever written before `confirm`/`confirm_cost` is `true`
  and `dry_run` is `false`.

---

## energy_guard.scan_statistics

**Purpose.** Read-only search for corrupted statistics: false zero resets, sum
jumps that do not match the real consumption, and the matching monetary offsets.
It produces the candidates you later repair explicitly.

**Parameters**

| Field | Required | Default | Meaning |
| --- | --- | --- | --- |
| `statistic_ids` | no | every Energy Guard statistic | statistic ids to scan, e.g. `sensor.grid_import` |
| `entity_ids` | no | - | alias for `statistic_ids`; entity ids are converted to statistic ids |
| `scope` | no | `linked` (from the options) | `linked` / `energy` / `all` - which statistics to scan. Ignored when `statistic_ids`/`entity_ids` are given |
| `start_time` | no | `now - lookback_hours` | start of the window |
| `end_time` | no | now | end of the window |
| `include_cost_suggestions` | no | `true` | also propose monetary offsets when cost repair is configured |
| `config_entry_id` | no | all entries | limit the scan to one entry |

**Example**

```yaml
action: energy_guard.scan_statistics
data:
  statistic_ids:
    - sensor.grid_import
    - sensor.grid_import_cost
  include_cost_suggestions: true
response_variable: scan_result
```

Scan everything the recorder stores, without naming a single sensor:

```yaml
action: energy_guard.scan_statistics
data:
  scope: all
  # optional, defaults to the configured lookback (24 h)
  # start_time: "2026-09-01T00:00:00+00:00"
response_variable: scan_result
```

**Scopes.** The scope decides which statistics a scan reads:

| Scope | What is scanned | When to use it |
| --- | --- | --- |
| `linked` (default) | the statistics Energy Guard is linked to: protected sources, derived sensors, utility meter sources, the configured cost statistics | everyday use; it is fast and quiet |
| `energy` | additionally every cumulative statistic in the recorder with an energy unit (kWh, Wh, MWh, MWh, ...) or `unit_class: energy` - including sensors Energy Guard does not manage | first install on an installation whose Energy Dashboard was already corrupted, or after a suspected outage of a sensor you have not registered yet |
| `all` | additionally every cumulative statistic in the recorder, whatever its unit (water, gas, volume, ...) | a full audit of everything the recorder stores |

The scope of a scan is reported back as `scope` (`linked`, `energy`, `all`, or
`explicit` when you passed `statistic_ids`/`entity_ids`) and stored on
`hub.last_scan_scope`, which the diagnostic sensor exposes as
`last_scan_scope`/`last_scan_statistic_count`.

Wide scopes are capped at 500 statistics per scan (`MAX_DISCOVERED_STATISTICS`);
the response then carries a warning and the linked statistics are always scanned
first. A scan never writes anything, whatever the scope.

Repairs for statistics that are **not** managed by Energy Guard work exactly the
same - pass the `statistic_id` from the candidates to
`energy_guard.repair_statistics` - but they are never done automatically and
never applied to a statistic you did not name.

**Dry run / confirmation.** Not applicable - this service is always read-only and
`read_only: true` is part of its response.

**Response** (trimmed, shape taken from a real run)

```json
{
  "status": "ok",
  "read_only": true,
  "start_time": "2026-09-18T21:00:00+00:00",
  "end_time": "2026-09-20T02:00:00+00:00",
  "statistic_ids": ["sensor.grid_import"],
  "statistic_count": 1,
  "scope": "linked",
  "candidate_count": 1,
  "candidates": [
    {
      "statistic_id": "sensor.grid_import",
      "start_time": "2026-09-19T22:00:00+00:00",
      "detected_at": "2026-09-20T03:47:40.164403+00:00",
      "unit": "kWh",
      "offset": -38243.46,
      "observed_delta": 38243.46,
      "expected_delta": 0.0,
      "estimated_false_energy": 38243.46,
      "evidence": ["zero_then_restore", "source_was_zero_in_previous_period"],
      "severity": "error",
      "fingerprint": "245d3942605e4993"
    }
  ],
  "cost_suggestions": [
    {
      "statistic_id": "sensor.grid_import_cost",
      "start_time": "2026-09-19T22:00:00+00:00",
      "offset": -8987.2131,
      "unit": "GEL",
      "reason": "Mirror of 38,243.46 kWh false energy at 0.235 GEL/kWh",
      "false_energy_kwh": 38243.46,
      "price": 0.235,
      "worked_example": "38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL",
      "source_energy_offset": -38243.46,
      "energy_statistic_id": "sensor.grid_import",
      "fingerprint": "5febc7c378184573",
      "separate_confirmation_required": true,
      "create_backup": true,
      "note": "Cost statistics are repaired separately: pass confirm_cost: true in addition to confirm: true to apply this offset."
    }
  ],
  "statistics": {
    "sensor.grid_import": {
      "unit": "kWh",
      "rows": 30,
      "first_row": "2026-09-18T21:00:00+00:00",
      "last_row": "2026-09-20T02:00:00+00:00",
      "total_increase": 38243.46
    }
  },
  "warnings": []
}
```

`status` is `ok`, or `recorder_unavailable` when the recorder is not running -
in that case nothing could be read and the `message` says so.  `warnings` lists
statistic ids that have no long term statistics (yet) and scan windows that were
clamped.  `severity` is `info`, `warning` or `error`; only `warning`/`error`
candidates are worth repairing.

**Possible errors.** `ServiceValidationError` when no statistic id can be
determined (configure a protected sensor or pass `statistic_ids` / `entity_ids`),
when an unknown `config_entry_id` is passed, or when the recorder component is
not available at all.

---

## energy_guard.repair_statistics

**Purpose.** Apply - only after confirmation - exactly the sum offsets you
reviewed.  Energy first, money separately.

**Parameters**

| Field | Required | Default | Meaning |
| --- | --- | --- | --- |
| `repairs` | yes (may be empty) | - | list of energy repairs, see below |
| `cost_repairs` | no | - | list of monetary repairs, same shape; needs `confirm_cost: true` |
| `confirm` | no | `false` | `true` applies the repairs, `false` only previews |
| `confirm_cost` | no | `false` | the separate confirmation for `cost_repairs` |
| `dry_run` | no | `false` | build the full report, change nothing (wins over `confirm`) |
| `create_backup` | no | `true` | write the JSON backup before touching a statistic |
| `verify` | no | `true` | re-read the statistic after the recorder queue drained |
| `config_entry_id` | no | all entries | limit the call to one entry |

Every item of `repairs` / `cost_repairs`:

| Field | Required | Meaning |
| --- | --- | --- |
| `statistic_id` | yes | e.g. `sensor.grid_import` |
| `start_time` | yes | the timestamp of the affected hour, e.g. `2026-09-18T04:00:00+00:00` |
| `offset` | yes | value added to the statistic from `start_time` on; negative repairs a false increase |
| `unit` | no | unit of the offset; must be compatible with the stored unit |
| `reason` | no | free text stored in the backup file and the diagnostic log |
| `fingerprint` | no | from `scan_statistics`; a mismatch skips that item |

**Example - preview first (no confirmation)**

```yaml
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import
      start_time: "2026-09-18T04:00:00+00:00"
      offset: -38243.46
      unit: kWh
      reason: "gateway reconnect 2026-09-18"
response_variable: repair_preview
```

**Example - apply energy and money (two confirmations)**

```yaml
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import
      start_time: "2026-09-18T04:00:00+00:00"
      offset: -38243.46
      unit: kWh
  cost_repairs:
    - statistic_id: sensor.grid_import_cost
      start_time: "2026-09-18T04:00:00+00:00"
      offset: -8987.21
      unit: GEL
  confirm: true          # energy
  confirm_cost: true     # money, separately
  create_backup: true
  verify: true
```

**Dry run behaviour.** `dry_run: true` (or a missing `confirm`) returns
`status: "preview"` with the same structure as an applied repair - including the
before/after values and the rollback offset - and writes neither a change nor a
backup.

**Confirmation behaviour.** Without `confirm: true` nothing is written.  Money
without `confirm_cost: true` raises a `ServiceValidationError` that already
contains the money preview and changes nothing:

> Monetary repairs need their own confirmation: repeat the service call with
> `confirm_cost: true` (and `confirm: true`) after reviewing the preview returned
> by `energy_guard.scan_statistics`. Nothing was changed. Preview:
> `sensor.grid_import_cost` -8987.2131 GEL (38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL)

**Response (applied)** - trimmed, shape taken from a real run

```json
{
  "status": "applied",
  "confirmed": true,
  "dry_run": false,
  "applied_count": 2,
  "applied": [
    {
      "statistic_id": "sensor.grid_import",
      "start_time": "2026-09-19T22:00:00+00:00",
      "offset": -38243.46,
      "unit": "kWh",
      "applied": true,
      "backup_file": "/config/energy_guard/backups/statistics_backup_20260920T034740Z_sensor_grid_import.json",
      "before_value": 76486.92,
      "after_value": 38243.46,
      "expected_value": 38243.46,
      "verified": true,
      "error": null,
      "rollback_adjustment": 38243.46
    },
    {
      "statistic_id": "sensor.grid_import_cost",
      "start_time": "2026-09-19T22:00:00+00:00",
      "offset": -8987.2131,
      "unit": "GEL",
      "applied": true,
      "backup_file": "/config/energy_guard/backups/statistics_backup_20260920T034740Z_sensor_grid_import_cost.json",
      "before_value": 17974.426199999998,
      "after_value": 8987.213099999997,
      "expected_value": 8987.2131,
      "verified": true,
      "error": null,
      "rollback_adjustment": 8987.2131
    }
  ],
  "preview": [],
  "skipped": [],
  "backups": [
    {
      "file": "/config/energy_guard/backups/statistics_backup_20260920T034740Z_sensor_grid_import.json",
      "name": "statistics_backup_20260920T034740Z_sensor_grid_import.json",
      "rows": 5,
      "checksum": "sha256:03b8fb045e495afb3e1cc2b88e18d2dc96c6995fff1cfb9a491c69892a13f068",
      "created_at": "2026-09-20T03:47:40.199510+00:00",
      "unit": "kWh",
      "statistic_id": "sensor.grid_import"
    }
  ],
  "rollback": [
    {
      "statistic_id": "sensor.grid_import",
      "start_time": "2026-09-19T22:00:00+00:00",
      "offset": 38243.46,
      "unit": "kWh",
      "backup_file": "/config/energy_guard/backups/statistics_backup_20260920T034740Z_sensor_grid_import.json",
      "message": "To undo this repair, call energy_guard.repair_statistics with this inverse offset (and confirm: true). The backup file contains the original rows."
    }
  ],
  "cost_repairs_awaiting_confirmation": [],
  "message": "Applied 2 repair(s), including 1 monetary correction(s)."
}
```

`status` is `preview`, `applied`, `partial` (some items were skipped) or
`confirmation_required`.  When verification could not confirm the result, the
response additionally contains `verification_warning`, and the item keeps
`verified: false` (read back but the value differs) or `verified: null` (could
not be read).  `cost_repairs_awaiting_confirmation` lists monetary repairs that
were held back because `confirm_cost` was missing.

**Skipped items and possible errors**

| Situation | Result |
| --- | --- |
| `cost_repairs` without `confirm_cost: true` | `ServiceValidationError`, nothing changed |
| unknown statistic | item skipped, `reason: unknown_statistic_id` |
| statistic has no sum (not cumulative) | item skipped, `reason: statistic_has_no_sum` |
| offset unit incompatible with the stored unit | item skipped, `reason: incompatible_unit` |
| `fingerprint` does not match the current data | item skipped, `reason: fingerprint_mismatch` |
| value already has the expected sum | item skipped, `reason: already_repaired` |
| recorder rejects a single call | item skipped, `reason: recorder_error`; other items continue |
| backup file cannot be written | item skipped, `reason: backup_failed`; **that statistic is not changed** |
| verification could not read the statistic | `verified: null` + `verification_warning` |

---

## energy_guard.calibrate_utility_meter

**Purpose.** Bring a `utility_meter` entity (for example a monthly billing meter)
back to the right value after a corrupted cycle - from an exact value or from a
protected cumulative source minus a baseline.

**Parameters**

| Field | Required | Default | Meaning |
| --- | --- | --- | --- |
| `entity_id` | yes | - | the `utility_meter` sensor to calibrate |
| `value` | one of `value` / `source_entity_id` | - | exact target value |
| `source_entity_id` | one of `value` / `source_entity_id` | - | cumulative source (usually the protected sensor) |
| `baseline_value` | no | `0` | source value at the start of the cycle |
| `cycle_start` | no | - | read the baseline from the recorded history at this timestamp (used when `baseline_value` is omitted) |
| `cycle` | no | - | label of the cycle, kept in the response |
| `confirm` | no | `false` | apply the calibration |
| `dry_run` | no | `false` | compute everything, change nothing |
| `config_entry_id` | no | all entries | limit the call to one entry |

If the meter is registered under **Utility Meters** in the options, its
configured source, baseline and cycle label are used as defaults.

**Example - preview, then exact value**

```yaml
action: energy_guard.calibrate_utility_meter
data:
  entity_id: sensor.grid_import_monthly
  value: 243.456
# second call, after reviewing the preview:
#   same data plus "confirm: true"
```

**Example - computed from the protected source**

```yaml
action: energy_guard.calibrate_utility_meter
data:
  entity_id: sensor.grid_import_monthly
  source_entity_id: sensor.grid_import_protected
  baseline_value: 38000.0            # meter reading when the cycle started
  cycle_start: "2026-09-01T00:00:00+04:00"
  cycle: monthly
  confirm: true
```

**Dry run / confirmation.** Without `confirm: true` the response is a preview
(`status: "preview"`, `applied: false`, no `new_value`/`verified`) with the
current value, the target value and the delta.  Nothing is calibrated.

**Response - preview**

```json
{
  "entity_id": "sensor.grid_import_monthly",
  "unit": "kWh",
  "current_value": 1.0,
  "target_value": 243.456,
  "delta": 242.456,
  "source_entity_id": null,
  "source_value": null,
  "baseline_value": null,
  "cycle_start": null,
  "cycle": null,
  "confirmed": false,
  "applied": false,
  "status": "preview",
  "message": "Preview only: would calibrate sensor.grid_import_monthly from 1.0 to 243.456 kWh. Re-run with confirm: true and dry_run: false to apply."
}
```

**Response - applied**

```json
{
  "entity_id": "sensor.grid_import_monthly",
  "unit": "kWh",
  "current_value": 1.0,
  "target_value": 243.456,
  "delta": 242.456,
  "source_entity_id": null,
  "source_value": null,
  "baseline_value": null,
  "cycle_start": null,
  "cycle": null,
  "confirmed": true,
  "applied": true,
  "new_value": 243.456,
  "verified": true,
  "status": "calibrated",
  "message": "Calibrated sensor.grid_import_monthly to 243.456 kWh (was 1.0)."
}
```

`status` is `preview`, `calibrated`, `already_correct` (the meter already reports
the target value) or `failed`.

**Possible errors.** `ServiceValidationError` when the entity does not exist, is
not provided by the `utility_meter` integration (Energy Guard never touches other
integrations' entities), when neither `value` nor `source_entity_id` is given,
when the source is currently not numeric, or when `cycle_start` was passed but
there is no recorded value for it (pass `baseline_value` instead).

---

## energy_guard.clear_statistics

**Purpose.** Delete the long term statistics of **exactly** the listed statistic
ids - the escape hatch for data that cannot be repaired.  The recorder rebuilds
them from the entities that keep reporting.

**Parameters**

| Field | Required | Default | Meaning |
| --- | --- | --- | --- |
| `statistic_ids` | yes | - | statistic ids to clear (must not be empty) |
| `confirm` | no | `false` | required to actually clear |
| `create_backup` | no | `true` | back up every row of every statistic first |
| `config_entry_id` | no | all entries | limit the call to one entry |

**Example**

```yaml
action: energy_guard.clear_statistics
data:
  statistic_ids:
    - sensor.grid_import
  confirm: true
```

**Dry run / confirmation.** Without `confirm: true` the response is
`status: "confirmation_required"` with a preview listing, per statistic, whether
it exists, its unit, its source, the number of rows and the first/last row
timestamp.  Nothing is cleared.

**Response - preview**

```json
{
  "confirm_required": true,
  "confirmed": false,
  "statistic_ids": ["sensor.grid_import"],
  "preview": [
    {
      "statistic_id": "sensor.grid_import",
      "found": true,
      "unit": "kWh",
      "source": "recorder",
      "rows": 30,
      "first_row": "2026-09-18T21:00:00+00:00",
      "last_row": "2026-09-20T02:00:00+00:00"
    }
  ],
  "backups": [],
  "cleared": [],
  "message": "Nothing was cleared. Energy Guard never clears statistics without confirm: true. Review the preview and repeat the service call with confirm: true if the affected statistics are correct.",
  "status": "confirmation_required"
}
```

**Response - cleared**

```json
{
  "confirm_required": true,
  "confirmed": true,
  "statistic_ids": ["sensor.grid_import"],
  "preview": [ { "statistic_id": "sensor.grid_import", "found": true, "unit": "kWh",
                 "source": "recorder", "rows": 30,
                 "first_row": "2026-09-18T21:00:00+00:00",
                 "last_row": "2026-09-20T02:00:00+00:00" } ],
  "backups": [ { "file": "/config/energy_guard/backups/statistics_backup_20260920T034751Z_sensor_grid_import.json",
                 "name": "statistics_backup_20260920T034751Z_sensor_grid_import.json",
                 "rows": 30, "checksum": "sha256:0de8a35a…",
                 "created_at": "2026-09-20T03:47:51.319570+00:00",
                 "unit": "kWh", "statistic_id": "sensor.grid_import" } ],
  "cleared": ["sensor.grid_import"],
  "message": "Cleared 1 statistic(s). Statistics will be rebuilt by the recorder for entities that are still reporting data.",
  "status": "cleared"
}
```

`status` is `confirmation_required`, `cleared`, `partial` (an id survived a
bounded retry), or `failed`.  `cleared` is empty in the last two cases.

**Possible errors.** `ServiceValidationError` for an empty list or an unknown
`config_entry_id`.  If a backup cannot be written, the call **fails** and nothing
is cleared (`status: "failed"`) - pass `create_backup: false` only if you
deliberately want to clear without a backup.

---

## energy_guard.export_repair_report

**Purpose.** Write a JSON **and** a Markdown report of what happened: anomalies,
scan candidates, repairs and calibrations.  Handy for support requests or as a
record for a billing dispute.

**Parameters**

| Field | Required | Default | Meaning |
| --- | --- | --- | --- |
| `name` | no | `energy_guard_report` | file name prefix |
| `hours` | no | `168` (7 days) | how far back to include events/repairs (1-8760) |
| `config_entry_id` | no | all entries | limit the call to one entry |

**Example**

```yaml
action: energy_guard.export_repair_report
data:
  name: reconnect_2026_09
  hours: 720
response_variable: report
```

**Dry run / confirmation.** Not applicable - it only creates new files under
`<config>/energy_guard/reports/`; it never modifies existing data.

**Response**

```json
{
  "config_entry_id": "01J8Z…",
  "json_file": "/config/energy_guard/reports/reconnect_2026_09_20260920T034751Z.json",
  "markdown_file": "/config/energy_guard/reports/reconnect_2026_09_20260920T034751Z.md",
  "directory": "/config/energy_guard/reports",
  "created_at": "2026-09-20T03:47:51.339099+00:00"
}
```

**Possible errors.** `ServiceValidationError` when Energy Guard is not
configured; a filesystem error makes the service call fail (no response is
returned) - the log contains the path that could not be written.

---

## energy_guard.export_templates

**Purpose.** Return the YAML template sensors that mirror your Energy Guard
definitions, for users who prefer plain templates over the integration entities.
The output is a **copy**: Energy Guard never reads it back and never writes into
your configuration.

**Parameters**

| Field | Required | Default | Meaning |
| --- | --- | --- | --- |
| `write_file` | no | `false` | also write `<config>/energy_guard/energy_guard_templates.yaml` |
| `config_entry_id` | no | all entries | limit the call to one entry |

**Example**

```yaml
action: energy_guard.export_templates
data:
  write_file: true
response_variable: templates
```

**Dry run / confirmation.** Without `write_file: true` the service only returns
the YAML in its response; no file is written.  With several config entries the
first matching entry (or the one named by `config_entry_id`) is exported, since
the template file is a single file.

**Response**

```json
{
  "yaml": "template:\n  - sensor:\n      - name: 'Grid import protected'\n        device_class: energy\n        state_class: total_increasing\n …",
  "sensor_count": 1,
  "note": "This YAML is a copy of your Energy Guard definitions. Energy Guard never reads or edits your configuration files.",
  "written_to": "/config/energy_guard/energy_guard_templates.yaml"
}
```

`written_to` only appears with `write_file: true`.

**Possible errors.** `ServiceValidationError` when Energy Guard is not
configured; a filesystem error makes the service call fail.

---

## Calling services from automations

The modifying services are admin-only, but automations run with the permissions
of the user who created them.  Preview first, keep the confirmations explicit,
and never compute an offset in a template and apply it blindly:

```yaml
alias: Energy Guard - nightly scan, notify only
triggers:
  - trigger: time
    at: "03:30:00"
actions:
  - action: energy_guard.scan_statistics
    data:
      include_cost_suggestions: true
    response_variable: scan
  - if: "{{ scan.candidate_count > 0 }}"
    then:
      - action: notify.persistent_notification
        data:
          title: "Energy Guard found {{ scan.candidate_count }} suspect offset(s)"
          message: >-
            {{ scan.candidates | map(attribute='statistic_id') | join(', ') }} -
            open Settings -> Repairs to review. Nothing was changed.
```

Related documents: [ARCHITECTURE.md](ARCHITECTURE.md) for the internal flow,
[SAFETY.md](SAFETY.md) for the confirmation/backup/rollback rules,
[STATISTICS-REPAIR.md](STATISTICS-REPAIR.md) for the corruption patterns these
services exist for.
