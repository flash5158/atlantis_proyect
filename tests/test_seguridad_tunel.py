"""Tests de blindaje de seguridad del túnel (auditoría de Argon).

Cubre: es_local() con cabeceras de proxy (cloudflared), /api/token_local
sin fuga para remoto, tokens de invitado (join emite guest_token, sirve
para endpoints normales pero NO para admin), rate-limit de join, MCP
solo-dueño vs invitado, target_cwd validado y bloqueo .git/.env.

El fixture pone el hub en modo NO público con un TOKEN de dueño fijo,
porque en modo público todo pasa (comportamiento histórico del demo).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("ATLANTIS_RELAY_DEFAULT", "local")

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from backend.core.config import config  # noqa: E402
from backend.routers import sync_teams as sync_teams_router  # noqa: E402

client = TestClient(server.app)

OWNER = "owner-test-token-secreto"


@pytest.fixture(autouse=True)
def modo_seguro():
    public_anterior = config.PUBLIC_MODE
    token_anterior = config.TOKEN
    config.PUBLIC_MODE = False
    config.TOKEN = OWNER
    config.GUEST_TOKENS.clear()
    sync_teams_router._JOIN_FALLOS.clear()
    yield
    sync_teams_router._JOIN_FALLOS.clear()
    config.GUEST_TOKENS.clear()
    config.PUBLIC_MODE = public_anterior
    config.TOKEN = token_anterior


def _crear_equipo(carpeta="Tunel"):
    # Equipo POR PROYECTO: compartir la carpeta crea el equipo + código.
    r = client.post(f"/api/proyectos/{carpeta}/compartir",
                    params={"token": OWNER},
                    json={"proyecto": "general", "agent_name": "Koko",
                          "user_name": "Daniel"})
    assert r.status_code == 200, r.text
    return r.json()["code"], r.json()["equipo_id"]


def _unir(codigo, agente="Hermes-Amigo", usuario="Amigo", token_kw=None):
    params = {}
    if token_kw is not None:
        params["token"] = token_kw
    return client.post("/api/sync/equipos/unirse", params=params,
                       json={"code": codigo, "agent_name": agente,
                             "user_name": usuario, "carpeta": "Tunel"})


# --- 1. es_local() con cabeceras de proxy -------------------------------

def test_token_local_no_fuga_por_tunel():
    # Petición "local" por socket pero con cabecera de Cloudflare:
    # NO es local -> no recibe el token del dueño.
    r = client.get("/api/token_local",
                   headers={"cf-connecting-ip": "203.0.113.9"})
    assert r.status_code == 403
    # Sin cabecera de proxy y desde loopback/testclient: bootstrap normal.
    r = client.get("/api/token_local")
    assert r.status_code == 200
    assert r.json()["token"] == OWNER


def test_arbol_no_fuga_token_por_tunel():
    r = client.get("/api/arbol", headers={"x-forwarded-for": "203.0.113.9"})
    assert r.status_code == 401
    r = client.get("/api/arbol")
    assert r.status_code == 200
    assert r.json()["token"] == OWNER


# --- 2. Tokens de invitado ------------------------------------------------

def test_join_emite_guest_token_y_sirve_para_endpoints_normales():
    codigo, _eid = _crear_equipo()
    r = _unir(codigo)
    assert r.status_code == 200
    guest = r.json()["guest_token"]
    assert isinstance(guest, str) and len(guest) == 48  # token_hex(24)
    # El guest_token NO sale en el resumen del equipo.
    assert guest not in str(r.json()["miembros"])
    # Sirve para endpoints normales... (cabecera proxy = petición remota:
    # sin ella, TestClient es "local" y pasaría sin token).
    r = client.get("/api/status", params={"token": guest},
                   headers={"cf-connecting-ip": "203.0.113.9"})
    assert r.status_code == 200
    # ...pero NO para admin.
    for metodo, ruta, payload in [
        ("POST", "/api/workspace/select", {"mode": "clean"}),
        ("POST", "/api/terminal/exec", {"cmd": "id"}),
        ("POST", "/api/agents/connect", {"adapter": "hermes"}),
        ("POST", "/api/tunnel/start", {}),
        ("POST", "/api/antigravity/sources/add", {"path": "/tmp"}),
        ("POST", "/api/mcps", {"name": "mcp-malo"}),
    ]:
        r = client.request(metodo, ruta, params={"token": guest}, json=payload)
        assert r.status_code == 403, f"{metodo} {ruta} debió dar 403, dio {r.status_code}"


def test_websocket_acepta_guest_token_y_rechaza_sin_token_valido():
    codigo, _eid = _crear_equipo()
    guest = _unir(codigo).json()["guest_token"]
    # Cabecera proxy = conexión remota por túnel: el token decide.
    with client.websocket_connect(
            f"/ws?token={guest}&nombre=Amigo",
            headers={"cf-connecting-ip": "203.0.113.9"}):
        pass
    with pytest.raises(Exception):
        with client.websocket_connect(
                "/ws?token=token-invalido&nombre=X",
                headers={"cf-connecting-ip": "203.0.113.9"}):
            pass


def test_leave_revoca_guest_token():
    codigo, eid = _crear_equipo()
    guest = _unir(codigo).json()["guest_token"]
    r = client.post("/api/sync/equipos/salir", params={"token": guest},
                    json={"equipo_id": eid, "agent_name": "Hermes-Amigo",
                          "user_name": "Amigo"})
    assert r.status_code == 200
    r = client.get("/api/status", params={"token": guest},
                   headers={"cf-connecting-ip": "203.0.113.9"})
    assert r.status_code == 401


def test_invitado_no_puede_sacar_a_otro_miembro():
    codigo, eid = _crear_equipo()
    guest_a = _unir(codigo, "Hermes-A", "A").json()["guest_token"]
    _unir(codigo, "Hermes-B", "B")
    r = client.post("/api/sync/equipos/salir", params={"token": guest_a},
                    json={"equipo_id": eid, "agent_name": "Hermes-B",
                          "user_name": "B"})
    assert r.status_code == 403


def test_cerrar_equipo_solo_dueno_y_revoca_invitados():
    codigo, eid = _crear_equipo()
    guest = _unir(codigo).json()["guest_token"]
    # Un invitado no puede cerrar el equipo (salir del anfitrión = cerrar).
    r = client.post("/api/sync/equipos/salir", params={"token": guest},
                    json={"equipo_id": eid, "agent_name": "Koko",
                          "user_name": "Daniel"})
    assert r.status_code == 403
    # El dueño sale (anfitrión): el equipo se cierra y el token se revoca.
    r = client.post("/api/sync/equipos/salir", params={"token": OWNER},
                    json={"equipo_id": eid, "agent_name": "Koko",
                          "user_name": "Daniel"})
    assert r.status_code == 200
    r = client.get("/api/status", params={"token": guest},
                   headers={"cf-connecting-ip": "203.0.113.9"})
    assert r.status_code == 401


def test_dueno_si_puede_administrar():
    # La puerta del dueño abre: sources/add con ruta inexistente da 400
    # (pasó la puerta, falló la validación), no 403.
    r = client.post("/api/antigravity/sources/add", params={"token": OWNER},
                    json={"path": "/ruta/que/no/existe/seguro"})
    assert r.status_code == 400


# --- 3. Rate-limit de join ------------------------------------------------

def test_join_rate_limit_tras_cinco_fallos():
    codigo, _eid = _crear_equipo()
    for _ in range(5):
        r = _unir("ATL-XXXXXX")
        assert r.status_code == 404
    r = _unir("ATL-XXXXXX")
    assert r.status_code == 429
    # Incluso un código válido queda bloqueado mientras dure la ventana.
    r = _unir(codigo)
    assert r.status_code == 429
    sync_teams_router._JOIN_FALLOS.clear()
    r = _unir(codigo)
    assert r.status_code == 200
    assert r.json()["guest_token"]


# --- 4. MCP solo-dueño vs invitado ----------------------------------------

def test_mcp_crear_borrar_start_stop_solo_dueno():
    codigo, _eid = _crear_equipo()
    guest = _unir(codigo).json()["guest_token"]
    # Invitado: crear/borrar/start/stop bloqueados antes de tocar nada.
    assert client.post("/api/mcps", params={"token": guest},
                       json={"name": "x"}).status_code == 403
    assert client.delete("/api/mcps/x",
                         params={"token": guest}).status_code == 403
    assert client.post("/api/mcps/x/start",
                       params={"token": guest}).status_code == 403
    assert client.post("/api/mcps/x/stop",
                       params={"token": guest}).status_code == 403
    # Invitado: listar sí puede (solo ve MCPs que el dueño aprobó).
    assert client.get("/api/mcps", params={"token": guest}).status_code == 200
    # Dueño: la puerta abre (crear sin nombre da 400 de validación, no 403).
    assert client.post("/api/mcps", params={"token": OWNER},
                       json={"name": ""}).status_code in (200, 400)


# --- 5. target_cwd validado -------------------------------------------------

def test_terminal_target_cwd_fuera_del_workspace_rechazado():
    r = client.post("/api/terminal/exec", params={"token": OWNER},
                    json={"cmd": "pwd", "target_cwd": "/etc"})
    assert r.status_code == 400


# --- 6. .git / .env bloqueados en ruta_segura ------------------------------

def test_archivo_git_y_env_bloqueados():
    # ".env.local" NO se bloquea: la regla es coincidencia exacta de
    # componente (".git" / ".env"), no prefijo.
    for ruta in (".git/config", ".git/hooks/pre-commit", ".env", "sub/.env"):
        r = client.post("/api/archivo",
                        params={"ruta": ruta, "token": OWNER},
                        json={"contenido": "secreto"})
        assert r.status_code == 400, f"{ruta} debió dar 400, dio {r.status_code}"
    r = client.get("/api/archivo", params={"ruta": ".env", "token": OWNER})
    assert r.status_code == 400


def test_archivo_normal_sigue_funcionando():
    r = client.post("/api/archivo",
                    params={"ruta": "seguridad_ok.txt", "token": OWNER},
                    json={"contenido": "hola"})
    assert r.status_code == 200
    r = client.get("/api/archivo", params={"ruta": "seguridad_ok.txt", "token": OWNER})
    assert r.status_code == 200
    assert "hola" in r.text
