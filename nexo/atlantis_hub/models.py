from typing import Any, Literal
from pydantic import BaseModel, Field


class Invite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: Literal["member", "worker"]


class MessageInput(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    channel: Literal["team", "agents"] = "team"
    text: str = Field(min_length=1, max_length=12000)
    task_id: str | None = None


class TaskInput(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=24000)
    assignee_id: str
    paths: list[str] = Field(default_factory=list, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=20)


class HandoffInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=24000)
    assignee_id: str
    paths: list[str] = Field(default_factory=list, max_length=100)


class CompleteInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=24000)
    diff: str = Field(default="", max_length=262144)
    exit_code: int | None = None
    status: Literal["completed", "failed", "interrupted", "conflict"]
    coordination_messages: list[str] = Field(default_factory=list, max_length=8)
    handoffs: list[HandoffInput] = Field(default_factory=list, max_length=4)
    base_commit: str | None = Field(default=None, max_length=128)
    worktree_path: str | None = Field(default=None, max_length=4096)
    verifications: list[dict[str, Any]] = Field(default_factory=list, max_length=30)


class FileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    text: str = Field(max_length=1048576)
    expected_revision: str | None = None


def as_dict(model: BaseModel) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()
