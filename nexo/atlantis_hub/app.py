"""Authenticated, durable Atlantis collaboration hub."""
# The compact endpoint declarations below keep each route's policy together.
# E701/E702 are intentionally scoped away from the legacy CI formatter.
# ruff: noqa: E701, E702

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .models import CompleteInput, FileWrite, Invite, MessageInput, TaskInput, as_dict
from .documents import install_document_routes


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Hub:
    def __init__(self, workspace: Path, data_dir: Path, owner_token: str, owner_name: str):
        self.workspace = workspace.resolve()
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.data_dir / "hub.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = RLock()
        self.subscribers: set[asyncio.Queue] = set()
        self.presence: dict[str, dict[str, Any]] = {}
        self.owner_token = owner_token
        self.owner_id = "member-owner"
        self._init_db(owner_name)

    def _init_db(self, owner_name: str) -> None:
        with self.lock, self.db:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,
              token_hash TEXT UNIQUE NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,
              type TEXT NOT NULL,actor_id TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,client_id TEXT UNIQUE NOT NULL,channel TEXT NOT NULL,
              actor_id TEXT NOT NULL,text TEXT NOT NULL,task_id TEXT,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,client_id TEXT UNIQUE NOT NULL,title TEXT NOT NULL,
              description TEXT NOT NULL,assignee_id TEXT NOT NULL,paths TEXT NOT NULL,depends_on TEXT NOT NULL,
              status TEXT NOT NULL,lease_id TEXT,lease_until REAL,created_by TEXT NOT NULL,result TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS file_revisions(path TEXT PRIMARY KEY,revision TEXT NOT NULL,updated_at REAL NOT NULL);
            """)
            if not self.db.execute("SELECT 1 FROM members WHERE id=?", (self.owner_id,)).fetchone():
                self.db.execute(
                    "INSERT INTO members VALUES(?,?,?,?,1,?)",
                    (self.owner_id, owner_name, "owner", _hash(self.owner_token), time.time()),
                )

    def member(self, token: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM members WHERE token_hash=? AND active=1", (_hash(token),)).fetchone()
        return dict(row) if row else None

    def safe_path(self, path: str) -> Path:
        if not path or "\\" in path or Path(path).is_absolute():
            raise HTTPException(400, "ruta inválida")
        clean = Path(path)
        if any(part in {".git", ".atlantis", ".env"} or part.startswith(".env.") for part in clean.parts):
            raise HTTPException(403, "ruta protegida")
        result = (self.workspace / clean).resolve()
        if result != self.workspace and self.workspace not in result.parents:
            raise HTTPException(403, "ruta fuera del workspace")
        return result

    def revision(self, path: str) -> str:
        target = self.safe_path(path)
        if not target.exists() or not target.is_file():
            return ""
        return hashlib.sha256(target.read_bytes()).hexdigest()

    async def publish(self, kind: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = {
            "v": 1,
            "id": secrets.token_hex(12),
            "type": kind,
            "actor_id": actor,
            "payload": payload,
            "created_at": time.time(),
        }
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO events(id,type,actor_id,payload,created_at) VALUES(?,?,?,?,?)",
                (event["id"], kind, actor, json.dumps(payload), event["created_at"]),
            )
            event["seq"] = cur.lastrowid
        stale = []
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.subscribers.discard(queue)
        return event

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM events WHERE seq>? ORDER BY seq", (seq,)).fetchall()
        return [
            {
                "v": 1,
                "id": r["id"],
                "seq": r["seq"],
                "type": r["type"],
                "actor_id": r["actor_id"],
                "payload": json.loads(r["payload"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def task(self, row: sqlite3.Row) -> dict[str, Any]:
        result = json.loads(row["result"]) if row["result"] else None
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "assignee_id": row["assignee_id"],
            "paths": json.loads(row["paths"]),
            "depends_on": json.loads(row["depends_on"]),
            "status": row["status"],
            "lease_id": row["lease_id"],
            "lease_until": row["lease_until"],
            "created_by": row["created_by"],
            "result": result,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def state(self, member: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            messages = [
                dict(r)
                for r in self.db.execute(
                    "SELECT id,client_id,channel,actor_id,text,task_id,created_at FROM messages ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            ]
            tasks = [
                self.task(r)
                for r in self.db.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 100").fetchall()
            ]
            members = [
                dict(r)
                for r in self.db.execute("SELECT id,name,role,active,created_at FROM members WHERE active=1").fetchall()
            ]
            seq = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
        files = []
        for item in sorted(self.workspace.rglob("*")):
            if item.is_file() and not any(
                part in {".git", ".atlantis"} or part.startswith(".env")
                for part in item.relative_to(self.workspace).parts
            ):
                files.append(str(item.relative_to(self.workspace)))
        return {
            "self": {"id": member["id"], "name": member["name"], "role": member["role"]},
            "members": members,
            "presence": list(self.presence.values()),
            "messages": list(reversed(messages)),
            "tasks": tasks,
            "files": files[:5000],
            "seq": seq,
        }


class CancelInput(BaseModel):
    reason: str = Field(default="cancelled", max_length=1000)


def create_app(workspace: Path, data_dir: Path, owner_token: str, owner_name: str = "Daniel") -> FastAPI:
    hub = Hub(workspace, data_dir, owner_token, owner_name)
    app = FastAPI(title="Atlantis Collaboration Hub", version="0.1.0")
    app.state.hub = hub
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    async def auth(request: Request) -> dict[str, Any]:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(401, "Bearer token requerido")
        member = hub.member(header[7:].strip())
        if not member:
            raise HTTPException(401, "token inválido")
        return member

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "atlantis-hub", "version": app.version}

    @app.get("/v2/state")
    async def state(member=__import__("fastapi").Depends(auth)):
        return hub.state(member)

    @app.post("/v2/invites")
    async def invite(body: Invite, member=__import__("fastapi").Depends(auth)):
        token = secrets.token_urlsafe(32)
        ident = f"member-{secrets.token_hex(8)}"
        with hub.lock, hub.db:
            hub.db.execute(
                "INSERT INTO members VALUES(?,?,?,?,1,?)", (ident, body.name, body.role, _hash(token), time.time())
            )
        await hub.publish("member.invited", member["id"], {"id": ident, "name": body.name, "role": body.role})
        return {"member": {"id": ident, "name": body.name, "role": body.role}, "token": token}

    @app.post("/v2/messages")
    async def message(body: MessageInput, member=__import__("fastapi").Depends(auth)):
        ident = secrets.token_hex(12)
        now = time.time()
        with hub.lock, hub.db:
            old = hub.db.execute("SELECT * FROM messages WHERE client_id=?", (body.client_id,)).fetchone()
            if old:
                return {"id": old["id"], "duplicate": True}
            hub.db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (ident, body.client_id, body.channel, member["id"], body.text, body.task_id, now),
            )
        await hub.publish(
            "message.created",
            member["id"],
            {
                "id": ident,
                "client_id": body.client_id,
                "channel": body.channel,
                "text": body.text,
                "task_id": body.task_id,
                "created_at": now,
            },
        )
        return {"id": ident, "duplicate": False}

    @app.post("/v2/tasks")
    async def task_create(body: TaskInput, member=__import__("fastapi").Depends(auth)):
        if not hub.db.execute("SELECT 1 FROM members WHERE id=? AND active=1", (body.assignee_id,)).fetchone():
            raise HTTPException(400, "assignee inválido")
        for path in body.paths:
            hub.safe_path(path)
        ident = secrets.token_hex(12)
        now = time.time()
        with hub.lock, hub.db:
            old = hub.db.execute("SELECT * FROM tasks WHERE client_id=?", (body.client_id,)).fetchone()
            if old:
                return hub.task(old) | {"duplicate": True}
            hub.db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    body.client_id,
                    body.title,
                    body.description,
                    body.assignee_id,
                    json.dumps(body.paths),
                    json.dumps(body.depends_on),
                    "pending",
                    None,
                    None,
                    member["id"],
                    None,
                    now,
                    now,
                ),
            )
        await hub.publish(
            "task.created",
            member["id"],
            {"id": ident, "title": body.title, "assignee_id": body.assignee_id, "paths": body.paths},
        )
        return {"id": ident, "status": "pending", "duplicate": False}

    @app.post("/v2/tasks/{task_id}/cancel")
    async def task_cancel(task_id: str, body: CancelInput, member=__import__("fastapi").Depends(auth)):
        with hub.lock, hub.db:
            row = hub.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise HTTPException(404, "tarea no encontrada")
            hub.db.execute(
                "UPDATE tasks SET status='cancel_requested',updated_at=? WHERE id=? AND status IN ('pending','running')",
                (time.time(), task_id),
            )
        await hub.publish("task.cancel_requested", member["id"], {"task_id": task_id, "reason": body.reason})
        return {"ok": True}

    @app.post("/v2/tasks/{task_id}/retry")
    async def task_retry(task_id: str, member=__import__("fastapi").Depends(auth)):
        with hub.lock, hub.db:
            row = hub.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise HTTPException(404, "tarea no encontrada")
            hub.db.execute(
                "UPDATE tasks SET status='pending',lease_id=NULL,lease_until=NULL,result=NULL,updated_at=? WHERE id=? AND status IN ('failed','interrupted','conflict')",
                (time.time(), task_id),
            )
        await hub.publish("task.retried", member["id"], {"task_id": task_id})
        return {"ok": True}

    @app.post("/v2/worker/claim")
    async def worker_claim(member=__import__("fastapi").Depends(auth)):
        if member["role"] not in {"worker", "owner"}:
            raise HTTPException(403, "solo workers")
        now = time.time()
        with hub.lock, hub.db:
            hub.db.execute(
                "UPDATE tasks SET status='interrupted',lease_id=NULL,lease_until=NULL,updated_at=? WHERE status='running' AND lease_until<?",
                (now, now),
            )
            row = hub.db.execute(
                "SELECT * FROM tasks WHERE assignee_id=? AND status='pending' ORDER BY created_at LIMIT 1",
                (member["id"],),
            ).fetchone()
            if not row:
                return {"task": None}
            lease = secrets.token_urlsafe(18)
            hub.db.execute(
                "UPDATE tasks SET status='running',lease_id=?,lease_until=?,updated_at=? WHERE id=?",
                (lease, now + 90, now, row["id"]),
            )
            result = hub.task(hub.db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())
        await hub.publish("task.claimed", member["id"], {"task_id": result["id"], "lease_id": lease})
        return {"task": result | {"lease_id": lease}}

    async def worker_task(task_id: str, member: dict[str, Any], lease_id: str) -> sqlite3.Row:
        row = hub.db.execute("SELECT * FROM tasks WHERE id=? AND assignee_id=?", (task_id, member["id"])).fetchone()
        if not row or row["lease_id"] != lease_id:
            raise HTTPException(409, "lease inválido")
        return row

    @app.post("/v2/worker/tasks/{task_id}/heartbeat")
    async def worker_heartbeat(task_id: str, body: dict[str, Any], member=__import__("fastapi").Depends(auth)):
        lease = str(body.get("lease_id", ""))
        await worker_task(task_id, member, lease)
        with hub.lock, hub.db:
            hub.db.execute(
                "UPDATE tasks SET lease_until=?,updated_at=? WHERE id=?", (time.time() + 90, time.time(), task_id)
            )
        return {
            "ok": True,
            "cancel_requested": bool(
                hub.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0] == "cancel_requested"
            ),
        }

    @app.post("/v2/worker/tasks/{task_id}/log")
    async def worker_log(task_id: str, body: dict[str, Any], member=__import__("fastapi").Depends(auth)):
        lease = str(body.get("lease_id", ""))
        await worker_task(task_id, member, lease)
        text = str(body.get("text", ""))[:8192]
        await hub.publish("task.log", member["id"], {"task_id": task_id, "text": text})
        return {"ok": True}

    @app.post("/v2/worker/tasks/{task_id}/complete")
    async def worker_complete(task_id: str, body: CompleteInput, member=__import__("fastapi").Depends(auth)):
        await worker_task(task_id, member, body.run_id)
        handoffs = [as_dict(x) for x in body.handoffs]
        if len(handoffs) > 4:
            raise HTTPException(400, "demasiados handoffs")
        result = as_dict(body)
        now = time.time()
        with hub.lock, hub.db:
            hub.db.execute(
                "UPDATE tasks SET status=?,lease_id=NULL,lease_until=NULL,result=?,updated_at=? WHERE id=?",
                (body.status, json.dumps(result), now, task_id),
            )
        await hub.publish(
            "task.completed",
            member["id"],
            {
                "task_id": task_id,
                "status": body.status,
                "summary": body.summary,
                "diff": body.diff[:262144],
                "verifications": body.verifications,
            },
        )
        return {"ok": True, "status": body.status}

    @app.get("/v2/files")
    async def files(member=__import__("fastapi").Depends(auth)):
        return {"files": hub.state(member)["files"]}

    @app.get("/v2/files/read")
    async def file_read(path: str, member=__import__("fastapi").Depends(auth)):
        target = hub.safe_path(path)
        if not target.is_file():
            raise HTTPException(404, "archivo no encontrado")
        return {
            "path": path,
            "text": target.read_text(encoding="utf-8", errors="replace"),
            "revision": hub.revision(path),
        }

    @app.post("/v2/files/write")
    async def file_write(body: FileWrite, member=__import__("fastapi").Depends(auth)):
        target = hub.safe_path(body.path)
        current = hub.revision(body.path)
        if body.expected_revision is None and target.exists():
            raise HTTPException(409, "falta revisión base")
        if body.expected_revision is not None and body.expected_revision != current:
            raise HTTPException(409, "conflicto de revisión")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.text, encoding="utf-8")
        revision = hub.revision(body.path)
        await hub.publish("file.updated", member["id"], {"path": body.path, "revision": revision})
        return {"path": body.path, "revision": revision}

    install_document_routes(app, hub, auth)

    @app.websocket("/v2/events")
    async def events(ws: WebSocket):
        await ws.accept()
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
            member = hub.member(str(hello.get("token", "")))
            if not member:
                await ws.close(code=4401)
                return
            hub.presence[member["id"]] = {
                "id": member["id"],
                "name": member["name"],
                "status": "online",
                "last_seen": time.time(),
            }
            await hub.publish("presence.updated", member["id"], hub.presence[member["id"]])
            for event in hub.events_since(int(hello.get("last_seq", 0))):
                await ws.send_json(event)
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)
            hub.subscribers.add(queue)
            await ws.send_json(
                {"v": 1, "type": "ready", "seq": hub.events_since(0)[-1]["seq"] if hub.events_since(0) else 0}
            )
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 25)
                    await ws.send_json(event)
                except TimeoutError:
                    await ws.send_json({"v": 1, "type": "ping", "seq": 0})
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            if "queue" in locals():
                hub.subscribers.discard(queue)
            if "member" in locals():
                current = hub.presence.get(member["id"], {"id": member["id"], "name": member["name"]})
                hub.presence[member["id"]] = {**current, "status": "offline", "last_seen": time.time()}
                await hub.publish("presence.updated", member["id"], hub.presence[member["id"]])

    return app
