"""Generated output: YAML template export and repair reports.

Energy Guard never edits files that belong to the user.  This module only
*generates* YAML that users can copy into their own configuration if they prefer
template sensors over the integration-managed entities.

The generated templates use ``this.state`` to remember the last good value, so
they never publish a false ``0`` either.  They are a best-effort equivalent: the
integration entities additionally keep a diagnostic log, restore state across
restarts and are picked up by the statistics scanner.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .backups import write_json, write_text
from .const import MODE_DIFFERENCE, MODE_PHASE_SPLIT, MODE_SUM, VERSION
from .models import AnomalyEvent, SensorDefinition, UtilityMeterDefinition

HEADER = """# ---------------------------------------------------------------------------
# Energy Guard generated templates  (integration version {version})
# Generated at {created}
#
# This file is a *copy* of the Energy Guard definitions. Energy Guard never
# reads or edits it. Paste the block below into configuration.yaml (or into a
# package) if you prefer plain template sensors instead of the entities that
# the integration creates.
#
# Notes:
#  * These templates keep the last good value in `this.state`, so a reconnect
#    that reports 0 or `unavailable` never reaches the recorder as a valid 0.
#  * They do not create a diagnostic log, no statistics scanner and no repair
#    services. That is what the integration entities are for.
#  * Units are not converted: make sure every source of a derived sensor uses
#    the same unit as the generated sensor.
# ---------------------------------------------------------------------------
"""


def _jinja_guard(definition: SensorDefinition, expression: str) -> str:
    """Return the Jinja guard body shared by all generated templates."""
    zero_floor = definition.zero_min_previous
    return (
        "{% set previous = this.state | float(none) %}\n"
        f"{{% set value = {expression} %}}\n"
        "{% if value is none %}\n"
        "  unavailable\n"
        f"{{% elif value < {zero_floor} and previous is not none and previous >= {zero_floor} %}}\n"
        "  {{ previous }}\n"
        "{% else %}\n"
        "  {{ value }}\n"
        "{% endif %}"
    )


def _template_for(definition: SensorDefinition) -> dict[str, Any]:
    """Return the template sensor definition for one Energy Guard sensor."""
    unit = definition.unit_of_measurement
    sources = list(definition.all_sources)

    if not sources:
        expression = "none"
    elif definition.mode == MODE_SUM and definition.source_entity_ids:
        expression = " + ".join(
            f"(states('{entity}') | float(none))" for entity in sources
        )
    elif definition.mode in (MODE_DIFFERENCE, MODE_PHASE_SPLIT):
        expression = " - ".join(
            f"(states('{entity}') | float(none))" for entity in sources
        )
    else:
        expression = f"states('{sources[0]}') | float(none)"

    if definition.scale and definition.scale != 1:
        expression = f"({expression}) * {definition.scale}"
    if definition.offset:
        expression = f"({expression}) + {definition.offset}"

    availability = (
        " and ".join(
            f"states('{entity}') not in ['unknown', 'unavailable', 'none', '']"
            for entity in sources
        )
        or "true"
    )

    return {
        "name": definition.name,
        "unique_id": f"energy_guard_{definition.id}",
        "device_class": "energy",
        "state_class": "total_increasing",
        "unit_of_measurement": unit,
        "availability": "{{ " + availability + " }}",
        "state": _jinja_guard(definition, expression),
    }


def _yaml_scalar(value: Any) -> str:
    """Render a Python value as YAML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if "\n" in text:
        return "|-\n" + "\n".join(f"      {line}" for line in text.splitlines())
    return "'" + text.replace("'", "''") + "'"


def render_templates(
    protected: list[SensorDefinition],
    derived: list[SensorDefinition],
    utility_meters: list[UtilityMeterDefinition] | None = None,
    created: datetime | None = None,
) -> str:
    """Render the YAML text for the given definitions."""
    created = created or dt_util.utcnow()
    lines = [HEADER.format(version=VERSION, created=created.isoformat())]
    lines.append("template:")
    lines.append("  - sensor:")

    definitions = [
        definition for definition in (*protected, *derived) if definition.enabled
    ]
    if not definitions:
        lines.append("      # No Energy Guard sensors are defined (yet).")

    for definition in definitions:
        template = _template_for(definition)
        lines.append(f"      - name: {_yaml_scalar(template['name'])}")
        lines.append(f"        unique_id: {_yaml_scalar(template['unique_id'])}")
        lines.append(f"        device_class: {template['device_class']}")
        lines.append(f"        state_class: {template['state_class']}")
        lines.append(
            f"        unit_of_measurement: {_yaml_scalar(template['unit_of_measurement'])}"
        )
        lines.append(f"        availability: {_yaml_scalar(template['availability'])}")
        lines.append(f"        state: {_yaml_scalar(template['state'])}")
        lines.append("")

    meters = [meter for meter in (utility_meters or []) if meter.enabled]
    if meters:
        lines.append("")
        lines.append("# Utility meters Energy Guard can calibrate for you:")
        for meter in meters:
            lines.append(
                f"#   {meter.name}: {meter.utility_meter_entity_id} "
                f"(source {meter.source_entity_id or 'n/a'}, baseline "
                f"{meter.baseline_value})"
            )
    return "\n".join(lines).rstrip() + "\n"


