FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NWIS_ROOT=/app

WORKDIR /app

# Tesseract is required by the document ingestion pipeline.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tesseract-ocr libgl1 libglib2.0-0 curl; \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY nwis_common ./nwis_common
COPY backend ./backend
COPY ml ./ml
COPY data_pipeline ./data_pipeline
COPY config ./config
COPY scripts ./scripts

# Run as a non-root user.
RUN useradd --create-home --uid 10001 nwis && chown -R nwis:nwis /app
USER nwis

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
