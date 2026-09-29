const ZHIJIA_TL = {
  hoursDefault: 24,

  hsToRgb(h, s) {
    const sat = Math.min(Math.max(s, 0), 100) / 100;
    const hue = ((h % 360) + 360) % 360;
    const x = sat * (1 - Math.abs(((hue / 60) % 2) - 1));
    const m = 1 - sat;
    let r = 0;
    let g = 0;
    let b = 0;
    if (hue < 60) {
      r = sat;
      g = x;
    } else if (hue < 120) {
      r = x;
      g = sat;
    } else if (hue < 180) {
      g = sat;
      b = x;
    } else if (hue < 240) {
      g = x;
      b = sat;
    } else if (hue < 300) {
      r = x;
      b = sat;
    } else {
      r = sat;
      b = x;
    }
    return [
      Math.round((r + m) * 255),
      Math.round((g + m) * 255),
      Math.round((b + m) * 255),
    ];
  },

  colorOf(seg) {
    if (seg.state !== "on") return null;
    const attrs = seg.attrs || {};
    let rgb = null;
    if (Array.isArray(attrs.rgb_color) && attrs.rgb_color.length === 3) {
      rgb = attrs.rgb_color.map(Number);
    } else if (Array.isArray(attrs.hs_color) && attrs.hs_color.length === 2) {
      rgb = this.hsToRgb(Number(attrs.hs_color[0]), Number(attrs.hs_color[1]));
    } else {
      rgb = [255, 255, 255];
    }
    const brightness =
      typeof attrs.brightness === "number"
        ? Math.min(Math.max(attrs.brightness, 0), 255) / 255
        : 1;
    const factor = 0.15 + 0.85 * brightness;
    return rgb.map((c) => Math.min(255, Math.round(c * factor)));
  },

  formatDur(ms) {
    const totalMin = Math.max(0, Math.round(ms / 60000));
    if (totalMin < 60) return `${totalMin}min`;
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (h >= 24) {
      const d = Math.floor(h / 24);
      const rh = h % 24;
      return rh ? `${d}d ${rh}h` : `${d}d`;
    }
    return m ? `${h}h${String(m).padStart(2, "0")}` : `${h}h`;
  },

  fmtTime(ms, hours) {
    const d = new Date(ms);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    if (hours > 24) {
      return `${String(d.getDate()).padStart(2, "0")}/${String(
        d.getMonth() + 1
      ).padStart(2, "0")} ${hh}:${mm}`;
    }
    return `${hh}:${mm}`;
  },

  buildSegments(entries, hours) {
    const now = Date.now();
    const start = now - hours * 3600 * 1000;
    const points = (entries || [])
      .map((e) => ({
        t:
          (typeof e.lc === "number"
            ? e.lc
            : typeof e.lu === "number"
              ? e.lu
              : 0) * 1000,
        state: e.s,
        attrs: e.a || {},
      }))
      .filter((p) => p.t > 0)
      .sort((a, b) => a.t - b.t);
    if (!points.length) return [];
    if (points[0].t > start) {
      points.unshift({
        t: start,
        state: points[0].state,
        attrs: points[0].attrs,
      });
    }
    const segs = [];
    for (let i = 0; i < points.length; i += 1) {
      const t0 = Math.max(points[i].t, start);
      const t1 = i + 1 < points.length ? points[i + 1].t : now;
      if (t1 <= t0) continue;
      const seg = {
        t0,
        t1,
        state: points[i].state,
        attrs: points[i].attrs,
      };
      seg.color = this.colorOf(seg);
      segs.push(seg);
    }
    return segs;
  },
};

