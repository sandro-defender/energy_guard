# Developing Energy Guard

Everything a contributor (human or AI agent) needs to change this integration
without breaking it.  Read [ARCHITECTURE.md](ARCHITECTURE.md) for the module
responsibilities and [SAFETY.md](SAFETY.md) for the rules a change must not
weaken.

---

## 1. Requirements

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.13+ (CI uses 3.14) | Home Assistant 2026.x requires 3.14 at runtime; the code itself stays 3.13-compatible |
| `uv` (recommended) | any | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Home Assistant | `2026.9.x` for the pinned test suite | pulled in by `pytest-homeassistant-custom-component` |
| `ruff` | recent | lint + format, configured in `pyproject.toml` |

The runtime dependency list of the integration is intentionally empty
(`manifest.json` -> `"requirements": []`, enforced by `tests/test_contracts.py`).
Anything new must be justified: a new dependency means a new install step for
every user.

---

## 2. Setting up a working copy

```bash
git clone https://github.com/your-github-username/energy-guard.git
cd energy-guard
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
```

`requirements_test.txt` pins `pytest-homeassistant-custom-component`, which pins
the matching Home Assistant release, so tests and runtime agree.  There is no
`pip` inside such a venv - always install through `uv pip install --python …`.

To run the integration against a real Home Assistant for manual checks, copy or
symlink the component into a scratch configuration directory:

```bash
mkdir -p ~/ha-config/custom_components
ln -s "$PWD/custom_components/energy_guard" ~/ha-config/custom_components/energy_guard
hass -c ~/ha-config    # or: python -m homeassistant -c ~/ha-config
```

Never point that at your production configuration directory.

---

## 3. Running the tests

```bash
.venv/bin/python -m pytest tests/ -q --log-cli-level=CRITICAL
```

* `--log-cli-level=CRITICAL` matters: SQLAlchemy logs every statement at INFO
  during recorder tests and would bury the real output.
* `-q --strict-markers` is the default from `pyproject.toml`; `asyncio_mode = auto`
  means test coroutines need no decorator.
* The full suite is fast (a few seconds).  Run it before every commit - a change
  that is not covered by a test is not ready.

### Test map

| File | Covers |
| --- | --- |
| `tests/conftest.py` | recorder fixture, config-entry helpers (`setup_guard`, `protected_definition`, `set_source`) |
| `tests/test_protect.py` | the guard rules: zero-reset hold, decrease, recovery, jumps, max values, simultaneous failures, restore |
| `tests/test_reconnect_sequence.py` | the six mandated scenarios end to end, plus the reconnect edge cases |
| `tests/test_statistics_repair.py` | scan, repair preview/apply, kWh + GEL, verification, rollback, clear |
| `tests/test_calibrate.py` | `calibrate_utility_meter` (exact value, from source, from history, guarded errors) |
| `tests/test_migrations.py` | config-entry migration and backwards compatibility of saved configuration |
| `tests/test_costs.py` | price handling, currency, worked examples |
| `tests/test_detection.py` | anomaly classification, `estimated_false_energy`, fingerprints |
| `tests/test_backups.py` | backup file naming/content/checksum, pruning, backup failure is fatal |
| `tests/test_flows_and_diagnostics.py` | config flow, options flow, diagnostics redaction, YAML export |
| `tests/test_options_flow.py` | every options-flow section end to end: add/edit/toggle/delete, what gets stored, entity ids surviving an edit, and that all seven forms render |
| `tests/test_models.py` | type normalisation of stored configuration (UI floats in integer fields), non-finite rejection, backup pruning |
| `tests/test_contracts.py` | repository contracts: manifest, `hacs.json`, services <-> `services.yaml` <-> `strings.json`, translations, docs, README, no direct database access, no secrets |

### Recorder test rules (do not fight them)

* Every test module that touches the recorder needs a module-level autouse
  `recorder` fixture returning `recorder_mock` and `recorder_db_url` set to
  `"sqlite://"`.  `conftest.py` already does this - do not invent an alternative.
* Long term statistics writes are queued.  Never assert straight after a write;
  poll with a bounded helper (see `_wait_for`, `_sums_after`, `_backup_count`).
