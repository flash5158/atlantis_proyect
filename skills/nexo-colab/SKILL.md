---
name: nexo-colab
description: Permite a Antigravity colaborar en tiempo real con otro desarrollador y comunicarse directamente con su IA compañera (Hermes u otra instancia de Antigravity) a través de NEXO 2.0.
---

# NEXO Colab Skill (IA-a-IA Collaboration)

Esta skill permite a Antigravity actuar como co-piloto activo en el espacio de trabajo colaborativo de NEXO, interactuando con el compañero de equipo y con su agente IA (ej. Hermes).

## Variables de Entorno y Configuración

El CLI de NEXO (`nexo/nexo-cli.py`) se comunica con el servidor NEXO. Puede configurarse mediante flags o variables de entorno:

- `NEXO_SERVER`: URL del servidor (ej: `http://127.0.0.1:8787` o `https://xxxx.trycloudflare.com`).
- `NEXO_TOKEN`: Token de autenticación (se encuentra en `nexo/config.json`).
- `NEXO_NOMBRE`: Nombre del agente (ej: `antigravity` o `jaime`).

Ruta del CLI: `<repo>/nexo/nexo-cli.py` (ejecutado con `<repo>/nexo/.venv/bin/python`).

---

## Flujos de Trabajo de la IA

### 1. Consultar Mensajes y Tareas de la IA Compañera (Inbox)
Para verificar si la IA compañera (Hermes) ha asignado una tarea o realizado una consulta:

```bash
python3 nexo/nexo-cli.py ai-inbox --pendientes --json
```

Si hay tareas pendientes:
1. Analiza el `titulo`, `contenido`, `archivos` y `codigo`.
2. Procede a editar o implementar la solución en los archivos correspondientes.
3. Responde a la tarea indicando el estado de completado.

### 2. Responder a una Tarea de la IA Compañera
Una vez completada la tarea o respuesta técnica:

```bash
python3 nexo/nexo-cli.py ai-reply \
  --id "<ID_MENSAJE>" \
  --contenido "He implementado la función solicitada y pasado los tests." \
  --estado "completado"
```

### 3. Asignar Tarea o Solicitar Ayuda a la IA Compañera
Para delegar un módulo, pedir tests o solicitar revisión de código a Hermes:

```bash
python3 nexo/nexo-cli.py ai-send \
  --para "hermes" \
  --tipo "tarea" \
  --titulo "Crear suite de pruebas para auth.py" \
  --contenido "He terminado la lógica de autenticación en src/auth.py. ¿Podrías crear los tests unitarios con pytest en tests/test_auth.py?" \
  --archivos "src/auth.py"
```

### 4. Consultar y Actualizar el Tablero de Tareas
Para ver el estado del proyecto en común:

```bash
python3 nexo/nexo-cli.py ai-tasks --json
```

Para crear una nueva tarea compartida:

```bash
python3 nexo/nexo-cli.py ai-task-add \
  --titulo "Refactorizar conexión de base de datos" \
  --descripcion "Migrar de SQLite a PostgreSQL para producción" \
  --asignado "antigravity" \
  --prioridad "alta"
```

### 5. Leer y Modificar Archivos del Workspace Compartido
Para garantizar que ambos desarrolladores vean los cambios en vivo en la GUI:

```bash
# Leer un archivo
python3 nexo/nexo-cli.py leer "src/app.py" --proyecto "mi-proyecto"

# Escribir o crear un archivo
python3 nexo/nexo-cli.py escribir "src/app.py" --contenido "print('Hola desde Antigravity')" --proyecto "mi-proyecto"
```

### 6. Ejecutar Código en el Workspace
Para ejecutar pruebas o scripts en el entorno del servidor:

```bash
python3 nexo/nexo-cli.py run "pytest tests/" --proyecto "mi-proyecto"
```

### 7. Sincronizar con Git
Para versionar los cambios colaborativos:

```bash
python3 nexo/nexo-cli.py git commit --msg "feat: autenticación terminada por Antigravity"
python3 nexo/nexo-cli.py git push
```

---

## Reglas de Colaboración para Antigravity

1. **Nunca sobreescribas trabajo sin avisar:** Antes de modificar un archivo grande, consulta el chat o la lista de tareas.
2. **Usa siempre la identidad 'antigravity':** Para que los commits y mensajes queden claramente trazados respecto a los de Hermes.
3. **Reporta avances mediante `ai-reply` o en el chat:** Mantén a la otra IA y al compañero al tanto del progreso.
