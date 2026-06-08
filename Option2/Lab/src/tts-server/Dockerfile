FROM pytorch/pytorch:2.2.2-cuda12.1-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    COQUI_TOS_AGREED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY *.py ./
COPY voice_samples/ voice_samples/

# TTS pins numpy==1.22.0 for py3.10
# Use constraints to prevent transformers>=4.46 (BeamSearchScorer removed)
RUN echo "transformers<4.46.0" > /tmp/constraints.txt && \
    pip install --no-cache-dir -c /tmp/constraints.txt TTS==0.22.0 && \
    rm /tmp/constraints.txt
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "huggingface-hub>=0.20" \
    "opentelemetry-api>=1.20" \
    "opentelemetry-sdk>=1.20" \
    "opentelemetry-instrumentation-fastapi>=0.41b0" \
    "opentelemetry-exporter-otlp-proto-grpc>=1.20"

EXPOSE 8004
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004", "--workers", "1"]
