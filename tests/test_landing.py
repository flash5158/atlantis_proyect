"""Tests de la landing pública y la descarga — Atlantis Studio.

Cubre: /landing pública 200, /download 409 honesto sin dist, /download 200
con dist, /api/download/info y /api/net/info. No toca la instalación real:
usa el HOME que el app ya tenga (tests corren con HOME temporal).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

client = TestClient(server.app)

def _dist():
    # Perezoso: create_app() puede re-ejecutarse a mitad de la suite y re-leer HOME.
    return server.config.ATLANTIS_USER_DIR / "dist" / "atlantis-studio.zip"


def _sin_dist():
    if _dist().exists():
        _dist().unlink()


def test_landing_publica_200():
    r = client.get("/landing")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Atlantis Studio" in r.text
    assert "Descargar" in r.text


def test_download_409_sin_dist():
    _sin_dist()
    r = client.get("/download")
    assert r.status_code == 409
    cuerpo = r.json()
    assert cuerpo["ok"] is False
    assert "aún no generó la descarga" in cuerpo["error"]


def test_download_200_con_dist():
    _dist().parent.mkdir(parents=True, exist_ok=True)
    fake = b"PK\x03\x04" + b"fake-zip-bytes" * 100
    _dist().write_bytes(fake)
    try:
        r = client.get("/download")
        assert r.status_code == 200
        assert "zip" in r.headers["content-type"]
        assert r.content == fake
    finally:
        _sin_dist()


def test_download_info_shape():
    _sin_dist()
    r = client.get("/api/download/info")
    assert r.status_code == 200
    d = r.json()
    assert set(d) >= {"ready", "size", "version"}
    assert d["ready"] is False
    assert d["size"] == 0
    assert isinstance(d["version"], str) and d["version"]


def test_net_info_shape():
    r = client.get("/api/net/info")
    assert r.status_code == 200
    d = r.json()
    assert set(d) >= {"lan_ip", "port", "tunnel_url"}
    assert isinstance(d["lan_ip"], str) and d["lan_ip"]
    assert isinstance(d["port"], int)
    assert d["tunnel_url"] is None or isinstance(d["tunnel_url"], str)
