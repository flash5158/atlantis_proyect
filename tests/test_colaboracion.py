"""Tests del núcleo colaborativo de Atlantis Studio (sin API keys).

Cubre: bus de agentes (con alcance al equipo del proyecto), compartir
por proyecto con código, versiones de archivos, presencia por WebSocket
y difusión file_changed. No tocan la instalación real:
usan HOME y workspace temporales.
"""

import json
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
os.environ["PORT"] = "18799"
os.environ["ATLANTIS_RELAY_DEFAULT"] = "local"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

client = TestClient(server.app)


def test_sin_rastro_de_claves():
    r = client.get("/api/ai/status")
    assert r.status_code == 200
    cuerpo = r.json()
    assert "gemini" not in cuerpo
    assert cuerpo["hermes"]["instalado"] in (True, False)
    # Config por clave: eliminada
    r = client.post("/api/ai/config", json={"proveedor": "x", "api_key": "sk-fake"})
    assert r.status_code == 410
    # Gemini en la nube: eliminado
    r = client.post("/api/ai/gemini/run", json={"prompt": "hola"})
    assert r.status_code == 410
    # Status sin has_key
    r = client.get("/api/status")
    assert "has_key" not in json.dumps(r.json())


def _compartir_proyecto(carpeta, agente="Koko", usuario="Daniel"):
    r = client.post(f"/api/proyectos/{carpeta}/compartir",
                    json={"proyecto": "general", "agent_name": agente, "user_name": usuario})
    assert r.status_code == 200, r.text
    return r.json()


def _unir_proyecto(codigo, agente, usuario, carpeta):
    return client.post("/api/sync/equipos/unirse", json={
        "code": codigo, "agent_name": agente, "user_name": usuario, "carpeta": carpeta})


def test_equipo_proyecto_handshake_real():
    import uuid
    carpeta = "Colab" + uuid.uuid4().hex[:8]
    # Compartir el proyecto (el anfitrión es el miembro 1)
    res = _compartir_proyecto(carpeta)
    codigo = res["code"]
    assert codigo.startswith("ATL-")
    assert res["miembros"][0]["es_host"] is True
    assert res["join_link"].startswith("atlantis://unirse?code=")

    # Código malo: 404 honesto
    r = _unir_proyecto("ATL-XXXXXX", "X", "Y", carpeta)
    assert r.status_code == 404

    # Join real: el equipo queda con 2 miembros
    r = _unir_proyecto(codigo, "KokoAmigo", "Amigo", carpeta)
    assert r.status_code == 200
    assert len(r.json()["miembros"]) == 2

    # Segundo compañero con el mismo código: el equipo acepta más miembros
    r = _unir_proyecto(codigo, "Z", "W", carpeta)
    assert r.status_code == 200
    assert len(r.json()["miembros"]) == 3


