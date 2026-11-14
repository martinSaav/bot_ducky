# --- Stage 1: build the Angular dashboard ---------------------------------
# Built here so the image always serves the dashboard for its own code, not
# whatever dashboard/dist happened to be on the host.
FROM node:24-slim AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
# The bot serves it under /dashboard/, so asset URLs must start there too.
RUN npx ng build --base-href /dashboard/

# --- Stage 2: the bot -----------------------------------------------------
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

# Copy the rest of the bot's code (.dockerignore keeps secrets and data out)
COPY . .
COPY --from=dashboard /dashboard/dist ./dashboard/dist

# Which code this image is: shown in /health and on the dashboard.
# Pass it with: GIT_COMMIT=$(git describe --always --dirty) docker compose up -d --build bot
ARG GIT_COMMIT=unknown
ENV BOT_COMMIT=$GIT_COMMIT
RUN date -u +%Y-%m-%dT%H:%M:%SZ > /app/.built_at

# Default command to start the bot
CMD ["python", "run.py"]
