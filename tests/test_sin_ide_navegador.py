"""NEXO_NO_WEB_UI=1: el hub no sirve la interfaz web del IDE.

Cubre: GET / muestra la página de descarga (sin JS, sin QR, sin app.js),
/static/* da 404, y /api/* + /ws siguen funcionando igual (el hub sigue
siendo relay). Con el flag apagado (defecto), todo sirve como antes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.core.config import config  # noqa: E402
from backend.main import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _restaurar_flag():
    yield
    # create_app() reescribe el flag global desde el entorno; al terminar
    # cada test, dejarlo en su valor por defecto para no contaminar
    # otros tests (las rutas leen config.NO_WEB_UI en cada request).
    config.NO_WEB_UI = False


def _cliente_con_flag(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("NEXO_NO_WEB_UI", raising=False)
    else:
        monkeypatch.setenv("NEXO_NO_WEB_UI", valor)
    return TestClient(create_app())


def test_flag_on_raiz_muestra_descarga(monkeypatch):
    c = _cliente_con_flag(monkeypatch, "1")
    r = c.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "https://atlantis-studio.netlify.app/" in r.text
    assert "download the desktop app" in r.text
    assert "Open the app to join a project with a code." in r.text
    assert "app.js" not in r.text
    assert "<script" not in r.text.lower()
    assert "qr" not in r.text.lower()


def test_flag_on_static_404(monkeypatch):
    c = _cliente_con_flag(monkeypatch, "1")
    assert c.get("/static/app.js").status_code == 404
    assert c.get("/static/vs/loader.js").status_code == 404


def test_flag_on_api_y_ws_siguen(monkeypatch):
    c = _cliente_con_flag(monkeypatch, "1")
    # La suite entera corre con NEXO_PUBLIC=1 (PUBLIC_MODE=True, cualquier
    # token pasa). Para probar que el endpoint exige auth, bajarlo a False
    # en este test, como hace test_seguridad_tunel.
    public_anterior = config.PUBLIC_MODE
    config.PUBLIC_MODE = False
    try:
        # Remoto con token inválido: el endpoint existe y exige auth (401, no 404).
        r = c.get("/api/proyectos/compartidos",
                  params={"token": "token-invalido"},
                  headers={"cf-connecting-ip": "203.0.113.9"})
        assert r.status_code == 401
    finally:
        config.PUBLIC_MODE = public_anterior
    # /ws sigue aceptando conexiones.
    with c.websocket_connect("/ws?token=&nombre=Prueba"):
        pass


def test_flag_off_raiz_sirve_ide(monkeypatch):
    c = _cliente_con_flag(monkeypatch, None)
    r = c.get("/")
    assert r.status_code == 200
    assert "app.js" in r.text
    assert c.get("/static/app.js").status_code == 200


def test_flag_cero_es_off(monkeypatch):
    c = _cliente_con_flag(monkeypatch, "0")
    r = c.get("/")
    assert r.status_code == 200
    assert "app.js" in r.text
