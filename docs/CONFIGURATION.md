# Energy Guard configuration panel

Energy Guard ships an optional **sidebar configuration page**: everything the
options flow can do, plus the repair workflow, without leaving the page.  It is
the recommended way to configure the integration.

* **Where:** a sidebar entry named *Energy Guard* (icon `mdi:shield-cog-outline`),
  reachable at `/energy-guard-config`.
* **Who:** administrators only (`require_admin=True`).  A non-admin user does not
  see the entry and cannot call any of its commands.
* **Not required:** the integration, its entities, its services and the options
  flow all work without it.  If Home Assistant has no frontend, the panel is
  simply not registered.

## What it does

| Tab | Content |
| --- | --- |
| **Overview** | Status of `binary_sensor.energy_guard_data_issue`, the three read-only scan buttons, counters and pointers to the other tabs |
| **Protected** | list / add / edit / enable / disable / delete protected sensors (source sensor, unit, multiplier, offset, zero threshold, confirmation scans, recovery hold, grace period, decrease and jump rules, max value, precision) |
| **Derived** | the same for derived sensors (mode sum / difference / phase split, sources, total & parts, require-all-sources) |
| **Meters** | utility meters that may be calibrated (meter entity, source, cycle, baseline) |
| **Detection** | scan interval, statistics lookback, issue window, simultaneous failure threshold and window, diagnostic log retention |
| **Statistics** | default scan scope (`linked` / `energy` / `all`) and the scanner thresholds |
| **Cost** | tariff, currency, energy/cost statistic ids and an optional live price entity |
| **Backups** | backup/retention settings, the list of existing backup files and reports, and a button to export a report now |
| **Repair** | the scan buttons, the candidates of the last scan, and the preview → confirm → apply workflow for kWh **and** money (money needs its own confirmation) |
| **Templates** | the read-only YAML copy of your configuration (copy to clipboard, or write it with the service) |

Nothing in the panel is required for protection to work: it is a front end for
the configuration and the existing services.

## How it is built (for maintainers)

```
frontend/energy_guard-panel.js   web component (plain JS, no build step)
        │  hass.callWS(...)             hass.callService(...)
        ▼                                      ▼
websocket.py  (admin-only commands)      services.py (unchanged safety)
        ▼
config_api.py  (schema + storage, shared with options_flow.py)
        ▼
config entry options  →  reload  →  entities / scanner / services
```

* `config_api.py` is the single place that validates and stores configuration.
  The options flow and the panel both go through it, so the two cannot drift
  apart (a test adds a definition through both paths and compares the result).
* `websocket.py` registers the commands; every one is decorated with
  `@websocket_api.require_admin`.
* `panel.py` serves `frontend/energy_guard-panel.js` (`/energy_guard/...`) and
  registers the sidebar entry.  Registration failures are logged and ignored:
  the panel must never be able to break data protection.
* Writes end in `hass.config_entries.async_update_entry(...)` +
  `async_schedule_reload(...)`, exactly like a completed options flow.

### WebSocket commands

All commands require an administrator and are answers to
`{"id": <int>, "type": "<command>", ...}`.  Errors come back as
`{"success": false, "error": {"code": "energy_guard_error", "message": "..."}}`
with a message written for a human ("A name is required.", "… is already taken",
"Deleting a definition needs an explicit confirmation.").

| Command | Payload | Returns |
| --- | --- | --- |
| `energy_guard/config/get` | – | `config`, `entry_id`, `version`, `sections`, `definition_sections`, `settings_sections`, `limits`, `last_scan` |
| `energy_guard/config/choices` | – | `energy_sources` (cumulative energy sensors), `entities`, `units`, `modes`, `scan_scopes` for the forms |
| `energy_guard/config/set` | `section`, `data` | validated+merged section, `stored_under`, `reloaded` |
| `energy_guard/config/definition` | `section`, `action` (`add`/`update`/`toggle`/`delete`), `definition_id`, `data`, `confirm` | the stored `item` (or `null` after a delete) |
| `energy_guard/config/review` | – | `candidates`, `cost_suggestions`, scan state, recent `repairs`/`calibrations` |
| `energy_guard/config/files` | – | `backups`, `reports` (name, size, mtime - **never a path**) |
| `energy_guard/config/templates` | – | the generated YAML |
| `energy_guard/config/subscribe` | – | pushes `{"entry_id", "config"}` after every change made through the API |

`definition_id` is never called `id`: that key is reserved for the WebSocket
message id of the envelope, and Home Assistant rejects a command that reuses it.

### Safety rules

* **Admin only** - enforced by `@websocket_api.require_admin` on every command
  and by `require_admin=True` on the panel itself; tested with a read-only user
  token.
* **Same validation as the options flow** - schemas, the name/entity-id
  collision rule and the optional-value cleaning all come from `config_api.py`.
* **Deleting needs a confirmation** - the panel asks in a dialog, the backend
  refuses `action: delete` without `confirm: true`.
* **Repairs are preview-first** - the *Apply* button of a candidate stays
  disabled until the matching *Preview* (a `dry_run` repair) was answered, and
  the apply step shows its own confirmation dialog.  Money has a second,
  separate dialog and sends `confirm_cost: true` with `confirm: true`.
* **No credentials, no paths** - the panel never handles a token, a password or
  a database path; the WebSocket connection is authenticated by Home Assistant
  and the file listing returns names only.
* **Optional** - `panel.py` never raises; a failure only means "use the options
  flow instead".

## Preferences

**I prefer the classic options flow.** It stays available:
*Settings → Devices & Services → Energy Guard → Configure*, same nine sections.

**I do not want the sidebar entry.** It is registered while the integration is
set up.  A panel that is hidden can be removed from the sidebar in the UI
(*Edit sidebar*); the page stays reachable at `/energy-guard-config` and no
configuration is needed either way.

**The entry is missing.** Either you are not an administrator, or Home Assistant
has no frontend (`frontend`/`panel_custom` are part of `default_config`).  Check
the log for `Energy Guard could not register its configuration panel` - the
message names the reason and the integration keeps working.

## Troubleshooting

| Symptom | Reason / fix |
| --- | --- |
| The panel shows "Energy Guard is not set up." | The config entry failed to load; check *Settings → Devices & Services*. |
| "… is already taken" when adding a sensor | Another entity (or a source sensor) already uses that entity id; pick another name, for example with the ` protected` suffix. |
| A saved form does not change anything visible | Saving reloads the config entry; entities are recreated within a second or two.  Refresh if the `hass` object of the page is older. |
| *Apply* stays greyed out | Press *Preview* for that candidate first - the preview is the confirmation of exactly which row will be repaired. |
| The candidate disappeared after a repair | Expected: the scan no longer finds the offset once the repair is verified.  Use *Repair* → *Scan* to refresh. |
| "Scan finished (read-only): 0 candidate(s)" | Nothing suspicious in the window; widen the lookback or the scope. |
