"""Offline testy RD5 logiky nad skutečným exportem stavu (config/xml.xml)."""
import importlib.util
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location(
    "rd5", os.path.join(HERE, "..", "custom_components", "atrea", "rd5.py")
)
rd5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd5)


def load_status():
    root = ET.parse(os.path.join(HERE, "fixtures", "rk5_status.xml")).getroot()
    return {o.get("I"): o.get("V") for o in root.iter("O")}


S = load_status()


def test_profile():
    assert rd5.is_r5(S) and rd5.is_two_zone(S)
    assert rd5.required_mode(S) == rd5.MODE_AUTOMATIC  # H10715=1
    assert rd5.current_phase(S) == rd5.MODE_CIRCULATION  # H10705=4
    assert rd5.required_power(S) == 36  # H10714
    assert rd5.program_state(S) == rd5.PROGRAM_MANUAL


def test_groups_follow_mode2z():
    assert rd5.power_group(S, 0) == "off"
    assert rd5.power_group(S, 1) == "combo"
    assert rd5.power_group(S, 3) == "combo"
    assert rd5.power_group(S, 4) == "circ"
    for m in (2, 5, 6, 7):
        assert rd5.power_group(S, m) == "vent"
    assert list(rd5.group_values("combo").values()) == [0] + list(range(30, 39))


def test_labels():
    assert rd5.label_for_value("combo", 36) == "Max/Min"
    # lag: H10704=22 (Max cirkulace) v kontextu combo → Max/Max
    assert rd5.label_for_value("combo", 22) == "Max/Max"
    assert rd5.label_for_value("vent", 36) == "Min"
    assert rd5.label_for_value("circ", 36) == "Max"


def test_mapping_on_mode_switch():
    assert rd5.map_power_to_group(36, "circ") == 22
    assert rd5.map_power_to_group(36, "vent") == 10
    assert rd5.map_power_to_group(36, "combo") == 36
    assert rd5.map_power_to_group(11, "combo") == 34
    assert rd5.map_power_to_group(0, "vent") == 11
    assert rd5.map_power_to_group(36, "off") == 0


def test_writes_like_web_ui():
    assert rd5.build_write(S, "power", 34) == {"H10708": 34}
    weekly = dict(S, H10702="1")
    assert rd5.build_write(weekly, "temp", 225) == {"H10710": 225, "H10702": 2}
    assert rd5.build_program(1) == {"H10700": 1, "H10701": 1, "H10702": 1, "H10703": 1}


def test_temperatures():
    assert rd5.decode_temp(S["I10211"]) == 2.1
    assert rd5.decode_temp(S["I10214"]) == 11.7
    assert rd5.decode_temp(65516) == -2.0
    assert rd5.decode_temp(S["I10204"]) is None  # 1260 = nepřipojeno
    assert rd5.decode_temp(S["I11420"]) == 4.4


def test_flows_and_hours():
    assert rd5.flow_for_value(S, 36) == {"circulation": 600, "ventilation": 100}
    assert rd5.decode_u32(S, "H13500", "H13501") == 8100


def test_decode_label_js_escape():
    assert rd5.decode_label("Cel%FD%20d%u016Fm") == "Celý dům"
    assert rd5.decode_label("P%u0159%EDzem%ED") == "Přízemí"
    assert rd5.decode_label("Zona%201") == "Zona 1"
    assert rd5.decode_label("Cel%C3%BD") == "Celý"  # UTF-8 percent-encoding
    assert rd5.decode_label("Obývák") == "Obývák"
    assert rd5.decode_labels({"Z3Text": "Cel%FD%20d%u016Fm"}) == {"Z3Text": "Celý dům"}
