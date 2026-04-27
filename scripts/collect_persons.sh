#!/bin/bash
# 采集缺失素材 - 直接用 data_search.py CLI
set -e

PYTHON=~/miniconda3/envs/tt/bin/python3
DS=~/.openclaw/workspace/skills/data-search/scripts/data_search.py
BASE=/mnt/d/data/think-tank-data/personalities

# API Keys
export EXA_API_KEY="7be7a599-fa7b-4107-9ef1-ad96646156c2"
export TAVILY_API_KEY="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"
export SERPAPI_KEY="848c4ec87cac7776b9404f0af1aeb36fce936d1aa3aebe899664eaa43e67ccc2"

collect() {
    local cat=$1 name=$2 search=$3
    local dir=$BASE/$cat/$name
    mkdir -p "$dir/materials" "$dir/papers/raw"
    
    echo "=========================================="
    echo "采集: $search"
    echo "=========================================="
    
    # 通用+中文搜索
    echo "[1] person搜索..."
    $PYTHON $DS person "$search" --layers general,chinese --max 10 --output-dir "$dir/materials" 2>&1 || true
    sleep 8
    
    # 学术搜索+下载
    echo "[2] 学术搜索+下载..."
    $PYTHON $DS academic "$search" --source all --max 10 --download --output-dir "$dir/papers/raw" --format text 2>&1 || true
    sleep 8
    
    echo "[3] 完成: $search"
    echo ""
}

# 印太（0素材）
collect scholars/indo-pacific Kurt_Campbell "Kurt Campbell"
collect scholars/indo-pacific Oriana_Mastro "Oriana Mastro"
collect scholars/indo-pacific Richard_Bush "Richard Bush"

# 政治预测（0素材）
collect scholars/political-forecasting Nate_Silver "Nate Silver"
collect scholars/political-forecasting Philip_Tetlock "Philip Tetlock"

# 科技地缘（0素材）
collect scholars/tech-geopolitics Chris_Miller "Chris Miller"
collect scholars/tech-geopolitics Mariana_Mazzucato "Mariana Mazzucato"

# 信息战/制裁（1-5素材）
collect scholars/information-warfare PW_Singer "PW Singer"
collect scholars/sanctions-finance Elizabeth_Economy "Elizabeth Economy"
collect scholars/information-warfare Thomas_Rid "Thomas Rid"
collect scholars/information-warfare Renee_DiResta "Renee DiResta"

# 素材偏少（<10）
collect scholars/economics Joseph_Stiglitz "Joseph Stiglitz"
collect scholars/ir-theory 阎学通 "Yan Xuetong"
collect scholars/middle-east 牛新春 "Niu Xinchun"

echo ""
echo "全部完成"
