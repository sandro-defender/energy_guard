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
  ["overview", "Overview"],
  ["protected_sensors", "Protected"],
  ["derived_sensors", "Derived"],
  ["utility_meters", "Meters"],
  ["detection_rules", "Detection"],
  ["statistics_repair", "Statistics"],
  ["cost_repair", "Cost"],
  ["backups_reports", "Backups"],
  ["review", "Repair"],
  ["templates", "Templates"],
];

const UNITS = ["kWh", "Wh", "MWh", "GWh", "MJ", "GJ"];

/* Field descriptors: one source of truth for every form. */
const FIELDS = {
  protected_sensors: [
    { key: "name", label: "Name", type: "text", required: true, hint: "Keep the ' protected' suffix: this name becomes the entity id." },
    { key: "source_entity_id", label: "Source sensor", type: "energy_entity", required: true, hint: "The cumulative sensor you want to protect." },
    { key: "enabled", label: "Enabled", type: "boolean", default: true },
    { key: "unit_of_measurement", label: "Unit", type: "select", options: UNITS, default: "kWh" },
    { key: "offset", label: "Offset", type: "number", step: "any", default: 0 },
    { key: "zero_min_previous", label: "Treat values below this as a false zero", type: "number", step: "any", hint: "kWh" },
    { key: "confirm_scans", label: "Confirm the false reading after N updates", type: "number", step: "1", default: 2 },
    { key: "recovery_hold_scans", label: "Recovery hold", type: "number", step: "1", default: 1 },
    { key: "grace_period", label: "Grace period (seconds)", type: "number", step: "1", default: 30 },
    { key: "do_not_decrease", label: "Never decrease", type: "boolean", default: true },
    { key: "accept_real_reset", label: "Accept real meter resets", type: "boolean", default: false },
    { key: "reject_large_jumps", label: "Reject large jumps", type: "boolean", default: false },
    { key: "large_jump", label: "Large jump threshold", type: "number", step: "any" },
    { key: "large_jump_ratio", label: "Large jump ratio", type: "number", step: "any" },
    { key: "max_value", label: "Maximum plausible value", type: "number", step: "any", optional: true },
    { key: "precision", label: "Precision", type: "number", step: "1", default: 3 },
  ],
  derived_sensors: [
    { key: "name", label: "Name", type: "text", required: true },
    { key: "mode", label: "Mode", type: "select", options: ["sum", "difference", "phase_split"], default: "sum" },
    { key: "source_entity_ids", label: "Sensors to combine", type: "entity_list", required: true },
    { key: "total_entity_id", label: "Total sensor (phase split)", type: "entity", optional: true },
    { key: "part_entity_ids", label: "Phase sensors (phase split)", type: "entity_list", optional: true },
    { key: "enabled", label: "Enabled", type: "boolean", default: true },
    { key: "unit_of_measurement", label: "Unit", type: "select", options: UNITS, default: "kWh" },
    { key: "scale", label: "Multiplier", type: "number", step: "any", default: 1 },
    { key: "offset", label: "Offset", type: "number", step: "any", default: 0 },
    { key: "require_all_sources", label: "Require all sources to be available", type: "boolean", default: true },
    { key: "do_not_decrease", label: "Never decrease", type: "boolean", default: true },
  ],
  utility_meters: [
    { key: "name", label: "Name", type: "text", required: true },
    { key: "utility_meter_entity_id", label: "Utility meter entity", type: "entity", required: true },
    { key: "source_entity_id", label: "Source sensor", type: "energy_entity", optional: true, hint: "Used to compute the target of a calibration." },
    { key: "cycle", label: "Cycle (monthly, daily, ...)", type: "text", optional: true },
    { key: "baseline_at", label: "Baseline at", type: "text", optional: true, hint: "ISO date/time of the baseline, optional." },
    { key: "baseline_value", label: "Baseline value", type: "number", step: "any", optional: true },
    { key: "enabled", label: "Enabled", type: "boolean", default: true },
  ],
  detection_rules: [
    { key: "scan_interval", label: "Scan interval (seconds)", type: "number", step: "1" },
    { key: "lookback_hours", label: "Statistics lookback (hours)", type: "number", step: "1" },
    { key: "issue_window_hours", label: "Issue window (hours)", type: "number", step: "1" },
    { key: "simultaneous_threshold", label: "Simultaneous failures to report", type: "number", step: "1" },
    { key: "simultaneous_window_minutes", label: "Simultaneous window (minutes)", type: "number", step: "1" },
    { key: "event_retention_days", label: "Keep the diagnostic log for (days)", type: "number", step: "1" },
  ],
  statistics_repair: [
    { key: "scan_scope", label: "Default scan scope", type: "scan_scope" },
    { key: "statistics_jump_threshold", label: "Suspicious jump threshold", type: "number", step: "any" },
    { key: "statistics_jump_ratio", label: "Suspicious jump ratio", type: "number", step: "any" },
    { key: "sum_state_ratio", label: "Sum/state ratio", type: "number", step: "any" },
  ],
  cost_repair: [
    { key: "enabled", label: "Mirror kWh repairs into the cost statistic", type: "boolean", default: false },
    { key: "price", label: "Price per kWh", type: "number", step: "any" },
    { key: "currency", label: "Currency", type: "text", default: "GEL" },
    { key: "energy_statistic_id", label: "Energy statistic id", type: "text", optional: true },
    { key: "cost_statistic_id", label: "Cost statistic id", type: "text", optional: true },
    { key: "price_entity_id", label: "Live price entity (optional)", type: "entity", optional: true },
  ],
  backups_reports: [
    { key: "create_backup", label: "Create a backup before every change", type: "boolean", default: true },
    { key: "keep_backups", label: "Backups to keep", type: "number", step: "1" },
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
    this._tab = "overview";
    this._error = null;
    this._message = null;
    this._editing = null; // {section, id}
    this._draft = {};
    this._previews = {}; // statistic_id -> preview result
    this._loaded = false;
    this._subscribed = false;
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
      ([id, label]) =>
        `<button class="tab ${this._tab === id ? "active" : ""}" data-tab="${id}">${label}</button>`
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
        .head h1 { font-size: 1.4rem; margin: 0; font-weight: 500; }
        .badge { font-size: 0.75rem; color: var(--secondary-text-color); }
        .tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
        .tab { border: 1px solid var(--divider-color); background: none; color: inherit;
               border-radius: 16px; padding: 6px 14px; cursor: pointer; font-size: 0.85rem; }
        .tab.active { background: var(--primary-color); border-color: var(--primary-color);
                      color: var(--text-primary-color, #fff); }
        .card { ${CARD} }
        .card h2 { margin: 0 0 8px 0; font-size: 1.05rem; font-weight: 500; }
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
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--divider-color); }
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
      </style>
      <div class="head">
        <h1>Energy Guard</h1>
        <span class="badge">v${this._escape((this._meta && this._meta.version) || "")} ·
          configuration</span>
      </div>
      ${this._error ? `<div class="error">${this._escape(this._error)}</div>` : ""}
      ${this._message ? `<div class="success">${this._escape(this._message)}</div>` : ""}
      <div class="tabs">${tabs}</div>
      <div id="body">${body}</div>
    `;
    this._attach();
  }

  _overview() {
    const review = this._review || {};
    const last = review.last_scan
      ? new Date(review.last_scan).toLocaleString()
      : "never";
    const state = this._hass.states["binary_sensor.energy_guard_data_issue"];
    const open = state && state.state === "on";
    const count = (review.candidates || []).length;
    return html`
      <div class="card">
        <h2>Status</h2>
        <p>
          ${
            open
              ? "<strong>Something needs your review.</strong> Open the Repair tab."
              : "<strong>No open issues.</strong> Energy Guard is publishing the protected values."
          }
        </p>
        <ul>
          <li>Suspicious statistics offsets: <strong>${count}</strong></li>
          <li>Last scan: <strong>${this._escape(last)}</strong>
            ${review.last_scan_scope ? `(scope <code>${this._escape(review.last_scan_scope)}</code>, ${review.last_scan_statistic_count} statistics)` : ""}</li>
          <li>Protected sensors: <strong>${this._items("protected_sensors").length}</strong>,
              derived: <strong>${this._items("derived_sensors").length}</strong>,
              meters: <strong>${this._items("utility_meters").length}</strong></li>
          <li>Default scan scope: <strong>${this._escape(this._settings("statistics_repair").scan_scope || "linked")}</strong></li>
        </ul>
        ${review.last_scan_error ? `<p class="error">${this._escape(review.last_scan_error)}</p>` : ""}
      </div>
      <div class="card">
        <h2>Scan for corrupted data</h2>
        <p class="note">Read-only. Nothing is repaired by a scan.</p>
        <div class="grid-buttons">
          ${["linked", "energy", "all"]
            .map(
              (scope) =>
                `<button class="action secondary" data-scan="${scope}">${this._escape(SCAN_SCOPE_LABELS[scope])}</button>`
            )
            .join("")}
        </div>
      </div>
      <div class="card">
        <h2>Where to go next</h2>
        <ul>
          <li><strong>Protected / Derived / Meters</strong>: add, edit, disable or delete the sensors Energy Guard manages.</li>
          <li><strong>Detection / Statistics / Cost / Backups</strong>: every option of the integration.</li>
          <li><strong>Repair</strong>: review the candidates, preview a repair, then apply it with an explicit confirmation.</li>
          <li><strong>Templates</strong>: the read-only YAML copy of your configuration.</li>
        </ul>
      </div>
    `;
  }

  _sectionTab(section) {
    const fields = FIELDS[section] || [];
    const isList = ["protected_sensors", "derived_sensors", "utility_meters"].includes(
      section
    );
    if (!isList) {
      const values = this._settings(section);
      return html`
        <div class="card">
          <h2>${this._escape(this._sectionLabel(section))}</h2>
          <form data-section-form="${section}">
            ${fields.map((field) => this._field(field, values[field.key])).join("")}
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
        <h2>${this._escape(this._sectionLabel(section))}</h2>
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
      <div class="list-item">
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
        <h2>${this._escape(title)}</h2>
        <form data-definition-form="${section}" data-id="${this._escape(this._editing.id || "")}">
          ${fields.map((field) => this._field(field, item[field.key])).join("")}
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
    const raw = value === undefined || value === null ? "" : value;
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

  _reviewTab() {
    const review = this._review || {};
    const candidates = review.candidates || [];
    const suggestions = review.cost_suggestions || [];
    return html`
      <div class="card">
        <h2>Nothing is repaired automatically</h2>
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
        <h2>Suspicious statistics offsets (${candidates.length})</h2>
        ${
          candidates.length === 0
            ? `<p class="note">No candidate. Either the data is fine or no scan has run yet.</p>`
            : `<table><thead><tr><th>Statistic</th><th>Starts</th><th>Offset</th>
               <th>False energy</th><th>Evidence</th><th></th></tr></thead><tbody>
               ${candidates.map((item) => this._candidateRow(item)).join("")}
               </tbody></table>`
        }
      </div>
      ${suggestions.length ? this._costSection(suggestions, review) : ""}
      <div class="card">
        <h2>Recent repairs and calibrations</h2>
        ${
          (review.repairs || []).length === 0 && (review.calibrations || []).length === 0
            ? `<p class="note">Nothing has been repaired or calibrated since the last restart.</p>`
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
    return html`
      <tr>
        <td><code>${this._escape(item.statistic_id)}</code></td>
        <td>${this._escape((item.start_time || "").replace("T", " ").slice(0, 16))}</td>
        <td>${this._escape(item.offset)} ${this._escape(item.unit || "")}</td>
        <td>${this._escape(item.estimated_false_energy)} kWh</td>
        <td class="hint">${this._escape((item.evidence || []).join(", "))}</td>
        <td>
          <button class="action secondary" data-preview="${this._escape(item.statistic_id)}"
            data-start="${this._escape(item.start_time)}" data-offset="${this._escape(item.offset)}"
            data-unit="${this._escape(item.unit || "kWh")}"
            data-fingerprint="${this._escape(item.fingerprint || "")}">Preview</button>
          <button class="action" data-apply="${this._escape(item.statistic_id)}" ${ready ? "" : "disabled"}>
            Apply
          </button>
        </td>
      </tr>
    `;
  }

  _costSection(suggestions) {
    return html`
      <div class="card">
        <h2>Cost repair suggestions (${suggestions.length})</h2>
        <p class="note">
          Money is always repaired separately: a cost repair needs <code>confirm_cost: true</code>
          in addition to <code>confirm: true</code>.
        </p>
        <table><thead><tr><th>Cost statistic</th><th>Starts</th><th>Offset</th>
          <th>Worked example</th><th></th></tr></thead><tbody>
          ${suggestions
            .map(
              (item) => `
            <tr>
              <td><code>${this._escape(item.statistic_id)}</code></td>
              <td>${this._escape((item.start_time || "").replace("T", " ").slice(0, 16))}</td>
              <td>${this._escape(item.offset)} ${this._escape(item.unit || "")}</td>
              <td>${this._escape(item.worked_example || "")}</td>
              <td>
                <button class="action secondary" data-preview-cost="${this._escape(item.statistic_id)}"
                  data-start="${this._escape(item.start_time)}" data-offset="${this._escape(item.offset)}"
                  data-unit="${this._escape(item.unit || "")}">Preview</button>
                <button class="action" data-apply-cost="${this._escape(item.statistic_id)}"
                  data-start="${this._escape(item.start_time)}" data-offset="${this._escape(item.offset)}"
                  data-unit="${this._escape(item.unit || "")}"
                  ${this._previews[`cost:${item.statistic_id}`] ? "" : "disabled"}>
                  Apply (money)
                </button>
              </td>
            </tr>`
            )
            .join("")}
        </tbody></table>
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
        <h2>Backup and report settings</h2>
        <form data-section-form="backups_reports">
          ${(FIELDS.backups_reports || []).map((field) => this._field(field, values[field.key])).join("")}
          <button class="action" type="submit">Save</button>
        </form>
      </div>
      <div class="card">
        <h2>Statistic backups</h2>
        <p class="note">Written before every change. Restore from these if a repair went wrong.</p>
        ${table(files.backups || [])}
      </div>
      <div class="card">
        <h2>Reports</h2>
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
        <h2>Generated YAML (read-only)</h2>
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
        .then(() => this._render())
        .catch(() => {});
    }, 1500);
  }
}

if (!customElements.get("energy-guard-config-panel")) {
  customElements.define("energy-guard-config-panel", EnergyGuardConfigPanel);
}
