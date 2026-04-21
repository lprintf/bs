#!/usr/bin/env bash
# start.sh — TabShare 容器启动脚本
# 支持根据硬件类型自动构建并启动容器，挂载对应的硬件加速设备。
# 用法: ./start.sh [intel|amd|cpu]

set -euo pipefail

GPU_TYPE="${1:-cpu}"
COMPOSE_OPTS=("-f" "docker-compose.yml")

echo "==== 启动 TabShare ===="
echo "选定硬件模式: $GPU_TYPE"

case "$GPU_TYPE" in
  intel)
    echo "🔨 正在构建 Intel 硬件加速镜像..."
    # 确保基础镜像存在
    docker compose build tabshare
    docker build -f tabshare/Dockerfile.intel -t bs-tabshare-intel .
    
    export TABSHARE_IMAGE="bs-tabshare-intel"
    COMPOSE_OPTS+=("-f" "docker-compose.gpu.yml")
    ;;
    
  amd)
    echo "🔨 正在构建 AMD 硬件加速镜像..."
    docker compose build tabshare
    docker build -f tabshare/Dockerfile.amd -t bs-tabshare-amd .
    
    export TABSHARE_IMAGE="bs-tabshare-amd"
    COMPOSE_OPTS+=("-f" "docker-compose.gpu.yml")
    ;;
    
  cpu|none)
    echo "运行在标准 CPU 模式..."
    export TABSHARE_IMAGE="bs-tabshare:latest"
    ;;
    
  *)
    echo "❌ 错误: 不支持的硬件类型 '$GPU_TYPE'"
    echo "用法: ./start.sh [intel|amd|cpu]"
    exit 1
    ;;
esac

echo "🚀 启动容器..."
docker compose "${COMPOSE_OPTS[@]}" up -d

echo "✅ 启动完成。"