* `utility_meter` entities are `unknown` until their source emits a delta - seed
  the source before and after setup, and declare `expected_lingering_timers=True`
  for tests that create meters.
* `unavailable` entities lose custom attributes.  Read `guard_status`,
  `last_good_value` etc. from the hub events or from a different entity, never
  from an `unavailable` state.
* `binary_sensor.energy_guard_data_issue` only turns `on` for
  warning/error-level events; an info-level `source_invalid` (a held reading)
  keeps it `off`.

Full details of these traps are listed in the "Recorder workarounds" section of
this file below; they were all found the hard way.

---

## 4. Linting and formatting

```bash
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format custom_components tests
.venv/bin/ruff format --check custom_components tests   # what CI runs
```

`pyproject.toml` selects `E,W,F,I,B,C4,UP,SIM,RUF,ASYNC,A,T20,PT` and ignores
`D`, `ANN` and `E501`.  Notes:

* `T20` bans `print()`; use `_LOGGER`.
* `ASYNC` flags blocking calls in async code - use `hass.async_add_executor_job`.
* `A` bans shadowing builtins (`id`, `type`, `input`); entity ids are `entity_id`.
* Line length is 88, handled by the formatter.

---

## 5. Where to make a change

### A new guard rule / detection rule

1. Add the constant to `const.py` (`REASON_*` for a held value, `EVENT_*` for a
   new anomaly kind) and, if it is user-visible, a plain-language entry in
   `RECOMMENDED_ACTIONS`.
2. Implement it in `protect.py` (`_evaluate_valid` / `_evaluate_invalid`) and
   return a `GuardOutcome` with the event (never emit the event yourself).
3. Add or extend the threshold in `DetectionRules` (`models.py`), remember that
   values coming from the UI are floats (add the field to `DetectionRules`'
   coercion table if it is an integer), expose it in
   `selectors.detection_schema()` so the options flow can edit it, and add it to
   `detection_schema` defaults in `options_flow.py`.
4. Add tests in `test_protect.py` (rule) and, if the rule can create corrupted
   statistics, in `test_detection.py` (scanner evidence).

### A new service

1. `const.py`: add to `SERVICE_NAMES` and the matching schema in `services.py`
   (`SCAN_SCHEMA`, `REPAIR_SCHEMA`, …).
2. `services.yaml`: parameters, defaults, translations of every field.
3. `strings.json` **and** `translations/en.json` (the contract test asserts they
   are identical) with a `services.<name>` block and `fields`.
4. `services.py`: register with `async_register_admin_service` if it changes
   data, otherwise `async_register` (user).  Return a response (`SupportsResponse`)
   built from a dataclass in `models.py`.
5. Document it in [SERVICES.md](SERVICES.md) and add a CHANGELOG entry.
6. `tests/test_contracts.py` fails if any of the four name lists disagree, if a
   data-changing service does not document `confirm` in `services.yaml` and
   `strings.json`, or if a read-only service documents `confirm`.

### A new option / menu section

1. Add the section name to `options_flow.MENU_OPTIONS` and a `options.step.<name>`
   block to `strings.json` + `translations/en.json` (title required).
2. Build the form with a helper in `selectors.py`; keep list sections as lists of
   dicts so a future version can add fields without breaking stored options.
3. Add a step test in `test_flows_and_diagnostics.py`.

### A change to the stored configuration

1. Bump `migrations.CURRENT_VERSION` and `config_flow`'s `VERSION`.
2. Add a `_migrate_vN_to_vN+1` function in `migrations.py` that normalises the
   old shape (see `async_migrate_entry` in `__init__.py`).
3. Extend `test_migrations.py` with the *old* payload shape as a literal, so the
   compatibility promise is tested, not assumed.

Entity ids and statistic ids must never change as part of a migration: users'
Energy Dashboard configuration, templates and automations reference them.

### A new module

Create it only when an existing module would otherwise need a fourth
responsibility.  Then: a module docstring of **at least two lines** (contract
test), no import of a higher layer (layering is described in
[ARCHITECTURE.md](ARCHITECTURE.md)), and a dedicated test file.

---

