"""Sensor platform for Eniris HACS integration."""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_HYBRID_INVERTER,
    DEVICE_TYPE_POWER_METER,
    DEVICE_TYPE_SOLAR_OPTIMIZER,
)
from .entity import EnirisHacsEntity

_LOGGER = logging.getLogger(__name__)

# Define sensor descriptions: (name_suffix, unit, device_class, state_class, value_fn, icon, entity_category)
# value_fn: a lambda that takes the device_data (properties.nodeInfluxSeries or properties.info)
#           and extracts the relevant value. This needs careful mapping.

# Example: Mapping fields from 'hybridInverterMetrics'
# "exportedEnergyDeltaTot_Wh", "actualPowerTot_W", "importedEnergyDeltaTot_Wh"
# These are often found in properties.nodeInfluxSeries.fields, but the actual values
# would need to be fetched from a different API endpoint (e.g., InfluxDB query based on those series).
# The current API only gives device structure, not live measurements.
# For this example, I will assume some values might be directly in `properties.info` or a simplified
# `measurements` block if the API were to provide it directly with the device info.
# **IMPORTANT**: The provided API output does NOT contain live sensor readings.
# It describes HOW to get them (nodeInfluxSeries). A real integration would need
# another API client part to query InfluxDB or a similar data source.
# For now, we will create placeholder sensors or sensors based on `properties.info`.

SENSOR_DESCRIPTIONS_COMMON = [
    # (key_in_info, name_suffix, unit, device_class, state_class, icon, entity_category)
    ("capacity_Wh", "Capacity", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL, "mdi:battery-high", None),
    ("nomPower_W", "Nominal Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:flash", None),
    # Add more based on `properties.info` if relevant
]

# Specific sensors based on nodeInfluxSeries fields (CONCEPTUAL - REQUIRES LIVE DATA FETCH)
# This part is highly speculative as we don't have live data.
# We'll define them, but they won't update without a mechanism to fetch actual measurements.
# The `value_fn` would typically parse a `measurements` block if the API provided it.
# For now, let's assume a hypothetical `latest_measurements` field in device_data for demonstration.

