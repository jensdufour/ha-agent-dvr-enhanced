"""Camera platform for Agent DVR Enhanced."""

import logging
from typing import Any

from aiohttp import web

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, OBJECT_TYPE_CAMERA
from .coordinator import AgentDVRCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AgentDVR camera entities."""
    coordinator: AgentDVRCoordinator = hass.data[DOMAIN][entry.entry_id]

    cameras = [
        AgentDVRCamera(coordinator, entry, device_data)
        for device_data in coordinator.devices
        if int(device_data.get("typeID", 0)) == OBJECT_TYPE_CAMERA
    ]
    async_add_entities(cameras)


class AgentDVRCamera(CoordinatorEntity[AgentDVRCoordinator], Camera):
    """Camera entity backed by AgentDVR."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AgentDVRCoordinator,
        entry: ConfigEntry,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the camera."""
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)

        self._oid = int(device_data["id"])
        self._ot = int(device_data["typeID"])
        self._device_data = device_data
        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_{self._oid}_{self._ot}"
        self._attr_name = device_data.get("name", f"Camera {self._oid}")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to link entities under one device."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"{self._entry.entry_id}_{self._oid}_{self._ot}")
            },
            name=self._device_data.get("name", f"Camera {self._oid}"),
            manufacturer="iSpyConnect",
            model="AgentDVR Camera",
            sw_version=self.coordinator.server_info.get("version", "Unknown"),
            configuration_url=self.coordinator.client.server_url,
        )

    # ------------------------------------------------------------------
    # State properties
    # ------------------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        """Return True if the camera is currently recording."""
        device = self._get_current_device()
        return device.get("data", {}).get("recording", False)

    @property
    def motion_detection_enabled(self) -> bool:
        """Return True if motion detection is enabled."""
        device = self._get_current_device()
        return device.get("data", {}).get("detectorActive", False)

    @property
    def is_on(self) -> bool:
        """Return True if the camera is online."""
        device = self._get_current_device()
        return device.get("data", {}).get("online", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        device = self._get_current_device()
        data = device.get("data", {})
        return {
            "connected": data.get("connected", False),
            "alerts_active": data.get("alertsActive", False),
            "detected": data.get("detected", False),
            "alerted": data.get("alerted", False),
            "object_id": self._oid,
            "object_type": self._ot,
        }

    # ------------------------------------------------------------------
    # Image / stream
    # ------------------------------------------------------------------

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        try:
            return await self.coordinator.client.get_still_image(self._oid)
        except Exception:
            _LOGGER.debug("Error fetching still image for camera %s", self._oid)
            return None

    async def handle_async_mjpeg_stream(self, request):
        """Proxy the native MJPEG stream from AgentDVR through HA."""
        session = async_get_clientsession(self.hass)
        mjpeg_url = self.coordinator.client.get_mjpeg_url(self._oid)

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "multipart/x-mixed-replace;boundary=myboundary"},
        )
        await response.prepare(request)

        try:
            upstream = await session.get(mjpeg_url)
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
        except ConnectionResetError:
            pass
        finally:
            return response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_current_device(self) -> dict[str, Any]:
        """Return the freshest device data from the coordinator."""
        if self.coordinator.data:
            for device in self.coordinator.data.get("devices", []):
                if int(device.get("id", 0)) == self._oid:
                    return device
        return self._device_data
