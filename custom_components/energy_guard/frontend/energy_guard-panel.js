/*
 * Energy Guard configuration panel.
 *
 * A plain web component (no build step, no external resources) that renders the
 * whole Energy Guard configuration and talks to the integration's admin-only
 * WebSocket API (`energy_guard/config/*`).
 *
 * Safety rules this file follows - keep them:
 *   - it only ever calls the read-only `energy_guard.scan_statistics` on a
 *     single click; repairs always show a PREVIEW first and need a second,
 *     explicit confirmation click (plus the typed "confirm" checkbox for
 *     money);
 *   - it never sends `confirm: true` on its own;
 *   - it never asks for, shows or stores a token, a password or a database
 *     path - the WebSocket connection is authenticated by Home Assistant;
 *   - every write is validated server-side by the same code the options flow
 *     uses, so a bug here cannot store invalid configuration.
 */

const SCAN_SCOPE_LABELS = {
  linked: "My Energy Guard sensors",
  energy: "All energy statistics",
  all: "All statistics (every cumulative sensor)",
};

const SECTIONS = [
  ["overview", "Overview", "mdi:view-dashboard"],
  ["protected_sensors", "Protected", "mdi:shield-check"],
  ["derived_sensors", "Derived", "mdi:calculator"],
  ["utility_meters", "Meters", "mdi:gauge"],
  ["detection_rules", "Detection", "mdi:radar"],
  ["statistics_repair", "Statistics", "mdi:chart-bar"],
  ["cost_repair", "Cost", "mdi:cash"],
  ["backups_reports", "Backups", "mdi:database"],
  ["review", "Repair", "mdi:auto-fix"],
  ["templates", "Templates", "mdi:file-code"],
];

const REPO_URL = "https://github.com/sandro-defender/energy_guard";

const UNITS = ["kWh", "Wh", "MWh", "GWh", "MJ", "GJ"];

/* Field descriptors: one source of truth for every form.
 *
 * Every field carries a `hint` (shown under the input) and every field that
 * has a documented default carries that same `default`, so a new definition
 * opens prefilled and no option is a mystery.  The defaults mirror the
 * DEFAULT_* constants in const.py - tests/test_panel.py fails if they drift.
 */
const FIELDS = {
  protected_sensors: [
    { key: "name", label: "Name", type: "text", required: true, hint: "Becomes the entity id. Keep the ' protected' suffix." },
    { key: "source_entity_id", label: "Source sensor", type: "energy_entity", required: true, hint: "The cumulative sensor to copy (device_class energy, total_increasing)." },
    { key: "enabled", label: "Enabled", type: "boolean", default: true, hint: "Disabled sensors are not created; the definition is kept." },
    { key: "unit_of_measurement", label: "Unit", type: "select", options: UNITS, default: "kWh", hint: "Unit of the copy. Values convert automatically." },
    { key: "offset", label: "Offset", type: "number", step: "any", default: 0, hint: "Added to every reading (kWh). Continues an old total after a meter swap." },
    { key: "zero_min_previous", label: "Suspicious zero threshold", type: "number", step: "any", default: 1, hint: "A 0 only looks suspicious when the last good value reached this (kWh)." },
    { key: "confirm_scans", label: "Readings needed to confirm a reset", type: "number", step: "1", default: 2, hint: "Consecutive readings that must agree before a reset is accepted." },
    { key: "recovery_hold_scans", label: "Recovery hold", type: "number", step: "1", default: 1, hint: "Sane readings required after a reconnect before publishing resumes." },
    { key: "grace_period", label: "Grace period (seconds)", type: "number", step: "1", default: 30, hint: "Gaps shorter than this are not treated as a reconnect." },
    { key: "do_not_decrease", label: "Never decrease", type: "boolean", default: true, hint: "Hold back decreases as unavailable instead of accepting them." },
    { key: "accept_real_reset", label: "Accept real meter resets", type: "boolean", default: false, hint: "Only for meters that can legitimately jump back to 0." },
    { key: "reject_large_jumps", label: "Reject large jumps", type: "boolean", default: false, hint: "Hold back suspicious jumps instead of publishing them." },
    { key: "large_jump", label: "Large jump threshold", type: "number", step: "any", default: 100, hint: "A single reading jump above this (kWh) is suspicious." },
    { key: "large_jump_ratio", label: "Large jump ratio", type: "number", step: "any", default: 20, hint: "Also suspicious when this many times larger than a typical increase." },
    { key: "max_value", label: "Maximum plausible value", type: "number", step: "any", optional: true, hint: "Readings above this are held back. Empty means no limit." },
    { key: "precision", label: "Precision", type: "number", step: "1", default: 3, hint: "Decimals shown. Statistics keep full precision." },
  ],
  derived_sensors: [
    { key: "name", label: "Name", type: "text", required: true, hint: "Becomes the entity id." },
    { key: "mode", label: "Mode", type: "select", options: ["sum", "difference", "phase_split"], default: "sum", hint: "Sum adds all sensors; difference subtracts all but the first; phase split is total minus phases." },
    { key: "source_entity_ids", label: "Sensors to combine", type: "entity_list", required: true, hint: "Sum and difference need at least two sensors." },
    { key: "total_entity_id", label: "Total sensor (phase split)", type: "entity", optional: true, hint: "Only for phase split: the whole-house meter." },
    { key: "part_entity_ids", label: "Phase sensors (phase split)", type: "entity_list", optional: true, hint: "Only for phase split: subtracted from the total." },
    { key: "enabled", label: "Enabled", type: "boolean", default: true, hint: "Disabled sensors are not created; the definition is kept." },
    { key: "unit_of_measurement", label: "Unit", type: "select", options: UNITS, default: "kWh", hint: "Unit of the result. Sources convert automatically." },
    { key: "scale", label: "Multiplier", type: "number", step: "any", default: 1, hint: "The combined value is multiplied by this first." },
    { key: "offset", label: "Offset", type: "number", step: "any", default: 0, hint: "Constant added after the multiplier (kWh)." },
    { key: "require_all_sources", label: "Require all sources to be available", type: "boolean", default: true, hint: "Recommended: a partial sum looks like a drop." },
    { key: "do_not_decrease", label: "Never decrease", type: "boolean", default: true, hint: "Hold back decreases as unavailable instead of accepting them." },
  ],
  utility_meters: [
    { key: "name", label: "Name", type: "text", required: true, hint: "Only used inside Energy Guard." },
    { key: "utility_meter_entity_id", label: "Utility meter entity", type: "entity", required: true, hint: "The utility_meter sensor to calibrate. Never created or deleted by us." },
    { key: "source_entity_id", label: "Source sensor", type: "energy_entity", optional: true, hint: "Protected sensor the calibration is computed from (source minus baseline)." },
    { key: "cycle", label: "Cycle (monthly, daily, ...)", type: "text", optional: true, hint: "Informational only; the cycle lives in the utility_meter integration." },
    { key: "baseline_at", label: "Baseline at", type: "text", optional: true, hint: "ISO date/time: read the baseline from history instead of the number below." },
    { key: "baseline_value", label: "Baseline value", type: "number", step: "any", optional: true, hint: "Source value at the cycle start." },
    { key: "enabled", label: "Enabled", type: "boolean", default: true, hint: "Disabled entries are skipped by the calibration service." },
  ],
  detection_rules: [
    { key: "scan_interval", label: "Scan interval (seconds)", type: "number", step: "1", default: 300, hint: "How often the read-only scan checks the recorder." },
    { key: "lookback_hours", label: "Statistics lookback (hours)", type: "number", step: "1", default: 24, hint: "How far back each scan looks." },
    { key: "issue_window_hours", label: "Issue window (hours)", type: "number", step: "1", default: 24, hint: "Only anomalies inside this window raise issues." },
    { key: "simultaneous_threshold", label: "Simultaneous failures to report", type: "number", step: "1", default: 2, hint: "Sensors that must fail together before it is reported." },
    { key: "simultaneous_window_minutes", label: "Simultaneous window (minutes)", type: "number", step: "1", default: 5, hint: "Time window for 'failed at the same moment'." },
    { key: "event_retention_days", label: "Keep the diagnostic log for (days)", type: "number", step: "1", default: 14, hint: "Older diagnostic events are pruned." },
  ],
  statistics_repair: [
    { key: "scan_scope", label: "Default scan scope", type: "scan_scope", default: "linked", hint: "Linked is fast (your sensors only); energy/all also cover other statistics. Always read-only." },
    { key: "statistics_jump_threshold", label: "Suspicious jump threshold", type: "number", step: "any", default: 100, hint: "An hourly increase above this (kWh) is reported." },
    { key: "statistics_jump_ratio", label: "Suspicious jump ratio", type: "number", step: "any", default: 20, hint: "Also reported when this many times the typical hourly increase." },
    { key: "sum_state_ratio", label: "Sum/state ratio", type: "number", step: "any", default: 10, hint: "Reported when the recorded sum is this many times larger than the states say." },
  ],
  cost_repair: [
    { key: "enabled", label: "Mirror kWh repairs into the cost statistic", type: "boolean", default: false, hint: "When off, no monetary math and no cost suggestions." },
    { key: "price", label: "Price per kWh", type: "number", step: "any", default: 0, hint: "Fixed tariff, e.g. 0.235 for 0.235 GEL per kWh." },
    { key: "currency", label: "Currency", type: "text", default: "GEL", hint: "Currency code, e.g. GEL, USD, EUR. Display only." },
    { key: "energy_statistic_id", label: "Energy statistic id", type: "text", optional: true, hint: "Statistic with the false kWh offset, e.g. sensor.grid_import." },
    { key: "cost_statistic_id", label: "Cost statistic id", type: "text", optional: true, hint: "Energy Dashboard cost statistic, e.g. sensor.grid_import_cost." },
    { key: "price_entity_id", label: "Live price entity (optional)", type: "entity", optional: true, hint: "When set, used instead of the fixed tariff above." },
  ],
  backups_reports: [
    { key: "create_backup", label: "Create a backup before every change", type: "boolean", default: true, hint: "Strongly recommended: without a backup nothing can be undone from a file." },
    { key: "keep_backups", label: "Backups to keep", type: "number", step: "1", default: 25, hint: "Older backups beyond this number are pruned." },
  ],
};

