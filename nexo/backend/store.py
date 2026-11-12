"""HubStore: fachada dict-compatible sobre SQLite.

El código del hub (y los tests) usan ``AI_STORE`` como un dict con
mutaciones anidadas, p. ej.::

    server.AI_STORE["pairings"][codigo]["expira"] = <iso>
    AI_STORE.setdefault("agent_bus", []).append(msg)

Esta fachada preserva EXACTAMENTE esa interfaz, pero cada operación lee
y escribe en SQLite (tablas ``pairings``, ``agent_bus``, ``chat_messages``,
``tasks``, ``file_versions``). Los dicts devueltos son proxies con
escritura directa (_RowDict): mutarlos actualiza la fila.

Claves no tabulares (p. ej. ``"agentes"``) viven en un overlay en memoria
con la misma semilla que antes.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any
from collections.abc import Callable, Iterator

from backend import db
from backend.models import AgentMessage, ChatMessage, FileVersion, Pairing, Task


class _RowDict(dict):
    """Dict cuya mutación se propaga a la BD (write-through)."""

    def __init__(self, data: dict, save: Callable[[dict], None]):
        super().__init__(data)
        object.__setattr__(self, "_save", save)

    def _commit(self) -> None:
        object.__getattribute__(self, "_save")(dict(self))

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self._commit()

    def __delitem__(self, k):
        super().__delitem__(k)
        self._commit()

    def update(self, *a, **kw):
        super().update(*a, **kw)
        self._commit()

    def pop(self, *a, **kw):
        r = super().pop(*a, **kw)
        self._commit()
        return r

    def clear(self):
        super().clear()
        self._commit()


def _row_upsert(model, pk_name: str, pk_val: str, data: dict) -> None:
    with db.session_scope() as s:
        row = s.query(model).filter(getattr(model, pk_name) == pk_val).first()
        payload = dict(data)
        if row is None:
            kwargs = {"data": payload}
            kwargs[pk_name] = pk_val
            if model is Pairing:
                kwargs["status"] = str(payload.get("estado", "pendiente"))
            s.add(model(**kwargs))
        else:
            row.data = payload
            if model is Pairing:
                row.status = str(payload.get("estado", row.status or "pendiente"))


def _row_delete(model, pk_name: str, pk_val: str) -> bool:
    with db.session_scope() as s:
        row = s.query(model).filter(getattr(model, pk_name) == pk_val).first()
        if row is None:
            return False
        s.delete(row)
        return True


def _row_get(model, pk_name: str, pk_val: str) -> dict | None:
    with db.session_scope() as s:
        row = s.query(model).filter(getattr(model, pk_name) == pk_val).first()
        return dict(row.data) if row is not None else None


class _DocMap(MutableMapping):
    """Mapping código/id -> dict con write-through, sobre una tabla documental."""

    def __init__(self, model, pk_name: str):
        self._model = model
        self._pk = pk_name

    def _codes(self) -> list[str]:
        with db.session_scope() as s:
            return [r[0] for r in s.query(getattr(self._model, self._pk)).all()]

    def __getitem__(self, key: str) -> _RowDict:
        data = _row_get(self._model, self._pk, key)
        if data is None:
            raise KeyError(key)
        return _RowDict(data, lambda d, k=key: _row_upsert(self._model, self._pk, k, d))

    def __setitem__(self, key: str, value: dict) -> None:
        _row_upsert(self._model, self._pk, key, dict(value))

    def __delitem__(self, key: str) -> None:
        if not _row_delete(self._model, self._pk, key):
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._codes())

    def __len__(self) -> int:
        return len(self._codes())


class _DocList:
    """Lista de documentos con write-through (append/inserta, iteración mutable)."""

    def __init__(self, model, id_field: str = "msg_id"):
        self._model = model
        self._id_field = id_field

    def _all_ids(self) -> list[str]:
        with db.session_scope() as s:
            rows = s.query(getattr(self._model, self._id_field)).order_by(
                self._model.seq).all()
            return [r[0] for r in rows]

    def append(self, msg: dict) -> _RowDict:
        """Inserta y devuelve el proxy mutable (para mutaciones posteriores)."""
        mid = str(msg.get("id", ""))
        data = dict(msg)
        with db.session_scope() as s:
            row = s.query(self._model).filter(
                getattr(self._model, self._id_field) == mid).first()
            if row is None:
                kwargs = {"data": data}
                kwargs[self._id_field] = mid
                s.add(self._model(**kwargs))
            else:
                row.data = data
        return _RowDict(data, lambda d, m=mid: _row_upsert(
            self._model, self._id_field, m, d))

    def __iter__(self) -> Iterator[_RowDict]:
        for mid in self._all_ids():
            data = _row_get(self._model, self._id_field, mid)
            if data is not None:
                yield _RowDict(data, lambda d, m=mid: _row_upsert(
                    self._model, self._id_field, m, d))

    def __len__(self) -> int:
        return len(self._all_ids())

    def __getitem__(self, idx):
        ids = self._all_ids()
        data = _row_get(self._model, self._id_field, ids[idx])
        return _RowDict(data or {}, lambda d, m=ids[idx]: _row_upsert(
            self._model, self._id_field, m, d))


class _VersionsMap(MutableMapping):
    """Mapping proyecto::ruta -> int (versión de archivo)."""

    def __getitem__(self, key: str) -> int:
        with db.session_scope() as s:
            row = s.query(FileVersion).filter_by(key=key).first()
            if row is None:
                raise KeyError(key)
            return int(row.version)

    def __setitem__(self, key: str, value: int) -> None:
        with db.session_scope() as s:
            row = s.query(FileVersion).filter_by(key=key).first()
            if row is None:
                s.add(FileVersion(key=key, version=int(value)))
            else:
                row.version = int(value)

    def __delitem__(self, key: str) -> None:
        with db.session_scope() as s:
            row = s.query(FileVersion).filter_by(key=key).first()
            if row is None:
                raise KeyError(key)
            s.delete(row)

    def __iter__(self) -> Iterator[str]:
        with db.session_scope() as s:
            return iter([r[0] for r in s.query(FileVersion.key).all()])

    def __len__(self) -> int:
        with db.session_scope() as s:
            return s.query(FileVersion).count()


class _AuthorsMap(MutableMapping):
    """Mapping proyecto::ruta -> autor (str)."""

    def __getitem__(self, key: str) -> str:
        with db.session_scope() as s:
            row = s.query(FileVersion).filter_by(key=key).first()
            if row is None:
                raise KeyError(key)
            return str(row.author or "")

    def __setitem__(self, key: str, value: str) -> None:
        with db.session_scope() as s:
            row = s.query(FileVersion).filter_by(key=key).first()
            if row is None:
                s.add(FileVersion(key=key, version=0, author=str(value)))
            else:
                row.author = str(value)

    def __delitem__(self, key: str) -> None:
        with db.session_scope() as s:
            row = s.query(FileVersion).filter_by(key=key).first()
            if row is None:
                raise KeyError(key)
            s.delete(row)

    def __iter__(self) -> Iterator[str]:
        with db.session_scope() as s:
            return iter([r[0] for r in s.query(FileVersion.key).all()])

    def __len__(self) -> int:
        with db.session_scope() as s:
            return s.query(FileVersion).count()


class HubStore(MutableMapping):
    """Reemplazo directo de AI_STORE: misma interfaz, persistencia en SQLite."""

    _TABLE_KEYS = ("pairings", "agent_bus", "mensajes", "tareas",
                   "file_versions", "file_version_authors")

    def __init__(self, agentes_seed: dict | None = None):
        self._overlay: dict[str, Any] = {}
        if agentes_seed:
            self._overlay["agentes"] = agentes_seed

    # -- vistas tabulares ------------------------------------------------
    def _view(self, key: str):
        if key == "pairings":
            return _DocMap(Pairing, "code")
        if key == "agent_bus":
            return _DocList(AgentMessage, "msg_id")
        if key == "mensajes":
            return _DocList(ChatMessage, "msg_id")
        if key == "tareas":
            return _DocList(Task, "task_id")
        if key == "file_versions":
            return _VersionsMap()
        if key == "file_version_authors":
            return _AuthorsMap()
        raise KeyError(key)

    def __getitem__(self, key: str):
        if key in self._TABLE_KEYS:
            return self._view(key)
        return self._overlay[key]

    def __setitem__(self, key: str, value) -> None:
        if key in self._TABLE_KEYS:
            raise TypeError(f"La clave tabular '{key}' se gestiona por vistas")
        self._overlay[key] = value

    def __delitem__(self, key: str) -> None:
        if key in self._TABLE_KEYS:
            raise TypeError(f"La clave tabular '{key}' se gestiona por vistas")
        del self._overlay[key]

    def __iter__(self) -> Iterator[str]:
        seen = list(self._TABLE_KEYS)
        for k in self._overlay:
            if k not in seen:
                seen.append(k)
        return iter(seen)

    def __len__(self) -> int:
        return len(list(iter(self)))

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            self[key] = default
            return default


_store: HubStore | None = None


def init_store(agentes_seed: dict | None = None) -> HubStore:
    """Crea el singleton (llamado una vez en create_app())."""
    global _store
    _store = HubStore(agentes_seed=agentes_seed)
    return _store


def get_store() -> HubStore:
    assert _store is not None, "init_store() no fue llamado"
    return _store
