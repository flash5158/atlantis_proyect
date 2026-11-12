# 🤝 Guía de colaboración — Atlantis Studio

Cómo trabajar **tú y tu amigo en el mismo proyecto al mismo tiempo**, con sus
**Hermes Agents comunicándose entre sí**. Sin API keys, sin cuentas, sin nube:
todo corre en sus máquinas.

```
   TU MÁQUINA (anfitrión)                        MÁQUINA DE TU AMIGO
 ┌──────────────────────────┐                    ┌──────────────────────────┐
 │ Atlantis Studio          │                    │ Atlantis Studio        │
 │ • Tú (editor+terminal)   │   Túnel HTTPS      │ • Tu amigo             │
 │ • Tu Hermes (hermes_link)│ ◄───────────────►  │ • Su Hermes            │
 │ • Hub NEXO (puerto 8787) │  + código ATL-XXX  │   (hermes_link)        │
 │ • Workspace en disco     │                    │ • Se une con el enlace │
 └──────────────────────────┘                    └──────────────────────────┘
```

---

## Paso 1 — Arranca tu hub (solo tú)

```bash
python3 -m venv nexo/.venv
nexo/.venv/bin/pip install -r nexo/requirements.txt
PORT=8787 NEXO_WORKSPACE=~/AtlantisProjects nexo/.venv/bin/python nexo/server.py
```

Abre `http://127.0.0.1:8787`. El asistente te pide **conectar tu Hermes**
(detecta `~/.local/bin/hermes` solo; si no lo tienes, igual entras).

## Paso 2 — Comparte tu espacio

1. Clic en **🔗 Compartir Espacio** (barra superior).
2. **⚡ Activar túnel** → copia la URL pública (`https://xxx.trycloudflare.com`).
3. **🎲 Generar código de enlace** → obtienes algo como `ATL-7F3K9Q`
   (expira en 15 minutos; si expira, genera otro).
4. Mándale a tu amigo por WhatsApp/Telegram/Discord:
   - el **enlace** (ya trae tu token y tu nombre)
   - el **código ATL-XXXXXX**

## Paso 3 — Tu amigo se une

1. Abre el enlace → verá **"🤝 [Tu nombre] te invitó a colaborar"**.
2. Conecta **su** Hermes (paso 1 del asistente).
3. Escribe el **código de enlace** (paso 2) → **⚡ Enlace establecido**.
   (También puede escanear el **QR** del código con la cámara de su teléfono:
   aparece junto al código en Compartir equipo.)
4. Entra al IDE. En **COLABORADORES** ambos se ven en vivo, con presencia
   (quién está en qué archivo y dónde tiene el cursor).

## Paso 4 — Enlacen sus agentes

En **cada máquina**, corran su worker (con el nombre de su agente):

```bash
# Tu máquina
python3 nexo/hermes_link.py --hub http://127.0.0.1:8787 --agent "Koko"

# Máquina de tu amigo (apuntando a TU hub)
python3 nexo/hermes_link.py --hub https://xxx.trycloudflare.com --agent "KokoAmigo" --token <tu-token>
```

Desde ese momento, en la pestaña **Agentes Hermes** verán a los dos agentes
hablarse: se reparten trabajo, se avisan cuando terminan y confirman resultados.

## Paso 5 — Trabajen el proyecto

- **Nuevo proyecto**: Archivo → Nuevo Proyecto (Web, Node.js, Python, Vacío).
- **Ejecutar**: el botón ▶ detecta el tipo y lo corre en la terminal real.
- **Archivos en vivo**: al guardar, el otro lo ve al instante. Si chocan en el
  mismo archivo, sale la barra de conflicto: **"Usar la de X"** / **"Usar la mía"**.
- **Asignar tareas a un agente**: desde Tareas, asígnala a `Koko` o `KokoAmigo`;
  el agente la recoge del bus y publica su resultado.
- **Chat de equipo** y **terminal compartida** en el panel lateral.

---

## Modo equipo (3+ personas)

El mismo código `ATL-XXXXXX` sirve para **todo un equipo** (hasta 8 miembros).
Cada compañero repite el Paso 3 con el mismo código: no expira mientras el
equipo esté activo.

- En **Compartir equipo** ves la lista de miembros en vivo, con medalla
  **Anfitrión** para quien creó el equipo.
- Cada miembro que se une aparece anunciado en el bus de agentes.
- Un miembro puede irse cuando quiera (**"Salir del equipo"**); el equipo
  sigue con los demás.
- Si sale el **anfitrión** (o no queda nadie), el equipo se cierra para todos.
- El anfitrión puede cerrar el equipo en cualquier momento
  (**"Cerrar equipo"**); todos vuelven a modo individual.

---

## Conecta cualquier modelo local (endpoint OpenAI-compatible)

Además de Hermes, Claude Code, Ollama y tu CLI, puedes conectar **cualquier
servidor local con API tipo OpenAI**: LM Studio, llama.cpp, text-generation-webui…

1. En **Mis Asistentes** (barra del asistente → gestionar) pulsa **Añadir asistente**.
2. Elige **"Endpoint local (OpenAI-compatible)"**.
3. Ponle un nombre, la **URL base** (ej. `http://localhost:1234/v1`), el **modelo**
   y la **API key solo si tu servidor la pide** (LM Studio y llama.cpp no la piden).
4. Pulsa **"Probar conexión"**: consulta `/models` de verdad y te muestra los
   modelos disponibles (o el error real si algo falla).
5. **Añadir asistente** → ya lo tienes como un slot más, con streaming en vivo.

Todo queda en tu máquina: sin nube y sin API keys obligatorias. Si el endpoint
no responde, el slot lo dice con honestidad ("no detectado ahora") en vez de
fingir que funciona.

---

## Preguntas rápidas

**¿Necesito alguna API key?** No. Nunca. Si algo la pidiera, es un bug: repórtalo.

**¿El código expiró?** Genera uno nuevo en Compartir Espacio (duran 15 min).

**¿Mi amigo no ve mis cambios?** Revisa que ambos estén en el mismo hub
(misma URL) y que el túnel siga activo.

**¿El agente no responde?** Verifica que `hermes_link.py` siga corriendo en su
máquina y que el binario `hermes` exista (`~/.local/bin/hermes`).

**¿Puedo usar la extensión de VS Code?** Sí, es un cliente secundario:
`desktop/extension` (útil si tu amigo prefiere VS Code; el IDE web es la
experiencia completa).
