FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgl1 libglib2.0-0 ffmpeg tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (cached if requirements.txt doesn’t change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy stable vendored libs + weights first
COPY easyocr ./easyocr
COPY weights ./weights

# Copy the rest of your app
COPY . .

# Make sure logs directory exists (so FileHandler works)
RUN mkdir -p /app/logs

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
