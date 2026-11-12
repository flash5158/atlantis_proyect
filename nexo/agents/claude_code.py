"""Adaptador Claude Code — CLI `claude` de Anthropic (modo print, local)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

from .base import AgentAdapter, _stream_subprocess


class ClaudeCodeAdapter(AgentAdapter):
    id = "claude-code"
    label = "Claude Code"

    def _bin(self) -> str | None:
        return shutil.which("claude")

    def detect(self) -> dict[str, Any] | None:
        b = self._bin()
        if not b:
            return None
        return {"detail": b, "default_model": ""}

    async def arun(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> tuple[int, str]:
        b = self._bin()
        if not b:
            return (1, "CLI 'claude' no encontrado en esta máquina.")
        proc = await asyncio.create_subprocess_exec(
            b,
            "-p",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return (-1, "⏱ Claude Code superó el límite de tiempo.")
        text = out.decode("utf-8", errors="replace")
        return (proc.returncode or 0, text)

    async def arun_stream(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Streaming REAL: la salida de `claude -p` se entrega por líneas."""
        b = self._bin()
        if not b:
            msg = "CLI 'claude' no encontrado en esta máquina."
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        proc = await asyncio.create_subprocess_exec(
            b,
            "-p",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
        )
        full: list[str] = []
        async for piece in _stream_subprocess(proc, timeout):
            full.append(piece)
            yield ("chunk", piece)
        yield ("done", (proc.returncode or 0, "".join(full)))

    def describe(self) -> str:
        return "Claude Code (CLI `claude` — modo print)"
