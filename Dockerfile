FROM python:3.12-slim

# Install OS dependencies (needed by yt-dlp and to build packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Install Python dependencies, making good use of the layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the bot's code
COPY . .

# Default command to start the bot
CMD ["python", "run.py"]
