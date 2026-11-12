"""Capa de base de datos: SQLAlchemy 2.0 + SQLite en modo WAL (local)
o Postgres vía ``DATABASE_URL`` (hub central).

- Sin ``DATABASE_URL``: SQLite en ``~/.atlantis/hub.db`` (hogar del hub,
  junto al antiguo ``accounts.json``): el estado del hub no depende del
  workspace activo y sobrevive a ``/api/workspace/select``.
- Con ``DATABASE_URL`` que empieza por ``postgres``: Postgres (pooler
  Supabase en producción). Render es solo-IPv4, así que la URL debe ser
  la del pooler (puerto 6543, modo transacción) y el pool debe ser
  ``NullPool``: el pooler transaccional no tolera conexiones retenidas
  en un pool del lado del cliente.
- ``init_db()`` crea tablas y ejecuta la migración única del JSON
  legado (idempotente, con marcador en la tabla ``meta``).
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from backend.core.config import config
from backend.models import Base, Meta, User


def _db_path() -> Path:
    return config.ATLANTIS_USER_DIR / "hub.db"


def _es_postgres() -> bool:
    return os.environ.get("DATABASE_URL", "").strip().lower().startswith("postgres")


def _normalizar_url_postgres(url: str) -> str:
    """postgres:// o postgresql:// → postgresql+psycopg:// (idempotente)."""
    return re.sub(r"^postgres(ql)?(\+psycopg)?://", "postgresql+psycopg://",
                  url.strip(), flags=re.IGNORECASE)


def _make_engine():
    if _es_postgres():
        # Sin StaticPool, sin WAL, sin check_same_thread: NullPool deja
        # que el pooler transaccional de Supabase gestione las conexiones.
        return create_engine(
            _normalizar_url_postgres(os.environ["DATABASE_URL"]),
            poolclass=NullPool,
            connect_args={"sslmode": "require", "connect_timeout": 10},
        )
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False + StaticPool: una sola conexión para todo
    # el proceso (el TestClient y los websockets comparten hilos).
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


engine = None
SessionLocal: sessionmaker | None = None


def _enable_wal() -> None:
    assert engine is not None
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")


def init_db() -> None:
    """Crea el engine, las tablas y corre la migración. Llamado en create_app()."""
    global engine, SessionLocal
    if engine is not None:
        return
    engine = _make_engine()
    if not _es_postgres():
        _enable_wal()
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    _migrate_accounts_json()


def _migrate_accounts_json() -> None:
    """Importa ~/.atlantis/accounts.json a la tabla users, una sola vez.

    Idempotente: el marcador ``meta.accounts_migrated`` evita reimportar.
    El JSON queda como legado de solo lectura (no se vuelve a escribir).
    """
    assert SessionLocal is not None
    with session_scope() as s:
        if s.get(Meta, "accounts_migrated") is not None:
            return
        raw: list = []
        legacy = config.ACCOUNTS_FILE
        if legacy.exists():
            try:
                raw = json.loads(legacy.read_text(encoding="utf-8")) or []
            except Exception as e:
                print(f"[DB] Aviso: no se pudo leer {legacy}: {e}")
        importados = 0
        for c in raw:
            if not isinstance(c, dict):
                continue
            username = str(c.get("username", "")).strip().lower()
            if not username:
                continue
            if s.query(User).filter_by(username=username).first():
                continue
            s.add(User(
                id=str(c.get("id", f"usr_{username}")),
                username=username,
                email=str(c.get("email", "")).strip().lower(),
                name=str(c.get("name", username.capitalize())),
                role=str(c.get("role", "")),
                password_hash=str(c.get("password_hash", "")),
                salt=str(c.get("salt", "")),
                avatar=str(c.get("avatar", "")),
                created_at=str(c.get("created_at", "")),
            ))
            importados += 1
        s.add(Meta(key="accounts_migrated", value="1"))
        if importados:
            print(f"[DB] Migración: {importados} cuenta(s) importadas desde accounts.json")


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión corta con commit/rollback automáticos."""
    assert SessionLocal is not None, "init_db() no fue llamado"
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """Dependencia FastAPI (si algún endpoint la necesita directa)."""
    with session_scope() as s:
        yield s
