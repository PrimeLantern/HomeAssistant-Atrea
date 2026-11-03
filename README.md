# HomeAssistant-Atrea (fork: česká lokalizace + R_5)

Custom component - climate platform - for Atrea ventilation units for Home Assistant

## Tento fork

Fork od [JurajNyiri/HomeAssistant-Atrea](https://github.com/JurajNyiri/HomeAssistant-Atrea) s těmito úpravami:

- **Česká lokalizace** — kompletní překlad config flow, dialogu nastavení, názvů předvoleb (Větrání / Cirkulace / Cirkulace s větráním / Noční předchlazení / Rozvážení / Přetlakové větrání), HVAC módů (Vypnuto / Týdenní program / Ruční) a labelů atributů (Výkon / Režim / Řízení VZT).
- **Nativní podpora jednotek řady R_5** (Modbus parameter `H10510=4`) — discrete výkonové stupně podle RD5 specifikace místo procent:
  - Větrání / Cirkulace / další předvolby: `Vypnuto / Min / Norm / Max`
  - Cirkulace s větráním: 9 kombinací (`Min/Min`, `Min/Norm`, ..., `Max/Max`) — formát „výkon cirkulace / výkon větrání"
- **Oprava kompatibility s HA 2025.12+** — odstraněn deprecated `self.config_entry = config_entry` v `OptionsFlow.__init__`, který v HA 2025.12+ vedl k HTTP 500 v dialogu Konfigurace.

**Backward compatibility:** Jednotky Duplex (H10510 ∈ {0, 1, 2, 3}) zachovávají původní procentuální logiku — nic se pro ně nemění.

## Úpravy pro Duplex R-5 RK5 (RD5, provoz 2Z)

Ověřeno offline nad exportem stavu jednotky Duplex R-5 RK5 typ 1, FW 2.03.41
(`H10510=4`, `C10509=1`). Komunikace zůstává přes HTTP (pyatrea, `xml.cgi`).

- **Předvolba** se čte z `H10715` (požadovaný režim), ne z `H10705`. `H10705`
  je aktuální fáze — v Automatu se střídá Větrání/Cirkulace. Je vidět jako
  atribut `current_phase` a senzor „Aktuální fáze režimu".
- **Výkon** se čte z `H10714` (požadovaný), `H10704` je výkon aktuální fáze.
- **Sady výkonu podle režimu** přesně jako web UI (`mode2Z()` ve scripts.js):
  Vypnuto → 0, Cirkulace → 20–22, Automat a Cirkulace s větráním → 30–38
  (cirkulace/větrání), ostatní → 10–12. Atributy `fan_flow_*` ukazují m³/h
  podle `H11627–H11632`.
- **Zápisy jako web UI**: výkon `H10708`, režim `H10709`, teplota `H10710`,
  zóna `H10711`; při Týdenním programu se přidá `2` (Dočasné) jen do příslušné
  rutiny `H10700–H10703`. Režim + převedený výkon jdou v jedné relaci.
- **Teploty** jako int16 (záporné u všech čidel), `1260` = nepřipojené čidlo.
- Nové entity: zóna (select), hystereze topení/chlazení a posun chlazení
  (number), teploty ETA/EHA/průměrná T-ODA, napětí M1/M2, provozní hodiny
  M1/M2, sezóna, přepínání TS/NTS, interval průměrování, TČ topí/chladí.
- `I11401` je aktuální sezóna (0 topná / 1 netopná), binární senzor přejmenován.

Testy: `python -m pytest tests` (bez sítě a bez Home Assistant).

## Instalace přes HACS

1. HACS → Integrations → ⋮ → **Custom repositories**
2. URL: `https://github.com/mposchl/HomeAssistant-Atrea`
3. Category: **Integration**
4. Add → Install → restart Home Assistant
5. Settings → Devices & Services → Add Integration → vyhledat „Atrea"

## Installation using HACS

HACS is a community store for Home Assistant. You can install [HACS](https://github.com/custom-components/hacs) and then install Atrea from the HACS store.

## Installation:

1. In your Home Assistant instance, create directory `/custom_components/atrea` in your `/config` directory.
2. Copy all files from [/custom_components/atrea](https://github.com/JurajNyiri/HomeAssistant-Atrea/tree/master/custom_components/atrea) of this repository to the newly created directory in your Home Assistant.

## Usage:

Add climate unit via Integrations (search for Atrea) in Home Assistant UI. You can also simply click the button below if you have MyHomeAssistant redirects set up.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=atrea)
