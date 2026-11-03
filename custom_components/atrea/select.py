"""Select entity pro Atrea RD5 — větrací zóna.

Web UI (userCtrl.xml): čtení H10717 („Zóna - aktuální požadavek"), zápis do
H10711 a při Týdenním programu (H10703=1) navíc H10703=2 (Dočasné).
Hodnoty: 0 = Zóna 1, 1 = Zóna 2, 2 = Zóna 1+2; názvy z textů Z1Text..Z3Text.
Entita se vytvoří jen pokud jsou zóny zapnuté (H10511 = 1).
"""

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import rd5
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    status = data.get("status") or {}
    entities = []
    if rd5.ival(status, "H10511") == 1 and "H10717" in status:
        entities.append(AtreaZoneSelect(data, entry))
    async_add_entities(entities)


class AtreaZoneSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_name = "Zóna"
    _attr_translation_key = "zone"
    _attr_icon = "mdi:home-floor-1"

    def __init__(self, data, entry):
        super().__init__(data["coordinator"])
        self._data = data
        self._ip = entry.data.get("ip_address")
        self._attr_unique_id = slugify(f"atrea_{self._ip}_zone")

    def _labels(self):
        user = self._data.get("userLabels") or {}
        return {
            raw: (user.get(rd5.ZONE_TEXT_KEYS[raw]) or label)
            for raw, label in rd5.ZONE_LABELS.items()
        }

    @property
    def options(self):
        return list(self._labels().values())

    @property
    def current_option(self):
        raw = rd5.ival(self._data.get("status"), "H10717")
        return self._labels().get(raw)

    async def async_select_option(self, option):
        for raw, label in self._labels().items():
            if label == option:
                climate = self._data["climate"]
                await climate._send(rd5.build_write(self._data.get("status"), "zone", raw))
                return

    @property
    def available(self):
        status = self._data.get("status")
        return bool(status) and "H10717" in status

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}
