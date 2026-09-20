"""JavaScript smoke tests for the configuration panel.

The panel is a browser web component, so it cannot be rendered in this test
suite.  It *can* be parsed and executed headlessly with Node, which is what this
module does: the file is loaded in a minimal sandbox (no DOM, stubbed
``customElements``/``window``) and the pure logic - escaping, labels, form
reading, field descriptors - is exercised for real.

The test is skipped when Node is not installed; it never replaces the Python
tests, which remain the source of truth for behaviour.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from custom_components.energy_guard import panel as panel_module

PANEL_JS = panel_module._FRONTEND_DIR / "energy_guard-panel.js"
NODE = shutil.which("node") or shutil.which("nodejs")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.argv[2], 'utf8');

class FakeShadow {
  constructor() { this.innerHTML = ''; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}
class FakeElement {
  constructor() { this.shadowRoot = new FakeShadow(); this._hass = null; this._loaded = false; }
  attachShadow() { return this.shadowRoot; }
}
const registry = new Map();
const sandbox = {
  console,
  HTMLElement: FakeElement,
  customElements: {
    define: (name, cls) => registry.set(name, cls),
    get: (name) => registry.get(name),
  },
  window: { confirm: () => false, setTimeout: () => 0 },
  navigator: { clipboard: { writeText: async () => {} } },
  setTimeout: () => 0,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source + '\n;globalThis.__FIELDS__ = FIELDS;', sandbox, {
  filename: 'energy_guard-panel.js',
});

const Panel = registry.get('energy-guard-config-panel');
const panel = new Panel();
// Never assign `hass` (that would start loading); the pure logic needs no
// connection.
panel._config = {
  protected_sensors: [{ id: 'a', name: 'Grid import protected', enabled: false }],
  derived_sensors: [],
  utility_meters: [{ id: 'm', name: 'Monthly', utility_meter_entity_id: 'sensor.m' }],
  detection_rules: { scan_interval: 300, scan_scope: 'linked' },
};
panel._choices = { energy_sources: [{ entity_id: 'sensor.grid_import', name: 'Grid import' }] };

const form = {
  elements: {
    name: { value: ' Garage protected ' },
    source_entity_id: { value: 'sensor.garage' },
    enabled: { checked: true },
    offset: { value: '1.5' },
    max_value: { value: '' },
    source_entity_ids: { selectedOptions: [] },
  },
};

panel._hass = { states: {} };
panel._meta = { version: '1.1.0-test' };
const now = Date.now();
panel._dashboard = {
  window_days: 14,
  protection: { blocked_total: 12, blocked_24h: 3, false_kwh_total: 45.5, false_kwh_24h: 7.25, events_total: 20 },
  points: [
    { t: new Date(now - 3600 * 1000).toISOString(), blocked: true, energy: 7.25 },
    { t: new Date(now - 26 * 3600 * 1000).toISOString(), blocked: true, energy: 38.25 },
    { t: new Date(now - 3600 * 1000).toISOString(), blocked: false, energy: 0 },
  ],
  by_kind: [
    { kind: 'zero_reset_blocked', label: 'False zero blocked', count: 11 },
    { kind: 'source_recovered', label: 'Source recovered', count: 9 },
  ],
  recent: [
    { t: new Date(now - 60000).toISOString(), severity: 'warning', kind: 'zero_reset_blocked', label: 'False zero blocked', message: 'Held back a <b>0</b>', source: 'sensor.grid_import' },
  ],
  scan: { last_scan: new Date(now - 7200 * 1000).toISOString(), scope: 'linked', statistic_count: 4, error: null, candidates: 0 },
  counts: { protected: 1, protected_enabled: 1, derived: 0, derived_enabled: 0, meters: 1, meters_enabled: 1 },
  actions: { repairs: 2, calibrations: 1 },
};
panel._review = { candidates: [] };

const buckets = panel._daily(panel._dashboard.points, 14);
const startOfToday = new Date();
startOfToday.setHours(0, 0, 0, 0);
const dayBuckets = panel._daily(
  [
    { t: new Date(startOfToday.getTime() + 12 * 3600 * 1000).toISOString(), blocked: true, energy: 1 },
    { t: new Date(startOfToday.getTime() - 12 * 3600 * 1000).toISOString(), blocked: true, energy: 2 },
  ],
  2
);
const dashboardChecks = {
  fmtInt: panel._fmt(12),
  fmtFloat: panel._fmt(45.678),
  fmtBad: panel._fmt(undefined),
  agoHour: panel._ago(new Date(now - 3600 * 1000).toISOString()),
  agoNever: panel._ago(null),
  buckets: buckets.length,
  bucketTotal: buckets.reduce((sum, bucket) => sum + bucket.blocked, 0),
  bucketEnergy: buckets.reduce((sum, bucket) => sum + bucket.energy, 0),
  todayBlocked: dayBuckets[1].blocked,
  yesterdayBlocked: dayBuckets[0].blocked,
  yesterdayEnergy: dayBuckets[1 - 1].energy,
  bars: panel._barsChart(buckets),
  barsEmpty: panel._barsChart(panel._daily([], 14)),
  area: panel._areaChart(buckets, 'kWh'),
  areaEmpty: panel._areaChart(panel._daily([], 14), 'kWh'),
  donut: panel._donutChart(panel._dashboard.by_kind),
  donutEmpty: panel._donutChart([]),
  spark: panel._spark([1, 2, 3]),
  overview: panel._overview(),
  overviewLoading: (() => {
    const saved = panel._dashboard;
    panel._dashboard = null;
    const out = panel._overview();
    panel._dashboard = saved;
    return out;
  })(),
  candidate: panel._candidateRow({
    statistic_id: "sensor.x",
    start_time: "2026-09-12T02:00:00+00:00",
    offset: -5,
    unit: "kWh",
    estimated_false_energy: 5,
    evidence: [],
  }),
};

const F = sandbox.__FIELDS__;
const find = (section, key) => F[section].find((f) => f.key === key);
const rendered = {
  // A new definition renders the documented defaults, not empty boxes.
  offset: panel._field(find('protected_sensors', 'offset'), undefined),
  confirm: panel._field(find('protected_sensors', 'confirm_scans'), undefined),
  unit: panel._field(find('protected_sensors', 'unit_of_measurement'), undefined),
  mode: panel._field(find('derived_sensors', 'mode'), undefined),
  scope: panel._field(find('statistics_repair', 'scan_scope'), undefined),
  keep: panel._field(find('backups_reports', 'keep_backups'), undefined),
  price: panel._field(find('cost_repair', 'price'), undefined),
  currency: panel._field(find('cost_repair', 'currency'), undefined),
  grace: panel._field(find('protected_sensors', 'grace_period'), undefined),
  // Required identity and optional fields stay empty ...
  maxValue: panel._field(find('protected_sensors', 'max_value'), undefined),
  name: panel._field(find('protected_sensors', 'name'), undefined),
  // ... booleans follow their default, and a stored value always wins.
  enabled: panel._field(find('protected_sensors', 'enabled'), undefined),
  stored: panel._field(find('protected_sensors', 'offset'), 5),
};

const result = {
  defined: registry.has('energy-guard-config-panel'),
  escape: panel._escape('<b>&"'),
  label: panel._sectionLabel('protected_sensors', true),
  plural: panel._sectionLabel('utility_meters', false),
  items: panel._items('protected_sensors').length,
  settings: panel._settings('statistics_repair').scan_scope,
  meterSource: panel._listItem('utility_meters', panel._items('utility_meters')[0]),
  form: panel._readForm(form, 'protected_sensors'),
  rendered,
  dashboard: dashboardChecks,
};

process.stdout.write('###RESULT###' + JSON.stringify(result));
"""


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_panel_javascript_logic_runs_headlessly(tmp_path: Path) -> None:
    """The panel script loads and its pure logic behaves as documented."""
    harness = tmp_path / "harness.js"
    harness.write_text(textwrap.dedent(HARNESS))

    completed = subprocess.run(
        [NODE, str(harness), str(PANEL_JS)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "###RESULT###" in completed.stdout, completed.stderr

    payload = json.loads(completed.stdout.split("###RESULT###", 1)[1])

    assert payload["defined"] is True
    # Values from Home Assistant are escaped before they reach innerHTML.
    assert payload["escape"] == "&lt;b&gt;&amp;&quot;"
    assert payload["label"] == "protected sensor"
    assert payload["plural"] == "Utility meters"
    assert payload["items"] == 1
    assert payload["settings"] == "linked"
    # A meter row shows the meter entity (not the source sensor).
    assert "sensor.m" in payload["meterSource"]
    # Form reading: strings stay strings, numbers become numbers, checkboxes
    # become booleans and empty optional fields are omitted.
    assert payload["form"] == {
        "name": " Garage protected ",
        "source_entity_id": "sensor.garage",
        "enabled": True,
        "offset": 1.5,
    }

    # New definitions open prefilled with the documented defaults.
    rendered = payload["rendered"]
    assert 'value="0"' in rendered["offset"]
    assert 'value="2"' in rendered["confirm"]
    assert 'value="30"' in rendered["grace"]
    assert '<option value="kWh" selected>' in rendered["unit"]
    assert '<option value="sum" selected>' in rendered["mode"]
    assert '<option value="linked" selected>' in rendered["scope"]
    assert 'value="25"' in rendered["keep"]
    assert 'value="0"' in rendered["price"]
    assert 'value="GEL"' in rendered["currency"]
    # ... while identity and optional fields stay empty.
    assert 'value=""' in rendered["maxValue"]
    assert "(optional)" in rendered["maxValue"]
    assert 'value=""' in rendered["name"]
    assert "checked" in rendered["enabled"]
    # A stored value always wins over the default.
    assert 'value="5"' in rendered["stored"]

    # Dashboard formatting, bucketing and charts.
    dashboard = payload["dashboard"]
    assert dashboard["fmtInt"] == "12"
    assert dashboard["fmtFloat"] in ("45.68", "45,68")  # locale decimal
    assert dashboard["fmtBad"] == "–"  # noqa: RUF001 - testing the en dash itself
    assert dashboard["agoHour"] == "1 h ago"
    assert dashboard["agoNever"] == "never"
    assert dashboard["buckets"] == 14
    assert dashboard["bucketTotal"] == 2
    assert dashboard["bucketEnergy"] == 45.5
    assert dashboard["todayBlocked"] == 1
    assert dashboard["yesterdayBlocked"] == 1
    assert dashboard["yesterdayEnergy"] == 2
    assert "<svg" in dashboard["bars"]
    assert 'class="bar"' in dashboard["bars"]
    assert "chart-empty" in dashboard["barsEmpty"]
    assert "<svg" not in dashboard["barsEmpty"]
    assert "linearGradient" in dashboard["area"]
    assert "chart-empty" in dashboard["areaEmpty"]
    assert "stroke-dasharray" in dashboard["donut"]
    assert "False zero blocked" in dashboard["donut"]
    assert "chart-empty" in dashboard["donutEmpty"]
    assert "<polyline" in dashboard["spark"]

    # The dashboard home renders hero, KPIs, charts and the activity feed,
    # with event text escaped.
    overview = dashboard["overview"]
    for marker in (
        "hero ok",
        "kpis",
        "Blocked readings per day",
        "False energy blocked per day",
        "Logged events by kind",
        "Recent activity",
        "Quick actions",
        "Managed",
        "Held back a &lt;b&gt;0&lt;/b&gt;",
        "guarded",
    ):
        assert marker in overview, marker
    assert "Loading statistics" in dashboard["overviewLoading"]
    # html`` is String.raw, so backslash escapes stay literal inside it:
    # punctuation there must be real characters, never backslash-u sequences.
    assert "…" in dashboard["overviewLoading"]
    assert "\\u2026" not in dashboard["overviewLoading"]
    assert "·" in dashboard["candidate"]
    assert "\\u00b7" not in dashboard["candidate"]
