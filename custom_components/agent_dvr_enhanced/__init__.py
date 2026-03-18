"""Agent DVR Enhanced integration."""

import hashlib
import logging
import os
import re

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AgentDVRApiClient
from .const import DOMAIN
from .coordinator import AgentDVRCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["camera", "binary_sensor"]

SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-\.]+$")

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Agent DVR Enhanced from a config entry."""
    session = async_get_clientsession(hass)
    client = AgentDVRApiClient(entry.data["server_url"], session)

    coordinator = AgentDVRCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up MQTT subscriptions for instant event detection
    await coordinator.setup_mqtt()

    # Register HTTP proxy views once
    try:
        if not hass.data.get(f"{DOMAIN}_views_registered"):
            js_path = os.path.join(os.path.dirname(__file__), "agent-dvr-card.js")
            hass.http.register_view(AgentDVRRecordingProxyView())
            hass.http.register_view(AgentDVRThumbnailProxyView())
            hass.http.register_view(AgentDVREventsApiView())
            hass.http.register_view(AgentDVRAlertsApiView())
            hass.http.register_view(AgentDVRCardJsView(js_path))
            hass.data[f"{DOMAIN}_views_registered"] = True
    except Exception:
        _LOGGER.warning("Could not register HTTP proxy views", exc_info=True)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class AgentDVRRecordingProxyView(HomeAssistantView):
    """Proxy view that serves AgentDVR recordings with HTTP Range support."""

    url = "/api/agent_dvr_enhanced/recording/{entry_id}/{oid}/{ot}/{filename:.+}"
    name = "api:agent_dvr_enhanced:recording"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, oid: str, ot: str, filename: str
    ) -> web.Response:
        """Serve a recording from AgentDVR with Range request support."""
        hass = request.app["hass"]

        if DOMAIN not in hass.data or entry_id not in hass.data[DOMAIN]:
            return web.Response(status=404, text="Integration not found")

        if not SAFE_FILENAME.match(filename):
            return web.Response(status=400, text="Invalid filename")

        try:
            oid_int = int(oid)
            ot_int = int(ot)
        except ValueError:
            return web.Response(status=400, text="Invalid parameters")

        coordinator: AgentDVRCoordinator = hass.data[DOMAIN][entry_id]

        try:
            data = await coordinator.client.get_recording_bytes(
                oid_int, ot_int, filename
            )
        except Exception:
            _LOGGER.exception("Error fetching recording %s", filename)
            return web.Response(status=502, text="Error fetching recording")

        total = len(data)
        content_type = "video/mp4"
        if filename.lower().endswith(".mkv"):
            content_type = "video/x-matroska"
        elif filename.lower().endswith(".webm"):
            content_type = "video/webm"

        range_header = request.headers.get("Range")
        if range_header:
            range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2)) if range_match.group(2) else total - 1
                end = min(end, total - 1)
                if start >= total:
                    return web.Response(
                        status=416,
                        headers={"Content-Range": f"bytes */{total}"},
                    )
                return web.Response(
                    body=data[start : end + 1],
                    status=206,
                    content_type=content_type,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{total}",
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(end - start + 1),
                    },
                )

        return web.Response(
            body=data,
            status=200,
            content_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(total),
            },
        )


class AgentDVRThumbnailProxyView(HomeAssistantView):
    """Proxy view that serves AgentDVR recording thumbnails through HA."""

    url = "/api/agent_dvr_enhanced/thumbnail/{entry_id}/{oid}/{filename:.+}"
    name = "api:agent_dvr_enhanced:thumbnail"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, oid: str, filename: str
    ) -> web.Response:
        """Serve a thumbnail from AgentDVR."""
        hass = request.app["hass"]

        if DOMAIN not in hass.data or entry_id not in hass.data[DOMAIN]:
            return web.Response(status=404, text="Integration not found")

        if not SAFE_FILENAME.match(filename):
            return web.Response(status=400, text="Invalid filename")

        try:
            oid_int = int(oid)
        except ValueError:
            return web.Response(status=400, text="Invalid parameters")

        coordinator: AgentDVRCoordinator = hass.data[DOMAIN][entry_id]

        try:
            data = await coordinator.client.get_thumbnail(oid_int, filename)
        except Exception:
            _LOGGER.exception("Error fetching thumbnail %s", filename)
            return web.Response(status=502, text="Error fetching thumbnail")

        return web.Response(body=data, content_type="image/jpeg")


class AgentDVREventsApiView(HomeAssistantView):
    """API view that returns recordings/events as JSON."""

    url = "/api/agent_dvr_enhanced/events/{entry_id}/{oid}/{ot}"
    name = "api:agent_dvr_enhanced:events"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, oid: str, ot: str
    ) -> web.Response:
        """Return events/recordings list for a camera."""
        hass = request.app["hass"]

        if DOMAIN not in hass.data or entry_id not in hass.data[DOMAIN]:
            return web.Response(status=404, text="Integration not found")

        try:
            oid_int = int(oid)
            ot_int = int(ot)
        except ValueError:
            return web.Response(status=400, text="Invalid parameters")

        coordinator: AgentDVRCoordinator = hass.data[DOMAIN][entry_id]

        try:
            events = await coordinator.client.get_events(oid=oid_int, ot=ot_int)
        except Exception:
            _LOGGER.exception("Error fetching events for oid=%s ot=%s", oid, ot)
            return web.Response(status=502, text="Error fetching events")

        _LOGGER.debug(
            "Events API: oid=%s ot=%s returned %d event(s)",
            oid, ot, len(events),
        )

        return web.json_response(events)


class AgentDVRAlertsApiView(HomeAssistantView):
    """API view that returns alerts as JSON."""

    url = "/api/agent_dvr_enhanced/alerts/{entry_id}"
    name = "api:agent_dvr_enhanced:alerts"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str
    ) -> web.Response:
        """Return alerts list."""
        hass = request.app["hass"]

        if DOMAIN not in hass.data or entry_id not in hass.data[DOMAIN]:
            return web.Response(status=404, text="Integration not found")

        coordinator: AgentDVRCoordinator = hass.data[DOMAIN][entry_id]

        try:
            alerts = await coordinator.client.get_alerts()
        except Exception:
            _LOGGER.exception("Error fetching alerts")
            return web.Response(status=502, text="Error fetching alerts")

        return web.json_response(alerts)


class AgentDVRCardJsView(HomeAssistantView):
    """Serve the custom card JavaScript file."""

    url = "/agent_dvr_enhanced/agent-dvr-card.js"
    name = "agent_dvr_enhanced:card_js"
    requires_auth = False

    def __init__(self, js_path: str) -> None:
        """Initialize with path to JS file."""
        self._js_path = js_path

    async def get(self, request: web.Request) -> web.Response:
        """Serve the JS file."""
        try:
            with open(self._js_path, encoding="utf-8") as fh:
                content = fh.read()
            content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
            return web.Response(
                body=content,
                content_type="application/javascript",
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "ETag": content_hash,
                },
            )
        except FileNotFoundError:
            return web.Response(status=404, text="Card JS not found")
