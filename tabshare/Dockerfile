FROM mcr.microsoft.com/playwright/python:v1.58.0-noble-amd64

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml /app/
RUN uv pip install --system fastapi uvicorn rebrowser-playwright pydantic httpx websockets python-multipart python-geoip-geolite2

EXPOSE 8085


# rebrowser-playwright 使用基础镜像自带的 Chromium
# 如需用独立 Chrome for Testing，设置 CHROME_PATH
ENV CHROME_PATH=/data/opt/chromium/chrome

CMD ["python", "-u", "-m", "server.app"]
