import json
import logging
from homeassistant.components.device_tracker.config_entry import TrackerEntity as DeviceTrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BATTERY_LEVELS
from .utils import get_sub_location, get_battery_level

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up SmartThings Find device tracker entities."""
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []
    for device in devices:
        if 'subType' in device['data'] and device['data']['subType'] == 'CANAL2':
            entities += [SmartThingsDeviceTracker(hass, coordinator, device, "left")]
            entities += [SmartThingsDeviceTracker(hass, coordinator, device, "right")]
        entities += [SmartThingsDeviceTracker(hass, coordinator, device)]
    async_add_entities(entities)

class SmartThingsDeviceTracker(DeviceTrackerEntity):
    """Representation of a SmartTag device tracker."""

    def __init__(self, hass: HomeAssistant, coordinator, device, subDeviceName=None):
        """Initialize the device tracker."""

        self.coordinator = coordinator
        self.hass = hass
        self.device = device['data']
        self.device_id = device['data']['dvceID']
        self.subDeviceName = subDeviceName

        self._attr_unique_id = f"stf_device_tracker_{device['data']['dvceID']}{'_' + subDeviceName if subDeviceName else ''}"
        self._attr_name = device['data']['modelName'] + (' ' + subDeviceName.capitalize() if subDeviceName else '')
        self._attr_device_info = device['ha_dev_info']
        self._attr_latitude = None
        self._attr_longitude = None

        if 'icons' in device['data'] and 'coloredIcon' in device['data']['icons']:
            self._attr_entity_picture = device['data']['icons']['coloredIcon']
        self.async_update = coordinator.async_add_listener(self.async_write_ha_state)
    
    def async_write_ha_state(self):
        if not self.enabled:
            _LOGGER.debug(f"Ignoring state write request for disabled entity '{self.entity_id}'")
            return
        return super().async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return true if the device is available."""
        tag_data = self.coordinator.data.get(self.device_id, {})
        if not tag_data:
            _LOGGER.info(f"tag_data none for '{self.name}'; rendering state unavailable")
            return False
        if not tag_data['update_success']:
            _LOGGER.info(f"Last update for '{self.name}' failed; rendering state unavailable")
            return False
        return True
    
    @property
    def source_type(self) -> str:
        return SourceType.GPS
    
    @property
    def latitude(self):
        """Return the latitude of the device."""
        data = self.coordinator.data.get(self.device_id, {})
        if not self.subDeviceName:
            if data['location_found']: return data.get('used_loc', {}).get('latitude', None)
            return None
        else:
            _, loc = get_sub_location(data['ops'], self.subDeviceName)
            return loc.get('latitude', None)

    @property
    def longitude(self):
        """Return the longitude of the device."""
        data = self.coordinator.data.get(self.device_id, {})
        if not self.subDeviceName:
            if data['location_found']: return data.get('used_loc', {}).get('longitude', None)
            return None
        else:
            _, loc = get_sub_location(data['ops'], self.subDeviceName)
            return loc.get('longitude', None)
    
    @property
    def location_accuracy(self):
        """Return the location accuracy of the device."""
        data = self.coordinator.data.get(self.device_id, {})
        if not self.subDeviceName:
            if data['location_found']: return data.get('used_loc', {}).get('gps_accuracy', None)
            return None
        else:
            _, loc = get_sub_location(data['ops'], self.subDeviceName)
            return loc.get('gps_accuracy', None)

    @property
    def battery_level(self):
        """Return the battery level of the device."""
        data = self.coordinator.data.get(self.device_id, {})
        if self.subDeviceName:
            return None
        return get_battery_level(self.name, data['ops'])
    
    @property
    def extra_state_attributes(self):
        tag_data = self.coordinator.data.get(self.device_id, {})
        device_data = self.device
        if self.subDeviceName:
            used_op, used_loc = get_sub_location(tag_data['ops'], self.subDeviceName)
            tag_data = tag_data | used_op | used_loc
        used_loc = tag_data.get('used_loc', {})
        if used_loc:
            tag_data['last_seen'] = used_loc.get('gps_date', None)
        else:
            tag_data['last_seen'] = None
        return tag_data | device_data