## 6. Recorder workarounds (all learned from real test failures)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `RuntimeError: Statistics API blocked` | recorder not mocked | module-level autouse `recorder` fixture + `recorder_db_url = "sqlite://"` |
| Assertions see old sums | statistics writes are queued | poll with a bounded helper, never `sleep()` |
| `statistics_during_period` returns nothing | `start`/`end` must be POSIX floats in HA 2026.9 | let `recorder_io.as_utc` convert |
| `Lingering timer after test` | a `utility_meter` or coordinator timer was created | `expected_lingering_timers = True` (or stop the entry) |
| Backup file overwritten within the same second | file name resolution is seconds | `backups.write_unique_json` adds a suffix |
| `NaN`/`inf` values compare as "in range" | `float("nan")` passes comparisons that raise no error | `protect.parse_float` rejects non-finite values |
| A form opens but cannot be submitted (`expected float at 'max'`) | `NumberSelectorConfig(max=None)` is invalid | omit unset optional values in `selectors._number`/`_int` |
| A form claims `Entity None is neither a valid entity ID` | `EntitySelector` rejects the `None` default of an *optional* field | use `OptionalEntitySelector` for optional entity fields, `required_field()` for required ones |
| A UI configured protected sensor never gets a state (`TypeError: 'float' object cannot be interpreted as an integer`) | the number selectors store floats, `round(value, precision=3.0)` fails | `models._coerce` normalises stored values to the declared types (`tests/test_models.py`) |
| The created entity is called `..._2` | the chosen name slugified to an entity id that is already taken (a source, a protected sensor, …) | `selectors.name_in_use` is checked in both flows; keep the `... protected` suffix in the name |
| `asyncio` warnings in CI only | test-only helpers (`async_block_till_done`) | keep them inside tests, not in the integration |

---

## 7. Debugging a live install

```yaml
# configuration.yaml - logging only, Energy Guard never writes here
logger:
  default: warning
  logs:
    custom_components.energy_guard: debug
```

* **Diagnostics**: *Settings -> Devices & Services -> Energy Guard -> Download
  diagnostics*.  The payload contains the configuration, entity ids, the anomaly
  log and statistics metadata.  It is redacted (`tests/test_flows_and_diagnostics.py`
  asserts that no secret-looking key or value can appear).
* **Anomaly log**: `sensor.energy_guard_last_anomaly` and the
  `energy_guard_anomaly` events carry `reason`, `source_state`, `guard_status`
  and a `recommended_action` - the fastest way to see *why* a value was held.
* **False energy counter**: `sensor.energy_guard_false_energy_*` accumulates the
  kWh and GEL that were prevented or detected; it is a diagnostic, not a
  billing source.
* **Report**: `energy_guard.export_repair_report` writes JSON + Markdown with the
  last N hours of events and repairs.

---

## 8. Releasing

1. Bump `const.VERSION` **and** `manifest.json:version` - they must be equal
   (`tests/test_contracts.py::test_manifest_matches_constants`).
2. Add a `CHANGELOG.md` section for the new version (Keep-a-Changelog style).
3. Check the minimum Home Assistant version in `hacs.json` and `README.md`; it
   must match the oldest release the code is tested against.
4. `ruff check` + full `pytest` run, then tag `vX.Y.Z` and push the tag.  HACS
   installs from the release zip, so the tag is the release.
5. Only bump `migrations.CURRENT_VERSION` if the *stored config shape* changed;
   a behaviour fix does not need it.

---

## 9. Definition of done

A change is ready when all of these hold:

- [ ] the new behaviour is covered by at least one test, and the full suite
      passes (`.venv/bin/python -m pytest tests/ -q --log-cli-level=CRITICAL`);
- [ ] `ruff check` and `ruff format --check` are clean;
- [ ] no confirmation, backup, verification or rollback rule was weakened
      (see [SAFETY.md](SAFETY.md));
- [ ] `services.yaml`, `strings.json`, `translations/en.json`, the README and the
      docs under `docs/` match the code;
- [ ] `CHANGELOG.md` has an entry;
- [ ] no new runtime dependency, no direct database access, no new secret;
- [ ] existing entity ids and statistic ids are untouched.

---

## 10. Getting help

Open an issue with the diagnostics download (redacted), the Energy Guard report
and the log excerpt around the anomaly.  Never paste tokens, passwords or
recorder database paths.
