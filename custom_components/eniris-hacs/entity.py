"""Base entity for the Eniris HACS integration."""

import logging
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)


class EnirisHacsEntity(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]]):
    """Base class for Eniris HACS entities."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        device_data: dict[
            str, Any
        ],  # This is the primary device data from processed_devices
        child_device_data: dict[str, Any]
        | None = None,  # For entities belonging to a child
    ):
        """Initialize the entity."""
        super().__init__(coordinator)
        self.primary_device_data = device_data
        self.child_device_data = (
            child_device_data  # This specific entity might represent a child
        )

        # Determine which device data to use for entity properties
        self._current_device_data = (
            child_device_data if child_device_data else device_data
        )

        self._properties = self._current_device_data.get("properties", {})
        self._node_id = self._properties.get("nodeId", "unknown_node")
        self._device_name_prefix = self._properties.get("name", self._node_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the Home Assistant device registry."""
        # If this entity is for a child device, it should be linked to the primary (parent) device.
        # The primary device (e.g., hybridInverter) is registered once.
        # Child device entities (e.g., sensors for a battery linked to that inverter)
        # will use the primary device's identifiers but can have their own unique_id for the entity itself.

        primary_properties = self.primary_device_data.get("properties", {})
        primary_node_id = primary_properties.get("nodeId", "unknown_parent_node")
        primary_name = primary_properties.get("name", primary_node_id)

        info_block = primary_properties.get("info", {})
        model = info_block.get(
            "model", primary_properties.get("nodeType", "Generic Device")
        )
        manufacturer = info_block.get("manufacturer", MANUFACTURER)
        sw_version = info_block.get("protocolDriverParameters", {}).get(
            "firmwareVersion"
        )
        serial_number = info_block.get(
            "serialNumber", primary_node_id
        )  # Use nodeId if SN not available

        device_identifiers = {(DOMAIN, primary_node_id)}

        # If this entity represents a child device, its unique HA device entry
        # should be via the parent.
        # However, the entity itself will be distinct.
        # The HA "device" is the physical unit (e.g. inverter).
        # Entities are sensors/controls *on* that device or its sub-components.

        # If child_device_data is present, this entity is for a component of the primary device
        if self.child_device_data:
            # The device_info still refers to the PARENT device in HA.
            # The entity's unique_id will differentiate it.
            pass  # device_info remains that of the primary_device_data

        return DeviceInfo(
            identifiers=device_identifiers,
            name=primary_name,
            manufacturer=manufacturer,
            model=model,
            sw_version=sw_version,
            # via_device: Use this if a device is truly separate but connected via another.
            # For children that are integral parts (like an inverter's battery module),
            # they are usually represented as entities of the main device.
            # If solarOptimizer is a distinct physical unit paired with an inverter,
            # then 'via_device' might be appropriate for the optimizer's HA device.
            # For now, we'll treat children as components whose sensors belong to the parent device.
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Availability is handled by the coordinator.
        # You can add specific checks here if needed, e.g., if a device disappears from the API.
        # Check if our specific device (or its parent if this is a child entity) is in the coordinator data
        if (
            self.coordinator.data
            and self.primary_device_data.get("properties", {}).get("nodeId")
            in self.coordinator.data
        ):
            # If it's a child entity, also check if the child data still exists (it should if parent exists and hierarchy is stable)
            if self.child_device_data:
                parent_from_coordinator = self.coordinator.data[
                    self.primary_device_data.get("properties", {}).get("nodeId")
                ]
                child_node_id_to_find = self.child_device_data.get(
                    "properties", {}
                ).get("nodeId")
                found_child_in_coordinator = False
                for child in parent_from_coordinator.get("_processed_children", []):
                    if (
                        child.get("properties", {}).get("nodeId")
                        == child_node_id_to_find
                    ):
                        found_child_in_coordinator = True
                        break
                return super().available and found_child_in_coordinator
            return super().available
        return False  # Parent device not in coordinator data

    def _get_current_device_data_from_coordinator(self) -> dict[str, Any] | None:
        """Safely get the most up-to-date data for this entity's device from the coordinator."""
        if not self.coordinator.data:
            return None

        parent_node_id = self.primary_device_data.get("properties", {}).get("nodeId")
        parent_data_from_coordinator = self.coordinator.data.get(parent_node_id)

        if not parent_data_from_coordinator:
            return None  # Parent device not found

        if self.child_device_data:
            child_node_id = self.child_device_data.get("properties", {}).get("nodeId")
            for child in parent_data_from_coordinator.get("_processed_children", []):
                if child.get("properties", {}).get("nodeId") == child_node_id:
                    return child  # Return the child data from the coordinator
            return None  # Specific child not found under parent in coordinator data

        return parent_data_from_coordinator  # Return the parent data itself