const CARD = `
  background: var(--card-background-color, #fff);
  border-radius: var(--ha-card-border-radius, 12px);
  box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.12));
  padding: 16px; margin: 0 0 16px 0;
`;

const html = String.raw;

class EnergyGuardConfigPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._config = null;
    this._choices = null;
    this._review = null;
    this._files = null;
    this._templates = null;
    this._dashboard = null;
    this._tab = "overview";
    this._error = null;
    this._message = null;
    this._editing = null; // {section, id}
    this._draft = {};
    this._previews = {}; // statistic_id -> preview result
    this._loaded = false;
    this._subscribed = false;
    this._gid = 0;
    this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    const first = this._hass === null;
    this._hass = hass;
    if (first || !this._loaded) {
      this._loaded = true;
      this._init();
    }
  }

  get hass() {
    return this._hass;
  }

  /* ------------------------------------------------------------------ */
  /* data                                                                */
  /* ------------------------------------------------------------------ */
  async _init() {
    try {
      await this._loadConfig();
      await this._loadChoices();
      await this._loadReview();
      await this._loadDashboard();
      this._render();
      this._subscribe();
    } catch (err) {
      this._fail(err);
    }
  }

  async _ws(msg) {
    if (!this._hass) throw new Error("Home Assistant is not ready yet");
    return this._hass.callWS(msg);
  }

  async _loadConfig() {
    const result = await this._ws({ type: "energy_guard/config/get" });
    this._config = result.config;
    this._meta = result;
  }

  async _loadChoices() {
    this._choices = await this._ws({ type: "energy_guard/config/choices" });
  }

  async _loadReview() {
    this._review = await this._ws({ type: "energy_guard/config/review" });
  }

  async _loadDashboard() {
    const result = await this._ws({ type: "energy_guard/config/dashboard" });
    this._dashboard = result.dashboard;
  }

  async _loadFiles() {
    this._files = await this._ws({ type: "energy_guard/config/files" });
  }

  async _loadTemplates() {
    this._templates = await this._ws({ type: "energy_guard/config/templates" });
  }

  async _subscribe() {
    if (this._subscribed) return;
    this._subscribed = true;
    try {
      await this._hass.connection.subscribeMessage(
        (event) => {
          this._config = event.config;
          if (this._tab !== "templates") this._render();
        },
        { type: "energy_guard/config/subscribe" }
      );
    } catch (err) {
      this._subscribed = false; // a reconnect re-subscribes
    }
  }

  /* ------------------------------------------------------------------ */
  /* helpers                                                             */
  /* ------------------------------------------------------------------ */
  _fail(err) {
    this._error =
      (err && (err.message || err.error || err.code)) || String(err) || "Unknown error";
    this._render();
  }

  _ok(message) {
    this._error = null;
    this._message = message || null;
    this._render();
  }

  _escape(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _label(entityId) {
    const state = this._hass.states[entityId];
    if (state && state.attributes.friendly_name) return state.attributes.friendly_name;
    return entityId;
  }

  _settings(section) {
    const key =
      section === "statistics_repair" ? "detection_rules" : section;
    return (this._config && this._config[key]) || {};
  }

  _items(section) {
    return (this._config && this._config[section]) || [];
  }

  _entityOptions(kind) {
    const choices = this._choices || {};
    if (kind === "energy_entity") return choices.energy_sources || [];
    return choices.entities || [];
  }

  /* ------------------------------------------------------------------ */
  /* rendering                                                           */
  /* ------------------------------------------------------------------ */
  _render() {
    if (!this.shadowRoot) return;
    const tabs = SECTIONS.map(
      ([id, label, icon]) =>
        `<button class="tab ${this._tab === id ? "active" : ""}" data-tab="${id}" title="${label}"><ha-icon icon="${icon}"></ha-icon><span>${label}</span></button>`
    ).join("");

    let body = "";
    if (this._config) {
      switch (this._tab) {
        case "overview":
          body = this._overview();
          break;
        case "review":
          body = this._reviewTab();
          break;
        case "templates":
          body = this._templatesTab();
          break;
        case "backups_reports":
          body = this._backupsTab();
          break;
        default:
          body = this._sectionTab(this._tab);
      }
    }

    this.shadowRoot.innerHTML = html`
      <style>
        :host { display: block; padding: 16px; color: var(--primary-text-color); }
        .head { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 12px; }
        .head h1 { font-size: 1.4rem; margin: 0; font-weight: 500; display: flex; align-items: center; gap: 8px; }
        .head ha-icon { color: var(--primary-color); }
        .badge { font-size: 0.75rem; color: var(--secondary-text-color); }
        .tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; position: sticky; top: 0; z-index: 5;
                padding: 8px 0; background: var(--primary-background-color); }
        .tab { border: 1px solid var(--divider-color); background: var(--card-background-color, #fff); color: inherit;
               border-radius: 16px; padding: 6px 14px 6px 10px; cursor: pointer; font-size: 0.85rem;
               display: inline-flex; align-items: center; gap: 6px; }
        .tab ha-icon { --mdc-icon-size: 18px; }
        .tab:not(.active):hover { border-color: var(--primary-color); color: var(--primary-color); }
        .tab.active { background: var(--primary-color); border-color: var(--primary-color);
                      color: var(--text-primary-color, #fff); }
        .card { ${CARD} }
        .card h2 { margin: 0 0 8px 0; font-size: 1.05rem; font-weight: 500; display: flex; align-items: center; gap: 8px; }
        .card h2 ha-icon { color: var(--primary-color); --mdc-icon-size: 20px; }
        .row { display: flex; flex-wrap: wrap; gap: 12px; }
        .row > * { flex: 1 1 220px; }
        label.field { display: flex; flex-direction: column; gap: 4px; margin: 0 0 12px 0;
                      font-size: 0.85rem; color: var(--secondary-text-color); }
        input, select, textarea { font: inherit; color: var(--primary-text-color);
          background: var(--secondary-background-color, rgba(0,0,0,.03));
          border: 1px solid var(--divider-color); border-radius: 6px; padding: 8px; }
        input[type="checkbox"] { width: auto; }
        textarea { min-height: 220px; font-family: var(--code-font-family, monospace); width: 100%; }
        .hint { color: var(--secondary-text-color); font-size: 0.75rem; }
        button.action { font: inherit; cursor: pointer; border-radius: 6px; padding: 8px 14px;
          border: 1px solid var(--primary-color); background: var(--primary-color);
          color: var(--text-primary-color, #fff); }
        button.action.secondary { background: none; color: var(--primary-color); }
        button.action.danger { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
        button.action:disabled { opacity: .5; cursor: not-allowed; }
        button.action { transition: filter .15s ease; }
        button.action:hover:not(:disabled) { filter: brightness(1.08); }
        input:focus, select:focus, textarea:focus { outline: none; border-color: var(--primary-color);
          box-shadow: 0 0 0 1px var(--primary-color); }
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--divider-color); }
        th { color: var(--secondary-text-color); font-weight: 500; }
        .list-item { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
                     padding: 10px 0; border-bottom: 1px solid var(--divider-color); }
        .list-item .grow { flex: 1 1 240px; }
        .pill { font-size: 0.75rem; border-radius: 10px; padding: 2px 8px;
                background: var(--secondary-background-color, rgba(0,0,0,.06)); }
        .pill.off { color: var(--secondary-text-color); }
        .error { background: var(--error-color, #db4437); color: #fff; padding: 10px 12px;
                 border-radius: 6px; margin-bottom: 12px; }
        .success { background: var(--success-color, #43a047); color: #fff; padding: 10px 12px;
                   border-radius: 6px; margin-bottom: 12px; }
        .note { color: var(--secondary-text-color); font-size: 0.8rem; margin-top: 8px; }
        .grid-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
        .hero { border-radius: 16px; padding: 20px; margin-bottom: 16px; display: flex;
                gap: 16px; align-items: center; color: #fff; }
        .hero.ok { background: linear-gradient(135deg, #1b5e20 0%, #00897b 100%); }
        .hero.bad { background: linear-gradient(135deg, #b71c1c 0%, #e65100 100%); }
        .hero ha-icon { --mdc-icon-size: 48px; flex-shrink: 0; }
        .hero-title { font-size: 1.3rem; font-weight: 500; }
        .hero-sub { opacity: .92; font-size: .85rem; margin-top: 4px; }
        .hero-chips { margin-left: auto; display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
        .chip { background: rgba(255,255,255,.18); border-radius: 12px; padding: 3px 10px;
                font-size: .75rem; white-space: nowrap; }
        .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin-bottom: 16px; }
        .kpi { background: var(--card-background-color, #fff); border-radius: var(--ha-card-border-radius, 12px);
               box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.12));
               padding: 14px; display: flex; gap: 12px; align-items: center; }
        .kpi .bubble { width: 44px; height: 44px; border-radius: 12px; display: flex;
                       align-items: center; justify-content: center; flex-shrink: 0; }
        .kpi .bubble ha-icon { --mdc-icon-size: 24px; }
        .bubble.blue { background: rgba(3,169,244,.15); color: #03a9f4; }
        .bubble.green { background: rgba(67,160,71,.15); color: #43a047; }
        .bubble.amber { background: rgba(255,160,0,.18); color: #ef9a00; }
        .bubble.red { background: rgba(219,68,55,.15); color: #db4437; }
        .kpi .value { font-size: 1.5rem; font-weight: 500; line-height: 1.1; }
        .kpi .value small { font-size: .85rem; font-weight: 400; color: var(--secondary-text-color); }
        .kpi .label { font-size: .8rem; color: var(--secondary-text-color); }
        .kpi .sub { font-size: .75rem; color: var(--secondary-text-color); }
        .kpi .spark-wrap { margin-left: auto; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
        @media (max-width: 900px) { .grid-2 { grid-template-columns: 1fr; } .hero-chips { display: none; } }
        .chart { width: 100%; height: auto; display: block; }
        .chart text { fill: var(--secondary-text-color); font-size: 11px; }
        .chart .grid { stroke: var(--divider-color); stroke-width: 1; }
        .chart .bar { fill: var(--primary-color, #03a9f4); }
        .chart .line { fill: none; stroke: var(--success-color, #43a047); stroke-width: 2.5; }
        .chart .dot { fill: var(--success-color, #43a047); }
        .area-stop-top { stop-color: var(--success-color, #43a047); stop-opacity: .45; }
        .area-stop-bottom { stop-color: var(--success-color, #43a047); stop-opacity: .05; }
        .chart-empty { display: flex; gap: 8px; align-items: center; color: var(--secondary-text-color);
                       font-size: .85rem; padding: 24px 8px; }
        .spark { width: 96px; height: 30px; }
        .spark polyline { fill: none; stroke: var(--primary-color, #03a9f4); stroke-width: 2; }
        .donut-wrap { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
        .donut { width: 150px; height: 150px; flex-shrink: 0; }
        .donut-total { font-size: 26px; font-weight: 500; fill: var(--primary-text-color); }
        .donut-sub { font-size: 12px; fill: var(--secondary-text-color); }
        .legend { display: flex; flex-direction: column; gap: 6px; font-size: .8rem; }
        .legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
        .feed { list-style: none; margin: 0; padding: 0; }
        .feed li { display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); font-size: .85rem; }
        .feed li:last-child { border-bottom: none; }
        .feed .sev { width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; flex-shrink: 0; }
        .sev.error { background: var(--error-color, #db4437); }
        .sev.warning { background: var(--warning-color, #ffa000); }
        .sev.info { background: var(--primary-color, #03a9f4); }
        .feed .when { margin-left: auto; color: var(--secondary-text-color); font-size: .75rem; white-space: nowrap; padding-left: 8px; }
        .feed .msg { color: var(--secondary-text-color); font-size: .8rem; }
        .kv { display: flex; justify-content: space-between; gap: 8px; padding: 7px 0;
              border-bottom: 1px solid var(--divider-color); font-size: .85rem; }
        .kv:last-child { border-bottom: none; }
        .kv b { font-weight: 500; }
        .candidate { border: 1px solid var(--divider-color); border-radius: 12px; padding: 12px; margin-bottom: 12px; }
        .candidate .top { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
        .candidate .offset { font-size: 1.2rem; font-weight: 500; margin-left: auto; }
        .candidate .meta { color: var(--secondary-text-color); font-size: .8rem; margin: 4px 0 8px 0; }
        .candidate .actions { display: flex; gap: 8px; margin-top: 4px; }
        .evidence { display: inline-block; background: var(--secondary-background-color, rgba(0,0,0,.06));
                    border-radius: 10px; padding: 2px 8px; font-size: .75rem; margin: 0 6px 6px 0; }
        .section-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .section-head h2 { margin: 0; }
        .section-head ha-icon { color: var(--primary-color); }
        .count-pill { margin-left: auto; font-size: .75rem; border-radius: 10px; padding: 2px 10px;
                      background: var(--secondary-background-color, rgba(0,0,0,.06));
                      color: var(--secondary-text-color); }
        .list-card { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px;
                     border: 1px solid var(--divider-color); border-radius: 12px; margin-bottom: 8px; }
        .list-card .grow { flex: 1 1 240px; }
        .list-card .avatar { width: 36px; height: 36px; border-radius: 10px; flex-shrink: 0;
                             background: var(--secondary-background-color, rgba(0,0,0,.06));
                             display: flex; align-items: center; justify-content: center;
                             color: var(--primary-color); }
        .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
        @media (max-width: 700px) { .form-grid { grid-template-columns: 1fr; } }
        .footer { color: var(--secondary-text-color); font-size: .75rem; text-align: center; margin: 8px 0 24px 0; }
        .footer a { color: var(--primary-color); }
      </style>
      <div class="head">
        <h1><ha-icon icon="mdi:shield-check"></ha-icon>Energy Guard</h1>
        <span class="badge">v${this._escape((this._meta && this._meta.version) || "")} ·
          dashboard &amp; configuration</span>
      </div>
      ${this._error ? `<div class="error">${this._escape(this._error)}</div>` : ""}
      ${this._message ? `<div class="success">${this._escape(this._message)}</div>` : ""}
      <div class="tabs">${tabs}</div>
      <div id="body">${body}</div>
      <div class="footer">Energy Guard · admin-only panel ·
        <a href="${REPO_URL}" target="_blank" rel="noreferrer">documentation &amp; issues</a></div>
    `;
    this._attach();
  }

  _overview() {
    const dash = this._dashboard;
    if (!dash) {
      return html`
        <div class="card">
          <h2><ha-icon icon="mdi:view-dashboard"></ha-icon>Dashboard</h2>
          <p class="note">Loading statistics…</p>
        </div>
      `;
    }
    const protection = dash.protection || {};
    const scan = dash.scan || {};
    const counts = dash.counts || {};
    const actions = dash.actions || {};
    const state = this._hass.states["binary_sensor.energy_guard_data_issue"];
    const open = state && state.state === "on";
    const candidates = scan.candidates || 0;
    const issues = open || candidates > 0 || !!scan.error;
    const daily = this._daily(dash.points || [], dash.window_days || 14);
    const retention = this._settings("detection_rules").event_retention_days || 14;

    const lastScan = scan.last_scan
      ? `${this._ago(scan.last_scan)}${scan.scope ? ` \u00b7 scope ${this._escape(scan.scope)} \u00b7 ${this._fmt(scan.statistic_count)} statistics` : ""}`
      : "never";
    const heroIcon = issues ? "mdi:shield-alert" : "mdi:shield-check";
    const heroTitle = issues ? "Needs your review" : "You are protected";
    const heroSub = issues
      ? `${open ? "The issue sensor is on. " : ""}${candidates ? `${candidates} suspicious statistics offset${candidates === 1 ? "" : "s"}. ` : ""}${scan.error ? `Last scan failed: ${this._escape(scan.error)}` : "Open the Repair tab to review."}`
      : "Protected sensors are publishing clean values. Nothing needs you right now.";
    const guarded = (counts.protected_enabled || 0) + (counts.derived_enabled || 0);

    return html`
      <div class="hero ${issues ? "bad" : "ok"}">
        <ha-icon icon="${heroIcon}"></ha-icon>
        <div class="hero-text">
          <div class="hero-title">${heroTitle}</div>
          <div class="hero-sub">${heroSub}</div>
        </div>
        <div class="hero-chips">
          <span class="chip">last scan: ${this._escape(lastScan)}</span>
          <span class="chip">${this._fmt(guarded)} sensor${guarded === 1 ? "" : "s"} guarded</span>
        </div>
      </div>
      <div class="kpis">
        ${this._kpi("mdi:shield-check", "green", this._escape(this._fmt(protection.blocked_total)), "readings blocked", `+${this._escape(this._fmt(protection.blocked_24h))} in 24 h`, daily.map((bucket) => bucket.blocked))}
        ${this._kpi("mdi:lightning-bolt", "amber", `${this._escape(this._fmt(protection.false_kwh_total))} <small>kWh</small>`, "false energy blocked", `+${this._escape(this._fmt(protection.false_kwh_24h))} kWh in 24 h`, daily.map((bucket) => bucket.energy))}
        ${this._kpi("mdi:database-alert-outline", candidates ? "red" : "blue", this._escape(this._fmt(candidates)), "suspicious offsets", "open right now", null)}
        ${this._kpi("mdi:clock-check-outline", "blue", this._escape(scan.last_scan ? this._ago(scan.last_scan) : "never"), "last scan", this._escape(scan.scope ? `scope: ${scan.scope}` : "no scan yet"), null)}
      </div>
      <div class="grid-2">
        <div class="card">
          <h2><ha-icon icon="mdi:chart-bar"></ha-icon>Blocked readings per day</h2>
          ${this._barsChart(daily)}
          <p class="note">Readings held back before they could corrupt the statistics.</p>
        </div>
        <div class="card">
          <h2><ha-icon icon="mdi:chart-line"></ha-icon>False energy blocked per day</h2>
          ${this._areaChart(daily, "kWh")}
          <p class="note">Energy that would have been added wrongly without protection.</p>
        </div>
      </div>
      <div class="grid-2">
        <div class="card">
          <h2><ha-icon icon="mdi:chart-donut"></ha-icon>Logged events by kind</h2>
          ${this._donutChart(dash.by_kind || [])}
        </div>
        <div class="card">
          <h2><ha-icon icon="mdi:timeline-text-outline"></ha-icon>Recent activity</h2>
          ${this._activityFeed(dash.recent || [])}
        </div>
      </div>
      <div class="grid-2">
        <div class="card">
          <h2><ha-icon icon="mdi:lightning-bolt"></ha-icon>Quick actions</h2>
          <p class="note">Scans are read-only. Nothing is repaired by a scan.</p>
          <div class="grid-buttons">
            ${["linked", "energy", "all"].map((scope) => `<button class="action secondary" data-scan="${scope}">${this._escape(SCAN_SCOPE_LABELS[scope])}</button>`).join("")}
          </div>
          <div class="grid-buttons" style="margin-top:8px">
            <button class="action secondary" data-goto="review">Open the repair workflow</button>
            <button class="action secondary" data-goto="protected_sensors">Add a protected sensor</button>
          </div>
        </div>
        <div class="card">
          <h2><ha-icon icon="mdi:gauge"></ha-icon>Managed</h2>
          <div class="kv"><span>Protected sensors</span><b>${this._escape(this._fmt(counts.protected_enabled))} of ${this._escape(this._fmt(counts.protected))}</b></div>
          <div class="kv"><span>Derived sensors</span><b>${this._escape(this._fmt(counts.derived_enabled))} of ${this._escape(this._fmt(counts.derived))}</b></div>
          <div class="kv"><span>Utility meters</span><b>${this._escape(this._fmt(counts.meters_enabled))} of ${this._escape(this._fmt(counts.meters))}</b></div>
          <div class="kv"><span>Repairs applied</span><b>${this._escape(this._fmt(actions.repairs))}</b></div>
          <div class="kv"><span>Calibrations</span><b>${this._escape(this._fmt(actions.calibrations))}</b></div>
        </div>
      </div>
      <p class="note">Totals cover the diagnostic log (kept ${this._escape(this._fmt(retention))} days, newest 250 events). Charts show your local days.</p>
    `;
  }

  _activityFeed(recent) {
    if (!recent.length) {
      return `<p class="note">No events logged yet. Protection starts working as soon as sources report.</p>`;
    }
    const items = recent.map((event) => {
      const severity = event.severity === "error" ? "error" : event.severity === "warning" ? "warning" : "info";
      return `<li><span class="sev ${severity}"></span><div><div><strong>${this._escape(event.label || event.kind)}</strong></div>` +
        `<div class="msg">${this._escape(event.message || "")}</div></div>` +
        `<span class="when">${this._escape(this._ago(event.t))}</span></li>`;
    }).join("");
    return `<ul class="feed">${items}</ul>`;
  }

  _sectionTab(section) {
    const fields = FIELDS[section] || [];
    const isList = ["protected_sensors", "derived_sensors", "utility_meters"].includes(
      section
    );
    const icon = this._sectionIcon(section);
    if (!isList) {
      const values = this._settings(section);
      return html`
        <div class="card">
          <div class="section-head"><ha-icon icon="${icon}"></ha-icon><h2>${this._escape(this._sectionLabel(section))}</h2></div>
          <form data-section-form="${section}">
            <div class="form-grid">${fields.map((field) => this._field(field, values[field.key])).join("")}</div>
            <button class="action" type="submit">Save</button>
          </form>
          <p class="note">Saving reloads the integration. Nothing is repaired or cleared by this form.</p>
        </div>
      `;
    }
    const items = this._items(section);
    const editing = this._editing && this._editing.section === section;
    return html`
      <div class="card">
        <div class="section-head"><ha-icon icon="${icon}"></ha-icon><h2>${this._escape(this._sectionLabel(section))}</h2><span class="count-pill">${items.length}</span></div>
        ${
          items.length === 0
            ? `<p class="note">Nothing configured yet.</p>`
            : items
                .map((item) => this._listItem(section, item))
                .join("")
        }
      </div>
      ${
        editing
          ? this._definitionForm(section)
          : `<div class="card"><button class="action" data-add="${section}">Add ${this._escape(this._sectionLabel(section, true))}</button></div>`
      }
    `;
  }

  _sectionLabel(section, singular) {
    const labels = {
      protected_sensors: singular ? "protected sensor" : "Protected sensors",
      derived_sensors: singular ? "derived sensor" : "Derived sensors",
      utility_meters: singular ? "utility meter" : "Utility meters",
      detection_rules: "Detection rules",
      statistics_repair: "Statistics scanner",
      cost_repair: "Cost (monetary) repair",
      backups_reports: "Backups and reports",
    };
    return labels[section] || section;
  }

  _listItem(section, item) {
    const enabled = item.enabled !== false;
    const source =
      item.source_entity_id ||
      (item.source_entity_ids || []).join(", ") ||
      item.utility_meter_entity_id ||
      "";
    return html`
      <div class="list-card">
        <div class="avatar"><ha-icon icon="${this._sectionIcon(section)}"></ha-icon></div>
        <div class="grow">
          <strong>${this._escape(item.name || item.id)}</strong><br />
          <span class="hint">${this._escape(source)}</span>
        </div>
        <span class="pill ${enabled ? "" : "off"}">${enabled ? "enabled" : "disabled"}</span>
        <button class="action secondary" data-edit="${section}" data-id="${this._escape(item.id)}">Edit</button>
        <button class="action secondary" data-toggle="${section}" data-id="${this._escape(item.id)}">
          ${enabled ? "Disable" : "Enable"}
        </button>
        <button class="action danger" data-delete="${section}" data-id="${this._escape(item.id)}">Delete</button>
      </div>
    `;
  }

  _definitionForm(section) {
    const fields = FIELDS[section] || [];
    const item = this._editing.id
      ? this._items(section).find((entry) => entry.id === this._editing.id) || {}
      : {};
    const title = this._editing.id ? `Edit ${item.name || item.id}` : `Add ${this._sectionLabel(section, true)}`;
    return html`
      <div class="card">
        <h2><ha-icon icon="${this._sectionIcon(section)}"></ha-icon>${this._escape(title)}</h2>
        <form data-definition-form="${section}" data-id="${this._escape(this._editing.id || "")}">
          <div class="form-grid">${fields.map((field) => this._field(field, item[field.key])).join("")}</div>
          <button class="action" type="submit">${this._editing.id ? "Save changes" : "Add"}</button>
          <button class="action secondary" type="button" data-cancel="1">Cancel</button>
        </form>
        <p class="note">
          The name becomes the entity id. A name whose entity id is already taken is refused,
          because Home Assistant would otherwise silently create <code>..._2</code>.
        </p>
      </div>
    `;
  }

  _field(field, value) {
    const name = field.key;
    // A new definition (or a setting never saved) renders the documented
    // default instead of an empty box, so the form is prefilled.
    const effective = value === undefined || value === null ? field.default : value;
    const raw = effective === undefined || effective === null ? "" : effective;
    const hint = field.hint ? `<span class="hint">${this._escape(field.hint)}</span>` : "";
    if (field.type === "boolean") {
      const checked = value === undefined ? !!field.default : !!value;
      return html`<label class="field" style="flex-direction:row;align-items:center;gap:8px">
        <input type="checkbox" name="${name}" ${checked ? "checked" : ""} />
        <span>${this._escape(field.label)}</span>${hint}
      </label>`;
    }
    if (field.type === "select") {
      return html`<label class="field">${this._escape(field.label)}
        <select name="${name}">
          ${(field.options || [])
            .map(
              (option) =>
                `<option value="${this._escape(option)}" ${String(raw) === String(option) ? "selected" : ""}>${this._escape(option)}</option>`
            )
            .join("")}
        </select>${hint}</label>`;
    }
    if (field.type === "scan_scope") {
      return html`<label class="field">${this._escape(field.label)}
        <select name="${name}">
          ${["linked", "energy", "all"]
            .map(
              (option) =>
                `<option value="${option}" ${String(raw || "linked") === option ? "selected" : ""}>${this._escape(SCAN_SCOPE_LABELS[option])}</option>`
            )
            .join("")}
        </select>${hint}</label>`;
    }
    if (field.type === "entity" || field.type === "energy_entity") {
      const options = this._entityOptions(field.type);
      return html`<label class="field">${this._escape(field.label)}
        <select name="${name}">
          <option value="">${field.optional ? "(none)" : "(pick a sensor)"}</option>
          ${options
            .map(
              (option) =>
                `<option value="${this._escape(option.entity_id)}" ${String(raw) === option.entity_id ? "selected" : ""}>${this._escape(option.name || option.entity_id)} (${this._escape(option.entity_id)})</option>`
            )
            .join("")}
        </select>${hint}</label>`;
    }
    if (field.type === "entity_list") {
      const selected = Array.isArray(value) ? value : [];
      const options = this._entityOptions("energy_entity");
      return html`<label class="field">${this._escape(field.label)}
        <select name="${name}" multiple size="6">
          ${options
            .map(
              (option) =>
                `<option value="${this._escape(option.entity_id)}" ${selected.includes(option.entity_id) ? "selected" : ""}>${this._escape(option.name || option.entity_id)} (${this._escape(option.entity_id)})</option>`
            )
            .join("")}
        </select>${hint}</label>`;
    }
    const type = field.type === "number" ? "number" : "text";
    const step = field.step ? ` step="${this._escape(field.step)}"` : "";
    return html`<label class="field">${this._escape(field.label)}
      <input type="${type}" name="${name}" value="${this._escape(raw)}"${step}
        placeholder="${field.optional ? "(optional)" : ""}" />
      ${hint}</label>`;
  }

  _sectionIcon(section) {
    const found = SECTIONS.find(([id]) => id === section);
    return (found && found[2]) || "mdi:cog";
  }

  _fmt(value, digits) {
    const num = Number(value);
    if (!isFinite(num)) return "\u2013";
    const places = digits === undefined ? (Number.isInteger(num) ? 0 : 2) : digits;
    return num.toLocaleString(undefined, { maximumFractionDigits: places });
  }

  _ago(iso) {
    if (!iso) return "never";
    const ms = Date.now() - new Date(iso).getTime();
    if (isNaN(ms) || ms < 0) return "just now";
    const minutes = Math.floor(ms / 60000);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} h ago`;
    const days = Math.floor(hours / 24);
    if (days < 14) return `${days} d ago`;
    return new Date(iso).toLocaleDateString();
  }

  _daily(points, days) {
    const buckets = [];
    const now = new Date();
    for (let i = days - 1; i >= 0; i--) {
      const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
      buckets.push({
        key: `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`,
        date,
        blocked: 0,
        energy: 0,
      });
    }
    const byKey = {};
    buckets.forEach((bucket) => { byKey[bucket.key] = bucket; });
    (points || []).forEach((point) => {
      const date = new Date(point.t);
      const bucket = byKey[`${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`];
      if (!bucket || !point.blocked) return;
      bucket.blocked += 1;
      bucket.energy += Number(point.energy) || 0;
    });
    return buckets;
  }

  _dayLabel(date, first) {
    if (first || date.getDate() === 1) {
      return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
    }
    return String(date.getDate());
  }

  _barsChart(buckets) {
    const total = buckets.reduce((sum, bucket) => sum + bucket.blocked, 0);
    if (total <= 0) {
      return `<div class="chart-empty"><ha-icon icon="mdi:chart-bar"></ha-icon><span>No blocked readings in the last ${buckets.length} days.</span></div>`;
    }
    const max = Math.max(...buckets.map((bucket) => bucket.blocked));
    const width = 560;
    const height = 220;
    const left = 40;
    const bottom = 30;
    const top = 10;
    const plotW = width - left - 8;
    const plotH = height - top - bottom;
    const step = Math.ceil(buckets.length / 7);
    const grid = [0, 0.5, 1].map((fraction) => {
      const y = top + plotH * (1 - fraction);
      return `<line x1="${left}" y1="${y}" x2="${width - 8}" y2="${y}" class="grid" />` +
        `<text x="${left - 6}" y="${y + 4}" text-anchor="end">${Math.round(max * fraction)}</text>`;
    }).join("");
    const slot = plotW / buckets.length;
    const barW = Math.max(4, slot - 6);
    const bars = buckets.map((bucket, index) => {
      const h = Math.round((plotH * bucket.blocked) / max);
      const x = left + slot * index + (slot - barW) / 2;
      const y = top + plotH - h;
      const tip = `${bucket.date.toLocaleDateString()}: ${bucket.blocked} blocked`;
      const label = index % step === 0 || index === buckets.length - 1
        ? `<text x="${(left + slot * index + slot / 2).toFixed(1)}" y="${height - 8}" text-anchor="middle">${this._escape(this._dayLabel(bucket.date, index === 0))}</text>`
        : "";
      return `<rect x="${x.toFixed(1)}" y="${y}" width="${barW.toFixed(1)}" height="${h}" rx="3" class="bar"><title>${this._escape(tip)}</title></rect>${label}`;
    }).join("");
    return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Blocked readings per day">${grid}${bars}</svg>`;
  }

  _areaChart(buckets, unit) {
    const total = buckets.reduce((sum, bucket) => sum + bucket.energy, 0);
    if (total <= 0) {
      return `<div class="chart-empty"><ha-icon icon="mdi:chart-line"></ha-icon><span>No false energy logged in the last ${buckets.length} days.</span></div>`;
    }
    const max = Math.max(...buckets.map((bucket) => bucket.energy));
    const width = 560;
    const height = 220;
    const left = 44;
    const bottom = 30;
    const top = 10;
    const plotW = width - left - 8;
    const plotH = height - top - bottom;
    const step = Math.ceil(buckets.length / 7);
    const x = (index) => left + (plotW * index) / Math.max(1, buckets.length - 1);
    const y = (value) => top + plotH - (plotH * value) / max;
    const grid = [0, 0.5, 1].map((fraction) => {
      const gy = top + plotH * (1 - fraction);
      return `<line x1="${left}" y1="${gy}" x2="${width - 8}" y2="${gy}" class="grid" />` +
        `<text x="${left - 6}" y="${gy + 4}" text-anchor="end">${this._escape(this._fmt(max * fraction))}</text>`;
    }).join("");
    const line = buckets.map((bucket, index) => `${x(index).toFixed(1)},${y(bucket.energy).toFixed(1)}`).join(" ");
    const gid = `eg-area-${this._gid++}`;
    const labels = buckets.map((bucket, index) => (
      index % step === 0 || index === buckets.length - 1
        ? `<text x="${x(index).toFixed(1)}" y="${height - 8}" text-anchor="middle">${this._escape(this._dayLabel(bucket.date, index === 0))}</text>`
        : ""
    )).join("");
    const dots = buckets.map((bucket, index) =>
      `<circle cx="${x(index).toFixed(1)}" cy="${y(bucket.energy).toFixed(1)}" r="3" class="dot"><title>${this._escape(`${bucket.date.toLocaleDateString()}: ${this._fmt(bucket.energy)} ${unit}`)}</title></circle>`
    ).join("");
    return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="False energy per day">` +
      `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">` +
      `<stop offset="0" class="area-stop-top" /><stop offset="1" class="area-stop-bottom" /></linearGradient></defs>` +
      `${grid}<polygon points="${left},${top + plotH} ${line} ${x(buckets.length - 1).toFixed(1)},${top + plotH}" fill="url(#${gid})" />` +
      `<polyline points="${line}" class="line" />${dots}${labels}</svg>`;
  }

  _donutChart(segments) {
    const total = segments.reduce((sum, item) => sum + item.count, 0);
    if (!segments.length || total <= 0) {
      return `<div class="chart-empty"><ha-icon icon="mdi:chart-donut"></ha-icon><span>No events logged yet.</span></div>`;
    }
    const palette = ["#03a9f4", "#43a047", "#ffa000", "#db4437", "#ab47bc", "#26a69a", "#78909c"];
    const radius = 54;
    const circle = 2 * Math.PI * radius;
    let offset = 0;
    const rings = segments.map((item, index) => {
      const length = (circle * item.count) / total;
      const ring = `<circle cx="70" cy="70" r="${radius}" fill="none" stroke="${palette[index % palette.length]}" stroke-width="22" stroke-dasharray="${length.toFixed(1)} ${(circle - length).toFixed(1)}" stroke-dashoffset="${(-offset).toFixed(1)}" transform="rotate(-90 70 70)"><title>${this._escape(`${item.label}: ${item.count}`)}</title></circle>`;
      offset += length;
      return ring;
    }).join("");
    const legend = segments.map((item, index) =>
      `<span><span class="dot" style="background:${palette[index % palette.length]}"></span>${this._escape(item.label)} <b>${this._escape(this._fmt(item.count))}</b></span>`
    ).join("");
    return `<div class="donut-wrap"><svg class="donut" viewBox="0 0 140 140" role="img" aria-label="Events by kind">${rings}` +
      `<text x="70" y="66" text-anchor="middle" class="donut-total">${this._escape(this._fmt(total))}</text>` +
      `<text x="70" y="84" text-anchor="middle" class="donut-sub">events</text></svg>` +
      `<div class="legend">${legend}</div></div>`;
  }

  _spark(values) {
    const width = 96;
    const height = 30;
    if (!values.length) return "";
    const max = Math.max(...values);
    const min = Math.min(...values);
    const span = max - min || 1;
    const points = values.map((value, index) => {
      const px = (width * index) / Math.max(1, values.length - 1);
      const py = height - 3 - ((height - 6) * (value - min)) / span;
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    }).join(" ");
    return `<svg class="spark" viewBox="0 0 ${width} ${height}" aria-hidden="true"><polyline points="${points}" /></svg>`;
  }

  _kpi(icon, bubble, value, label, sub, sparkValues) {
    return `<div class="kpi"><div class="bubble ${bubble}"><ha-icon icon="${icon}"></ha-icon></div>` +
      `<div><div class="value">${value}</div><div class="label">${label}</div>` +
      (sub ? `<div class="sub">${sub}</div>` : "") + `</div>` +
      (sparkValues ? `<div class="spark-wrap">${this._spark(sparkValues)}</div>` : "") + `</div>`;
  }

  _reviewTab() {
    const review = this._review || {};
    const candidates = review.candidates || [];
    const suggestions = review.cost_suggestions || [];
    return html`
      <div class="card">
        <h2><ha-icon icon="mdi:auto-fix"></ha-icon>Nothing is repaired automatically</h2>
        <p class="note">
          Preview first, then confirm. Every repair writes a JSON backup of the affected
          statistics rows before it changes anything and verifies the result afterwards.
          Monetary repairs need their own confirmation.
        </p>
        <div class="grid-buttons">
          ${["linked", "energy", "all"]
            .map(
              (scope) =>
                `<button class="action secondary" data-scan="${scope}">Scan: ${this._escape(SCAN_SCOPE_LABELS[scope])}</button>`
            )
            .join("")}
        </div>
      </div>
      <div class="card">
        <div class="section-head"><ha-icon icon="mdi:database-alert-outline"></ha-icon><h2>Suspicious statistics offsets</h2><span class="count-pill">${candidates.length}</span></div>
        ${
          candidates.length === 0
            ? `<p class="note">No candidate. Either the data is fine or no scan has run yet.</p>`
            : candidates.map((item) => this._candidateRow(item)).join("")
        }
      </div>
      ${suggestions.length ? this._costSection(suggestions, review) : ""}
      <div class="card">
        <h2><ha-icon icon="mdi:history"></ha-icon>Recent repairs and calibrations</h2>
        ${
          (review.repairs || []).length === 0 && (review.calibrations || []).length === 0
            ? `<p class="note">Nothing in the action log yet.</p>`
            : `<table><thead><tr><th>When</th><th>Action</th><th>Statistic / entity</th><th>Result</th></tr></thead>
               <tbody>
               ${(review.repairs || [])
                 .map(
                   (item) =>
                     `<tr><td>${this._escape(item.timestamp || "")}</td><td>${this._escape(item.kind || "repair")}</td>
                      <td>${this._escape(item.statistic_id || "")}</td><td>${this._escape(item.status || item.message || "")}</td></tr>`
                 )
                 .join("")}
               ${(review.calibrations || [])
                 .map(
                   (item) =>
                     `<tr><td>${this._escape(item.timestamp || "")}</td><td>calibration</td>
                      <td>${this._escape(item.entity_id || "")}</td><td>${this._escape(item.status || item.message || "")}</td></tr>`
                 )
                 .join("")}
               </tbody></table>`
        }
      </div>
    `;
  }

  _candidateRow(item) {
    const preview = this._previews[item.statistic_id];
    const ready = preview && preview.start_time === item.start_time;
    const evidence = (item.evidence || [])
      .map((chip) => `<span class="evidence">${this._escape(chip)}</span>`)
      .join("");
    return html`
      <div class="candidate">
        <div class="top">
          <strong><code>${this._escape(item.statistic_id)}</code></strong>
          <span class="offset">${this._escape(item.offset)} ${this._escape(item.unit || "")}</span>
        </div>
        <div class="meta">starts ${this._escape((item.start_time || "").replace("T", " ").slice(0, 16))} · false energy ${this._escape(item.estimated_false_energy)} kWh</div>
        <div>${evidence}</div>
        <div class="actions">
          <button class="action secondary" data-preview="${this._escape(item.statistic_id)}"
            data-start="${this._escape(item.start_time)}" data-offset="${this._escape(item.offset)}"
            data-unit="${this._escape(item.unit || "kWh")}"
            data-fingerprint="${this._escape(item.fingerprint || "")}">Preview</button>
          <button class="action" data-apply="${this._escape(item.statistic_id)}" ${ready ? "" : "disabled"}>
            Apply
          </button>
        </div>
      </div>
    `;
  }

  _costSection(suggestions) {
    const cards = suggestions
      .map(
        (item) => `
      <div class="candidate">
        <div class="top">
          <strong><code>${this._escape(item.statistic_id)}</code></strong>
          <span class="offset">${this._escape(item.offset)} ${this._escape(item.unit || "")}</span>
        </div>
        <div class="meta">starts ${this._escape((item.start_time || "").replace("T", " ").slice(0, 16))} \u00b7 ${this._escape(item.worked_example || "")}</div>
        <div class="actions">
          <button class="action secondary" data-preview-cost="${this._escape(item.statistic_id)}"
            data-start="${this._escape(item.start_time)}" data-offset="${this._escape(item.offset)}"
            data-unit="${this._escape(item.unit || "")}">Preview</button>
          <button class="action" data-apply-cost="${this._escape(item.statistic_id)}"
            data-start="${this._escape(item.start_time)}" data-offset="${this._escape(item.offset)}"
            data-unit="${this._escape(item.unit || "")}"
            ${this._previews[`cost:${item.statistic_id}`] ? "" : "disabled"}>
            Apply (money)
          </button>
        </div>
      </div>`
      )
      .join("");
    return html`
      <div class="card">
        <div class="section-head"><ha-icon icon="mdi:cash"></ha-icon><h2>Cost repair suggestions</h2><span class="count-pill">${suggestions.length}</span></div>
        <p class="note">
          Money is always repaired separately: a cost repair needs <code>confirm_cost: true</code>
          in addition to <code>confirm: true</code>.
        </p>
        ${cards}
      </div>
    `;
  }

  _backupsTab() {
    if (!this._files) {
      this._loadFiles().then(() => this._render()).catch((err) => this._fail(err));
    }
    const files = this._files || { backups: [], reports: [] };
    const table = (rows) =>
      rows.length === 0
        ? `<p class="note">No file yet.</p>`
        : `<table><thead><tr><th>File</th><th>Size</th><th>Written</th></tr></thead><tbody>
           ${rows
             .map(
               (row) =>
                 `<tr><td>${this._escape(row.name)}</td><td>${Math.round(row.size / 1024)} KiB</td>
                  <td>${new Date(row.modified * 1000).toLocaleString()}</td></tr>`
             )
             .join("")}
           </tbody></table>`;
    const values = this._settings("backups_reports");
    return html`
      <div class="card">
        <h2><ha-icon icon="mdi:database"></ha-icon>Backup and report settings</h2>
        <form data-section-form="backups_reports">
          ${(FIELDS.backups_reports || []).map((field) => this._field(field, values[field.key])).join("")}
          <button class="action" type="submit">Save</button>
        </form>
      </div>
      <div class="card">
        <h2><ha-icon icon="mdi:database"></ha-icon>Statistic backups</h2>
        <p class="note">Written before every change. Restore from these if a repair went wrong.</p>
        ${table(files.backups || [])}
      </div>
      <div class="card">
        <h2><ha-icon icon="mdi:file-document-outline"></ha-icon>Reports</h2>
        <p class="note">Created by <code>energy_guard.export_repair_report</code> (Repair tab → Export).</p>
        ${table(files.reports || [])}
        <div class="grid-buttons" style="margin-top:12px">
          <button class="action secondary" data-export-report="1">Export a report now</button>
        </div>
      </div>
    `;
  }

  _templatesTab() {
    if (!this._templates) {
      this._loadTemplates().then(() => this._render()).catch((err) => this._fail(err));
    }
    const yamlText = (this._templates && this._templates.yaml) || "";
    return html`
      <div class="card">
        <h2><ha-icon icon="mdi:file-code"></ha-icon>Generated YAML (read-only)</h2>
        <p class="note">
          This is a copy of your Energy Guard definitions. Energy Guard never edits any YAML
          file, and this dashboard/panel never writes YAML either.
        </p>
        <textarea readonly>${this._escape(yamlText)}</textarea>
        <div class="grid-buttons" style="margin-top:12px">
          <button class="action secondary" data-copy="1">Copy to clipboard</button>
          <button class="action secondary" data-export-templates="1">Write the file via the service</button>
        </div>
      </div>
    `;
  }

  /* ------------------------------------------------------------------ */
  /* events                                                             */
  /* ------------------------------------------------------------------ */
  _attach() {
    this.shadowRoot.querySelectorAll("[data-tab]").forEach((el) =>
      el.addEventListener("click", () => {
        this._tab = el.dataset.tab;
        this._editing = null;
        this._render();
      })
    );
    this.shadowRoot.querySelectorAll("[data-scan]").forEach((el) =>
      el.addEventListener("click", () => this._scan(el.dataset.scan))
    );
    this.shadowRoot.querySelectorAll("[data-goto]").forEach((el) =>
      el.addEventListener("click", () => {
        this._tab = el.dataset.goto;
        this._editing = null;
        this._render();
      })
    );
    this.shadowRoot.querySelectorAll("[data-add]").forEach((el) =>
      el.addEventListener("click", () => {
        this._editing = { section: el.dataset.add, id: null };
        this._render();
      })
    );
    this.shadowRoot.querySelectorAll("[data-edit]").forEach((el) =>
      el.addEventListener("click", () => {
        this._editing = { section: el.dataset.edit, id: el.dataset.id };
        this._render();
      })
    );
    this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) =>
      el.addEventListener("click", () =>
        this._definition(el.dataset.toggle, "toggle", { id: el.dataset.id })
      )
    );
    this.shadowRoot.querySelectorAll("[data-delete]").forEach((el) =>
      el.addEventListener("click", () => {
        const confirmed = window.confirm(
          "Delete this definition? The entity is removed from Home Assistant. " +
            "Statistics and history are NOT touched."
        );
        if (confirmed) {
          this._definition(el.dataset.delete, "delete", {
            id: el.dataset.id,
            confirm: true,
          });
        }
      })
    );
    this.shadowRoot.querySelectorAll("[data-cancel]").forEach((el) =>
      el.addEventListener("click", () => {
        this._editing = null;
        this._render();
      })
    );
    this.shadowRoot.querySelectorAll("form[data-section-form]").forEach((form) =>
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        this._saveSection(form.dataset.sectionForm, this._readForm(form, form.dataset.sectionForm));
      })
    );
    this.shadowRoot.querySelectorAll("form[data-definition-form]").forEach((form) =>
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const section = form.dataset.definitionForm;
        const id = form.dataset.id || null;
        this._definition(section, id ? "update" : "add", {
          id,
          data: this._readForm(form, section),
        });
      })
    );
    this.shadowRoot.querySelectorAll("[data-preview]").forEach((el) =>
      el.addEventListener("click", () =>
        this._previewRepair(el.dataset.preview, el.dataset)
      )
    );
    this.shadowRoot.querySelectorAll("[data-apply]").forEach((el) =>
      el.addEventListener("click", () => this._applyRepair(el.dataset.apply))
    );
    this.shadowRoot.querySelectorAll("[data-preview-cost]").forEach((el) =>
      el.addEventListener("click", () =>
        this._previewCost(el.dataset.previewCost, el.dataset)
      )
    );
    this.shadowRoot.querySelectorAll("[data-apply-cost]").forEach((el) =>
      el.addEventListener("click", () => this._applyCost(el.dataset))
    );
    this.shadowRoot.querySelectorAll("[data-copy]").forEach((el) =>
      el.addEventListener("click", () => this._copy())
    );
    this.shadowRoot.querySelectorAll("[data-export-report]").forEach((el) =>
      el.addEventListener("click", () => this._exportReport())
    );
    this.shadowRoot.querySelectorAll("[data-export-templates]").forEach((el) =>
      el.addEventListener("click", () => this._exportTemplates())
    );
    const textarea = this.shadowRoot.querySelector("textarea[readonly]");
    if (textarea) this._renderedYaml = textarea.value;
  }

  _readForm(form, section) {
    const payload = {};
    const fields = FIELDS[section] || [];
    fields.forEach((field) => {
      const input = form.elements[field.key];
      if (!input) return;
      if (field.type === "boolean") {
        payload[field.key] = !!input.checked;
        return;
      }
      if (field.type === "entity_list") {
        payload[field.key] = Array.from(input.selectedOptions).map(
          (option) => option.value
        );
        if (payload[field.key].length === 0) delete payload[field.key];
        return;
      }
      const value = input.value;
      if (value === "" || value === null) {
        if (field.optional) return; // let the server keep/clear the default
        if (field.type === "number") return;
        return;
      }
      payload[field.key] = field.type === "number" ? Number(value) : value;
    });
    return payload;
  }

  /* ------------------------------------------------------------------ */
  /* actions                                                            */
  /* ------------------------------------------------------------------ */
  async _saveSection(section, data) {
    try {
      const result = await this._ws({
        type: "energy_guard/config/set",
        section,
        data,
      });
      this._ok(`${this._sectionLabel(section)} saved.`);
      this._config = result.config;
      this._render();
      this._refreshLater();
    } catch (err) {
      this._fail(err);
    }
  }

  async _definition(section, action, payload) {
    try {
      const result = await this._ws({
        type: "energy_guard/config/definition",
        section,
        action,
        definition_id: payload.id || undefined,
        data: payload.data || {},
        confirm: !!payload.confirm,
      });
      this._config = result.config;
      this._editing = null;
      this._ok(
        action === "delete"
          ? "Definition deleted. Statistics were not touched."
          : `Definition ${action === "add" ? "added" : "updated"}.`
      );
      this._refreshLater();
    } catch (err) {
      this._fail(err);
    }
  }

  async _scan(scope) {
    try {
      const response = await this._hass.callService(
        "energy_guard",
        "scan_statistics",
        { scope },
        undefined,
        false,
        true
      );
      const data = (response && response.response) || response || {};
      const count = data.candidate_count;
      await this._loadReview();
      this._ok(
        count === undefined
          ? "Scan finished (read-only)."
          : `Scan finished (read-only): ${count} candidate(s) in ${data.statistic_count} statistics.`
      );
    } catch (err) {
      this._fail(err);
    }
  }

  async _previewRepair(statisticId, dataset) {
    try {
      const response = await this._hass.callService(
        "energy_guard",
        "repair_statistics",
        {
          repairs: [
            {
              statistic_id: statisticId,
              start_time: dataset.start,
              offset: Number(dataset.offset),
              unit: dataset.unit,
              fingerprint: dataset.fingerprint || undefined,
            },
          ],
          dry_run: true,
        },
        undefined,
        false,
        true
      );
      const data = (response && response.response) || response || {};
      this._previews[statisticId] = data;
      this._ok(
        `Preview (nothing changed yet): ${JSON.stringify(
          (data.preview && data.preview[0]) || data.message || "see the response"
        )}`
      );
    } catch (err) {
      this._fail(err);
    }
  }

  async _applyRepair(statisticId) {
    const preview = this._previews[statisticId];
    const start =
      (preview && (preview.start_time || (preview.preview && preview.preview[0] && preview.preview[0].start_time))) ||
      "";
    const candidate = (this._review.candidates || []).find(
      (item) => item.statistic_id === statisticId
    );
    if (!candidate) {
      this._fail("The candidate disappeared; run a fresh scan.");
      return;
    }
    const confirmed = window.confirm(
      `Repair ${statisticId} at ${candidate.start_time} by ${candidate.offset} ` +
        `${candidate.unit}?\n\nA JSON backup is written first and the result is verified.`
    );
    if (!confirmed) return;
    try {
      const response = await this._hass.callService(
        "energy_guard",
        "repair_statistics",
        {
          repairs: [
            {
              statistic_id: statisticId,
              start_time: candidate.start_time,
              offset: Number(candidate.offset),
              unit: candidate.unit,
              fingerprint: candidate.fingerprint || undefined,
            },
          ],
          confirm: true,
          create_backup: true,
          verify: true,
        },
        undefined,
        false,
        true
      );
      const data = (response && response.response) || response || {};
      await this._loadReview();
      this._ok(`Repair applied: ${JSON.stringify(data.status || data)}`);
      delete this._previews[statisticId];
    } catch (err) {
      this._fail(err);
    }
  }

  async _previewCost(statisticId, dataset) {
    try {
      const response = await this._hass.callService(
        "energy_guard",
        "repair_statistics",
        {
          cost_repairs: [
            {
              statistic_id: statisticId,
              start_time: dataset.start,
              offset: Number(dataset.offset),
              unit: dataset.unit,
            },
          ],
          dry_run: true,
        },
        undefined,
        false,
        true
      );
      const data = (response && response.response) || response || {};
      this._previews[`cost:${statisticId}`] = data;
      this._ok("Monetary preview ready. Nothing changed yet.");
    } catch (err) {
      this._fail(err);
    }
  }

  async _applyCost(dataset) {
    const statisticId = dataset.applyCost;
    const confirmed = window.confirm(
      `Repair the COST statistic ${statisticId} by ${dataset.offset} ${dataset.unit}?\n\n` +
        "This is a second, separate confirmation. A JSON backup is written first."
    );
    if (!confirmed) return;
    try {
      const response = await this._hass.callService(
        "energy_guard",
        "repair_statistics",
        {
          cost_repairs: [
            {
              statistic_id: statisticId,
              start_time: dataset.start,
              offset: Number(dataset.offset),
              unit: dataset.unit,
            },
          ],
          confirm: true,
          confirm_cost: true,
          create_backup: true,
        },
        undefined,
        false,
        true
      );
      const data = (response && response.response) || response || {};
      await this._loadReview();
      this._ok(`Cost repair applied: ${JSON.stringify(data.status || data)}`);
      delete this._previews[`cost:${statisticId}`];
    } catch (err) {
      this._fail(err);
    }
  }

  async _exportReport() {
    try {
      const response = await this._hass.callService(
        "energy_guard",
        "export_repair_report",
        { hours: 168 },
        undefined,
        false,
        true
      );
      const data = (response && response.response) || response || {};
      this._files = null;
      this._ok(`Report written: ${JSON.stringify(data.report || data.message || data)}`);
    } catch (err) {
      this._fail(err);
    }
  }

  async _exportTemplates() {
    try {
      await this._hass.callService("energy_guard", "export_templates", {
        write_file: true,
      });
      this._ok("Template file written by the service (Energy Guard never edits YAML).");
    } catch (err) {
      this._fail(err);
    }
  }

  async _copy() {
    try {
      const text = (this._templates && this._templates.yaml) || "";
      await navigator.clipboard.writeText(text);
      this._ok("Copied to the clipboard.");
    } catch (err) {
      this._fail(err);
    }
  }

  _refreshLater() {
    window.setTimeout(() => {
      this._loadReview()
        .then(() => this._loadDashboard())
        .then(() => this._render())
        .catch(() => {});
    }, 1500);
  }
}

if (!customElements.get("energy-guard-config-panel")) {
  customElements.define("energy-guard-config-panel", EnergyGuardConfigPanel);
}
