import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from .const import DOMAIN
from .utils import get_battery_level

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up SmartThings Find sensor entities."""
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []
    for device in devices:
        entities += [DeviceBatterySensor(hass, coordinator, device)]
    async_add_entities(entities)


class DeviceBatterySensor(SensorEntity):
    """Representation of a Device battery sensor."""

    def __init__(self, hass: HomeAssistant, coordinator, device):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self._attr_unique_id = f"stf_device_battery_{device['data']['dvceID']}"
        self._attr_name = f"{device['data']['modelName']} Battery"
        self._state = None
        self.hass = hass
        self.device = device['data']
        self.device_id = device['data']['dvceID']
        self._attr_device_info = device['ha_dev_info']
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        """
        Makes the entity show unavailable state if no data was received
        or there was an error during last update
        """
        tag_data = self.coordinator.data.get(self.device_id, {})
        if not tag_data:
            _LOGGER.info(f"battery sensor: tag_data none for '{self.name}'; rendering state unavailable")
            return False
        if not tag_data['update_success']:
            _LOGGER.info(f"Last update for battery sensor '{self.name}' failed; rendering state unavailable")
            return False
        return True
    
    @property
    def unit_of_measurement(self) -> str:
        return '%'
    
    @property
    def state(self):
        ops = self.coordinator.data.get(self.device_id, {}).get('ops', [])
        return get_battery_level(self.name, ops)