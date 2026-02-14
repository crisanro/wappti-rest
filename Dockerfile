# Usamos Python 3.11 en versión slim para que pese poco
FROM python:3.11-slim

# Evita que Python genere archivos .pyc y fuerza que los logs salgan de inmediato
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Establecemos el directorio de trabajo
WORKDIR /app

# Instalamos dependencias del sistema necesarias para PostgreSQL y zonas horarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalamos las librerías de Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiamos todo el código de tu API
COPY . .

# Exponemos el puerto 8000
EXPOSE 8000

# Usamos Gunicorn para manejar los procesos en producción
# -w 4: 4 procesos simultáneos (ideal para servidores de 1-2 CPUs)
# -k uvicorn.workers.UvicornWorker: Clase de worker para que FastAPI vuele
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "main:app", "--bind", "0.0.0.0:8000"]