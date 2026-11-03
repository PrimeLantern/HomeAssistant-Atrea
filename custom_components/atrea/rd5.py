"""Čistá logika RD5 (bez závislostí na Home Assistant / pyatrea).

Vše je odvozené z webového rozhraní jednotky (userCtrl.xml + scripts.js,
FW 2.03.x) a z „Index table for Modbus TCP - RD5 control system".
Modul je záměrně bez importů HA, aby šel testovat samostatně.

Klíčová pozorování na jednotce Duplex R-5 RK5 (H10510=4, C10509=1):

* H10715 = požadovaný režim (to, co uživatel zvolil; web UI „Požadovaný režim").
  H10705 = aktuálně běžící fáze — v režimu Automat se střídá Větrání/Cirkulace.
* H10714 = požadovaný výkon, H10704 = výkon aktuální fáze.
* Sada platných hodnot výkonu závisí na požadovaném režimu (scripts.js mode2Z):
    H10715 = 0           → [0]
    H10715 = 4           → 20/21/22            (cirkulace Min/Norm/Max)
    H10715 = 1 nebo 3    → 30..38              (cirk./větr. kombinace)
    H10715 = 2,5,6,7     → 10/11/12            (větrání Min/Norm/Max)
* Bez 2Z (C10509=0) a H10510=4 web UI používá Power_4 (1..9 kombinace) pro
  Automat a Power_5 (1..3) pro ostatní režimy.
* Zápis (userCtrl.xml): výkon → H10708, režim → H10709, teplota → H10710,
  zóna → H10711. Pokud je příslušná rutina (H10700..H10703) v Týdenním
  programu (=1), přidá se do téže relace zápis 2 (Dočasné) jen do té jedné.
"""

from __future__ import annotations

LEVELS = ("Min", "Norm", "Max")
OFF_LABEL = "Vypnuto"

MODE_OFF = 0
MODE_AUTOMATIC = 1
MODE_VENTILATION = 2
MODE_CIRC_VENT = 3
MODE_CIRCULATION = 4

PROGRAM_MANUAL = 0
PROGRAM_WEEKLY = 1
PROGRAM_TEMPORARY = 2

REG_PROGRAM = {"power": "H10700", "mode": "H10701", "temp": "H10702", "zone": "H10703"}
REG_WRITE = {"power": "H10708", "mode": "H10709", "temp": "H10710", "zone": "H10711"}

# Hodnota 126,0 °C = nepřipojené čidlo (TA2, TRk, …).
TEMP_DISCONNECTED = 1260

# H10712 „Vnucený stav" — op ModeText z userCtrl.xml.
FORCED_MODE_LABELS = {
    0: "Nic",
    1: "Náběh",
    2: "Doběh",
    3: "D1",
    4: "D2",
    5: "D3",
    6: "D4",
    7: "IN1",
    8: "IN2",
    9: "INk1/1",
    10: "INk2/1",
    11: "INk3/1",
    12: "INk4/1",
    13: "INk1/2",
    14: "INk2/2",
    15: "INk3/2",
    16: "INk4/2",
    27: "Odmražení TČ",
    28: "Předehřívání",
    29: "Učení",
    30: "Odmražení TČ",
    31: "Test filtru",
    32: "Periodické větrání",
}
# Klíče uživatelských textů (config/texts.xml) pro vstupy D1..INk.
FORCED_MODE_TEXT_KEYS = {
    3: "D1", 4: "D2", 5: "D3", 6: "D4", 7: "IN1", 8: "IN2",
    9: "INK1_1", 10: "INK1_2", 11: "INK1_3", 12: "INK1_4",
    13: "INK2_1", 14: "INK2_2", 15: "INK2_3", 16: "INK2_4",
}

MODE_LABELS = {
    0: "Vypnuto",
    1: "Automat",
    2: "Větrání",
    3: "Cirkulace s větráním",
    4: "Cirkulace",
    5: "Noční předchlazení",
    6: "Rozvážení",
    7: "Přetlakové větrání",
}

