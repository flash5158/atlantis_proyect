"""Tests de entrega persistente del bus de agentes (v0.6.1).

El bus era fire-and-forget: mensaje publicado con el peer desconectado
= mensaje perdido. Ahora los mensajes persisten por equipo y cada
agente lleva un cursor: al (re)conectar recibe los perdidos en orden,
sin duplicados, sin ver mensajes de otros equipos.
"""

import os
import sys
import tempfile
from pathlib import Path

TMP_HOME = Path(tempfile.mkdtemp(prefix="atlantis-test-home-"))
TMP_WS = Path(tempfile.mkdtemp(prefix="atlantis-test-ws-"))
(TMP_WS / "proyectos").mkdir(exist_ok=True)

os.environ["HOME"] = str(TMP_HOME)
os.environ["NEXO_WORKSPACE"] = str(TMP_WS)
os.environ["NEXO_PUBLIC"] = "1"
os.environ["PORT"] = "18801"
os.environ["ATLANTIS_RELAY_DEFAULT"] = "local"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

client = TestClient(server.app)


def _post_equipo(equipo, de, para, texto):
    r = client.post("/api/agents/message", json={
        "from": de, "to": para, "text": texto, "tipo": "mensaje",
        "equipo_id": equipo, "carpeta": "demo",
        "agente": {"nombre": de, "color": "#22d3ee"},
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    return r.json()["mensaje"]


def _conectar(equipo, agente):
    r = client.post("/api/agents/bus/conectar",
                    json={"equipo_id": equipo, "agente": agente})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    return body


def test_offline_reconnect_recibe_perdidos_en_orden():
    # B está "desconectado": nadie llama conectar por él.
    _post_equipo("EQ1", "AgenteA", "AgenteB", "primero")
    _post_equipo("EQ1", "AgenteA", "AgenteB", "segundo")
    _post_equipo("EQ1", "AgenteA", "AgenteB", "tercero")
    # B se (re)conecta: recibe los 3 perdidos, en orden, con seq creciente.
    res = _conectar("EQ1", "AgenteB")
    assert res["entregados"] == 3
    textos = [m["texto"] for m in res["mensajes"]]
    assert textos == ["primero", "segundo", "tercero"]
    seqs = [m["seq"] for m in res["mensajes"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    # Identidad del que habla preservada en cada mensaje.
    assert all(m["agente"]["nombre"] == "AgenteA" for m in res["mensajes"])
    assert all(m["equipo_id"] == "EQ1" for m in res["mensajes"])


def test_sin_duplicados_en_segunda_conexion():
    _post_equipo("EQ2", "AgenteA", "AgenteB", "hola")
    r1 = _conectar("EQ2", "AgenteB")
    assert r1["entregados"] == 1
    # Segunda conexión sin mensajes nuevos: nada (sin duplicados).
    r2 = _conectar("EQ2", "AgenteB")
    assert r2["entregados"] == 0
    assert r2["mensajes"] == []
    # Un mensaje nuevo sí llega, y solo ese.
    _post_equipo("EQ2", "AgenteA", "AgenteB", "nuevo")
    r3 = _conectar("EQ2", "AgenteB")
    assert r3["entregados"] == 1
    assert r3["mensajes"][0]["texto"] == "nuevo"


def test_aislamiento_entre_equipos():
    _post_equipo("EQX", "AgenteA", "AgenteB", "secreto de X")
    # El agente del equipo Y no recibe nada del equipo X.
    res_y = _conectar("EQY", "AgenteB")
    assert res_y["entregados"] == 0
    # El agente del equipo X sí lo recibe.
    res_x = _conectar("EQX", "AgenteB")
    assert res_x["entregados"] == 1
    assert res_x["mensajes"][0]["texto"] == "secreto de X"
    # Mensaje dirigido a otro agente no se entrega a este.
    _post_equipo("EQX", "AgenteA", "AgenteC", "solo para C")
    res_b = _conectar("EQX", "AgenteB")
    assert all(m["texto"] != "solo para C" for m in res_b["mensajes"])
    res_c = _conectar("EQX", "AgenteC")
    assert any(m["texto"] == "solo para C" for m in res_c["mensajes"])


def test_retencion_200_por_equipo():
    for i in range(205):
        _post_equipo("EQRET", "AgenteA", "todos", f"msg-{i:03d}")
    r = client.get("/api/agents/bus", params={"equipo_id": "EQRET"})
    assert r.status_code == 200
    total = r.json()["total"]
    assert total == 200, f"retención esperada 200, hay {total}"
    textos = [m["texto"] for m in r.json()["mensajes"]]
    # Se podaron los 5 más viejos, quedan los 200 más recientes.
    assert "msg-000" not in textos
    assert "msg-004" not in textos
    assert "msg-005" in textos
    assert "msg-204" in textos


def test_post_falla_si_persistencia_falla(monkeypatch):
    # Simula la BD caída: el post debe devolver error real, no ok:true.
    import backend.db as db_mod
    from contextlib import contextmanager

    @contextmanager
    def _roto():
        raise RuntimeError("BD no disponible (simulado)")
        yield

    monkeypatch.setattr(db_mod, "session_scope", _roto)
    r = client.post("/api/agents/message", json={
        "from": "AgenteA", "to": "todos", "text": "esto no debe guardarse",
        "equipo_id": "EQFAIL", "carpeta": "demo"})
    assert r.status_code == 500
    body = r.json()
    assert body["ok"] is not True
    assert "error" in body


def test_conectar_requiere_equipo_y_agente():
    r = client.post("/api/agents/bus/conectar", json={"equipo_id": "", "agente": ""})
    assert r.status_code == 400
    r = client.post("/api/agents/bus/conectar", json={"equipo_id": "EQ1"})
    assert r.status_code == 400
