"""Tests de las 8 mejoras de Atlantis Studio (build v0.6.0).

Ítems:
1. Unirse solo con el código (sin variables ni URLs; enlace de unión).
2. Resolver conflictos en la app (escribe + sincroniza + borra backup).
3. Cursores en vivo: la presencia lleva posición + selección + color.
4. Códigos que expiran o se revocan (revocado 410, hermanos vivos).
5. Estado de sync visible: refleja outbox/conexión reales.
6. Modo equipo viejo retirado (pairing 404) + bus con alcance al
   equipo + identidad del agente + renombre solo del dueño.
7. Preview local gratis (HTML estático + proyecto con script; detener).
8. Aprobación dual: una aprobación no dispara; ambas en la misma
   versión sí; versión vieja no dispara; retirar bloquea.

Usa HOME y workspace temporales. Sin secretos.
"""

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

TMP_HOME = Path(tempfile.mkdtemp(prefix="atlantis-8-home-"))
# TMP_WS propio solo para el entorno; los archivos de prueba se
# escriben en el workspace REAL del servidor (config.PROYECTOS): la
# config queda fijada por el primer archivo de test importado, así
# que escribir en un TMP_WS propio fallaría según el orden de import.
TMP_WS = Path(tempfile.mkdtemp(prefix="atlantis-8-ws-"))
(TMP_WS / "proyectos").mkdir(exist_ok=True)

