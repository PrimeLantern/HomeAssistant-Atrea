"""Odolnější klient nad pyatrea.

Opravuje chyby knihovny pyatrea, kvůli kterým se integrace sama nezotavila
a šla ovládat až po restartu Home Assistantu:

* ``auth()`` uložil do ``self.code`` text odpovědi i když byl prázdný
  (``None``) — např. když webserver jednotky krátce neodpovídal. Každý další
  požadavek pak padal na ``"auth=" + None`` (TypeError) a přihlášení se už
  nikdy nezopakovalo.
* Žádný HTTP požadavek neměl timeout — neodpovídající jednotka mohla
  zablokovat aktualizace i příkazy natrvalo.
* ``exec()`` (zápis) nepoznal vypršené přihlášení (403) a příkaz potichu
  zahodil.
"""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET

import pyatrea as _pyatrea
import requests
from pyatrea import Atrea

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # s
FORBIDDEN = "HTTP: 403 Forbidden"


class _TimeoutRequests:
    """Náhrada modulu ``requests`` uvnitř pyatrea — doplní výchozí timeout."""

    def __getattr__(self, name):
        return getattr(requests, name)

    @staticmethod
    def get(url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return requests.get(url, **kwargs)


# pyatrea volá modulový ``requests.get`` — podstrčíme verzi s timeoutem.
_pyatrea.requests = _TimeoutRequests()


class AtreaClient(Atrea):
    """Atrea s bezpečným přihlášením, timeouty a opakováním zápisu po 403."""

    def __init__(self, ip, port=80, password="", code=""):
        super().__init__(ip, port, password or "", code)
        if self.code in (None, "None"):
            self.code = ""

    def getAuthURL(self):
        return "auth=" + (self.code or "")

    def auth(self):
        """Přihlásí se; kód relace uloží jen pokud je platný. Vrací True/False."""
        magic = hashlib.md5(("\r\n" + (self.password or "")).encode("utf-8")).hexdigest()
        try:
            response = requests.get(
                self.getURL("config/login.cgi?magic=" + magic), timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as err:
            LOGGER.debug("Atrea auth: request failed: %s", err)
            return False
        if response.status_code != 200:
            LOGGER.debug("Atrea auth: HTTP %s", response.status_code)
            return False
        try:
            code = ET.fromstring(response.content).text
        except ET.ParseError:
            LOGGER.debug("Atrea auth: neplatná odpověď %r", response.text[:200])
            return False
        if not code or code.strip() in ("", "denied"):
            LOGGER.warning(
                "Atrea: přihlášení se nezdařilo (%s) — zkusím znovu při další aktualizaci",
                "špatné heslo nebo příliš mnoho přihlášených" if code == "denied" else "prázdná odpověď",
            )
            # Neplatný kód nepoužívat; prázdný kód vynutí nové přihlášení příště.
            self.code = ""
            return False
        self.code = code.strip()
        return True

    def getStatus(self, useCache=True):
        if useCache and self.status:
            return self.status
        try:
            return super().getStatus(useCache)
        except ET.ParseError:
            LOGGER.debug("Atrea: neplatné XML stavu")
            return False

    def exec(self):
        """Odešle frontu příkazů; po vypršeném přihlášení se přihlásí a zopakuje."""
        if not self.commands:
            return False
        query = "".join("&" + reg + val for reg, val in self.commands.items())
        for attempt in range(2):
            response = requests.get(
                self.getURL("config/xml.cgi") + query, timeout=REQUEST_TIMEOUT
            )
            if response.status_code == 200 and FORBIDDEN not in response.text:
                return True
            LOGGER.debug(
                "Atrea exec: HTTP %s%s, pokus %d",
                response.status_code,
                " (403)" if FORBIDDEN in response.text else "",
                attempt + 1,
            )
            if not self.auth():
                break
        return False
