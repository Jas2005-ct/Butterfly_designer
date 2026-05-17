# Use a slim Python image for a smaller footprint
# Base image
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Install system dependencies (required for Pillow/Images and psycopg2)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first to leverage Docker build cache
COPY requirements.txt ./

# Install Python dependencies via pip
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
# Copy application code
COPY . .

# Expose the port Django runs on
EXPOSE 8000

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Use entrypoint to collect static and start server
ENTRYPOINT ["/entrypoint.sh"]
