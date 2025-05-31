"""API client for Eniris HACS."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp.client_exceptions import ClientConnectorError, ClientResponseError

from .const import (
    ACCESS_TOKEN_URL,
    DEVICES_URL,
    LOGIN_URL,
    SUPPORTED_NODE_TYPES,
    HEADER_CONTENT_TYPE_JSON,
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
            response_data = await self._request("GET", ACCESS_TOKEN_URL, headers=headers)
            if response_data and "accessToken" in response_data:
                self._access_token = response_data["accessToken"]
                # Optionally handle "expiresIn" if provided by API to manage token lifecycle
                # self._access_token_expires_at = time.time() + response_data.get("expiresIn", 3600)
                _LOGGER.info("Successfully obtained access token.")
                return self._access_token
            _LOGGER.error("Failed to get access token: 'accessToken' not in response or empty response. Response: %s", response_data)
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

    async def get_processed_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get devices and process them for hierarchy and supported types."""
        raw_devices = await self.get_devices()
        if not raw_devices:
            return {}

        devices_by_node_id: Dict[str, Dict[str, Any]] = {}
        processed_devices: Dict[str, Dict[str, Any]] = {}

        # First pass: index all devices by their nodeId and filter by supported types
        for device_data in raw_devices:
            properties = device_data.get("properties", {})
            node_id = properties.get("nodeId")
            node_type = properties.get("nodeType")

            if not node_id:
                _LOGGER.warning("Device data missing 'nodeId': %s", device_data.get("id"))
                continue
            
            device_data["_processed_children"] = [] # To store actual child device data

            devices_by_node_id[node_id] = device_data

        # Second pass: build hierarchy and filter primary devices
        # Primary devices are those not children of another processed device of a primary type
        # and are of a supported type.
        all_child_node_ids = set()

        for node_id, device_data in devices_by_node_id.items():
            properties = device_data.get("properties", {})
            node_type = properties.get("nodeType")

            if node_type not in SUPPORTED_NODE_TYPES:
                _LOGGER.debug("Skipping device %s, nodeType '%s' is not supported.", node_id, node_type)
                continue

            # Populate children for this device
            child_node_ids = properties.get("nodeChildrenIds", [])
            for child_node_id in child_node_ids:
                if child_node_id in devices_by_node_id:
                    child_device_data = devices_by_node_id[child_node_id]
                    # Check if child is also a supported type before adding
                    if child_device_data.get("properties", {}).get("nodeType") in SUPPORTED_NODE_TYPES:
                        device_data["_processed_children"].append(child_device_data)
                        all_child_node_ids.add(child_node_id)
                else:
                    _LOGGER.warning("Child node ID %s for parent %s not found in fetched devices.", child_node_id, node_id)
        
        # Third pass: identify top-level devices to create HA devices for
        # These are devices that are not themselves children of other *supported* devices.
        for node_id, device_data in devices_by_node_id.items():
            properties = device_data.get("properties", {})
            node_type = properties.get("nodeType")

            if node_type not in SUPPORTED_NODE_TYPES:
                continue # Already filtered, but good to double check

            # A device is top-level if it's not in the set of children *that were processed*.
            # This handles the case where a hybrid inverter might be listed as a child of something
            # non-supported, but we still want to treat the hybrid inverter as a primary device.
            if node_id not in all_child_node_ids:
                 _LOGGER.debug("Adding device %s (type: %s) as a primary HA device.", node_id, node_type)
                 processed_devices[node_id] = device_data
            else:
                # If it is a child, check if its parent is *not* a primary device type.
                # This is tricky. The current logic is: if it's a child of *any* device
                # that we indexed, and that child relationship was processed, it's not top-level.
                # The goal is to avoid adding, for example, a battery separately if it's already
                # a child of a hybrid inverter that *is* being added.
                is_child_of_primary = False
                for parent_node_id, parent_data in devices_by_node_id.items():
                    if node_id in parent_data.get("properties", {}).get("nodeChildrenIds", []):
                        # Check if this parent is one of the devices we are adding as primary
                        if parent_node_id in processed_devices: # This check might be problematic due to order of iteration
                             is_child_of_primary = True
                             break
                if not is_child_of_primary:
                    _LOGGER.debug(
                        "Device %s (type: %s) is a child, but its parent is not a primary HA device (or not yet processed as such). Adding it as primary.",
                        node_id, node_type
                    )
                    # This logic might need refinement based on how strictly "hierarchy" should be enforced.
                    # For now, if it's supported and not explicitly a child of an *already selected* primary device, add it.
                    # A simpler rule might be: if it's a hybridInverter, it's always primary.
                    # If it's another supported type AND not a child of a hybridInverter, it's primary.
                    
                    # Simpler approach:
                    # If device_type is hybridInverter, it's a primary candidate.
                    # If device_type is battery, solarOptimizer, powerMeter, it's a primary candidate
                    # *unless* it's a child of a hybridInverter that is also in devices_by_node_id.
                    
                    is_child_of_hybrid_inverter = False
                    if node_type != DEVICE_TYPE_HYBRID_INVERTER:
                        for p_id, p_data in devices_by_node_id.items(): # Check all devices
                            if p_data.get("properties", {}).get("nodeType") == DEVICE_TYPE_HYBRID_INVERTER and \
                               node_id in p_data.get("properties", {}).get("nodeChildrenIds", []):
                                is_child_of_hybrid_inverter = True
                                break
                    
                    if node_type == DEVICE_TYPE_HYBRID_INVERTER or not is_child_of_hybrid_inverter:
                        _LOGGER.debug("Adding device %s (type: %s) as a primary HA device (refined logic).", node_id, node_type)
                        processed_devices[node_id] = device_data
                    else:
                        _LOGGER.debug("Skipping device %s (type: %s) as it's a child of a hybrid inverter.", node_id, node_type)

        _LOGGER.info("Processed %s primary devices for Home Assistant.", len(processed_devices))
        for node_id, dev_data in processed_devices.items():
            _LOGGER.debug("Primary device: %s, Type: %s, Children found: %s", node_id, dev_data.get("properties",{}).get("nodeType"), len(dev_data.get("_processed_children",[])))
            for child_dev in dev_data.get("_processed_children",[]):
                _LOGGER.debug("  Child: %s, Type: %s", child_dev.get("properties",{}).get("nodeId"), child_dev.get("properties",{}).get("nodeType"))
        return processed_devices

    async def close(self) -> None:
        """Close the client session."""
        await self._session.close()

