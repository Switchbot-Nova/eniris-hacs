"""API client for Eniris HACS."""

import copy
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
from aiohttp.client_exceptions import ClientConnectorError, ClientResponseError

from .const import (
    ACCESS_TOKEN_URL,
    DEVICE_TYPE_HYBRID_INVERTER,
    DEVICES_URL,
    HEADER_CONTENT_TYPE_JSON,
    LOGIN_URL,
    SUPPORTED_NODE_TYPES,
)

_LOGGER = logging.getLogger(__name__)

# HTTP status codes
_HTTP_OK = 200
_HTTP_CREATED = 201


_FRACTIONAL_SECONDS_RE = re.compile(r"\.(\d+)")


def _summarize_for_log(value: Any) -> str:
    """Return a small, non-spammy summary suitable for WARNING/INFO logs."""
    if value is None:
        return "None"
    if isinstance(value, dict):
        keys = list(value.keys())
        preview = keys[:25]
        suffix = "…" if len(keys) > len(preview) else ""
        return f"dict(keys={preview}{suffix})"
    if isinstance(value, list):
        return f"list(len={len(value)})"
    if isinstance(value, str):
        return f"str(len={len(value)})"
    return type(value).__name__


def _normalize_iso8601(ts: str) -> str:
    """
    Normalize Eniris timestamps for Python parsing.

    - Converts trailing 'Z' to '+00:00'
    - Truncates fractional seconds to microseconds (6 digits)
    """
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"

    match = _FRACTIONAL_SECONDS_RE.search(ts)
    if not match:
        return ts

    fraction = match.group(1)
    normalized = (fraction + "000000")[:6]
    return ts[: match.start(1)] + normalized + ts[match.end(1) :]


