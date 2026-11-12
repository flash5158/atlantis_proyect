# Atlantis Studio — IDE colaborativo

Estudio de creación de apps inspirado en Antigravity, pero **colaborativo**:
tú y tu compañero trabajan en el **mismo proyecto al mismo tiempo**, cada uno
conecta su propio **Hermes Agent**, y los dos agentes **se comunican entre sí**
para coordinarse y dividirse el trabajo.

**Sin API keys. Sin nube. Sin cuentas.** La IA del estudio son sus propios
agentes Hermes corriendo en sus máquinas.

---

## 🚀 Inicio rápido

```bash
# 1. Entorno Python (una vez)
python3 -m venv nexo/.venv
nexo/.venv/bin/pip install -r nexo/requirements.txt

# 2. Arrancar el hub (puerto 8787 por defecto)
PORT=8787 NEXO_WORKSPACE=~/AtlantisProjects nexo/.venv/bin/python nexo/server.py
```

Abre **`http://127.0.0.1:8787`** en tu navegador, o la app Electron:

```bash
cd desktop/native && npm install && npm start
```

El asistente inicial te guía en 2 pasos: **conectar tu Hermes** → **vincularte
con tu compañero**.

---

## 🤝 Colaborar con tu amigo

1. En la app, abre **Compartir Espacio** (botón 🔗).
2. **Activa el túnel** → obtienes una URL pública (`https://xxx.trycloudflare.com`).
3. **Genera un código de enlace** (`ATL-XXXXXX`, expira en 15 min).
4. Pásale a tu amigo el **enlace + el código**.
5. Tu amigo abre el enlace (verá que **tú lo invitaste**), conecta su Hermes,
   escribe el código y entra al IDE. Ambos se ven en **COLABORADORES** con
   presencia en vivo (quién está en qué archivo).

Guía paso a paso: [`GUIA_COLABORACION.md`](GUIA_COLABORACION.md)

---

## 🤖 Tus agentes, conectados

Cada máquina corre su Hermes y lo enlaza al hub con **`hermes_link`**:

```bash
# En tu máquina (agente "Koko")
python3 nexo/hermes_link.py --hub http://127.0.0.1:8787 --agent "Koko"

# En la de tu amigo (agente "KokoAmigo")
python3 nexo/hermes_link.py --hub http://127.0.0.1:8787 --agent "KokoAmigo"
```

El worker sondea el bus de agentes, ejecuta cada mensaje/tarea con el binario
real de Hermes (`~/.local/bin/hermes`) y publica el resultado. Si el hub se
cae, reintenta con backoff exponencial y no duplica trabajo. Sin el binario,
queda inactivo de forma honesta.

**Bus de agentes** (visible en la pestaña *Agentes Hermes*):
- `POST /api/agents/message` — un agente (o humano) le escribe a otro
- `GET /api/agents/inbox?agent=<nombre>&pendientes=true` — bandeja del agente
- `POST /api/agents/ack` — el agente confirma con su resultado
- `POST /api/ai/task` con `asignado: <agente>` — asignar tarea a un agente

---

## 📁 Proyectos de todo tipo

**Archivo → Nuevo Proyecto** (o `⇧⌘N`): plantillas Web estático, Node.js,
Python y Vacío. El botón **▶ Ejecutar proyecto** detecta el tipo
(`package.json`, `requirements.txt`/`*.py`, `index.html`) y corre el comando
correcto en la terminal integrada real (PTY).

**Colaboración en archivos**: al guardar, tu cambio se difunde en vivo al otro.
Si ambos editaron lo mismo, verás una barra de conflicto con resolución en un
clic: **"Usar la de X"** / **"Usar la mía"**. Nada se pierde en silencio.

---

## 📦 Empaquetar para cada sistema

Ver [`PACKAGING.md`](PACKAGING.md): AppImage + deb (Linux), DMG (macOS),
instalador NSIS (Windows). El venv de Python debe construirse en cada sistema
destino.

## 📁 Estructura

```
atlantis_proyect/
├── nexo/
│   ├── server.py          # Hub NEXO: API, WebSocket, PTY, bus de agentes, pairing
│   ├── hermes_link.py     # Worker: enlaza tu Hermes CLI local con el bus
│   ├── ai_engine.py       # Detección del binario Hermes (sin keys)
│   ├── nexo-cli.py        # CLI: chat, archivos, git, ai-send/ai-inbox/ai-reply
│   └── requirements.txt
├── web/                   # IDE web (Monaco, xterm.js, panel multi-agente)
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── desktop/
│   ├── native/            # Shell Electron (AppImage/DMG/NSIS)
│   └── extension/         # Extensión VS Code (cliente secundario)
├── tests/                 # pytest: bus, pairing, versiones, presencia, WS
├── tunnel.sh              # Túnel Cloudflare para compartir tu espacio
└── GUIA_COLABORACION.md   # Guía de colaboración paso a paso
```

## ✅ Verificación

```bash
nexo/.venv/bin/python -m pytest tests/ -q
nexo/.venv/bin/python -m ruff check nexo/ tests/
node --check web/app.js
```

## 📜 Licencia

MIT — Hecho para construir en equipo, humanos y agentes.
