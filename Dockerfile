# Imagen oficial del backend Atlantis Studio (NEXO 2.0)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias del sistema (build mínimo para wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
 && rm -rf /var/lib/apt/lists/*

COPY nexo/requirements.txt ./nexo/requirements.txt
RUN pip install --no-cache-dir -r nexo/requirements.txt

COPY nexo/ ./nexo/
COPY web/ ./web/
COPY desktop/ ./desktop/

# El hub guarda su base de datos en ~/.atlantis/hub.db.
# Monta un volumen persistente en /root/.atlantis para no perder cuentas/sesiones.
VOLUME ["/root/.atlantis"]

EXPOSE 8787

# PORT lo inyecta la plataforma (Railway/Render/Fly) y server.py lo lee del entorno.
# Ya escucha en 0.0.0.0 por defecto.
# Crea el workspace dentro del volumen persistente para que los archivos
# del proyecto sobrevivan reinicios igual que la base de datos.
CMD ["sh", "-c", "mkdir -p /root/.atlantis/workspace && cd nexo && python server.py"]
