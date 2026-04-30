#!/usr/bin/env bash
# sync.sh — 基于 rsync + sshpass 同步项目到远程服务器
# 用法: ./sync.sh [user@host] [remote_path]
# 示例: ./sync.sh shawn@192.168.0.37 /home/shawn/bs
# 示例: ./sync.sh shawn@192.168.0.96 /home/shawn/bs
# 需要手动复制deb文件: 
# scp -r ./tabshare/data/opt/deb shawn@192.168.0.37:/home/shawn/bs/tabshare/data/opt/deb
# scp -r ./tabshare/data/opt/deb shawn@192.168.0.96:/home/shawn/bs/tabshare/data/opt/deb
# scp -r ./tabshare/data/opt/deb shawn@192.168.0.24:/home/shawn/bs/tabshare/data/opt/deb

set -euo pipefail

# ---------- 配置（可通过命令行参数或环境变量覆盖） ----------
REMOTE="${1:-${SYNC_REMOTE:-shawn@192.168.0.24}}"
REMOTE_PATH="${2:-${SYNC_PATH:-~/bs}}"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------- 检查依赖 ----------
for cmd in rsync sshpass; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "❌ 需要安装 $cmd: sudo apt install $cmd"
    exit 1
  fi
done

# ---------- 密码输入 ----------
if [ -z "${SSHPASS:-}" ]; then
  read -rsp "🔑 SSH 密码 ($REMOTE): " SSHPASS
  echo
  export SSHPASS
fi

# ---------- 构建 rsync 排除规则 ----------
# 1. 始终排除 .git 目录
# 2. 读取 .gitignore 生成排除规则
RSYNC_OPTS=(
  -avz --delete
  --exclude=".git"
  --filter=":- ${LOCAL_DIR}/.gitignore"
  -e "sshpass -e ssh -o StrictHostKeyChecking=no"
)

# ---------- 创建远程目录 ----------
sshpass -e ssh -o StrictHostKeyChecking=no "$REMOTE" "mkdir -p '$REMOTE_PATH'"

# ---------- 同步 ----------
echo "📦 同步 $LOCAL_DIR → $REMOTE:$REMOTE_PATH"
rsync "${RSYNC_OPTS[@]}" "$LOCAL_DIR/" "$REMOTE:$REMOTE_PATH/"

echo "✅ 同步完成"
