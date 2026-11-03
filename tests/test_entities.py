"""Offline test entit nad exportem stavu — bez Home Assistant (stuby) a bez sítě.

Ověřuje, co integrace zobrazí a jaké registry by poslala do xml.cgi.
"""
import asyncio
import enum
import importlib
import os
import sys
import types
import xml.etree.ElementTree as ET

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")


# ---------------------------------------------------------------- HA stuby
class _Meta(type):
    def __getattr__(cls, item):
        return f"{cls.__name__}.{item}".lower().split(".")[-1]


def _auto_class(name):
    return _Meta(name, (object,), {"__init__": lambda self, *a, **k: None})


class _AutoModule(types.ModuleType):
    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        if item[0].isupper():
            obj = _auto_class(item)
        else:
            obj = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
        setattr(self, item, obj)
        return obj


class HVACMode:
    OFF, AUTO, FAN_ONLY = "off", "auto", "fan_only"


class HVACAction:
    OFF, HEATING, COOLING, FAN = "off", "heating", "cooling", "fan"


def install_stubs():
    names = [
        "homeassistant", "homeassistant.core", "homeassistant.config_entries",
        "homeassistant.util", "homeassistant.const", "homeassistant.exceptions",
        "homeassistant.components", "homeassistant.components.climate",
        "homeassistant.components.climate.const", "homeassistant.components.sensor",
        "homeassistant.components.binary_sensor", "homeassistant.components.select",
        "homeassistant.components.number", "homeassistant.components.button",
        "homeassistant.components.update", "homeassistant.helpers",
        "homeassistant.helpers.update_coordinator", "homeassistant.helpers.device_registry",
        "homeassistant.data_entry_flow", "voluptuous", "requests",
    ]
    for n in names:
        sys.modules[n] = _AutoModule(n)
    cc = sys.modules["homeassistant.components.climate.const"]
    cc.HVACMode, cc.HVACAction = HVACMode, HVACAction
    hc = sys.modules["homeassistant.const"]
    hc.ATTR_TEMPERATURE, hc.CONF_NAME, hc.CONF_IP_ADDRESS = "temperature", "name", "ip_address"
    cc.ClimateEntityFeature = enum.IntFlag(
        "ClimateEntityFeature", "TARGET_TEMPERATURE FAN_MODE PRESET_MODE TURN_OFF TURN_ON"
    )
    sys.modules["homeassistant.util"].slugify = lambda s: s.replace(".", "_").lower()
    sys.modules["homeassistant.util"].Throttle = lambda *_: (lambda f: f)
    # pyatrea: stačí enumy (shodné s knihovnou), síť se nepoužívá
    pa = types.ModuleType("pyatrea")
    pa.AtreaProgram = enum.IntEnum("AtreaProgram", "MANUAL WEEKLY TEMPORARY", start=0)
    pa.AtreaMode = enum.IntEnum(
        "AtreaMode",
        "OFF AUTOMATIC VENTILATION CIRCULATION_AND_VENTILATION CIRCULATION "
        "NIGHT_PRECOOLING DISBALANCE OVERPRESSURE PERIODIC_VENTILATION STARTUP "
        "RUNDOWN DEFROSTING EXTERNAL HP_DEFROSTING IN1 IN2 D1 D2 D3 D4",
        start=0,
    )
    pa.Atrea = object
    sys.modules["pyatrea"] = pa
    sys.path.insert(0, os.path.join(ROOT, "custom_components"))


install_stubs()
from pyatrea import AtreaMode  # noqa: E402

climate = importlib.import_module("atrea.climate")
sensor = importlib.import_module("atrea.sensor")
select = importlib.import_module("atrea.select")


# ---------------------------------------------------------------- fakes
def load_status():
    root = ET.parse(os.path.join(HERE, "fixtures", "rk5_status.xml")).getroot()
    return {o.get("I"): o.get("V") for o in root.iter("O")}


class FakeAtrea:
    def __init__(self):
        self.commands = {}
        self.sent = []

    def exec(self):
        self.sent.append(dict(self.commands))
        return True

    getID = getVersion = lambda self: "x"
    getForcedMode = lambda self: AtreaMode.OFF
    getParams = lambda self, *_: {"warning": [], "alert": []}
    getTranslation = lambda self, k: k
    getMode = lambda self: None


class FakeCoordinator:
    async def async_refresh(self):
        pass

    def async_update_listeners(self):
        pass


class FakeHass:
    async def async_add_executor_job(self, fn, *a):
        return fn(*a)


class Entry:
    entry_id = "e"
    data = {"ip_address": "192.168.1.50"}


