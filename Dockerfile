# Use official slim Python image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies for numpy, OpenCV, EasyOCR, Tesseract, and Poppler
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libopenblas-dev \
    liblapack-dev \
    libglib2.0-0 \
    ffmpeg \
    tesseract-ocr \
    poppler-utils \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Upgrade pip and install core scientific packages from wheels first
RUN pip install --upgrade pip setuptools wheel \
    && pip install --only-binary=:all: numpy==1.26.4 scipy==1.11.4 \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y bidi || true \
    && pip install --force-reinstall python-bidi==0.6.6

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