async def async_write_templates(hass: HomeAssistant, path: Path, content: str) -> str:
    """Write the generated YAML to disk (never inside the user's packages)."""

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    await hass.async_add_executor_job(_write)
    return str(path)


# ---------------------------------------------------------------------------
# Repair reports
# ---------------------------------------------------------------------------
def build_report(
    *,
    hub_events: list[AnomalyEvent],
    candidates: list[dict[str, Any]],
    repairs: list[dict[str, Any]],
    calibrations: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the JSON structure of a repair report."""
    return {
        "energy_guard": {
            "version": VERSION,
            "generated_at": dt_util.utcnow().isoformat(),
            "read_only_scan_by_default": True,
        },
        "detected_anomalies": [event.to_dict() for event in hub_events],
        "statistics_candidates": candidates,
        "repairs": repairs,
        "calibrations": calibrations,
        "extra": extra or {},
    }


def render_markdown(report: dict[str, Any], *, title: str) -> str:
    """Render a human readable report."""
    lines = [f"# {title}", ""]
    meta = report.get("energy_guard", {})
    lines.append(f"Generated: {meta.get('generated_at')}")
    lines.append(f"Energy Guard version: {meta.get('version')}")
    lines.append("")

    anomalies = report.get("detected_anomalies", [])
    lines.append(f"## Detected anomalies ({len(anomalies)})")
    lines.append("")
    if anomalies:
        lines.append(
            "| Timestamp | Kind | Source | Protected | Previous | Invalid | Restored "
            "| Est. false energy | Unit | Message |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for event in anomalies:
            lines.append(
                "| {ts} | {kind} | {source} | {protected} | {prev} | {invalid} | "
                "{restored} | {false} | {unit} | {message} |".format(
                    ts=event.get("timestamp"),
                    kind=event.get("kind"),
                    source=event.get("source_entity") or "",
                    protected=event.get("protected_entity") or "",
                    prev=event.get("previous_value"),
                    invalid=event.get("invalid_value"),
                    restored=event.get("restored_value"),
                    false=event.get("estimated_false_energy"),
                    unit=event.get("unit"),
                    message=(event.get("message") or "").replace("|", "\\|"),
                )
            )
    else:
        lines.append("None.")
    lines.append("")

    candidates = report.get("statistics_candidates", [])
    lines.append(f"## Suspicious statistics offsets ({len(candidates)})")
    lines.append("")
    for candidate in candidates:
        lines.append(
            f"- `{candidate.get('statistic_id')}` at {candidate.get('start_time')}: "
            f"offset {candidate.get('offset')} {candidate.get('unit')} "
            f"(evidence: {', '.join(candidate.get('evidence', []))})"
        )
    lines.append("")

    repairs = report.get("repairs", [])
    lines.append(f"## Repairs ({len(repairs)})")
    lines.append("")
    if repairs:
        lines.append(
            "| Timestamp | Statistic | Start | Offset | Unit | Before | After "
            "| Verified | Backup |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for repair in repairs:
            lines.append(
                "| {ts} | {stat} | {start} | {offset} | {unit} | {before} | {after} "
                "| {verified} | {backup} |".format(
                    ts=repair.get("timestamp"),
                    stat=repair.get("statistic_id"),
                    start=repair.get("start_time"),
                    offset=repair.get("offset"),
                    unit=repair.get("unit"),
                    before=repair.get("before_value"),
                    after=repair.get("after_value"),
                    verified=repair.get("verified"),
                    backup=repair.get("backup_file") or "",
                )
            )
    else:
        lines.append("None.")
    lines.append("")

    calibrations = report.get("calibrations", [])
    lines.append(f"## Utility meter calibrations ({len(calibrations)})")
    lines.append("")
    for calibration in calibrations:
        lines.append(
            f"- {calibration.get('timestamp')}: `{calibration.get('entity_id')}` -> "
            f"{calibration.get('target_value')} {calibration.get('unit')} "
            f"(was {calibration.get('current_value')}, verified: "
            f"{calibration.get('verified')})"
        )
    lines.append("")
    extra = report.get("extra") or {}
    if extra:
        lines.append("## Additional information")
        lines.append("")
        for key, value in extra.items():
            lines.append(f"- **{key}**: {value}")
        lines.append("")
    return "\n".join(lines)


async def async_export_report(
    hass: HomeAssistant,
    *,
    directory: Path,
    report: dict[str, Any],
    name: str = "energy_guard_report",
) -> dict[str, Any]:
    """Write a JSON and a Markdown copy of ``report`` and return their paths."""
    timestamp = dt_util.utcnow().strftime("%Y%m%dT%H%M%SZ")
    json_path = directory / f"{name}_{timestamp}.json"
    md_path = directory / f"{name}_{timestamp}.md"
    await hass.async_add_executor_job(write_json, json_path, report)
    await hass.async_add_executor_job(
        write_text, md_path, render_markdown(report, title="Energy Guard report")
    )
    return {
        "json_file": str(json_path),
        "markdown_file": str(md_path),
        "directory": str(directory),
        "created_at": dt_util.utcnow().isoformat(),
    }
