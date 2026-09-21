# Energy Guard dashboard

Energy Guard ships one optional dashboard: [`dashboards/energy_guard.yaml`](../dashboards/energy_guard.yaml).

The dashboard is the *read-only* view plus three scan buttons.  Configuration
(and the repair workflow with its previews and confirmations) lives on the
optional [configuration page](CONFIGURATION.md) - the *Repair centre* view links
to it, so the two never overlap.

It answers three questions:

1. **Is my data protected right now?** (`Status` view)
2. **What is the difference between my raw source and the protected sensor the
   Energy Dashboard uses?** (`Sources vs protected` view)
3. **What did the last scan find, and how do I repair it safely?**
   (`Repair centre` view)

The dashboard is a convenience, never a requirement: the integration, its
entities and its services work exactly the same without it. Nothing in the
dashboard is needed for detection, blocking or repair.

## What the dashboard will never do

* It contains **no repair, calibration or clear button**. Data-changing services
  stay manual, confirmation-gated service calls that you run yourself in
  **Developer tools -> Actions**.
* The only service a card can call is `energy_guard.scan_statistics`, which is
  **read-only** and itself behind a click confirmation.
* It never reads a token, a secret or the recorder database path - it only shows
  entity states and attributes that are already visible in Home Assistant.
* It writes nothing to your configuration. It is not referenced by the
  integration at all; Home Assistant only reads it if you register it as a
  dashboard.

This is enforced by `tests/test_dashboard.py`: a card that starts calling
`repair_statistics`, `calibrate_utility_meter` or `clear_statistics` fails the
test suite.

## Requirements

* Home Assistant **2025.8.0** or newer (the minimum in `hacs.json`).
* Only **core** Home Assistant cards are used (`markdown`, `entities`,
  `conditional`, `history-graph`, `statistics-graph`, `button`), so no
  dashboard resources, HACS frontend plugins or custom cards are needed.
* Energy Guard installed and configured (HACS or manual).

## Installing it

HACS installs `custom_components/energy_guard` only - it cannot install
dashboards. Both ways below are one-time manual steps.

### Option A - copy and paste (no YAML configuration, recommended)

1. Open `dashboards/energy_guard.yaml` in the repository (or in the ZIP you
   downloaded) and copy everything from `title: Energy Guard` downwards.
2. In Home Assistant: **Settings -> Dashboards -> Add dashboard -> New dashboard
   from scratch**, give it a name (for example *Energy Guard*), create it.
3. Open the new dashboard, click the pencil (**Edit**) icon, open the three dot
   menu (**⋮**) and choose **Raw configuration editor**.
4. Delete the placeholder content and paste the copied YAML.
5. **Save**. The dashboard is now a normal UI dashboard: you can edit cards in
   the UI, drag them around and add your own.

### Option B - YAML mode

1. Copy the file into your Home Assistant configuration directory, for example
   as `energy_guard_dashboard.yaml`.
2. Register it in `configuration.yaml` (you edit this file, Energy Guard never
   does):

   ```yaml
   lovelace:
     dashboards:
       energy-guard:
         mode: yaml
         title: Energy Guard
         icon: mdi:shield-check-outline
         show_in_sidebar: true
         filename: energy_guard_dashboard.yaml
   ```

3. **Restart Home Assistant** (Lovelace dashboards are read at startup).

## The views

| View | Contents |
| --- | --- |
| `Status` | A plain-language headline (fine / needs review / starting up), the six diagnostic entities, an "open issue" card that appears only while `binary_sensor.energy_guard_data_issue` is `on`, and the detection settings the hub currently uses |
| `Sources vs protected` | Side by side view of the quick-start example: raw source vs `*_protected`, derived sum/difference, the utility meter, a 48 hour history graph and a 12 month long-term statistics graph |
| `Repair centre` | The safety model, three read-only scan buttons (`my sensors`, `all energy statistics`, `all statistics`), the scan result (scope, statistics read, candidates and cost suggestions), and the exact manual service calls for preview, repair, cost repair, meter calibration and clearing |

### Entities used

Deterministic Energy Guard entities (always exist once the integration is set
up):

