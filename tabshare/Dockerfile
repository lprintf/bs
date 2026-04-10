FROM mcr.microsoft.com/playwright/python:v1.58.0-noble-amd64

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml /app/
RUN uv pip install --system fastapi uvicorn playwright pydantic httpx websockets python-multipart python-geoip-geolite2

EXPOSE 8085

# Chrome 二进制通过挂载 data/opt/chromium 提供，无需在镜像内安装
ENV CHROME_PATH=/data/opt/chromium/chrome

CMD ["python", "-u", "-m", "server.app"]
