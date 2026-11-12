"""Dependencias compartidas de los routers."""

from __future__ import annotations

from backend.db import get_db, session_scope

__all__ = ["get_db", "session_scope"]
