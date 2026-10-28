FROM python:3.12-slim

# Tesseract with Ukrainian language pack for scanned PDFs
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ukr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so Docker caches this layer between code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY data ./data

# Print logs immediately instead of buffering them
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot"]