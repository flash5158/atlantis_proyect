"""Seguridad: hash y verificación de contraseñas.

Movido intacto desde server.py (sha256 con salt). Sin cambios de
comportamiento: los hashes existentes en accounts.json siguen válidos
tras la migración a la BD.
"""

from __future__ import annotations

import hashlib
import secrets


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest() == hashed
