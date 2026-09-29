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
    } else if (
      Array.isArray(attrs.color_temp_kelvin) === false &&
      typeof attrs.color_mode === "string" &&
      attrs.color_mode !== "onoff"
    ) {
      rgb = [255, 255, 255];
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
    const totalMin = Math.round(ms / 60000);
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
    this._config = null;
    this._hass = null;
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
    this._config = Object.assign(
      { hours: ZHIJIA_TL.hoursDefault, title: null },
      config
    );
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const state = hass.states[this._config.entity];
    const key = `${this._config.entity}|${this._config.hours}|${
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

  getCardSize() {
    return 4;
  }

  connectedCallback() {
    if (this._hass && !this._segs) this._fetch();
    else this._render();
  }

  async _fetch() {
    if (this._fetching || !this._hass || !this._config) return;
    this._fetching = true;
    const hours = this._config.hours;
    const start = new Date(Date.now() - hours * 3600 * 1000);
    try {
      const res = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        entity_ids: [this._config.entity],
        include_start_time_state: true,
        significant_changes_only: false,
        minimal_response: true,
        no_attributes: false,
      });
      const entries = (res && res[this._config.entity]) || [];
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

  _render() {
    if (!this._config || !this._hass || !this.shadowRoot) return;
    const state = this._hass.states[this._config.entity];
    const name =
      (state &&
        state.attributes &&
        state.attributes.friendly_name) ||
      this._config.entity;
    const title = this._config.title || `${name} · linha do tempo`;

    if (!state) {
      this.shadowRoot.innerHTML = `
        <ha-card><div class="pad"><div class="title">${title}</div>
        <div class="empty">Entidade não encontrada</div></div></ha-card>`;
      return;
    }

    if (this._error) {
      this.shadowRoot.innerHTML = `
        <ha-card><div class="pad"><div class="title">${title}</div>
        <div class="empty">Erro ao buscar histórico: ${this._error}</div></div></ha-card>`;
      return;
    }

    if (!this._segs) {
      this.shadowRoot.innerHTML = `
        <ha-card><div class="pad"><div class="title">${title}</div>
        <div class="empty">Carregando histórico…</div></div></ha-card>`;
      return;
    }

    const { onMs, pct, legend } = this._summary(this._segs);
    const hours = this._config.hours;
    const span = this._span;
    const now = Date.now();

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
          bg = "repeating-linear-gradient(45deg, rgba(160,160,160,.35), rgba(160,160,160,.35) 6px, rgba(160,160,160,.12) 6px, rgba(160,160,160,.12) 12px)";
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
      .map((f) => {
        const t = this._start + span * f;
        return `<span>${ZHIJIA_TL.fmtTime(t, hours)}</span>`;
      })
      .join("");

    const legendHtml = legend
      .map((item) => {
        const c = item.rgb.split(",");
        return `<span class="chip"><i style="background:rgb(${item.rgb})"></i>${ZHIJIA_TL.formatDur(
          item.dur
        )}</span>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 14px 14px; overflow: hidden; }
        .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 10px; }
        .title { font-size: .95rem; font-weight: 500; color: var(--primary-text-color); }
        .sum { font-size: .8rem; color: var(--secondary-text-color); white-space: nowrap; }
        .bar { position: relative; height: 34px; border-radius: 8px; overflow: hidden;
               background: var(--card-background-color, #fff);
               border: 1px solid var(--divider-color, rgba(0,0,0,.12)); cursor: pointer; }
        .seg { position: absolute; top: 0; bottom: 0; }
        .axis { display: flex; justify-content: space-between; margin-top: 6px;
                font-size: .72rem; color: var(--secondary-text-color); }
        .legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
        .chip { display: inline-flex; align-items: center; gap: 5px; font-size: .72rem;
                color: var(--secondary-text-color); }
        .chip i { width: 12px; height: 12px; border-radius: 3px; display: inline-block;
                  border: 1px solid var(--divider-color, rgba(0,0,0,.15)); }
        .empty { color: var(--secondary-text-color); font-size: .85rem; padding: 8px 0 4px; }
      </style>
      <ha-card>
        <div class="head">
          <div class="title">${title}</div>
          <div class="sum">Ligada ${ZHIJIA_TL.formatDur(onMs)} · ${pct}%</div>
        </div>
        <div class="bar">${bar}</div>
        <div class="axis">${ticks}</div>
        ${legendHtml ? `<div class="legend">${legendHtml}</div>` : ""}
      </ha-card>`;

    const barEl = this.shadowRoot.querySelector(".bar");
    if (barEl) {
      barEl.addEventListener("click", () => {
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entity: this._config.entity },
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
