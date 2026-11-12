FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-liberation && rm -rf /var/lib/apt/lists/*
RUN useradd -m app && mkdir /app && chown app /app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app . .
USER app
CMD ["sh","-c","uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
