#!/bin/bash
# 智库数据自动备份脚本
# 备份源: /mnt/d/data2 (D:\data2)
# 备份目标: /mnt/d/data2/_backups/
# 用法: 手动运行 或 加到crontab

set -euo pipefail

SRC="/mnt/d/data"
BACKUP_ROOT="/mnt/d/data2"
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"
MAX_BACKUPS=30  # 保留最近30个备份

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err() { echo -e "${RED}[ERROR]${NC} $*"; }

# 检查源目录
if [ ! -d "$SRC" ] || [ -z "$(ls -A "$SRC" 2>/dev/null)" ]; then
    err "源目录 ${SRC} 为空或不存在"
    exit 1
fi

# 创建备份目录
mkdir -p "$BACKUP_DIR"
log "开始备份: ${SRC} → ${BACKUP_DIR}"

# rsync备份（排除_backups自身）
if command -v rsync &>/dev/null; then
    rsync -a --delete \
        --exclude='_backups' \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.DS_Store' \
        "$SRC/" "$BACKUP_DIR/"
    log "rsync 完成"
else
    # 回退用cp
    cp -a "$SRC" "$BACKUP_DIR"
    log "cp -a 完成"
fi

# 压缩（减少空间占用）
log "压缩中..."
cd "$BACKUP_ROOT"
tar -czf "${TIMESTAMP}.tar.gz" "${TIMESTAMP}/"
rm -rf "${TIMESTAMP}/"
COMPRESSED_SIZE=$(du -sh "${TIMESTAMP}.tar.gz" | cut -f1)
log "压缩完成: ${TIMESTAMP}.tar.gz (${COMPRESSED_SIZE})"

# 清理旧备份（保留最近MAX_BACKUPS个）
BACKUP_COUNT=$(ls -1 *.tar.gz 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
    DELETE_COUNT=$((BACKUP_COUNT - MAX_BACKUPS))
    ls -1t *.tar.gz | tail -n "$DELETE_COUNT" | xargs rm -f
    log "清理了 ${DELETE_COUNT} 个旧备份，保留最近 ${MAX_BACKUPS} 个"
fi

# 统计
TOTAL_BACKUPS=$(ls -1 *.tar.gz 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh . | cut -f1)
log "当前共 ${TOTAL_BACKUPS} 个备份，总占用 ${TOTAL_SIZE}"
log "备份完成 ✓"
