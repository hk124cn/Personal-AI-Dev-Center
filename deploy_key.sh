#!/bin/bash
# ============================================================
# Dev Center - SSH 密钥部署脚本
# 将公钥复制到各服务器，实现免密登录
# 用法: bash deploy_key.sh
# ============================================================

set -e

KEY_PATH="$HOME/.ssh/dev_center"
PUB_KEY="$KEY_PATH.pub"

if [ ! -f "$PUB_KEY" ]; then
    echo "[!] 公钥不存在: $PUB_KEY"
    echo "    请先运行: ssh-keygen -t ed25519 -C 'dev-center@win11' -f $KEY_PATH -N ''"
    exit 1
fi

echo "公钥内容:"
cat "$PUB_KEY"
echo ""
echo "============================================================"

# 从 config.json 读取服务器列表
CONFIG_FILE="$(dirname "$0")/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "[!] 配置文件不存在: $CONFIG_FILE"
    exit 1
fi

# 逐个服务器部署密钥
deploy_to() {
    local HOST=$1
    local PORT=$2
    local USER=$3
    local NAME=$4

    echo ""
    echo ">>> 部署到 $NAME ($USER@$HOST:$PORT) ..."
    echo "    请输入该服务器的密码（仅需这一次）:"

    # 使用 ssh-copy-id（如果可用）或手动复制
    if command -v ssh-copy-id &>/dev/null; then
        ssh-copy-id -i "$PUB_KEY" -p "$PORT" "$USER@$HOST" 2>/dev/null
    else
        cat "$PUB_KEY" | ssh -p "$PORT" "$USER@$HOST" \
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    fi

    if [ $? -eq 0 ]; then
        echo "    [OK] $NAME 密钥部署成功"
        # 验证免密登录
        ssh -p "$PORT" -i "$KEY_PATH" -o BatchMode=yes -o ConnectTimeout=5 "$USER@$HOST" "echo '    [OK] 免密登录验证通过'" 2>/dev/null || \
            echo "    [!] 免密登录验证失败，请检查"
    else
        echo "    [FAIL] $NAME 部署失败"
    fi
}

echo ""
echo "开始部署 SSH 密钥到各服务器..."
echo "（每个服务器需要输入一次密码）"

# 阿里云
deploy_to "47.xx.xx.xx" 22 "root" "阿里云轻量"

# 腾讯云
deploy_to "101.xx.xx.xx" 22 "root" "腾讯云轻量"

# GCP
deploy_to "34.xx.xx.xx" 22 "admin" "GCP 主力"

echo ""
echo "============================================================"
echo "部署完成！测试命令:"
echo "  ssh -i $KEY_PATH root@47.xx.xx.xx"
echo "  ssh -i $KEY_PATH root@101.xx.xx.xx"
echo "  ssh -i $KEY_PATH admin@34.xx.xx.xx"
