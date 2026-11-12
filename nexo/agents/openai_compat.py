"""Adaptador genérico OpenAI-compatible — LM Studio, llama.cpp, text-generation-webui…

Habla HTTP contra un endpoint en tu propia máquina o red (100% local).
La API key es OPCIONAL: LM Studio y llama.cpp no la piden.

Nada se simula:
  - detect(): solo marca detectado si hay base_url configurada Y el
    endpoint responde GET /models de verdad (timeout corto).
  - arun_stream(): parsea el SSE real de POST /chat/completions
    (líneas `data:` con choices[0].delta.content, termina en [DONE]).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

import httpx

from .base import AgentAdapter

_MODELS_TIMEOUT = 6.0


class OpenAICompatAdapter(AgentAdapter):
    id = "openai_compat"
    label = "Endpoint local (OpenAI-compatible)"

    def __init__(
        self,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        timeout: int = 120,
    ):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.model = (model or "").strip()
        self.api_key = (api_key or "").strip()
        self.timeout_default = timeout or 120

    # ------------------------------------------------------------- utilidades
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _models(self) -> list[str]:
        """GET /models de verdad. [] si no hay URL, no responde o no es compatible."""
        if not self.base_url:
            return []
        try:
            # trust_env=False: el endpoint es local por diseño; nunca debe
            # pasar por un proxy de salida (además evita proxies rotos del entorno).
            with httpx.Client(trust_env=False, timeout=_MODELS_TIMEOUT) as client:
                r = client.get(f"{self.base_url}/models", headers=self._headers())
            if r.status_code != 200:
                return []
            data = r.json()
            items = data.get("data", []) if isinstance(data, dict) else []
            return [
                str(m.get("id", ""))
                for m in items
                if isinstance(m, dict) and m.get("id")
            ]
        except Exception:
            return []

    def _friendly_red(self, e: Exception) -> str:
        if isinstance(e, httpx.ConnectError):
            return (
                f"No se pudo conectar a {self.base_url}. "
                "¿Está el servidor encendido y la URL es correcta?"
            )
        if isinstance(e, httpx.TimeoutException):
            return f"Tiempo de espera agotado hablando con {self.base_url}."
        return f"Error de red con {self.base_url}: {e}"

    @staticmethod
    def _api_error(status: int, body: str) -> str:
        msg = ""
        try:
            data = json.loads(body or "")
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            if isinstance(err, dict):
                msg = str(err.get("message", "") or "")
            elif err:
                msg = str(err)
        except Exception:
            pass
        msg = msg or (body or "")[:300]
        if status == 404:
            return "El endpoint no responde como OpenAI-compatible (HTTP 404)."
        if status == 401:
            return "El endpoint rechazó la credencial (401). Revisa la API key."
        if status == 400:
            return f"El endpoint rechazó la petición (400): {msg[:300]}"
        return f"El endpoint devolvió HTTP {status}: {msg[:300]}"

    def _payload(self, prompt: str, stream: bool) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }

    # ------------------------------------------------------------------ API
    def detect(self) -> dict[str, Any] | None:
        # Honesto: sin URL configurada, o sin respuesta real de /models, no existe.
        if not self.base_url:
            return None
        models = self._models()
        if not models:
            return None
        chosen = self.model or models[0]
        return {
            "detail": f"{self.base_url} · modelo: {chosen}",
            "default_model": chosen,
        }

    def describe(self) -> str:
        base = self.base_url or "(sin configurar)"
        return f"Endpoint OpenAI-compatible en {base}"

    async def arun(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 0,
    ) -> tuple[int, str]:
        if not self.base_url:
            return (1, "Sin URL base configurada para el endpoint OpenAI-compatible.")
        if not self.model:
            return (1, "Configura el modelo a usar en este endpoint.")
        timeout = timeout or self.timeout_default
        try:
            async with httpx.AsyncClient(
                timeout=timeout, headers=self._headers(), trust_env=False
            ) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=self._payload(prompt, False),
                )
        except Exception as e:
            return (1, self._friendly_red(e))
        if r.status_code != 200:
            return (1, self._api_error(r.status_code, r.text))
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
        except Exception:
            return (1, "El endpoint respondió algo que no parece /chat/completions.")
        return (0, str(text))

    async def arun_stream(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 0,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Streaming REAL: parsea el SSE estándar de OpenAI a medida que llega."""
        if not self.base_url:
            msg = "Sin URL base configurada para el endpoint OpenAI-compatible."
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        if not self.model:
            msg = "Configura el modelo a usar en este endpoint."
            yield ("chunk", msg)
            yield ("done", (1, msg))
            return
        timeout = timeout or self.timeout_default
        try:
            async with httpx.AsyncClient(
                timeout=timeout, headers=self._headers(), trust_env=False
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=self._payload(prompt, True),
                ) as r:
                    if r.status_code != 200:
                        await r.aread()
                        msg = self._api_error(r.status_code, r.text)
                        yield ("chunk", msg)
                        yield ("done", (1, msg))
                        return
                    full: list[str] = []
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except Exception:
                            continue
                        try:
                            piece = obj["choices"][0]["delta"].get("content") or ""
                        except Exception:
                            piece = ""
                        if piece:
                            full.append(piece)
                            yield ("chunk", piece)
                    yield ("done", (0, "".join(full)))
        except Exception as e:
            # Si ya se había entregado texto parcial, se conserva; se reporta el error.
            msg = self._friendly_red(e)
            yield ("chunk", f"\n{msg}\n")
            yield ("done", (1, msg))


def test_connection(base_url: str, api_key: str = "") -> dict[str, Any]:
    """Prueba honesta de un endpoint: GET /models de verdad.

    Retorna {"ok": True, "models": [...]} o {"ok": False, "error": "..."}.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "Falta la URL base del endpoint."}
    headers = {"Content-Type": "application/json"}
    if (api_key or "").strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    try:
        with httpx.Client(trust_env=False, timeout=8.0) as client:
            r = client.get(f"{base}/models", headers=headers)
    except httpx.ConnectError:
        return {
            "ok": False,
            "error": f"No se pudo conectar a {base}. ¿Está el servidor encendido?",
        }
    except httpx.TimeoutException:
        return {"ok": False, "error": f"Tiempo de espera agotado con {base}."}
    except Exception as e:
        return {"ok": False, "error": f"Error de red con {base}: {e}"}
    if r.status_code != 200:
        return {
            "ok": False,
            "error": f"HTTP {r.status_code}: no parece un endpoint OpenAI-compatible.",
        }
    try:
        data = r.json()
        items = data.get("data", []) if isinstance(data, dict) else []
        models = [
            str(m.get("id", "")) for m in items if isinstance(m, dict) and m.get("id")
        ]
    except Exception:
        return {"ok": False, "error": "Respuesta ilegible en /models."}
    if not models:
        return {"ok": False, "error": "/models respondió pero no lista ningún modelo."}
    return {"ok": True, "models": models}
