FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv
ENV PATH="/root/.local/bin:${PATH}"

# Set working directory
WORKDIR /app

# Configure uv to use a container-specific venv location (not .venv which might conflict with mounted volumes)
ENV UV_PROJECT_ENVIRONMENT=/app/.venv-container

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Default command (can be overridden in docker-compose)
CMD ["uv", "run", "fastapi", "dev", "main.py", "--host", "0.0.0.0", "--port", "8000"]


