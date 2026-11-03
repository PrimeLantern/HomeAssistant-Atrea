"""Senzory pro Atrea — analogové výstupy 0–10 V.

Z RD5 specs (tab.7 stavové registry, holding registry 2.x Výstupy):
- H10207 = Výstup DA1, analogový 0–10 V (U = DATA/1000). Nese řídicí napětí
  modulace tepelného čerpadla (jednotka řídí TČ signálem 0–10 V — viz H12100
  „TČ-ovládání 0-10V"). 10,0 V = TČ jede na max, méně = má rezervu.

Směr (topí/chladí) NEvystavujeme zvlášť — climate entity ho už ukazuje přes
hvac_action (mapuje C10215 SE → HEATING, C10216 SC → COOLING).

Průtoky vzduchu (tab. 4.1 input registry, hodnota = m³/h přímo, 1:1):
- I11600/I11602 = požadovaný/aktuální průtok SUP (přívod)
- I11601/I11603 = požadovaný/aktuální průtok ETA (odtah)
- I11604/I11605 = požadovaný/aktuální průtok ODA (venkovní) — pouze pro R_5

Teploty (tab.7 input registry, hodnota = °C × 10, tj. /10):
- I10211 = T-ODA, venkovní vzduch. Vodičové čidlo: pro záporné teploty
  hodnota „přeteče" > 1300 → vzorec (50 - (raw - 65036) / 10) * -1.
"""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import rd5
from .const import DOMAIN, LOGGER


VOLTAGE_SENSORS = [
    {
        "register": "H10207",
        "key": "hp_voltage",
        "name": "Napětí TČ",
        "translation_key": "hp_voltage",
    },
    {
        "register": "H10200",
        "key": "m1_voltage",
        "name": "Napětí ventilátoru M1",
        "translation_key": "m1_voltage",
    },
    {
        "register": "H10201",
        "key": "m2_voltage",
        "name": "Napětí ventilátoru M2",
        "translation_key": "m2_voltage",
    },
]

FLOW_SENSORS = [
    {
        "register": "I11600",
        "key": "flow_sup_req",
        "name": "Požadovaný průtok přívod (SUP)",
        "translation_key": "flow_sup_req",
    },
    {
        "register": "I11602",
        "key": "flow_sup_act",
        "name": "Aktuální průtok přívod (SUP)",
        "translation_key": "flow_sup_act",
    },
    {
        "register": "I11601",
        "key": "flow_eta_req",
        "name": "Požadovaný průtok odtah (ETA)",
        "translation_key": "flow_eta_req",
    },
    {
        "register": "I11603",
        "key": "flow_eta_act",
        "name": "Aktuální průtok odtah (ETA)",
        "translation_key": "flow_eta_act",
    },
    {
        "register": "I11604",
        "key": "flow_oda_req",
        "name": "Požadovaný průtok venkovní (ODA)",
        "translation_key": "flow_oda_req",
    },
    {
        "register": "I11605",
        "key": "flow_oda_act",
        "name": "Aktuální průtok venkovní (ODA)",
        "translation_key": "flow_oda_act",
    },
]


TEMP_SENSORS = [
    {
        "register": "I10211",
        "key": "outdoor_temp",
        "name": "Venkovní teplota",
        "translation_key": "outdoor_temp",
    },
    {
        "register": "I10212",
        "key": "supply_temp",
        "name": "Teplota přívod (SUP)",
        "translation_key": "supply_temp",
    },
    {
        "register": "I10215",
        "key": "inside_temp",
        "name": "Vnitřní teplota (IDA)",
        "translation_key": "inside_temp",
    },
    {
        "register": "I10213",
        "key": "extract_temp",
        "name": "Teplota odtah (ETA)",
        "translation_key": "extract_temp",
    },
    {
        "register": "I10214",
        "key": "exhaust_temp",
        "name": "Teplota odpadní (EHA)",
        "translation_key": "exhaust_temp",
    },
    {
        "register": "I11420",
        "key": "outdoor_temp_avg",
        "name": "Venkovní teplota průměrná",
        "translation_key": "outdoor_temp_avg",
    },
]

