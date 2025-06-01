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

    _RP_ONE_M_UPDATE_INTERVAL = timedelta(seconds=60) # Interval for rp_one_m updates
    _DEFAULT_RETENTION_POLICIES = ["rp_one_m", "rp_one_s"] # All supported RPs

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
        self._rp_one_m_last_update_times: Dict[str, datetime] = {} # node_id -> last rp_one_m update time

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

    async def get_device_telemetry(self, node_id: str, measurement: str, fields: List[str], retention_policies_to_fetch: List[str]) -> Dict[str, Any]:
        """Get telemetry data for a specific device for specified retention policies."""
        access_token = await self.ensure_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # Get the last 5 minutes of data
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=5)

        queries = []
        # Only iterate over the policies we want to fetch for this call
        for rp in retention_policies_to_fetch:
            if rp not in self._DEFAULT_RETENTION_POLICIES: # Validate against known policies
                _LOGGER.warning("Requested to fetch unknown retention policy %s for node %s. Skipping.", rp, node_id)
                continue
            for field in fields:
                queries.append({
                    "select": [field],
                    "from": {
                        "namespace": {
                            "version": "1",
                            "database": "beauvent",
                            "retentionPolicy": rp
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
                            "retentionPolicy": rp
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
            latest_timestamp = None # We'll store the latest timestamp across all series

            # Each field has two queries (latest, sum) per retention policy.
            # The number of fields is len(fields)
            # The number of retention policies to fetch *in this call* is len(retention_policies_to_fetch)

            num_fields = len(fields)
            # Adjust for potentially filtered list of RPs being fetched
            active_rps_in_response_order = [rp for rp in retention_policies_to_fetch if rp in self._DEFAULT_RETENTION_POLICIES]
            num_active_rps = len(active_rps_in_response_order)
            
            if not queries: # No valid RPs or fields to query
                _LOGGER.debug("No queries generated for telemetry fetch for node %s, RPs: %s, fields: %s", node_id, retention_policies_to_fetch, fields)
                return {}

            # The response will only contain data for the RPs that were actually queried.
            # We need to map the response index based on active_rps_in_response_order.
            for rp_idx, rp in enumerate(active_rps_in_response_order):
                for field_idx, field in enumerate(fields):
                    base_idx = (rp_idx * num_fields * 2) + (field_idx * 2)

                    latest_stmt = response[base_idx] if base_idx < len(response) else None
                    sum_stmt = response[base_idx + 1] if base_idx + 1 < len(response) else None
                    
                    # Create keys like "field_rp_one_m_latest", "field_rp_one_s_sum"
                    # And for backward compatibility / general data, store under field name as dict
                    if field not in result:
                        result[field] = {}


                    # Process latest value for current rp and field
                    if latest_stmt and latest_stmt.get("series"):
                        for series in latest_stmt["series"]:
                            if not series.get("values"):
                                continue
                            latest_value_data = series["values"][-1] # list: [timestamp, val1, val2,...]
                            timestamp = latest_value_data[0]
                            columns = series.get("columns", []) # First column is always 'time'
                            for val_idx, value in enumerate(latest_value_data[1:], 1):
                                if val_idx < len(columns):
                                    col_name = columns[val_idx]
                                    if col_name == field: # Make sure we are processing the correct field
                                        result[field][f"{rp}_latest"] = value
                                        # Update the overall latest_timestamp if this one is newer
                                        if latest_timestamp is None:
                                            latest_timestamp = timestamp
                                        elif isinstance(timestamp, (int, float)) and isinstance(latest_timestamp, (int, float)) and timestamp > latest_timestamp:
                                            latest_timestamp = timestamp
                                        elif isinstance(timestamp, str) and isinstance(latest_timestamp, str): # Assuming ISO strings if not numbers
                                            try:
                                                dt_timestamp = datetime.fromisoformat(timestamp.rstrip('Z'))
                                                dt_latest_timestamp = datetime.fromisoformat(latest_timestamp.rstrip('Z'))
                                                if dt_timestamp > dt_latest_timestamp:
                                                    latest_timestamp = timestamp
                                            except ValueError: # Handle cases where parsing might fail or types are mixed unexpectedly
                                                pass # Keep existing latest_timestamp


                    # Process sum value for current rp and field
                    if sum_stmt and sum_stmt.get("series"):
                        for series in sum_stmt["series"]:
                            if not series.get("values"):
                                continue
                            sum_value_data = series["values"][-1] # list: [timestamp, sum_val1, sum_val2,...]
                            columns = series.get("columns", []) # First column is 'time'
                            for val_idx, value in enumerate(sum_value_data[1:], 1):
                                if val_idx < len(columns):
                                    col_name = columns[val_idx] # e.g., "sum_field"
                                    # Ensure we match the correct summed field, strip "sum_" prefix
                                    if col_name.startswith("sum_") and col_name[4:] == field:
                                        result[field][f"{rp}_sum"] = value

            # Add the overall latest timestamp in UTC
            if latest_timestamp:
                # Handle both integer (Unix ms) and ISO8601 string
                if isinstance(latest_timestamp, (int, float)):
                    result["timestamp"] = datetime.fromtimestamp(latest_timestamp / 1000, timezone.utc)
                elif isinstance(latest_timestamp, str):
                    # Attempt to parse various ISO 8601 formats, including those with 'Z'
                    ts_str = latest_timestamp.rstrip('Z')
                    if '.' in ts_str: # Check if fractional seconds are present
                        try:
                            dt_obj = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f")
                        except ValueError:
                            try: # Fallback for non-fractional
                                dt_obj = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                            except ValueError:
                                _LOGGER.warning("Could not parse timestamp string: %s", latest_timestamp)
                                dt_obj = None
                    else:
                        try:
                            dt_obj = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                        except ValueError:
                             _LOGGER.warning("Could not parse timestamp string: %s", latest_timestamp)
                             dt_obj = None
                    
                    if dt_obj:
                        result["timestamp"] = dt_obj.replace(tzinfo=timezone.utc)


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
                        # Process the response as before, adapted for new structure
                        result = {}
                        latest_timestamp = None
                        num_fields = len(fields)
                        # Adjust for potentially filtered list of RPs being fetched in retry
                        active_rps_in_response_order_retry = [rp for rp in retention_policies_to_fetch if rp in self._DEFAULT_RETENTION_POLICIES]
                        num_active_rps_retry = len(active_rps_in_response_order_retry)

                        if not queries: # No queries means no response processing needed
                            return {}

                        for rp_idx, rp in enumerate(active_rps_in_response_order_retry):
                            for field_idx, field in enumerate(fields):
                                base_idx = (rp_idx * num_fields * 2) + (field_idx * 2)
                                latest_stmt = response[base_idx] if base_idx < len(response) else None
                                sum_stmt = response[base_idx + 1] if base_idx + 1 < len(response) else None
                                
                                if field not in result:
                                    result[field] = {}

                                # Process latest value
                                if latest_stmt and latest_stmt.get("series"):
                                    for series_data in latest_stmt["series"]:
                                        if not series_data.get("values"):
                                            continue
                                        latest_value_data = series_data["values"][-1]
                                        timestamp = latest_value_data[0]
                                        columns = series_data.get("columns", [])
                                        for val_idx, value in enumerate(latest_value_data[1:], 1):
                                            if val_idx < len(columns):
                                                col_name = columns[val_idx]
                                                if col_name == field:
                                                    result[field][f"{rp}_latest"] = value
                                                    if latest_timestamp is None:
                                                        latest_timestamp = timestamp
                                                    elif isinstance(timestamp, (int, float)) and isinstance(latest_timestamp, (int, float)) and timestamp > latest_timestamp:
                                                        latest_timestamp = timestamp
                                                    elif isinstance(timestamp, str) and isinstance(latest_timestamp, str):
                                                        try:
                                                            dt_timestamp = datetime.fromisoformat(timestamp.rstrip('Z'))
                                                            dt_latest_timestamp = datetime.fromisoformat(latest_timestamp.rstrip('Z'))
                                                            if dt_timestamp > dt_latest_timestamp:
                                                                latest_timestamp = timestamp
                                                        except ValueError:
                                                            pass


                                # Process sum value
                                if sum_stmt and sum_stmt.get("series"):
                                    for series_data in sum_stmt["series"]:
                                        if not series_data.get("values"):
                                            continue
                                        sum_value_data = series_data["values"][-1]
                                        columns = series_data.get("columns", [])
                                        for val_idx, value in enumerate(sum_value_data[1:], 1):
                                            if val_idx < len(columns):
                                                col_name = columns[val_idx]
                                                if col_name.startswith("sum_") and col_name[4:] == field:
                                                    result[field][f"{rp}_sum"] = value
                        if latest_timestamp:
                            # Handle both integer (Unix ms) and ISO8601 string
                            if isinstance(latest_timestamp, (int,float)):
                                result["timestamp"] = datetime.fromtimestamp(latest_timestamp / 1000, timezone.utc)
                            elif isinstance(latest_timestamp, str):
                                ts_str = latest_timestamp.rstrip('Z')
                                if '.' in ts_str:
                                    try:
                                        dt_obj = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f")
                                    except ValueError:
                                        try:
                                            dt_obj = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                                        except ValueError:
                                            _LOGGER.warning("Could not parse timestamp string on retry: %s", latest_timestamp)
                                            dt_obj = None
                                else:
                                    try:
                                        dt_obj = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                                    except ValueError:
                                        _LOGGER.warning("Could not parse timestamp string on retry: %s", latest_timestamp)
                                        dt_obj = None
                                if dt_obj:
                                    result["timestamp"] = dt_obj.replace(tzinfo=timezone.utc)
                        return result
                except Exception as retry_error:
                    _LOGGER.error("Still failed to fetch telemetry after token refresh: %s", retry_error)
            return {}

    async def get_device_latest_data(self, device_data: Dict[str, Any], retention_policies_to_fetch: List[str]) -> Dict[str, Any]:
        """Get the latest telemetry data for a device for specified RPs and merge it into device_data."""
        properties = device_data.get("properties", {})
        node_id = properties.get("nodeId")
        if not node_id:
            _LOGGER.warning("Device data missing 'nodeId' in get_device_latest_data. ID: %s", device_data.get("id"))
            return device_data # Return original data

        series_configs = properties.get("nodeInfluxSeries", [])
        if not series_configs:
            _LOGGER.debug("No nodeInfluxSeries configuration for device %s.", node_id)
            return device_data # Return original data

        # Ensure _latest_data exists, even if empty, to allow merging.
        if "_latest_data" not in device_data:
            device_data["_latest_data"] = {}

        if not retention_policies_to_fetch:
            _LOGGER.debug("No retention policies specified to fetch for node %s. Returning existing data.", node_id)
            return device_data

        _LOGGER.debug("Fetching RPs %s for device %s", retention_policies_to_fetch, node_id)

        something_fetched = False
        for series_config in series_configs:
            measurement = series_config.get("measurement")
            fields = series_config.get("fields", [])
            
            if not measurement or not fields:
                continue

            # Get telemetry data for this series, only for the specified RPs
            new_telemetry_data = await self.get_device_telemetry(node_id, measurement, fields, retention_policies_to_fetch)
            
            if new_telemetry_data:
                something_fetched = True
                # Merge new_telemetry_data into existing device_data["_latest_data"]
                # The structure from get_device_telemetry is: {field: {rp_key: value}, timestamp: ...}
                timestamp_from_new_data = new_telemetry_data.pop("timestamp", None)

                for field, rp_values in new_telemetry_data.items():
                    if field not in device_data["_latest_data"]:
                        device_data["_latest_data"][field] = {}
                    if isinstance(rp_values, dict):
                        device_data["_latest_data"][field].update(rp_values) # Merge rp_one_m_latest, rp_one_s_latest etc.
                    else:
                        # This case should ideally not happen if get_device_telemetry returns the new structure
                        # but as a fallback, if a direct value is under field (e.g. old data struct or simple value)
                        # we can place it, but it might be overwritten if rp_values for this field come later.
                        device_data["_latest_data"][field] = rp_values 

                # Update the overall timestamp if the new data has a more recent one
                if timestamp_from_new_data:
                    current_timestamp = device_data["_latest_data"].get("timestamp")
                    if current_timestamp is None or timestamp_from_new_data > current_timestamp:
                        device_data["_latest_data"]["timestamp"] = timestamp_from_new_data
            
        if something_fetched:
            _LOGGER.debug("Updated _latest_data for %s after fetching %s: %s", node_id, retention_policies_to_fetch, device_data["_latest_data"])
        else:
            _LOGGER.debug("No new telemetry data was fetched for %s with RPs %s.", node_id, retention_policies_to_fetch)

        return device_data # Return the modified device_data

    async def get_processed_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get devices and process them for hierarchy, supported types, and selective telemetry update."""
        raw_devices = await self.get_devices()
        if not raw_devices:
            _LOGGER.error("No devices returned from API")
            return {}

        _LOGGER.debug("Raw devices from API: %s", raw_devices)
        # Use a class member or persistent store if you need to maintain device_data across calls
        # For now, we rebuild devices_by_node_id each time, but merge telemetry into it.
        # If get_processed_devices is the main entry point for the coordinator update,
        # then self._all_devices_cache (or similar) should be used and updated.
        # Let's assume for now we have a cached dict `self._cached_devices` that persists across calls.
        # For simplicity in this change, I will re-fetch structure but this is an optimization point.

        current_time = datetime.now(timezone.utc)
        processed_devices_output: Dict[str, Dict[str, Any]] = {}

        # Index all raw devices first (structural part)
        # This part could be optimized to run less frequently than telemetry updates.
        temp_devices_by_node_id: Dict[str, Dict[str, Any]] = {}
        for device_data_from_api in raw_devices:
            properties = device_data_from_api.get("properties", {})
            node_id = properties.get("nodeId")
            if not node_id:
                _LOGGER.warning("Device data from API missing 'nodeId': %s", device_data_from_api.get("id"))
                continue
            # Initialize with new structural data, ensure _latest_data and _processed_children exist
            temp_devices_by_node_id[node_id] = {
                **device_data_from_api, 
                "_latest_data": {}, 
                "_processed_children": []
            }

        # Second pass: build hierarchy and fetch telemetry selectively
        for node_id, current_device_struct in temp_devices_by_node_id.items():
            properties = current_device_struct.get("properties", {})
            node_type = properties.get("nodeType")
            _LOGGER.debug("Processing device %s of type %s for telemetry update strategy", node_id, node_type)

            if node_type not in SUPPORTED_NODE_TYPES:
                _LOGGER.debug("Skipping unsupported device type %s for device %s", node_type, node_id)
                continue

            # Populate children for this device (structural)
            child_node_ids = properties.get("nodeChildrenIds", [])
            current_device_struct["_processed_children"] = [] # Reset children based on new structure
            for child_node_id in child_node_ids:
                if child_node_id in temp_devices_by_node_id:
                    current_device_struct["_processed_children"].append(temp_devices_by_node_id[child_node_id])
            
            # Determine if this device should be a primary device (structural)
            is_primary = (node_type == DEVICE_TYPE_HYBRID_INVERTER) or \
                         not any(temp_devices_by_node_id.get(parent_id, {}).get("properties", {}).get("nodeType") == DEVICE_TYPE_HYBRID_INVERTER 
                                 for parent_id in properties.get("nodeParentsIds", []))

            if is_primary:
                try:
                    # This is where we decide which RPs to fetch for the primary device
                    rps_to_fetch_for_primary: List[str] = ["rp_one_s"] # Always fetch rp_one_s
                    last_rp_m_update = self._rp_one_m_last_update_times.get(node_id)
                    if not last_rp_m_update or (current_time - last_rp_m_update) >= self._RP_ONE_M_UPDATE_INTERVAL:
                        rps_to_fetch_for_primary.append("rp_one_m")
                        self._rp_one_m_last_update_times[node_id] = current_time
                    
                    _LOGGER.debug("Primary device %s: fetching RPs: %s", node_id, rps_to_fetch_for_primary)
                    # get_device_latest_data will fetch and MERGE into current_device_struct["_latest_data"]
                    updated_device_with_telemetry = await self.get_device_latest_data(current_device_struct, rps_to_fetch_for_primary)
                    # The above call modifies current_device_struct by reference if _latest_data is a dict within it.
                    # For safety, we can reassign, though it should be the same object if modified in place.
                    current_device_struct = updated_device_with_telemetry 

                    # Fetch/update telemetry for children of this primary device
                    for child_device_struct in current_device_struct.get("_processed_children", []):
                        child_node_id = child_device_struct.get("properties", {}).get("nodeId")
                        if not child_node_id:
                            continue

                        rps_to_fetch_for_child: List[str] = ["rp_one_s"]
                        last_rp_m_update_child = self._rp_one_m_last_update_times.get(child_node_id)
                        if not last_rp_m_update_child or (current_time - last_rp_m_update_child) >= self._RP_ONE_M_UPDATE_INTERVAL:
                            rps_to_fetch_for_child.append("rp_one_m")
                            self._rp_one_m_last_update_times[child_node_id] = current_time
                        
                        _LOGGER.debug("Child device %s of %s: fetching RPs: %s", child_node_id, node_id, rps_to_fetch_for_child)
                        # get_device_latest_data will merge into child_device_struct["_latest_data"]
                        await self.get_device_latest_data(child_device_struct, rps_to_fetch_for_child)
                        # child_device_struct is modified in place within current_device_struct._processed_children

                    # Mark parent as updated (for HA coordinator to see a change)
                    # The deepcopy below will make HA see it as a new object.
                    current_device_struct["_last_telemetry_update_attempt"] = current_time.isoformat()

                    processed_devices_output[node_id] = copy.deepcopy(current_device_struct)

                except Exception as e:
                    _LOGGER.error("Error processing device %s for telemetry: %s", node_id, e)
                    # Optionally, still add the device structure without fresh telemetry if needed
                    # processed_devices_output[node_id] = copy.deepcopy(current_device_struct) 

        _LOGGER.info("Processed %s primary devices for Home Assistant with selective telemetry update.", len(processed_devices_output))
        # For debugging, log the state of a device's _latest_data if needed
        # for node_id, dev_data in processed_devices_output.items():
        #     _LOGGER.debug("Final _latest_data for %s: %s", node_id, dev_data.get("_latest_data"))
        return processed_devices_output

    async def close(self) -> None:
        """Close the client session."""
        await self._session.close()

