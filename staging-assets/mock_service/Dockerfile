# Use official high-performance slim Python runtime
FROM python:3.11-slim

# Force stdout/stderr buffering off
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

# Install system dependencies (if any)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest
COPY requirements.txt .

# Install python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy API code and ledger database
COPY main.py .
COPY ledger.csv .

# Run high-performance production server (Uvicorn)
# Binds to port 8080 to match Cloud Run container defaults
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