class ZhijiaLightTimeline extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._entityId = null;
    this._inline = false;
    this._hours = ZHIJIA_TL.hoursDefault;
    this._segs = null;
    this._start = 0;
    this._span = 1;
    this._error = null;
    this._lastKey = "";
    this._lastFetch = 0;
    this._fetching = false;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Informe 'entity'");
    }
    this._entityId = config.entity;
    this._hours = Number(config.hours) || ZHIJIA_TL.hoursDefault;
    this._inline = false;
    this._maybeFetch();
  }

  set entityId(entityId) {
    this._entityId = entityId;
    this._inline = true;
    this._maybeFetch();
  }

  get entityId() {
    return this._entityId;
  }

  set hours(hours) {
    const value = Number(hours) || ZHIJIA_TL.hoursDefault;
    if (value !== this._hours) {
      this._hours = value;
      this._segs = null;
      this._maybeFetch();
    }
  }

  get hours() {
    return this._hours;
  }

  set hass(hass) {
    this._hass = hass;
    this._maybeFetch();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return 4;
  }

  connectedCallback() {
    this._maybeFetch();
  }

  _maybeFetch() {
    if (!this._hass || !this._entityId) return;
    const state = this._hass.states[this._entityId];
    const key = `${this._entityId}|${this._hours}|${
      state ? state.last_updated : "-"
    }`;
    const stale = Date.now() - this._lastFetch > 120000;
    if (key !== this._lastKey || !this._segs || stale) {
      this._lastKey = key;
      this._fetch();
    } else {
      this._render();
    }
  }

  async _fetch() {
    if (this._fetching || !this._hass || !this._entityId) return;
    this._fetching = true;
    const hours = this._hours;
    const start = new Date(Date.now() - hours * 3600 * 1000);
    try {
      const res = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        entity_ids: [this._entityId],
        include_start_time_state: true,
        significant_changes_only: false,
        minimal_response: false,
        no_attributes: false,
      });
      const entries = (res && res[this._entityId]) || [];
      this._segs = ZHIJIA_TL.buildSegments(entries, hours);
      this._start = Date.now() - hours * 3600 * 1000;
      this._span = hours * 3600 * 1000;
      this._error = null;
      this._lastFetch = Date.now();
    } catch (err) {
      this._error = err && err.message ? err.message : String(err);
    } finally {
      this._fetching = false;
      this._render();
    }
  }

  _summary(segs) {
    let onMs = 0;
    const buckets = new Map();
    segs.forEach((seg) => {
      const dur = seg.t1 - seg.t0;
      if (seg.state === "on") {
        onMs += dur;
        const key = seg.color ? seg.color.join(",") : "255,255,255";
        buckets.set(key, (buckets.get(key) || 0) + dur);
      }
    });
    const legend = [...buckets.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([rgb, dur]) => ({ rgb, dur }));
    return { onMs, pct: Math.round((onMs / this._span) * 100), legend };
  }

  _message(title, text, showTitle) {
    this.shadowRoot.innerHTML = `
      <style>${this._baseStyles()}</style>
      ${
        showTitle && !this._inline
          ? `<div class="wrap"><div class="title">${title}</div><div class="msg">${text}</div></div>`
          : `<div class="wrap"><div class="msg">${text}</div></div>`
      }`;
  }

  _baseStyles() {
    return `
      :host { display: block; }
      .wrap { padding: 4px 0; }
      .head { display: flex; justify-content: space-between; align-items: baseline;
              gap: 8px; margin-bottom: 8px; }
      .title { font-size: .95rem; font-weight: 500; color: var(--primary-text-color); }
      .sum { font-size: .78rem; color: var(--secondary-text-color); white-space: nowrap; }
      .bar { position: relative; height: 30px; border-radius: 8px; overflow: hidden;
             background: var(--card-background-color, #fff);
             border: 1px solid var(--divider-color, rgba(0,0,0,.12)); }
      .bar.clickable { cursor: pointer; }
      .seg { position: absolute; top: 0; bottom: 0; }
      .axis { display: flex; justify-content: space-between; margin-top: 5px;
              font-size: .7rem; color: var(--secondary-text-color); }
      .legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
      .chip { display: inline-flex; align-items: center; gap: 5px; font-size: .7rem;
              color: var(--secondary-text-color); }
      .chip i { width: 11px; height: 11px; border-radius: 3px; display: inline-block;
                border: 1px solid var(--divider-color, rgba(0,0,0,.15)); }
      .msg { color: var(--secondary-text-color); font-size: .8rem; padding: 4px 0; }`;
  }

  _render() {
    if (!this._hass || !this._entityId || !this.shadowRoot) return;
    const state = this._hass.states[this._entityId];
    const title =
      (state && state.attributes && state.attributes.friendly_name) ||
      this._entityId;

    if (!state) {
      this._message(title, "Entidade não encontrada", true);
      return;
    }
    if (this._error) {
      this._message(title, `Erro no histórico: ${this._error}`, true);
      return;
    }
    if (!this._segs) {
      this._message(title, "Carregando histórico…", true);
      return;
    }

    const { onMs, pct, legend } = this._summary(this._segs);
    const hours = this._hours;
    const span = this._span;

    const bar = this._segs
      .map((seg) => {
        const left = ((seg.t0 - this._start) / span) * 100;
        const width = ((seg.t1 - seg.t0) / span) * 100;
        let bg;
        let tip;
        if (seg.state === "on") {
          const c = seg.color || [255, 255, 255];
          bg = `rgb(${c[0]},${c[1]},${c[2]})`;
          const attrs = seg.attrs || {};
          const rawRgb = Array.isArray(attrs.rgb_color)
            ? `rgb(${attrs.rgb_color.join(",")})`
            : "—";
          const br =
            typeof attrs.brightness === "number"
              ? `${Math.round((attrs.brightness / 255) * 100)}%`
              : "100%";
          tip = `${ZHIJIA_TL.fmtTime(seg.t0, hours)} → ${ZHIJIA_TL.fmtTime(
            seg.t1,
            hours
          )} · ${ZHIJIA_TL.formatDur(seg.t1 - seg.t0)} · cor ${rawRgb} · brilho ${br}`;
        } else if (seg.state === "unavailable" || seg.state === "unknown") {
          bg =
            "repeating-linear-gradient(45deg, rgba(160,160,160,.35), rgba(160,160,160,.35) 6px, rgba(160,160,160,.12) 6px, rgba(160,160,160,.12) 12px)";
          tip = `${ZHIJIA_TL.fmtTime(seg.t0, hours)} → ${ZHIJIA_TL.fmtTime(
            seg.t1,
            hours
          )} · ${seg.state}`;
        } else {
          bg = "var(--state-inactive-color, #9e9e9e)";
          tip = `${ZHIJIA_TL.fmtTime(seg.t0, hours)} → ${ZHIJIA_TL.fmtTime(
            seg.t1,
            hours
          )} · desligada · ${ZHIJIA_TL.formatDur(seg.t1 - seg.t0)}`;
        }
        return `<div class="seg" style="left:${left.toFixed(
          3
        )}%;width:${width.toFixed(3)}%;background:${bg}" title="${tip}"></div>`;
      })
      .join("");

    const ticks = [0, 0.25, 0.5, 0.75, 1]
      .map(
        (f) =>
          `<span>${ZHIJIA_TL.fmtTime(this._start + span * f, hours)}</span>`
      )
      .join("");

    const legendHtml = legend
      .map(
        (item) =>
          `<span class="chip"><i style="background:rgb(${item.rgb})"></i>${ZHIJIA_TL.formatDur(
            item.dur
          )}</span>`
      )
      .join("");

    const wrapClass = this._inline ? "wrap" : "wrap card";
    const head = this._inline
      ? `<div class="head"><div class="sum">Ligada ${ZHIJIA_TL.formatDur(
          onMs
        )} · ${pct}%</div><div class="sum">${hours}h</div></div>`
      : `<div class="head"><div class="title">${title} · linha do tempo</div>
         <div class="sum">Ligada ${ZHIJIA_TL.formatDur(onMs)} · ${pct}%</div></div>`;

    this.shadowRoot.innerHTML = `
      <style>${this._baseStyles()}
        .card { background: var(--ha-card-background, var(--card-background-color, #fff));
                border: var(--ha-card-border, 1px solid var(--divider-color, rgba(0,0,0,.12)));
                border-radius: var(--ha-card-border-radius, 12px);
                box-shadow: var(--ha-card-box-shadow, none);
                padding: 12px 14px 14px; }
      </style>
      <div class="${wrapClass}">
        ${head}
        <div class="bar${this._inline ? "" : " clickable"}">${bar}</div>
        <div class="axis">${ticks}</div>
        ${legendHtml ? `<div class="legend">${legendHtml}</div>` : ""}
      </div>`;

    const barEl = this.shadowRoot.querySelector(".bar.clickable");
    if (barEl) {
      barEl.addEventListener("click", () => {
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entity: this._entityId },
            bubbles: true,
            composed: true,
          })
        );
      });
    }
  }
}

customElements.define("zhijia-light-timeline", ZhijiaLightTimeline);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "zhijia:light-timeline",
  name: "Linha do tempo da luz (ZhiJia)",
  description:
    "Barra de ligada/desligada colorida com a cor real que a luz estava em cada período",
  preview: true,
});
