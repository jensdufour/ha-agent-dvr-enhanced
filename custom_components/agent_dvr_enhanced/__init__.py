"""Agent DVR Enhanced integration."""

import logging
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

    # Register HTTP proxy views once
    if not hass.data.get(f"{DOMAIN}_views_registered"):
        hass.http.register_view(AgentDVRRecordingProxyView())
        hass.http.register_view(AgentDVRThumbnailProxyView())
        hass.data[f"{DOMAIN}_views_registered"] = True

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
