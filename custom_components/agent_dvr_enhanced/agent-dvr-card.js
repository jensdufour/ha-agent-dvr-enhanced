class AgentDVRCard extends HTMLElement {
    static get properties() {
        return {
            hass: {},
            config: {},
        };
    }

    constructor() {
        super();
        this.attachShadow({ mode: "open" });
        this._activeTab = "live";
        this._recordings = [];
        this._loading = false;
        this._playingRecording = null;
    }

    setConfig(config) {
        if (!config.camera_entity) {
            throw new Error("You need to define a camera_entity");
        }
        this._config = config;
        this._render();
    }

    set hass(hass) {
        this._hass = hass;
        if (!this._initialized) {
            this._initialized = true;
            this._resolveEntryId().then(() => this._render());
        }
        this._updateCameraImage();
    }

    get hass() {
        return this._hass;
    }

    _getEntryInfo() {
        if (!this._hass) return null;
        const state = this._hass.states[this._config.camera_entity];
        if (!state) return null;
        const oid = state.attributes.object_id;
        const ot = state.attributes.object_type;
        let entryId = this._config.entry_id || this._resolvedEntryId || null;
        return { entryId, oid, ot };
    }

    async _resolveEntryId() {
        if (this._resolvedEntryId || this._config.entry_id) return;
        if (!this._hass || !this._hass.connection) return;
        try {
            const result = await this._hass.connection.sendMessagePromise({
                type: "config/entity_registry/get",
                entity_id: this._config.camera_entity,
            });
            if (result && result.config_entry_id) {
                this._resolvedEntryId = result.config_entry_id;
            }
        } catch { /* entry resolution failed, will retry */ }
    }

    _updateCameraImage() {
        if (this._activeTab !== "live") return;
        const img = this.shadowRoot.querySelector("#live-image");
        if (!img || !this._hass) return;
        const state = this._hass.states[this._config.camera_entity];
        if (!state) return;
        const token = state.attributes.access_token;
        img.src = `/api/camera_proxy/${this._config.camera_entity}?token=${token}&t=${Date.now()}`;
    }

    async _switchTab(tab) {
        this._activeTab = tab;
        this._playingRecording = null;

        if (tab === "live") {
            this._render();
            this._updateCameraImage();
            this._startLiveRefresh();
            return;
        }

        this._stopLiveRefresh();
        await this._resolveEntryId();

        if (tab === "recordings") {
            await this._fetchRecordings();
        }

        this._render();
    }

    _startLiveRefresh() {
        this._stopLiveRefresh();
        this._refreshInterval = setInterval(() => this._updateCameraImage(), 1000);
    }

    _stopLiveRefresh() {
        if (this._refreshInterval) {
            clearInterval(this._refreshInterval);
            this._refreshInterval = null;
        }
    }

    async _fetchRecordings() {
        const info = this._getEntryInfo();
        if (!info || !info.entryId) {
            this._error = "Could not resolve entry ID for camera";
            this._render();
            return;
        }

        this._loading = true;
        this._error = null;
        this._render();

        try {
            const url = `agent_dvr_enhanced/events/${info.entryId}/${info.oid}/${info.ot}`;
            const resp = await this._hass.callApi("GET", url);
            if (Array.isArray(resp)) {
                this._recordings = resp;
            } else if (resp && typeof resp === "object") {
                const arr = resp.items || resp.events || resp.data || resp.result || resp.recordings || Object.values(resp).find(v => Array.isArray(v));
                this._recordings = Array.isArray(arr) ? arr : [];
            } else {
                this._recordings = [];
            }
        } catch (err) {
            this._error = `Error fetching recordings: ${err.message || err}`;
            this._recordings = [];
        }

        this._loading = false;
    }

    async _signUrl(path) {
        const resp = await this._hass.callWS({ type: "auth/sign_path", path });
        return resp.path;
    }

    async _playRecording(rec) {
        const info = this._getEntryInfo();
        if (!info || !info.entryId) return;
        const fn = rec.fn || rec.filename || "";
        const rawUrl = `/api/agent_dvr_enhanced/recording/${info.entryId}/${info.oid}/${info.ot}/${fn}`;
        const signedUrl = await this._signUrl(rawUrl);
        this._playingRecording = { ...rec, url: signedUrl };
        this._render();
    }

    _stopPlayback() {
        this._playingRecording = null;
        this._render();
    }

    // .NET ticks epoch: ticks between 0001-01-01 and 1970-01-01
    static get DOTNET_EPOCH_TICKS() { return 621355968000000000; }

    _parseTimestamp(timestamp) {
        if (!timestamp && timestamp !== 0) return null;
        let ms;
        if (typeof timestamp === "string") {
            const dotnetMatch = timestamp.match(/\/Date\(([-+]?\d+)\)\//);
            if (dotnetMatch) {
                ms = parseInt(dotnetMatch[1], 10);
            } else if (/^\d+$/.test(timestamp.trim())) {
                const num = parseFloat(timestamp.trim());
                ms = this._numToMs(num);
            } else {
                ms = new Date(timestamp).getTime();
                if (isNaN(ms)) {
                    const dmy = timestamp.match(/(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})[\sT]?(\d{1,2}:\d{2}:?\d{0,2})?\.?.*/);
                    if (dmy) {
                        ms = new Date(`${dmy[3]}-${dmy[2].padStart(2, '0')}-${dmy[1].padStart(2, '0')}T${dmy[4] || '00:00:00'}`).getTime();
                    }
                }
            }
        } else if (typeof timestamp === "number") {
            ms = this._numToMs(timestamp);
        } else {
            return null;
        }
        if (isNaN(ms)) return null;
        return new Date(ms);
    }

    _numToMs(num) {
        if (num > 1e15) return (num - AgentDVRCard.DOTNET_EPOCH_TICKS) / 10000;
        if (num > 1e12) return num;
        return num * 1000;
    }

    _extractTimestamp(rec) {
        if (!rec || typeof rec !== "object") return null;
        for (const key of Object.keys(rec)) {
            const val = rec[key];
            if (val === undefined || val === null || val === "") continue;
            if (typeof val === "object" || typeof val === "boolean") continue;
            if (typeof val === "string" && val.length > 50) continue;
            const d = this._parseTimestamp(val);
            if (d) {
                const year = d.getFullYear();
                if (year >= 2000 && year <= 2100) return val;
            }
        }
        const fn = rec.fn || rec.filename || "";
        const fnMatch = fn.match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
        if (fnMatch) {
            return `${fnMatch[1]}-${fnMatch[2]}-${fnMatch[3]}T${fnMatch[4]}:${fnMatch[5]}:${fnMatch[6]}`;
        }
        return null;
    }

    _formatTime(timestamp) {
        if (!timestamp && timestamp !== 0) return "";
        const d = this._parseTimestamp(timestamp);
        return d ? d.toLocaleString() : String(timestamp);
    }

    _safeDateStr(d, method, options) {
        if (!d) return null;
        try {
            const result = d[method](undefined, options);
            return result === "Invalid Date" ? null : result;
        } catch { return null; }
    }

    _formatDuration(dur) {
        if (!dur) return "";
        const s = parseInt(dur, 10);
        if (isNaN(s)) return "";
        const m = Math.floor(s / 60);
        const sec = s % 60;
        return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
    }

    _render() {
        if (!this._config) return;

        const state = this._hass ? this._hass.states[this._config.camera_entity] : null;
        const name = this._config.title || (state ? state.attributes.friendly_name : "Agent DVR");

        this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .card {
          background: var(--ha-card-background, var(--card-background-color, #fff));
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.15));
          overflow: hidden;
          color: var(--primary-text-color);
        }
        .tabs {
          display: flex;
          border-bottom: 1px solid var(--divider-color, #e0e0e0);
          padding: 0 8px;
        }
        .tab {
          flex: 1;
          text-align: center;
          padding: 12px 8px;
          cursor: pointer;
          font-size: 0.9em;
          font-weight: 500;
          color: var(--secondary-text-color);
          border-bottom: 2px solid transparent;
          transition: color 0.2s, border-color 0.2s;
          user-select: none;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
        }
        .tab:hover { color: var(--primary-text-color); }
        .tab.active {
          color: var(--primary-color);
          border-bottom-color: var(--primary-color);
        }
        .tab svg { width: 18px; height: 18px; fill: currentColor; }
        .content { position: relative; }

        /* Live */
        .live-container { position: relative; width: 100%; background: #000; }
        .live-container img { display: block; width: 100%; height: auto; }
        .status-bar {
          display: flex; gap: 12px; padding: 8px 16px;
          font-size: 0.8em; color: var(--secondary-text-color);
          background: var(--secondary-background-color, #f5f5f5);
        }
        .status-dot {
          display: inline-block; width: 8px; height: 8px;
          border-radius: 50%; margin-right: 4px; vertical-align: middle;
        }
        .status-dot.on { background: #4caf50; }
        .status-dot.off { background: #9e9e9e; }
        .status-dot.alert { background: #f44336; }

        /* Player */
        .player-container { position: relative; background: #000; }
        .player-container video { display: block; width: 100%; }
        .player-close {
          position: absolute; top: 8px; right: 8px;
          background: rgba(0,0,0,0.6); color: #fff; border: none;
          border-radius: 50%; width: 32px; height: 32px; cursor: pointer;
          font-size: 18px; display: flex; align-items: center; justify-content: center;
        }

        /* Timeline recordings */
        .timeline { max-height: 600px; overflow-y: auto; }
        .timeline-day { margin-bottom: 4px; }
        .timeline-date {
          font-size: 0.82em; font-weight: 600; color: var(--primary-color);
          padding: 8px 16px 4px 16px;
          border-bottom: 1px solid var(--divider-color, #e8e8e8);
          position: sticky; top: 0; z-index: 1;
          background: var(--ha-card-background, var(--card-background-color, #fff));
        }
        .tl-item {
          display: flex; align-items: center; gap: 12px;
          padding: 8px 16px; cursor: pointer;
          border-bottom: 1px solid var(--divider-color, #f0f0f0);
          transition: background 0.15s;
        }
        .tl-item:hover { background: var(--secondary-background-color, #f5f5f5); }
        .tl-thumb {
          width: 96px; height: 54px; border-radius: 6px;
          object-fit: cover; background: #222; flex-shrink: 0;
        }
        .tl-play-icon {
          position: absolute; width: 96px; height: 54px;
          display: flex; align-items: center; justify-content: center;
          pointer-events: none;
        }
        .tl-play-icon svg {
          width: 28px; height: 28px; fill: rgba(255,255,255,0.85);
          filter: drop-shadow(0 1px 3px rgba(0,0,0,0.5));
        }
        .tl-thumb-wrap { position: relative; flex-shrink: 0; }
        .tl-body { flex: 1; min-width: 0; }
        .tl-time { font-size: 0.85em; font-weight: 600; }
        .tl-meta {
          font-size: 0.78em; color: var(--secondary-text-color); margin-top: 2px;
          display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
        }
        .tl-tag {
          display: inline-block; background: var(--primary-color); color: #fff;
          font-size: 0.7em; padding: 1px 6px; border-radius: 8px;
        }

        .loading, .empty {
          display: flex; align-items: center; justify-content: center;
          min-height: 200px; color: var(--secondary-text-color); font-size: 0.9em;
        }
      </style>

      <div class="card">
        <div class="tabs">
          <div class="tab ${this._activeTab === "live" ? "active" : ""}" data-tab="live">
            <svg viewBox="0 0 24 24"><path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>
            Live
          </div>
          <div class="tab ${this._activeTab === "recordings" ? "active" : ""}" data-tab="recordings">
            <svg viewBox="0 0 24 24"><path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/></svg>
            Recordings
          </div>
        </div>
        <div class="content" id="content">
          ${this._renderContent(state)}
        </div>
      </div>
    `;

        this.shadowRoot.querySelectorAll(".tab").forEach((tab) => {
            tab.addEventListener("click", () => this._switchTab(tab.dataset.tab));
        });

        const content = this.shadowRoot.querySelector("#content");
        if (content) {
            content.addEventListener("click", async (e) => {
                const item = e.target.closest(".tl-item");
                if (item) {
                    const idx = parseInt(item.dataset.idx, 10);
                    if (!isNaN(idx) && this._recordings[idx]) {
                        this._playRecording(this._recordings[idx]);
                    }
                    return;
                }
                const closeBtn = e.target.closest(".player-close");
                if (closeBtn) {
                    this._stopPlayback();
                    return;
                }
            });
        }

        if (this._activeTab === "live") {
            this._startLiveRefresh();
        }
    }

    _renderContent(state) {
        switch (this._activeTab) {
            case "live": return this._renderLive(state);
            case "recordings": return this._renderRecordings();
            default: return "";
        }
    }

    _renderLive(state) {
        if (!state) return '<div class="empty">Camera unavailable</div>';

        const token = state.attributes.access_token;
        const imgUrl = `/api/camera_proxy/${this._config.camera_entity}?token=${token}`;
        const isRecording = state.attributes.recording || false;
        const detected = state.attributes.detected || false;
        const connected = state.attributes.connected !== false;

        return `
      <div class="live-container">
        <img id="live-image" src="${imgUrl}" alt="Live view" />
      </div>
      <div class="status-bar">
        <span><span class="status-dot ${connected ? "on" : "off"}"></span>${connected ? "Connected" : "Disconnected"}</span>
        ${isRecording ? '<span><span class="status-dot alert"></span>Recording</span>' : ""}
        ${detected ? '<span><span class="status-dot alert"></span>Motion</span>' : ""}
      </div>
    `;
    }

    _renderRecordings() {
        if (this._loading) return '<div class="loading">Loading recordings...</div>';
        if (this._error) return `<div class="empty" style="color:var(--error-color,#db4437)">${this._escHtml(this._error)}</div>`;

        if (this._playingRecording) {
            return `
        <div class="player-container">
          <video controls autoplay src="${this._playingRecording.url}"></video>
          <button class="player-close">&times;</button>
        </div>
      `;
        }

        if (this._recordings.length === 0) {
            return '<div class="empty">No recordings found</div>';
        }

        const info = this._getEntryInfo();
        const entryId = info ? info.entryId : "";
        const oid = info ? info.oid : "";
        const ot = info ? info.ot : "";

        // Build events with parsed dates
        const events = this._recordings.map((rec, idx) => {
            const ts = this._extractTimestamp(rec);
            const d = this._parseTimestamp(ts);
            return { rec, idx, date: d, ts };
        });

        // Sort newest first
        events.sort((a, b) => {
            const ta = a.date ? a.date.getTime() : 0;
            const tb = b.date ? b.date.getTime() : 0;
            return tb - ta;
        });

        // Group by day
        const groups = {};
        for (const ev of events) {
            const key = this._safeDateStr(ev.date, "toLocaleDateString", {
                weekday: "long", year: "numeric", month: "long", day: "numeric",
            }) || "Unknown date";
            if (!groups[key]) groups[key] = [];
            groups[key].push(ev);
        }

        const playIcon = `<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>`;

        let html = "";
        for (const [date, dayEvents] of Object.entries(groups)) {
            html += `<div class="timeline-day">`;
            html += `<div class="timeline-date">${this._escHtml(date)}</div>`;
            for (const ev of dayEvents) {
                const rec = ev.rec;
                const fn = rec.fn || rec.filename || "";
                const thumbBase = fn.replace(/\.[^.]+$/, "");
                const thumbUrl = fn ? `/api/agent_dvr_enhanced/thumbnail/${entryId}/${oid}/${thumbBase}.jpg` : "";
                const timeStr = this._safeDateStr(ev.date, "toLocaleTimeString", {
                    hour: "2-digit", minute: "2-digit", second: "2-digit",
                }) || "";
                const dur = this._formatDuration(rec.duration || rec.dur || rec.d);
                const tags = rec.tags || rec.tag || "";

                html += `
          <div class="tl-item" data-idx="${ev.idx}">
            <div class="tl-thumb-wrap">
              ${thumbUrl
                        ? `<img class="tl-thumb" data-sign-url="${thumbUrl}" alt="" onerror="this.style.background='#333'" />`
                        : `<div class="tl-thumb"></div>`}
              <div class="tl-play-icon">${playIcon}</div>
            </div>
            <div class="tl-body">
              <div class="tl-time">${this._escHtml(timeStr || `Recording ${ev.idx + 1}`)}</div>
              <div class="tl-meta">
                ${dur ? `<span>${dur}</span>` : ""}
                ${tags ? `<span class="tl-tag">${this._escHtml(tags)}</span>` : ""}
              </div>
            </div>
          </div>
        `;
            }
            html += `</div>`;
        }

        // After returning HTML, schedule signing of thumbnail URLs
        setTimeout(() => this._signThumbnails(), 0);

        return `<div class="timeline">${html}</div>`;
    }

    async _signThumbnails() {
        const imgs = this.shadowRoot.querySelectorAll("img[data-sign-url]");
        for (const img of imgs) {
            const url = img.getAttribute("data-sign-url");
            if (!url) continue;
            img.removeAttribute("data-sign-url");
            const signed = await this._signUrl(url);
            img.src = signed;
        }
    }

    _escHtml(str) {
        const div = document.createElement("div");
        div.textContent = str || "";
        return div.innerHTML;
    }

    disconnectedCallback() {
        this._stopLiveRefresh();
    }

    getCardSize() {
        return 5;
    }

    static getStubConfig() {
        return { camera_entity: "" };
    }
}

customElements.define("agent-dvr-card", AgentDVRCard);

window.customCards = window.customCards || [];
window.customCards.push({
    type: "agent-dvr-card",
    name: "Agent DVR Card",
    description: "Live view and recordings for Agent DVR cameras",
    preview: false,
    documentationURL: "https://github.com/jensdufour/ha-agent-dvr-enhanced",
});
