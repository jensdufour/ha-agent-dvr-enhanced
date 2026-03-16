"""DataUpdateCoordinator for Agent DVR Enhanced."""

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import AgentDVRApiClient, AgentDVRApiError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class AgentDVRCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage polling AgentDVR for device state."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AgentDVRApiClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.client = client
        self.server_info: dict[str, Any] = {}
        self.devices: list[dict[str, Any]] = []

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from AgentDVR."""
        try:
            objects_data = await self.client.get_objects()
            status_data = await self.client.get_status()
        except AgentDVRApiError as err:
            raise UpdateFailed(
                f"Error communicating with AgentDVR: {err}"
            ) from err

        self.server_info = status_data
        self.devices = objects_data.get("objectList", [])

        return {
            "status": status_data,
            "devices": self.devices,
        }
