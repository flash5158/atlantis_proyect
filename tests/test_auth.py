"""Tests del login real de Atlantis Studio (/api/auth/*).

Cubre: registro -> login -> me con token -> contraseña incorrecta 401 ->
duplicados 400 -> sin token 401 estricto -> logout invalida el token ->
sin cuentas inventadas en un hub nuevo.
"""

import os
import sys
import tempfile
from pathlib import Path

TMP_HOME = Path(tempfile.mkdtemp(prefix="atlantis-test-auth-home-"))
TMP_WS = Path(tempfile.mkdtemp(prefix="atlantis-test-auth-ws-"))
(TMP_WS / "proyectos").mkdir(exist_ok=True)

os.environ["HOME"] = str(TMP_HOME)
os.environ["NEXO_WORKSPACE"] = str(TMP_WS)
os.environ["NEXO_PUBLIC"] = "1"
os.environ["PORT"] = "18798"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

client = TestClient(server.app)


def test_hub_nuevo_sin_cuentas_inventadas():
    r = client.get("/api/auth/count")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_me_sin_token_es_401_estricto():
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    r = client.get("/api/auth/me", params={"token": "invalido"})
    assert r.status_code == 401


def test_registro_login_me_logout():
    # Registro
    r = client.post("/api/auth/register", json={
        "username": "amigo",
        "name": "Amigo",
        "email": "amigo@test.local",
        "password": "secreta123",
        "role": "Developer",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    token = body["token"]
    assert body["user"]["username"] == "amigo"
    assert body["user"]["name"] == "Amigo"
    assert "password_hash" not in body["user"]

    # /me con token válido
    r = client.get("/api/auth/me", params={"token": token})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "amigo"

    # /me con Bearer header también funciona
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # Login con contraseña incorrecta -> 401
    r = client.post("/api/auth/login", json={"identifier": "amigo", "password": "mal"})
    assert r.status_code == 401

    # Login con email funciona
    r = client.post("/api/auth/login", json={"identifier": "amigo@test.local", "password": "secreta123"})
    assert r.status_code == 200
    token2 = r.json()["token"]
    assert token2 != token

    # Logout invalida el token
    r = client.post("/api/auth/logout", params={"token": token2})
    assert r.status_code == 200
    r = client.get("/api/auth/me", params={"token": token2})
    assert r.status_code == 401
    # El primer token sigue válido (logout solo invalida el suyo)
    r = client.get("/api/auth/me", params={"token": token})
    assert r.status_code == 200


def test_registro_duplicado_400():
    client.post("/api/auth/register", json={
        "username": "repe", "email": "repe@test.local", "password": "abc12345",
    })
    r = client.post("/api/auth/register", json={
        "username": "repe", "email": "otro@test.local", "password": "abc12345",
    })
    assert r.status_code == 400
    r = client.post("/api/auth/register", json={
        "username": "otro2", "email": "repe@test.local", "password": "abc12345",
    })
    assert r.status_code == 400


def test_login_usuario_inexistente_404():
    r = client.post("/api/auth/login", json={"identifier": "nadie", "password": "x"})
    assert r.status_code == 404


def test_registro_validaciones():
    r = client.post("/api/auth/register", json={"username": "ab", "email": "a@b.c", "password": "1234"})
    assert r.status_code == 400
    r = client.post("/api/auth/register", json={"username": "okuser", "email": "sin-arroba", "password": "1234"})
    assert r.status_code == 400
