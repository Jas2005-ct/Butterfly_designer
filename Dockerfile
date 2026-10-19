# Use a slim Python image for a smaller footprint
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Install system dependencies (required for Pillow/Images and psycopg2)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
# Pin the install directory explicitly: the installer used to default to
# /root/.cargo/bin but now defaults to /root/.local/bin, and a mismatched
# PATH entry made `uv sync` fail with "uv: not found".
ENV UV_INSTALL_DIR=/opt/uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/opt/uv:$PATH"

# Copy dependency lock files first to leverage Docker's layer cache
COPY pyproject.toml uv.lock ./

# Install dependencies into the project's virtual environment
# uv sync --frozen creates .venv in /app with all locked dependencies
RUN uv sync --frozen

# Set the PATH to use the project's virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"

# Copy the rest of the application code
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