# (measurement_field_name, name_suffix, unit, device_class, state_class, icon, entity_category)
CONCEPTUAL_MEASUREMENT_SENSORS = {
    DEVICE_TYPE_HYBRID_INVERTER: [
        ("actualPowerTot_W", "Total Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:solar-power", None),
        ("exportedEnergyDeltaTot_Wh", "Exported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower-export", None),
        ("importedEnergyDeltaTot_Wh", "Imported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower-import", None),
        # "faultCodes1" - could be a diagnostic sensor
    ],
    DEVICE_TYPE_BATTERY: [
        ("stateOfCharge_frac", "State of Charge", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery", None),
        ("actualPowerTot_W", "Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:battery-charging", None),
        ("chargedEnergyDeltaTot_Wh", "Charged Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging", None),
        ("dischargedEnergyDeltaTot_Wh", "Discharged Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:battery-discharging", None),
    ],
    DEVICE_TYPE_SOLAR_OPTIMIZER: [
        ("actualPowerTot_W", "PV Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:solar-panel", None),
        ("producedEnergyDeltaTot_Wh", "PV Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:solar-panel-large", None),
    ],
    DEVICE_TYPE_POWER_METER: [
        # Total measurements
        ("actualPowerTot_W", "Total Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:gauge", None),
        ("exportedEnergyDeltaTot_Wh", "Exported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower-export", None),
        ("importedEnergyDeltaTot_Wh", "Imported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower-import", None),
        ("exportedAbsEnergyTot_Wh", "Total Exported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL, "mdi:transmission-tower-export", None),
        ("importedAbsEnergyTot_Wh", "Total Imported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL, "mdi:transmission-tower-import", None),
        ("reacPowerTot_VAr", "Total Reactive Power", "var", SensorDeviceClass.REACTIVE_POWER, SensorStateClass.MEASUREMENT, "mdi:flash", None),
        ("powerfactor", "Power Factor", None, None, SensorStateClass.MEASUREMENT, "mdi:flash", None),
        ("frequency_Hz", "Frequency", "Hz", SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT, "mdi:sine-wave", None),
        
        # Phase 1 measurements
        ("actualPowerL1_W", "Phase 1 Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:gauge", None),
        ("voltageL1N_V", "Phase 1 Voltage", "V", SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:lightning-bolt", None),
        ("currentL1_A", "Phase 1 Current", "A", SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:current-ac", None),
        
        # Phase 2 measurements
        ("actualPowerL2_W", "Phase 2 Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:gauge", None),
        ("voltageL2N_V", "Phase 2 Voltage", "V", SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:lightning-bolt", None),
        ("currentL2_A", "Phase 2 Current", "A", SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:current-ac", None),
        
        # Phase 3 measurements
        ("actualPowerL3_W", "Phase 3 Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:gauge", None),
        ("voltageL3N_V", "Phase 3 Voltage", "V", SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:lightning-bolt", None),
        ("currentL3_A", "Phase 3 Current", "A", SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:current-ac", None),
    ],
}


def get_value_from_info(data: Dict[str, Any], key: str) -> Any:
    """Extract value from device_data.properties.info."""
    return data.get("properties", {}).get("info", {}).get(key)

# Placeholder for actual measurement fetching logic
def get_value_from_conceptual_measurements(data: Dict[str, Any], key: str) -> Any:
    """
    Placeholder to extract value from a hypothetical 'latest_measurements' field.
    In a real scenario, this would involve querying based on nodeInfluxSeries.
    """
    # This is where you would look up the live value.
    # For now, we return None, so these sensors will be 'unknown'.
    # Example: return data.get("latest_measurements", {}).get(key)
    _LOGGER.debug("Attempting to get conceptual measurement for key '%s'. Live data fetch not implemented.", key)
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DataUpdateCoordinator = coordinator_data["coordinator"]
    # api_client: EnirisHacsApiClient = coordinator_data["api_client"] # If needed for direct calls

    entities_to_add: List[EnirisHacsSensor] = []

    # The coordinator.data should be the processed_devices dictionary
    if not coordinator.data:
        _LOGGER.warning("No data from coordinator, cannot set up sensors.")
        return

    for node_id, device_data in coordinator.data.items():
        properties = device_data.get("properties", {})
        node_type = properties.get("nodeType")
        device_info_block = properties.get("info", {})

        # 1. Add sensors based on properties.info (COMMON SENSORS)
        for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in SENSOR_DESCRIPTIONS_COMMON:
            if get_value_from_info(device_data, key) is not None:
                entities_to_add.append(
                    EnirisHacsSensor(
                        coordinator,
                        device_data, # Primary device data
                        entity_description_tuple=(key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                        value_extractor=get_value_from_info,
                        is_info_sensor=True
                    )
                )
        
        # 2. Add sensors based on conceptual measurements for the primary device
        if node_type in CONCEPTUAL_MEASUREMENT_SENSORS:
            for m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat in CONCEPTUAL_MEASUREMENT_SENSORS[node_type]:
                entities_to_add.append(
                    EnirisHacsSensor(
                        coordinator,
                        device_data, # Primary device data
                        entity_description_tuple=(m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                        value_extractor=get_value_from_conceptual_measurements
                    )
                )

        # 3. Add sensors for CHİLD devices (e.g., battery or PV attached to an inverter)
        # These sensors will be associated with the PARENT's HA device entry.
        for child_device_data in device_data.get("_processed_children", []):
            child_properties = child_device_data.get("properties", {})
            child_node_type = child_properties.get("nodeType")
            child_info_block = child_properties.get("info", {})

            # 3a. Common sensors for the child device (from its own info block)
            for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in SENSOR_DESCRIPTIONS_COMMON:
                if get_value_from_info(child_device_data, key) is not None:
                    entities_to_add.append(
                        EnirisHacsSensor(
                            coordinator,
                            device_data, # Parent device data for HA device linking
                            child_device_data=child_device_data, # Actual data source for this sensor
                            entity_description_tuple=(key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                            value_extractor=get_value_from_info, # Use info from child_device_data
                            is_info_sensor=True
                        )
                    )
            
            # 3b. Conceptual measurement sensors for the child device
            if child_node_type in CONCEPTUAL_MEASUREMENT_SENSORS:
                for m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat in CONCEPTUAL_MEASUREMENT_SENSORS[child_node_type]:
                    entities_to_add.append(
                        EnirisHacsSensor(
                            coordinator,
                            device_data, # Parent device data for HA device linking
                            child_device_data=child_device_data, # Actual data source for this sensor
                            entity_description_tuple=(m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                            value_extractor=get_value_from_conceptual_measurements
                        )
                    )

    if entities_to_add:
        _LOGGER.info("Adding %s sensor entities.", len(entities_to_add))
        async_add_entities(entities_to_add)
    else:
        _LOGGER.info("No sensor entities to add.")


class EnirisHacsSensor(EnirisHacsEntity, SensorEntity):
    """Representation of a Eniris HACS Sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        primary_device_data: Dict[str, Any],
        entity_description_tuple: Tuple, # (key, name_suffix, unit, dev_class, state_class, icon, ent_cat)
        value_extractor: Callable[[Dict[str, Any], str], Any],
        child_device_data: Optional[Dict[str, Any]] = None,
        is_info_sensor: bool = False, # True if sensor value comes from 'info' block
    ):
        """Initialize the sensor."""
        super().__init__(coordinator, primary_device_data, child_device_data)
        
        self._value_key = entity_description_tuple[0]
        self._name_suffix = entity_description_tuple[1]
        self._unit = entity_description_tuple[2]
        self._device_class = entity_description_tuple[3]
        self._state_class = entity_description_tuple[4]
        self._icon_override = entity_description_tuple[5]
        self._entity_category_override = entity_description_tuple[6]
        self._value_extractor = value_extractor
        self._is_info_sensor = is_info_sensor # Static sensors from 'info' don't need frequent updates from coordinator's live data part

        # Determine the device name prefix (either from child or parent)
        current_device_props = (child_device_data or primary_device_data).get("properties", {})
        device_name_prefix = current_device_props.get("name", current_device_props.get("nodeId", "Unknown Device"))
        
        self.entity_id = f"sensor.{DOMAIN}_{current_device_props.get('nodeId', '').replace('-', '_')}_{self._value_key}".lower()
        self._attr_name = f"{device_name_prefix} {self._name_suffix}"
        self._attr_unique_id = f"{current_device_props.get('nodeId')}_{self._value_key}"

        self._update_internal_state() # Initial update

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        """Return the unit of measurement."""
        return self._unit

    @property
    def device_class(self) -> Optional[SensorDeviceClass]:
        """Return the device class."""
        return self._device_class

    @property
    def state_class(self) -> Optional[SensorStateClass]:
        """Return the state class."""
        return self._state_class

    @property
    def icon(self) -> Optional[str]:
        """Return the icon."""
        return self._icon_override

    @property
    def entity_category(self) -> Optional[str]:
        """Return the entity category."""
        return self._entity_category_override
    
    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        # For sensors based on `properties.info`, the value is static once read.
        # For sensors based on measurements, it comes from the coordinator.
        # The self._attr_native_value is updated by _handle_coordinator_update or initially.
        return self._attr_native_value

    def _update_internal_state(self) -> None:
        """Update the internal state of the sensor from the current device data."""
        # This uses the device data passed during __init__ or refreshed from coordinator
        current_device_data_for_sensor = self._get_current_device_data_from_coordinator()
        if current_device_data_for_sensor is None:
            self._attr_native_value = None
            _LOGGER.debug("Sensor %s: No current data from coordinator.", self.unique_id)
            return

        # Get the latest telemetry data
        latest_data = current_device_data_for_sensor.get("_latest_data", {})
        
        # For state of charge, scale from 0-1 to 0-100
        if self._value_key == "stateOfCharge_frac" and self._value_key in latest_data:
            self._attr_native_value = latest_data[self._value_key] * 100
        else:
            self._attr_native_value = latest_data.get(self._value_key)

        # Set the last update time if we have a timestamp
        if "timestamp" in latest_data:
            self._attr_last_updated = latest_data["timestamp"]

        _LOGGER.debug("Sensor %s updated native_value to: %s at %s", 
                     self.unique_id, 
                     self._attr_native_value,
                     self._attr_last_updated)


    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug("Coordinator update received for sensor: %s", self.unique_id)
        self._update_internal_state()
        super()._handle_coordinator_update() # Updates availability and calls async_write_ha_state
