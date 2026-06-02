#!/usr/bin/env python3
"""统一入口：一键生成今日市场简报"""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from analysis.sectors import sector_daily_chart
from analysis.signals import all_signals_summary
from datetime import datetime

def daily_briefing() -> str:
    parts = ["A股简报 | " + datetime.now().strftime('%m-%d %H:%M')]
    parts.append("=" * 50)
    
    # 板块部分
    parts.append("")
    parts.append(sector_daily_chart())
    
    # 信号部分
    parts.append("")
    parts.append(all_signals_summary())
    
    return "\n".join(parts)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(daily_briefing())
