# Use official slim Python image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgl1 libglib2.0-0 ffmpeg tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Upgrade pip and install all Python dependencies in one layer
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy vendored libraries and weights (rarely change, cached)
COPY easyocr ./easyocr
COPY weights ./weights

# Copy the rest of the application code
COPY . .

# Ensure logs directory exists (for FileHandler)
RUN mkdir -p /app/logs

# Set PYTHONPATH so Python can find app module
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8001

# Run Uvicorn pointing to app.main
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
