"""Modelos SQLAlchemy del hub.

Toda la persistencia del hub vive en SQLite (WAL). Los modelos de
contenido colaborativo (Pairing, AgentMessage, ChatMessage, Task)
guardan el documento completo en la columna ``data`` (JSON) para
preservar EXACTAMENTE las formas que la API devuelve hoy; las columnas
extra solo indexan lo necesario.
"""

from __future__ import annotations

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Meta(Base):
    """Clave/valor interno (marcadores de migración, etc.)."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), default="")


class User(Base):
    """Cuenta de usuario del hub (antes: ~/.atlantis/accounts.json)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # usr_<hex>
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(64))
    avatar: Mapped[str] = mapped_column(String(8), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")

    def to_public(self) -> dict:
        """Forma pública (sin secretos), idéntica a la que devolvía el JSON."""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "avatar": self.avatar,
            "created_at": self.created_at,
        }


class Session(Base):
    """Sesión de login (antes: dict en memoria ACTIVE_SESSIONS)."""

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[str] = mapped_column(String(32), default="")


class Pairing(Base):
    """Código de vinculación entre los dos agentes (antes: AI_STORE["pairings"])."""

    __tablename__ = "pairings"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)  # ATL-XXXXXX
    status: Mapped[str] = mapped_column(String(16), index=True, default="pendiente")
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentMessage(Base):
    """Mensaje del bus agente<->agente (antes: AI_STORE["agent_bus"])."""

    __tablename__ = "agent_bus"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    msg_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ChatMessage(Base):
    """Mensaje de IA / inbox (antes: AI_STORE["mensajes"])."""

    __tablename__ = "chat_messages"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    msg_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Task(Base):
    """Tarea colaborativa (antes: AI_STORE["tareas"])."""

    __tablename__ = "tasks"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class FileVersion(Base):
    """Versión + autor por archivo (antes: AI_STORE["file_versions"])."""

    __tablename__ = "file_versions"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)  # proyecto::ruta
    version: Mapped[int] = mapped_column(Integer, default=0)
    author: Mapped[str] = mapped_column(String(128), default="")


class BusCursor(Base):
    """Último seq del bus entregado a un agente de un equipo.

    Clave (equipo_id, agente): al (re)conectar, el agente recibe los
    mensajes de su equipo con seq > last_seq, en orden, y el cursor
    avanza. Sin duplicados, sin pérdidas por desconexión."""

    __tablename__ = "bus_cursors"

    equipo_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agente: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
