"""Tests de transferencia completa de archivos grandes (file_big).

Un archivo grande/binario viaja COMPLETO en un solo mensaje por el relay
(bytes en base64 + sha256 del archivo completo). El receptor verifica el
sha256 ANTES de escribir: si no coincide, descarta y reporta error real.
Sin chunks, sin resume por pedazos: si se interrumpe, se reenvía completo.
"""
import asyncio
import base64
import hashlib
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# Aislamiento: HOME y workspace temporales (igual que test_sync_archivos).
TMP = tempfile.mkdtemp(prefix="atlantis-test-big-")
os.environ["HOME"] = TMP
os.environ["NEXO_WORKSPACE"] = str(__import__("pathlib").Path(TMP) / "ws")
os.environ["NEXO_PUBLIC"] = "1"
os.environ["ATLANTIS_RELAY_DEFAULT"] = "ws://127.0.0.1:19999/nada"

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "nexo"))

# Misma convención que test_sync_archivos.py: usar el árbol `backend.*`
# (vía nexo/), no `nexo.backend.*`, para compartir los singletons del
# proceso (store, sesiones, config) con el resto del backend.
from backend import sync as sync_mod  # noqa: E402
from backend.sync import PeerClient, aplicar_file_big  # noqa: E402
from backend.sync import validar_mensaje_sync, _construir_file_big  # noqa: E402
from backend.sync import notificar_cambio_local, MAX_FILE_BIG  # noqa: E402
from backend.core import state as state_mod  # noqa: E402
from backend.core.config import config  # noqa: E402
# OJO: state.py usa `backend.store`: inicializar ESE singleton (no el de
# la ruta `nexo.backend.store`, que es otro objeto).
from backend.store import init_store  # noqa: E402
from backend.db import init_db  # noqa: E402

init_db()
init_store()

PROY = "general"
EQ = "team-big"

import pytest  # noqa: E402
import httpx  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# --- Utilidades para tests LIVE (procesos reales, como test_sync_archivos) ---

