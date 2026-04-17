FROM mcr.microsoft.com/playwright/python:v1.58.0-noble-amd64

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 安装 Google Chrome Stable（从本地 deb 或在线下载）
# 将 deb 放到 data/opt/deb/ 可跳过下载
COPY data/opt/deb/google-chrome-stable_current_amd64.deb /tmp/chrome.deb
RUN apt-get update \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/
RUN uv pip install --system fastapi uvicorn rebrowser-playwright pydantic httpx websockets python-multipart python-geoip-geolite2 "sqlalchemy[asyncio]" asyncpg

# 预创建数据目录并赋权给 ubuntu (uid 1000，与宿主机一致)
RUN mkdir -p /data/browsers /data/recordings /data/downloads /data/uploads \
    && chown -R ubuntu:ubuntu /data

EXPOSE 8085

# 使用 apt 安装的 Google Chrome Stable
ENV CHROME_PATH=/opt/google/chrome/google-chrome

# 以非 root 用户运行（Chrome 启用 sandbox）
USER ubuntu

CMD ["python", "-u", "-m", "server.app"]
