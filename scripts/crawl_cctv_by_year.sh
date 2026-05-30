#!/bin/bash
# 分年分段爬取新闻联播，每完成一年写入标记
# 用法: bash crawl_cctv_by_year.sh [--resume]

BASE="/home/tearsyu/.openclaw/workspace"
MARKER_DIR="$BASE/cache/news_cctv/markers"
mkdir -p "$MARKER_DIR"

YEARS=("2019" "2020" "2021" "2022" "2023" "2024")

for year in "${YEARS[@]}"; do
    marker="$MARKER_DIR/${year}.done"
    if [ -f "$marker" ]; then
        echo "[$year] 已完成，跳过"
        continue
    fi
    
    echo "[$year] 开始爬取..."
    start="${year}0101"
    end="${year}1231"
    
    cd "$BASE"
    timeout 7200 ~/miniconda3/envs/tt/bin/python3 scripts/data_acquisition.py \
      --cctv --start "$start" --end "$end" --batch-size 60 \
      2>&1 | tee -a "/tmp/cctv_${year}.log"
    
    if [ $? -eq 0 ]; then
        date > "$marker"
        echo "[$year] ✅ 完成"
    else
        echo "[$year] ⚠️ 可能未完成（超时或异常），日志在 /tmp/cctv_${year}.log"
    fi
    
    sleep 5
done

echo "=== 全部完成 ==="
