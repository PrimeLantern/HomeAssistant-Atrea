"""Button entity pro Atrea — reset filtru (C10007) + reset alarmů (C10005).

Z RD5 specs:
- C10007 (tab.11): zapsání 1 nastaví další interval výměny filtru podle H10910
  (defaultně 90 dní). User stiskne po fyzické výměně filtru → RD5 restartuje
  clock a D11183 přepne zpět na 0.
- C10005: zapsání 1 provede reset alarmů (např. nevyrovnaný průtok D11114).
  Tohle reálně používá web UI (`xml.cgi?...&C1000500001`) a je to uvedeno
  i v RD5 Modbus tabulce (tab.10, vydání 11/2021). User stiskne po odstranění
  příčiny, aby se jednotka znovu spustila.
"""

from homeassistant.components.button import ButtonEntity
from homeassistant.util import slugify

from .const import DOMAIN, LOGGER


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            AtreaFilterResetButton(data, entry),
            AtreaAlarmResetButton(data, entry),
        ]
    )


class AtreaFilterResetButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Reset intervalu výměny filtru"
    _attr_translation_key = "filter_reset"
    _attr_icon = "mdi:air-filter"

    def __init__(self, data, entry):
        self._data = data
        self._atrea = data["atrea"]
        self._coordinator = data["coordinator"]
        self._entry_id = entry.entry_id
        self._ip = entry.data.get("ip_address")
        self._attr_unique_id = slugify(f"atrea_{self._ip}_filter_reset")

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}

    async def async_press(self):
        """Zapíše 1 do C10007 — Atrea restartuje filter clock.

        Pozor: NEpoužívat pyatrea `setCommand` — ten má guard `if id in status`,
        a write-only akční coily (C10007) nejsou ve status → tiše se zahodí.
        Píšeme přímo do `commands` dictu (= obchází guard, exec to pošle)."""
        LOGGER.info("Atrea: pressing reset filter interval (C10007=1)")
        self._atrea.commands.clear()
        self._atrea.commands["C10007"] = f"{1:05}"
        await self.hass.async_add_executor_job(self._atrea.exec)
        await self._coordinator.async_refresh()


class AtreaAlarmResetButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Reset alarmů"
    _attr_translation_key = "alarm_reset"
    _attr_icon = "mdi:alarm-light"

    def __init__(self, data, entry):
        self._data = data
        self._atrea = data["atrea"]
        self._coordinator = data["coordinator"]
        self._entry_id = entry.entry_id
        self._ip = entry.data.get("ip_address")
        self._attr_unique_id = slugify(f"atrea_{self._ip}_alarm_reset")

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}

    async def async_press(self):
        """Zapíše 1 do C10005 — reset alarmů (např. D11114). C10005 = registr
        z odposlechu web UI; PDF dokumentuje C10006, ale ten D11114 neshodí.

        Pozor: NEpoužívat pyatrea `setCommand` — guard `if id in status` zahodí
        write-only coil C10005 (není ve status). Píšeme přímo do `commands`."""
        LOGGER.info("Atrea: pressing reset alarms (C10005=1)")
        self._atrea.commands.clear()
        self._atrea.commands["C10005"] = f"{1:05}"
        await self.hass.async_add_executor_job(self._atrea.exec)
        await self._coordinator.async_refresh()