def _parse_timestamp_to_utc(ts: Any) -> datetime | None:
    """Parse an Eniris timestamp to a timezone-aware UTC datetime."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, UTC)
    if not isinstance(ts, str):
        return None

    normalized = _normalize_iso8601(ts)
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class EnirisHacsApiError(Exception):
    """Custom exception for API errors."""


class EnirisHacsAuthError(EnirisHacsApiError):
    """Custom exception for authentication errors."""


class EnirisHacsApiClient:
    """API Client for Eniris HACS."""

    _SUPPORTED_RETENTION_POLICY = "rp_one_m"

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        # Home Assistant provides a shared ClientSession via async_get_clientsession(hass).
        # We must never close that shared session. Only close sessions we create ourselves.
        self._session_owner = session is None
        self._session = session or aiohttp.ClientSession()
        self._refresh_token: str | None = None
        self._access_token: str | None = None

    async def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: Any | None = None,
        *,
        is_text_response: bool = False,
    ) -> Any:
        """Make an API request."""

        def _truncate(value: str, limit: int = 300) -> str:
            value = value.replace("\n", " ").replace("\r", " ")
            return value if len(value) <= limit else f"{value[:limit]}…"

        # Intentionally do NOT log headers/body here: it can contain passwords and bearer tokens.
        try:
            async with self._session.request(
                method, url, headers=headers, json=data
            ) as response:
                if response.status in (_HTTP_OK, _HTTP_CREATED):
                    if is_text_response:
                        return await response.text()
                    try:
                        return await response.json()
                    except ValueError as e:
                        _LOGGER.warning(
                            "Failed to parse JSON response: %s. Falling back to text response.",
                            e,
                        )
                        return await response.text()

                body_text = await response.text()
                if response.status in (401, 403):
                    _LOGGER.error(
                        "Authentication error %s for %s", response.status, url
                    )
                    raise EnirisHacsAuthError(
                        f"Authentication failed ({response.status}): {_truncate(body_text)}"
                    )

                _LOGGER.error("API request failed %s for %s", response.status, url)
                raise EnirisHacsApiError(
                    f"API request failed ({response.status}): {_truncate(body_text)}"
                )
        except ClientConnectorError as e:
            _LOGGER.error("Connection error during API request to %s: %s", url, e)
            raise EnirisHacsApiError(f"Connection error: {e}") from e
        except (
            ClientResponseError
        ) as e:  # Should be caught by status checks, but good to have
            _LOGGER.error("Client response error during API request to %s: %s", url, e)
            raise EnirisHacsApiError(
                f"Client response error: {e.message} ({e.status})"
            ) from e
        except TimeoutError as e:
            _LOGGER.error("Timeout during API request to %s: %s", url, e)
            raise EnirisHacsApiError(f"Request timed out: {e}") from e

    async def get_refresh_token(self) -> str:
        """Get a refresh token."""
        payload = {"username": self._email, "password": self._password}
        try:
            response_text = await self._request(
                "POST",
                LOGIN_URL,
                headers=HEADER_CONTENT_TYPE_JSON,
                data=payload,
                is_text_response=True,
            )
            if response_text:
                # Clean up the response text - remove any whitespace and quotes
                self._refresh_token = response_text.strip().strip('"')
                if self._refresh_token is None:
                    raise EnirisHacsAuthError("Refresh token is empty after parsing.")
                return self._refresh_token
            _LOGGER.error("Failed to get refresh token: Empty response.")
            raise EnirisHacsAuthError("Failed to get refresh token: Empty response")
        except EnirisHacsApiError as e:
            _LOGGER.error("Error obtaining refresh token: %s", e)
            raise EnirisHacsAuthError(f"Failed to obtain refresh token: {e}") from e

    async def get_access_token(self) -> str:
        """Get an access token using the refresh token."""
        if not self._refresh_token:
            await self.get_refresh_token()  # This will raise if it fails

        if not self._refresh_token:  # Should not happen if above call succeeded
            _LOGGER.error("Refresh token is still missing after attempting to fetch.")
            raise EnirisHacsAuthError("Refresh token is missing.")

        headers = {"Authorization": f"Bearer {self._refresh_token}"}
        try:
            response_data = await self._request(
                "GET", ACCESS_TOKEN_URL, headers=headers, is_text_response=True
            )
            if isinstance(response_data, str):
                # Handle plain text response
                self._access_token = response_data.strip().strip('"')
                if not self._access_token:
                    raise EnirisHacsAuthError("Access token is empty after parsing.")
                return self._access_token
            if isinstance(response_data, dict) and "accessToken" in response_data:
                # Handle JSON response
                self._access_token = response_data["accessToken"]
                if not self._access_token:
                    raise EnirisHacsAuthError("Access token is empty after parsing.")
            _LOGGER.error(
                "Failed to get access token: Invalid response format. Response: %s",
                response_data,
            )
            raise EnirisHacsAuthError(
                "Failed to get access token: Invalid response format"
            )
        except EnirisHacsApiError as e:
            _LOGGER.error("Error obtaining access token: %s", e)
            self._access_token = None
            # If the refresh token is no longer accepted, drop it so the next attempt re-logins.
            if isinstance(e, EnirisHacsAuthError):
                self._refresh_token = None
            raise EnirisHacsAuthError(f"Failed to obtain access token: {e}") from e

    async def ensure_access_token(self) -> str:
        """Ensure a valid access token is available, refreshing if necessary."""
        if not self._access_token:
            await self.get_access_token()

        if not self._access_token:
            _LOGGER.error("Access token is still missing after attempting to fetch.")
            raise EnirisHacsAuthError("Access token is missing.")
        return self._access_token

    async def get_devices(self) -> list[dict[str, Any]]:
        """Get a list of devices."""
        access_token = await self.ensure_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            response_data = await self._request("GET", DEVICES_URL, headers=headers)
            if (
                response_data
                and "device" in response_data
                and isinstance(response_data["device"], list)
            ):
                devices = response_data["device"]
                return devices
            _LOGGER.warning(
                "No 'device' list found in API response or response is not as expected. Response summary: %s",
                _summarize_for_log(response_data),
            )
            return []  # Return empty list if structure is not as expected
        except EnirisHacsApiError as e:
            _LOGGER.error("Error fetching devices: %s", e)
            # If it's an auth error, it might mean the access token expired mid-flight.
            # A more robust system might retry getting an access token once.
            if isinstance(e, EnirisHacsAuthError):
                self._access_token = None  # Clear current access token to force refresh
                access_token = await self.ensure_access_token()  # Retry getting token
                headers = {"Authorization": f"Bearer {access_token}"}
                # Retry fetching devices once
                response_data = await self._request("GET", DEVICES_URL, headers=headers)
                if (
                    response_data
                    and "device" in response_data
                    and isinstance(response_data["device"], list)
                ):
                    devices = response_data["device"]
                    _LOGGER.info(
                        "Successfully fetched %s devices on retry.", len(devices)
                    )
                    return devices
                _LOGGER.error(
                    "Still failed to fetch devices after token refresh: %s",
                    _summarize_for_log(response_data),
                )
                return []
            raise  # Re-raise original error if not auth or if retry failed

    async def get_device_telemetry(
        self,
        node_id: str,
        measurement: str,
        fields: list[str],
        retention_policy: str | None = None,
    ) -> dict[str, Any]:
        """
        Get telemetry data for a specific device.

        Only the retention policy `rp_one_m` is supported.
        """
        access_token = await self.ensure_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        rp = retention_policy or self._SUPPORTED_RETENTION_POLICY
        if rp != self._SUPPORTED_RETENTION_POLICY:
            _LOGGER.warning(
                "Unsupported retention policy %s requested for node %s; forcing %s.",
                rp,
                node_id,
                self._SUPPORTED_RETENTION_POLICY,
            )
            rp = self._SUPPORTED_RETENTION_POLICY

        # Get the last 5 minutes of data
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=5)

        queries: list[dict[str, Any]] = []
        for field in fields:
            queries.append(
                {
                    "select": [field],
                    "from": {
                        "namespace": {
                            "version": "1",
                            "database": "beauvent",
                            "retentionPolicy": rp,
                        },
                        "measurement": measurement,
                    },
                    "where": {
                        "time": [
                            {
                                "operator": ">=",
                                "value": int(start_time.timestamp() * 1000),
                            },
                            {
                                "operator": "<",
                                "value": int(end_time.timestamp() * 1000),
                            },
                        ],
                        "tags": {"nodeId": node_id},
                    },
                    # Fetch raw values for the time window and compute latest client-side.
                    # This avoids server-side sum() errors on non-numeric fields which can
                    # abort subsequent statements.
                    "orderBy": "ASC",
                    "limit": 10000,
                }
            )

        try:
            response = await self._request(
                "POST",
                "https://api.eniris.be/v1/telemetry/query",
                headers=headers,
                data=queries,
            )

            if not response or not isinstance(response, list) or len(response) == 0:
                _LOGGER.warning("No telemetry data received for device %s", node_id)
                return {}

            result = {}
            latest_timestamp = (
                None  # We'll store the latest timestamp across all series
            )

            if not queries:  # No valid RPs or fields to query
                return {}

            # Each field has one query (raw values); we compute latest locally.
            for field_idx, field in enumerate(fields):
                stmt = response[field_idx] if field_idx < len(response) else None

                if field not in result:
                    result[field] = {}

                if not stmt or not isinstance(stmt, dict) or not stmt.get("series"):
                    continue

                # Process latest value
                for series in stmt["series"]:
                    values = series.get("values")
                    if not values:
                        continue

                    columns = series.get("columns", [])
                    if field not in columns:
                        continue
                    field_col_idx = columns.index(field)

                    # Latest
                    latest_value_data = values[-1]
                    if (
                        isinstance(latest_value_data, list)
                        and len(latest_value_data) > field_col_idx
                    ):
                        timestamp = latest_value_data[0]
                        result[field][f"{rp}_latest"] = latest_value_data[field_col_idx]

                        if latest_timestamp is None or (
                            isinstance(timestamp, (int, float))
                            and isinstance(latest_timestamp, (int, float))
                            and timestamp > latest_timestamp
                        ):
                            latest_timestamp = timestamp
                        elif isinstance(timestamp, str) and isinstance(
                            latest_timestamp, str
                        ):
                            try:
                                dt_timestamp = _parse_timestamp_to_utc(timestamp)
                                dt_latest_timestamp = _parse_timestamp_to_utc(
                                    latest_timestamp
                                )
                                if dt_timestamp and dt_latest_timestamp:
                                    if dt_timestamp > dt_latest_timestamp:
                                        latest_timestamp = timestamp
                            except Exception:
                                pass

            # Add the overall latest timestamp in UTC
            if latest_timestamp:
                dt_obj = _parse_timestamp_to_utc(latest_timestamp)
                if dt_obj:
                    result["timestamp"] = dt_obj
                else:
                    _LOGGER.warning(
                        "Could not parse timestamp string: %s",
                        latest_timestamp,
                    )

            return result

        except EnirisHacsApiError as e:
            _LOGGER.error("Error fetching telemetry data for device %s: %s", node_id, e)
            # If it's an auth error, it might mean the access token expired mid-flight.
            # Retry getting an access token once.
            if isinstance(e, EnirisHacsAuthError):
                _LOGGER.info(
                    "Auth error during telemetry fetch, attempting to refresh access token once."
                )
                self._access_token = None  # Clear current access token to force refresh
                access_token = await self.ensure_access_token()  # Retry getting token
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                }
                # Retry fetching telemetry once
                try:
                    response = await self._request(
                        "POST",
                        "https://api.eniris.be/v1/telemetry/query",
                        headers=headers,
                        data=queries,
                    )
                    if response and isinstance(response, list) and len(response) > 0:
                        _LOGGER.info(
                            "Successfully fetched telemetry data on retry for device %s",
                            node_id,
                        )
                        # Process the response as before, adapted for new structure
                        result = {}
                        latest_timestamp = None
                        if (
                            not queries
                        ):  # No queries means no response processing needed
                            return {}

                        for field_idx, field in enumerate(fields):
                            stmt = (
                                response[field_idx]
                                if field_idx < len(response)
                                else None
                            )

                            if field not in result:
                                result[field] = {}

                            # Process latest value
                            if (
                                not stmt
                                or not isinstance(stmt, dict)
                                or not stmt.get("series")
                            ):
                                continue

                            for series_data in stmt["series"]:
                                values = series_data.get("values")
                                if not values:
                                    continue

                                columns = series_data.get("columns", [])
                                if field not in columns:
                                    continue
                                field_col_idx = columns.index(field)

                                latest_value_data = values[-1]
                                if (
                                    isinstance(latest_value_data, list)
                                    and len(latest_value_data) > field_col_idx
                                ):
                                    timestamp = latest_value_data[0]
                                    result[field][f"{rp}_latest"] = latest_value_data[
                                        field_col_idx
                                    ]
                                    if latest_timestamp is None or (
                                        isinstance(timestamp, (int, float))
                                        and isinstance(latest_timestamp, (int, float))
                                        and timestamp > latest_timestamp
                                    ):
                                        latest_timestamp = timestamp
                                    elif isinstance(timestamp, str) and isinstance(
                                        latest_timestamp, str
                                    ):
                                        try:
                                            dt_timestamp = _parse_timestamp_to_utc(
                                                timestamp
                                            )
                                            dt_latest_timestamp = (
                                                _parse_timestamp_to_utc(
                                                    latest_timestamp
                                                )
                                            )
                                            if dt_timestamp and dt_latest_timestamp:
                                                if dt_timestamp > dt_latest_timestamp:
                                                    latest_timestamp = timestamp
                                        except Exception:
                                            pass

                        if latest_timestamp:
                            dt_obj = _parse_timestamp_to_utc(latest_timestamp)
                            if dt_obj:
                                result["timestamp"] = dt_obj
                            else:
                                _LOGGER.warning(
                                    "Could not parse timestamp string on retry: %s",
                                    latest_timestamp,
                                )
                        return result
                except Exception as retry_error:
                    _LOGGER.error(
                        "Still failed to fetch telemetry after token refresh: %s",
                        retry_error,
                    )
            return {}

    async def get_device_latest_data(
        self, device_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Get the latest telemetry data for a device and merge it into device_data."""
        properties = device_data.get("properties", {})
        node_id = properties.get("nodeId")
        if not node_id:
            _LOGGER.warning(
                "Device data missing 'nodeId' in get_device_latest_data. ID: %s",
                device_data.get("id"),
            )
            return device_data  # Return original data

        series_configs = properties.get("nodeInfluxSeries", [])
        if not series_configs:
            return device_data  # Return original data

        # Ensure _latest_data exists, even if empty, to allow merging.
        if "_latest_data" not in device_data:
            device_data["_latest_data"] = {}

        something_fetched = False
        for series_config in series_configs:
            measurement = series_config.get("measurement")
            fields = series_config.get("fields", [])

            if not measurement or not fields:
                continue

            # Get telemetry data for this series, only for the specified RPs
            new_telemetry_data = await self.get_device_telemetry(
                node_id,
                measurement,
                fields,
                retention_policy=self._SUPPORTED_RETENTION_POLICY,
            )

            if new_telemetry_data:
                something_fetched = True
                # Merge new_telemetry_data into existing device_data["_latest_data"]
                # The structure from get_device_telemetry is: {field: {rp_key: value}, timestamp: ...}
                timestamp_from_new_data = new_telemetry_data.pop("timestamp", None)

                for field, rp_values in new_telemetry_data.items():
                    if field not in device_data["_latest_data"]:
                        device_data["_latest_data"][field] = {}
                    if isinstance(rp_values, dict):
                        # Sum values are no longer produced; remove any stale *_sum keys.
                        if isinstance(device_data["_latest_data"].get(field), dict):
                            for existing_key in list(
                                device_data["_latest_data"][field].keys()
                            ):
                                if isinstance(
                                    existing_key, str
                                ) and existing_key.endswith("_sum"):
                                    device_data["_latest_data"][field].pop(
                                        existing_key, None
                                    )
                        device_data["_latest_data"][field].update(
                            rp_values
                        )  # Merge rp_one_m_latest etc.
                    else:
                        # This case should ideally not happen if get_device_telemetry returns the new structure
                        # but as a fallback, if a direct value is under field (e.g. old data struct or simple value)
                        # we can place it, but it might be overwritten if rp_values for this field come later.
                        device_data["_latest_data"][field] = rp_values

                # Update the overall timestamp if the new data has a more recent one
                if timestamp_from_new_data:
                    current_timestamp = device_data["_latest_data"].get("timestamp")
                    if (
                        current_timestamp is None
                        or timestamp_from_new_data > current_timestamp
                    ):
                        device_data["_latest_data"]["timestamp"] = (
                            timestamp_from_new_data
                        )

        return device_data  # Return the modified device_data

    async def get_processed_devices(self) -> dict[str, dict[str, Any]]:
        """Get devices and process them for hierarchy, supported types, and selective telemetry update."""
        raw_devices = await self.get_devices()
        if not raw_devices:
            _LOGGER.error("No devices returned from API")
            return {}
        # Use a class member or persistent store if you need to maintain device_data across calls
        # For now, we rebuild devices_by_node_id each time, but merge telemetry into it.
        # If get_processed_devices is the main entry point for the coordinator update,
        # then self._all_devices_cache (or similar) should be used and updated.
        # Let's assume for now we have a cached dict `self._cached_devices` that persists across calls.
        # For simplicity in this change, I will re-fetch structure but this is an optimization point.

        current_time = datetime.now(UTC)
        processed_devices_output: dict[str, dict[str, Any]] = {}

        # Index all raw devices first (structural part)
        # This part could be optimized to run less frequently than telemetry updates.
        temp_devices_by_node_id: dict[str, dict[str, Any]] = {}
        for device_data_from_api in raw_devices:
            properties = device_data_from_api.get("properties", {})
            node_id = properties.get("nodeId")
            if not node_id:
                continue
            # Initialize with new structural data.
            temp_devices_by_node_id[node_id] = {
                **device_data_from_api,
                "_latest_data": {},
                "_processed_children": [],
            }

        # Second pass: build hierarchy and fetch telemetry selectively
        for node_id, current_device_struct in temp_devices_by_node_id.items():
            properties = current_device_struct.get("properties", {})
            node_type = properties.get("nodeType")

            if node_type not in SUPPORTED_NODE_TYPES:
                continue

            # Populate children for this device (structural)
            child_node_ids = properties.get("nodeChildrenIds", [])
            current_device_struct[
                "_processed_children"
            ] = []  # Reset children based on new structure
            for child_node_id in child_node_ids:
                if child_node_id in temp_devices_by_node_id:
                    current_device_struct["_processed_children"].append(
                        temp_devices_by_node_id[child_node_id]
                    )

            # Determine if this device should be a primary device (structural)
            is_primary = (node_type == DEVICE_TYPE_HYBRID_INVERTER) or not any(
                temp_devices_by_node_id.get(parent_id, {})
                .get("properties", {})
                .get("nodeType")
                == DEVICE_TYPE_HYBRID_INVERTER
                for parent_id in properties.get("nodeParentsIds", [])
            )

            if is_primary:
                try:
                    # Fetch and merge into current_device_struct["_latest_data"].
                    await self.get_device_latest_data(current_device_struct)

                    # Fetch/update telemetry for children of this primary device
                    for child_device_struct in current_device_struct.get(
                        "_processed_children", []
                    ):
                        child_node_id = child_device_struct.get("properties", {}).get(
                            "nodeId"
                        )
                        if not child_node_id:
                            continue
                        # Merge into child_device_struct["_latest_data"].
                        await self.get_device_latest_data(child_device_struct)

                    # Mark parent as updated (for HA coordinator to see a change)
                    # The deepcopy below will make HA see it as a new object.
                    current_device_struct["_last_telemetry_update_attempt"] = (
                        current_time.isoformat()
                    )

                    processed_devices_output[node_id] = copy.deepcopy(
                        current_device_struct
                    )

                except Exception:
                    _LOGGER.exception(
                        "Error processing device %s for telemetry", node_id
                    )

        _LOGGER.info(
            "Processed %s primary devices for Home Assistant.",
            len(processed_devices_output),
        )
        return processed_devices_output

    async def close(self) -> None:
        """Close the client session."""
        if self._session_owner and not self._session.closed:
            await self._session.close()
