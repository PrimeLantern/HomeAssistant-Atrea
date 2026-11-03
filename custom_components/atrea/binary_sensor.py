"""Binary sensors pro Atrea — výměna filtru + alarmy (device_class=problem).

Z RD5 specs (tab.9 Alarmy a upozornění):
- D11122 = Zanesený filtr (TR mode — tlakové čidlo přes filtr).
- D11183 = Interval výměny filtru (Perioda mode — uplynul nastavený interval H10910).
- D11114 = Nevyrovnaný průtok (alarm) — jednotka se kvůli němu vypíná. Reset přes
  tlačítko C10005 (registr z odposlechu web UI; viz button.py).

Atrea filtr může běžet buď v TR módu (= D11122 reaguje na fyzické zanesení) nebo
Perioda módu (= D11183 reaguje na uplynulý interval, default 90 dní). Nastavení
je v registru H10512. Vystavujeme oba flagy — user uvidí ten relevantní podle
toho, který mód jeho jednotka má aktivní (a druhý zůstane vždy off).
"""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, LOGGER


# Stavové příznaky, které NEJSOU porucha.
# I11401 = aktuální sezóna (0 = topná, 1 = netopná) — dle RD5 Modbus tabulky
# i textů web UI („Aktuální sezóna"). Noční předchlazení je podle tabulky
# režimů povolené jen při I11401=1, proto si entita ponechává původní klíč
# (kvůli entity_id), ale význam je „netopná sezóna".
# C10215 / C10216 = výstupy SE / SC — tepelné čerpadlo topí / chladí.
STATUS_BINARY_SENSORS = [
    {
        "register": "I11401",
        "key": "night_precooling_available",
        "name": "Netopná sezóna (předchlazení povoleno)",
        "translation_key": "night_precooling_available",
        "icon": "mdi:weather-night",
    },
    {
        "register": "C10215",
        "key": "hp_heating",
        "name": "TČ topí (SE)",
        "translation_key": "hp_heating",
        "icon": "mdi:heat-pump",
    },
    {
        "register": "C10216",
        "key": "hp_cooling",
        "name": "TČ chladí (SC)",
        "translation_key": "hp_cooling",
        "icon": "mdi:snowflake",
    },
]


PROBLEM_BINARY_SENSORS = [
    {
        "register": "D11122",
        "key": "filter_dirty",
        "name": "Filtr zanesený",
        "translation_key": "filter_dirty",
    },
    {
        "register": "D11183",
        "key": "filter_period_expired",
        "name": "Filtr — interval výměny",
        "translation_key": "filter_period_expired",
    },
    {
        "register": "D11114",
        "key": "unbalanced_flow",
        "name": "Nevyrovnaný průtok",
        "translation_key": "unbalanced_flow",
    },
]


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    entities = [
        AtreaProblemBinarySensor(data, entry, spec) for spec in PROBLEM_BINARY_SENSORS
    ]
    entities += [
        AtreaStatusBinarySensor(data, entry, spec) for spec in STATUS_BINARY_SENSORS
    ]
    entities.append(AtreaSwitchingBinarySensor(data, entry))
    async_add_entities(entities)


class AtreaProblemBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_has_entity_name = True

    def __init__(self, data, entry, spec):
        super().__init__(data["coordinator"])
        self._data = data
        self._atrea = data["atrea"]
        self._register = spec["register"]
        self._entry_id = entry.entry_id
        self._ip = entry.data.get("ip_address")
        self._attr_name = spec["name"]
        self._attr_translation_key = spec["translation_key"]
        self._attr_unique_id = slugify(f"atrea_{self._ip}_{spec['key']}")

    @property
    def is_on(self):
        status = self._data.get("status")
        if not status or self._register not in status:
            return None
        try:
            return int(status[self._register]) == 1
        except (TypeError, ValueError):
            LOGGER.debug(
                "Atrea binary_sensor %s: unexpected value %r",
                self._register,
                status.get(self._register),
            )
            return None

    @property
    def available(self):
        status = self._data.get("status")
        return bool(status) and self._register in status

    @property
    def device_info(self):
        # Share device with the climate entity so all Atrea entities cluster
        # under one device card in HA UI.
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}


class AtreaStatusBinarySensor(AtreaProblemBinarySensor):
    """Stavový příznak z jednoho registru — bez device_class „problem".

    Dědí čtení i dostupnost z AtreaProblemBinarySensor, přebíjí jen sémantiku:
    tohle není porucha, takže se to nesmí objevit v „Problems" badge.
    """

    _attr_device_class = None

    def __init__(self, data, entry, spec):
        super().__init__(data, entry, spec)
        self._attr_icon = spec.get("icon")


class AtreaSwitchingBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """„Přepínám" — on po dobu preset operace (60s sync loop v climate).

    Čte sdílený flag data["switching"], který climate nastavuje kolem
    async_set_preset_mode a notifikuje přes coordinator.async_update_listeners().
    Slouží k UI indikaci „něco se děje" i když jednotka nehlásí náběh (H10712).
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_has_entity_name = True
    _attr_name = "Přepínám"
    _attr_icon = "mdi:sync"

    def __init__(self, data, entry):
        super().__init__(data["coordinator"])
        self._data = data
        self._ip = entry.data.get("ip_address")
        self._attr_unique_id = slugify(f"atrea_{self._ip}_switching")

    @property
    def is_on(self):
        return bool(self._data.get("switching"))

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}
