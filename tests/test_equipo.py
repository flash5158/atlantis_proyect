"""Tests de equipos POR PROYECTO en Atlantis Studio (el modo equipo viejo
a nivel de app está retirado: /api/pairing/* no existe).

Cubre: compartir un proyecto, unir varios miembros, join idempotente,
leave de miembro, leave del anfitrión (cierra el equipo), código
expirado rechazado (410), código revocado rechazado (410) mientras los
códigos hermanos siguen funcionando, y límite de 8 miembros.
Usa HOME y workspace temporales (no toca la instalación real).
"""

import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

TMP_HOME = Path(tempfile.mkdtemp(prefix="atlantis-test-home-"))
TMP_WS = Path(tempfile.mkdtemp(prefix="atlantis-test-ws-"))
(TMP_WS / "proyectos").mkdir(exist_ok=True)

os.environ["HOME"] = str(TMP_HOME)
os.environ["NEXO_WORKSPACE"] = str(TMP_WS)
os.environ["NEXO_PUBLIC"] = "1"
os.environ["PORT"] = "18799"
os.environ["ATLANTIS_RELAY_DEFAULT"] = "local"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from backend import sync as sync_mod  # noqa: E402
from backend.core.config import config  # noqa: E402

client = TestClient(server.app)


def _carpeta() -> str:
    return "Equipo" + uuid.uuid4().hex[:8]


