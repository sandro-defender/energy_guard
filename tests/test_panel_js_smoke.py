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
vm.runInContext(source, sandbox, { filename: 'energy_guard-panel.js' });

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

const result = {
  defined: registry.has('energy-guard-config-panel'),
  escape: panel._escape('<b>&"'),
  label: panel._sectionLabel('protected_sensors', true),
  plural: panel._sectionLabel('utility_meters', false),
  items: panel._items('protected_sensors').length,
  settings: panel._settings('statistics_repair').scan_scope,
  meterSource: panel._listItem('utility_meters', panel._items('utility_meters')[0]),
  form: panel._readForm(form, 'protected_sensors'),
  fields: Object.keys(panel.constructor),
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
