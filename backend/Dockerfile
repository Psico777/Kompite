# =============================================================================
# KOMPITE BACKEND - Dockerfile
# Imagen optimizada para FastAPI con soporte async
# =============================================================================

FROM python:3.12-slim-bookworm

# Metadata
LABEL maintainer="Kompite Team"
LABEL description="Kompite Backend - Infraestructura de Arbitraje"

# Variables de entorno
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements primero para cache de Docker
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente
COPY . .

# Crear usuario no-root para seguridad
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

# Puerto de la aplicación
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Comando por defecto - Usa combined_app que envuelve FastAPI con Socket.IO
CMD ["uvicorn", "app.main:combined_app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
