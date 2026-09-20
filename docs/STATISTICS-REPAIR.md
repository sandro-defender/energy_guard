# Repairing corrupted statistics

This document explains what the corruption looks like, how to find it, and how
to repair it - with the exact numbers of a real reconnect through the whole
document.  The service API itself is in [SERVICES.md](SERVICES.md); the safety
rules behind every step are in [SAFETY.md](SAFETY.md).

---

## 1. What actually gets corrupted

A `total_increasing` meter reports a lifetime value, for example 38,243.46 kWh.
When the inverter, gateway or meter modem reconnects, Home Assistant may see the
entity as `unavailable` or as `0` for a short while.  Two independent damages
follow from that:

| Damage | Where | Why |
| --- | --- | --- |
| A false 0 is written into long term statistics | `statistics` / `statistics_short_term` and the Energy Dashboard | The recorder stores the states it sees; `total_increasing` with a `0` state means "the meter restarted", so the sum gets an extra increase |
| The recovery jump is counted as real consumption | Energy Dashboard, cost sensor, utility meters, billing templates | The statistic jumps from the false low value back to 38,243.46; the difference is treated as energy consumed in that hour |

With the documented example (Grid import, 0.235 GEL/kWh):

```
before the reconnect     sum = 38,243.46 kWh
one hour of `0`          sum = 38,243.46 kWh   state = 0.0
next hour, meter back    sum = 76,486.92 kWh   state = 38,243.46
                         ~~~~~~~~~~~~~~~~
                         false energy: 38,243.46 kWh
                         false money:  38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL
```

Because the damage is *inside the statistics tables*, fixing the live entity is
not enough: the history keeps the wrong sum until somebody adjusts it.  That
adjustment is what `energy_guard.repair_statistics` does - explicitly, with a
backup and a read-back.

---

## 2. Detection signatures

`energy_guard.scan_statistics` walks the hourly rows of a statistic and compares
the growth of the sum with the growth of the recorded state.  A single row pair
can produce several pieces of `evidence`:

| Evidence | Severity | Meaning |
| --- | --- | --- |
| `zero_then_restore` | `error` | The recorded state was (near) zero and came back to a high value, and the sum grew by roughly that high value. This is the reconnect fingerprint. |
| `source_was_zero_in_previous_period` | `error` | The companion of the above: the false zero really is in the previous row. |
| `state_flat_while_sum_grew` | `error` | The sum grew a lot while the entity's own state did not move - the classic "unavailable became 0" case where the state row is flat. |
| `sum_state_mismatch` | `error` | The sum grew much faster than the state (`sum_state_ratio`, default 3x) - typical of a restored lifetime value being counted twice. |
| `large_jump` | `warning`/`error` | The sum grew beyond `statistics_jump_threshold` and `statistics_jump_ratio` x the median growth. |
| `last_reset_changed` | `warning` | A reset was recorded in the same period; the candidate is down-graded to a warning because a genuine meter swap looks similar. |

For every candidate the scan reports:

* `offset` - what to add to the statistic from `start_time` on.  A false
  increase is repaired with a **negative** offset (`-38243.46`).
* `observed_delta`, `expected_delta` - what the sum did versus what it should
  have done (the median growth of the window).
* `estimated_false_energy` - `observed_delta - expected_delta`, the kWh and GEL
  amounts that are wrong.
* `fingerprint` - a stable hash of `(statistic_id, start_time, offset, unit)`.

Pass the fingerprint into the repair call and the repair is refused if the
statistic no longer matches what you reviewed (somebody already fixed it, or the
recorder recomputed the row).

### Cost suggestions

When the `Cost repair` section of the options knows a price (or an energy/cost
statistic pair), the scan adds one entry per candidate to `cost_suggestions`:

```json
{
  "statistic_id": "sensor.grid_import_cost",
  "start_time": "2026-09-19T22:00:00+00:00",
  "offset": -8987.2131,
  "unit": "GEL",
  "false_energy_kwh": 38243.46,
  "price": 0.235,
  "worked_example": "38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL",
  "separate_confirmation_required": true
}
```

Money is never included in a kWh confirmation: it needs `confirm_cost: true` in
addition to `confirm: true`.  See section 5.

---

## 3. The repair playbook

Do these steps in order.  Every step is safe to stop at.

**Step 0 - protect the source first.**  Before repairing history, make sure the
same reconnect cannot happen again: the `Protected` section of the options must
contain the source sensor (`sensor.grid_import` ->
`sensor.grid_import_protected`).  Repairing history while the live entity still
falls to 0 just creates new damage next time.  See
[ARCHITECTURE.md](ARCHITECTURE.md) for the guard rules.

**Step 1 - scan (read-only).**

```yaml
action: energy_guard.scan_statistics
data:
  statistic_ids: [sensor.grid_import]
  include_cost_suggestions: true
response_variable: scan
```

