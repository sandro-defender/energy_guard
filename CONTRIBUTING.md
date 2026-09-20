# Contributing to Energy Guard

Thanks for taking the time to help.  Energy Guard touches other people's energy
history and, in the worst case, their billing data - so the bar is not "does it
work", it is "does it fail safely".  This file explains how to get a change
merged.

By contributing you agree that your contribution is licensed under the MIT
licence of this repository (see [LICENSE](LICENSE)).

---

## 1. Before you write code

* **Bug report**: open an issue with
  * the Energy Guard version and the Home Assistant version,
  * the *Download diagnostics* output (it is redacted - no tokens, no database
    paths),
  * an `energy_guard.export_repair_report` output if the problem is about
    statistics,
  * the log excerpt around the anomaly (with `custom_components.energy_guard`
    set to `debug`).
* **Feature request**: describe the situation (sensor type, what goes wrong,
  what you would expect).  Please do not open a PR for a new option before the
  design is agreed - the options flow and the config-entry migration need to stay
  compatible, and that is easier to plan first.
* **Security / privacy**: never include real credentials, tokens, recorder
  database paths or billing documents in an issue or a PR.

---

## 2. The five rules a change may not weaken

These come from the project's safety contract ([docs/SAFETY.md](docs/SAFETY.md));
a PR that breaks one of them will be rejected even if it is convenient.

1. **Read-only by default.**  Scanning, exporting and diagnostics never modify
   data.  Only an explicit service call with a confirmation flag changes
   anything.
2. **Preview -> confirm -> backup -> verify -> rollback.**  Every data-changing
   action keeps that order.  A statistic that could not be backed up is never
   modified.
3. **Money is separate.**  Monetary repairs need `confirm_cost: true` on top of
   `confirm: true`, and are never applied automatically.
4. **No shortcuts around Home Assistant.**  Only official recorder / WebSocket /
   entity APIs.  No direct database access, no browser automation, no SSH, no
   tokens, no user YAML edits.
5. **Never claim success before verification.**  A repair is only reported as
   applied after the recorder queue drained and the value was read back.

Behaviour that is not covered by a test is not considered done: add the test in
the same PR.

---

## 3. Setting up

```bash
git clone https://github.com/sandro-defender/energy_guard.git
cd energy-guard
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
```

Details, editor setup and the manual-test recipe are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

---

## 4. Before you open a pull request

```bash
.venv/bin/python -m pytest tests/ -q --log-cli-level=CRITICAL   # all green
.venv/bin/ruff check custom_components tests                    # no findings
.venv/bin/ruff format --check custom_components tests            # formatted
```

CI runs exactly these three commands plus `hassfest` and the HACS validation.

Checklist for the PR description:

- [ ] what changed and why (one topic per PR - small and focused);
- [ ] the tests that cover it (new file or new test names);
- [ ] documentation updated: `README.md`, the relevant `docs/*.md`, and
      `CHANGELOG.md` under "Unreleased";
- [ ] `services.yaml`, `strings.json` and `translations/en.json` updated
      together when a service or option changed;
- [ ] no new runtime dependency (`manifest.json` stays `"requirements": []`);
- [ ] no change to existing entity ids, statistic ids or stored option keys
      without a config-entry migration (`custom_components/energy_guard/migrations.py`).

---

## 5. Style

* Follow the existing code: `snake_case`, type hints everywhere, `_LOGGER.debug`
  for decisions and `_LOGGER.warning`/`error` for anomalies, no `print()`.
* Module docstrings: at least two lines describing the module's responsibility
  (`tests/test_contracts.py` enforces this).
* Docstrings for public functions and classes; comments only where the *why* is
  not obvious from the code.
* Friendly, non-technical wording in anything the user sees: options flow labels,
  repair issue text, service messages.  They must explain what happened and what
  to do - never why the code thinks so.
* Keep commit messages focused: `<area>: <what changed>` in the imperative
  ("repair: skip statistics whose backup cannot be written").
* Use timezone-aware UTC datetimes internally; convert only for display.

---

## 6. Good first contributions

* a new detection signature with a test and a `docs/STATISTICS-REPAIR.md` entry;
* a translation of `translations/en.json`;
* an additional scenario in `tests/test_reconnect_sequence.py` (unusual meter
  behaviour during a reconnect);
* documentation clarifications - if a sentence in `docs/` was not enough to
  answer your own question, it probably needs improving.

---

## 7. Review process

1. A maintainer reviews for the five rules, test coverage and documentation.
2. CI (`.github/workflows/tests.yml`, `.github/workflows/validate.yml`) must be
   green.
3. The `CHANGELOG.md` entry is checked against the actual behaviour change.
4. Merges are squash-merged; releases are tagged by a maintainer
   (`docs/DEVELOPMENT.md` -> "Releasing").

Thank you for keeping other people's energy data honest.
