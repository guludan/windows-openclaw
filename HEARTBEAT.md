# HEARTBEAT.md

## 论文下载监控
每次heartbeat检查论文下载进程是否存活，汇报进度。

```python
# 检查下载进程
import subprocess
result = subprocess.run(['pgrep', '-f', 'scholar_download.py'], capture_output=True, text=True)
if result.returncode != 0:
    # 进程挂了，重启
    ...
```

### 检查项
1. `pgrep -f scholar_download.py` — 进程是否存活
2. `find ... -name "paper_*.txt" | wc -l` — 累计论文数
3. 如果进程死了 → 自动重启（先学者后心理学家）
4. 每2小时汇报一次进度（不频繁打扰）