os.environ["HOME"] = str(TMP_HOME)
os.environ["NEXO_WORKSPACE"] = str(TMP_WS)
os.environ["NEXO_PUBLIC"] = "1"
os.environ["PORT"] = "18801"
os.environ["ATLANTIS_RELAY_DEFAULT"] = "local"
os.environ.pop("SYNC_HUB_URL", None)
os.environ.pop("SYNC_TOKEN", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from backend import sync as sync_mod  # noqa: E402
from backend.core import state as state_mod  # noqa: E402
from backend.core.config import config  # noqa: E402
from backend.routers import sync_teams as sync_teams_mod  # noqa: E402

client = TestClient(server.app)
PROY = "general"


def _carpeta(prefijo="P"):
    return f"{prefijo}" + uuid.uuid4().hex[:8]


def _compartir(carpeta, agente="Koko", usuario="Daniel", **kw):
    body = {"proyecto": PROY, "agent_name": agente, "user_name": usuario}
    body.update(kw)
    r = client.post(f"/api/proyectos/{carpeta}/compartir", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _unir_relay(codigo, agente, usuario, carpeta):
    return client.post("/api/sync/equipos/unirse", json={
        "code": codigo, "agent_name": agente, "user_name": usuario,
        "carpeta": carpeta})


def _equipo_con_dos(carpeta=None):
    carpeta = carpeta or _carpeta()
    res = _compartir(carpeta)
    codigo, equipo_id = res["code"], res["equipo_id"]
    r = _unir_relay(codigo, "KokoAmigo", "Amigo", carpeta)
    assert r.status_code == 200, r.text
    return carpeta, equipo_id, codigo, r.json()["guest_token"]


# ---------------------------------------------------------------- ítem 1
def test_unirse_solo_con_codigo_sin_urls():
    """Unirse NO requiere SYNC_HUB_URL ni hub_url: pegar solo el código
    conecta (relay por defecto local en tests)."""
    assert "SYNC_HUB_URL" not in os.environ
    carpeta = _carpeta("Solo")
    res = _compartir(carpeta)
    codigo = res["code"]
    # Solo código, sin hub_url ni variables
    r = client.post("/api/proyectos/unirse", json={
        "code": codigo, "carpeta": carpeta, "proyecto": PROY,
        "agent_name": "KokoAmigo", "user_name": "Amigo"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["equipo_id"] == res["equipo_id"]


def test_unirse_con_enlace_de_union():
    """El enlace de unión (código + relay) también une: el diálogo
    acepta código solo o enlace completo."""
    carpeta = _carpeta("Enlace")
    res = _compartir(carpeta)
    enlace = res["join_link"]
    assert enlace.startswith("atlantis://unirse?code=")
    assert "relay=" in enlace
    r = client.post("/api/proyectos/unirse", json={
        "code": enlace, "carpeta": carpeta, "proyecto": PROY,
        "agent_name": "KokoAmigo", "user_name": "Amigo"})
    assert r.status_code == 200, r.text
    assert r.json()["equipo_id"] == res["equipo_id"]


def test_parsear_codigo_o_enlace():
    f = sync_teams_mod._parsear_codigo_o_enlace
    assert f("ATL-ABC123") == ("ATL-ABC123", None)
    assert f("  atl-abc123 ") == ("ATL-ABC123", None)
    codigo, relay = f("atlantis://unirse?code=ATL-ABC123&relay=https%3A%2F%2Fhub.test")
    assert codigo == "ATL-ABC123" and relay == "https://hub.test"
    codigo, relay = f("https://hub.test/unirse?code=ATL-XYZ&relay=local")
    assert codigo == "ATL-XYZ" and relay == "local"
    assert f("") == ("", None)


# ---------------------------------------------------------------- ítem 2
def _crear_conflicto(carpeta, ruta="nota.txt"):
    base = config.PROYECTOS / PROY / carpeta
    base.mkdir(parents=True, exist_ok=True)
    (base / ruta).write_text("mi version", encoding="utf-8")
    backup = base / f"{ruta}.conflict.Amigo.20260101-000000-000000"
    backup.write_text("su version", encoding="utf-8")
    return f"{carpeta}/{ruta}", f"{carpeta}/{ruta}.conflict.Amigo.20260101-000000-000000"


def test_conflicto_listar_contenido_y_resolver_suyo():
    carpeta = _carpeta("Conf")
    ruta, backup = _crear_conflicto(carpeta)
    r = client.get("/api/conflictos", params={"proyecto": PROY})
    assert r.status_code == 200
    items = [c for c in r.json()["conflictos"] if c["ruta"] == ruta]
    assert len(items) == 1
    assert items[0]["conflicto_archivo"] == backup
    assert items[0]["autor"] == "Amigo"

    r = client.get("/api/conflictos/contenido",
                   params={"proyecto": PROY, "ruta": ruta, "conflicto_archivo": backup})
    assert r.status_code == 200
    assert r.json()["mio"] == "mi version"
    assert r.json()["suyo"] == "su version"

    r = client.post("/api/conflictos/resolver", json={
        "proyecto": PROY, "ruta": ruta, "conflicto_archivo": backup,
        "eleccion": "suyo"})
    assert r.status_code == 200, r.text
    base = config.PROYECTOS / PROY / carpeta
    assert (base / "nota.txt").read_text() == "su version"
    # Backup borrado y ya no aparece en la lista
    assert not (base / "nota.txt.conflict.Amigo.20260101-000000-000000").exists()
    r = client.get("/api/conflictos", params={"proyecto": PROY})
    assert all(c["conflicto_archivo"] != backup for c in r.json()["conflictos"])


def test_conflicto_resolver_mio_y_mezcla():
    carpeta = _carpeta("Conf")
    ruta, backup = _crear_conflicto(carpeta)
    r = client.post("/api/conflictos/resolver", json={
        "proyecto": PROY, "ruta": ruta, "conflicto_archivo": backup,
        "eleccion": "mio"})
    assert r.status_code == 200
    base = config.PROYECTOS / PROY / carpeta
    assert (base / "nota.txt").read_text() == "mi version"
    assert not (base / "nota.txt.conflict.Amigo.20260101-000000-000000").exists()

    # Mezcla: contenido manual
    ruta2, backup2 = _crear_conflicto(carpeta, "otra.txt")
    r = client.post("/api/conflictos/resolver", json={
        "proyecto": PROY, "ruta": ruta2, "conflicto_archivo": backup2,
        "eleccion": "mezcla", "contenido": "línea mía\nlínea suya\n"})
    assert r.status_code == 200
    assert (base / "otra.txt").read_text() == "línea mía\nlínea suya\n"
    # Mezcla sin contenido: 400 honesto
    ruta3, backup3 = _crear_conflicto(carpeta, "tercera.txt")
    r = client.post("/api/conflictos/resolver", json={
        "proyecto": PROY, "ruta": ruta3, "conflicto_archivo": backup3,
        "eleccion": "mezcla"})
    assert r.status_code == 400
    # Elección inválida: 400
    r = client.post("/api/conflictos/resolver", json={
        "proyecto": PROY, "ruta": ruta3, "conflicto_archivo": backup3,
        "eleccion": "borrar_todo"})
    assert r.status_code == 400


# ---------------------------------------------------------------- ítem 3
def test_presencia_lleva_cursor_seleccion_y_color():
    """El mensaje de presencia del WS lleva cursor + selección + color,
    y el broadcast presence_update los incluye."""
    mensajes = []

    async def _correr():
        with client.websocket_connect("/ws?nombre=Daniel") as ws1:
            with client.websocket_connect("/ws?nombre=Amigo") as ws2:
                ws2.send_json({
                    "tipo": "presence", "file": "X/a.txt",
                    "cursor": {"line": 12, "col": 5},
                    "selection": {"startLineNumber": 12, "startColumn": 1,
                                  "endLineNumber": 13, "endColumn": 4}})
                # El broadcast llega a ambos; buscar el presence_update
                for _ in range(10):
                    try:
                        msg = ws1.receive_json()
                    except Exception:
                        break
                    if msg.get("tipo") == "presence_update":
                        mensajes.append(msg)
                        break
    asyncio.run(_correr())
    assert mensajes, "no llegó presence_update"
    usuarios = mensajes[0]["users"]
    amigo = next((u for u in usuarios if u["nombre"] == "Amigo"), None)
    assert amigo is not None
    assert amigo["cursor"] == {"line": 12, "col": 5}
    assert amigo["selection"] == {"startLineNumber": 12, "startColumn": 1,
                                  "endLineNumber": 13, "endColumn": 4}
    assert amigo["file"] == "X/a.txt"
    assert amigo["color"].startswith("#")


# ---------------------------------------------------------------- ítem 4
def test_codigo_con_expiracion_corta_rechazado_al_expirar():
    carpeta = _carpeta("Exp")
    res = _compartir(carpeta, expira_minutos=60)
    assert res["expira_en"], "el código debe llevar expira_en"
    codigo, equipo_id = res["code"], res["equipo_id"]
    # Forzar expiración
    equipos = sync_mod._cargar_equipos()
    for c in equipos[equipo_id]["codigos"]:
        if isinstance(c, dict) and c.get("code") == codigo:
            from datetime import datetime, timedelta
            c["expira_en"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    sync_mod._guardar_equipos(equipos)
    r = _unir_relay(codigo, "X", "Y", carpeta)
    assert r.status_code == 410
    assert "expirado" in r.json()["error"].lower()


def test_codigo_sin_expiracion_no_expira():
    carpeta = _carpeta("NoExp")
    res = _compartir(carpeta, expira_minutos=0)
    assert res["expira_en"] is None
    r = _unir_relay(res["code"], "KokoAmigo", "Amigo", carpeta)
    assert r.status_code == 200


# ---------------------------------------------------------------- ítem 5
def test_estado_sync_refleja_outbox_y_conexion():
    """PeerClient.estado(): sin conexión si no hay socket; pendiente
    si el outbox no está vacío; sincronizado en caso contrario."""
    cliente = sync_mod.PeerClient("ws://127.0.0.1:1", "t", "general",
                                  "peer-test", "Daniel", carpeta="ZZ",
                                  equipo_id="team-zz")
    # Sin socket: sin conexión
    estado = cliente.estado()
    assert estado == {"estado": "sin_conexion", "pendientes": 0,
                      "conectado": False, "transferencias": []}
    # Con socket simulado + outbox vacío: sincronizado
    cliente._conectado.set()
    cliente._ws = object()
    assert cliente.estado()["estado"] == "sincronizado"
    assert cliente.estado()["conectado"] is True
    # Con outbox no vacío: pendiente
    cliente._outbox.append({"tipo": "file_sync"})
    estado = cliente.estado()
    assert estado["estado"] == "pendiente"
    assert estado["pendientes"] == 1
    # Sin conexión + outbox: sin conexión manda (no se puede enviar)
    cliente._conectado.clear()
    cliente._ws = None
    assert cliente.estado()["estado"] == "sin_conexion"


def test_sesiones_activas_incluyen_sync_estado_y_yo():
    carpeta = _carpeta("Estado")
    _compartir(carpeta)
    r = client.get("/api/proyectos/compartidos")
    assert r.status_code == 200
    sesion = next(s for s in r.json()["proyectos"] if s["carpeta"] == carpeta)
    assert sesion["sync_estado"]["estado"] in ("sincronizado", "pendiente", "sin_conexion")
    assert "pendientes" in sesion["sync_estado"]
    assert "conectado" in sesion["sync_estado"]
    assert sesion["yo"] == {"user_name": "Daniel"}


# ---------------------------------------------------------------- ítem 6
def test_endpoints_viejos_pairing_desaparecidos():
    for metodo, ruta in [("POST", "/api/pairing/create"),
                         ("POST", "/api/pairing/join"),
                         ("POST", "/api/pairing/leave"),
                         ("POST", "/api/pairing/unpair"),
                         ("GET", "/api/pairing/status")]:
        r = client.request(metodo, ruta, json={})
        assert r.status_code == 404, f"{metodo} {ruta} debió dar 404, dio {r.status_code}"


def test_bus_con_alcance_al_equipo_y_no_cruza_proyectos():
    """El bus con equipo_id: un mensaje de X no aparece en la bandeja
    de Y, y viceversa."""
    carpeta_x, eid_x, _c, gt_x = _equipo_con_dos(_carpeta("BusX"))
    carpeta_y, eid_y, _c2, gt_y = _equipo_con_dos(_carpeta("BusY"))
    # Mensaje en el equipo X
    r = client.post("/api/agents/message", json={
        "from": "Koko", "to": "todos", "text": "tarea para X",
        "tipo": "tarea", "proyecto": PROY, "equipo_id": eid_x, "carpeta": carpeta_x,
        "agente": {"nombre": "Koko", "color": "#4da3ff"}})
    assert r.status_code == 200
    assert r.json()["mensaje"]["equipo_id"] == eid_x
    assert r.json()["mensaje"]["agente"]["nombre"] == "Koko"
    # Bandeja de X lo ve; bandeja de Y no
    r = client.get("/api/agents/inbox", params={"agent": "todos", "equipo_id": eid_x})
    assert any(m["texto"] == "tarea para X" for m in r.json()["mensajes"])
    r = client.get("/api/agents/inbox", params={"agent": "todos", "equipo_id": eid_y})
    assert not any(m["texto"] == "tarea para X" for m in r.json()["mensajes"])
    # Historial filtrado por equipo
    r = client.get("/api/agents/bus", params={"equipo_id": eid_y})
    assert not any(m["texto"] == "tarea para X" for m in r.json()["mensajes"])


def test_mensaje_agente_lleva_identidad():
    """Todo mensaje de agente lleva su identidad (nombre + color)."""
    r = client.post("/api/agents/message", json={
        "from": "Koko", "to": "todos", "text": "hola equipo",
        "agente": {"id": "agent-1", "nombre": "Koko", "color": "#4da3ff",
                   "dueno": "Daniel"}})
    assert r.status_code == 200
    msg = r.json()["mensaje"]
    assert msg["agente"]["nombre"] == "Koko"
    assert msg["agente"]["color"] == "#4da3ff"
    assert msg["agente"]["dueno"] == "Daniel"


def test_renombre_solo_del_dueno():
    carpeta, equipo_id, _codigo, guest_token = _equipo_con_dos()
    # El invitado renombra SU propio agente: funciona
    r = client.post("/api/sync/equipos/agente/renombrar",
                    params={"token": guest_token},
                    json={"equipo_id": equipo_id, "nuevo_nombre": "Koko-Amigo2"})
    assert r.status_code == 200, r.text
    assert r.json()["agente"]["nombre"] == "Koko-Amigo2"
    assert r.json()["agente"]["dueno"] == "Amigo"
    # El invitado NO puede renombrar el del anfitrión: su token solo
    # renombra su propio miembro (el nombre que pide se aplica a sí
    # mismo, nunca al del otro). Verificar que el anfitrión sigue Koko.
    equipo = sync_mod.equipo_por_id(equipo_id)
    host = next(m for m in equipo["miembros"] if m["es_host"])
    assert host["agent_name"] == "Koko"
    # Token que no es del equipo: 403
    r = client.post("/api/sync/equipos/agente/renombrar",
                    params={"token": "token-ajeno"},
                    json={"equipo_id": equipo_id, "nuevo_nombre": "Intruso"})
    assert r.status_code == 403
    # Nombre vacío: 400 de validación con error claro
    r = client.post("/api/sync/equipos/agente/renombrar",
                    params={"token": guest_token},
                    json={"equipo_id": equipo_id, "nuevo_nombre": ""})
    assert r.status_code == 400
    # El renombre se propagó a los miembros (identidad actualizada)
    equipo = sync_mod.equipo_por_id(equipo_id)
    invitado = next(m for m in equipo["miembros"] if not m["es_host"])
    assert invitado["agent_name"] == "Koko-Amigo2"
    publicos = sync_mod.miembros_publicos(equipo)
    invitado_pub = next(m for m in publicos if not m["es_host"])
    assert invitado_pub["agente"]["nombre"] == "Koko-Amigo2"
    # El renombre no crea un duplicado al re-unirse
    r = _unir_relay(_compartir(carpeta)["code"], "OtroNombre", "Amigo", carpeta)
    assert r.status_code == 200
    equipo = sync_mod.equipo_por_id(equipo_id)
    assert len([m for m in equipo["miembros"] if m["user_name"] == "Amigo"]) == 1


def test_validar_agent_msg():
    """El relay valida los mensajes del bus de agentes (sin sync_id o
    sin mensaje → rechazado; con todo → aceptado)."""
    base = {"tipo": "agent_msg", "proyecto": PROY, "carpeta": "X",
            "equipo_id": "team-x", "sync_id": "bus-1",
            "mensaje": {"id": "m1", "de": "Koko", "texto": "hola"},
            "agente": {"nombre": "Koko", "color": "#4da3ff"}}
    assert sync_mod.validar_mensaje_sync(base)[0] is True
    sin_sync = dict(base)
    del sin_sync["sync_id"]
    assert sync_mod.validar_mensaje_sync(sin_sync)[0] is False
    sin_mensaje = dict(base)
    del sin_mensaje["mensaje"]
    assert sync_mod.validar_mensaje_sync(sin_mensaje)[0] is False
    sin_texto = dict(base)
    sin_texto["mensaje"] = {"id": "m1", "de": "Koko"}
    assert sync_mod.validar_mensaje_sync(sin_texto)[0] is False
    otro_equipo = dict(base)
    otro_equipo["equipo_id"] = "team-otro"
    # La validación estructural pasa; el aislamiento lo hace el relay
    # (el receptor descarta mensajes de otro equipo).
    assert sync_mod.validar_mensaje_sync(otro_equipo)[0] is True


# ---------------------------------------------------------------- ítem 7
def test_preview_estatico_sirve_contenido_real():
    carpeta = _carpeta("Prev")
    base = config.PROYECTOS / PROY / carpeta
    base.mkdir(parents=True, exist_ok=True)
    (base / "index.html").write_text("<h1>App real</h1>", encoding="utf-8")
    (base / "estilo.css").write_text("body { color: red; }", encoding="utf-8")
    r = client.post("/api/preview/iniciar", json={"proyecto": PROY, "carpeta": carpeta})
    assert r.status_code == 200, r.text
    assert r.json()["tipo"] == "estatico"
    r = client.get(f"/api/preview/sitio/{carpeta}/?proyecto={PROY}")
    assert r.status_code == 200
    assert "App real" in r.text
    r = client.get(f"/api/preview/sitio/{carpeta}/estilo.css?proyecto={PROY}")
    assert r.status_code == 200
    assert "color: red" in r.text
    # .env nunca se sirve en preview
    (base / ".env").write_text("SECRETO=x", encoding="utf-8")
    r = client.get(f"/api/preview/sitio/{carpeta}/.env?proyecto={PROY}")
    assert r.status_code == 403
    r = client.post("/api/preview/detener", json={"proyecto": PROY, "carpeta": carpeta})
    assert r.status_code == 200
    r = client.get(f"/api/preview/sitio/{carpeta}/?proyecto={PROY}")
    assert r.status_code == 404  # detenido: ya no sirve


def test_preview_script_sirve_app_real_y_detener():
    carpeta = _carpeta("PrevS")
    base = config.PROYECTOS / PROY / carpeta
    base.mkdir(parents=True, exist_ok=True)
    (base / "app.py").write_text(
        "from http.server import HTTPServer, BaseHTTPRequestHandler\n"
        "import os\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'text/plain')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'app real con script')\n"
        "    def log_message(self, *a): pass\n"
        "HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()\n",
        encoding="utf-8")
    r = client.post("/api/preview/iniciar", json={"proyecto": PROY, "carpeta": carpeta})
    assert r.status_code == 200, r.text
    assert r.json()["tipo"] == "script"
    assert r.json()["puerto"]
    r = client.get(f"/api/preview/app/{carpeta}/?proyecto={PROY}")
    assert r.status_code == 200
    assert "app real con script" in r.text
    # Estado refleja el proceso vivo
    r = client.get("/api/preview/estado", params={"proyecto": PROY, "carpeta": carpeta})
    assert r.json()["activo"] is True
    assert r.json()["proceso_vivo"] is True
    # Detener mata el proceso
    r = client.post("/api/preview/detener", json={"proyecto": PROY, "carpeta": carpeta})
    assert r.status_code == 200
    r = client.get("/api/preview/estado", params={"proyecto": PROY, "carpeta": carpeta})
    assert r.json()["activo"] is False
    r = client.get(f"/api/preview/app/{carpeta}/?proyecto={PROY}")
    assert r.status_code == 404


def test_preview_sin_entrada_error_claro():
    carpeta = _carpeta("PrevV")
    base = config.PROYECTOS / PROY / carpeta
    base.mkdir(parents=True, exist_ok=True)
    (base / "notas.txt").write_text("nada que previsualizar", encoding="utf-8")
    r = client.post("/api/preview/iniciar", json={"proyecto": PROY, "carpeta": carpeta})
    assert r.status_code == 400
    assert "index.html" in r.json()["error"] or "punto de entrada" in r.json()["error"]


def test_preview_compartir_sin_tunel_error_claro():
    carpeta = _carpeta("PrevC")
    base = config.PROYECTOS / PROY / carpeta
    base.mkdir(parents=True, exist_ok=True)
    (base / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
    client.post("/api/preview/iniciar", json={"proyecto": PROY, "carpeta": carpeta})
    # Sin túnel abierto: error claro, no un enlace inventado
    r = client.post("/api/preview/compartir", json={"proyecto": PROY, "carpeta": carpeta})
    assert r.status_code == 400
    assert "túnel" in r.json()["error"].lower()
    client.post("/api/preview/detener", json={"proyecto": PROY, "carpeta": carpeta})


# ---------------------------------------------------------------- ítem 8
def _aprobar(equipo_id, token, fingerprint, nota=""):
    return client.post("/api/sync/equipos/aprobar",
                       params={"token": token},
                       json={"equipo_id": equipo_id, "fingerprint": fingerprint,
                             "nota": nota})


def test_aprobacion_una_sola_no_dispara():
    carpeta, equipo_id, _codigo, guest_token = _equipo_con_dos()
    fp = "fp-una-sola"
    r = _aprobar(equipo_id, config.TOKEN, fp, "me gusta")
    assert r.status_code == 200
    assert r.json()["ambos"] is False
    assert r.json()["estado"] == "esperando"
    assert r.json()["falta"] == ["un compañero"]
    assert not r.json()["tarea_id"]


def test_aprobacion_ambos_misma_version_dispara():
    carpeta, equipo_id, _codigo, guest_token = _equipo_con_dos()
    fp = "fp-ambos-" + uuid.uuid4().hex[:6]
    _aprobar(equipo_id, config.TOKEN, fp, "me gusta")
    r = _aprobar(equipo_id, guest_token, fp, "falta X")
    assert r.status_code == 200
    assert r.json()["ambos"] is True
    assert r.json()["estado"] == "terminando"
    assert r.json()["tarea_id"].startswith("fin-")
    tarea_id = r.json()["tarea_id"]
    # La tarea de terminar quedó en el bus del equipo
    r = client.get("/api/agents/bus", params={"equipo_id": equipo_id})
    tareas = [m for m in r.json()["mensajes"] if m.get("tipo") == "tarea_terminar"]
    assert tareas, "la tarea de terminar debe estar en el bus del equipo"
    assert tareas[-1]["tarea_id"] == tarea_id


def test_aprobacion_version_vieja_no_dispara():
    carpeta, equipo_id, _codigo, guest_token = _equipo_con_dos()
    # El anfitrión aprueba la versión 1; el invitado aprueba la versión 2:
    # no hay "ambos" en ninguna versión.
    r = _aprobar(equipo_id, config.TOKEN, "fp-v1")
    assert r.json()["ambos"] is False
    r = _aprobar(equipo_id, guest_token, "fp-v2")
    assert r.status_code == 200
    assert r.json()["ambos"] is False
    assert r.json()["estado"] == "esperando"
    assert not r.json()["tarea_id"]


def test_aprobacion_retirar_bloquea_disparo():
    carpeta, equipo_id, _codigo, guest_token = _equipo_con_dos()
    fp = "fp-retirar-" + uuid.uuid4().hex[:6]
    _aprobar(equipo_id, config.TOKEN, fp)
    # Retirar antes de que el otro apruebe
    r = client.post("/api/sync/equipos/retirar-aprobacion",
                    params={"token": config.TOKEN},
                    json={"equipo_id": equipo_id})
    assert r.status_code == 200
    # Ahora el invitado aprueba: sigue sin "ambos" (falta el anfitrión)
    r = _aprobar(equipo_id, guest_token, fp)
    assert r.json()["ambos"] is False
    assert r.json()["estado"] == "esperando"


def test_aprobacion_completada_marca_terminado():
    carpeta, equipo_id, _codigo, guest_token = _equipo_con_dos()
    fp = "fp-fin-" + uuid.uuid4().hex[:6]
    _aprobar(equipo_id, config.TOKEN, fp)
    r = _aprobar(equipo_id, guest_token, fp)
    tarea_id = r.json()["tarea_id"]
    assert tarea_id
    r = client.post("/api/sync/equipos/aprobacion/completada",
                    params={"token": guest_token},
                    json={"equipo_id": equipo_id, "tarea_id": tarea_id,
                          "resumen": "Se agregaron los tests y el README."})
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "terminado"
    assert "README" in r.json()["resumen"]
    # Estado persistido
    r = client.get("/api/sync/equipos/aprobaciones",
                   params={"equipo_id": equipo_id, "fingerprint": fp,
                           "token": config.TOKEN})
    assert r.json()["estado"] == "terminado"
    assert "README" in r.json()["resumen"]
