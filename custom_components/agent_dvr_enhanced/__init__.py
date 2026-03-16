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
            hass.http.register_view(AgentDVRDebugApiView())
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
    """Proxy view that streams AgentDVR recordings through HA."""

    url = "/api/agent_dvr_enhanced/recording/{entry_id}/{oid}/{ot}/{filename:.+}"
    name = "api:agent_dvr_enhanced:recording"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, oid: str, ot: str, filename: str
    ) -> web.StreamResponse:
        """Stream a recording from AgentDVR."""
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
            upstream = await coordinator.client.stream_recording(
                oid_int, ot_int, filename
            )
        except Exception:
            _LOGGER.exception("Error fetching recording %s", filename)
            return web.Response(status=502, text="Error fetching recording")

        response = web.StreamResponse()
        response.content_type = upstream.content_type or "video/mp4"
        if upstream.content_length:
            response.content_length = upstream.content_length
        await response.prepare(request)

        try:
            async for chunk in upstream.content.iter_chunked(65536):
                await response.write(chunk)
        finally:
            upstream.close()

        return response


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

        _LOGGER.info(
            "Events API: oid=%s ot=%s returned %d event(s)%s",
            oid, ot, len(events),
            f", first keys={list(events[0].keys())}" if events and isinstance(events[0], dict) else "",
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


class AgentDVRDebugApiView(HomeAssistantView):
    """Debug view to inspect raw API responses from AgentDVR."""

    url = "/api/agent_dvr_enhanced/debug/{entry_id}/{oid}/{ot}"
    name = "api:agent_dvr_enhanced:debug"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, oid: str, ot: str
    ) -> web.Response:
        """Return raw events and alerts from AgentDVR for debugging."""
        hass = request.app["hass"]

        if DOMAIN not in hass.data or entry_id not in hass.data[DOMAIN]:
            return web.json_response({"error": "Integration not found"}, status=404)

        try:
            oid_int = int(oid)
            ot_int = int(ot)
        except ValueError:
            return web.json_response({"error": "Invalid parameters"}, status=400)

        coordinator: AgentDVRCoordinator = hass.data[DOMAIN][entry_id]
        result = {"oid": oid_int, "ot": ot_int}

        # Try multiple Agent DVR API paths to find recordings
        api_paths = [
            f"q/getEvents?oid={oid_int}&ot={ot_int}",
            f"q/getEvents?oid={oid_int}",
            "q/getEvents",
            f"command.cgi?cmd=getEvents&oid={oid_int}&ot={ot_int}",
            f"command.cgi?cmd=getEvents&oid={oid_int}",
            f"api/Journal?oid={oid_int}&ot={ot_int}",
            f"api/Journal?oid={oid_int}",
            f"command.cgi?cmd=getTimeline&oid={oid_int}&ot={ot_int}",
            f"command.cgi?cmd=getTimeline&oid={oid_int}",
            f"command.cgi?cmd=getMediaList&oid={oid_int}&ot={ot_int}",
            f"command.cgi?cmd=getMediaList&oid={oid_int}",
        ]

        probes = {}
        for path in api_paths:
            try:
                data = await coordinator.client._request_json(path)
                count = None
                data_type = type(data).__name__
                sample = None
                if isinstance(data, list):
                    count = len(data)
                    if data and isinstance(data[0], dict):
                        sample = list(data[0].keys())
                elif isinstance(data, dict):
                    # Check for nested lists
                    for key, val in data.items():
                        if isinstance(val, list):
                            count = len(val)
                            if val and isinstance(val[0], dict):
                                sample = list(val[0].keys())
                            break
                probes[path] = {
                    "type": data_type,
                    "count": count,
                    "sample_keys": sample,
                    "raw_preview": str(data)[:300],
                }
            except Exception as exc:
                probes[path] = {"error": str(exc)}

        result["probes"] = probes

        # Also get the server version and objects summary
        try:
            status = await coordinator.client.get_status()
            result["server_version"] = status.get("version", "unknown")
        except Exception as exc:
            result["server_version_error"] = str(exc)

        try:
            objs = await coordinator.client.get_objects()
            devices = objs.get("objectList", [])
            result["devices"] = [
                {"id": d.get("id"), "typeID": d.get("typeID"), "name": d.get("name")}
                for d in devices
            ]
        except Exception as exc:
            result["devices_error"] = str(exc)

        return web.json_response(result)
