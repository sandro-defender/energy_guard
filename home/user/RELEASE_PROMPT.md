# Prompt for the next chat — copy everything below the line
---
Repo: **sandro-defender/energy_guard** (Home Assistant custom integration, installed via HACS).
**Context you need:** The "Phase 0" PR (#2 — CI workflow with tests/hassfest/hacs jobs, issue templates, SECURITY.md, pre-commit, .gitignore, version alignment at 1.1.0, ROADMAP.md) is already **merged into main and fully green** (tests 3.14, hassfest, hacs all pass). The **only remaining step** is publishing release **1.1.0** so HACS users actually receive all the work — they are still on **1.0.2** (latest existing tag/release). The repo's tags have **no `v` prefix** (`1.0.0`, `1.0.1`, `1.0.2`) — stay consistent.
**Facts to rely on:**
- `manifest.json`, `const.VERSION` and `pyproject.toml` all say `1.1.0`; `tests/test_contracts.py` enforces this and requires a `## [1.1.0]` section in `CHANGELOG.md`.
- `CHANGELOG.md` currently has an `## [Unreleased]` section whose entries (brand assets, service icons, prefilled forms, panel home dashboard, repo-link fix, project infrastructure/CI) belong in this release, plus an existing `## [1.1.0] - 2026-09-20` section.
- Today's date: 2026-09-21.
**Do exactly this, in order:**
1. `git fetch origin main` and confirm main's HEAD. Run `gh release list` — if `1.1.0` already exists, STOP and ask me.
2. **CHANGELOG consolidation PR:** move every entry from `## [Unreleased]` under the existing `## [1.1.0]` heading, set that section's date to `2026-09-21`, and leave a fresh empty `## [Unreleased]` heading at the top. Commit on the session branch, open a PR to `main`, wait for CI to be green, merge it. (Do not bump any version — everything stays `1.1.0`.)
3. Sync local `main`, then tag the merge commit: `git tag -a 1.1.0 <merge-commit-sha> -m "Energy Guard 1.1.0"` and `git push origin 1.1.0`.
4. Create the GitHub release from the tag: `gh release create 1.1.0 --title "1.1.0"` with the body set to the full `## [1.1.0]` section of the CHANGELOG (markdown preserved). Verify `gh release list` shows `1.1.0` as **Latest**.
5. Report the release URL and confirm HACS users will now get 1.1.0 (can take a few minutes to appear in HACS).
**Rules:** only push the session branch and the `1.1.0` tag; never move/delete existing tags; if CI fails on the PR, fix and re-run before merging; if `gh` hits an auth error, tell me to reconnect GitHub instead of asking for tokens.
**Optional, if I say "continue with the roadmap":** repo description polish (current one reads oddly: "home-assistant, hacs, energy dashboard entete guard" — suggest "Protect your Home Assistant Energy Dashboard from reconnect artefacts and repair corrupted statistics. HACS custom integration."), add config-panel screenshots to the README (Phase 0.5 of ROADMAP.md), then start Phase 1: pytest-cov coverage in CI, oldest-supported-HA verification, contract test keeping `translations/en.json` in sync with `strings.json`, then a Georgian (`ka`) translation.

