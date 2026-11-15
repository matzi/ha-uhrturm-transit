/**
 * Uhrturm Transit departure card.
 *
 * One row per upcoming departure: coloured line badge, destination, minutes.
 * Delayed departures are shown in red with the delay ("7 min +2").
 *
 * type: custom:uhrturm-departures-card
 * entity: sensor.<board>_departure_1   # the sensor carrying the `departures` list
 * title: Mozartgasse → Hbf            # optional, default: stop name
 * hide_cancelled: false               # optional
 * line_colors:                        # optional, overrides the automatic colours
 *   "7": "#e2001a"
 */

const CARD_TAG = "uhrturm-departures-card";

// well distinguishable colours; numeric lines use n % length, so neighbours differ
const PALETTE = [
  "#e53935", "#1e88e5", "#43a047", "#fb8c00", "#8e24aa", "#00897b",
  "#fdd835", "#6d4c41", "#d81b60", "#3949ab", "#7cb342", "#00acc1",
  "#f4511e", "#5e35b1", "#c0ca33", "#546e7a",
];

const TEXTS = {
  en: { now: "now", cancelled: "cancelled", empty: "No departures", unavailable: "Entity unavailable" },
  de: { now: "jetzt", cancelled: "entfällt", empty: "Keine Abfahrten", unavailable: "Entität nicht verfügbar" },
};

function lineColor(line, overrides) {
  const name = String(line ?? "");
  if (overrides && overrides[name]) return overrides[name];
  if (/^\d+$/.test(name)) return PALETTE[Number(name) % PALETTE.length];
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

function textColor(background) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(background.trim());
  if (!m) return "#fff";
  const [r, g, b] = m.slice(1).map((h) => {
    const c = parseInt(h, 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? "#000" : "#fff";
}

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

class UhrturmDeparturesCard extends HTMLElement {
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { domain: "sensor", integration: "uhrturm_oepnv" } } },
        { name: "title", selector: { text: {} } },
        { name: "hide_cancelled", selector: { boolean: {} } },
        { name: "line_colors", selector: { object: {} } },
      ],
    };
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("sensor.") && Array.isArray(hass.states[id].attributes.departures)
    );
    return { entity: entity ?? "" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    this._config = config;
    this._render();
  }

  set hass(hass) {
    const stateObj = hass.states[this._config?.entity];
    const changed = !this._hass || stateObj !== this._hass.states[this._config?.entity];
    this._hass = hass;
    if (changed) this._render(); // hass is set on every state change in HA
  }

  connectedCallback() {
    // minutes count down between state updates
    this._timer = setInterval(() => this._render(), 15000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    const deps = this._hass?.states[this._config?.entity]?.attributes.departures;
    return 1 + (Array.isArray(deps) ? deps.length : 1);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 4 };
  }

  _t(key) {
    const lang = (this._hass?.locale?.language || this._hass?.language || "en").split("-")[0];
    return (TEXTS[lang] || TEXTS.en)[key];
  }

  _row(dep, now) {
    const color = lineColor(dep.line, this._config.line_colors);
    const delay = Number(dep.delay) || 0;
    const delayed = delay > 0 && !dep.cancelled;
    let time;
    if (dep.cancelled) {
      time = `<span class="time late">${esc(this._t("cancelled"))}</span>`;
    } else {
      const minutes = Math.max(0, Math.floor((Date.parse(dep.time) - now) / 60000));
      const label = minutes === 0 ? this._t("now") : `${minutes} min`;
      const plus = delayed ? `<span class="delay">+${delay}</span>` : "";
      time = `<span class="time${delayed ? " late" : ""}">${esc(label)}${plus}</span>`;
    }
    return `
      <div class="row${dep.cancelled ? " cancelled" : ""}">
        <span class="line" style="background:${esc(color)};color:${textColor(color)}">${esc(dep.line)}</span>
        <span class="destination">${esc(dep.destination)}</span>
        ${time}
      </div>`;
  }

  _render() {
    if (!this._config || !this._hass) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });

    const stateObj = this._hass.states[this._config.entity];
    const title = this._config.title ?? stateObj?.attributes.stop ?? "";
    let body;
    if (!stateObj || stateObj.state === "unavailable") {
      body = `<div class="empty">${esc(this._t("unavailable"))}</div>`;
    } else {
      const now = Date.now();
      const deps = (stateObj.attributes.departures || []).filter(
        (d) => d.time && !(this._config.hide_cancelled && d.cancelled)
      );
      body = deps.length
        ? deps.map((d) => this._row(d, now)).join("")
        : `<div class="empty">${esc(this._t("empty"))}</div>`;
    }

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px 16px; }
        .title { font-size: 1.2em; font-weight: 500; padding: 4px 0 8px; }
        .row { display: flex; align-items: center; gap: 12px; padding: 5px 0; }
        .line {
          flex: none; min-width: 2.2em; height: 2.2em; padding: 0 4px; box-sizing: border-box;
          display: inline-flex; align-items: center; justify-content: center;
          border-radius: 4px; font-weight: 700; white-space: nowrap;
        }
        .destination { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .time { flex: none; font-weight: 600; white-space: nowrap; font-variant-numeric: tabular-nums; }
        .late { color: var(--error-color, #db4437); }
        .delay { font-size: 0.8em; margin-left: 4px; }
        .cancelled .destination { text-decoration: line-through; color: var(--secondary-text-color); }
        .cancelled .line { opacity: 0.5; }
        .empty { color: var(--secondary-text-color); padding: 4px 0; }
      </style>
      <ha-card>
        ${title ? `<div class="title">${esc(title)}</div>` : ""}
        ${body}
      </ha-card>`;
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, UhrturmDeparturesCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TAG,
    name: "Uhrturm departures",
    description: "Upcoming departures of an Uhrturm Transit departure board",
    preview: true,
  });
}
