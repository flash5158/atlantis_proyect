# 🚀 Guía Definitiva de Colaboración Simultánea — NEXO 2.0 (Atlantis)

> **Desarrollo en conjunto para ti y tu compañero, potenciado por comunicación directa entre sus Inteligencias Artificiales (Antigravity & Hermes).**

---

## 🎯 ¿Qué es NEXO 2.0?

NEXO 2.0 es un entorno de desarrollo colaborativo en tiempo real diseñado para que dos programadores trabajen sobre el mismo código simultáneamente, mientras sus agentes de IA se comunican entre sí para coordinar tareas, revisar código y sincronizar cambios sin conflictos.

```
       MÁQUINA 1 (Tú)                              MÁQUINA 2 (Tu amigo)
 ┌─────────────────────────┐               ┌─────────────────────────┐
 │ • Jaime (Desarrollador) │               │ • Amigo (Desarrollador) │
 │ • Antigravity (IA)      │               │ • Hermes (IA)           │
 │ • Servidor NEXO (8787)  │ ◄───────────► │ • Cliente NEXO / Web    │
 │ • Workspace en disco    │  Túnel HTTPS  │ • Terminal & Editor HUD │
 └─────────────────────────┘ (Cloudflare)  └─────────────────────────┘
```

---

## ⚡ Paso 1: Arrancar el Servidor (En tu máquina)

Solo **una persona** (tú) necesita tener el servidor levantado. Todo lo que ambos programen se guardará en el workspace de tu máquina y se sincronizará con Git.

### Opción Rápida (1 solo comando):

```bash
./start.sh --tunnel
```

Este comando:
1. Configura el entorno virtual de Python.
2. Inicia el servidor NEXO en el puerto `8787`.
3. Levanta automáticamente un **túnel público gratuito (Cloudflare Quick Tunnel)** sin necesidad de crear cuenta ni abrir puertos en el router.
4. Te muestra en pantalla la **URL pública** y el **Token de acceso**.

Si solo quieres trabajar en tu red local (LAN/Wi-Fi):
```bash
./start.sh
# Accede en http://127.0.0.1:8787
```

---

## 🤝 Paso 2: Invitar a tu Amigo

En la barra superior de la interfaz web (`http://127.0.0.1:8787`), haz clic en el botón:

👉 **`🔗 Conectar Amigo`**

Se abrirá una ventana con todos los accesos listos para copiar con 1 clic:
- **URL Web Pública:** `https://xxxx.trycloudflare.com` (o tu IP local)
- **Token Secreto:** `(tu token de config.json)`

Pásale esos datos a tu amigo por Discord, Telegram o WhatsApp.

---

## 💻 Paso 3: Cómo se Conecta tu Amigo

Tu amigo tiene tres formas de colaborar (puede usar cualquiera de ellas o combinarlas):

### Forma A: Directo desde el Navegador (Sin instalar nada)
1. Abre el enlace público que le diste (`https://xxxx.trycloudflare.com`).
2. Pega el Token cuando se lo pida.
3. ¡Listo! Verá la interfaz HUD estilo **Jarvis / Ultron**, el editor de código, el chat en vivo, el tablero de tareas y la terminal integrada.

---

### Forma B: Usando su Agente Hermes (Terminal)
Si tu amigo prefiere programar con Hermes desde su terminal:

```bash
# 1. Clona el repositorio
git clone https://github.com/flash5158/atlantis_proyect.git
cd atlantis_proyect/nexo

# 2. Configura sus credenciales (o pásalas por flag)
export NEXO_SERVER="https://xxxx.trycloudflare.com"
export NEXO_TOKEN="tu-token-aqui"
export NEXO_NOMBRE="hermes-2"

# 3. Quedarse escuchando en vivo (modo agente):
python3 nexo-cli.py escuchar --proyecto mi-proyecto
```

---

### Forma C: Usando Antigravity IDE
Si tu amigo también usa Antigravity:
1. Abre el proyecto en Antigravity IDE.
2. La skill `nexo-colab` ya está incluida en la carpeta `skills/nexo-colab/`.
3. Simplemente le dice a Antigravity: *"Conéctate al servidor NEXO en https://xxxx.trycloudflare.com y revisa si hay tareas pendientes."*

---

## 🤖 Comunicación Directa IA-a-IA (Antigravity ↔ Hermes)

El punto más potente de NEXO 2.0 es que **ustedes no tienen que copiar y pegar código entre sus IAs**. Las IAs se comunican directamente a través del protocolo estructurado de NEXO:

### 1. Antigravity le asigna una tarea a Hermes:
Desde tu IDE, puedes decirle a Antigravity:
> *"Pídele a Hermes que implemente los endpoints de login en auth.py mientras yo diseño la base de datos en models.py."*

Antigravity ejecutará internamente:
```bash
python3 nexo/nexo-cli.py ai-send \
  --para "hermes" \
  --tipo "tarea" \
  --titulo "Crear endpoints de login" \
  --contenido "Necesitamos POST /login y POST /register con JWT." \
  --archivos "auth.py"
```

### 2. Hermes recibe la tarea automáticamente:
Hermes consulta su buzón mediante cron o con:
```bash
python3 nexo/nexo-cli.py ai-inbox --pendientes
```

Hermes lee los requisitos, genera el código, lo escribe directamente en el workspace con:
```bash
python3 nexo/nexo-cli.py escribir auth.py --contenido "..."
```

Y le responde a Antigravity:
```bash
python3 nexo/nexo-cli.py ai-reply \
  --id "<ID_TAREA>" \
  --contenido "Endpoints creados y verificados. ¿Puedes conectarlos a la UI?"
```

### 3. Revisión de Código Cruzada (Code Review):
En la interfaz web, puedes hacer clic en **`🤖 Solicitar Revisión a IA`** en cualquier archivo abierto para que la IA de tu compañero analice el archivo y emita comentarios o propuestas de mejora en tiempo real.

---

## 🎨 Temas Visuales: Jarvis & Ultron

En la esquina superior derecha puedes alternar el tema visual:
- **⚡ JARVIS:** Estética holográfica en cian y azul neón (`#00f0ff`) sobre negro profundo espacial, con bordes de cristal y líneas técnicas.
- **🔴 ULTRON:** Estética cibernética en carmesí y cobalto (`#ff1e56` y `#3a86ff`) sobre titanio oscuro.

Ambos temas incluyen un **sintetizador de voz HUD** (Web Speech API) que anuncia eventos del sistema, como llegada de mensajes o ejecución de código (puedes activarlo o silenciarlo con el botón `🔊 HUD Audio`).

---

## 🛡️ Prevención de Conflictos de Archivos

Para evitar que tú y tu compañero sobreescriban el mismo archivo al mismo tiempo:
- Cuando abres un archivo en el editor, NEXO activa una etiqueta de presencia en tiempo real (`EDITANDO: <usuario>`).
- Tu compañero verá la etiqueta de bloqueo preventivo en su árbol lateral.

---

## ⎇ Sincronización con GitHub

Cualquiera de los dos puede pulsar el botón **`⎇ Git`** en la barra superior o usar el CLI:
- Ver estado: `python3 nexo/nexo-cli.py git status`
- Guardar cambios: `python3 nexo/nexo-cli.py git commit --msg "feat: nueva funcionalidad"`
- Sincronizar: `python3 nexo/nexo-cli.py git push` o `git pull`

Cada agente y desarrollador commitea con su propio nombre para mantener un historial limpio y transparente.
