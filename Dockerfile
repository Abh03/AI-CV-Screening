FROM python:3.11.16-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TORCH_HOME=/app/.cache/torch
ENV EMBEDDING_MODEL_PATH=/opt/models/embedding \
    RERANKER_MODEL_PATH=/opt/models/reranker

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libpq-dev \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgomp1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file (with torch removed)
COPY requirements.txt .

# 1. Upgrade build tools
# 2. Install CPU-only PyTorch first
# 3. Install remaining application dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

COPY scripts/provision_models.py /tmp/provision_models.py
RUN python /tmp/provision_models.py && rm /tmp/provision_models.py
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

# Copy only runtime code and migration assets; local checkout files never enter the image.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Healthcheck probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/ready || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
