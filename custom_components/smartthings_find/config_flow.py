from typing import Any, Dict
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry
)
from .const import (
    DOMAIN,
    CONF_JSESSIONID,
    CONF_UPDATE_INTERVAL,
    CONF_UPDATE_INTERVAL_DEFAULT,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_OTHERS_DEFAULT
)
from .utils import do_login_stage_one, do_login_stage_two, gen_qr_code_base64
import asyncio
import logging


_LOGGER = logging.getLogger(__name__)

class SmartThingsFindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartThings Find."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    reauth_entry: ConfigEntry | None = None

    task_stage_one: asyncio.Task | None = None
    task_stage_two: asyncio.Task | None = None

    qr_url = None
    session = None

    jsessionid = None


    error = None

    async def do_stage_one(self):
        _LOGGER.debug("Running login stage 1")
        try:
            stage_one_res = await do_login_stage_one(self.hass)
            if not stage_one_res is None:
                self.session, self.qr_url = stage_one_res
            else:
                self.error = "Login stage 1 failed. Check logs for details."
                _LOGGER.warn("Login stage 1 failed")
            _LOGGER.debug("Login stage 1 done")
        except Exception as e:
            self.error = "Login stage 1 failed. Check logs for details."
            _LOGGER.error(f"Exception in stage 1: {e}", exc_info=True)

    async def do_stage_two(self):
        _LOGGER.debug("Running login stage 2")
        try: 
            stage_two_res = await do_login_stage_two(self.session)
            if not stage_two_res is None:
                self.jsessionid = stage_two_res
                _LOGGER.info("Login successful")
            else:
                self.error = "Login stage 2 failed. Check logs for details."
                _LOGGER.warning("Login stage 2 failed")
            _LOGGER.debug("Login stage 2 done")
        except Exception as e:
            self.error = "Login stage 2 failed. Check logs for details."
            _LOGGER.error(f"Exception in stage 2: {e}", exc_info=True)

    # First step: Get QR Code login URL
    async def async_step_user(self, user_input=None):
        _LOGGER.debug("Entering login stage 1")
        if not self.task_stage_one:
            self.task_stage_one = self.hass.async_create_task(self.do_stage_one())
        if not self.task_stage_one.done():        
            return self.async_show_progress(
                progress_action="task_stage_one",
                progress_task=self.task_stage_one
            )
        # At this point stage 1 is completed
        if self.error:
            # An error occurred, cancel the flow by moving to the finish-
            # form which shows the error
            return self.async_show_progress_done(next_step_id="finish")
        # No error -> Proceed to stage 2
        return self.async_show_progress_done(next_step_id="auth_stage_two")

    # Second step: Wait until QR scanned an log in
    async def async_step_auth_stage_two(self, user_input=None):
        if not self.task_stage_two:
            self.task_stage_two = self.hass.async_create_task(self.do_stage_two())
        if not self.task_stage_two.done():
            return self.async_show_progress(
                progress_action="task_stage_two",
                progress_task=self.task_stage_two,
                description_placeholders={
                    "qr_code": gen_qr_code_base64(self.qr_url),
                    "url": self.qr_url,
                    "code": self.qr_url.split('/')[-1],
                }
            )
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(self, user_input=None):
        if self.error:
            return self.async_show_form(step_id="finish", errors={'base': self.error})
        
        data={CONF_JSESSIONID: self.jsessionid}
        
        if self.reauth_entry:
            # Finish step was called by reauth-flow. Do not create a new entry,
            # instead update the existing entry
            return self.async_update_reload_and_abort(
                self.reauth_entry,
                data=data
            )
        
        return self.async_create_entry(title="SmartThings Find", data=data)

    async def async_step_reauth(self, user_input=None):
        self.reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({}),
            )
        return await self.async_step_user()
    
    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_reauth_confirm(self)
    
    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return SmartThingsFindOptionsFlowHandler(config_entry)
    
    
class SmartThingsFindOptionsFlowHandler(OptionsFlowWithConfigEntry):
    """Handle an options flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options flow."""

        if user_input is not None:

            res = self.async_create_entry(title="", data=user_input)

            # Reload the integration entry to make sure the newly set options take effect
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return res

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=self.options.get(
                        CONF_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL_DEFAULT
                    ),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=30)),
                vol.Optional(
                    CONF_ACTIVE_MODE_SMARTTAGS,
                    default=self.options.get(
                        CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_SMARTTAGS_DEFAULT
                    ),
                ): bool,
                vol.Optional(
                    CONF_ACTIVE_MODE_OTHERS,
                    default=self.options.get(
                        CONF_ACTIVE_MODE_OTHERS, CONF_ACTIVE_MODE_OTHERS_DEFAULT
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=data_schema)