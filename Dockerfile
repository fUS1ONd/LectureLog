FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install ".[dev]" || pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libreoffice poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# JS-рантайм для yt-dlp (YouTube требует исполнения JS-челленджей).
# Установщик кладёт бинарь в $DENO_INSTALL/bin/deno → /usr/local/bin/deno.
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s v2.9.3 \
    && apt-get purge -y curl unzip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir --no-deps -e .
RUN chmod +x docker/entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
