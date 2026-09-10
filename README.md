# NEXO Studio 2.0 — Atlantis Project (Dual-AI Collaborative IDE)

Plataforma de desarrollo colaborativo en tiempo real para que **dos desarrolladores programen simultáneamente** mientras sus **agentes de Inteligencia Artificial (Hermes Agent & Google Gemini)** se comunican directamente, coordinan tareas, auditan código y ejecutan cambios en el workspace local.

![NEXO Studio 2.0](https://img.shields.io/badge/NEXO_Studio-2.0_Dual--AI-06b6d4?style=for-the-badge)
![Hermes CLI](https://img.shields.io/badge/Nous_Hermes-CLI_Online-10b981?style=for-the-badge)
![Google Gemini](https://img.shields.io/badge/Gemini_3.6-Flash_Connected-8b5cf6?style=for-the-badge)

---

## 🚀 Inicio Rápido en 1 Paso

En tu terminal local:

```bash
git clone https://github.com/flash5158/atlantis_proyect.git
cd atlantis_proyect

# Iniciar servidor local y generar túnel público opcional:
./start.sh --tunnel
```

Abre en tu navegador: **`http://127.0.0.1:8787`**

Para invitar a tu compañero, haz clic en el botón superior **`🔗 Invitar Amigo`** o comparte el enlace seguro de Cloudflare generado por `./tunnel.sh`.

---

## 🌟 Características Principales (Cero Simulaciones)

### 1. Conexión Auténtica con Nous Hermes Agent CLI
- Detección automática del binario real en `~/.local/bin/hermes`.
- Configurado con el motor nativo `gemini-3.6-flash`.
- Ejecución real en el workspace con herramientas locales (`--in <DIR> --yolo`).
- Consola de streaming en tiempo real (stdout/stderr línea a línea) mediante WebSockets.

### 2. Google Gemini 3.6 Flash (AI Studio)
- Detección automática de `GOOGLE_API_KEY` desde `~/.hermes/.env` o variables de entorno.
- Cliente asíncrono ultra-rápido (`httpx`) con HTTP/2 y TLS moderno.
- Latencia comprobada de **~215 ms** con generación de código e inserción directa en el editor.

### 3. Matriz de Colaboración Dual Autónoma (Hermes ↔ Gemini)
- **Fase 1 (Gemini Arquitecto):** Analiza el objetivo del usuario y diseña el plan técnico y la arquitectura.
- **Fase 2 (Hermes Ejecutor):** Aplica modificaciones de código, ejecuta comandos de terminal y crea archivos en el repositorio.
- **Fase 3 (Gemini Auditor):** Inspecciona el código resultante y emite una auditoría técnica final.

### 4. Interfaz Moderna Estándar IDE 2026
- **Paleta Obsidian Slate:** Tema oscuro profesional (`#07090e`, `#0f1523`) con tipografía `Inter` y `JetBrains Mono`.
- **Monaco Editor:** Motor oficial de VS Code con múltiples pestañas, mini-mapa, resaltado de sintaxis y atajo nativo `Ctrl+S` / `Cmd+S`.
- **Dock Inferior Multitarea:** Terminal interactiva PTY (ZSH/Bash con `xterm.js`), consola en vivo de Hermes y registro de colaboración dual.
- **Control de Versiones Integrado:** Panel Git para revisar cambios modificados y hacer `commit & push` con un clic.

---

## 🩺 Diagnóstico de Salud del Sistema

Verifica en cualquier momento la conexión con ambas IAs ejecutando:

```bash
python3 salud_sistema.py
```

Salida esperada:
```json
{
  "gemini": {
    "status": "healthy",
    "modelo": "gemini-3.6-flash",
    "latency_ms": 215.22,
    "error": null
  },
  "hermes": {
    "status": "healthy",
    "modelo": "gemini-3.6-flash (Nous Hermes CLI)",
    "latency_ms": 293.76,
    "error": null
  }
}
```

---

## 📁 Estructura del Repositorio

```
atlantis_proyect/
├── nexo/                      # Servidor y herramientas NEXO
│   ├── server.py              # API FastAPI, WebSockets y PTY interactivo
│   ├── ai_engine.py           # Motor de integración real Gemini 3.6 + Hermes CLI
│   ├── nexo-cli.py            # CLI para agentes y desarrolladores
│   ├── static/index.html      # IDE Studio 2026 (Monaco + xterm.js + Dual-AI)
│   └── requirements.txt       # Dependencias
├── proyectos/                 # Espacio de trabajo para proyectos colaborativos
├── skills/nexo-colab/         # Skill oficial para Antigravity IDE
├── salud_sistema.py           # Script de diagnóstico de latencia y estado
├── start.sh                   # Script de inicio rápido
├── tunnel.sh                  # Gestor de túneles públicos Cloudflare
└── GUIA_COLABORACION.md       # Guía paso a paso para programar en pareja
```

---

## 📜 Licencia

MIT License — Desarrollado para colaboración simultánea entre humanos e inteligencias artificiales en tiempo real.

## Atlantis Runtime (colaboración persistente)

La implementación nueva y aislada para uso diario vive en `nexo/atlantis_hub/`.
Usa SQLite, autenticación Bearer, eventos con secuencia/replay, leases para
workers Hermes y edición de documentos con CRDT. El servidor antiguo en el
puerto 8787 se conserva para compatibilidad con la demo anterior.

```bash
nexo/.venv/bin/pip install -r nexo/requirements-atlantis.txt
./start-atlantis.sh /ruta/a/tu/workspace
```

Instala `desktop/extension` en Code-OSS/VSCodium. En el primer ordenador usa
`Atlantis: Crear espacio compartido`; el segundo usa `Atlantis: Conectarse a un
espacio`. Cada uno registra su worker local con `Atlantis: Conectar mi Hermes`.
Para crear el ejecutable nativo del sistema actual ejecuta
`python3 scripts/build_runtime.py`; el flujo de CI debe producir un artefacto
por cada plataforma (Linux, macOS y Windows).