If you do not know yet which sensors are affected (the usual case after a
gateway outage, and always the case when Energy Guard is installed into an
installation that was already wrong), leave `statistic_ids` out and widen the
scope instead:

```yaml
action: energy_guard.scan_statistics
data:
  scope: all        # linked (default) | energy | all
  include_cost_suggestions: true
response_variable: scan
```

`scope: energy` reads every energy statistic of the recorder, `scope: all` every
cumulative statistic (water, gas, ...).  The candidates tell you exactly which
statistic each offset belongs to, so the repair step below stays per statistic -
Energy Guard never repairs a statistic you did not name.  The scan is capped at
500 statistics per run and reports what it read in `statistic_count`/`scope`.

Read `candidates[].evidence`, `offset` and `estimated_false_energy`.  A candidate
that only carries `last_reset_changed` is usually a real meter reset - do not
blindly repair it.

**Step 2 - preview the repair.**  Same service, no `confirm`:

```yaml
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import
      start_time: "2026-09-18T04:00:00+00:00"
      offset: -38243.46
      unit: kWh
      fingerprint: "245d3942605e4993"
      reason: "gateway reconnect 2026-09-18"
response_variable: preview
```

Check `status: "preview"`, `preview[].before_value` (76,486.92) and
`preview[].expected_value` (38,243.46).  Still nothing has changed - no backup is
written for a preview.

**Step 3 - apply the energy repair.**

```yaml
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import
      start_time: "2026-09-18T04:00:00+00:00"
      offset: -38243.46
      unit: kWh
      fingerprint: "245d3942605e4993"
      reason: "gateway reconnect 2026-09-18"
  confirm: true
  create_backup: true
  verify: true
response_variable: repair
```

The call now, in this order: writes the JSON backup of the affected rows, asks
the recorder to adjust the sum (`recorder.adjust_sum_statistics`), waits for the
recorder queue, re-reads the statistic, and only then reports
`applied[].verified: true` and `after_value: 38243.46`.

**Step 4 - repair the money separately.**  Add `cost_repairs` **and**
`confirm_cost: true` in the same call (see section 5).

**Step 5 - calibrate billing meters.**  If a `utility_meter` counted the false
energy, bring it back with `energy_guard.calibrate_utility_meter` (section 6).

**Step 6 - keep the evidence.**  `energy_guard.export_repair_report` writes a
JSON + Markdown record of the anomaly, the scan, the repairs and the
verification results - useful for a utility or landlord conversation.

### Rolling back

Every applied repair answers with a `rollback` list containing the inverse
offset:

```yaml
action: energy_guard.repair_statistics
data:
  repairs:
    - statistic_id: sensor.grid_import
      start_time: "2026-09-18T04:00:00+00:00"
      offset: 38243.46        # the inverse of the repair above
      unit: kWh
      reason: "rollback of the 2026-09-18 repair"
  confirm: true
```

The backup file (`<config>/energy_guard/backups/statistics_backup_<utc>_<statistic_id>.json`)
also contains the original rows with their checksum, so a repair can always be
compared against the state before it.

---

## 4. The complete worked example

```yaml
# 1 - what energy and money are wrong?
action: energy_guard.scan_statistics
data: {statistic_ids: [sensor.grid_import], include_cost_suggestions: true}
#    -> offset -38243.46 kWh, false energy 38,243.46 kWh
#    -> cost suggestion -8987.2131 GEL at 0.235 GEL/kWh

# 2 - kWh repair, explicitly confirmed
action: energy_guard.repair_statistics
data:
  repairs:
    - {statistic_id: sensor.grid_import, start_time: "2026-09-18T04:00:00+00:00",
       offset: -38243.46, unit: kWh, reason: "reconnect 2026-09-18"}
  confirm: true

# 3 - money repair, own confirmation
action: energy_guard.repair_statistics
data:
  cost_repairs:
    - {statistic_id: sensor.grid_import_cost, start_time: "2026-09-18T04:00:00+00:00",
       offset: -8987.2131, unit: GEL, reason: "reconnect 2026-09-18"}
  confirm: true
  confirm_cost: true

# 4 - the monthly meter that counted the false energy
action: energy_guard.calibrate_utility_meter
data: {entity_id: sensor.grid_import_monthly, value: 243.46, confirm: true}
```

---

## 5. Why money needs its own confirmation

An energy repair and a money repair fail in different ways, and a wrong money
offset is much harder to notice than a wrong kWh offset:

* the price may have changed since the reconnect, so the mirrored amount is an
  approximation, not a ledger entry;
* some cost statistics are fed by a fixed price, others by a price sensor;
  Energy Guard cannot know which is authoritative for a past hour;
* after a kWh repair, Home Assistant's own Energy Dashboard cost sensor
  recomputes **future** rows from the corrected energy, but the rows already
  written keep the false money - that is exactly what `cost_repairs` fixes.

So: `confirm: true` covers kWh, `confirm_cost: true` covers GEL (or any other
currency).  Without it the call raises a `ServiceValidationError` whose message
already shows the preview:

> Monetary repairs need their own confirmation: repeat the service call with
> `confirm_cost: true` (and `confirm: true`) … Preview: `sensor.grid_import_cost`
> -8987.2131 GEL (38,243.46 kWh x 0.235 GEL/kWh = 8,987.21 GEL)

Cost repair is never automatic.  Detection may *suggest* money, the operator
decides.

---

## 6. Utility meters and billing cycles

`utility_meter` entities keep their own state.  If the meter counted the false
38,243.46 kWh during a cycle, repairing the statistics does not change the meter
- it must be calibrated:

* **exact value** (`value: 243.46`) when you know the billing value from the
  utility bill;
* **from the protected source** (`source_entity_id: sensor.grid_import_protected`
  + `baseline_value` or `cycle_start`) when the meter should simply equal
  "protected reading - reading at the start of the cycle".

The service refuses entities that are not provided by the `utility_meter`
integration, previews the delta, and reads the new value back before it reports
`status: "calibrated"`.

---

## 7. Repair or clear?

| Situation | Do |
| --- | --- |
| A single spike/jump in an otherwise healthy statistic (the reconnect case) | **Repair** - the offset is known, the rest of the history is good |
| Several unrelated jumps from before Energy Guard was installed | Scan, then repair hour by hour (each candidate is one row pair) |
| The statistic is wrong almost everywhere (imported CSV, sensor replaced without resetting the sum, corrupted long term rows) | **Clear** with `energy_guard.clear_statistics` and let the recorder rebuild from the protected entity |
| The entity no longer exists / was renamed | Clear the orphaned statistic id, then delete the leftover rows in `Statistics` in the UI if it still shows |
| The value differs by a rounding amount (< 0.001 kWh) | Do nothing - Energy Guard treats that as already repaired |

Clearing deletes **all** history of the listed statistic ids.  It is the last
resort, never the first, and it is never automatic.

---

## 8. What happens after the repair

1. The adjusted rows are visible immediately in the statistics tables.
2. The Energy Dashboard re-reads long term statistics, so the graph corrects
   itself on its next refresh (opening the dashboard or waiting for the next
   hour is enough; a browser refresh may be needed).
3. The Energy Dashboard's own cost sensor keeps deriving money from the repaired
   energy for **new** rows; old cost rows only change if you repaired them.
4. The false kWh is reported through `sensor.energy_guard_false_energy_*` and the
   anomaly log so the event stays visible: the repair fixes history, the log
   explains it.

---

## 9. Troubleshooting

| Symptom | Likely cause / what to do |
| --- | --- |
| Scan returns `candidates: []` though the dashboard looks wrong | The window is too short (`start_time`) or the affected statistic is not in `statistic_ids`. Widen the window, run the scan with `scope: all`, and check `statistic_count`/`statistics[].rows` to see whether the statistic was read at all. |
| `status: recorder_unavailable` | The recorder is restarting or disabled (`recorder: purge_keep_days` misconfigured). Retry after Home Assistant is fully up. |
| `reason: already_repaired` | The row already has the expected sum (tolerance 0.001). Nothing to do. |
| `reason: fingerprint_mismatch` | The statistic changed after your scan - re-scan before repairing. |
| `reason: statistic_has_no_sum` | The statistic is not cumulative (no `sum`), so an offset makes no sense. Repair the metric differently. |
| `reason: backup_failed` | The backup directory is not writable (permissions/disk). **Nothing was changed.** Fix the path in the options or the filesystem, then retry. |
| `reason: incompatible_unit` | The offset unit is not convertible to the stored unit (e.g. `m3` for kWh). Use `kWh`/`Wh`, or omit `unit`. |
| `verified: false` / `verification_warning` | The recorder did not (yet) show the expected value. Re-read with a fresh `scan_statistics`; if it really did not apply, retry the repair once and check the log. |
| Energy Dashboard still shows the old value | Long term statistics are cached in the frontend; reload the page, or wait for the next hourly rollup. Check the raw values with `scan_statistics` first. |
| Money offset does not match the bill | The configured price differs from the historical tariff. Repair the kWh only and correct the money with the tariff you paid. |

---

## 10. Prevention is the primary product

Everything in this document exists because a repair is an exception.  With the
`Protected` entities from Energy Guard in the Energy Dashboard, the reconnect
never reaches the recorder as a `0`: the protected sensor holds the last good
value, stays `unavailable` while the source is invalid, and therefore never
produces the false increase that would have to be repaired here.  A repair is
needed only for damage that happened *before* Energy Guard was installed, for
sources that are not protected yet, or for data that was corrupted by something
else (an import, a manual reset, a reversed meter).

Related: [ARCHITECTURE.md](ARCHITECTURE.md) (guard rules),
[SAFETY.md](SAFETY.md) (backup, verification, rollback),
[SERVICES.md](SERVICES.md) (service API),
[DEVELOPMENT.md](DEVELOPMENT.md) (how this is tested).
