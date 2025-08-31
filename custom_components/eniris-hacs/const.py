"""Constants for the Eniris HACS integration."""

# Domain for the integration
DOMAIN = "eniris_hacs"

# Configuration keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password" # Note: Storing passwords directly is not ideal for production.
                           # Consider OAuth or other secure methods if the API supports them.

# API Endpoints
BASE_AUTH_URL = "https://authentication.eniris.be/auth"
LOGIN_URL = f"{BASE_AUTH_URL}/login"
ACCESS_TOKEN_URL = f"{BASE_AUTH_URL}/accesstoken"
API_BASE_URL = "https://api.eniris.be/v1"
DEVICES_URL = f"{API_BASE_URL}/device"

# Update interval for polling data (in seconds)
# Adjust as needed, considering API rate limits
SCAN_INTERVAL_SECONDS = 5  # Formerly 1 minute, now 5 seconds for frequent rp_one_s updates

# Supported device types
SUPPORTED_NODE_TYPES = [
    "hybridInverter",
    "solarOptimizer",
    "powerMeter",
    "battery",
]

# Device type mapping for Home Assistant
DEVICE_TYPE_HYBRID_INVERTER = "hybridInverter"
DEVICE_TYPE_SOLAR_OPTIMIZER = "solarOptimizer"
DEVICE_TYPE_POWER_METER = "powerMeter"
DEVICE_TYPE_BATTERY = "battery"

# Default manufacturer
MANUFACTURER = "Eniris (via SmartgridOne)"

# Headers
HEADER_CONTENT_TYPE_JSON = {"Content-Type": "application/json"}
