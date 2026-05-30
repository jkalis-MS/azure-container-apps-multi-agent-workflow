FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
COPY tools/ tools/
COPY *.py ./
RUN pip install --no-cache-dir .
EXPOSE 8003
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
