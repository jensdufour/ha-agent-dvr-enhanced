/**
 * Agent DVR Card - Custom Lovelace card for Agent DVR Enhanced
 * Version: 1.1.0
 * Provides Live, Timeline, and Recordings views.
 */

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
        this._alerts = [];
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
        } catch (err) {
            console.error("Error resolving entry ID:", err);
        }
    }

    _updateCameraImage() {
        if (this._activeTab !== "live") return;
        const img = this.shadowRoot.querySelector("#live-image");
        if (!img || !this._hass) return;
        const state = this._hass.states[this._config.camera_entity];
        if (!state) return;
        const token = state.attributes.access_token;
        const url = `/api/camera_proxy/${this._config.camera_entity}?token=${token}&t=${Date.now()}`;
        img.src = url;
    }

    async _switchTab(tab) {
        this._activeTab = tab;
        this._playingRecording = null;
        this._render();

        if (tab === "live") {
            this._updateCameraImage();
            this._startLiveRefresh();
        } else {
            this._stopLiveRefresh();
        }

        await this._resolveEntryId();

        if (tab === "recordings" && this._recordings.length === 0) {
            this._fetchRecordings();
        }
        if (tab === "timeline") {
            this._fetchAlerts();
            if (this._recordings.length === 0) {
                this._fetchRecordings();
            }
        }
    }

    _startLiveRefresh() {
        this._stopLiveRefresh();
        this._refreshInterval = setInterval(() => {
            this._updateCameraImage();
        }, 1000);
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
            const resp = await this._hass.callApi(
                "GET",
                `agent_dvr_enhanced/events/${info.entryId}/${info.oid}/${info.ot}`
            );
            this._recordings = Array.isArray(resp) ? resp : [];
        } catch (err) {
            console.error("Error fetching recordings:", err);
            this._error = `Error fetching recordings: ${err.message || err}`;
            this._recordings = [];
        }

        this._loading = false;
        this._render();
    }

    async _fetchAlerts() {
        const info = this._getEntryInfo();
        if (!info || !info.entryId) return;

        try {
            const resp = await this._hass.callApi(
                "GET",
                `agent_dvr_enhanced/alerts/${info.entryId}`
            );
            this._alerts = resp.alerts || resp || [];
        } catch (err) {
            console.error("Error fetching alerts:", err);
            this._alerts = [];
        }
        this._render();
    }

    _playRecording(rec) {
        const info = this._getEntryInfo();
        if (!info || !info.entryId) return;
        const fn = rec.fn || rec.filename || "";
        this._playingRecording = {
            ...rec,
            url: `/api/agent_dvr_enhanced/recording/${info.entryId}/${info.oid}/${info.ot}/${fn}`,
        };
        this._render();
    }

    _stopPlayback() {
        this._playingRecording = null;
        this._render();
    }

    _parseTimestamp(timestamp) {
        if (!timestamp && timestamp !== 0) return null;
        let ms;
        // .NET JSON date format: /Date(1234567890000)/
        if (typeof timestamp === "string") {
            const dotnetMatch = timestamp.match(/\/Date\(([-+]?\d+)\)\//);
            if (dotnetMatch) {
                ms = parseInt(dotnetMatch[1], 10);
            } else if (/^\d+$/.test(timestamp.trim())) {
                // Numeric string
                const num = parseInt(timestamp.trim(), 10);
                ms = num > 1e12 ? num : num * 1000;
            } else {
                // Try ISO 8601 or other parseable string
                ms = new Date(timestamp).getTime();
                // Fallback: try DD/MM/YYYY or DD-MM-YYYY patterns
                if (isNaN(ms)) {
                    const dmy = timestamp.match(/(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})[\sT]?(\d{1,2}:\d{2}:?\d{0,2})?\.?.*/);
                    if (dmy) {
                        const isoStr = `${dmy[3]}-${dmy[2].padStart(2, '0')}-${dmy[1].padStart(2, '0')}T${dmy[4] || '00:00:00'}`;
                        ms = new Date(isoStr).getTime();
                    }
                }
            }
        } else if (typeof timestamp === "number") {
            ms = timestamp > 1e12 ? timestamp : timestamp * 1000;
        } else {
            return null;
        }
        if (isNaN(ms)) return null;
        return new Date(ms);
    }

    _extractTimestamp(rec) {
        if (!rec || typeof rec !== "object") return null;
        // Try ALL fields on the object and return the first one that parses to a valid date
        for (const key of Object.keys(rec)) {
            const val = rec[key];
            if (val === undefined || val === null || val === "") continue;
            // Skip fields that are clearly not timestamps
            if (typeof val === "object") continue;
            if (typeof val === "boolean") continue;
            // Skip very long strings (likely filenames or data)
            if (typeof val === "string" && val.length > 50) continue;
            const d = this._parseTimestamp(val);
            if (d) {
                // Sanity check: year between 2000 and 2100
                const year = d.getFullYear();
                if (year >= 2000 && year <= 2100) return val;
            }
        }
        // Fallback: extract date from filename (e.g. "20260316_103000.mp4")
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

        const state = this._hass
            ? this._hass.states[this._config.camera_entity]
            : null;
        const name =
            this._config.title ||
            (state ? state.attributes.friendly_name : "Agent DVR");

        this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        .card {
          background: var(--ha-card-background, var(--card-background-color, #fff));
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.15));
          overflow: hidden;
          color: var(--primary-text-color);
        }
        .header {
          padding: 16px 16px 0 16px;
          font-size: 1.1em;
          font-weight: 500;
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
        .tab:hover {
          color: var(--primary-text-color);
        }
        .tab.active {
          color: var(--primary-color);
          border-bottom-color: var(--primary-color);
        }
        .tab svg {
          width: 18px;
          height: 18px;
          fill: currentColor;
        }
        .content {
          min-height: 240px;
          position: relative;
        }
        .live-container {
          position: relative;
          width: 100%;
          background: #000;
        }
        .live-container img {
          display: block;
          width: 100%;
          height: auto;
        }
        .status-bar {
          display: flex;
          gap: 12px;
          padding: 8px 16px;
          font-size: 0.8em;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color, #f5f5f5);
        }
        .status-dot {
          display: inline-block;
          width: 8px;
          height: 8px;
          border-radius: 50%;
          margin-right: 4px;
          vertical-align: middle;
        }
        .status-dot.on { background: #4caf50; }
        .status-dot.off { background: #9e9e9e; }
        .status-dot.alert { background: #f44336; }

        /* Recordings */
        .recordings-list {
          max-height: 500px;
          overflow-y: auto;
        }
        .rec-item {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 10px 16px;
          cursor: pointer;
          border-bottom: 1px solid var(--divider-color, #e8e8e8);
          transition: background 0.15s;
        }
        .rec-item:hover {
          background: var(--secondary-background-color, #f5f5f5);
        }
        .rec-thumb {
          width: 80px;
          height: 45px;
          border-radius: 4px;
          object-fit: cover;
          background: #222;
          flex-shrink: 0;
        }
        .rec-info {
          flex: 1;
          min-width: 0;
        }
        .rec-title {
          font-size: 0.9em;
          font-weight: 500;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .rec-meta {
          font-size: 0.78em;
          color: var(--secondary-text-color);
          margin-top: 2px;
        }
        .rec-tags {
          display: inline-block;
          background: var(--primary-color);
          color: #fff;
          font-size: 0.7em;
          padding: 1px 6px;
          border-radius: 8px;
          margin-left: 6px;
        }

        /* Player */
        .player-container {
          position: relative;
          background: #000;
        }
        .player-container video {
          display: block;
          width: 100%;
          max-height: 400px;
        }
        .player-close {
          position: absolute;
          top: 8px;
          right: 8px;
          background: rgba(0,0,0,0.6);
          color: #fff;
          border: none;
          border-radius: 50%;
          width: 32px;
          height: 32px;
          cursor: pointer;
          font-size: 18px;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        /* Timeline */
        .timeline {
          padding: 16px;
          max-height: 500px;
          overflow-y: auto;
        }
        .timeline-day {
          margin-bottom: 16px;
        }
        .timeline-date {
          font-size: 0.85em;
          font-weight: 600;
          color: var(--primary-color);
          margin-bottom: 8px;
          padding-bottom: 4px;
          border-bottom: 1px solid var(--divider-color, #e8e8e8);
        }
        .timeline-events {
          position: relative;
          padding-left: 20px;
        }
        .timeline-events::before {
          content: '';
          position: absolute;
          left: 6px;
          top: 0;
          bottom: 0;
          width: 2px;
          background: var(--divider-color, #ddd);
        }
        .timeline-event {
          position: relative;
          padding: 6px 0 6px 12px;
          cursor: pointer;
          transition: background 0.1s;
          border-radius: 4px;
        }
        .timeline-event:hover {
          background: var(--secondary-background-color, #f5f5f5);
        }
        .timeline-event::before {
          content: '';
          position: absolute;
          left: -17px;
          top: 14px;
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: var(--primary-color);
          border: 2px solid var(--ha-card-background, #fff);
        }
        .timeline-event.alert-event::before {
          background: #f44336;
        }
        .tl-time {
          font-size: 0.8em;
          font-weight: 600;
          color: var(--primary-text-color);
        }
        .tl-detail {
          font-size: 0.78em;
          color: var(--secondary-text-color);
          margin-top: 1px;
        }

        .loading, .empty {
          display: flex;
          align-items: center;
          justify-content: center;
          min-height: 200px;
          color: var(--secondary-text-color);
          font-size: 0.9em;
        }
      </style>

      <div class="card">
        <div class="header">${this._escHtml(name)} <span style="font-size:0.6em;color:var(--secondary-text-color)">v1.1.0</span></div>
        <div class="tabs">
          <div class="tab ${this._activeTab === "live" ? "active" : ""}" data-tab="live">
            <svg viewBox="0 0 24 24"><path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>
            Live
          </div>
          <div class="tab ${this._activeTab === "timeline" ? "active" : ""}" data-tab="timeline">
            <svg viewBox="0 0 24 24"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg>
            Timeline
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

        // Attach tab click handlers
        this.shadowRoot.querySelectorAll(".tab").forEach((tab) => {
            tab.addEventListener("click", () => {
                this._switchTab(tab.dataset.tab);
            });
        });

        // Attach recording item click handlers
        this.shadowRoot.querySelectorAll(".rec-item").forEach((item) => {
            item.addEventListener("click", () => {
                const idx = parseInt(item.dataset.idx, 10);
                if (!isNaN(idx) && this._recordings[idx]) {
                    this._playRecording(this._recordings[idx]);
                }
            });
        });

        // Attach timeline event click handlers
        this.shadowRoot.querySelectorAll(".timeline-event").forEach((item) => {
            item.addEventListener("click", () => {
                const idx = parseInt(item.dataset.idx, 10);
                if (!isNaN(idx) && this._recordings[idx]) {
                    this._activeTab = "recordings";
                    this._playRecording(this._recordings[idx]);
                }
            });
        });

        // Close button
        const closeBtn = this.shadowRoot.querySelector(".player-close");
        if (closeBtn) {
            closeBtn.addEventListener("click", () => this._stopPlayback());
        }

        // Start live refresh if on live tab
        if (this._activeTab === "live") {
            this._startLiveRefresh();
        }
    }

    _renderContent(state) {
        switch (this._activeTab) {
            case "live":
                return this._renderLive(state);
            case "timeline":
                return this._renderTimeline();
            case "recordings":
                return this._renderRecordings();
            default:
                return "";
        }
    }

    _renderLive(state) {
        if (!state) {
            return '<div class="empty">Camera unavailable</div>';
        }

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
        if (this._loading) {
            return '<div class="loading">Loading recordings...</div>';
        }

        if (this._error) {
            return `<div class="empty" style="color: var(--error-color, #db4437);">${this._escHtml(this._error)}</div>`;
        }

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

        // Debug: show raw event structure
        const debugInfo = this._recordings.length > 0
            ? `<div style="padding:8px 16px;font-size:0.75em;color:var(--secondary-text-color);background:var(--secondary-background-color);overflow-x:auto;white-space:pre-wrap;max-height:150px;overflow-y:auto;"><strong>Debug (first event keys):</strong>\n${this._escHtml(JSON.stringify(this._recordings[0], null, 2))}</div>`
            : "";

        const info = this._getEntryInfo();
        const entryId = info ? info.entryId : "";
        const oid = info ? info.oid : "";

        const items = this._recordings.map((rec, idx) => {
            const fn = rec.fn || rec.filename || "";
            const thumbBase = fn.replace(/\.[^.]+$/, "");
            const thumbUrl = `/api/agent_dvr_enhanced/thumbnail/${entryId}/${oid}/${thumbBase}.jpg`;
            const time = this._formatTime(this._extractTimestamp(rec));
            const dur = this._formatDuration(rec.duration || rec.dur || rec.d);
            const tags = rec.tags || rec.tag || "";

            return `
        <div class="rec-item" data-idx="${idx}">
          <img class="rec-thumb" src="${thumbUrl}" alt="" loading="lazy"
               onerror="this.style.background='#333'" />
          <div class="rec-info">
            <div class="rec-title">${this._escHtml(time || fn)}</div>
            <div class="rec-meta">
              ${dur ? dur : ""}
              ${tags ? `<span class="rec-tags">${this._escHtml(tags)}</span>` : ""}
            </div>
          </div>
        </div>
      `;
        });

        return `<div class="recordings-list">${items.join("")}</div>`;
    }

    _safeDateStr(d, method, options) {
        if (!d) return null;
        try {
            const result = d[method](undefined, options);
            if (result === "Invalid Date") return null;
            return result;
        } catch {
            return null;
        }
    }

    _renderTimeline() {
        if (this._loading) {
            return '<div class="loading">Loading timeline...</div>';
        }

        if (this._error) {
            return `<div class="empty" style="color: var(--error-color, #db4437);">${this._escHtml(this._error)}</div>`;
        }

        // Combine recordings and alerts, group by day
        const events = [];

        for (let i = 0; i < this._recordings.length; i++) {
            const rec = this._recordings[i];
            const ts = this._extractTimestamp(rec);
            events.push({
                type: "recording",
                timestamp: ts,
                label: rec.tags || rec.tag || "Recording",
                duration: rec.duration || rec.dur || rec.d,
                idx: i,
            });
        }

        for (const alert of this._alerts) {
            events.push({
                type: "alert",
                timestamp: this._extractTimestamp(alert),
                label: alert.reason || alert.msg || "Alert",
                duration: null,
                idx: -1,
            });
        }

        // Sort newest first
        events.sort((a, b) => {
            const da = this._parseTimestamp(a.timestamp);
            const db = this._parseTimestamp(b.timestamp);
            const ta = da ? da.getTime() : 0;
            const tb = db ? db.getTime() : 0;
            return tb - ta;
        });

        if (events.length === 0) {
            return '<div class="empty">No events found</div>';
        }

        // Debug: show raw first recording with all keys and types
        const debugRec = this._recordings.length > 0 ? this._recordings[0] : null;
        let debugTs = "";
        if (debugRec) {
            const keys = Object.keys(debugRec);
            const fieldInfo = keys.map(k => `${k} (${typeof debugRec[k]}): ${JSON.stringify(debugRec[k])}`).join("\\n");
            const extracted = this._extractTimestamp(debugRec);
            const parsed = this._parseTimestamp(extracted);
            debugTs = `<div style="padding:8px 16px;font-size:0.75em;color:var(--secondary-text-color);background:var(--secondary-background-color);overflow-x:auto;white-space:pre-wrap;max-height:250px;overflow-y:auto;">` +
                `<strong>v1.1.0 | Fields (${keys.length}):</strong>\\n${this._escHtml(fieldInfo)}\\n\\n` +
                `<strong>Extracted:</strong> ${this._escHtml(JSON.stringify(extracted))}\\n` +
                `<strong>Parsed:</strong> ${this._escHtml(String(parsed))}</div>`;
        }

        // Group by date
        const groups = {};
        for (const ev of events) {
            const d = this._parseTimestamp(ev.timestamp);
            const key = this._safeDateStr(d, "toLocaleDateString", {
                weekday: "long",
                year: "numeric",
                month: "long",
                day: "numeric",
            }) || "Unknown date";
            if (!groups[key]) groups[key] = [];
            groups[key].push({ ...ev, date: d });
        }

        let html = "";
        for (const [date, dayEvents] of Object.entries(groups)) {
            html += `<div class="timeline-day">`;
            html += `<div class="timeline-date">${this._escHtml(date)}</div>`;
            html += `<div class="timeline-events">`;
            for (const ev of dayEvents) {
                const timeStr = this._safeDateStr(ev.date, "toLocaleTimeString", {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                }) || "--:--:--";
                const dur = ev.duration ? ` (${this._formatDuration(ev.duration)})` : "";
                const cls = ev.type === "alert" ? "timeline-event alert-event" : "timeline-event";
                const clickable = ev.idx >= 0 ? `data-idx="${ev.idx}"` : "";
                html += `
          <div class="${cls}" ${clickable}>
            <div class="tl-time">${this._escHtml(timeStr)}</div>
            <div class="tl-detail">${this._escHtml(ev.label)}${dur}</div>
          </div>
        `;
            }
            html += `</div></div>`;
        }

        return `${debugTs}<div class="timeline">${html}</div>`;
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
    description: "Live view, timeline, and recordings for Agent DVR cameras",
    preview: false,
    documentationURL: "https://github.com/jensdufour/ha-agent-dvr-enhanced",
});
