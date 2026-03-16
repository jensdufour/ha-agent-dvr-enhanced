"""DataUpdateCoordinator for Agent DVR Enhanced."""

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import AgentDVRApiClient, AgentDVRApiError
from .const import DOMAIN, MQTT_ROOT_TOPIC, SCAN_INTERVAL

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
        self._mqtt_unsubscribes: list[Any] = []
        # Map of lowercase device name -> device data for MQTT routing
        self._name_to_device: dict[str, dict[str, Any]] = {}
        # MQTT-driven instant state overrides: {oid: {field: value}}
        self.mqtt_state: dict[int, dict[str, Any]] = {}

    async def setup_mqtt(self) -> None:
        """Subscribe to Agent DVR MQTT topics if MQTT is available."""
        try:
            from homeassistant.components.mqtt import async_subscribe
        except ImportError:
            _LOGGER.debug("MQTT integration not available, skipping MQTT subscriptions")
            return

        if "mqtt" not in self.hass.config.components:
            _LOGGER.debug("MQTT not loaded, skipping MQTT subscriptions")
            return

        # Subscribe to all Agent DVR camera and microphone topics
        topics = [
            f"{MQTT_ROOT_TOPIC}/cameras/+/+",
            f"{MQTT_ROOT_TOPIC}/microphones/+/+",
        ]

        for topic in topics:
            try:
                unsub = await async_subscribe(
                    self.hass, topic, self._handle_mqtt_message, qos=0
                )
                self._mqtt_unsubscribes.append(unsub)
                _LOGGER.info("Subscribed to MQTT topic: %s", topic)
            except Exception:
                _LOGGER.warning("Failed to subscribe to MQTT topic: %s", topic, exc_info=True)

    @callback
    def _handle_mqtt_message(self, msg: Any) -> None:
        """Handle incoming MQTT message from Agent DVR."""
        topic = msg.topic
        payload = msg.payload

        # Parse topic: agentdvr/{type}/{name}/{event}
        parts = topic.split("/")
        if len(parts) < 4:
            return

        device_name = parts[2].lower()
        event_type = parts[3].lower()

        _LOGGER.debug(
            "MQTT message: topic=%s payload=%s device=%s event=%s",
            topic, payload, device_name, event_type,
        )

        # Find the device by name
        device = self._name_to_device.get(device_name)
        if not device:
            # Try to rebuild the mapping
            self._rebuild_name_map()
            device = self._name_to_device.get(device_name)
            if not device:
                _LOGGER.debug("No device found for MQTT name '%s'", device_name)
                return

        oid = int(device.get("id", 0))
        if oid not in self.mqtt_state:
            self.mqtt_state[oid] = {}

        payload_lower = payload.lower().strip() if isinstance(payload, str) else str(payload).lower().strip()
        is_true = payload_lower in ("true", "1", "on", "yes")

        if event_type == "alert":
            self.mqtt_state[oid]["alerted"] = is_true
            self.mqtt_state[oid]["detected"] = is_true
            _LOGGER.debug("MQTT: Device %s (oid=%d) alert=%s", device_name, oid, is_true)
        elif event_type == "motion":
            self.mqtt_state[oid]["detected"] = is_true
            _LOGGER.debug("MQTT: Device %s (oid=%d) motion=%s", device_name, oid, is_true)
        elif event_type == "motion_stopped":
            self.mqtt_state[oid]["detected"] = False
            _LOGGER.debug("MQTT: Device %s (oid=%d) motion_stopped", device_name, oid)
        elif event_type == "alert_stopped":
            self.mqtt_state[oid]["alerted"] = False
            _LOGGER.debug("MQTT: Device %s (oid=%d) alert_stopped", device_name, oid)
        elif event_type == "recording":
            self.mqtt_state[oid]["recording"] = is_true
        elif event_type == "recording_stopped":
            self.mqtt_state[oid]["recording"] = False
        else:
            _LOGGER.debug("MQTT: Unhandled event type '%s' for %s", event_type, device_name)
            return

        # Trigger an immediate coordinator update to push state to entities
        self.async_set_updated_data(self.data or {"status": self.server_info, "devices": self.devices})

    def _rebuild_name_map(self) -> None:
        """Rebuild the device name to device data mapping."""
        self._name_to_device = {}
        for device in self.devices:
            name = device.get("name", "").lower().replace(" ", "_")
            self._name_to_device[name] = device
            # Also map without underscores and with original name
            raw_name = device.get("name", "").lower()
            self._name_to_device[raw_name] = device

    def get_device_state(self, oid: int, field: str, fallback: Any = False) -> Any:
        """Get device state, preferring MQTT instant state over polled data."""
        mqtt = self.mqtt_state.get(oid, {})
        if field in mqtt:
            return mqtt[field]
        return fallback

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from AgentDVR."""
        try:
            objects_data = await self.client.get_objects()
            status_data = await self.client.get_status()
        except AgentDVRApiError as err:
            raise UpdateFailed(
                f"Error communicating with AgentDVR: {err}"
            ) from err
        except Exception as err:
            _LOGGER.exception("Unexpected error fetching AgentDVR data")
            raise UpdateFailed(
                f"Unexpected error: {err}"
            ) from err

        self.server_info = status_data
        self.devices = objects_data.get("objectList", [])
        self._rebuild_name_map()

        # Clear MQTT overrides on poll to re-sync with actual state
        self.mqtt_state.clear()

        return {
            "status": status_data,
            "devices": self.devices,
        }

    async def async_shutdown(self) -> None:
        """Unsubscribe from MQTT on shutdown."""
        for unsub in self._mqtt_unsubscribes:
            unsub()
        self._mqtt_unsubscribes.clear()
        await super().async_shutdown()
