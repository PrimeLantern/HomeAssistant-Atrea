"""Zotavení klienta po prázdné odpovědi při přihlášení (TypeError auth=None)."""
import os
import sys
import types

import pytest

HERE = os.path.dirname(__file__)
FIXTURE = open(os.path.join(HERE, "fixtures", "rk5_status.xml"), "rb").read()


class Resp:
    def __init__(self, body, status=200):
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "replace")
        self.status_code = status


class FakeUnit:
    """Webserver RD5: login vrací kód nebo prázdno, xml.xml vyžaduje platný kód."""

    def __init__(self):
        self.valid = "12345"
        self.empty_login = 0
        self.calls = []
        self.timeouts = []

    def get(self, url, **kw):
        self.timeouts.append(kw.get("timeout"))
        self.calls.append(url)
        if "login.cgi" in url:
            if self.empty_login:
                self.empty_login -= 1
                return Resp("<root></root>")  # text = None
            return Resp(f"<root>{self.valid}</root>")
        if f"auth={self.valid}&" not in url:
            return Resp("HTTP: 403 Forbidden")
        return Resp(FIXTURE)


def load_client(unit):
    fake_requests = types.ModuleType("requests")
    fake_requests.get = unit.get
    fake_requests.RequestException = Exception
    sys.modules["requests"] = fake_requests
    sys.modules.setdefault("demjson3", types.ModuleType("demjson3"))
    for m in ("pyatrea", "atrea.client"):
        sys.modules.pop(m, None)
    # pyatrea (fork mposchl) — cesta přes PYATREA_PATH, jinak nainstalovaný balíček
    sys.path.insert(0, os.environ.get("PYATREA_PATH", "/tmp/mpyatrea"))
    try:
        import pyatrea  # noqa: F401
    except ImportError:
        pytest.skip("pyatrea není k dispozici")
    sys.path.insert(0, os.path.join(HERE, "..", "custom_components"))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "atrea_client", os.path.join(HERE, "..", "custom_components", "atrea", "client.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recovers_after_empty_login():
    unit = FakeUnit()
    client = load_client(unit).AtreaClient("10.0.0.5", 80, "heslo")
    assert client.getStatus(False)["H10715"] == "1"

    # jednotka zneplatní relaci a jednou vrátí prázdný login
    unit.valid, unit.empty_login = "67890", 1
    assert client.getStatus(False) is False          # dřív TypeError navždy
    assert client.code == ""                          # ne None
    assert client.getStatus(False)["H10715"] == "1"   # další interval: zotaveno
    assert client.code == "67890"


def test_exec_reauths_on_403_and_uses_timeouts():
    unit = FakeUnit()
    client = load_client(unit).AtreaClient("10.0.0.5", 80, "heslo")
    client.getStatus(False)
    unit.valid = "55555"
    client.commands = {"H10709": "00002"}
    assert client.exec() is True
    assert "auth=55555&" in unit.calls[-1] and "H1070900002" in unit.calls[-1]
    assert all(t for t in unit.timeouts)


def test_exec_reports_failure():
    unit = FakeUnit()
    client = load_client(unit).AtreaClient("10.0.0.5", 80, "heslo")
    client.getStatus(False)
    unit.valid, unit.empty_login = "11111", 5
    client.commands = {"H10709": "00002"}
    assert client.exec() is False
