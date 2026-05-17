# Use a slim Python image for a smaller footprint
FROM python:3.11-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set the working directory
WORKDIR /app

# Install system dependencies (required for Pillow/Images)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only the dependency files first to leverage Docker's cache
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment
RUN uv sync --frozen

# Copy the rest of the application code
COPY . .

# Expose the port Django runs on
EXPOSE 8000

# Compile static assets during Docker build for WhiteNoise
RUN uv run python manage.py collectstatic --noinput

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Command to start gunicorn production WSGI server
CMD uv run gunicorn core.wsgi:application --bind 0.0.0.0:${PORT:-8000}
