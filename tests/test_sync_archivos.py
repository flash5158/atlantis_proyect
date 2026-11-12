"""Tests de sync entre servidores (co-edición en vivo POR PROYECTO) — tests/test_sync_archivos.py.

Modelo: proyecto = carpeta dentro del workspace (``X/...``). Cada
proyecto tiene SU equipo (código + miembros). El relay solo reenvía
dentro del mismo equipo y rechaza rutas fuera de la carpeta.

Cubre:
- Carpetas: derivación, prefijo, escapes.
- Validación de mensajes de sync (rutas, carpeta, equipo, tamaño, hash).
- Equipos: compartir reutiliza el equipo (códigos nuevos, mismo equipo),
  unirse idempotente, carpeta equivocada, salir, aislamiento de tokens.
- aplicar_file_sync: upsert/delete/rename, conflicto con backup sin pérdida,
  dedupe, binario, sin-contenido (nunca borra), fuera-de-carpeta.
- PeerClient: decisión de fetch, catch-up con binario -> sync_unsupported.
- Relay: solo reenvía dentro del equipo; rechaza cruce de proyectos;
  no persiste contenido. peer_join con token de otro equipo -> 4403.
- LIVE (procesos reales): aislamiento X/Y, convergencia secuencial,
  conflicto simultáneo sin pérdida, delete/rename, catch-up acotado,
  proyecto en solitario nunca sincroniza, eco sin conflicto, oversize.

No toca la instalación real: HOME y workspace temporales. Sin secretos.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx
import pytest
import websockets

TMP_HOME = Path(tempfile.mkdtemp(prefix="atlantis-sync-home-"))
TMP_WS = Path(tempfile.mkdtemp(prefix="atlantis-sync-ws-"))
(TMP_WS / "proyectos").mkdir(exist_ok=True)

os.environ["HOME"] = str(TMP_HOME)
os.environ["NEXO_WORKSPACE"] = str(TMP_WS)
os.environ["NEXO_PUBLIC"] = "1"
os.environ["PORT"] = "18800"
# Sin SYNC_HUB_URL: las sesiones solo arrancan al compartir/unirse.
# Los archivos de prueba se escriben en el workspace REAL del servidor
# (config.PROYECTOS): la config queda fijada por el primer módulo de
# test importado, así que escribir en el TMP_WS propio fallaría según
# el orden de import. WS = config.PROYECTOS.parent.
os.environ.pop("SYNC_HUB_URL", None)
os.environ["ATLANTIS_RELAY_DEFAULT"] = "local"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from backend import sync as sync_mod  # noqa: E402
from backend.core import state as state_mod  # noqa: E402
from backend.core.config import config  # noqa: E402

client = TestClient(server.app)
ROOT = Path(__file__).resolve().parent.parent
PROY = "general"
EQ = "team-test"


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _msg_sync(ruta, contenido, carpeta="sync", equipo_id=EQ, base=0, version=1,
              operacion="upsert", autor="Daniel", sync_id=None, **extra):
    msg = {
        "tipo": "file_sync", "proyecto": PROY, "carpeta": carpeta,
        "equipo_id": equipo_id, "ruta": ruta,
        "operacion": operacion, "base_version": base, "version": version,
        "autor": autor, "contenido": contenido,
        "hash": _hash(contenido) if contenido is not None else None,
        "sync_id": sync_id or f"test-{ruta}-{base}-{version}-{contenido!r}"[:120],
    }
    msg.update(extra)
    return msg


# ---------------------------------------------------------- carpetas
def test_carpeta_de_ruta_y_prefijo():
    assert sync_mod.carpeta_de_ruta("X/a/b.txt") == "X"
    assert sync_mod.carpeta_de_ruta("X/a.txt") == "X"
    assert sync_mod.carpeta_de_ruta("README.md") == ""  # raíz: sin proyecto
    assert sync_mod.carpeta_de_ruta("") == ""
    assert sync_mod.ruta_en_carpeta("X/a.txt", "X") is True
    assert sync_mod.ruta_en_carpeta("X/a/b.txt", "X") is True
    assert sync_mod.ruta_en_carpeta("X", "X") is False  # la carpeta misma no
    assert sync_mod.ruta_en_carpeta("XY/a.txt", "X") is False  # prefijo falso
    assert sync_mod.ruta_en_carpeta("Y/a.txt", "X") is False
    assert sync_mod.ruta_en_carpeta("../X/a.txt", "X") is False
    assert sync_mod.carpeta_segura("mi-proyecto") is True
    assert sync_mod.carpeta_segura("a/b") is False
    assert sync_mod.carpeta_segura(".git") is False
    assert sync_mod.carpeta_segura("") is False


# ---------------------------------------------------------- validación
def test_validar_mensaje_sync_rechaza_rutas_peligrosas():
    base = {"tipo": "file_sync", "proyecto": PROY, "carpeta": "sync",
            "equipo_id": EQ, "operacion": "upsert",
            "base_version": 0, "version": 1, "contenido": "x", "hash": _hash("x")}
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "sync/a.txt"})[0] is True
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "../secreto"})[0] is False
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "sync/.git/config"})[0] is False
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "sync/.env"})[0] is False
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "/absoluta"})[0] is False
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "sync/ok", "base_version": -1})[0] is False
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "sync/ok", "operacion": "magia"})[0] is False
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "sync/ok", "proyecto": "../x"})[0] is False
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "sync/ok", "carpeta": "a/b"})[0] is False
    assert sync_mod.validar_mensaje_sync({**base, "ruta": "sync/ok", "equipo_id": ""})[0] is False


def test_validar_mensaje_sync_rechaza_fuera_de_carpeta():
    # Ruta de OTRO proyecto dentro de un mensaje del equipo de "sync".
    assert sync_mod.validar_mensaje_sync(_msg_sync("otro/a.txt", "x"))[0] is False
    # Rename con origen fuera de la carpeta.
    msg = _msg_sync("sync/nuevo.txt", "x", base=1, version=1,
                    operacion="rename", origen="otro/viejo.txt")
    assert sync_mod.validar_mensaje_sync(msg)[0] is False
    # Árbol con una ruta fuera de la carpeta.
    arbol = {"tipo": "sync_state", "proyecto": PROY, "carpeta": "sync",
             "equipo_id": EQ, "peer_id": "p",
             "arbol": {"sync/a.txt": {"version": 1, "hash": "a"},
                       "otro/b.txt": {"version": 1, "hash": "b"}}}
    assert sync_mod.validar_mensaje_sync(arbol)[0] is False
    # Fetch fuera de la carpeta.
    fetch = {"tipo": "sync_fetch", "proyecto": PROY, "carpeta": "sync",
             "equipo_id": EQ, "ruta": "otro/a.txt", "version": 1,
             "fetch_id": "f1"}
    assert sync_mod.validar_mensaje_sync(fetch)[0] is False


def test_validar_mensaje_sync_rechaza_gigante():
    gigante = "x" * (sync_mod.MAX_SYNC_CONTENIDO + 1)
    msg = _msg_sync("sync/g.txt", gigante)
    assert sync_mod.validar_mensaje_sync(msg)[0] is False


def test_validar_peer_join_sin_equipo_id_rechazado():
    # El modo legado (pairing global, sin equipo_id) está retirado:
    # todo peer pertenece a un equipo de proyecto.
    assert sync_mod.validar_peer_join("cualquier-token")[0] is False
    assert sync_mod.validar_peer_join("")[0] is False


# ---------------------------------------------------------- equipos
def _equipo_unico(sufijo: str) -> str:
    return f"eq-{sufijo}"


def test_equipos_compartir_reutiliza_equipo_y_codigos_nuevos():
    carpeta = _equipo_unico("reutiliza")
    r1 = sync_mod.crear_equipo_sync(PROY, carpeta, {"agent_name": "Hermes", "user_name": "Daniel"})
    r2 = sync_mod.crear_equipo_sync(PROY, carpeta, {"agent_name": "Hermes", "user_name": "Daniel"})
    assert r1["nuevo"] is True
    assert r2["nuevo"] is False
    assert r1["equipo_id"] == r2["equipo_id"]  # MISMO equipo
    assert r1["code"] != r2["code"]  # código NUEVO para el mismo equipo
    # Ambos códigos unen al mismo equipo.
    u1 = sync_mod.unirse_equipo_sync(r1["code"], {"agent_name": "H-A", "user_name": "Ana"})
    u2 = sync_mod.unirse_equipo_sync(r2["code"], {"agent_name": "H-B", "user_name": "Beto"})
    assert u1["equipo_id"] == u2["equipo_id"] == r1["equipo_id"]
    equipo = sync_mod.equipo_por_id(r1["equipo_id"])
    assert len(equipo["miembros"]) == 3  # Daniel + Ana + Beto


def test_equipos_unirse_idempotente():
    carpeta = _equipo_unico("idempotente")
    r = sync_mod.crear_equipo_sync(PROY, carpeta, {"agent_name": "Hermes", "user_name": "Daniel"})
    u1 = sync_mod.unirse_equipo_sync(r["code"], {"agent_name": "H-A", "user_name": "Ana"})
    u2 = sync_mod.unirse_equipo_sync(r["code"], {"agent_name": "H-A", "user_name": "Ana"})
    assert u2["ya_miembro"] is True
    assert u1["guest_token"] == u2["guest_token"]  # mismo token
    equipo = sync_mod.equipo_por_id(r["equipo_id"])
    assert len([m for m in equipo["miembros"] if m["user_name"] == "Ana"]) == 1


def test_equipos_unirse_carpeta_equivocada_no_deja_fantasma():
    carpeta = _equipo_unico("equivocada")
    r = sync_mod.crear_equipo_sync(PROY, carpeta, {"agent_name": "Hermes", "user_name": "Daniel"})
    with pytest.raises(ValueError, match="no de"):
        sync_mod.unirse_equipo_sync(r["code"], {"agent_name": "H-A", "user_name": "Ana"},
                                    carpeta_esperada="otra-carpeta")
    equipo = sync_mod.equipo_por_id(r["equipo_id"])
    assert len(equipo["miembros"]) == 1  # Ana NO quedó como miembro fantasma


def test_equipos_salir_e_invitado_no_saca_a_otros():
    carpeta = _equipo_unico("salir")
    r = sync_mod.crear_equipo_sync(PROY, carpeta, {"agent_name": "Hermes", "user_name": "Daniel"})
    u = sync_mod.unirse_equipo_sync(r["code"], {"agent_name": "H-A", "user_name": "Ana"})
    # Un invitado no puede sacar a otro miembro.
    with pytest.raises(ValueError):
        sync_mod.salir_equipo_sync(r["equipo_id"], u["guest_token"],
                                   "Hermes", "Daniel", es_dueno=False)
    # La invitada sale ella misma.
    res = sync_mod.salir_equipo_sync(r["equipo_id"], u["guest_token"],
                                     "H-A", "Ana", es_dueno=False)
    assert res["cerrado"] is False
    assert len(res["equipo"]["miembros"]) == 1
    # El anfitrión sale: el equipo se cierra.
    res2 = sync_mod.salir_equipo_sync(r["equipo_id"], "", "Hermes", "Daniel", es_dueno=True)
    assert res2["cerrado"] is True
    assert sync_mod.equipo_por_id(r["equipo_id"]) is None


def test_validar_peer_join_aisla_equipos():
    carpeta_x = _equipo_unico("x")
    carpeta_y = _equipo_unico("y")
    rx = sync_mod.crear_equipo_sync(PROY, carpeta_x, {"agent_name": "Hermes", "user_name": "Daniel"})
    ry = sync_mod.crear_equipo_sync(PROY, carpeta_y, {"agent_name": "Hermes", "user_name": "Daniel"})
    ux = sync_mod.unirse_equipo_sync(rx["code"], {"agent_name": "H-A", "user_name": "Ana"})
    # Token de invitada de X: solo vale para X.
    assert sync_mod.validar_peer_join(ux["guest_token"], rx["equipo_id"])[0] is True
    ok, motivo = sync_mod.validar_peer_join(ux["guest_token"], ry["equipo_id"],
                                            public_mode=True)
    assert ok is False and "otro equipo" in motivo
    # Token del dueño: cualquier equipo.
    from backend.core.config import config as cfg
    assert sync_mod.validar_peer_join(cfg.TOKEN, ry["equipo_id"])[0] is True
    # Sin token: solo local.
    assert sync_mod.validar_peer_join("", rx["equipo_id"], es_local_conn=True)[0] is True
    assert sync_mod.validar_peer_join("", rx["equipo_id"], es_local_conn=False)[0] is False
    # Equipo inexistente.
    assert sync_mod.validar_peer_join(cfg.TOKEN, "team-no-existe")[0] is False


# ---------------------------------------------------------- aplicar
def test_aplicar_upsert_escribe_y_fija_version():
    res = asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync("sync/unit/upsert.txt", "hola sync")))
    assert res["aplicado"] is True
    p = config.PROYECTOS / PROY / "sync" / "unit" / "upsert.txt"
    assert p.read_text(encoding="utf-8") == "hola sync"
    assert state_mod._version_archivo(PROY, "sync/unit/upsert.txt") == 1


def test_aplicar_upsert_conflicto_crea_backup_sin_perdida():
    ruta = "sync/unit/conflicto.txt"
    assert asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync(ruta, "LOCAL", base=0, version=1)))["aplicado"] is True
    # Entrante con base vieja (0) mientras local ya va en 1 -> conflicto
    res = asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync(ruta, "ENTRANTE", base=0, version=1, autor="Amigo")))
    assert res["conflicto"] is True
    assert res["aplicado"] is False
    p = config.PROYECTOS / PROY / ruta
    assert p.read_text(encoding="utf-8") == "LOCAL"  # local intacto
    assert state_mod._version_archivo(PROY, ruta) == 1  # versión intacta
    backup = config.PROYECTOS / PROY / res["conflicto_archivo"]
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "ENTRANTE"  # nada perdido


def test_aplicar_upsert_descarta_duplicado():
    ruta = "sync/unit/duplicado.txt"
    msg = _msg_sync(ruta, "una vez", sync_id="dup-unico-1")
    assert asyncio.run(sync_mod.aplicar_file_sync(PROY, msg))["aplicado"] is True
    res2 = asyncio.run(sync_mod.aplicar_file_sync(PROY, msg))
    assert res2["motivo"] == "duplicado"
    assert state_mod._version_archivo(PROY, ruta) == 1  # sin doble bump


def test_aplicar_rechaza_hash_invalido():
    ruta = "sync/unit/hash.txt"
    msg = _msg_sync(ruta, "contenido real")
    msg["hash"] = _hash("otro contenido")
    res = asyncio.run(sync_mod.aplicar_file_sync(PROY, msg))
    assert res["motivo"] == "hash_invalido"
    assert not (config.PROYECTOS / PROY / ruta).exists()


def test_aplicar_binario_no_escribe():
    ruta = "sync/unit/binario.bin"
    msg = _msg_sync(ruta, None, binario=True)
    res = asyncio.run(sync_mod.aplicar_file_sync(PROY, msg))
    assert res["motivo"] == "binario"
    assert not (config.PROYECTOS / PROY / ruta).exists()


def test_aplicar_upsert_sin_contenido_no_borra():
    ruta = "sync/unit/importante.txt"
    assert asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync(ruta, "IMPORTANTE", base=0, version=1)))["aplicado"] is True
    # Mensaje malformado: upsert sin contenido -> no escribe nada
    res = asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync(ruta, None, base=1, version=2)))
    assert res["motivo"] == "sin_contenido"
    assert (config.PROYECTOS / PROY / ruta).read_text(encoding="utf-8") == "IMPORTANTE"


def test_aplicar_rechaza_fuera_de_carpeta():
    # Un file_sync de otro proyecto no escribe nada, aunque el relay fallara.
    res = asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync("otro/evil.txt", "malicioso", carpeta="sync")))
    assert res["aplicado"] is False
    assert "carpeta" in res["motivo"]
    assert not (config.PROYECTOS / PROY / "otro" / "evil.txt").exists()


def test_aplicar_delete_y_rename():
    # delete
    ruta = "sync/unit/borrar.txt"
    assert asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync(ruta, "x", base=0, version=1)))["aplicado"] is True
    res = asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync(ruta, None, base=1, version=1, operacion="delete")))
    assert res["aplicado"] is True
    assert not (config.PROYECTOS / PROY / ruta).exists()
    assert state_mod._version_archivo(PROY, ruta) == 0
    # rename
    assert asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync("sync/unit/viejo.txt", "y", base=0, version=1)))["aplicado"] is True
    res = asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync("sync/unit/nuevo.txt", "y", base=1, version=1,
                        operacion="rename", origen="sync/unit/viejo.txt")))
    assert res["aplicado"] is True
    assert not (config.PROYECTOS / PROY / "sync/unit/viejo.txt").exists()
    assert (config.PROYECTOS / PROY / "sync/unit/nuevo.txt").read_text(encoding="utf-8") == "y"
    assert state_mod._version_archivo(PROY, "sync/unit/nuevo.txt") == 1
    assert state_mod._version_archivo(PROY, "sync/unit/viejo.txt") == 0


def test_aplicar_delete_conflicto_mantiene_archivo():
    ruta = "sync/unit/borrar_conf.txt"
    assert asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync(ruta, "mio", base=0, version=1)))["aplicado"] is True
    # Delete con base vieja (0) mientras local va en 1 -> conflicto, no borra
    res = asyncio.run(sync_mod.aplicar_file_sync(
        PROY, _msg_sync(ruta, None, base=0, version=1, operacion="delete")))
    assert res["conflicto"] is True
    assert (config.PROYECTOS / PROY / ruta).read_text(encoding="utf-8") == "mio"


def test_decidir_fetch():
    assert sync_mod._decidir_fetch(None, {"version": 1, "hash": "a"}) == "fetch"
    assert sync_mod._decidir_fetch({"version": 1, "hash": "a"},
                                    {"version": 2, "hash": "b"}) == "fetch"
    # Misma versión, hash distinto -> divergente -> fetch
    assert sync_mod._decidir_fetch({"version": 1, "hash": "a"},
                                    {"version": 1, "hash": "b"}) == "fetch"
    assert sync_mod._decidir_fetch({"version": 1, "hash": "a"},
                                    {"version": 1, "hash": "a"}) == "nada"
    assert sync_mod._decidir_fetch(None, {"version": 1, "binario": True}) == "binario"
    assert sync_mod._decidir_fetch({"version": 1, "hash": "a"},
                                    {"version": 1, "hash": "a", "binario": True}) == "nada"


def test_construir_arbol_sync_acotado_a_carpeta():
    base = config.PROYECTOS / PROY / "sync" / "arbol"
    base.mkdir(parents=True, exist_ok=True)
    (base / "a.txt").write_text("texto", encoding="utf-8")
    (base / ".oculto.txt").write_text("x", encoding="utf-8")
    (base / ".git").mkdir(exist_ok=True)
    (base / ".git" / "config").write_text("x", encoding="utf-8")
    (base / "b.bin").write_bytes(b"\x00\xff\xfe\x01")
    (base / "c.txt.conflict.Daniel.20260101-000000-000001").write_text("viejo", encoding="utf-8")
    (base / "grande.txt").write_text("x" * (sync_mod.MAX_SYNC_ARBOL_BYTES + 10), encoding="utf-8")
    # Archivo de OTRO proyecto: no debe salir en el árbol de "sync".
    otra = config.PROYECTOS / PROY / "otro"
    otra.mkdir(parents=True, exist_ok=True)
    (otra / "secreto.txt").write_text("no-debe-salir", encoding="utf-8")
    arbol = sync_mod.construir_arbol_sync(PROY, "sync")
    assert arbol["sync/arbol/a.txt"]["hash"] == _hash("texto")
    assert "sync/arbol/.oculto.txt" not in arbol
    assert "sync/arbol/.git/config" not in arbol
    assert arbol["sync/arbol/b.bin"]["binario"] is True
    assert not any(".conflict." in k for k in arbol)
    assert arbol["sync/arbol/grande.txt"]["grande"] is True
    assert arbol["sync/arbol/grande.txt"]["hash"] is None
    assert "otro/secreto.txt" not in arbol  # aislamiento por carpeta


def test_sync_state_binario_pide_fetch():
    # Un peer reporta un archivo binario que yo no tengo -> se pide por
    # fetch (puede llegar como file_big completo si cabe en el cap; si el
    # peer responde con la bandera, ahí sí se reporta no soportado).
    vistos = []

    async def falso_enviar_todos(msg):
        vistos.append(msg)

    cliente = sync_mod.PeerClient("ws://127.0.0.1:1", "t", PROY, "peer-x", "X",
                                  carpeta="sync", equipo_id=EQ)
    enviados = []
    cliente._enviar = lambda msg: enviados.append(msg) or asyncio.sleep(0)
    orig = sync_mod.enviar_todos
    sync_mod.enviar_todos = falso_enviar_todos
    try:
        asyncio.run(cliente._manejar_sync_state({
            "tipo": "sync_state", "peer_id": "peer-y",
            "arbol": {"sync/unit/solo_binario.bin": {"version": 1, "hash": "abc", "binario": True}},
        }))
    finally:
        sync_mod.enviar_todos = orig
    assert any(m.get("tipo") == "sync_fetch" and m.get("ruta") == "sync/unit/solo_binario.bin"
               for m in enviados)
    assert not any(m.get("tipo") == "sync_unsupported" for m in vistos)


# ---------------------------------------------------------- live helpers
def _arrancar(puerto, home, ws_dir, extra=None):
    env = dict(os.environ)
    env.update({"HOME": str(home), "NEXO_WORKSPACE": str(ws_dir),
                "NEXO_PUBLIC": "1", "PORT": str(puerto)})
    if extra:
        env.update(extra)
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
    proc.terminate()
    raise RuntimeError(f"el servidor del puerto {puerto} no arrancó")


def _parar(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def _get(puerto, path):
    return httpx.get(f"http://127.0.0.1:{puerto}{path}", timeout=15, trust_env=False)


def _post(puerto, path, payload=None):
    return httpx.post(f"http://127.0.0.1:{puerto}{path}", json=payload or {}, timeout=15, trust_env=False)


def _delete(puerto, path):
    return httpx.delete(f"http://127.0.0.1:{puerto}{path}", timeout=15, trust_env=False)


def _esperar(cond, timeout=40, mensaje="condición no cumplida"):
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
    """POST /api/proyectos/<carpeta>/compartir en un servidor. Retorna (equipo_id, code)."""
    r = _post(puerto, f"/api/proyectos/{carpeta}/compartir",
              {"hub_url": f"ws://127.0.0.1:{hub_puerto}", "user_name": nombre,
               "agent_name": f"Hermes-{nombre}"})
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["ok"] is True, datos
    return datos["equipo_id"], datos["code"]


def _unirse(puerto, codigo, carpeta, hub_puerto, nombre="Amigo"):
    """POST /api/proyectos/unirse en un servidor. Retorna equipo_id."""
    r = _post(puerto, "/api/proyectos/unirse",
              {"code": codigo, "carpeta": carpeta,
               "hub_url": f"ws://127.0.0.1:{hub_puerto}", "user_name": nombre,
               "agent_name": f"Hermes-{nombre}"})
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["ok"] is True, datos
    return datos["equipo_id"]


def _contiene_en_disco(ws_dir, aguja: str) -> bool:
    for p in Path(ws_dir).rglob("*"):
        if p.is_file():
            try:
                if aguja in p.read_text(encoding="utf-8", errors="ignore"):
                    return True
            except OSError:
                pass
    return False


# ---------------------------------------------------------- live tests
def test_sync_aislamiento_proyectos():
    """Daniel en X+Y, A en X, B en Y: nadie ve el proyecto del otro."""
    relay_home = Path(tempfile.mkdtemp(prefix="sync-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="sync-relay-ws-"))
    d_home = Path(tempfile.mkdtemp(prefix="sync-d-"))
    d_ws = Path(tempfile.mkdtemp(prefix="sync-d-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="sync-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="sync-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="sync-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="sync-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18911, relay_home, relay_ws))
        procs.append(_arrancar(18912, d_home, d_ws))
        procs.append(_arrancar(18913, a_home, a_ws))
        procs.append(_arrancar(18914, b_home, b_ws))
        # Daniel comparte X con A y Y con B (dos equipos distintos).
        _eq_x, code_x = _compartir(18912, "X", 18911)
        _unirse(18913, code_x, "X", 18911, "Ana")
        _eq_y, code_y = _compartir(18912, "Y", 18911)
        _unirse(18914, code_y, "Y", 18911, "Beto")
        time.sleep(2)  # sesiones peer conectadas

        # A edita X -> Daniel lo recibe, B NO.
        assert _post(18913, "/api/archivo?ruta=X/a.txt&proyecto=general",
                     {"contenido": "de Ana para X", "autor": "Ana"}).status_code == 200
        _esperar(lambda: _get(18912, "/api/archivo?ruta=X/a.txt&proyecto=general").status_code == 200,
                 mensaje="Daniel no recibió la edición de Ana en X")
        assert _get(18912, "/api/archivo?ruta=X/a.txt&proyecto=general").json()["contenido"] == "de Ana para X"
        time.sleep(2)
        assert _get(18914, "/api/archivo?ruta=X/a.txt&proyecto=general").status_code == 404, \
            "¡Beto recibió un archivo del proyecto X (que no es suyo)!"

        # B edita Y -> Daniel lo recibe, A NO.
        assert _post(18914, "/api/archivo?ruta=Y/b.txt&proyecto=general",
                     {"contenido": "de Beto para Y", "autor": "Beto"}).status_code == 200
        _esperar(lambda: _get(18912, "/api/archivo?ruta=Y/b.txt&proyecto=general").status_code == 200,
                 mensaje="Daniel no recibió la edición de Beto en Y")
        time.sleep(2)
        assert _get(18913, "/api/archivo?ruta=Y/b.txt&proyecto=general").status_code == 404, \
            "¡Ana recibió un archivo del proyecto Y (que no es suyo)!"

        # Daniel edita X -> solo A lo recibe.
        assert _post(18912, "/api/archivo?ruta=X/de_daniel.txt&proyecto=general",
                     {"contenido": "Daniel en X", "autor": "Daniel"}).status_code == 200
        _esperar(lambda: _get(18913, "/api/archivo?ruta=X/de_daniel.txt&proyecto=general").status_code == 200,
                 mensaje="Ana no recibió la edición de Daniel en X")
        time.sleep(2)
        assert _get(18914, "/api/archivo?ruta=X/de_daniel.txt&proyecto=general").status_code == 404

        # Daniel edita Y -> solo B lo recibe.
        assert _post(18912, "/api/archivo?ruta=Y/de_daniel.txt&proyecto=general",
                     {"contenido": "Daniel en Y", "autor": "Daniel"}).status_code == 200
        _esperar(lambda: _get(18914, "/api/archivo?ruta=Y/de_daniel.txt&proyecto=general").status_code == 200,
                 mensaje="Beto no recibió la edición de Daniel en Y")
        time.sleep(2)
        assert _get(18913, "/api/archivo?ruta=Y/de_daniel.txt&proyecto=general").status_code == 404
    finally:
        for p in procs:
            _parar(p)


def test_sync_secuencial_converge_y_relay_no_guarda():
    relay_home = Path(tempfile.mkdtemp(prefix="sync-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="sync-relay-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="sync-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="sync-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="sync-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="sync-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18921, relay_home, relay_ws))
        procs.append(_arrancar(18922, a_home, a_ws))
        procs.append(_arrancar(18923, b_home, b_ws))
        _eq, code = _compartir(18922, "sync", 18921)
        _unirse(18923, code, "sync", 18921, "Amigo")
        time.sleep(2)

        r = _post(18922, "/api/archivo?ruta=sync/archivo.txt&proyecto=general",
                  {"contenido": "hola desde A", "autor": "Daniel"})
        assert r.status_code == 200, r.text
        assert r.json()["version"] == 1

        # B converge: mismo contenido y misma versión
        _esperar(lambda: _get(18923, "/api/archivo?ruta=sync/archivo.txt&proyecto=general").status_code == 200,
                 mensaje="B no recibió el archivo")
        rb = _get(18923, "/api/archivo?ruta=sync/archivo.txt&proyecto=general")
        assert rb.json()["contenido"] == "hola desde A"
        assert rb.json()["version"] == 1

        # El relay NUNCA guarda contenido: 404 + nada en su disco
        rr = _get(18921, "/api/archivo?ruta=sync/archivo.txt&proyecto=general")
        assert rr.status_code == 404
        assert _contiene_en_disco(relay_ws, "hola desde A") is False
    finally:
        for p in procs:
            _parar(p)


def test_sync_relay_rechaza_cruce_de_proyectos():
    """Relay: un peer del equipo X no puede enviar ni pedir nada de Y,
    y un token de X no puede hacer peer_join en Y (4403)."""
    async def _run():
        relay_home = Path(tempfile.mkdtemp(prefix="sync-relay-"))
        relay_ws = Path(tempfile.mkdtemp(prefix="sync-relay-ws-"))
        proc = _arrancar(18931, relay_home, relay_ws)
        try:
            # Equipo X con una invitada (token real del relay).
            r = _post(18931, "/api/sync/equipos",
                      {"proyecto": "general", "carpeta": "X",
                       "agent_name": "Hermes", "user_name": "Daniel"})
            assert r.status_code == 200, r.text
            eq_x, code_x = r.json()["equipo_id"], r.json()["code"]
            r = _post(18931, "/api/sync/equipos/unirse",
                      {"code": code_x, "agent_name": "H-A", "user_name": "Ana"})
            assert r.status_code == 200, r.text
            token_x = r.json()["guest_token"]
            # Equipo Y.
            r = _post(18931, "/api/sync/equipos",
                      {"proyecto": "general", "carpeta": "Y",
                       "agent_name": "Hermes", "user_name": "Daniel"})
            eq_y = r.json()["equipo_id"]

            # Peer_join en X con el token de X: OK.
            async with websockets.connect(
                    f"ws://127.0.0.1:18931/ws?token={token_x}&nombre=Ana") as ws:
                await ws.send(json.dumps({"tipo": "peer_join", "proyecto": "general",
                                          "carpeta": "X", "equipo_id": eq_x,
                                          "peer_id": "peer-x", "autor": "Ana"}))
                ok = False
                for _ in range(20):
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    if json.loads(raw).get("tipo") == "peer_ok":
                        ok = True
                        break
                assert ok, "peer_join válido en X no recibió peer_ok"
                # file_sync con ruta de Y dentro del equipo X -> error, no reenvío.
                await ws.send(json.dumps(_msg_sync("Y/evil.txt", "malicioso",
                                                   carpeta="X", equipo_id=eq_x,
                                                   sync_id="cruce-1")))
                visto_error = False
                for _ in range(20):
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    if json.loads(raw).get("tipo") == "error":
                        visto_error = True
                        break
                assert visto_error, "el relay no rechazó el file_sync fuera de carpeta"
                # sync_fetch de Y -> error.
                await ws.send(json.dumps({"tipo": "sync_fetch", "proyecto": "general",
                                          "carpeta": "X", "equipo_id": eq_x,
                                          "ruta": "Y/b.txt", "version": 1,
                                          "fetch_id": "cruce-fetch-1"}))
                visto_error = False
                for _ in range(20):
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    if json.loads(raw).get("tipo") == "error":
                        visto_error = True
                        break
                assert visto_error, "el relay no rechazó el fetch fuera de carpeta"
                # file_sync con equipo_id de Y (no es su equipo) -> error.
                await ws.send(json.dumps(_msg_sync("X/a.txt", "x",
                                                   carpeta="X", equipo_id=eq_y,
                                                   sync_id="cruce-2")))
                visto_error = False
                for _ in range(20):
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    if json.loads(raw).get("tipo") == "error":
                        visto_error = True
                        break
                assert visto_error, "el relay no rechazó el mensaje de otro equipo"

            # Peer_join en Y con el token de X -> 4403.
            async with websockets.connect(
                    f"ws://127.0.0.1:18931/ws?token={token_x}&nombre=Ana") as ws:
                await ws.send(json.dumps({"tipo": "peer_join", "proyecto": "general",
                                          "carpeta": "Y", "equipo_id": eq_y,
                                          "peer_id": "peer-x2", "autor": "Ana"}))
                cerrado_4403 = False
                for _ in range(20):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        if json.loads(raw).get("tipo") == "peer_ok":
                            raise AssertionError("token de X aceptado en el equipo Y")
                    except websockets.exceptions.ConnectionClosed as e:
                        assert e.code == 4403, f"código de cierre inesperado: {e.code}"
                        cerrado_4403 = True
                        break
                assert cerrado_4403, "el peer con token de otro equipo no fue cerrado (4403)"
        finally:
            _parar(proc)

    asyncio.run(_run())


def test_sync_conflicto_simultaneo_sin_perdida():
    relay_home = Path(tempfile.mkdtemp(prefix="sync-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="sync-relay-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="sync-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="sync-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="sync-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="sync-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18941, relay_home, relay_ws))
        procs.append(_arrancar(18942, a_home, a_ws))
        # B arranca SIN unirse: edita a ciegas (offline, sin sesión)
        procs.append(_arrancar(18943, b_home, b_ws))
        _eq, code = _compartir(18942, "sync", 18941)
        time.sleep(1)
        # Misma base en ambos
        assert _post(18942, "/api/archivo?ruta=sync/conflicto.txt&proyecto=general",
                     {"contenido": "base", "autor": "Daniel"}).status_code == 200
        assert _post(18943, "/api/archivo?ruta=sync/conflicto.txt&proyecto=general",
                     {"contenido": "base", "autor": "Amigo"}).status_code == 200
        # Edición simultánea: misma base, contenido distinto
        assert _post(18942, "/api/archivo?ruta=sync/conflicto.txt&proyecto=general",
                     {"contenido": "editado por A", "autor": "Daniel"}).status_code == 200
        assert _post(18943, "/api/archivo?ruta=sync/conflicto.txt&proyecto=general",
                     {"contenido": "editado por B", "autor": "Amigo"}).status_code == 200
        # B se une ahora: el catch-up detecta la divergencia (misma
        # versión, hash distinto) y ambos lados guardan backup.
        _unirse(18943, code, "sync", 18941, "Amigo")

        # Ambos mantienen su contenido; ambos reciben un backup con el otro
        def _convergio():
            pa = a_ws / "proyectos" / "general" / "sync" / "conflicto.txt"
            pb = b_ws / "proyectos" / "general" / "sync" / "conflicto.txt"
            if not pa.exists() or not pb.exists():
                return False
            if pa.read_text(encoding="utf-8") != "editado por A":
                return False
            if pb.read_text(encoding="utf-8") != "editado por B":
                return False
            backups_a = [f for f in (a_ws / "proyectos" / "general" / "sync").glob("*.conflict.*")
                         if f.read_text(encoding="utf-8") == "editado por B"]
            backups_b = [f for f in (b_ws / "proyectos" / "general" / "sync").glob("*.conflict.*")
                         if f.read_text(encoding="utf-8") == "editado por A"]
            return bool(backups_a) and bool(backups_b)

        _esperar(_convergio, timeout=45,
                 mensaje="el conflicto simultáneo no dejó backups en ambos lados")
    finally:
        for p in procs:
            _parar(p)


def test_sync_delete_y_rename():
    relay_home = Path(tempfile.mkdtemp(prefix="sync-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="sync-relay-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="sync-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="sync-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="sync-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="sync-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18951, relay_home, relay_ws))
        procs.append(_arrancar(18952, a_home, a_ws))
        procs.append(_arrancar(18953, b_home, b_ws))
        _eq, code = _compartir(18952, "sync", 18951)
        _unirse(18953, code, "sync", 18951, "Amigo")
        time.sleep(2)
        assert _post(18952, "/api/archivo?ruta=sync/borrar.txt&proyecto=general",
                     {"contenido": "x", "autor": "Daniel"}).status_code == 200
        assert _post(18952, "/api/archivo?ruta=sync/viejo.txt&proyecto=general",
                     {"contenido": "y", "autor": "Daniel"}).status_code == 200
        _esperar(lambda: _get(18953, "/api/archivo?ruta=sync/borrar.txt&proyecto=general").status_code == 200,
                 mensaje="B no recibió los archivos iniciales")
        # delete propaga
        assert _delete(18952, "/api/archivo?ruta=sync/borrar.txt&proyecto=general").status_code == 200
        _esperar(lambda: _get(18953, "/api/archivo?ruta=sync/borrar.txt&proyecto=general").status_code == 404,
                 mensaje="B no aplicó el delete")
        # rename propaga
        r = _post(18952, "/api/archivo/renombrar",
                  {"origen": "sync/viejo.txt", "destino": "sync/nuevo.txt", "proyecto": "general"})
        assert r.status_code == 200, r.text
        _esperar(lambda: _get(18953, "/api/archivo?ruta=sync/nuevo.txt&proyecto=general").status_code == 200,
                 mensaje="B no aplicó el rename")
        assert _get(18953, "/api/archivo?ruta=sync/nuevo.txt&proyecto=general").json()["contenido"] == "y"
        assert _get(18953, "/api/archivo?ruta=sync/viejo.txt&proyecto=general").status_code == 404
    finally:
        for p in procs:
            _parar(p)


def test_sync_catchup_acotado_al_reconectar():
    """B se une tarde al equipo de X: recibe los archivos de X, NUNCA los de Y."""
    relay_home = Path(tempfile.mkdtemp(prefix="sync-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="sync-relay-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="sync-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="sync-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="sync-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="sync-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18961, relay_home, relay_ws))
        procs.append(_arrancar(18962, a_home, a_ws))
        # A tiene archivos de X y de Y antes de que B exista.
        assert _post(18962, "/api/archivo?ruta=X/uno.txt&proyecto=general",
                     {"contenido": "uno", "autor": "Daniel"}).status_code == 200
        assert _post(18962, "/api/archivo?ruta=X/dos.txt&proyecto=general",
                     {"contenido": "dos", "autor": "Daniel"}).status_code == 200
        assert _post(18962, "/api/archivo?ruta=Y/privado.txt&proyecto=general",
                     {"contenido": "privado de Y", "autor": "Daniel"}).status_code == 200
        # A comparte SOLO X; B se une a X.
        _eq, code = _compartir(18962, "X", 18961)
        procs.append(_arrancar(18963, b_home, b_ws))
        _unirse(18963, code, "X", 18961, "Amigo")
        _esperar(lambda: _get(18963, "/api/archivo?ruta=X/uno.txt&proyecto=general").status_code == 200,
                 mensaje="B no hizo catch-up del primer archivo de X")
        assert _get(18963, "/api/archivo?ruta=X/uno.txt&proyecto=general").json()["contenido"] == "uno"
        _esperar(lambda: _get(18963, "/api/archivo?ruta=X/dos.txt&proyecto=general").status_code == 200,
                 mensaje="B no hizo catch-up del segundo archivo de X")
        assert _get(18963, "/api/archivo?ruta=X/dos.txt&proyecto=general").json()["contenido"] == "dos"
        # Y jamás llega a B.
        time.sleep(2)
        assert _get(18963, "/api/archivo?ruta=Y/privado.txt&proyecto=general").status_code == 404
        assert _contiene_en_disco(b_ws, "privado de Y") is False
        # Versiones iguales en ambos para X.
        assert _get(18963, "/api/archivo?ruta=X/uno.txt&proyecto=general").json()["version"] == \
            _get(18962, "/api/archivo?ruta=X/uno.txt&proyecto=general").json()["version"] == 1
    finally:
        for p in procs:
            _parar(p)


def test_sync_proyecto_solo_nunca_sincroniza():
    """Un proyecto sin equipo no sincroniza: editar 'solo/' no le llega a nadie."""
    relay_home = Path(tempfile.mkdtemp(prefix="sync-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="sync-relay-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="sync-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="sync-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="sync-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="sync-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18971, relay_home, relay_ws))
        procs.append(_arrancar(18972, a_home, a_ws))
        procs.append(_arrancar(18973, b_home, b_ws))
        # A y B comparten "sync"; nadie comparte "solo".
        _eq, code = _compartir(18972, "sync", 18971)
        _unirse(18973, code, "sync", 18971, "Amigo")
        time.sleep(2)
        # A edita el proyecto en solitario.
        assert _post(18972, "/api/archivo?ruta=solo/mio.txt&proyecto=general",
                     {"contenido": "solo mio", "autor": "Daniel"}).status_code == 200
        time.sleep(3)
        assert _get(18973, "/api/archivo?ruta=solo/mio.txt&proyecto=general").status_code == 404
        assert _contiene_en_disco(b_ws, "solo mio") is False
        # Y el archivo de la raíz (sin carpeta) tampoco sincroniza.
        assert _post(18972, "/api/archivo?ruta=raiz.txt&proyecto=general",
                     {"contenido": "raiz", "autor": "Daniel"}).status_code == 200
        time.sleep(2)
        assert _get(18973, "/api/archivo?ruta=raiz.txt&proyecto=general").status_code == 404
        # B no tiene sesión para "solo": no está compartido.
        compartidos = _get(18973, "/api/proyectos/compartidos").json()["proyectos"]
        assert [p["carpeta"] for p in compartidos] == ["sync"]
    finally:
        for p in procs:
            _parar(p)


def test_sync_eco_sin_conflicto():
    """Relay == propio servidor: guardar no crea un conflicto contra mi
    propio mensaje (el eco se descarta por sync_id)."""
    home = Path(tempfile.mkdtemp(prefix="sync-eco-"))
    ws_dir = Path(tempfile.mkdtemp(prefix="sync-eco-ws-"))
    proc = None
    try:
        proc = _arrancar(18981, home, ws_dir)
        # Compartir sin hub_url: el relay es este mismo servidor.
        r = _post(18981, "/api/proyectos/eco/compartir",
                  {"user_name": "Daniel", "agent_name": "Hermes-Daniel"})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        time.sleep(2)
        assert _post(18981, "/api/archivo?ruta=eco/a.txt&proyecto=general",
                     {"contenido": "v1", "autor": "Daniel"}).status_code == 200
        assert _post(18981, "/api/archivo?ruta=eco/a.txt&proyecto=general",
                     {"contenido": "v2", "autor": "Daniel"}).status_code == 200
        time.sleep(2)
        resp = _get(18981, "/api/archivo?ruta=eco/a.txt&proyecto=general")
        assert resp.json()["contenido"] == "v2"
        assert resp.json()["version"] == 2
        backups = list((ws_dir / "proyectos" / "general" / "eco").glob("*.conflict.*"))
        assert backups == [], f"el eco creó backups de conflicto: {backups}"
    finally:
        if proc:
            _parar(proc)


def test_sync_gigante_viaja_completo_y_binario_fuera_de_banda_no_se_empuja_solo():
    # Contrato nuevo: los archivos grandes SÍ viajan completos (file_big).
    # Límite conocido: sin watcher de archivos, un binario copiado fuera
    # de banda con el peer ya unido no dispara push en vivo (llega en el
    # catch-up al reunirse, ver test_live_file_big_binario_byte_identico).
    relay_home = Path(tempfile.mkdtemp(prefix="sync-relay-"))
    relay_ws = Path(tempfile.mkdtemp(prefix="sync-relay-ws-"))
    a_home = Path(tempfile.mkdtemp(prefix="sync-a-"))
    a_ws = Path(tempfile.mkdtemp(prefix="sync-a-ws-"))
    b_home = Path(tempfile.mkdtemp(prefix="sync-b-"))
    b_ws = Path(tempfile.mkdtemp(prefix="sync-b-ws-"))
    procs = []
    try:
        procs.append(_arrancar(18991, relay_home, relay_ws))
        procs.append(_arrancar(18992, a_home, a_ws))
        procs.append(_arrancar(18993, b_home, b_ws))
        _eq, code = _compartir(18992, "sync", 18991)
        _unirse(18993, code, "sync", 18991, "Amigo")
        # Archivo binario en el disco de A con B ya unido: sin push en vivo.
        bin_dir = a_ws / "proyectos" / "general" / "sync"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "binario.bin").write_bytes(b"\x00\xff\xfe\x01\x02" * 100)
        time.sleep(3)
        assert not (b_ws / "proyectos" / "general" / "sync" / "binario.bin").exists()
        assert (bin_dir / "binario.bin").read_bytes() == b"\x00\xff\xfe\x01\x02" * 100
        # Archivo gigante por la API: viaja COMPLETO en vivo (file_big).
        gigante = "x" * (sync_mod.MAX_SYNC_CONTENIDO + 500_000)
        assert _post(18992, "/api/archivo?ruta=sync/gigante.txt&proyecto=general",
                     {"contenido": gigante, "autor": "Daniel"}).status_code == 200
        _esperar(lambda: _get(18993, "/api/archivo?ruta=sync/gigante.txt&proyecto=general"
                              ).json()["contenido"] == gigante,
                 mensaje="B no recibió el archivo gigante completo")
        assert _get(18992, "/api/archivo?ruta=sync/gigante.txt&proyecto=general").json()["contenido"] == gigante
    finally:
        for p in procs:
            _parar(p)
