# Empaquetar Atlantis Studio

Atlantis Studio es una app Electron que incluye el hub Python (NEXO 2.0).
Se empaqueta **en la máquina del sistema operativo destino** — no se puede
compilar el `.exe` de Windows ni el `.dmg` de macOS desde Linux.

## Requisitos (todas las plataformas)

- Node.js 20+ y npm
- Python 3.11+

## 1. Preparar el venv de Python (en la máquina destino)

El instalador incluye `nexo/.venv` con las dependencias ya instaladas.
El venv es específico de cada plataforma: hay que crearlo **en el OS destino**
antes de empaquetar.

```bash
cd nexo
python3 -m venv .venv
# Linux / macOS:
.venv/bin/pip install -r requirements.txt
# Windows (PowerShell):
.venv\Scripts\pip install -r requirements.txt
```

> `nexo/config.json` (tu configuración personal) **no** se incluye en el
> paquete; la app la crea al arrancar si no existe.

## 2. Instalar dependencias de Electron

```bash
cd desktop/native
npm ci
```

## 3. Empaquetar según el sistema

```bash
# Linux → dist/Atlantis Studio-*.AppImage (+ .deb)
npm run package:linux

# Windows → dist/Atlantis Studio Setup *.exe  (ejecutar EN Windows)
npm run package:windows

# macOS → dist/Atlantis Studio-*.dmg  (ejecutar EN macOS)
npm run package:mac
```

## Notas

- **Linux**: el AppImage se ejecuta directamente. Para integrarlo al menú,
  usa el archivo `Atlantis-Studio.desktop` incluido en el repo (ajusta la
  ruta `Exec=` a donde guardes el AppImage).
- **Windows**: el instalador NSIS crea acceso directo en el menú inicio.
  El primer arranque puede tardar unos segundos (descomprime recursos).
- **macOS**: arrastra la app a Aplicaciones. Sin firmar con certificado de
  Apple, Gatekeeper pedirá confirmación la primera vez
  (clic derecho → Abrir).
- La app elige un puerto libre al arrancar y guarda tu workspace en
  `~/AtlantisProjects/sandbox` (o la ruta de `ATLANTIS_WORKSPACE`).
- Para colaborar con otra máquina, usa **Compartir** (♧) dentro de la app:
  activa el túnel Cloudflare y comparte el enlace + el código de enlace.
