"""The Eniris HACS integration."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EnirisHacsApiClient, EnirisHacsApiError, EnirisHacsAuthError
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

# Define the platforms that this integration will support
PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eniris HACS from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    session = async_get_clientsession(hass)
    api_client = EnirisHacsApiClient(email, password, session)

    async def async_update_data():
        """Fetch data from API endpoint.

        This is the place to pre-process data to limit Home Assistant
        processing calls.
        """
        try:
            # Note: get_processed_devices already handles token refresh internally
            devices = await api_client.get_processed_devices()
            _LOGGER.debug("Coordinator update: %s devices processed", len(devices))
            return devices
        except EnirisHacsAuthError as err:
            # This will trigger re-authentication flow if implemented,
            # or just log an error and fail the update.
            _LOGGER.error("Authentication error during data update: %s", err)
            # Re-raising UpdateFailed is important for the coordinator
            raise UpdateFailed(f"Authentication error: {err}") from err
        except EnirisHacsApiError as err:
            _LOGGER.error("API error during data update: %s", err)
            raise UpdateFailed(f"API error: {err}") from err
        except Exception as err:
            _LOGGER.error("Unexpected error during data update: %s", err, exc_info=True)
            raise UpdateFailed(f"Unexpected error: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN} ({entry.title})",
        update_method=async_update_data,
        update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
    )

    # Fetch initial data so we have data when entities are set up.
    # If an error occurs, setup will fail.
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "api_client": api_client, # Store if needed by platforms directly, though coordinator is preferred
        "coordinator": coordinator,
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This is called when an integration instance is removed from Home Assistant.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        api_client = hass.data[DOMAIN][entry.entry_id].get("api_client")
        if api_client:
            await api_client.close() # Close the aiohttp session
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.info("Eniris HACS integration unloaded for %s", entry.title)

    return unload_ok
