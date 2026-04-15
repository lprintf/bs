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
RUN uv pip install --system fastapi uvicorn rebrowser-playwright pydantic httpx websockets python-multipart python-geoip-geolite2

EXPOSE 8085

# 使用 apt 安装的 Google Chrome Stable
ENV CHROME_PATH=/opt/google/chrome/google-chrome

# Xvfb 有头模式 + Python 应用
CMD ["bash", "/app/entrypoint.sh"]
