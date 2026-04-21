#!/bin/bash
# rebuild_all.sh — 严格串行重建向量库
# 一次只跑一个python进程，绝不多进程
set -e

PYTHON="/home/tearsyu/miniconda3/envs/tt/bin/python3"
SCRIPT="/home/tearsyu/.openclaw/workspace/scripts/ingest_v3.py"
BASE="/mnt/d/data/think-tank-data/personalities"

TOTAL=0; OK=0; FAIL=0

for group in "$BASE"/*/; do
    [ -d "$group" ] || continue
    for candidate in "$group"*/; do
        [ -d "$candidate" ] || continue
        if [ -f "$candidate/personality_v3.json" ]; then
            name=$(basename "$candidate")
            TOTAL=$((TOTAL+1))
            echo ""
            echo "========== [$TOTAL] $name =========="
            if $PYTHON "$SCRIPT" "$name" "$candidate" 2>&1; then
                OK=$((OK+1))
            else
                echo "❌ FAILED: $name (exit $?)"
                FAIL=$((FAIL+1))
            fi
            sleep 2
        else
            # nested: group/sub/person
            for person in "$candidate"*/; do
                [ -d "$person" ] || continue
                if [ -f "$person/personality_v3.json" ]; then
                    name=$(basename "$person")
                    TOTAL=$((TOTAL+1))
                    echo ""
                    echo "========== [$TOTAL] $name =========="
                    if $PYTHON "$SCRIPT" "$name" "$person" 2>&1; then
                        OK=$((OK+1))
                    else
                        echo "❌ FAILED: $name (exit $?)"
                        FAIL=$((FAIL+1))
                    fi
                    sleep 2
                fi
            done
        fi
    done
done

echo ""
echo "==========================================="
echo "DONE: $OK ok, $FAIL fail, $TOTAL total"
echo "==========================================="
