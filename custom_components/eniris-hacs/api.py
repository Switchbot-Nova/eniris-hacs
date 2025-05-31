"""API client for Eniris HACS."""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
import copy

import aiohttp
from aiohttp.client_exceptions import ClientConnectorError, ClientResponseError

from .const import (
    ACCESS_TOKEN_URL,
    DEVICES_URL,
    LOGIN_URL,
    SUPPORTED_NODE_TYPES,
    HEADER_CONTENT_TYPE_JSON,
    DEVICE_TYPE_HYBRID_INVERTER,
    DEVICE_TYPE_SOLAR_OPTIMIZER,
    DEVICE_TYPE_POWER_METER,
    DEVICE_TYPE_BATTERY,
)

_LOGGER = logging.getLogger(__name__)


class EnirisHacsApiError(Exception):
    """Custom exception for API errors."""


class EnirisHacsAuthError(EnirisHacsApiError):
    """Custom exception for authentication errors."""


class EnirisHacsApiClient:
    """API Client for Eniris HACS."""

    def __init__(
        self,
        email: str,
        password: str,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        self._session = session or aiohttp.ClientSession()
        self._refresh_token: Optional[str] = None
        self._access_token: Optional[str] = None
        self._access_token_expires_at: Optional[float] = None # Placeholder for future expiry handling

    async def _request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, Any]] = None,
        is_text_response: bool = False,
    ) -> Any:
        """Make an API request."""
        _LOGGER.debug("Request: %s %s, Headers: %s, Data: %s", method, url, headers, data)
        try:
            async with self._session.request(
                method, url, headers=headers, json=data
            ) as response:
                _LOGGER.debug("Response status: %s, for URL: %s", response.status, url)
                if response.status == 200 or response.status == 201:
                    if is_text_response:
                        return await response.text()
                    try:
                        return await response.json()
                    except Exception as e:
                        _LOGGER.warning("Failed to parse JSON response: %s. Falling back to text response.", e)
                        return await response.text()
                elif response.status in (401, 403):
                    _LOGGER.error(
                        "Authentication error %s for %s: %s",
                        response.status,
                        url,
                        await response.text(),
                    )
                    raise EnirisHacsAuthError(
                        f"Authentication failed ({response.status}): {await response.text()}"
                    )
                else:
                    _LOGGER.error(
                        "API request failed %s for %s: %s",
                        response.status,
                        url,
                        await response.text(),
                    )
                    raise EnirisHacsApiError(
                        f"API request failed ({response.status}): {await response.text()}"
                    )
        except ClientConnectorError as e:
            _LOGGER.error("Connection error during API request to %s: %s", url, e)
            raise EnirisHacsApiError(f"Connection error: {e}") from e
        except ClientResponseError as e: # Should be caught by status checks, but good to have
            _LOGGER.error("Client response error during API request to %s: %s", url, e)
            raise EnirisHacsApiError(f"Client response error: {e.message} ({e.status})") from e
        except asyncio.TimeoutError as e:
            _LOGGER.error("Timeout during API request to %s: %s", url, e)
            raise EnirisHacsApiError(f"Request timed out: {e}") from e


    async def get_refresh_token(self) -> str:
        """Get a refresh token."""
        _LOGGER.info("Attempting to get refresh token for user %s", self._email)
        payload = {"username": self._email, "password": self._password}
        try:
            response_text = await self._request(
                "POST", LOGIN_URL, headers=HEADER_CONTENT_TYPE_JSON, data=payload, is_text_response=True
            )
            if response_text:
                # Clean up the response text - remove any whitespace and quotes
                self._refresh_token = response_text.strip().strip('"')
                _LOGGER.info("Successfully obtained refresh token.")
                return self._refresh_token
            _LOGGER.error("Failed to get refresh token: Empty response.")
            raise EnirisHacsAuthError("Failed to get refresh token: Empty response")
        except EnirisHacsApiError as e:
            _LOGGER.error("Error obtaining refresh token: %s", e)
            raise EnirisHacsAuthError(f"Failed to obtain refresh token: {e}") from e

    async def get_access_token(self) -> str:
        """Get an access token using the refresh token."""
        if not self._refresh_token:
            _LOGGER.info("No refresh token available, fetching new one.")
            await self.get_refresh_token() # This will raise if it fails

        if not self._refresh_token: # Should not happen if above call succeeded
            _LOGGER.error("Refresh token is still missing after attempting to fetch.")
            raise EnirisHacsAuthError("Refresh token is missing.")

        _LOGGER.info("Attempting to get access token.")
        headers = {"Authorization": f"Bearer {self._refresh_token}"}
        try:
            response_data = await self._request("GET", ACCESS_TOKEN_URL, headers=headers, is_text_response=True)
            if isinstance(response_data, str):
                # Handle plain text response
                self._access_token = response_data.strip().strip('"')
                _LOGGER.info("Successfully obtained access token from text response.")
                return self._access_token
            elif isinstance(response_data, dict) and "accessToken" in response_data:
                # Handle JSON response
                self._access_token = response_data["accessToken"]
                _LOGGER.info("Successfully obtained access token from JSON response.")
                return self._access_token
            _LOGGER.error("Failed to get access token: Invalid response format. Response: %s", response_data)
            raise EnirisHacsAuthError("Failed to get access token: Invalid response format")
        except EnirisHacsApiError as e:
            _LOGGER.error("Error obtaining access token: %s", e)
            raise EnirisHacsAuthError(f"Failed to obtain access token: {e}") from e

    async def ensure_access_token(self) -> str:
        """Ensure a valid access token is available, refreshing if necessary."""
        # Basic check; could be expanded with expiry time if API provides it
        if not self._access_token: # or (self._access_token_expires_at and time.time() >= self._access_token_expires_at):
            _LOGGER.info("Access token is missing or expired, obtaining new one.")
            await self.get_access_token()
        
        if not self._access_token: # Should not happen if above call succeeded
             _LOGGER.error("Access token is still missing after attempting to fetch.")
             raise EnirisHacsAuthError("Access token is missing.")
        return self._access_token

    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get a list of devices."""
        access_token = await self.ensure_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        _LOGGER.info("Fetching devices from Eniris HACS API.")
        try:
            response_data = await self._request("GET", DEVICES_URL, headers=headers)
            if response_data and "device" in response_data and isinstance(response_data["device"], list):
                devices = response_data["device"]
                _LOGGER.info("Successfully fetched %s devices.", len(devices))
                return devices
            _LOGGER.warning("No 'device' list found in API response or response is not as expected. Response: %s", response_data)
            return [] # Return empty list if structure is not as expected
        except EnirisHacsApiError as e:
            _LOGGER.error("Error fetching devices: %s", e)
            # If it's an auth error, it might mean the access token expired mid-flight.
            # A more robust system might retry getting an access token once.
            if isinstance(e, EnirisHacsAuthError):
                _LOGGER.info("Auth error during device fetch, attempting to refresh access token once.")
                self._access_token = None # Clear current access token to force refresh
                access_token = await self.ensure_access_token() # Retry getting token
                headers = {"Authorization": f"Bearer {access_token}"}
                # Retry fetching devices once
                response_data = await self._request("GET", DEVICES_URL, headers=headers)
                if response_data and "device" in response_data and isinstance(response_data["device"], list):
                    devices = response_data["device"]
                    _LOGGER.info("Successfully fetched %s devices on retry.", len(devices))
                    return devices
                _LOGGER.error("Still failed to fetch devices after token refresh: %s", response_data)
                return []
            raise # Re-raise original error if not auth or if retry failed

    async def get_device_telemetry(self, node_id: str, measurement: str, fields: List[str]) -> Dict[str, Any]:
        """Get telemetry data for a specific device."""
        access_token = await self.ensure_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # Get the last 5 minutes of data
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=5)

        # Create queries for each field
        queries = []
        for field in fields:
            # Query for the latest value
            queries.append({
                "select": [field],
                "from": {
                    "namespace": {
                        "version": "1",
                        "database": "beauvent",
                        "retentionPolicy": "rp_one_m"
                    },
                    "measurement": measurement
                },
                "where": {
                    "time": [
                        {"operator": ">=", "value": int(start_time.timestamp() * 1000)},
                        {"operator": "<", "value": int(end_time.timestamp() * 1000)}
                    ],
                    "tags": {"nodeId": node_id}
                },
                "limit": 1,
                "orderBy": "DESC"
            })
            
            # Query for the sum in the time range
            queries.append({
                "select": [{"field": field, "function": "sum"}],
                "from": {
                    "namespace": {
                        "version": "1",
                        "database": "beauvent",
                        "retentionPolicy": "rp_one_m"
                    },
                    "measurement": measurement
                },
                "where": {
                    "time": [
                        {"operator": ">=", "value": int(start_time.timestamp() * 1000)},
                        {"operator": "<", "value": int(end_time.timestamp() * 1000)}
                    ],
                    "tags": {"nodeId": node_id}
                }
            })

        try:
            response = await self._request(
                "POST",
                "https://api.eniris.be/v1/telemetry/query",
                headers=headers,
                data=queries
            )
            
            if not response or not isinstance(response, list) or len(response) == 0:
                _LOGGER.warning("No telemetry data received for device %s", node_id)
                return {}

            result = {}
            latest_timestamp = None

            # Each field has two queries: one for latest, one for sum
            # We'll process them in pairs
            for i in range(0, len(response), 2):
                latest_stmt = response[i] if i < len(response) else None
                sum_stmt = response[i+1] if i+1 < len(response) else None
                field = fields[i//2] if i//2 < len(fields) else None
                if not field:
                    continue
                result[field] = {}

                # Process latest value
                if latest_stmt and latest_stmt.get("series"):
                    for series in latest_stmt["series"]:
                        if not series.get("values"):
                            continue
                        latest_value = series["values"][-1]
                        timestamp = latest_value[0]
                        # Get the column names
                        columns = series.get("columns", [])
                        for idx, value in enumerate(latest_value[1:], 1):
                            if idx < len(columns):
                                field_name = columns[idx]
                                if field_name == field:
                                    result[field]["latest"] = value
                                    # Save timestamp for latest value
                                    if latest_timestamp is None or timestamp > latest_timestamp:
                                        latest_timestamp = timestamp
                # Process sum value
                if sum_stmt and sum_stmt.get("series"):
                    for series in sum_stmt["series"]:
                        if not series.get("values"):
                            continue
                        sum_value = series["values"][-1]
                        columns = series.get("columns", [])
                        for idx, value in enumerate(sum_value[1:], 1):
                            if idx < len(columns):
                                field_name = columns[idx]
                                if field_name.startswith("sum_"):
                                    field_name = field_name[4:]
                                if field_name == field:
                                    result[field]["sum"] = value

            # Add the timestamp in UTC
            if latest_timestamp:
                if isinstance(latest_timestamp, int):
                    result["timestamp"] = datetime.fromtimestamp(latest_timestamp / 1000, timezone.utc)
                elif isinstance(latest_timestamp, str):
                    ts = latest_timestamp.rstrip('Z')
                    try:
                        result["timestamp"] = datetime.fromisoformat(ts)
                    except Exception:
                        result["timestamp"] = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")

            _LOGGER.debug("Telemetry data for device %s: %s", node_id, result)
            return result

        except EnirisHacsApiError as e:
            _LOGGER.error("Error fetching telemetry data for device %s: %s", node_id, e)
            # If it's an auth error, it might mean the access token expired mid-flight.
            # Retry getting an access token once.
            if isinstance(e, EnirisHacsAuthError):
                _LOGGER.info("Auth error during telemetry fetch, attempting to refresh access token once.")
                self._access_token = None  # Clear current access token to force refresh
                access_token = await self.ensure_access_token()  # Retry getting token
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }
                # Retry fetching telemetry once
                try:
                    response = await self._request(
                        "POST",
                        "https://api.eniris.be/v1/telemetry/query",
                        headers=headers,
                        data=queries
                    )
                    if response and isinstance(response, list) and len(response) > 0:
                        _LOGGER.info("Successfully fetched telemetry data on retry for device %s", node_id)
                        # Process the response as before
                        result = {}
                        latest_timestamp = None
                        for i in range(0, len(response), 2):
                            latest_stmt = response[i] if i < len(response) else None
                            sum_stmt = response[i+1] if i+1 < len(response) else None
                            field = fields[i//2] if i//2 < len(fields) else None
                            if not field:
                                continue
                            result[field] = {}

                            # Process latest value
                            if latest_stmt and latest_stmt.get("series"):
                                for series in latest_stmt["series"]:
                                    if not series.get("values"):
                                        continue
                                    latest_value = series["values"][-1]
                                    timestamp = latest_value[0]
                                    # Get the column names
                                    columns = series.get("columns", [])
                                    for idx, value in enumerate(latest_value[1:], 1):
                                        if idx < len(columns):
                                            field_name = columns[idx]
                                            if field_name == field:
                                                result[field]["latest"] = value
                                                # Save timestamp for latest value
                                                if latest_timestamp is None or timestamp > latest_timestamp:
                                                    latest_timestamp = timestamp
                            # Process sum value
                            if sum_stmt and sum_stmt.get("series"):
                                for series in sum_stmt["series"]:
                                    if not series.get("values"):
                                        continue
                                    sum_value = series["values"][-1]
                                    columns = series.get("columns", [])
                                    for idx, value in enumerate(sum_value[1:], 1):
                                        if idx < len(columns):
                                            field_name = columns[idx]
                                            if field_name.startswith("sum_"):
                                                field_name = field_name[4:]
                                            if field_name == field:
                                                result[field]["sum"] = value
                        if latest_timestamp:
                            # Handle both integer (Unix ms) and ISO8601 string
                            if isinstance(latest_timestamp, int):
                                result["timestamp"] = datetime.fromtimestamp(latest_timestamp / 1000, timezone.utc)
                            elif isinstance(latest_timestamp, str):
                                ts = latest_timestamp.rstrip('Z')
                                try:
                                    result["timestamp"] = datetime.fromisoformat(ts)
                                except Exception:
                                    result["timestamp"] = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
                        return result
                except Exception as retry_error:
                    _LOGGER.error("Still failed to fetch telemetry after token refresh: %s", retry_error)
            return {}

    async def get_device_latest_data(self, device_data: Dict[str, Any]) -> Dict[str, Any]:
        """Get the latest telemetry data for a device based on its nodeInfluxSeries configuration."""
        properties = device_data.get("properties", {})
        node_id = properties.get("nodeId")
        if not node_id:
            return {}

        # Get the nodeInfluxSeries configuration
        series_configs = properties.get("nodeInfluxSeries", [])
        if not series_configs:
            return {}

        latest_data = {}
        for series_config in series_configs:
            measurement = series_config.get("measurement")
            fields = series_config.get("fields", [])
            
            if not measurement or not fields:
                continue

            # Get telemetry data for this series
            telemetry_data = await self.get_device_telemetry(node_id, measurement, fields)
            
            # Add the data to our result
            latest_data.update(telemetry_data)

        return latest_data

    async def get_processed_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get devices and process them for hierarchy and supported types."""
        raw_devices = await self.get_devices()
        if not raw_devices:
            _LOGGER.error("No devices returned from API")
            return {}

        _LOGGER.debug("Raw devices from API: %s", raw_devices)
        devices_by_node_id: Dict[str, Dict[str, Any]] = {}
        processed_devices: Dict[str, Dict[str, Any]] = {}

        # First pass: index all devices by their nodeId
        for device_data in raw_devices:
            properties = device_data.get("properties", {})
            node_id = properties.get("nodeId")
            if not node_id:
                _LOGGER.warning("Device data missing 'nodeId': %s", device_data.get("id"))
                continue
            
            device_data["_processed_children"] = [] # To store actual child device data
            devices_by_node_id[node_id] = device_data
            _LOGGER.debug("Indexed device %s with type %s", node_id, properties.get("nodeType"))

        # Second pass: build hierarchy and identify primary devices
        for node_id, device_data in devices_by_node_id.items():
            properties = device_data.get("properties", {})
            node_type = properties.get("nodeType")
            _LOGGER.debug("Processing device %s of type %s", node_id, node_type)

            if node_type not in SUPPORTED_NODE_TYPES:
                _LOGGER.debug("Skipping unsupported device type %s for device %s", node_type, node_id)
                continue

            # Populate children for this device
            child_node_ids = properties.get("nodeChildrenIds", [])
            for child_node_id in child_node_ids:
                if child_node_id in devices_by_node_id:
                    child_device_data = devices_by_node_id[child_node_id]
                    device_data["_processed_children"].append(child_device_data)
                    _LOGGER.debug("Added child device %s to parent %s", child_node_id, node_id)

            # Determine if this device should be a primary device
            is_primary = False

            # Always make hybrid inverters primary devices
            if node_type == DEVICE_TYPE_HYBRID_INVERTER:
                is_primary = True
                _LOGGER.debug("Device %s is primary because it's a hybrid inverter", node_id)
            # For other device types, check if they're not children of a hybrid inverter
            else:
                parent_ids = properties.get("nodeParentsIds", [])
                is_child_of_hybrid = False
                for parent_id in parent_ids:
                    parent_device = devices_by_node_id.get(parent_id)
                    if parent_device and parent_device.get("properties", {}).get("nodeType") == DEVICE_TYPE_HYBRID_INVERTER:
                        is_child_of_hybrid = True
                        _LOGGER.debug("Device %s is a child of hybrid inverter %s", node_id, parent_id)
                        break
                is_primary = not is_child_of_hybrid
                if is_primary:
                    _LOGGER.debug("Device %s is primary because it's not a child of a hybrid inverter", node_id)

            if is_primary:
                try:
                    # Get latest telemetry data for this device
                    latest_data = await self.get_device_latest_data(device_data)
                    device_data["_latest_data"] = latest_data
                    _LOGGER.debug("Got latest data for device %s: %s", node_id, latest_data)

                    # Get latest telemetry data for children
                    for child_device in device_data["_processed_children"]:
                        child_latest_data = await self.get_device_latest_data(child_device)
                        child_device["_latest_data"] = child_latest_data
                        _LOGGER.debug("Got latest data for child device %s: %s", 
                                    child_device.get("properties", {}).get("nodeId"), 
                                    child_latest_data)

                    # Mark parent as updated if any child updated
                    device_data["_children_last_updated"] = datetime.now(timezone.utc).isoformat()

                    # Assign a deepcopy to ensure object reference changes for HA updates
                    processed_devices[node_id] = copy.deepcopy(device_data)
                except Exception as e:
                    _LOGGER.error("Error getting latest data for device %s: %s", node_id, e)

        _LOGGER.info("Processed %s primary devices for Home Assistant.", len(processed_devices))
        for node_id, dev_data in processed_devices.items():
            _LOGGER.debug("Primary device: %s, Type: %s, Children found: %s", 
                         node_id, 
                         dev_data.get("properties",{}).get("nodeType"), 
                         len(dev_data.get("_processed_children",[])))
            for child_dev in dev_data.get("_processed_children",[]):
                _LOGGER.debug("  Child: %s, Type: %s", 
                            child_dev.get("properties",{}).get("nodeId"), 
                            child_dev.get("properties",{}).get("nodeType"))
        return processed_devices

    async def close(self) -> None:
        """Close the client session."""
        await self._session.close()

