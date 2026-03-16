"""Binary sensor platform for Agent DVR Enhanced."""

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    """Set up AgentDVR binary sensor entities."""
    coordinator: AgentDVRCoordinator = hass.data[DOMAIN][entry.entry_id]

    sensors: list[BinarySensorEntity] = []
    for device_data in coordinator.devices:
        if int(device_data.get("typeID", 0)) == OBJECT_TYPE_CAMERA:
            sensors.append(
                AgentDVRMotionSensor(coordinator, entry, device_data)
            )
            sensors.append(
                AgentDVRAlertSensor(coordinator, entry, device_data)
            )
            sensors.append(
                AgentDVRRecordingSensor(coordinator, entry, device_data)
            )

    async_add_entities(sensors)


class _AgentDVRBinarySensor(
    CoordinatorEntity[AgentDVRCoordinator], BinarySensorEntity
):
    """Base class for AgentDVR binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AgentDVRCoordinator,
        entry: ConfigEntry,
        device_data: dict[str, Any],
        suffix: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._oid = int(device_data["id"])
        self._ot = int(device_data["typeID"])
        self._device_data = device_data
        self._entry = entry
        self._attr_unique_id = (
            f"{entry.entry_id}_{self._oid}_{self._ot}_{suffix}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Link this sensor to the same device as the camera."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"{self._entry.entry_id}_{self._oid}_{self._ot}")
            },
        )

    def _get_current_device(self) -> dict[str, Any]:
        """Return the freshest device data from the coordinator."""
        if self.coordinator.data:
            for device in self.coordinator.data.get("devices", []):
                if int(device.get("id", 0)) == self._oid:
                    return device
        return self._device_data


class AgentDVRMotionSensor(_AgentDVRBinarySensor):
    """Binary sensor that turns on when motion is detected."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_name = "Motion"

    def __init__(
        self,
        coordinator: AgentDVRCoordinator,
        entry: ConfigEntry,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the motion sensor."""
        super().__init__(coordinator, entry, device_data, "motion")

    @property
    def is_on(self) -> bool:
        """Return True when motion is detected."""
        # Check MQTT instant state first
        mqtt_val = self.coordinator.get_device_state(self._oid, "detected")
        if mqtt_val is not False:
            return mqtt_val
        device = self._get_current_device()
        return device.get("data", {}).get("detected", False)


class AgentDVRAlertSensor(_AgentDVRBinarySensor):
    """Binary sensor that turns on when an alert fires."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_name = "Alert"

    def __init__(
        self,
        coordinator: AgentDVRCoordinator,
        entry: ConfigEntry,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the alert sensor."""
        super().__init__(coordinator, entry, device_data, "alert")

    @property
    def is_on(self) -> bool:
        """Return True when an alert is active."""
        # Check MQTT instant state first
        mqtt_val = self.coordinator.get_device_state(self._oid, "alerted")
        if mqtt_val is not False:
            return mqtt_val
        device = self._get_current_device()
        return device.get("data", {}).get("alerted", False)


class AgentDVRRecordingSensor(_AgentDVRBinarySensor):
    """Binary sensor that turns on when the camera is recording."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Recording"

    def __init__(
        self,
        coordinator: AgentDVRCoordinator,
        entry: ConfigEntry,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the recording sensor."""
        super().__init__(coordinator, entry, device_data, "recording")

    @property
    def is_on(self) -> bool:
        """Return True when the camera is recording."""
        # Check MQTT instant state first
        mqtt_val = self.coordinator.get_device_state(self._oid, "recording")
        if mqtt_val is not False:
            return mqtt_val
        device = self._get_current_device()
        return device.get("data", {}).get("recording", False)
