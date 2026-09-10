"""CRDT-backed text documents for the shared editor."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from pycrdt import Doc, Text


class DocumentUpdate(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    update: str = Field(min_length=1, max_length=4_000_000)
    client_id: str = Field(min_length=1, max_length=128)


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise HTTPException(400, "update CRDT inválido") from exc


def install_document_routes(app: Any, hub: Any, auth: Any) -> None:
    with hub.lock, hub.db:
        hub.db.execute(
            "CREATE TABLE IF NOT EXISTS documents(path TEXT PRIMARY KEY,update_blob BLOB NOT NULL,text TEXT NOT NULL,revision INTEGER NOT NULL,updated_at REAL NOT NULL)"
        )

    @app.get("/v2/documents/read")
    async def document_read(path: str, member=Depends(auth)):
        target = hub.safe_path(path)
        with hub.lock:
            row = hub.db.execute("SELECT * FROM documents WHERE path=?", (path,)).fetchone()
        if row:
            return {
                "path": path,
                "text": row["text"],
                "update": _encode(row["update_blob"]),
                "revision": row["revision"],
            }
        if not target.is_file():
            raise HTTPException(404, "documento no encontrado")
        doc = Doc()
        text = doc.get("text", type=Text)
        text.insert(0, target.read_text(encoding="utf-8", errors="replace"))
        update = doc.get_update()
        digest = hashlib.sha256(update).hexdigest()
        with hub.lock, hub.db:
            hub.db.execute(
                "INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)",
                (path, update, str(text), 1, __import__("time").time()),
            )
        return {"path": path, "text": str(text), "update": _encode(update), "revision": 1, "content_revision": digest}

    @app.post("/v2/documents/update")
    async def document_update(body: DocumentUpdate, member=Depends(auth)):
        hub.safe_path(body.path)
        incoming = _decode(body.update)
        with hub.lock:
            row = hub.db.execute("SELECT * FROM documents WHERE path=?", (body.path,)).fetchone()
            doc = Doc()
            text = doc.get("text", type=Text)
            if row:
                doc.apply_update(bytes(row["update_blob"]))
                text = doc.get("text", type=Text)
            try:
                doc.apply_update(incoming)
            except Exception as exc:
                raise HTTPException(400, "update CRDT incompatible") from exc
            full = doc.get_update()
            content = str(text)
            revision = (row["revision"] if row else 0) + 1
            target = hub.safe_path(body.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            with hub.db:
                hub.db.execute(
                    "INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)",
                    (body.path, full, content, revision, __import__("time").time()),
                )
        await hub.publish(
            "document.updated",
            member["id"],
            {"path": body.path, "update": _encode(full), "revision": revision, "client_id": body.client_id},
        )
        return {"path": body.path, "text": content, "update": _encode(full), "revision": revision}
