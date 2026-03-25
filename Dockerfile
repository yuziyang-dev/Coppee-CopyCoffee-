FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/temp /app/output && chown -R appuser:appuser /app

USER appuser

ENV PYTHONUNBUFFERED=1 \
    TEMP_DIR=/app/temp \
    OUTPUT_DIR=/app/output

EXPOSE 7860

CMD ["python", "start.py"]