def _arrancar(puerto, home, ws_dir):
    env = dict(os.environ)
    env.update({"HOME": str(home), "NEXO_WORKSPACE": str(ws_dir),
                "NEXO_PUBLIC": "1", "PORT": str(puerto)})
    env.pop("SYNC_HUB_URL", None)
    proc = subprocess.Popen(
        [sys.executable, "-u", "nexo/server.py"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/status", timeout=2)
            return proc
        except Exception:
            time.sleep(0.5)
    _parar(proc)
    raise AssertionError(f"el servidor no arrancó en el puerto {puerto}")


def _parar(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def _post(puerto, path, payload=None):
    return httpx.post(f"http://127.0.0.1:{puerto}{path}", json=payload or {},
                      timeout=30, trust_env=False)


def _esperar(cond, timeout=60, mensaje="condición no cumplida"):
    fin = time.time() + timeout
    ultimo = None
    while time.time() < fin:
        try:
            if cond():
                return True
        except Exception as e:
            ultimo = e
        time.sleep(0.5)
    raise AssertionError(f"{mensaje} (último error: {ultimo})")


def _compartir(puerto, carpeta, hub_puerto, nombre="Daniel"):
    r = _post(puerto, f"/api/proyectos/{carpeta}/compartir",
              {"hub_url": f"ws://127.0.0.1:{hub_puerto}", "user_name": nombre,
               "agent_name": f"Hermes-{nombre}"})
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["ok"] is True, datos
    return datos["equipo_id"], datos["code"]


def _unirse(puerto, codigo, carpeta, hub_puerto, nombre="Amigo"):
    r = _post(puerto, "/api/proyectos/unirse",
              {"code": codigo, "carpeta": carpeta,
               "hub_url": f"ws://127.0.0.1:{hub_puerto}", "user_name": nombre,
               "agent_name": f"Hermes-{nombre}"})
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["ok"] is True, datos
    return datos["equipo_id"]


def _bytes(n: int) -> bytes:
    return os.urandom(n)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _escribir(ruta: str, datos: bytes) -> None:
    p = config.PROYECTOS / PROY / ruta
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(datos)


def _msg_big(ruta: str, datos: bytes, equipo_id: str = EQ, version: int = 1,
             base_version: int = 0, autor: str = "Daniel") -> dict:
    msg = _construir_file_big(PROY, "big", equipo_id, ruta, base_version,
                              version, autor, datos)
    assert msg is not None
    return msg


def test_construir_file_big_ok():
    datos = _bytes(2 * 1024 * 1024)
    msg = _construir_file_big(PROY, "big", EQ, "big/a.bin", 0, 1, "Daniel", datos)
    assert msg is not None
    assert msg["tipo"] == "file_big"
    assert msg["total_bytes"] == len(datos)
    assert msg["file_sha256"] == _sha(datos)
    assert base64.b64decode(msg["datos_b64"]) == datos
    assert msg["transfer_id"] and msg["sync_id"]
    ok, motivo = validar_mensaje_sync(msg)
    assert ok, motivo


def test_construir_file_big_sobre_cap_retorna_none():
    assert _construir_file_big(PROY, "big", EQ, "big/x.bin", 0, 1, "D",
                               b"z" * (MAX_FILE_BIG + 1)) is None


def test_validar_file_big_rechaza_malformados():
    datos = _bytes(1024)
    base = _msg_big("big/ok.bin", datos)
    for mutar, campo in [
        (lambda m: m.update(ruta="../evil.bin"), "ruta"),
        (lambda m: m.update(carpeta=""), "carpeta"),
        (lambda m: m.update(total_bytes=MAX_FILE_BIG + 1), "cap"),
        (lambda m: m.update(file_sha256="xyz"), "sha"),
        (lambda m: m.update(datos_b64=""), "datos"),
        (lambda m: m.update(operacion="delete"), "op"),
        (lambda m: m.update(datos_b64="A" * (4 * ((len(datos) + 2) // 3) + 1000)), "b64"),
    ]:
        m = dict(base)
        mutar(m)
        ok, _ = validar_mensaje_sync(m)
        assert not ok, f"debió rechazar: {campo}"


def test_aplicar_file_big_roundtrip_binario_5mb():
    datos = _bytes(5 * 1024 * 1024)
    ruta = "big/roundtrip.bin"
    res = asyncio.run(aplicar_file_big(PROY, _msg_big(ruta, datos)))
    assert res["aplicado"] is True, res
    p = config.PROYECTOS / PROY / ruta
    assert p.read_bytes() == datos
    assert state_mod._version_archivo(PROY, ruta) == 1


def test_aplicar_file_big_hash_mismatch_no_escribe():
    datos = _bytes(1024 * 1024)
    ruta = "big/corrupto.bin"
    msg = _msg_big(ruta, datos)
    # Corromper un byte manteniendo el sha anunciado (el del original).
    crudos = bytearray(base64.b64decode(msg["datos_b64"]))
    crudos[100] ^= 0xFF
    msg["datos_b64"] = base64.b64encode(bytes(crudos)).decode("ascii")
    res = asyncio.run(aplicar_file_big(PROY, msg))
    assert res["aplicado"] is False
    assert res["motivo"] == "hash_invalido", res
    assert not (config.PROYECTOS / PROY / ruta).exists()


def test_aplicar_file_big_conflicto_guarda_backup_sin_perder_local():
    datos_nuevos = _bytes(512 * 1024)
    ruta = "big/conflicto.bin"
    _escribir(ruta, b"contenido local mio")
    state_mod._fijar_version_archivo(PROY, ruta, 1, "Daniel")
    # El entrante viene de una base vieja (divergió): conflicto.
    msg = _msg_big(ruta, datos_nuevos, base_version=0, version=2, autor="Amigo")
    res = asyncio.run(aplicar_file_big(PROY, msg))
    assert res["aplicado"] is False and res["conflicto"] is True, res
    # Local intacto + backup con los bytes entrantes (verificados).
    assert (config.PROYECTOS / PROY / ruta).read_bytes() == b"contenido local mio"
    backup = res["conflicto_archivo"]
    assert backup
    assert (config.PROYECTOS / PROY / backup).read_bytes() == datos_nuevos


def test_aplicar_file_big_duplicado_no_reescribe():
    datos = _bytes(64 * 1024)
    ruta = "big/duplicado.bin"
    msg = _msg_big(ruta, datos)
    r1 = asyncio.run(aplicar_file_big(PROY, msg))
    assert r1["aplicado"] is True
    r2 = asyncio.run(aplicar_file_big(PROY, msg))
    assert r2["aplicado"] is False and r2["motivo"] == "duplicado", r2


def _registrar_sesion() -> PeerClient:
    cliente = PeerClient("ws://127.0.0.1:1", "t", PROY, "peer-a", "A",
                         carpeta="big", equipo_id=EQ)
    sync_mod._SESIONES[(PROY, "big")] = cliente
    return cliente


def test_notificar_cambio_local_binario_encola_file_big_en_vivo():
    datos = _bytes(2 * 1024 * 1024)
    ruta = "big/vivo.bin"
    _escribir(ruta, datos)
    state_mod._bump_version_archivo(PROY, ruta, "Daniel")
    cliente = _registrar_sesion()
    vistos = []
    orig = sync_mod.enviar_todos
    sync_mod.enviar_todos = lambda msg: vistos.append(msg) or asyncio.sleep(0)  # noqa: E731
    try:
        msg = asyncio.run(notificar_cambio_local(
            PROY, ruta, 0, 1, "Daniel", contenido=None,
            operacion="upsert", binario=True))
    finally:
        sync_mod.enviar_todos = orig
        sync_mod._SESIONES.pop((PROY, "big"), None)
    assert msg is not None and msg["tipo"] == "file_big", msg
    assert len(cliente._outbox) == 1
    assert base64.b64decode(msg["datos_b64"]) == datos
    # Progreso en vivo registrado y anunciado.
    tid = msg["transfer_id"]
    assert cliente._transferencias[tid]["estado"] == "en_cola"
    assert any(m.get("tipo") == "sync_big_progreso" and m.get("estado") == "en_cola"
               for m in vistos)


def test_notificar_cambio_local_texto_grande_va_por_file_big():
    contenido = "línea de texto\n" * 90000  # > 1 MB
    assert len(contenido.encode("utf-8")) > 1024 * 1024
    ruta = "big/grande.txt"
    _escribir(ruta, contenido.encode("utf-8"))
    state_mod._bump_version_archivo(PROY, ruta, "Daniel")
    cliente = _registrar_sesion()
    try:
        msg = asyncio.run(notificar_cambio_local(
            PROY, ruta, 0, 1, "Daniel", contenido=contenido, operacion="upsert"))
    finally:
        sync_mod._SESIONES.pop((PROY, "big"), None)
    assert msg is not None and msg["tipo"] == "file_big", msg
    assert base64.b64decode(msg["datos_b64"]) == contenido.encode("utf-8")
    assert len(cliente._outbox) == 1


def test_notificar_sobre_cap_no_envia_nada_a_medias():
    datos = _bytes(MAX_FILE_BIG + 1024)
    ruta = "big/enorme.bin"
    _escribir(ruta, datos)
    state_mod._bump_version_archivo(PROY, ruta, "Daniel")
    cliente = _registrar_sesion()
    vistos = []
    orig = sync_mod.enviar_todos
    sync_mod.enviar_todos = lambda msg: vistos.append(msg) or asyncio.sleep(0)  # noqa: E731
    try:
        msg = asyncio.run(notificar_cambio_local(
            PROY, ruta, 0, 1, "Daniel", contenido=None,
            operacion="upsert", binario=True))
    finally:
        sync_mod.enviar_todos = orig
        sync_mod._SESIONES.pop((PROY, "big"), None)
    assert msg is None
    assert len(cliente._outbox) == 0
    assert any(m.get("tipo") == "sync_unsupported" and m.get("motivo") == "demasiado_grande"
               for m in vistos)


def test_encolar_coalesce_reemplaza_file_big_viejo():
    cliente = _registrar_sesion()
    try:
        viejo = _msg_big("big/misma.bin", b"v1" * 1000)
        nuevo = _msg_big("big/misma.bin", b"v2" * 1000)
        cliente.encolar(viejo)
        cliente.encolar(nuevo)
        assert len(cliente._outbox) == 1
        assert cliente._outbox[0]["transfer_id"] == nuevo["transfer_id"]
    finally:
        sync_mod._SESIONES.pop((PROY, "big"), None)


def test_despachar_file_big_aplica_y_reporta_progreso():
    datos = _bytes(3 * 1024 * 1024)
    ruta = "big/bajada.bin"
    msg = _msg_big(ruta, datos)
    cliente = PeerClient("ws://127.0.0.1:1", "t", PROY, "peer-b", "B",
                         carpeta="big", equipo_id=EQ)
    vistos = []
    orig = sync_mod.enviar_todos
    sync_mod.enviar_todos = lambda m: vistos.append(m) or asyncio.sleep(0)  # noqa: E731
    try:
        asyncio.run(cliente._despachar(msg))
    finally:
        sync_mod.enviar_todos = orig
    assert (config.PROYECTOS / PROY / ruta).read_bytes() == datos
    tid = msg["transfer_id"]
    assert cliente._transferencias[tid]["estado"] == "escrito"
    assert any(m.get("tipo") == "sync_big_progreso" and m.get("estado") == "escrito"
               for m in vistos)


def test_despachar_file_big_equipo_ajeno_ignorado():
    datos = _bytes(64 * 1024)
    ruta = "big/ajeno.bin"
    msg = _msg_big(ruta, datos, equipo_id="team-otro")
    cliente = PeerClient("ws://127.0.0.1:1", "t", PROY, "peer-b", "B",
                         carpeta="big", equipo_id=EQ)
    asyncio.run(cliente._despachar(msg))
    assert not (config.PROYECTOS / PROY / ruta).exists()
    assert msg["transfer_id"] not in cliente._transferencias


def test_despachar_file_big_hash_mismatch_reporta_error_real():
    datos = _bytes(256 * 1024)
    ruta = "big/mala.bin"
    msg = _msg_big(ruta, datos)
    crudos = bytearray(base64.b64decode(msg["datos_b64"]))
    crudos[10] ^= 0xFF
    msg["datos_b64"] = base64.b64encode(bytes(crudos)).decode("ascii")
    cliente = PeerClient("ws://127.0.0.1:1", "t", PROY, "peer-b", "B",
                         carpeta="big", equipo_id=EQ)
    vistos = []
    orig = sync_mod.enviar_todos
    sync_mod.enviar_todos = lambda m: vistos.append(m) or asyncio.sleep(0)  # noqa: E731
    try:
        asyncio.run(cliente._despachar(msg))
    finally:
        sync_mod.enviar_todos = orig
    assert not (config.PROYECTOS / PROY / ruta).exists()
    errores = [m for m in vistos
               if m.get("tipo") == "sync_big_progreso" and m.get("estado") == "error"]
    assert errores and errores[0]["motivo"] == "hash_invalido"


def test_file_big_reenvio_completo_tras_interrupcion():
    # La conexión cae antes de enviar: el mensaje queda en cola; el watcher
    # vuelve a notificar y la cola coalesce a UN solo mensaje completo; al
    # reconectar llega íntegro (byte-idéntico por sha256).
    datos = _bytes(2 * 1024 * 1024)
    ruta = "big/reenvio.bin"
    _escribir(ruta, datos)
    state_mod._bump_version_archivo(PROY, ruta, "Daniel")
    cliente = _registrar_sesion()
    orig = sync_mod.enviar_todos
    sync_mod.enviar_todos = lambda m: asyncio.sleep(0)  # noqa: E731
    try:
        m1 = asyncio.run(notificar_cambio_local(
            PROY, ruta, 0, 1, "Daniel", contenido=None,
            operacion="upsert", binario=True))
        assert m1 and m1["tipo"] == "file_big"
        assert len(cliente._outbox) == 1
        # Interrupción: _ws es None, el mensaje nunca salió (sigue en cola).
        assert cliente._ws is None
        # El watcher detecta de nuevo y re-notifica: UN solo mensaje completo.
        m2 = asyncio.run(notificar_cambio_local(
            PROY, ruta, 0, 1, "Daniel", contenido=None,
            operacion="upsert", binario=True))
        assert len(cliente._outbox) == 1
        final = cliente._outbox[0]
        assert final["tipo"] == "file_big"
        assert base64.b64decode(final["datos_b64"]) == datos
        assert _sha(base64.b64decode(final["datos_b64"])) == final["file_sha256"]
    finally:
        sync_mod.enviar_todos = orig
        sync_mod._SESIONES.pop((PROY, "big"), None)


def test_manejar_sync_fetch_binario_responde_file_big():
    datos = _bytes(2 * 1024 * 1024)
    ruta = "big/fetch.bin"
    _escribir(ruta, datos)
    state_mod._bump_version_archivo(PROY, ruta, "Daniel")
    cliente = PeerClient("ws://127.0.0.1:1", "t", PROY, "peer-a", "A",
                         carpeta="big", equipo_id=EQ)
    enviados = []
    cliente._enviar = lambda msg: enviados.append(msg) or asyncio.sleep(0)
    asyncio.run(cliente._manejar_sync_fetch({
        "tipo": "sync_fetch", "proyecto": PROY, "carpeta": "big",
        "equipo_id": EQ, "ruta": ruta, "version": 0, "hash": None,
        "fetch_id": "f-1", "peer_id": "peer-b",
    }))
    bigs = [m for m in enviados if m.get("tipo") == "file_big"]
    assert len(bigs) == 1, enviados
    assert base64.b64decode(bigs[0]["datos_b64"]) == datos
    assert bigs[0]["fetch_id"] == "f-1"


def test_manejar_sync_fetch_sobre_cap_responde_bandera():
    datos = _bytes(MAX_FILE_BIG + 1024)
    ruta = "big/fetch_enorme.bin"
    _escribir(ruta, datos)
    state_mod._bump_version_archivo(PROY, ruta, "Daniel")
    cliente = PeerClient("ws://127.0.0.1:1", "t", PROY, "peer-a", "A",
                         carpeta="big", equipo_id=EQ)
    enviados = []
    cliente._enviar = lambda msg: enviados.append(msg) or asyncio.sleep(0)
    asyncio.run(cliente._manejar_sync_fetch({
        "tipo": "sync_fetch", "proyecto": PROY, "carpeta": "big",
        "equipo_id": EQ, "ruta": ruta, "version": 0, "hash": None,
        "fetch_id": "f-2", "peer_id": "peer-b",
    }))
    assert not any(m.get("tipo") == "file_big" for m in enviados)
    assert any(m.get("binario") or m.get("grande") for m in enviados)


def test_estado_incluye_transferencias():
    cliente = PeerClient("ws://127.0.0.1:1", "t", PROY, "peer-a", "A",
                         carpeta="big", equipo_id=EQ)
    cliente._transferencias["t1"] = {
        "ruta": "big/x.bin", "direccion": "subida", "estado": "en_cola",
        "total_bytes": 123, "ts": time.time(), "motivo": "",
    }
    est = cliente.estado()
    assert any(t["ruta"] == "big/x.bin" and t["estado"] == "en_cola"
               for t in est["transferencias"])


# --- LIVE: procesos reales (relay + dos servidores) ---

def test_live_file_big_binario_byte_identico():
    """LIVE: A comparte 'big'; B se une después. Un binario real de 5 MB
    en A (sin versión: el caso normal al copiar un archivo a la carpeta)
    llega a B byte-idéntico (sha256) vía file_big, por el relay."""
    relay_home = Path(tempfile.mkdtemp(prefix="big-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="big-relay-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="big-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="big-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="big-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="big-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18971, relay_home, relay_ws))
        procs.append(_arrancar(18972, a_home, a_ws))
        # Binario real de 5 MB en A antes de compartir (fuera de banda).
        datos = os.urandom(5 * 1024 * 1024)
        sha = hashlib.sha256(datos).hexdigest()
        pa = a_ws / "proyectos" / "general" / "big" / "asset.bin"
        pa.parent.mkdir(parents=True, exist_ok=True)
        pa.write_bytes(datos)
        _eq, code = _compartir(18972, "big", 18971)
        procs.append(_arrancar(18973, b_home, b_ws))
        _unirse(18973, code, "big", 18971, "Amigo")

        def _llego():
            pb = b_ws / "proyectos" / "general" / "big" / "asset.bin"
            return pb.exists() and hashlib.sha256(pb.read_bytes()).hexdigest() == sha

        _esperar(_llego, timeout=60,
                 mensaje="B no recibió el binario de 5 MB byte-idéntico")
    finally:
        for p in procs:
            _parar(p)


def test_live_file_big_reconexion_reenvia_completo():
    """LIVE: B se cae en medio; A publica un archivo grande en vivo; B
    vuelve (reconexión automática) y recibe el archivo COMPLETO y
    verificado. Nada a medias, nada perdido."""
    relay_home = Path(tempfile.mkdtemp(prefix="big2-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="big2-relay-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="big2-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="big2-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="big2-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="big2-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18981, relay_home, relay_ws))
        procs.append(_arrancar(18982, a_home, a_ws))
        procs.append(_arrancar(18983, b_home, b_ws))
        _eq, code = _compartir(18982, "big", 18981)
        _unirse(18983, code, "big", 18981, "Amigo")
        time.sleep(2)
        # B se cae.
        _parar(procs.pop())
        # A publica un archivo grande EN VIVO por el flujo normal
        # (notificar_cambio_local -> file_big en la sesión abierta).
        contenido = "línea de datos\n" * 150000  # ~2.2 MB
        sha = hashlib.sha256(contenido.encode("utf-8")).hexdigest()
        r = _post(18982, "/api/archivo?ruta=big/nota.txt&proyecto=general",
                  {"contenido": contenido, "autor": "Daniel"})
        assert r.status_code == 200, r.text
        time.sleep(1)
        # B vuelve (mismo home: re-une sus sesiones guardadas solo).
        procs.append(_arrancar(18983, b_home, b_ws))

        def _llego():
            pb = b_ws / "proyectos" / "general" / "big" / "nota.txt"
            if not pb.exists():
                return False
            return hashlib.sha256(pb.read_bytes()).hexdigest() == sha

        _esperar(_llego, timeout=60,
                 mensaje="B no recibió el archivo completo tras reconectar")
    finally:
        for p in procs:
            _parar(p)