# Výčtové stavy: (registr, klíč, název, mapa hodnot, ikona)
ENUM_SENSORS = [
    {
        "register": "H10705",
        "key": "current_phase",
        "name": "Aktuální fáze režimu",
        "translation_key": "current_phase",
        "labels": rd5.MODE_LABELS,
        "icon": "mdi:fan-clock",
    },
    {
        "register": "I11401",
        "key": "season",
        "name": "Sezóna",
        "translation_key": "season",
        "labels": rd5.SEASON_LABELS,
        "icon": "mdi:sun-snowflake-variant",
    },
    {
        "register": "H11401",
        "key": "season_switch",
        "name": "Přepínání TS/NTS",
        "translation_key": "season_switch",
        "labels": rd5.SEASON_SWITCH_LABELS,
        "icon": "mdi:swap-horizontal",
    },
    {
        "register": "H11431",
        "key": "toda_avg_interval",
        "name": "Interval průměrování T-ODA",
        "translation_key": "toda_avg_interval",
        "labels": rd5.TODA_AVG_LABELS,
        "icon": "mdi:timer-outline",
    },
]

# Provozní hodiny: 32bit low/high
HOURS_SENSORS = [
    {
        "low": "H13500",
        "high": "H13501",
        "key": "m1_hours",
        "name": "Provozní hodiny M1",
        "translation_key": "m1_hours",
    },
    {
        "low": "H13502",
        "high": "H13503",
        "key": "m2_hours",
        "name": "Provozní hodiny M2",
        "translation_key": "m2_hours",
    },
]


# Vnucený stav (H10712) — pyatrea getForcedMode() mapuje jen podmnožinu (viz
# loadSupportedForcedModes). Mapa AtreaMode.name → CZ; neznámé → "Stav {raw}".
FORCED_MODE_CZ = {
    "OFF": "Nic",
    "AUTOMATIC": "Automatický",
    "VENTILATION": "Větrání",
    "CIRCULATION_AND_VENTILATION": "Cirkulace s větráním",
    "CIRCULATION": "Cirkulace",
    "NIGHT_PRECOOLING": "Noční předchlazení",
    "DISBALANCE": "Rozvážení",
    "OVERPRESSURE": "Přetlakové větrání",
    "PERIODIC_VENTILATION": "Periodické větrání",
    "STARTUP": "Náběh",
    "RUNDOWN": "Doběh",
    "DEFROSTING": "Odmrazování",
    "HP_DEFROSTING": "Odmrazování TČ",
    "EXTERNAL": "Externí",
    "IN1": "Vstup 1",
    "IN2": "Vstup 2",
    "D1": "D1",
    "D2": "D2",
    "D3": "D3",
    "D4": "D4",
}


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    entities = [AtreaVoltageSensor(data, entry, spec) for spec in VOLTAGE_SENSORS]
    entities += [AtreaFlowSensor(data, entry, spec) for spec in FLOW_SENSORS]
    entities += [AtreaTemperatureSensor(data, entry, spec) for spec in TEMP_SENSORS]
    status = data.get("status") or {}
    entities += [
        AtreaEnumSensor(data, entry, spec)
        for spec in ENUM_SENSORS
        if spec["register"] in status
    ]
    entities += [
        AtreaHoursSensor(data, entry, spec)
        for spec in HOURS_SENSORS
        if spec["low"] in status
    ]
    if "H11402" in status:
        entities.append(
            AtreaTemperatureSensor(
                data,
                entry,
                {
                    "register": "H11402",
                    "key": "season_temp",
                    "name": "Teplota přepnutí TS/NTS",
                    "translation_key": "season_temp",
                    "unsigned": True,
                },
            )
        )
    entities.append(AtreaForcedModeSensor(data, entry))
    async_add_entities(entities)


class AtreaVoltageSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
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
    def native_value(self):
        status = self._data.get("status")
        if not status or self._register not in status:
            return None
        try:
            # Analogový výstup: U = DATA/1000 (0–10000 → 0,0–10,0 V)
            return int(status[self._register]) / 1000
        except (TypeError, ValueError):
            LOGGER.debug(
                "Atrea sensor %s: unexpected value %r",
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


class AtreaFlowSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = (
        UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
    )
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
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
    def native_value(self):
        status = self._data.get("status")
        if not status or self._register not in status:
            return None
        try:
            # Průtok: hodnota = m³/h přímo (0..65535 ~ 0..65535 m³/h)
            return int(status[self._register])
        except (TypeError, ValueError):
            LOGGER.debug(
                "Atrea sensor %s: unexpected value %r",
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


class AtreaTemperatureSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
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
        self._unsigned = spec.get("unsigned", False)

    @property
    def native_value(self):
        status = self._data.get("status")
        if not status or self._register not in status:
            return None
        try:
            raw = int(status[self._register])
        except (TypeError, ValueError):
            LOGGER.debug(
                "Atrea sensor %s: unexpected value %r",
                self._register,
                status.get(self._register),
            )
            return None
        # Teplota = °C × 10 jako int16; 1260 (126,0 °C) = nepřipojené čidlo.
        if self._unsigned:
            return raw / 10
        return rd5.decode_temp(raw)

    @property
    def available(self):
        status = self._data.get("status")
        if not status or self._register not in status:
            return False
        if self._unsigned:
            return True
        return rd5.decode_temp(status[self._register]) is not None

    @property
    def device_info(self):
        # Share device with the climate entity so all Atrea entities cluster
        # under one device card in HA UI.
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}


class AtreaEnumSensor(CoordinatorEntity, SensorEntity):
    """Číselný registr zobrazený jako text (režim, sezóna, …)."""

    _attr_has_entity_name = True

    def __init__(self, data, entry, spec):
        super().__init__(data["coordinator"])
        self._data = data
        self._register = spec["register"]
        self._labels = spec["labels"]
        self._ip = entry.data.get("ip_address")
        self._attr_name = spec["name"]
        self._attr_translation_key = spec["translation_key"]
        self._attr_icon = spec.get("icon")
        self._attr_unique_id = slugify(f"atrea_{self._ip}_{spec['key']}")

    @property
    def native_value(self):
        raw = rd5.ival(self._data.get("status"), self._register)
        if raw is None:
            return None
        return self._labels.get(raw, f"Stav {raw}")

    @property
    def extra_state_attributes(self):
        return {"raw": rd5.ival(self._data.get("status"), self._register)}

    @property
    def available(self):
        status = self._data.get("status")
        return bool(status) and self._register in status

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}


class AtreaHoursSensor(CoordinatorEntity, SensorEntity):
    """Provozní hodiny ventilátoru (32bit low + high·65536)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:timer-cog-outline"

    def __init__(self, data, entry, spec):
        super().__init__(data["coordinator"])
        self._data = data
        self._low = spec["low"]
        self._high = spec["high"]
        self._ip = entry.data.get("ip_address")
        self._attr_name = spec["name"]
        self._attr_translation_key = spec["translation_key"]
        self._attr_unique_id = slugify(f"atrea_{self._ip}_{spec['key']}")

    @property
    def native_value(self):
        return rd5.decode_u32(self._data.get("status"), self._low, self._high)

    @property
    def available(self):
        status = self._data.get("status")
        return bool(status) and self._low in status

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}


class AtreaForcedModeSensor(CoordinatorEntity, SensorEntity):
    """Vnucený stav jednotky (H10712) — náběh, doběh, noční předchlazení, …

    Mapuje přes pyatrea forcedModes (getSupportedForcedModes → AtreaMode);
    neznámé hodnoty zobrazí jako "Stav {raw}" pro doidentifikování.
    """

    _attr_has_entity_name = True
    _attr_name = "Vnucený stav"
    _attr_icon = "mdi:autorenew"

    def __init__(self, data, entry):
        super().__init__(data["coordinator"])
        self._data = data
        self._atrea = data["atrea"]
        self._ip = entry.data.get("ip_address")
        self._attr_unique_id = slugify(f"atrea_{self._ip}_forced_mode")

    @property
    def native_value(self):
        status = self._data.get("status")
        if not status or "H10712" not in status:
            return None
        try:
            raw = int(status["H10712"])
        except (TypeError, ValueError):
            return None
        if raw in rd5.FORCED_MODE_LABELS:
            labels = self._data.get("userLabels") or {}
            text_key = rd5.FORCED_MODE_TEXT_KEYS.get(raw)
            if text_key and labels.get(text_key):
                return f"{rd5.FORCED_MODE_LABELS[raw]} ({labels[text_key]})"
            return rd5.FORCED_MODE_LABELS[raw]
        modes = getattr(self._atrea, "forcedModes", None)
        if modes:
            mode = modes.get(raw)
            if mode is not None:
                return FORCED_MODE_CZ.get(mode.name, mode.name)
        # neznámý stav → syrová hodnota pro identifikaci
        return f"Stav {raw}"

    @property
    def available(self):
        status = self._data.get("status")
        return bool(status) and "H10712" in status

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, slugify(f"atrea_{self._ip}"))}}
