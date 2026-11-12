"""Router de autenticación — cuentas Atlantis.

Mismas rutas, métodos, validaciones y formas de respuesta que antes;
la persistencia cambió de ``~/.atlantis/accounts.json`` a SQLite
(tabla ``users``) y las sesiones de un dict en memoria a la tabla
``sessions``. El JSON legado se migra una sola vez en ``db.init_db()``.
"""
from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func

from backend.core.security import hash_password, verify_password
from backend.db import session_scope
from backend.models import Session, User

router = APIRouter()


def _token_de(request: Request, token: str) -> str:
    """Token desde ?token= o cabecera Authorization: Bearer."""
    auth_header = request.headers.get("authorization", "")
    return token or (auth_header.split(" ")[1] if "Bearer " in auth_header else "")


@router.post("/api/auth/register")
async def api_auth_register(request: Request):
    body = await request.json()
    username = str(body.get("username", "")).strip().lower()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    name = str(body.get("name", "")).strip() or username.capitalize()
    role = str(body.get("role", "Lead Developer / Arquitecto")).strip()

    if not username or len(username) < 3:
        return JSONResponse(
            {"error": "El nombre de usuario debe tener al menos 3 caracteres."},
            status_code=400,
        )
    if not email or "@" not in email:
        return JSONResponse(
            {"error": "Introduce un correo electrónico válido."},
            status_code=400,
        )
    if not password or len(password) < 4:
        return JSONResponse(
            {"error": "La contraseña debe tener al menos 4 caracteres."},
            status_code=400,
        )

    with session_scope() as s:
        if s.query(User).filter(func.lower(User.username) == username).first():
            return JSONResponse(
                {"error": f"El usuario '{username}' ya está registrado."},
                status_code=400,
            )
        if s.query(User).filter(func.lower(User.email) == email).first():
            return JSONResponse(
                {"error": f"El correo '{email}' ya está registrado."},
                status_code=400,
            )

        password_hash, salt = hash_password(password)
        initials = "".join([p[0] for p in name.split()[:2]]).upper() or username[:2].upper()
        user_id = f"usr_{secrets.token_hex(6)}"
        ahora = datetime.now().isoformat()
        user = User(
            id=user_id,
            username=username,
            email=email,
            name=name,
            role=role,
            password_hash=password_hash,
            salt=salt,
            avatar=initials,
            created_at=ahora,
        )
        s.add(user)
        token = secrets.token_hex(32)
        s.add(Session(token=token, user_id=user_id, created_at=ahora))
        user_public = user.to_public()

    return {
        "ok": True,
        "message": f"Cuenta '{username}' creada exitosamente.",
        "token": token,
        "user": user_public,
    }


@router.post("/api/auth/login")
async def api_auth_login(request: Request):
    body = await request.json()
    identifier = str(body.get("identifier", "")).strip().lower()
    password = str(body.get("password", ""))

    if not identifier or not password:
        return JSONResponse(
            {"error": "Introduce usuario/correo y contraseña."},
            status_code=400,
        )

    with session_scope() as s:
        user = (
            s.query(User)
            .filter(
                (func.lower(User.username) == identifier)
                | (func.lower(User.email) == identifier)
            )
            .first()
        )
        if not user:
            return JSONResponse(
                {"error": "Usuario o correo no encontrado."},
                status_code=404,
            )
        if not verify_password(password, user.password_hash, user.salt):
            return JSONResponse(
                {"error": "Contraseña incorrecta."},
                status_code=401,
            )

        token = secrets.token_hex(32)
        ahora = datetime.now().isoformat()
        s.add(Session(token=token, user_id=user.id, created_at=ahora))
        user_public = user.to_public()

    return {
        "ok": True,
        "message": f"Bienvenido, {user_public.get('name')}.",
        "token": token,
        "user": user_public,
    }


@router.get("/api/auth/me")
async def api_auth_me(request: Request, token: str = Query("")):
    """Devuelve el usuario de la sesión o 401 estricto (sin cuenta backdoor)."""
    tok = _token_de(request, token)
    with session_scope() as s:
        sess = s.query(Session).filter_by(token=tok).first() if tok else None
        user = s.query(User).filter_by(id=sess.user_id).first() if sess else None
        if not user:
            return JSONResponse({"error": "No autenticado"}, status_code=401)
        return {"ok": True, "user": user.to_public()}


@router.get("/api/auth/count")
async def api_auth_count():
    """Número de cuentas registradas (el frontend decide si mostrar login o registro)."""
    try:
        with session_scope() as s:
            n = s.query(User).count()
    except Exception:
        n = 0
    return {"ok": True, "count": n}


@router.post("/api/auth/logout")
async def api_auth_logout(request: Request, token: str = Query("")):
    tok = _token_de(request, token)
    if tok:
        with session_scope() as s:
            s.query(Session).filter_by(token=tok).delete()
    return {"ok": True, "message": "Sesión cerrada correctamente."}
