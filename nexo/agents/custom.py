"""Adaptador CLI genérico — cualquier asistente con interfaz de línea de comandos.

El usuario configura su propio comando (guardado por máquina en
~/.atlantis/config.json, nunca quemado en código):

  - Modo plantilla: el comando contiene {prompt}, p. ej.
      "mi-asistente --pregunta \"{prompt}\" --formato texto"
    Se ejecuta vía shell con el prompt sustituido (comando del propio usuario,
    en su propia máquina).
  - Modo stdin: sin {prompt} en el comando, p. ej. "mi-asistente --stdin".
    El prompt se envía por la entrada estándar.

Nada se simula: "Probar" ejecuta un prompt real y muestra la salida real.
"""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any

from .base import AgentAdapter, _stream_subprocess


class GenericCLIAdapter(AgentAdapter):
    id = "custom"
    label = "CLI personalizado"

    def __init__(self, name: str = "", command: str = "", timeout: int = 120):
        self.custom_name = (name or "").strip() or "Mi CLI"
        self.command = (command or "").strip()
        self.timeout_default = timeout

    @property
    def display_label(self) -> str:
        return self.custom_name

    def detect(self) -> dict[str, Any] | None:
        # Un CLI personalizado solo "existe" si el usuario lo configuró.
        if not self.command:
            return None
        return {"detail": self.command, "default_model": ""}

    def describe(self) -> str:
        return f"{self.custom_name} (comando: {self.command})"

    def _with_prompt(self, prompt: str) -> str:
        # Sustitución simple y predecible del placeholder.
        return self.command.replace("{prompt}", prompt)

    async def arun(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 0,
    ) -> tuple[int, str]:
        if not self.command:
            return (1, "No hay comando personalizado configurado.")
        timeout = timeout or self.timeout_default
        cwd_s = str(cwd) if cwd else None
        try:
            if "{prompt}" in self.command:
                cmd = self._with_prompt(prompt)
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd_s,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            else:
                parts = shlex.split(self.command)
                proc = await asyncio.create_subprocess_exec(
                    *parts,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd_s,
                )
                out, _ = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=timeout)
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return (-1, "⏱ El comando personalizado superó el límite de tiempo.")
        except FileNotFoundError:
            return (1, f"No se encontró el comando: {self.command.split()[0] if self.command.split() else '?'}")
        except Exception as e:
            return (1, f"Error ejecutando comando personalizado: {e}")
        text = out.decode("utf-8", errors="replace")
        return (proc.returncode or 0, text)

    async def arun_stream(self, prompt, cwd=None, timeout=0):
        """Streaming REAL: la salida se entrega a medida que el CLI la produce."""

        if not self.command:
            msg = "No hay comando personalizado configurado."
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        timeout = timeout or self.timeout_default
        cwd_s = str(cwd) if cwd else None
        try:
            if "{prompt}" in self.command:
                cmd = self._with_prompt(prompt)
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd_s,
                )
            else:
                parts = shlex.split(self.command)
                proc = await asyncio.create_subprocess_exec(
                    *parts,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd_s,
                )
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
        except FileNotFoundError:
            msg = f"No se encontró el comando: {self.command.split()[0] if self.command.split() else '?'}"
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        except Exception as e:
            msg = f"Error ejecutando comando personalizado: {e}"
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        full = []
        async for piece in _stream_subprocess(proc, timeout):
            full.append(piece)
            yield ("chunk", piece)
        yield ("done", (proc.returncode or 0, "".join(full)))