def _compartir(carpeta: str, **kw):
    body = {"proyecto": "general", "agent_name": "Koko", "user_name": "Daniel"}
    body.update(kw)
    r = client.post(f"/api/proyectos/{carpeta}/compartir", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _unir(codigo, agente, usuario, carpeta):
    r = client.post("/api/sync/equipos/unirse", json={
        "code": codigo, "agent_name": agente, "user_name": usuario,
        "carpeta": carpeta})
    assert r.status_code == 200, r.text
    return r.json()


def _salir(equipo_id, token, agente, usuario):
    return client.post("/api/sync/equipos/salir",
                       params={"token": token},
                       json={"equipo_id": equipo_id, "agent_name": agente,
                             "user_name": usuario})


def _expirar_codigo(equipo_id, codigo):
    equipos = sync_mod._cargar_equipos()
    equipo = equipos[equipo_id]
    for c in equipo["codigos"]:
        if isinstance(c, dict) and c.get("code") == codigo:
            c["expira_en"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    sync_mod._guardar_equipos(equipos)


def test_equipo_crear_unir_tres_miembros():
    carpeta = _carpeta()
    res = _compartir(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]
    _unir(codigo, "KokoAmigo", "Amigo", carpeta)
    res2 = _unir(codigo, "KokoTercero", "Tercero", carpeta)
    assert len(res2["miembros"]) == 3
    assert len(res2["miembros"]) == 3  # equipo activo
    miembros = res2["miembros"]
    assert miembros[0]["es_host"] is True
    assert miembros[0]["agent_name"] == "Koko"
    assert sum(1 for m in miembros if m["es_host"]) == 1
    # Cada miembro lleva su identidad de agente (id, nombre, color, dueño)
    for m in miembros:
        assert m["agente"]["nombre"] == m["agent_name"]
        assert m["agente"]["dueno"] == m["user_name"]
        assert m["agente"]["color"].startswith("#")
        assert "guest_token" not in m


def test_equipo_join_duplicado_es_idempotente():
    carpeta = _carpeta()
    res = _compartir(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]
    _unir(codigo, "KokoAmigo", "Amigo", carpeta)
    # El mismo usuario se une otra vez: ok, sin duplicarlo (aunque
    # cambie el nombre de agente, la identidad es el usuario).
    res2 = _unir(codigo, "KokoAmigo", "Amigo", carpeta)
    assert res2["ya_miembro"] is True
    assert len(res2["miembros"]) == 2
    res3 = _unir(codigo, "OtroNombre", "Amigo", carpeta)
    assert res3["ya_miembro"] is True
    assert len(res3["miembros"]) == 2
    # Insensible a mayúsculas/minúsculas
    res4 = _unir(codigo, "kokoamigo", "amigo", carpeta)
    assert res4["ya_miembro"] is True
    assert len(res4["miembros"]) == 2


def test_equipo_leave_de_miembro():
    carpeta = _carpeta()
    res = _compartir(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]
    amigo = _unir(codigo, "KokoAmigo", "Amigo", carpeta)
    _unir(codigo, "KokoTercero", "Tercero", carpeta)

    r = _salir(equipo_id, amigo["guest_token"], "KokoAmigo", "Amigo")
    assert r.status_code == 200, r.text
    assert r.json().get("cerrado") is not True
    assert len(r.json()["equipo"]["miembros"]) == 2

    equipo = sync_mod.equipo_por_id(equipo_id)
    agentes = [m["agent_name"] for m in equipo["miembros"]]
    assert agentes == ["Koko", "KokoTercero"]

    # Salir dos veces: la segunda es 404 honesto
    r = _salir(equipo_id, amigo["guest_token"], "KokoAmigo", "Amigo")
    assert r.status_code == 404


def test_equipo_leave_del_host_cierra_la_sesion():
    carpeta = _carpeta()
    res = _compartir(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]
    _unir(codigo, "KokoAmigo", "Amigo", carpeta)

    r = _salir(equipo_id, config.TOKEN, "Koko", "Daniel")
    assert r.status_code == 200, r.text
    assert r.json().get("cerrado") is True
    assert sync_mod.equipo_por_id(equipo_id) is None


def test_equipo_codigo_expirado_rechazado():
    carpeta = _carpeta()
    res = _compartir(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]
    _expirar_codigo(equipo_id, codigo)
    r = client.post("/api/sync/equipos/unirse", json={
        "code": codigo, "agent_name": "X", "user_name": "Y", "carpeta": carpeta})
    assert r.status_code == 410
    assert "expirado" in r.json()["error"].lower()


def test_equipo_codigo_revocado_rechazado_hermanos_siguen():
    carpeta = _carpeta()
    res = _compartir(carpeta)
    codigo1, equipo_id = res["code"], res["equipo_id"]
    # Segundo código para el mismo equipo (equipo abierto)
    res2 = _compartir(carpeta)
    codigo2 = res2["code"]
    assert codigo2 != codigo1
    assert res2["equipo_id"] == equipo_id

    r = client.post("/api/sync/equipos/codigos/revocar",
                    params={"token": config.TOKEN},
                    json={"equipo_id": equipo_id, "code": codigo1})
    assert r.status_code == 200, r.text

    # El revocado se rechaza con 410 y error claro
    r = client.post("/api/sync/equipos/unirse", json={
        "code": codigo1, "agent_name": "X", "user_name": "Y", "carpeta": carpeta})
    assert r.status_code == 410
    assert "revocado" in r.json()["error"].lower()
    # El hermano sigue funcionando
    res3 = _unir(codigo2, "KokoAmigo", "Amigo", carpeta)
    assert len(res3["miembros"]) == 2


def test_equipo_limite_ocho_miembros():
    carpeta = _carpeta()
    res = _compartir(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]
    # Anfitrión + 7 = 8 (el máximo)
    for i in range(7):
        _unir(codigo, f"Agente{i}", f"Usuario{i}", carpeta)
    equipo = sync_mod.equipo_por_id(equipo_id)
    assert len(equipo["miembros"]) == 8

    # El noveno es rechazado con 403 honesto
    r = client.post("/api/sync/equipos/unirse", json={
        "code": codigo, "agent_name": "AgenteX", "user_name": "UsuarioX",
        "carpeta": carpeta})
    assert r.status_code == 403
    assert "8" in r.json()["error"]
    equipo = sync_mod.equipo_por_id(equipo_id)
    assert len(equipo["miembros"]) == 8


def test_equipo_codigo_expirado_no_afecta_equipo_activo():
    # Un código expirado se rechaza, pero el equipo activo sigue vivo
    # y sus demás códigos vigentes siguen funcionando.
    carpeta = _carpeta()
    res = _compartir(carpeta)
    codigo1, equipo_id = res["code"], res["equipo_id"]
    _unir(codigo1, "KokoAmigo", "Amigo", carpeta)
    res2 = _compartir(carpeta)
    codigo2 = res2["code"]
    _expirar_codigo(equipo_id, codigo2)
    r = client.post("/api/sync/equipos/unirse", json={
        "code": codigo2, "agent_name": "X", "user_name": "Y", "carpeta": carpeta})
    assert r.status_code == 410
    # El equipo sigue activo y el primer código sigue valiendo
    res3 = _unir(codigo1, "KokoTercero", "Tercero", carpeta)
    assert len(res3["miembros"]) == 3
