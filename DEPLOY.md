# Desplegar el backend de Atlantis Studio en internet

El backend (NEXO 2.0) es una app FastAPI normal. Cualquier plataforma que corra
Docker la puede hospedar. Una vez desplegado, tú y tu pana apuntan la app al
mismo hub y colaboran sin túneles.

## ⚠️ Seguridad: NUNCA uses `NEXO_PUBLIC=1` en un hub público

Con `NEXO_PUBLIC=1` (o en Vercel), el modo público hace que `token_ok()` y
`es_host()` devuelvan True para CUALQUIER persona: cualquiera con la URL del
hub sería dueño y podría ejecutar shell en el contenedor, crear/iniciar MCPs,
cambiar el workspace y leer/escribir archivos. El login de la app NO protege
contra esto (los tokens de sesión no se validan en los endpoints).

**Usa siempre `TOKEN=<secreto fuerte>` (modo privado).** Genera el secreto con
`openssl rand -hex 24` y compártelo con tu pana por un canal privado. Nunca
pongas el link con el token en lugares públicos ni en el QR de la landing.

## Opción A — Railway Trial vía CLI (piloto recomendado, ~1 mes)

Gratis, sin tarjeta, sin push a repo. El Trial da $5 por 30 días; después el
plan Free ($1/mes) solo alcanza para unos días de uso al mes. Para un hub
permanente, el paso natural es Hobby $5/mes (requiere tarjeta), mismo
proyecto, sin migrar nada.

1. Crea cuenta en https://railway.app (verifícala con GitHub para el Trial
   completo; sin verificar, la salida de red queda restringida).
2. Instala la CLI: `npm i -g @railway/cli` y autentica con `railway login`.
3. Desde la carpeta del proyecto:
   `railway init` → `railway add` (servicio vacío) → `railway link`.
4. Agrega un Volume de 0.5 GB montado en `/root/.atlantis` (dashboard o CLI).
5. Variables del servicio:
   - `TOKEN=<tu secreto>` (obligatorio)
   - `NEXO_WORKSPACE=/root/.atlantis/workspace`
   - NO pongas `NEXO_PUBLIC`. NO pongas `PORT` (Railway lo inyecta).
6. `railway up` (sube el directorio local, construye con el Dockerfile).
7. `railway domain` → te da la URL `https://xxxx.up.railway.app`.

## Opción B — Render Free (solo demo)

Gratis para siempre y sin tarjeta, pero con dos peajes: **exige push del
código a GitHub/GitLab** (necesitas aprobarlo explícitamente) y **no persiste
nada** (el servicio duerme a los 15 min sin tráfico y la base de datos se
borra: cuentas, equipos, chat). Además el `render.yaml` del repo declara
`disk:` + `plan: free`, que Render gratis no admite: crea el servicio sin
disco desde el dashboard. Solo válido como demo, no como hub real.

1. Crea cuenta en https://render.com.
2. New → Web Service → conecta tu repo (sin disco).
3. Variables: `TOKEN=<tu secreto>`, `NEXO_WORKSPACE=/root/.atlantis/workspace`.
   Nunca `NEXO_PUBLIC`.

## Opción C — Fly.io (permanente, más barato)

~$2.50–4/mes (máquina 256–512 MB + volumen 1 GB), volumen persistente,
deploy desde local con `flyctl` (sin push). **Requiere tarjeta.**

1. Instala `flyctl` y haz `fly launch` en la carpeta del proyecto.
2. `fly volumes create atlantis_data --size 1` y móntalo en `/root/.atlantis`.
3. `fly secrets set TOKEN=<tu secreto>` y `fly deploy`.

## Opción D — Tu propio servidor (VPS)

```bash
docker build -t atlantis-hub .
docker run -d --restart unless-stopped -p 8787:8787 \
  -e TOKEN=<tu secreto> \
  -e NEXO_WORKSPACE=/root/.atlantis/workspace \
  -v atlantis-data:/root/.atlantis \
  atlantis-hub
```

## Conectar la app al hub en la nube

La app acepta el parámetro `?hub=` para apuntar a un hub remoto:

1. Abre `https://xxxx.up.railway.app/?hub=https://xxxx.up.railway.app&token=<tu secreto>`
   (o abre tu app local como `http://localhost:8787/?hub=https://xxxx.up.railway.app&token=<tu secreto>`).
2. Regístrate / inicia sesión con tu cuenta. Tu pana hace lo mismo desde su máquina.
3. Genera el código de equipo en `Compartir Espacio`; tu pana se une con el
   código y recibe su `guest_token` (invitado: sin terminal, sin admin).

## Notas

- La base de datos vive en `~/.atlantis/hub.db`. Sin volumen persistente,
  las cuentas se pierden al reiniciar el contenedor.
- Los archivos del proyecto viven en `NEXO_WORKSPACE`
  (`/root/.atlantis/workspace` en el contenedor, dentro del volumen).
- El WebSocket (`/ws`) funciona en las cuatro opciones sin config extra.
- El servidor corre **un solo worker** con una sola conexión SQLite: una sola
  instancia, sin réplicas ni autoscaling.
- Tras un reinicio del servicio, las cuentas y el equipo sobreviven (volumen),
  pero los invitados deben hacer re-join con el código y los MCPs hay que
  re-iniciarlos a mano.
- Todo sigue siendo tu backend: sin keys de terceros, tu código, tu base de datos.
