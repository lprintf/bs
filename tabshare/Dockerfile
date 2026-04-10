FROM mcr.microsoft.com/playwright/python:v1.58.0-noble-amd64

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 使用 Playwright 内置 Chromium（与 g2api 一致，代理兼容性更好）
# 如需外部 Chrome，在 docker-compose.yml environment 中设置 CHROME_PATH

WORKDIR /app

COPY pyproject.toml /app/
RUN uv pip install --system fastapi uvicorn playwright pydantic httpx websockets python-multipart python-geoip-geolite2

EXPOSE 8085

CMD ["python", "-u", "-m", "server.app"]