| Entity | Meaning |
| --- | --- |
| `binary_sensor.energy_guard_data_issue` | `on` while an anomaly or a suspicious statistics offset is open |
| `sensor.energy_guard_last_anomaly` | Last event kind (`none` when nothing happened) |
| `sensor.energy_guard_last_scan` | Timestamp of the last statistics scan |
| `sensor.energy_guard_anomalies` | Number of anomalies in the issue window |
| `sensor.energy_guard_estimated_false_energy` | False kWh Energy Guard refused to publish |
| `sensor.energy_guard_statistics_issues` | Number of suspicious offsets found by the scan |

The `Sources vs protected` view uses the README quick-start example entities
(`sensor.grid_import`, `sensor.grid_import_protected`, `sensor.phase_a_protected`,
`sensor.phase_b_protected`, `sensor.phase_total`, `sensor.dp2_total`,
`sensor.not_measured_by_dp2`, `sensor.grid_import_monthly`). **Replace them with
your own entity ids**, or delete that view - the other two views work on their
own. A test asserts that every example entity used here is also documented in the
README, so the two cannot drift apart.

Your own protected and derived sensors are entity ids you chose in the options
flow; they are the ones your Energy Dashboard should use:

**Settings -> Dashboards -> Energy -> Grid consumption -> `sensor.<your>_protected`**

## Adapting it

* **Change an example entity:** in the raw configuration editor, search for
  `grid_import` and replace it with your entity id. Nothing else is needed.
* **Add a sensor you protect:** duplicate an `- entity:` row in the *Protected
  Energy Dashboard sources* card.
* **Remove the example view:** delete the whole `- title: Sources vs protected`
  block (including `path: energy-guard-sources`).
* **Hide the diagnostics from the UI:** leave them; they are cheap and they are
  the only place where the blocked false energy is visible.
* **Use your own layout:** the file is plain Lovelace. Feel free to convert the
  views to `type: sections`, split it into multiple dashboards or move cards -
  the entities and the names of the views/`path` values are the only contract.

## Repairing something you found

The `Repair centre` view prints the candidates and the exact snippets. The full
procedure (preview -> confirm -> backup -> verify -> rollback) is documented in
[docs/STATISTICS-REPAIR.md](STATISTICS-REPAIR.md) and
[docs/SERVICES.md](SERVICES.md); the guarantees behind it are in
[docs/SAFETY.md](SAFETY.md).

Two rules that the dashboard cannot enforce for you:

* The **kWh** repair needs `confirm: true`.
* The **monetary** part needs `confirm: true` **and** `confirm_cost: true` - it
  is a second, separate confirmation, never implied by the kWh repair.

## Troubleshooting

**The `Sources vs protected` view shows "Entity not available".**
That view uses the README example entity ids. Either you have not created those
entities, or your ids differ. Replace them with your own ids or delete the view;
the `Status` and `Repair centre` views are unaffected.

**"All statistics" found offsets on sensors I never configured.**
That is what the scope is for: `energy`/`all` deliberately look beyond the
sensors Energy Guard manages, so an installation that was already corrupted
before Energy Guard was installed can be cleaned up. The scan only *reports*
those offsets - repair them explicitly with `energy_guard.repair_statistics`
(see [STATISTICS-REPAIR.md](STATISTICS-REPAIR.md)).

**The **Scan statistics** button does nothing.**
Check `Settings -> Devices & Services -> Energy Guard` is loaded and that you
are logged in as an administrator. The button calls
`energy_guard.scan_statistics`; its result also appears in
`Settings -> Logbook`/Developer tools responses and refreshes
`sensor.energy_guard_statistics_issues`.

**The button asks for confirmation every time.**
By design: the confirmation exists so a stray tap on a wall tablet cannot start
work in the background.

**Everything is `unknown` after a restart.**
Normal. The status sensors populate with the first evaluation of the scan
interval; the dashboard shows the "starting up" headline until then.

**`history-graph` / `statistics-graph` cards are empty.**
Both need the recorder (and, for statistics, entities with a state class). The
`*_protected` sensors created by Energy Guard have `state_class:
total_increasing` and appear in long term statistics on their own.
