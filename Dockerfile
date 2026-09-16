FROM python:3.12-slim

# Instalar dependencias del sistema operativo (necesarias para yt-dlp y compilar paquetes)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Configurar el directorio de trabajo
WORKDIR /app

# Instalar dependencias de Python optimizando caché de capas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código del bot
COPY . .

# Comando por defecto para iniciar el bot
CMD ["python", "run.py"]
