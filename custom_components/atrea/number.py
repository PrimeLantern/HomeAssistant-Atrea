"""Number entity pro Atrea RD5 — uživatelská „Nastavení regulace" z web UI.

userCtrl.xml (menu UNcorr), všechny s přístupem uživatele (access="00"):
- H11318 Hystereze topení   0,1–3,0 °C  (raw = °C × 10)
- H11208 Hystereze chlazení 0,1–3,0 °C  (raw = °C × 10)
- H11209 Posun chlazení     0,0–5,0 °C  (raw = °C × 10)
"""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import rd5
from .const import DOMAIN

NUMBERS = [
    {
        "register": "H11318",
        "key": "hyst_heat",
        "name": "Hystereze topení",
        "min": 0.1,
        "max": 3.0,
        "icon": "mdi:thermometer-chevron-up",
    },
    {
        "register": "H11208",
        "key": "hyst_cool",
        "name": "Hystereze chlazení",
        "min": 0.1,
        "max": 3.0,
        "icon": "mdi:thermometer-chevron-down",
    },
    {
        "register": "H11209",
        "key": "offset_cool",
        "name": "Posun chlazení",
        "min": 0.0,
        "max": 5.0,
        "icon": "mdi:snowflake-thermometer",
    },
]


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    status = data.get("status") or {}
    async_add_entities(
        [AtreaNumber(data, entry, spec) for spec in NUMBERS if spec["register"] in status]
    )


class AtreaNumber(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, data, entry, spec):
        super().__init__(data["coordinator"])
        self._data = data
        self._register = spec["register"]
        self._ip = entry.data.get("ip_address")
        self._attr_name = spec["name"]
        self._attr_translation_key = spec["key"]
        self._attr_icon = spec["icon"]
        self._attr_native_min_value = spec["min"]
        self._attr_native_max_value = spec["max"]
        self._attr_unique_id = slugify(f"atrea_{self._ip}_{spec['key']}")

    @property
    def native_value(self):
        raw = rd5.ival(self._data.get("status"), self._register)
        return None if raw is None else raw / 10

    async def async_set_native_value(self, value):
        raw = int(round(value * 10))
        await self._data["climate"]._send({self._register: raw})

    @property
    def available(self):
        status = self._data.get("status")
        return bool(status) and self._register in status

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}
