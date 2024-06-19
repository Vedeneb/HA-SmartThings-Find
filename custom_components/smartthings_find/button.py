import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .utils import fetch_csrf

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up SmartThings Find button entities."""
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    entities = []
    for device in devices:
        entities += [RingButton(hass, device)]
    async_add_entities(entities)


class RingButton(ButtonEntity):
    """Representation a button entity to make a SmartThings Find device ring."""

    def __init__(self, hass: HomeAssistant, device):
        """Initialize the button."""
        self._attr_unique_id = f"stf_ring_button_{device['data']['dvceID']}"
        self._attr_name = f"{device['data']['modelName']} Ring"

        if 'icons' in device['data'] and 'coloredIcon' in device['data']['icons']:
            self._attr_entity_picture = device['data']['icons']['coloredIcon']
        self._attr_icon = 'mdi:nfc-search-variant'
        # self.hass = hass
        self.device = device['data']
        self._attr_device_info = device['ha_dev_info']

    async def async_press(self):
        """Handle the button press."""
        entry_id = self.registry_entry.config_entry_id
        session = self.hass.data[DOMAIN][entry_id]["session"]
        csrf_token = self.hass.data[DOMAIN][entry_id]["_csrf"]
        ring_payload = {
            "dvceId": self.device['dvceID'],
            "operation": "RING",
            "usrId": self.device['usrId'],
            "status": "start",
            "lockMessage": "Home Assistant is ringing your device!"
        }
        url = f"https://smartthingsfind.samsung.com/dm/addOperation.do?_csrf={
            csrf_token}"

        try:
            async with session.post(url, json=ring_payload) as response:
                _LOGGER.debug("HTTP response status: %s", response.status)
                if response.status == 200:
                    _LOGGER.info(f"Successfully rang device {self.device['modelName']}")
                    _LOGGER.debug(f"Response: {await response.text()}")
                else:
                    # Fetch a new CSRF token to make sure we're still logged in
                    await fetch_csrf(self.hass, session, entry_id)
        except Exception as e:
            _LOGGER.error(f"Exception occurred while ringing '{self.device['modelName']}': %s", e)