ZONE_LABELS = {0: "Zóna 1", 1: "Zóna 2", 2: "Zóna 1+2"}
ZONE_TEXT_KEYS = {0: "Z1Text", 1: "Z2Text", 2: "Z3Text"}

SEASON_LABELS = {0: "Topná", 1: "Netopná"}
SEASON_SWITCH_LABELS = {0: "Topná", 1: "Netopná", 2: "T-ODA", 3: "T-ODA+"}
TODA_AVG_LABELS = {
    0: "1 h", 1: "3 h", 2: "6 h", 3: "12 h", 4: "1 den", 5: "2 dny",
    6: "3 dny", 7: "4 dny", 8: "5 dní", 9: "6 dní", 10: "7 dní",
    11: "8 dní", 12: "9 dní", 13: "10 dní",
}


# --------------------------------------------------------------------------
# Obecné čtení
# --------------------------------------------------------------------------
def ival(status, key, default=None):
    """Celé číslo z dict stavu (hodnoty jsou stringy), jinak default."""
    if not status or key not in status:
        return default
    try:
        return int(status[key])
    except (TypeError, ValueError):
        return default


def decode_temp(raw):
    """Teplota °C ×10 jako int16. 1260 (=126,0 °C) = nepřipojené čidlo → None."""
    if raw is None:
        return None
    raw = int(raw)
    if raw == TEMP_DISCONNECTED:
        return None
    if raw >= 32768:
        raw -= 65536
    return round(raw / 10, 1)


def decode_u32(status, low_key, high_key):
    low = ival(status, low_key)
    high = ival(status, high_key, 0)
    if low is None:
        return None
    return low + high * 65536


def program_state(status):
    """Stav řízení jako ve web UI: Dočasné má přednost, pak Týdenní, jinak Ruční."""
    vals = [ival(status, r, 0) for r in REG_PROGRAM.values()]
    if PROGRAM_TEMPORARY in vals:
        return PROGRAM_TEMPORARY
    if PROGRAM_WEEKLY in vals:
        return PROGRAM_WEEKLY
    return PROGRAM_MANUAL


def required_mode(status):
    """Požadovaný režim (H10715), fallback na H10709 / H10705."""
    for key in ("H10715", "H10709", "H10705"):
        v = ival(status, key)
        if v is not None:
            return v
    return None


def current_phase(status):
    """Aktuálně běžící fáze (H10705)."""
    return ival(status, "H10705")


def required_power(status):
    """Požadovaný výkon (H10714), fallback H10708 / H10704."""
    for key in ("H10714", "H10708", "H10704"):
        v = ival(status, key)
        if v is not None:
            return v
    return None


def build_write(status, kind, value):
    """Sestaví {registr: hodnota} pro zápis jedné řídicí veličiny jako web UI."""
    cmds = {REG_WRITE[kind]: int(value)}
    if ival(status, REG_PROGRAM[kind]) == PROGRAM_WEEKLY:
        cmds[REG_PROGRAM[kind]] = PROGRAM_TEMPORARY
    return cmds


def build_program(program):
    """Přepnutí rutiny řízení pro všechny 4 veličiny (jako přepínač ve web UI)."""
    return {reg: int(program) for reg in REG_PROGRAM.values()}


# --------------------------------------------------------------------------
# R_5 výkonové stupně
# --------------------------------------------------------------------------
def is_two_zone(status):
    return ival(status, "C10509") == 1


def is_r5(status):
    return ival(status, "H10510") == 4 or is_two_zone(status)


def power_group(status, mode):
    """Vrátí identifikátor sady hodnot výkonu pro daný požadovaný režim.

    'off', 'vent', 'circ', 'combo' (2Z: 10-12 / 20-22 / 30-38),
    'r5_combo' (bez 2Z, Automat: 1..9), 'r5_single' (bez 2Z: 1..3).
    """
    if mode is None:
        return None
    if mode == MODE_OFF:
        return "off"
    if is_two_zone(status):
        if mode == MODE_CIRCULATION:
            return "circ"
        if mode in (MODE_AUTOMATIC, MODE_CIRC_VENT):
            return "combo"
        return "vent"
    # R_5 bez 2Z — web UI rozhoduje podle H10705 (aktuální fáze) == 1
    if mode == MODE_AUTOMATIC:
        return "r5_combo"
    return "r5_single"


