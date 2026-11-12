"""Tests de la selección de engine SQLite vs Postgres (db.py).

Sin DATABASE_URL: SQLite exactamente como antes (StaticPool,
check_same_thread=False). Con DATABASE_URL postgres: engine Postgres con
NullPool + sslmode=require + connect_timeout=10, sin tocar SQLite y sin
conectar a ningún servidor real (create_engine mockeado).
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool, StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nexo"))

from backend import db as db_mod  # noqa: E402

POOLER = ("postgresql+psycopg://postgres.ref:secreto"
          "@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require")


@pytest.fixture
def fake_engine(monkeypatch):
    llamadas = {}

    def falso(url, **kwargs):
        llamadas["url"] = url
        llamadas.update(kwargs)
        return object()

    monkeypatch.setattr(db_mod, "create_engine", falso)
    return llamadas


def test_postgres_usa_nullpool_y_ssl(monkeypatch, fake_engine):
    monkeypatch.setenv("DATABASE_URL", POOLER)
    db_mod._make_engine()
    assert fake_engine["url"] == POOLER
    assert fake_engine["poolclass"] is NullPool
    assert fake_engine["connect_args"] == {"sslmode": "require", "connect_timeout": 10}


def test_postgres_normaliza_esquema(monkeypatch, fake_engine):
    # postgres:// y postgresql:// → postgresql+psycopg://
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://postgres.ref:secreto@aws-0-us-east-1.pooler.supabase.com:6543/postgres")
    db_mod._make_engine()
    assert fake_engine["url"].startswith("postgresql+psycopg://")
    assert "pooler.supabase.com:6543" in fake_engine["url"]

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres.ref:secreto@aws-0-us-east-1.pooler.supabase.com:6543/postgres")
    db_mod._make_engine()
    assert fake_engine["url"].startswith("postgresql+psycopg://")


def test_sin_database_url_sigue_sqlite(monkeypatch, fake_engine):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_mod._make_engine()
    assert fake_engine["url"].startswith("sqlite:///")
    assert fake_engine["poolclass"] is StaticPool
    assert fake_engine["connect_args"] == {"check_same_thread": False}
