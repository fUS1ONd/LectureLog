FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY lecturelog ./lecturelog
COPY server ./server
COPY cli ./cli
COPY bot ./bot
RUN pip install --no-cache-dir .
COPY . .

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