def group_values(group):
    """{label: hodnota registru} pro skupinu, v pořadí pro UI."""
    if group == "off" or group is None:
        return {OFF_LABEL: 0}
    out = {OFF_LABEL: 0}
    if group == "vent":
        out.update({lvl: 10 + i for i, lvl in enumerate(LEVELS)})
    elif group == "circ":
        out.update({lvl: 20 + i for i, lvl in enumerate(LEVELS)})
    elif group == "combo":
        for c, cl in enumerate(LEVELS):
            for v, vl in enumerate(LEVELS):
                out[f"{cl}/{vl}"] = 30 + c * 3 + v
    elif group == "r5_single":
        out.update({lvl: 1 + i for i, lvl in enumerate(LEVELS)})
    elif group == "r5_combo":
        for c, cl in enumerate(LEVELS):
            for v, vl in enumerate(LEVELS):
                out[f"{cl}/{vl}"] = 1 + c * 3 + v
    return out


def split_levels(value, group_hint=None):
    """Rozloží hodnotu na (cirk_úroveň, vět_úroveň), 0..2 nebo None."""
    if value is None:
        return (None, None)
    v = int(value)
    if 10 <= v <= 12:
        return (None, v - 10)
    if 20 <= v <= 22:
        return (v - 20, None)
    if 30 <= v <= 38:
        return ((v - 30) // 3, (v - 30) % 3)
    if group_hint == "r5_combo" and 1 <= v <= 9:
        return ((v - 1) // 3, (v - 1) % 3)
    if 1 <= v <= 3:
        return (None, v - 1)
    return (None, None)


def encode_levels(group, circ, vent):
    """Složí hodnotu pro skupinu z úrovní; chybějící složku doplní druhou/Norm."""
    if group in (None, "off"):
        return 0
    if circ is None and vent is None:
        circ = vent = 1  # Norm
    if circ is None:
        circ = vent
    if vent is None:
        vent = circ
    if group == "vent":
        return 10 + vent
    if group == "circ":
        return 20 + circ
    if group == "combo":
        return 30 + circ * 3 + vent
    if group == "r5_single":
        return 1 + vent
    if group == "r5_combo":
        return 1 + circ * 3 + vent
    return None


def map_power_to_group(value, group, source_group=None):
    """Převede výkon do platné hodnoty cílové skupiny se zachováním úrovní.

    Vrátí hodnotu beze změny, pokud už do skupiny patří.
    0 (vypnuto) se převede na Norm, aby jednotka po přepnutí režimu běžela.
    """
    if group in (None, "off"):
        return 0
    valid = group_values(group).values()
    if value is not None and int(value) in valid and int(value) != 0:
        return int(value)
    circ, vent = split_levels(value, source_group)
    return encode_levels(group, circ, vent)


def label_for_value(group, value):
    """Label pro hodnotu výkonu v kontextu skupiny (s koercí při lagu)."""
    if value is None:
        return None
    v = int(value)
    if v == 0:
        return OFF_LABEL
    values = group_values(group)
    for label, raw in values.items():
        if raw == v:
            return label
    # Hodnota nepatří do skupiny (jednotka ještě nepřevzala nový režim)
    mapped = map_power_to_group(v, group)
    for label, raw in values.items():
        if raw == mapped:
            return label
    return f"Neznámý ({v})"


def flow_for_value(status, value, group_hint=None):
    """Průtok m³/h pro hodnotu výkonu R_5 podle H11627..H11632.

    Vrací dict {'circulation': m3h|None, 'ventilation': m3h|None}.
    """
    circ, vent = split_levels(value, group_hint)
    out = {"circulation": None, "ventilation": None}
    if circ is not None:
        out["circulation"] = ival(status, f"H1162{7 + circ}")
    if vent is not None:
        out["ventilation"] = ival(status, f"H116{30 + vent}")
    return out
