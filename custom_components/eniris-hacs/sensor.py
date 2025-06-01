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

# Add these to all device types that can have import/export power
IMPORT_EXPORT_POWER_SENSORS = [
    ("import_power", "Import Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:transmission-tower-import", None),
    ("export_power", "Export Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:transmission-tower-export", None),
]

# Add these to all battery devices
BATTERY_CHARGE_DISCHARGE_SENSORS = [
    ("charging_power", "Charging Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:battery-charging", None),
    ("discharging_power", "Discharging Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:battery-discharging", None),
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
        ("actualPowerTot_W", "Realtime Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:solar-power", None),
        ("exportedEnergyDeltaTot_Wh", "Exported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower-export", None),
        ("importedEnergyDeltaTot_Wh", "Imported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower-import", None),
        # New sensor for state of charge from child battery
        ("stateOfCharge_frac", "State of Charge", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery", None),
        ("stateOfCharge_frac", "Realtime State of Charge", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery", None),
    ],
    DEVICE_TYPE_BATTERY: [
        ("stateOfCharge_frac", "State of Charge", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery", None),
        ("stateOfCharge_frac", "Realtime State of Charge", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery", None),
        ("actualPowerTot_W", "Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:battery-charging", None),
        ("actualPowerTot_W", "Realtime Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:battery-charging", None),
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
    regular_coordinator = coordinator_data["coordinator"]
    realtime_coordinator = coordinator_data["realtime_coordinator"]

    entities_to_add: List[EnirisHacsSensor] = []

    # The coordinator.data should be the processed_devices dictionary
    if not regular_coordinator.data:
        _LOGGER.warning("No data from coordinator, cannot set up sensors.")
        return

    for node_id, device_data in regular_coordinator.data.items():
        properties = device_data.get("properties", {})
        node_type = properties.get("nodeType")
        device_info_block = properties.get("info", {})
        latest_data = device_data.get("_latest_data", {})

        # 1. Add sensors based on properties.info (COMMON SENSORS)
        for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in SENSOR_DESCRIPTIONS_COMMON:
            if get_value_from_info(device_data, key) is not None:
                entities_to_add.append(
                    EnirisHacsSensor(
                        regular_coordinator,
                        device_data, # Primary device data
                        entity_description_tuple=(key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                        value_extractor=get_value_from_info,
                        is_info_sensor=True
                    )
                )
        
        # 1b. Add Import/Export Power sensors for all power meters, PV, and battery
        if node_type in [DEVICE_TYPE_POWER_METER, DEVICE_TYPE_SOLAR_OPTIMIZER, DEVICE_TYPE_BATTERY]:
            for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in IMPORT_EXPORT_POWER_SENSORS:
                entities_to_add.append(
                    EnirisHacsSensor(
                        regular_coordinator,
                        device_data,
                        entity_description_tuple=(key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                        value_extractor=None # We'll handle in the sensor class
                    )
                )
        # 1c. Add Charging/Discharging Power sensors for all batteries and hybrid inverters
        if node_type in [DEVICE_TYPE_BATTERY, DEVICE_TYPE_HYBRID_INVERTER]:
            if node_type == DEVICE_TYPE_HYBRID_INVERTER:
                for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in BATTERY_CHARGE_DISCHARGE_SENSORS:
                    entities_to_add.append(
                        EnirisHacsSensor(
                            regular_coordinator,
                            device_data,  # Parent is inverter, but value comes from children
                            entity_description_tuple=(key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                            value_extractor=None
                        )
                    )
            else:
                for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in BATTERY_CHARGE_DISCHARGE_SENSORS:
                    entities_to_add.append(
                        EnirisHacsSensor(
                            regular_coordinator,
                            device_data,
                            entity_description_tuple=(key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                            value_extractor=None
                        )
                    )

        # 2. Add sensors based on conceptual measurements for the primary device
        if node_type in CONCEPTUAL_MEASUREMENT_SENSORS:
            for m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat in CONCEPTUAL_MEASUREMENT_SENSORS[node_type]:
                # Determine if this is a real-time sensor
                is_realtime = m_key.endswith("_realtime")
                coordinator = realtime_coordinator if is_realtime else regular_coordinator
                
                entities_to_add.append(
                    EnirisHacsSensor(
                        coordinator,
                        device_data, # Primary device data
                        entity_description_tuple=(m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                        value_extractor=get_value_from_conceptual_measurements
                    )
                )

        # 3. Add sensors for CHİLD devices (e.g., battery or PV attached to an inverter)
        for child_device_data in device_data.get("_processed_children", []):
            child_properties = child_device_data.get("properties", {})
            child_node_type = child_properties.get("nodeType")
            child_info_block = child_properties.get("info", {})
            child_latest_data = child_device_data.get("_latest_data", {})

            # 3a. Common sensors for the child device (from its own info block)
            for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in SENSOR_DESCRIPTIONS_COMMON:
                entities_to_add.append(
                    EnirisHacsSensor(
                        regular_coordinator,
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
                    # Determine if this is a real-time sensor
                    is_realtime = m_key.endswith("_realtime")
                    coordinator = realtime_coordinator if is_realtime else regular_coordinator
                    
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
        
        # Add _realtime suffix to entity_id and unique_id for real-time sensors
        is_realtime = self._name_suffix.startswith("Realtime")
        id_suffix = "_realtime" if is_realtime else ""
        
        self.entity_id = f"sensor.{DOMAIN}_{current_device_props.get('nodeId', '').replace('-', '_')}_{self._value_key}{id_suffix}".lower()
        self._attr_name = f"{device_name_prefix} {self._name_suffix}"
        self._attr_unique_id = f"{current_device_props.get('nodeId')}_{self._value_key}{id_suffix}"

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
        current_device_data_for_sensor = self._get_current_device_data_from_coordinator()
        if current_device_data_for_sensor is None:
            self._attr_native_value = None
            _LOGGER.debug("Sensor %s: No current data from coordinator.", self.unique_id)
            return

        latest_data = current_device_data_for_sensor.get("_latest_data", {})
        current_properties = current_device_data_for_sensor.get("properties", {}) # Get current properties

        # Custom logic for import/export power sensors
        if self._value_key == "import_power":
            value = latest_data.get("actualPowerTot_W", {}).get("latest") if isinstance(latest_data.get("actualPowerTot_W"), dict) else latest_data.get("actualPowerTot_W")
            self._attr_native_value = value if value is not None and value > 0 else 0
        elif self._value_key == "export_power":
            value = latest_data.get("actualPowerTot_W", {}).get("latest") if isinstance(latest_data.get("actualPowerTot_W"), dict) else latest_data.get("actualPowerTot_W")
            self._attr_native_value = abs(value) if value is not None and value < 0 else 0
        
        # Custom logic for battery/hybrid inverter charging/discharging power
        elif self._value_key in ("charging_power", "discharging_power"):
            node_type_of_current_device = current_properties.get("nodeType") # Use current nodeType

            if node_type_of_current_device == DEVICE_TYPE_HYBRID_INVERTER:
                total_power = 0
                # Access _processed_children from the *updated* data block of the hybrid inverter
                # Ensure coordinator updates _latest_data within these child blocks too.
                for child_block in current_device_data_for_sensor.get("_processed_children", []): # Use fresh children list
                    if child_block.get("properties", {}).get("nodeType") == DEVICE_TYPE_BATTERY:
                        battery_latest_data = child_block.get("_latest_data", {}) # Get latest_data from the fresh child_block
                        value = battery_latest_data.get("actualPowerTot_W", {}).get("latest") if isinstance(battery_latest_data.get("actualPowerTot_W"), dict) else battery_latest_data.get("actualPowerTot_W")
                        if value is not None:
                            total_power += value
                
                if self._value_key == "charging_power":
                    self._attr_native_value = total_power if total_power > 0 else 0
                else:  # discharging_power
                    self._attr_native_value = abs(total_power) if total_power < 0 else 0
            
            elif node_type_of_current_device == DEVICE_TYPE_BATTERY: # Standalone battery
                # This logic relies on current_device_data_for_sensor being the standalone battery's data
                value = latest_data.get("actualPowerTot_W", {}).get("latest") if isinstance(latest_data.get("actualPowerTot_W"), dict) else latest_data.get("actualPowerTot_W")
                if self._value_key == "charging_power":
                    self._attr_native_value = value if value is not None and value > 0 else 0
                else:  # discharging_power
                    self._attr_native_value = abs(value) if value is not None and value < 0 else 0
            else:
                self._attr_native_value = None
        
        # For state of charge, scale from 0-1 to 0-100
        elif self._value_key == "stateOfCharge_frac":
            node_type_of_current_device = current_properties.get("nodeType") # Use current nodeType
            is_realtime = self._name_suffix.startswith("Realtime")

            if node_type_of_current_device == DEVICE_TYPE_HYBRID_INVERTER:
                # Retrieve state of charge from child battery using current data
                self._attr_native_value = None # Default if no battery child found or no value
                for child_block in current_device_data_for_sensor.get("_processed_children", []): # Use fresh children list
                    if child_block.get("properties", {}).get("nodeType") == DEVICE_TYPE_BATTERY:
                        battery_latest_data = child_block.get("_latest_data", {}) # Get latest_data from fresh child_block
                        value = battery_latest_data.get("stateOfCharge_frac", {}).get("latest_realtime" if is_realtime else "latest") if isinstance(battery_latest_data.get("stateOfCharge_frac"), dict) else battery_latest_data.get("stateOfCharge_frac")
                        if value is not None:
                            self._attr_native_value = value * 100
                            break 
            else: # Assumed to be a standalone battery or a direct child battery sensor
                value = latest_data.get("stateOfCharge_frac", {}).get("latest_realtime" if is_realtime else "latest") if isinstance(latest_data.get("stateOfCharge_frac"), dict) else latest_data.get("stateOfCharge_frac")
                self._attr_native_value = value * 100 if value is not None else None
        
        # For energy delta fields, use the 'sum' value
        elif self._value_key.endswith("EnergyDeltaTot_Wh") or self._value_key.endswith("chargedEnergyDeltaTot_Wh") or self._value_key.endswith("dischargedEnergyDeltaTot_Wh"):
            value = latest_data.get(self._value_key, {}).get("sum") if isinstance(latest_data.get(self._value_key), dict) else None
            self._attr_native_value = value
        
        # For all other fields, use the 'latest' value if available
        else:
            # This handles sensors where value_extractor was get_value_from_conceptual_measurements
            # and those where is_info_sensor is True (value_extractor=get_value_from_info)
            # For conceptual (live) measurements:
            if not self._is_info_sensor and self._value_extractor == get_value_from_conceptual_measurements:
                 # The original logic for conceptual measurements expected the value extractor to get the value
                 # However, the current structure relies on _latest_data for most live values.
                 # If get_value_from_conceptual_measurements is just a placeholder returning None,
                 # we should ensure the value is actually sourced from latest_data if available there.
                 is_realtime = self._name_suffix.startswith("Realtime")
                 value = latest_data.get(self._value_key, {}).get("latest_realtime" if is_realtime else "latest") if isinstance(latest_data.get(self._value_key), dict) else latest_data.get(self._value_key)
                 self._attr_native_value = value
            elif self._is_info_sensor: # Static info sensor
                 # Value extractor for info sensors is get_value_from_info
                 # This is usually called once at init or if data structure allows updates to info.
                 # For safety, re-evaluate if needed, though info is typically static.
                 self._attr_native_value = self._value_extractor(current_device_data_for_sensor, self._value_key)
            else:
                 # Fallback for other non-info, non-special-case sensors that might exist or future ones
                 is_realtime = self._name_suffix.startswith("Realtime")
                 value = latest_data.get(self._value_key, {}).get("latest_realtime" if is_realtime else "latest") if isinstance(latest_data.get(self._value_key), dict) else latest_data.get(self._value_key)
                 self._attr_native_value = value


        _LOGGER.debug("Sensor %s updated native_value to: %s", 
                      self.unique_id, 
                      self._attr_native_value)


    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug("Coordinator update received for sensor: %s", self.unique_id)
        self._update_internal_state()
        super()._handle_coordinator_update() # Updates availability and calls async_write_ha_state