def test_equipo_proyecto_codigo_expirado_y_regenerable():
    import uuid
    from datetime import datetime, timedelta
    from backend import sync as sync_mod
    carpeta = "Colab" + uuid.uuid4().hex[:8]
    res = _compartir_proyecto(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]

    # Forzar expiración del código
    equipos = sync_mod._cargar_equipos()
    for c in equipos[equipo_id]["codigos"]:
        if isinstance(c, dict) and c.get("code") == codigo:
            c["expira_en"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    sync_mod._guardar_equipos(equipos)

    # Unirse con código expirado: 410 con mensaje claro (no 404 genérico)
    r = _unir_proyecto(codigo, "X", "Y", carpeta)
    assert r.status_code == 410
    assert "expirado" in r.json()["error"].lower()
    # Y se puede regenerar: compartir de nuevo da un código nuevo que funciona
    res2 = _compartir_proyecto(carpeta)
    assert res2["code"].startswith("ATL-") and res2["code"] != codigo
    r = _unir_proyecto(res2["code"], "X", "Y", carpeta)
    assert r.status_code == 200


def test_agent_bus_ida_y_vuelta():
    # Koko -> KokoAmigo
    r = client.post("/api/agents/message", json={
        "from": "Koko", "to": "KokoAmigo", "text": "¿Dividimos el trabajo?", "tipo": "mensaje"})
    assert r.status_code == 200
    mid = r.json()["mensaje"]["id"]

    # Inbox del amigo lo ve pendiente
    r = client.get("/api/agents/inbox", params={"agent": "KokoAmigo", "pendientes": True})
    ids = [m["id"] for m in r.json()["mensajes"]]
    assert mid in ids

    # El amigo responde con ack
    r = client.post("/api/agents/ack", json={
        "id": mid, "from": "KokoAmigo", "result": "Yo hago el frontend."})
    assert r.status_code == 200
    resp = r.json()["respuesta"]
    assert resp["de"] == "KokoAmigo" and resp["para"] == "Koko"
    assert resp["respuesta_a"] == mid

    # Ya no está pendiente
    r = client.get("/api/agents/inbox", params={"agent": "KokoAmigo", "pendientes": True})
    assert mid not in [m["id"] for m in r.json()["mensajes"]]

    # El historial del bus lo muestra todo
    r = client.get("/api/agents/bus")
    assert r.json()["total"] >= 2


def test_tarea_asignada_a_agente_viaja_al_bus():
    r = client.post("/api/ai/task", json={
        "titulo": "Implementar login", "descripcion": "OAuth2",
        "asignado": "KokoAmigo", "creador": "Daniel", "proyecto": "general"})
    assert r.status_code == 200
    tid = r.json()["tarea"]["id"]
    # El agente la ve en su inbox como tarea
    r = client.get("/api/agents/inbox", params={"agent": "KokoAmigo", "pendientes": True})
    tareas = [m for m in r.json()["mensajes"] if m.get("task_ref") == tid]
    assert len(tareas) == 1
    assert tareas[0]["tipo"] == "tarea"


def test_tarea_bus_ack_ciclo_completo():    # 1. Humano asigna tarea al agente
    r = client.post("/api/ai/task", json={
        "titulo": "Escribir README", "descripcion": "Documentar el proyecto",
        "asignado": "Koko", "creador": "Daniel", "proyecto": "general"})
    assert r.status_code == 200
    tid = r.json()["tarea"]["id"]

    # 2. El agente la recoge de su inbox
    r = client.get("/api/agents/inbox", params={"agent": "Koko", "pendientes": True})
    tareas = [m for m in r.json()["mensajes"] if m.get("task_ref") == tid]
    assert len(tareas) == 1
    mid = tareas[0]["id"]

    # 3. El agente confirma con su resultado
    r = client.post("/api/agents/ack", json={
        "id": mid, "from": "Koko", "result": "README.md escrito y verificado."})
    assert r.status_code == 200
    resp = r.json()["respuesta"]
    assert resp["respuesta_a"] == mid
    assert resp["de"] == "Koko" and resp["para"] == "Daniel"
    assert resp["estado"] == "completado"

    # 4. El mensaje original quedó completado: ya no sale en pendientes
    r = client.get("/api/agents/inbox", params={"agent": "Koko", "pendientes": True})
    assert mid not in [m["id"] for m in r.json()["mensajes"]]

    # 5. La respuesta es visible en el bus para el humano
    r = client.get("/api/agents/bus", params={"proyecto": "general"})
    respuestas = [m for m in r.json()["mensajes"] if m.get("respuesta_a") == mid]
    assert len(respuestas) == 1
    assert "README.md" in respuestas[0]["texto"]


def test_versiones_de_archivo_y_conflicto():
    ruta = "colab_test/nota.txt"
    # Guardar v1
    r = client.post("/api/archivo", params={"ruta": ruta, "proyecto": "general"},
                    json={"contenido": "v1"})
    assert r.status_code == 200
    assert r.json()["version"] == 1
    # Guardar v2 con expected_version correcta
    r = client.post("/api/archivo", params={"ruta": ruta, "proyecto": "general"},
                    json={"contenido": "v2", "expected_version": 1, "autor": "Daniel"})
    assert r.json()["version"] == 2
    # Conflicto: cliente con versión vieja
    r = client.post("/api/archivo", params={"ruta": ruta, "proyecto": "general"},
                    json={"contenido": "v3-vieja", "expected_version": 1, "autor": "Amigo"})
    assert r.status_code == 409
    assert r.json()["version"] == 2
    assert r.json()["autor"] == "Daniel"
    # GET incluye versión
    r = client.get("/api/archivo", params={"ruta": ruta, "proyecto": "general"})
    assert r.json()["version"] == 2
    assert r.json()["contenido"] == "v2"


def _drenar(ws, segundos=1.0):
    """Vacía los mensajes pendientes sin bloquear (solo REST-testclient)."""
    return


async def _test_ws_dos_clientes():
    """Simulación real de dos clientes contra un servidor vivo."""
    import asyncio
    import subprocess

    import websockets

    puerto = 18797
    env = dict(os.environ)
    env.update({
        "HOME": str(TMP_HOME),
        "NEXO_WORKSPACE": str(TMP_WS),
        "NEXO_PUBLIC": "1",
        "PORT": str(puerto),
    })
    proc = subprocess.Popen(
        [sys.executable, "-u", "nexo/server.py"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # Esperar al servidor
        import urllib.request
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/status", timeout=2)
                break
            except Exception:
                await asyncio.sleep(0.5)
        else:
            raise RuntimeError("el servidor de prueba no arrancó")

        async def _drenar_ws(ws):
            while True:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=0.4)
                except Exception:
                    break

        async with websockets.connect(f"ws://127.0.0.1:{puerto}/ws?nombre=Daniel") as wa, \
                   websockets.connect(f"ws://127.0.0.1:{puerto}/ws?nombre=Amigo") as wb:
            await _drenar_ws(wa)
            await _drenar_ws(wb)

            # 1. A anuncia presencia -> B la recibe
            await wa.send(json.dumps({"tipo": "presence", "file": "main.py",
                                      "cursor": {"line": 10, "col": 3}}))
            encontrado = False
            for _ in range(20):
                try:
                    raw = await asyncio.wait_for(wb.recv(), timeout=2)
                except Exception:
                    break
                msg = json.loads(raw)
                if msg.get("tipo") == "presence_update":
                    if any(u["nombre"] == "Daniel" and u["file"] == "main.py"
                           for u in msg["users"]):
                        u = next(x for x in msg["users"] if x["nombre"] == "Daniel")
                        assert u["cursor"] == {"line": 10, "col": 3} and u["color"]
                        encontrado = True
                        break
            assert encontrado, "B no recibió la presencia de A"

            # 2. A guarda archivo -> B recibe file_changed en vivo
            await wa.send(json.dumps({"tipo": "archivo_escribir", "proyecto": "general",
                                      "ruta": "colab_test/ws.txt", "contenido": "hola desde A"}))
            cambiado = False
            for _ in range(20):
                try:
                    raw = await asyncio.wait_for(wb.recv(), timeout=2)
                except Exception:
                    break
                msg = json.loads(raw)
                if msg.get("tipo") == "file_changed" and msg.get("ruta") == "colab_test/ws.txt":
                    assert msg["autor"] == "Daniel"
                    assert msg["contenido"] == "hola desde A"
                    assert msg["version"] >= 1
                    cambiado = True
                    break
            assert cambiado, "B no recibió file_changed"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def test_websocket_presencia_y_file_changed():
    import asyncio
    asyncio.run(_test_ws_dos_clientes())


def test_hermes_connect_sin_clave():
    r = client.post("/api/hermes/connect", json={
        "name": "Koko", "engine": "local", "model": "auto",
        "api_key": "sk-esto-se-ignora",
    })
    assert r.status_code == 200
    assert r.json()["agent"]["status"] == "connected"
    assert r.json()["agent"]["provider"] == "hermes"


def test_equipo_proyecto_salir_host_cierra_equipo():
    import uuid
    from backend import sync as sync_mod
    from backend.core.config import config
    carpeta = "Colab" + uuid.uuid4().hex[:8]
    res = _compartir_proyecto(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]
    r = _unir_proyecto(codigo, "KokoAmigo", "Amigo", carpeta)
    assert r.status_code == 200

    # El anfitrión sale: el equipo se cierra entero
    r = client.post("/api/sync/equipos/salir", params={"token": config.TOKEN},
                    json={"equipo_id": equipo_id, "agent_name": "Koko",
                          "user_name": "Daniel"})
    assert r.status_code == 200
    assert r.json().get("cerrado") is True
    assert sync_mod.equipo_por_id(equipo_id) is None

    # Unirse después del cierre: 404 honesto (el código ya no existe)
    r = _unir_proyecto(codigo, "Z", "W", carpeta)
    assert r.status_code == 404
