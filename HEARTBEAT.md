# HEARTBEAT.md

## 论文下载监控
- `pgrep -f scholar_download.py` — 进程存活检查
- 累计论文数：`find /mnt/d/data -name "paper_*.txt" 2>/dev/null | wc -l`
- 进程挂了 → 自动重启（先学者后心理学家）
- 每2小时汇报一次，不频繁打扰

## 待办跟踪（VPN恢复后推进）
- Finnhub ETF指数数据
- UCDP/ACLED冲突数据
- 克里斯·哈德纳吉素材补采集+蒸馏（当前唯一缺蒸馏的人）

## 定期任务
- 检查磁盘空间（阈值85%）
