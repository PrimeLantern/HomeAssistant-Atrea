import time
import re
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import slugify
from homeassistant.components.climate.const import HVACAction

from .utils import processFanModes

try:
    from homeassistant.components.climate import ClimateEntity, PLATFORM_SCHEMA
except ImportError:
    from homeassistant.components.climate import (
        ClimateDevice as ClimateEntity,
        PLATFORM_SCHEMA,
    )
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import (
    CONF_NAME,
    CONF_IP_ADDRESS,
    UnitOfTemperature,
    ATTR_TEMPERATURE,
)
from homeassistant.util import Throttle
from typing import Callable
from pyatrea import AtreaProgram, AtreaMode

from . import rd5
from .const import (
    DOMAIN,
    LOGGER,
    UPDATE_DELAY,
    MIN_TIME_BETWEEN_SCANS,
    SUPPORT_FLAGS,
    STATE_UNKNOWN,
    CONF_FAN_MODES,
    CONF_PRESETS,
    DEFAULT_FAN_MODE_LIST,
    ALL_PRESET_LIST,
    PRESET_LABELS_CZ,
    ICONS,
    HVAC_MODES,
)

# Režimy, které lze na RD5 zapisovat do H10709 (0..7).
WRITABLE_MODE_MAX = 7


def _to_cz(preset_en):
    """Map EN preset ID na CZ display label, fallback na EN pokud chybí."""
    return PRESET_LABELS_CZ.get(preset_en, preset_en)


def _from_cz(preset_cz):
    """Reverse map CZ label na EN preset ID, fallback na input pokud už je EN."""
    for en, cz in PRESET_LABELS_CZ.items():
        if cz == preset_cz:
            return en
    return preset_cz


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Callable
):
    sensor_name = entry.data.get(CONF_NAME)
    if sensor_name is None:
        sensor_name = "atrea"

    fan_list = entry.data.get(CONF_FAN_MODES)
    if fan_list is None:
        fan_list = DEFAULT_FAN_MODE_LIST

    preset_list = entry.data.get(CONF_PRESETS)
    if preset_list is None:
        preset_list = {}
        for preset in ALL_PRESET_LIST:
            preset_list[preset] = True

    hass.data[DOMAIN][entry.entry_id]["climate"] = AtreaDevice(
        hass, entry, sensor_name, fan_list, preset_list
    )

    async_add_entities([hass.data[DOMAIN][entry.entry_id]["climate"]])


