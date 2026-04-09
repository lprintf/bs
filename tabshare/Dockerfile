FROM mcr.microsoft.com/playwright/python:v1.58.0-noble-amd64

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml /app/
RUN uv pip install --system fastapi uvicorn playwright pydantic httpx websockets

EXPOSE 8085

CMD ["python", "-u", "-m", "server.app"]
