# Eniris HACS Integration

This is a custom Home Assistant integration for Eniris energy monitoring devices, including hybrid inverters, batteries, power meters, and solar optimizers. It allows you to monitor real-time and historical energy data from your Eniris-connected devices directly in Home Assistant.

## Features
- Automatic device discovery (hybrid inverter, battery, power meter, solar optimizer)
- Telemetry for power, energy, state of charge, and more
- Import/Export power sensors for all power meters, PV, and batteries
- Charging/Discharging power sensors for batteries and hybrid inverters (sums all batteries)
- Only creates sensors for fields with real data
- Home Assistant Energy dashboard compatible (see below)

## Installation
1. Copy the `custom_components/eniris-hacs` directory into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.
3. Add the Eniris HACS integration via the Home Assistant UI (Configuration > Devices & Services > Add Integration > Eniris HACS).
4. Enter your Eniris credentials.

## Configuration
- The integration will auto-discover supported devices and create sensors for all available telemetry fields.
- Sensors are only created if data is available for that field.
- Charging/Discharging power for hybrid inverters is calculated as the sum of all child batteries.

## Energy Dashboard Setup

### Why Use Integration Helpers?
Most Eniris devices provide **instantaneous power** (W) and energy deltas, but not always a cumulative total energy value. Home Assistant's Energy dashboard requires a cumulative energy sensor (Wh or kWh, state class `total_increasing`).

If your device does not provide a cumulative energy value, you can use Home Assistant's **Integration - Riemann sum integral helper** to convert power (W) into energy (Wh or kWh):

### How to Set Up the Integration Helper
1. Go to **Settings > Devices & Services > Helpers** in Home Assistant.
2. Click **Create Helper** and select **Integration - Riemann sum integral**.
3. Choose your power sensor (e.g., `sensor.your_pv_actual_power` or `sensor.import_power`).
4. Set the **time unit** to `hours` (for Wh/kWh).
5. (Optional) Set the **metric prefix** to `k` for kWh, or leave blank for Wh.
6. Name your helper (e.g., "Solar Energy (kWh)").
7. Click **Submit**.
8. Use the new helper sensor in the Home Assistant Energy dashboard as your energy source.

**Note:**
- The integration helper works best if your power sensor updates at least every minute.
- If your device provides a cumulative energy value (e.g., `importedAbsEnergyTot_Wh`), use that directly in the Energy dashboard for best accuracy.

## Troubleshooting
- If you see "unknown" sensors, it means no data is available for that field.
- If your energy values in the dashboard are too low, check your power sensor's update frequency and the integration helper settings.
- For hybrid inverters, charging/discharging power is the sum of all child batteries.

## Contributing
Pull requests and issues are welcome! Please open an issue if you find a bug or have a feature request.

## License
GPL-3.0 license 