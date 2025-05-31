"""Config flow for Eniris HACS integration."""

import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from aiohttp import ClientSession

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnirisHacsApiClient, EnirisHacsAuthError, EnirisHacsApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class EnirisHacsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eniris HACS."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    async def _test_credentials(self, user_input: Dict[str, Any]) -> Optional[str]:
        """Test credentials against the API."""
        session = async_get_clientsession(self.hass)
        client = EnirisHacsApiClient(
            email=user_input[CONF_EMAIL],
            password=user_input[CONF_PASSWORD],
            session=session,
        )
        try:
            # Attempt to get a refresh token as a validation step
            await client.get_refresh_token()
            return None  # Indicates success
        except EnirisHacsAuthError:
            return "invalid_auth"
        except EnirisHacsApiError:
            return "cannot_connect" # More generic API error
        except Exception as e: # Catch any other unexpected errors
            _LOGGER.error("Unexpected error during credential test: %s", e, exc_info=True)
            return "unknown"


    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            # Prevent duplicate entries for the same email
            await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
            self._abort_if_unique_id_configured()

            error_code = await self._test_credentials(user_input)
            if error_code is None:
                _LOGGER.info(
                    "Credentials validated for %s. Creating config entry.",
                    user_input[CONF_EMAIL],
                )
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL], data=user_input
                )
            else:
                errors["base"] = error_code
        
        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    # If you want to support re-authentication, you can implement async_step_reauth
    # async def async_step_reauth(self, user_input: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    #     """Handle re-authentication."""
    #     # Similar logic to async_step_user but for re-auth
    #     pass
