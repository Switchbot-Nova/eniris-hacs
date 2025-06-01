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
        ("exportedEnergyDeltaTot_Wh", "Exported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower-export", None),
        ("importedEnergyDeltaTot_Wh", "Imported Energy", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower-import", None),
        # New sensor for state of charge from child battery
        ("stateOfCharge_frac", "State of Charge", PERCENTAGE, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "mdi:battery", None),
        # New sensors for summed charging/discharging power of child batteries
        ("summed_child_battery_charging_power", "Summed Battery Charging Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:battery-arrow-up-outline", None),
        ("summed_child_battery_discharging_power", "Summed Battery Discharging Power", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "mdi:battery-arrow-down-outline", None),
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
        latest_data = device_data.get("_latest_data", {})

        # 1. Add sensors based on properties.info (COMMON SENSORS)
        for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in SENSOR_DESCRIPTIONS_COMMON:
            if get_value_from_info(device_data, key) is not None:
                entities_to_add.append(
                    EnirisHacsSensor(
                        coordinator,
                        device_data, # Primary device data
                        entity_description_tuple=(key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                        is_info_sensor=True
                    )
                )
        
        # 1b. Add Import/Export Power sensors for all power meters, PV, battery and hybrid inverters
        if node_type in [DEVICE_TYPE_POWER_METER, DEVICE_TYPE_SOLAR_OPTIMIZER, DEVICE_TYPE_BATTERY, DEVICE_TYPE_HYBRID_INVERTER]:
            for entity_desc_tuple in IMPORT_EXPORT_POWER_SENSORS: # (base_key, name_suffix, unit, dev_class, state_class, icon, ent_cat)
                # These are special base_keys ("import_power", "export_power") handled in _update_internal_state
                # Create for both rp_one_m and rp_one_s
                for rp_tag in ["rp_one_m", "rp_one_s"]:
                    entities_to_add.append(
                        EnirisHacsSensor(
                            coordinator,
                            device_data,
                            entity_description_tuple=entity_desc_tuple,
                            retention_policy_tag=rp_tag,
                            data_type_tag="latest" # Import/Export power is always a 'latest' type value
                        )
                    )
        # 1c. Add Charging/Discharging Power sensors for all batteries and hybrid inverters
        if node_type in [DEVICE_TYPE_BATTERY, DEVICE_TYPE_HYBRID_INVERTER]:
            for entity_desc_tuple in BATTERY_CHARGE_DISCHARGE_SENSORS:
                entities_to_add.append(
                    EnirisHacsSensor(
                        coordinator,
                        device_data,
                        entity_description_tuple=entity_desc_tuple,
                    )
                )

        # 2. Add sensors based on telemetry measurements for the primary device
        if node_type in CONCEPTUAL_MEASUREMENT_SENSORS:
            for m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat in CONCEPTUAL_MEASUREMENT_SENSORS[node_type]:
                entity_desc_tuple_base = (m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat)
                
                # Determine data type: sum for energy, latest for others
                data_type_for_m_key = "sum" if "Energy" in m_key or "Energy" in name_suffix else "latest"

                # Create sensors for each retention policy defined in api.py (e.g., rp_one_m, rp_one_s)
                # Assuming api.py defines retention_policies = ["rp_one_m", "rp_one_s"]
                # If stateOfCharge_frac, only create one sensor (uses rp_one_m by default in class)
                if m_key == "stateOfCharge_frac":
                    entities_to_add.append(
                        EnirisHacsSensor(
                            coordinator,
                            device_data,
                            entity_description_tuple=entity_desc_tuple_base,
                            # retention_policy_tag is None, uses default rp_one_m logic in class
                            data_type_tag="latest" # SoC is always a latest value
                        )
                    )
                else:
                    for rp_tag in ["rp_one_m", "rp_one_s"]:
                        entities_to_add.append(
                            EnirisHacsSensor(
                                coordinator,
                                device_data, 
                                entity_description_tuple=entity_desc_tuple_base,
                                retention_policy_tag=rp_tag,
                                data_type_tag=data_type_for_m_key
                            )
                        )

        # 3. Add sensors for CHILD devices (e.g., battery or PV attached to an inverter)
        for child_device_data in device_data.get("_processed_children", []):
            child_properties = child_device_data.get("properties", {})
            child_node_type = child_properties.get("nodeType")
            child_info_block = child_properties.get("info", {})
            child_latest_data = child_device_data.get("_latest_data", {})

            # 3a. Common sensors for the child device (from its own info block)
            for key, name_suffix, unit, dev_class, state_class, icon, ent_cat in SENSOR_DESCRIPTIONS_COMMON:
                if get_value_from_info(child_device_data, key) is not None:
                    entities_to_add.append(
                        EnirisHacsSensor(
                            coordinator,
                            device_data, # Parent device data for HA device linking
                            child_device_data=child_device_data, # Actual data source for this sensor
                            entity_description_tuple=(key, name_suffix, unit, dev_class, state_class, icon, ent_cat),
                            is_info_sensor=True
                        )
                    )
            # 3b. Telemetry measurement sensors for the child device
            if child_node_type in CONCEPTUAL_MEASUREMENT_SENSORS:
                for m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat in CONCEPTUAL_MEASUREMENT_SENSORS[child_node_type]:
                    entity_desc_tuple_base = (m_key, name_suffix, unit, dev_class, state_class, icon, ent_cat)
                    data_type_for_m_key = "sum" if "Energy" in m_key or "Energy" in name_suffix else "latest"

                    if m_key == "stateOfCharge_frac": # SoC special handling for child
                         entities_to_add.append(
                            EnirisHacsSensor(
                                coordinator,
                                device_data, # Parent for linking
                                child_device_data=child_device_data,
                                entity_description_tuple=entity_desc_tuple_base,
                                data_type_tag="latest"
                            )
                        )
                    else:
                        for rp_tag in ["rp_one_m", "rp_one_s"]:
                            entities_to_add.append(
                                EnirisHacsSensor(
                                    coordinator,
                                    device_data, # Parent device data for HA device linking
                                    child_device_data=child_device_data, 
                                    entity_description_tuple=entity_desc_tuple_base,
                                    retention_policy_tag=rp_tag,
                                    data_type_tag=data_type_for_m_key
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
        entity_description_tuple: Tuple, # (base_key, name_suffix, unit, dev_class, state_class, icon, ent_cat)
        retention_policy_tag: Optional[str] = None, # e.g., "rp_one_m" or "rp_one_s"
        data_type_tag: str = "latest", # "latest" or "sum"
        child_device_data: Optional[Dict[str, Any]] = None,
        is_info_sensor: bool = False, 
    ):
        """Initialize the sensor."""
        super().__init__(coordinator, primary_device_data, child_device_data)
        
        self._base_value_key = entity_description_tuple[0] # e.g., "actualPowerTot_W"
        self._name_suffix = entity_description_tuple[1]
        self._unit = entity_description_tuple[2]
        self._device_class = entity_description_tuple[3]
        self._state_class = entity_description_tuple[4]
        self._icon_override = entity_description_tuple[5]
        self._entity_category_override = entity_description_tuple[6]
        
        self._retention_policy_tag = retention_policy_tag # e.g., "rp_one_m" or "rp_one_s" or None for info sensors
        self._data_type_tag = data_type_tag # "latest" or "sum"
        self._is_info_sensor = is_info_sensor

        # Determine the device name prefix (either from child or parent)
        current_device_props = (child_device_data or primary_device_data).get("properties", {})
        device_name_prefix = current_device_props.get("name", current_device_props.get("nodeId", "Unknown Device"))
        
        # Construct a unique key part based on retention policy and data type
        key_suffix = f"_{self._retention_policy_tag}_{self._data_type_tag}" if self._retention_policy_tag else ""
        
        # Adjust name suffix if it's an RP-specific sensor
        name_suffix_adjusted = self._name_suffix
        if self._retention_policy_tag == "rp_one_m":
            name_suffix_adjusted = f"{self._name_suffix} (1m)"
        elif self._retention_policy_tag == "rp_one_s":
            name_suffix_adjusted = f"{self._name_suffix} (1s)"
        # If it's a sum, add that too, unless already specific
        if self._data_type_tag == "sum" and "Energy" not in self._name_suffix: # Energy already implies sum
             name_suffix_adjusted = f"{name_suffix_adjusted} Sum"


        self._value_key_for_unique_id = f"{self._base_value_key}{key_suffix}"
        
        self.entity_id = f"sensor.{DOMAIN}_{current_device_props.get('nodeId', '').replace('-', '_')}_{self._value_key_for_unique_id}".lower()
        self._attr_name = f"{device_name_prefix} {name_suffix_adjusted}"
        self._attr_unique_id = f"{current_device_props.get('nodeId')}_{self._value_key_for_unique_id}"

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
        return self._attr_native_value

    def _get_sensor_value_from_latest_data(self, latest_data: Dict[str, Any], base_key: str, rp_tag: Optional[str], data_type: str) -> Any:
        """Helper to extract a specific value from the _latest_data structure."""
        if not latest_data or not isinstance(latest_data, dict):
            return None
        
        field_data = latest_data.get(base_key)
        if not field_data or not isinstance(field_data, dict):
            # Fallback for old structure or direct values (e.g. if API changes or for simpler sensors)
            if rp_tag is None and data_type == "latest": # Assuming direct value if no RP tag
                return field_data 
            return None

        if rp_tag: # rp_one_m or rp_one_s
            return field_data.get(f"{rp_tag}_{data_type}")
        else: # Should not happen if rp_tag is expected, but as a fallback for non-rp specific latest/sum
            return field_data.get(data_type)


    def _update_internal_state(self) -> None:
        """Update the internal state of the sensor from the current device data."""
        current_device_data_for_sensor = self._get_current_device_data_from_coordinator()
        if current_device_data_for_sensor is None:
            # Do not change state if coordinator data for this device is missing entirely.
            # Availability is handled by the EnirisHacsEntity base class.
            # _LOGGER.debug("Sensor %s: No current data from coordinator for this device. State not changed.", self.unique_id)
            return

        latest_data_source = current_device_data_for_sensor.get("_latest_data", {})
        current_properties = current_device_data_for_sensor.get("properties", {})

        # For static info sensors, value is set once at init mostly or if info itself changes.
        # These are less affected by partial telemetry updates.
        if self._is_info_sensor:
            new_value = get_value_from_info(current_device_data_for_sensor, self._base_value_key)
            # Only update if it genuinely changed, though info is usually static.
            if self._attr_native_value != new_value:
                 self._attr_native_value = new_value
            return

        # --- Special Handling for specific sensor types ---
        # These sensors derive their state from one or more telemetry points.
        # They should only update if the specific underlying data they need is present in this update.

        new_sensor_value: Any = None # Temporary variable to hold the potential new value
        value_is_available_for_update = False # Flag to track if we should update state

        # Import/Export Power
        if self._base_value_key == "import_power":
            value = self._get_sensor_value_from_latest_data(latest_data_source, "actualPowerTot_W", self._retention_policy_tag, "latest")
            if value is not None:
                new_sensor_value = value if value > 0 else 0
                value_is_available_for_update = True
        elif self._base_value_key == "export_power":
            value = self._get_sensor_value_from_latest_data(latest_data_source, "actualPowerTot_W", self._retention_policy_tag, "latest")
            if value is not None:
                new_sensor_value = abs(value) if value < 0 else 0
                value_is_available_for_update = True
        
        # Charging/Discharging Power
        elif self._base_value_key in ("charging_power", "discharging_power"):
            node_type_of_current_device = current_properties.get("nodeType")
            total_power = 0
            power_data_found = False # Did we find any relevant power data to make a calculation?

            if node_type_of_current_device == DEVICE_TYPE_HYBRID_INVERTER:
                # For Hybrid Inverters, sums power from child batteries.
                # It should use the *derived sensor's* RP tag, not hardcode one for children.
                # However, these are specific summary sensors, not direct telemetry.
                # The current BATTERY_CHARGE_DISCHARGE_SENSORS are general for the device.
                # Let's assume these still use rp_one_m by convention as they summarize.
                # If rp_one_s versions of *these specific summary sensors* are needed,
                # they'd need separate base_keys and instantiation in async_setup_entry.
                # For now, sticking to original intent of these as rp_one_m summaries.
                for child_block in current_device_data_for_sensor.get("_processed_children", []):
                    if child_block.get("properties", {}).get("nodeType") == DEVICE_TYPE_BATTERY:
                        battery_latest_data = child_block.get("_latest_data", {})
                        value = self._get_sensor_value_from_latest_data(battery_latest_data, "actualPowerTot_W", "rp_one_m", "latest") # Using rp_one_m for this summary
                        if value is not None:
                            total_power += value
                            power_data_found = True
            elif node_type_of_current_device == DEVICE_TYPE_BATTERY:
                # For standalone batteries, uses its own power.
                value = self._get_sensor_value_from_latest_data(latest_data_source, "actualPowerTot_W", "rp_one_m", "latest") # Using rp_one_m for this summary
                if value is not None:
                    total_power = value
                    power_data_found = True
            
            if power_data_found:
                if self._base_value_key == "charging_power":
                    new_sensor_value = total_power if total_power > 0 else 0
                else:  # discharging_power
                    new_sensor_value = abs(total_power) if total_power < 0 else 0
                value_is_available_for_update = True
        
        # State of Charge
        elif self._base_value_key == "stateOfCharge_frac":
            # This sensor is instantiated once, expected to use rp_one_m by default.
            node_type_of_current_device = current_properties.get("nodeType")
            soc_value_from_api = None

            if node_type_of_current_device == DEVICE_TYPE_HYBRID_INVERTER:
                for child_block in current_device_data_for_sensor.get("_processed_children", []):
                    if child_block.get("properties", {}).get("nodeType") == DEVICE_TYPE_BATTERY:
                        battery_latest_data = child_block.get("_latest_data", {})
                        soc_value_from_api = self._get_sensor_value_from_latest_data(battery_latest_data, "stateOfCharge_frac", "rp_one_m", "latest")
                        if soc_value_from_api is not None:
                            break 
            else: # Standalone battery
                soc_value_from_api = self._get_sensor_value_from_latest_data(latest_data_source, "stateOfCharge_frac", self._retention_policy_tag or "rp_one_m", "latest")
            
            if soc_value_from_api is not None:
                new_sensor_value = soc_value_from_api * 100
                value_is_available_for_update = True

        # Summed real-time charging power of child batteries for a Hybrid Inverter
        elif self._base_value_key == "summed_child_battery_charging_power" and current_properties.get("nodeType") == DEVICE_TYPE_HYBRID_INVERTER:
            net_summed_child_battery_power = 0
            found_any_battery_data = False
            for child_block in current_device_data_for_sensor.get("_processed_children", []):
                if child_block.get("properties", {}).get("nodeType") == DEVICE_TYPE_BATTERY:
                    battery_latest_data = child_block.get("_latest_data", {})
                    power_value = self._get_sensor_value_from_latest_data(battery_latest_data, "actualPowerTot_W", self._retention_policy_tag, "latest")
                    if power_value is not None:
                        net_summed_child_battery_power += power_value
                        found_any_battery_data = True
            if found_any_battery_data:
                new_sensor_value = net_summed_child_battery_power if net_summed_child_battery_power > 0 else 0
                value_is_available_for_update = True

        # Summed real-time discharging power of child batteries for a Hybrid Inverter
        elif self._base_value_key == "summed_child_battery_discharging_power" and current_properties.get("nodeType") == DEVICE_TYPE_HYBRID_INVERTER:
            net_summed_child_battery_power = 0
            found_any_battery_data = False
            for child_block in current_device_data_for_sensor.get("_processed_children", []):
                if child_block.get("properties", {}).get("nodeType") == DEVICE_TYPE_BATTERY:
                    battery_latest_data = child_block.get("_latest_data", {})
                    power_value = self._get_sensor_value_from_latest_data(battery_latest_data, "actualPowerTot_W", self._retention_policy_tag, "latest")
                    if power_value is not None:
                        net_summed_child_battery_power += power_value
                        found_any_battery_data = True
            if found_any_battery_data:
                new_sensor_value = abs(net_summed_child_battery_power) if net_summed_child_battery_power < 0 else 0
                value_is_available_for_update = True
        
        # --- Generic Handling for telemetry data based on retention_policy_tag and data_type_tag ---
        else:
            if self._retention_policy_tag and self._data_type_tag: # Ensure it's a telemetry sensor
                value = self._get_sensor_value_from_latest_data(
                    latest_data_source,
                    self._base_value_key,
                    self._retention_policy_tag,
                    self._data_type_tag
                )
                if value is not None:
                    new_sensor_value = value
                    value_is_available_for_update = True
                # If value is None, we don't set value_is_available_for_update = True, so state is preserved.
            else:
                # This case is for sensors that might not be info sensors and don't have RP tags.
                # Potentially problematic or legacy. Log if necessary or handle if such sensors exist.
                # For now, if it falls here, it won't update unless new_sensor_value is explicitly set.
                # _LOGGER.warning("Sensor %s (%s) lacks RP/data tags and is not an info sensor. State may not update as expected.", self.unique_id, self._base_value_key)
                pass # No update unless specific logic above caught it.


        if value_is_available_for_update:
            if self._attr_native_value != new_sensor_value:
                self._attr_native_value = new_sensor_value
                # _LOGGER.debug("Sensor %s updated native_value to: %s", self.unique_id, self._attr_native_value) # Optional: for debugging
        # else:
            # _LOGGER.debug("Sensor %s: No new specific data available in this update cycle. State preserved: %s", self.unique_id, self._attr_native_value) # Optional: for debugging
            
    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug("Coordinator update received for sensor: %s", self.unique_id)
        self._update_internal_state()
        super()._handle_coordinator_update() # Updates availability and calls async_write_ha_state
