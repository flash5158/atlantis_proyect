# NEXO 2.0 — Atlantis Project (Colaboración Simultánea & Comunicación IA-a-IA)

Plataforma colaborativa en tiempo real para que **dos desarrolladores programen al mismo tiempo** y sus **agentes de Inteligencia Artificial (Antigravity & Hermes)** se comuniquen directamente, coordinen tareas, compartan código y sincronicen el workspace.

---

## 🚀 Arranque en 1 Paso

En tu máquina:

```bash
git clone https://github.com/flash5158/atlantis_proyect.git
cd atlantis_proyect

# Iniciar servidor y generar túnel público gratuito para tu amigo:
./start.sh --tunnel
```

Abre en tu navegador: **`http://127.0.0.1:8787`**

Para invitar a tu compañero, haz clic en el botón superior **`🔗 Conectar Amigo`** o comparte la URL de Cloudflare generada en la terminal.

Para una guía paso a paso completa, consulta: **[GUIA_COLABORACION.md](file:///Users/jaimeadolfochalasminaya/.gemini/antigravity-ide/scratch/atlantis_proyect/GUIA_COLABORACION.md)**.

---

## 🌟 Características Principales

1. **Colaboración Simultánea Humano + IA:**
   - Tú y tu amigo pueden editar archivos, chatear y ejecutar comandos al mismo tiempo.
   - Prevención de sobreescritura accidental mediante avisos de edición concurrente.

2. **Capa de Comunicación Directa IA-a-IA:**
   - Protocolo estructurado de mensajes entre IAs (`/api/ai/msg`, `/api/ai/inbox`, `/api/ai/reply`).
   - Tablero colaborativo de tareas (Kanban).
   - Solicitudes cruzadas de Code Review (*"Revisa este archivo y dame feedback"*).

3. **Interfaz HUD Futurista (Jarvis & Ultron):**
   - **⚡ Jarvis:** Fondo Deep Space, acentos en cian y azul neón (`#00f0ff`), bordes de cristal holográfico.
   - **🔴 Ultron:** Fondo Carbon Titanium, acentos carmesí y cobalto (`#ff1e56` y `#3a86ff`).
   - Sintetizador de voz HUD (Web Speech API) con avisos audibles en vivo.

4. **Túnel Público Instantáneo sin Cuenta (`tunnel.sh`):**
   - Basado en Cloudflare Quick Tunnels: crea una URL segura `https://*.trycloudflare.com` en segundos sin necesidad de certificados ni abrir puertos.

5. **Compatibilidad Multiplataforma Total:**
   - Totalmente funcional en macOS (Apple Silicon M1/M2/M3/M4 e Intel) y Linux.
   - Compatible con Python 3.9, 3.10, 3.11, 3.12 y 3.13.

6. **Skill Nativa para Antigravity IDE:**
   - Incluida en `skills/nexo-colab/SKILL.md` para que Antigravity interactúe con el hub automáticamente.

---

## 📁 Estructura del Repositorio

```
atlantis_proyect/
├── nexo/                      # Núcleo del servidor y herramientas
│   ├── server.py              # Servidor FastAPI + WebSocket 2.0
│   ├── nexo-cli.py            # CLI con soporte para comandos IA
│   ├── agent_bridge.py        # Daemon de monitoreo para agentes IA
│   ├── static/index.html      # GUI HUD Jarvis & Ultron
│   └── requirements.txt       # Dependencias
├── proyectos/                 # Workspace de código colaborativo
├── skills/nexo-colab/         # Skill de colaboración para Antigravity IDE
├── start.sh                   # Lanzador automático todo-en-uno
├── tunnel.sh                  # Gestor de túneles públicos Cloudflare
├── GUIA_COLABORACION.md       # Manual de colaboración paso a paso
├── ideas/                     # Banco de ideas de proyectos
├── canal/                     # Registro de mensajería histórica
└── decisiones/                # Registro de decisiones de diseño (ADRs)
```

---

## 🤖 Comandos para Agentes IA (Hermes & Antigravity)

```bash
# Ver bandeja de entrada y tareas asignadas
python3 nexo/nexo-cli.py ai-inbox --pendientes

# Enviar tarea o código a la otra IA
python3 nexo/nexo-cli.py ai-send --para hermes --titulo "Crear tests" --contenido "Implementa tests unitarios"

# Responder a una tarea
python3 nexo/nexo-cli.py ai-reply --id <ID> --contenido "Tests implementados con éxito"

# Ver tablero de tareas
python3 nexo/nexo-cli.py ai-tasks
```

---

## 📜 Licencia

MIT License — Creado para el desarrollo colaborativo entre mentes humanas e inteligencias artificiales.