class AtreaDevice(ClimateEntity):
    _attr_translation_key = "atrea"
    _attr_has_entity_name = False

    def __init__(
        self, hass, entry, sensor_name, fan_list, preset_list,
    ):
        super().__init__()
        self.data = hass.data[DOMAIN][entry.entry_id]
        self.atrea = self.data["atrea"]
        self._coordinator = self.data["coordinator"]
        self._userLabels = self.data["userLabels"]
        self.ip = entry.data.get(CONF_IP_ADDRESS)
        self.updatePending = False
        self._preset_list = []
        self._warnings = []
        self._name = sensor_name
        self._current_fan_mode = None
        self._alerts = []
        self._outside_temp = None
        self._inside_temp = None
        self._supply_air_temp = None
        self._exhaust_temp = None
        self._extract_temp = None
        self._requested_temp = None
        self._requested_power = None
        self._active_inputs = []
        self._forced_mode = None
        self._current_power = None
        self._current_phase = None
        self._zone = None
        self._fan_flow = {}

        self._current_preset = None
        self._current_hvac_mode = None
        self.air_handling_control = None
        self._enabled = False
        self._cooling = -1
        self._heating = -1
        self._last_on_mode = rd5.MODE_VENTILATION

        self._da1 = 0.0

        # R_5 detekce: H10510=4 nebo provoz 2Z (C10509=1). Bez fallbacku na
        # R_5 — Duplex jednotky musí zůstat na procentech.
        self._is_r5 = rd5.is_r5(self.data.get("status"))

        self.updatePresetList(preset_list, False)
        self.updateFanList(fan_list, False)
        self.manualUpdate(False)

    # ------------------------------------------------------------------
    def updatePresetList(self, preset_list, updateState=True):
        self._preset_list = []
        for required_preset in preset_list:
            if preset_list[required_preset]:
                for i, preset_supported in self.data["supportedModes"]:
                    if preset_supported and ALL_PRESET_LIST[i] == required_preset:
                        self._preset_list.append(ALL_PRESET_LIST[i])
        if updateState:
            self.async_schedule_update_ha_state(True)

    def updateFanList(self, fan_list, updateState=True):
        self._fan_list = processFanModes(fan_list)
        if updateState:
            self.async_schedule_update_ha_state(True)

    def updateName(self, name, updateState=True):
        self._name = name
        if updateState:
            self.async_schedule_update_ha_state(True)

    async def async_added_to_hass(self) -> None:
        self._enabled = True

    async def async_will_remove_from_hass(self) -> None:
        self._enabled = False

    def getUniqueID(self):
        return slugify(f"atrea_{self.ip}")

    @property
    def _status(self):
        return self.data.get("status") or {}

    def _required_mode(self):
        return rd5.required_mode(self._status)

    def _group(self, mode=None):
        if mode is None:
            mode = self._required_mode()
        return rd5.power_group(self._status, mode)

    # ------------------------------------------------------------------
    @property
    def brand(self):
        return "ATREA s.r.o."

    @property
    def model(self):
        if self._model:
            return self._model["category"] + " " + self._model["model"]
        return False

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.getUniqueID())},
            "name": self.name,
            "manufacturer": self.brand,
            "model": self.model,
            "sw_version": self._swVersion,
            "hw_version": self._id,
            "connections": {},
        }

    @property
    def should_poll(self):
        return True

    @property
    def icon(self):
        if len(self._alerts) > 0:
            return "mdi:fan-alert"
        elif self.fan_mode in ("0%", rd5.OFF_LABEL):
            return "mdi:fan-off"
        elif self._current_preset in ICONS:
            return ICONS[self._current_preset]
        else:
            return "mdi:fan"

    @property
    def supported_features(self):
        return SUPPORT_FLAGS

    @property
    def unique_id(self) -> str:
        return self.getUniqueID()

    @property
    def name(self):
        return "{}".format(self._name)

    @property
    def extra_state_attributes(self):
        attributes = {}

        attributes["outside_temp"] = self._outside_temp
        attributes["inside_temp"] = self._inside_temp
        attributes["supply_air_temp"] = self._supply_air_temp
        attributes["requested_temp"] = self._requested_temp
        attributes["exhaust_temp"] = self._exhaust_temp
        attributes["extract_temp"] = self._extract_temp
        attributes["requested_power"] = self._requested_power
        attributes["warnings"] = self._warnings
        attributes["alerts"] = self._alerts
        attributes["program"] = self.air_handling_control
        attributes["active_inputs"] = self._active_inputs
        attributes["forced_mode"] = (
            self._forced_mode.name if self._forced_mode is not None else None
        )
        attributes["current_power"] = self._current_power
        attributes["current_da1_output"] = self._da1

        if self._is_r5:
            # Aktuálně běžící fáze (v Automatu se střídá větrání/cirkulace)
            attributes["current_phase"] = rd5.MODE_LABELS.get(
                self._current_phase, self._current_phase
            )
            attributes["zone"] = self._zone
            attributes["fan_flow_circulation"] = self._fan_flow.get("circulation")
            attributes["fan_flow_ventilation"] = self._fan_flow.get("ventilation")

        if self._heating == 1:
            attributes["hvac_action"] = HVACAction.HEATING
        elif self._cooling == 1:
            attributes["hvac_action"] = HVACAction.COOLING
        elif self._current_hvac_mode == HVACMode.OFF:
            attributes["hvac_action"] = HVACAction.OFF
        return attributes

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def target_temperature(self):
        return self._requested_temp

    @property
    def target_temperature_step(self):
        return 0.5

    @property
    def hvac_modes(self):
        return HVAC_MODES

    @property
    def hvac_mode(self):
        return self._current_hvac_mode

    @property
    def hvac_action(self):
        if self._current_hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._heating == 1:
            return HVACAction.HEATING
        if self._cooling == 1:
            return HVACAction.COOLING
        if self._heating == -1 and self._cooling == -1:
            return None
        return HVACAction.FAN

    @property
    def preset_mode(self):
        if self._current_preset is None:
            return STATE_UNKNOWN
        if self._current_preset.name and self._current_preset.name in self._userLabels:
            return self._userLabels[self._current_preset.name]
        elif self._current_preset < len(ALL_PRESET_LIST):
            return _to_cz(ALL_PRESET_LIST[self._current_preset])
        else:
            return STATE_UNKNOWN

    @property
    def preset_modes(self):
        return [_to_cz(p) for p in self._preset_list]

    @property
    def current_temperature(self):
        return self._inside_temp

    @property
    def min_temp(self):
        return 10

    @property
    def max_temp(self):
        return 40

    @property
    def fan_mode(self):
        return self._current_fan_mode

    @property
    def fan_modes(self):
        if self._is_r5:
            return list(rd5.group_values(self._group()).keys())
        return self._fan_list

    @property
    def program(self):
        return self.air_handling_control

    # ------------------------------------------------------------------
    @Throttle(MIN_TIME_BETWEEN_SCANS)
    async def async_update(self):
        if not self.updatePending:
            self.updatePending = True
            await self._coordinator.async_refresh()
            await self.hass.async_add_executor_job(time.sleep, UPDATE_DELAY / 1000)
            self.manualUpdate()
            self.updatePending = False

    def manualUpdate(self, updateState=True):
        status = self.data["status"]
        self._id = self.atrea.getID()
        self._model = self.data["model"]
        self._swVersion = self.atrea.getVersion()
        self._warnings = []
        self._alerts = []
        self._active_inputs = []
        if status:
            if "I10211" in status:
                self._outside_temp = rd5.decode_temp(status["I10211"])
            elif "I00202" in status:
                self._outside_temp = self.atrea.getValue("I00202")

            if "I10215" in status:
                self._inside_temp = rd5.decode_temp(status["I10215"])
            if "I10212" in status:
                self._supply_air_temp = rd5.decode_temp(status["I10212"])
            elif "I00200" in status:
                self._supply_air_temp = self.atrea.getValue("I00200")
            if "I10214" in status:
                self._exhaust_temp = rd5.decode_temp(status["I10214"])
            if "I10213" in status:
                self._extract_temp = rd5.decode_temp(status["I10213"])

            if "H10706" in status:
                self._requested_temp = rd5.ival(status, "H10706") / 10
            elif "H01006" in status:
                self._requested_temp = self.atrea.getValue("H01006")

            if "H10714" in status:
                self._requested_power = rd5.ival(status, "H10714")
            elif "H01005" in status:
                self._requested_power = int(self.atrea.getValue("H01005"))

            if "H10704" in status:
                self._current_power = rd5.ival(status, "H10704")

            req_mode = self._required_mode()
            self._current_phase = rd5.current_phase(status)
            if req_mode is not None and req_mode <= WRITABLE_MODE_MAX:
                self._current_preset = AtreaMode(req_mode)
                if req_mode != rd5.MODE_OFF:
                    self._last_on_mode = req_mode
            else:
                self._current_preset = self.atrea.getMode()

            if self._is_r5:
                group = self._group(req_mode)
                power = rd5.required_power(status)
                self._current_fan_mode = rd5.label_for_value(group, power)
                self._fan_flow = rd5.flow_for_value(status, power, group)
                zone = rd5.ival(status, "H10717")
                if rd5.ival(status, "H10511") == 1 and zone is not None:
                    self._zone = self._userLabels.get(
                        rd5.ZONE_TEXT_KEYS.get(zone), rd5.ZONE_LABELS.get(zone)
                    ) or rd5.ZONE_LABELS.get(zone)
                else:
                    self._zone = None
            else:
                if "H01001" in status:
                    self._current_fan_mode = str(int(self.atrea.getValue("H01001"))) + "%"
                else:
                    self._current_fan_mode = str(self._requested_power) + "%"

            # Topení: SE (TČ topení) nebo SA2 (sekundární/vodní ohřev) > 0
            heating = rd5.ival(status, "C10215")
            sa2 = rd5.ival(status, "H10203")
            if heating is None and sa2 is None:
                self._heating = -1
            else:
                self._heating = 1 if (heating == 1 or (sa2 or 0) > 0) else 0
            cooling = rd5.ival(status, "C10216")
            self._cooling = -1 if cooling is None else cooling

            if "H10207" in status:
                self._da1 = rd5.ival(status, "H10207") / 1000

            for inpt in range(4):
                entry = f"D1020{inpt}"
                if rd5.ival(status, entry) == 1:
                    self._active_inputs.append(f"D{inpt + 1}")

            self._forced_mode = self.atrea.getForcedMode()

            program = rd5.program_state(status)
            if program == rd5.PROGRAM_MANUAL:
                self.air_handling_control = "Manual"
            elif program == rd5.PROGRAM_WEEKLY:
                self.air_handling_control = "Schedule"
            else:
                self.air_handling_control = "Temporary"

            if req_mode == rd5.MODE_OFF or self._current_fan_mode in ("0%", rd5.OFF_LABEL):
                self._current_hvac_mode = HVACMode.OFF
            elif program == rd5.PROGRAM_WEEKLY:
                self._current_hvac_mode = HVACMode.AUTO
            else:
                self._current_hvac_mode = HVACMode.FAN_ONLY

            params = self.data.get("params") or self.atrea.getParams()
            for warning in params["warning"]:
                if status.get(warning) == "1":
                    self._warnings.append(self.atrea.getTranslation(warning))
            for alert in params["alert"]:
                if status.get(alert) == "1":
                    self._alerts.append(self.atrea.getTranslation(alert))
        else:
            self._current_hvac_mode = None
        if updateState:
            self.async_schedule_update_ha_state(True)

    # ------------------------------------------------------------------
    @property
    def _is_rd5(self):
        """RD5 regulace (registry H107xx). Starší RD4 jednotky mají H01xxx."""
        return "H10709" in self._status

    async def _send_legacy(self, fn, *args):
        """Starší jednotky (RD4) — původní cesta přes pyatrea settery."""
        self.atrea.commands.clear()
        fn(*args)
        self.updatePending = True
        try:
            await self.hass.async_add_executor_job(self.atrea.exec)
            await self._coordinator.async_refresh()
        finally:
            self.updatePending = False
        self.manualUpdate()

    async def _send(self, cmds):
        """Odešle {registr: hodnota} v jedné relaci (jeden xml.cgi request)."""
        if not cmds:
            return
        LOGGER.debug("Atrea write: %s", cmds)
        self.atrea.commands.clear()
        for reg, val in cmds.items():
            val = int(val)
            if val < 0:
                val += 65536
            self.atrea.commands[reg] = f"{val:05}"
        self.updatePending = True
        try:
            await self.hass.async_add_executor_job(self.atrea.exec)
            await self._coordinator.async_refresh()
            await self.hass.async_add_executor_job(time.sleep, UPDATE_DELAY / 1000)
        finally:
            self.updatePending = False
            self.atrea.commands.clear()
        self.manualUpdate()

    def _mode_cmds(self, mode):
        """Zápis režimu + výkon převedený do sady nového režimu (jedna relace)."""
        status = self._status
        cmds = rd5.build_write(status, "mode", mode)
        if self._is_r5 and mode != rd5.MODE_OFF:
            old_group = self._group()
            new_group = self._group(mode)
            power = rd5.required_power(status)
            if not power:
                power = rd5.ival(status, "H10713") or rd5.ival(status, "H10708")
            target = rd5.map_power_to_group(power, new_group, old_group)
            if target is not None and target != rd5.ival(status, "H10708"):
                cmds.update(rd5.build_write(status, "power", target))
        return cmds

    async def async_set_fan_mode(self, fan_mode):
        status = self._status
        if self._is_r5:
            group = self._group()
            value = rd5.group_values(group).get(fan_mode)
            if value is None:
                LOGGER.warning(
                    "Nepodporovaný výkon '%s' pro režim %s", fan_mode, self._required_mode()
                )
                return
            if value == 0:
                # Web UI nedovolí zapsat výkon 0 (box „Off" má rw=0) → vypnout režimem.
                await self.async_turn_off()
                return
            await self._send(rd5.build_write(status, "power", value))
            return

        # Duplex — procenta
        fan_percent = int(re.sub("[^0-9]", "", fan_mode))
        fan_percent = max(12, min(100, fan_percent))
        if not self._is_rd5:
            await self._send_legacy(self.atrea.setPower, fan_percent)
            return
        await self._send(rd5.build_write(status, "power", fan_percent))

    async def async_turn_on(self):
        if not self._is_rd5:
            await self._send_legacy(self.atrea.setMode, AtreaMode.VENTILATION)
            return
        mode = self._last_on_mode or rd5.MODE_VENTILATION
        await self._send(self._mode_cmds(mode))

    async def async_turn_off(self):
        if not self._is_rd5:
            await self._send_legacy(self.atrea.setMode, AtreaMode.OFF)
            return
        await self._send(rd5.build_write(self._status, "mode", rd5.MODE_OFF))

    async def async_set_hvac_mode(self, hvac_mode):
        if not self._is_rd5 and hvac_mode != HVACMode.OFF:
            program = AtreaProgram.WEEKLY if hvac_mode == HVACMode.AUTO else AtreaProgram.MANUAL
            await self._send_legacy(self.atrea.setProgram, program)
            return
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        cmds = {}
        if hvac_mode == HVACMode.AUTO:
            if rd5.program_state(self._status) != rd5.PROGRAM_WEEKLY:
                cmds = rd5.build_program(rd5.PROGRAM_WEEKLY)
        elif hvac_mode == HVACMode.FAN_ONLY:
            if self._required_mode() in (None, rd5.MODE_OFF):
                cmds = self._mode_cmds(self._last_on_mode or rd5.MODE_VENTILATION)
            # přepnutí do Ručního přepíše případné příznaky Dočasné (2) na 0
            cmds.update(rd5.build_program(rd5.PROGRAM_MANUAL))
        await self._send(cmds)

    async def async_set_preset_mode(self, preset_mode):
        preset_en = _from_cz(preset_mode)
        try:
            mode = AtreaMode(ALL_PRESET_LIST.index(preset_en))
        except ValueError:
            LOGGER.warning("Chosen preset=%s is incorrect preset.", str(preset_mode))
            return
        if int(mode) > WRITABLE_MODE_MAX:
            LOGGER.warning("Předvolba %s je jen informativní (vnucený stav).", preset_mode)
            return

        self.data["switching"] = True
        self._coordinator.async_update_listeners()
        try:
            # Porovnání s POŽADOVANÝM režimem (H10715), ne s fází H10705 —
            # jinak by se v Automatu (fáze cirkulace) volba Cirkulace neodeslala.
            if not self._is_rd5:
                await self._send_legacy(self.atrea.setMode, mode)
            elif int(mode) == self._required_mode():
                LOGGER.debug("Předvolba %s už je nastavená", preset_mode)
            else:
                await self._send(self._mode_cmds(int(mode)))
        finally:
            self.data["switching"] = False
            self._coordinator.async_update_listeners()

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if 10 <= temperature <= 40:
            if not self._is_rd5:
                await self._send_legacy(self.atrea.setTemperature, temperature)
                return
            await self._send(
                rd5.build_write(self._status, "temp", int(round(temperature * 10)))
            )
        else:
            LOGGER.warning(
                "Chosen temperature=%s is incorrect. It needs to be between 10 and 40.",
                str(temperature),
            )