def make(status=None):
    atrea = FakeAtrea()
    data = {
        "atrea": atrea, "coordinator": FakeCoordinator(), "status": status or load_status(),
        "userLabels": {"Z1Text": "Přízemí"}, "model": None, "params": None,
        "supportedModes": [(i, True) for i in range(8)],
    }
    hass = FakeHass()
    hass.data = {"atrea": {"e": data}}
    presets = {p: True for p in climate.ALL_PRESET_LIST}
    dev = climate.AtreaDevice(hass, Entry(), "atrea", "12,50,100", presets)
    dev.hass = hass
    dev.async_schedule_update_ha_state = lambda *a: None
    data["climate"] = dev
    return dev, atrea, data


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- testy
def test_state_from_dump():
    dev, _, _ = make()
    assert dev.preset_mode == "Automat"          # H10715=1, ne H10705=4
    assert dev.fan_mode == "Max/Min"             # H10714=36
    assert dev.fan_modes[0] == "Vypnuto" and "Norm/Norm" in dev.fan_modes
    assert dev.hvac_mode == HVACMode.FAN_ONLY    # ruční řízení
    assert dev.hvac_action == HVACAction.HEATING # C10215=1
    assert dev.target_temperature == 25.0
    assert dev.current_temperature == 23.5
    a = dev.extra_state_attributes
    assert a["current_phase"] == "Cirkulace"
    assert a["fan_flow_circulation"] == 600 and a["fan_flow_ventilation"] == 100
    assert a["outside_temp"] == 2.1 and a["exhaust_temp"] == 11.7


def test_preset_circulation_while_automat_in_circ_phase_is_sent():
    dev, atrea, _ = make()
    run(dev.async_set_preset_mode("Cirkulace"))
    assert atrea.sent == [{"H10709": "00004", "H10708": "00022"}]


def test_preset_ventilation_maps_power():
    dev, atrea, _ = make()
    run(dev.async_set_preset_mode("Větrání"))
    assert atrea.sent == [{"H10709": "00002", "H10708": "00010"}]


def test_fan_and_temperature_and_schedule_flags():
    dev, atrea, data = make()
    run(dev.async_set_fan_mode("Norm/Max"))
    run(dev.async_set_temperature(temperature=22.5))
    assert atrea.sent == [{"H10708": "00035"}, {"H10710": "00225"}]
    data["status"] = dict(data["status"], H10700="1", H10701="1", H10702="1", H10703="1")
    dev.manualUpdate(False)
    assert dev.hvac_mode == HVACMode.AUTO
    run(dev.async_set_temperature(temperature=21))
    assert atrea.sent[-1] == {"H10710": "00210", "H10702": "00002"}
    run(dev.async_set_hvac_mode(HVACMode.FAN_ONLY))
    assert atrea.sent[-1] == {"H10700": "00000", "H10701": "00000", "H10702": "00000", "H10703": "00000"}


def test_off_and_on():
    dev, atrea, data = make()
    run(dev.async_turn_off())
    assert atrea.sent[-1] == {"H10709": "00000"}
    data["status"] = dict(data["status"], H10715="0", H10709="0", H10714="0", H10708="0")
    dev.manualUpdate(False)
    assert dev.hvac_mode == HVACMode.OFF and dev.fan_modes == ["Vypnuto"]
    run(dev.async_turn_on())  # vrátí poslední režim (Automat) + poslední ruční výkon H10713
    assert atrea.sent[-1] == {"H10709": "00001", "H10708": "00036"}
    data["status"]["H10713"] = "0"
    run(dev.async_turn_on())  # bez známého výkonu → Norm/Norm
    assert atrea.sent[-1] == {"H10709": "00001", "H10708": "00034"}


def test_fan_off_turns_unit_off():
    dev, atrea, _ = make()
    run(dev.async_set_fan_mode("Vypnuto"))
    assert atrea.sent[-1] == {"H10709": "00000"}


def test_zone_select():
    dev, atrea, data = make()
    z = select.AtreaZoneSelect(data, Entry())
    assert z.options == ["Přízemí", "Zóna 2", "Zóna 1+2"] and z.current_option == "Zóna 1+2"
    run(z.async_select_option("Přízemí"))
    assert atrea.sent[-1] == {"H10711": "00000"}


def test_sensors():
    _, _, data = make()
    t = {s["register"]: s for s in sensor.TEMP_SENSORS}
    eha = sensor.AtreaTemperatureSensor(data, Entry(), t["I10214"])
    assert eha.native_value == 11.7
    data["status"]["I10214"] = "65516"
    assert eha.native_value == -2.0
    phase = sensor.AtreaEnumSensor(data, Entry(), sensor.ENUM_SENSORS[0])
    assert phase.native_value == "Cirkulace"
    hours = sensor.AtreaHoursSensor(data, Entry(), sensor.HOURS_SENSORS[0])
    assert hours.native_value == 8100


def test_all_platforms_import():
    # config_flow se neměnil (a stuby neumí HANDLERS.register)
    for mod in ("binary_sensor", "number", "button", "update", "sensor", "select"):
        importlib.import_module(f"atrea.{mod}")
    number = importlib.import_module("atrea.number")
    _, atrea, data = make()
    n = number.AtreaNumber(data, Entry(), number.NUMBERS[0])
    assert n.native_value == 0.5
    run(n.async_set_native_value(0.8))
    assert atrea.sent[-1] == {"H11318": "00008"}
