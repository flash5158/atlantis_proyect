"""Tests del refactor a backend real (paquete nexo/backend/).

Cubre: estructura del paquete (sin tocar los 39 tests existentes),
migración idempotente de accounts.json -> SQLite, y persistencia real
del HubStore en la BD.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

NEXO_DIR = Path(__file__).resolve().parent.parent / "nexo"
sys.path.insert(0, str(NEXO_DIR))


def test_estructura_paquete_backend():
    """El paquete backend existe con la arquitectura objetivo."""
    import backend.core.config
    import backend.core.security
    import backend.core.state
    import backend.db
    import backend.main
    import backend.models
    import backend.store
    from backend.main import create_app

    for mod in ("auth", "files", "agents", "mcp", "terminal", "tunnel", "social", "git", "status", "landing"):
        router_mod = __import__(f"backend.routers.{mod}", fromlist=["router"])
        assert hasattr(router_mod, "router"), f"routers/{mod}.py sin APIRouter"

    assert callable(create_app)
    # create_app es idempotente a nivel de importación (ya corrido por server);
    # standalone, inicializar el store con un HOME temporal para no tocar el real.
    import tempfile
    _old_home = os.environ.get("HOME")
    os.environ["HOME"] = tempfile.mkdtemp(prefix="atlantis-struct-test-")
    try:
        backend.store.init_store()
        assert hasattr(backend.store.get_store(), "get")
    finally:
        if _old_home is not None:
            os.environ["HOME"] = _old_home


def test_hubstore_es_fachada_dict_compatible():
    """AI_STORE conserva la interfaz dict que usan los tests viejos."""
    sys.path.insert(0, str(NEXO_DIR))
    import server

    # setitem/getitem a nivel de clave tabular no tabular
    server.AI_STORE["clave_efimera"] = {"a": 1}
    assert server.AI_STORE["clave_efimera"] == {"a": 1}
    del server.AI_STORE["clave_efimera"]

    # vista de agent_bus con write-through (el bus persiste igual que
    # persistían los pairings: la tabla Pairing quedó sin uso).
    bus = server.AI_STORE["agent_bus"]
    bus.append({"id": "bus-test99", "de": "Koko", "para": "todos",
                "texto": "hola", "tipo": "mensaje", "estado": "pendiente"})
    assert any(m["id"] == "bus-test99" for m in server.AI_STORE["agent_bus"])
    for m in server.AI_STORE["agent_bus"]:
        if m["id"] == "bus-test99":
            m["estado"] = "completado"
    assert next(m for m in server.AI_STORE["agent_bus"]
                if m["id"] == "bus-test99")["estado"] == "completado"


def test_hubstore_persiste_en_sqlite():
    """Lo que entra al HubStore queda en hub.db (no es memoria)."""
    sys.path.insert(0, str(NEXO_DIR))
    import server
    from backend.core.config import config

    bus = server.AI_STORE["agent_bus"]
    bus.append({"id": "bus-persist", "de": "Koko", "para": "todos",
                "texto": "persistir", "tipo": "mensaje", "estado": "pendiente"})
    try:
        db_path = config.ATLANTIS_USER_DIR / "hub.db"
        assert db_path.exists(), "hub.db no existe"
        con = sqlite3.connect(str(db_path))
        fila = con.execute(
            "SELECT msg_id FROM agent_bus WHERE msg_id = 'bus-persist'").fetchone()
        con.close()
        assert fila is not None, "el mensaje del bus no se persistió en SQLite"
        assert fila[0] == "bus-persist"
    finally:
        resto = [m for m in server.AI_STORE["agent_bus"] if m["id"] != "bus-persist"]
        # _DocList no tiene clear por índice: reescribir vía delete no
        # aplica; se deja el registro (es un store de test temporal).


def test_migracion_accounts_json_legacy():
    """Una cuenta del accounts.json viejo migra a la BD y el login funciona.

    Corre en subproceso para no contaminar el server compartido de los tests.
    """
    tmp_home = Path(tempfile.mkdtemp(prefix="atlantis-test-mig-home-"))
    salt = "saltsaltsaltsalt"
    pw = "clavelegacy123"
    h = hashlib.sha256((salt + pw).encode()).hexdigest()
    legacy = [{
        "id": "usr_legacy99", "username": "legado", "email": "legado@test.local",
        "name": "Legado", "role": "Developer",
        "password_hash": h, "salt": salt, "avatar": "LE",
        "created_at": "2026-01-01T00:00:00",
    }]
    (tmp_home / ".atlantis").mkdir(parents=True, exist_ok=True)
    (tmp_home / ".atlantis" / "accounts.json").write_text(json.dumps(legacy))

    nexo_r, pw_r = repr(str(NEXO_DIR)), repr(pw)
    script = (
        f"import sys; sys.path.insert(0, {nexo_r});"
        "import server;"
        "from fastapi.testclient import TestClient;"
        "c = TestClient(server.app);"
        f"r = c.post('/api/auth/login', json={{'identifier': 'legado', 'password': {pw_r}}});"
        "assert r.status_code == 200, r.text;"
        "u = r.json()['user'];"
        "assert u['username'] == 'legado' and u['id'] == 'usr_legacy99' and u['avatar'] == 'LE';"
        "r = c.get('/api/auth/count');"
        "assert r.json()['count'] == 1;"
        "print('MIGRACION OK')"
    )

    env = dict(os.environ, HOME=str(tmp_home), NEXO_PUBLIC="1", PORT="18799")
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        env=env, timeout=60, cwd=str(NEXO_DIR),
    )
    assert "MIGRACION OK" in proc.stdout, f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    # El JSON legado queda intacto (solo lectura tras migrar)
    assert json.loads((tmp_home / ".atlantis" / "accounts.json").read_text())[0]["username"] == "legado"


def test_global_exception_handler_no_traceback():
    """Un error no manejado devuelve forma consistente sin filtrar tracebacks."""
    from fastapi.testclient import TestClient
    from backend.main import create_app
    app = create_app()

    @app.get("/api/_test_boom")
    async def _boom():
        raise RuntimeError("secreto interno: /home/x/clave")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/_test_boom")
    assert r.status_code == 500
    body = r.json()
    assert body.get("ok") is False
    assert "secreto interno" not in body.get("error", "")
    assert "traceback" not in body.get("error", "").lower()
