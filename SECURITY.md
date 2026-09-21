# Security Policy

Energy Guard is a data-safety tool: its entire purpose is to protect recorded
Home Assistant energy data and to repair corrupted statistics **only** when a
user explicitly confirms it. Reports that a data-changing operation can run
without confirmation, modify data it does not own, or weaken the safety model
described in [`docs/SAFETY.md`](docs/SAFETY.md) are treated as security issues.

## Supported versions

Only the latest published [release](https://github.com/sandro-defender/energy_guard/releases)
is supported. HACS keeps users current, so please always test against the
latest version before reporting.

## How to report

1. **Preferred:** use GitHub *private vulnerability reporting*
   (Security tab of this repository -> Report a vulnerability). This keeps the
   report private until a fix is ready.
2. If that is impossible, contact **@sandro-defender** directly.

Please include:

* the Energy Guard version and the Home Assistant version,
* the exact service calls / panel actions involved,
* what changed (or could have changed) that should not have,
* logs or a recorder/statistics dump that shows the behaviour.

**Please do not** open a public issue for anything that could put another
user's recorded data at risk before a fixed version is released.

## Scope

In scope: every data-changing path (`repair_statistics`, `clear_statistics`,
`calibrate_utility_meter`), the confirmation flags (`confirm`, `confirm_cost`),
backup integrity, and the admin-only guarantees of the configuration panel and
WebSocket API.

Out of scope: Home Assistant core, the recorder integration itself, and
third-party integrations Energy Guard reads sensors from.
