FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependencies first to leverage Docker cache
COPY pyproject.toml .
RUN uv sync --no-dev

# Copy the application code
COPY main_ptb.py .
COPY log.py .

# Run the application
CMD ["uv", "run", "main_ptb.py"]