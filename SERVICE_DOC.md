# 小狗蛋 OpenClaw 服务文档

## 一、环境要求

- **OS**: Linux (Alinux 8 / Ubuntu 20.04+)
- **CPU**: 2核+, **内存**: 3.5G+
- **Node.js**: v22+
- **Python**: Miniconda3 (base=3.13, stt=3.11)
- **OpenClaw**: latest (`npm i -g openclaw`)
- **Playwright**: `npx playwright install chromium` + 系统依赖 (atk, cups-libs, libXdamage, libXcomposite 等)
- **STT**: jianchang512/stt (faster-whisper base, conda stt 环境, port 9977)
- **Embedding API**: SiliconFlow (Qwen3-Embedding-8B)

## 二、核心服务

| 服务 | 说明 | 端口/路径 |
|------|------|----------|
| OpenClaw Gateway | 主服务 | :18789 (lan) |
| STT 语音识别 | faster-whisper | http://127.0.0.1:9977 |
| 飞书机器人 | WebSocket 长连接 | 内置插件 |
| 向量库 (经济学) | ChromaDB | think-tank/lacan_db |
| 向量库 (联参) | ChromaDB | think-tank/joint-staff/lacan_db |

## 三、OpenClaw 配置 (openclaw.json)

```jsonc
{
  "gateway": {
    "mode": "local",
    "port": 18789,
    "bind": "lan"
  },
  "agents": {
    "defaults": {
      "workspace": "/home/<user>/.openclaw/workspace",
      "models": {
        "zai/glm-5.1": { "alias": "GLM5.1", "fallbacks": ["zai/glm-4.7", "zai/glm-5-turbo"] },
        "zai/glm-5-turbo": {},
        "zai/glm-4.7": { "alias": "GLM4.7" }
      },
      "model": { "primary": "zai/glm-5.1" }
    }
  },
  "channels": {
    "feishu": {
      "enabled": true,
      "dmPolicy": "pairing",
      "accounts": {
        "default": {
          "appId": "<你的飞书AppID>",
          "appSecret": "<你的飞书AppSecret>",
          "name": "小狗蛋"
        }
      }
    }
  }
}
```

### 飞书开放平台配置

1. 创建企业自建应用，获取 AppID + AppSecret
2. 权限：批量导入（见 OpenClaw docs/channels/feishu.md）
3. 启用机器人能力
4. 事件订阅：选「长连接」，添加 `im.message.receive_v1`
5. 发布应用

## 四、Python 依赖

```bash
# 基础
pip install yfinance akshare chromadb requests

# STT 环境 (conda)
conda create -n stt python=3.11
conda activate stt
pip install faster-whisper

# Playwright
npx playwright install chromium
# 系统依赖 (RHEL/CentOS):
yum install -y atk cups-libs libXdamage libXcomposite libXrandr mesa-libgbm pango alsa-lib
```

## 五、关键 API Keys

| 服务 | Key | 用途 |
|------|-----|------|
| 智谱 AI (zai) | `<your_key>` | LLM 主模型 |
| SiliconFlow | `sk-tnaasdpeozarapwsxndsewyobdlvmwauwoemzmwcvltbapmj` | Embedding 向量化 |
| Tavily | `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` | 网络搜索 |
| 飞书 | AppID + AppSecret | 机器人 |

## 六、技能清单 (33个)

### 📊 数据采集
| 技能 | 说明 |
|------|------|
| `policy-monitor` | 央行+财联社政策数据采集管道 |
| `stock-query` | A股行情/K线/财务 (Yahoo+AKShare, 含缓存) |
| `thinktank-discuss` | 经济学智库RAG讨论系统 |
| `playwright-scraper` | 通用网页爬取 |
| `web-scraper-jina` | Jina Reader 网页提取 |
| `zhihu-keyword-content-search` | 知乎内容搜索 |
| `agent-stealth-scraper` | 反检测爬虫 |

### 🧠 人格系统
| 技能 | 说明 |
|------|------|
| `person-distillation` | 人格蒸馏 (B站/文章→结构化→Lacan四维) |
| `person-replication` | 人格还原/预测 |
| `think-tank` | 智库报告生成 |
| `agent-council` | 多Agent议会 |

### 🔧 基础设施
| 技能 | 说明 |
|------|------|
| `github-cli` | Git 操作 |
| `docker-essentials` | Docker 管理 |
| `debug-pro` | 调试工具 |
| `self-improving-agent` | 自我进化 |
| `knowledge-capture` | 知识捕获 |
| `extract-pdf-text` | PDF 提取 |

### 📈 业务
| 技能 | 说明 |
|------|------|
| `fashion-trend-ops` | 时尚趋势运营 |
| `seo-geo-ops` | SEO/GEO 运营 |
| `competitor-watch` | 竞品监控 |
| `academic-research-hub` | 学术研究 |

## 七、向量库结构

### 经济学者 (think-tank/lacan_db)

```
lacan_db/
├── unconscious/    # 深层分析逻辑
├── repetition/     # 分析框架重复模式  
├── transference/   # 评价立场转移
├── drive/          # 核心驱动力/立场
└── core_profile/   # 学者画像
```

已入库学者：温铁军(956 chunks)、张维迎(176 chunks)、卢麒元(~40 chunks)

### 联参 (think-tank/joint-staff/lacan_db)

5集合共 9777 向量，覆盖 25+ 军事/政治人物。

## 八、关键脚本路径

```
workspace/
├── scripts/
│   ├── lacan_vecdb_v2.py          # 联参向量库入库
│   ├── ingest_all_vecdb.py        # 联参批量入库
│   ├── _extract_one.py            # 单人结构化提取
│   └── distill_v2.py              # 人格蒸馏v2
├── skills/
│   ├── policy-monitor/scripts/
│   │   └── pipeline.py            # 政策采集管道
│   ├── stock-query/scripts/
│   │   └── stock_query.py         # A股查询工具
│   ├── thinktank-discuss/scripts/
│   │   ├── thinktank_vecdb.py     # 经济学者向量库+RAG
│   │   ├── quick_ingest.py        # 快速入库
│   │   └── collect_luxiyuan_web.py # Tavily文章采集
│   └── person-distillation/       # 蒸馏技能目录
└── think-tank/
    ├── lacan_db/                  # 经济学者向量库
    ├── joint-staff/lacan_db/      # 联参向量库
    └── scholars/economics/        # 经济学者数据
        ├── 温铁军/
        ├── 张维迎/
        └── 卢麒元/
```

## 九、常用命令

```bash
# Gateway
openclaw gateway status
openclaw gateway restart
openclaw logs --follow

# 飞书配对
openclaw pairing list feishu
openclaw pairing approve feishu <CODE>

# 向量库
python3 scripts/thinktank_vecdb.py status
python3 scripts/thinktank_vecdb.py search "查询" --person 温铁军 --top 5
python3 scripts/quick_ingest.py --dir <学者目录>

# A股查询
/home/admin/miniconda3/bin/python3 skills/stock-query/scripts/stock_query.py quote 600519
/home/admin/miniconda3/bin/python3 skills/stock-query/scripts/stock_query.py kline 600519 --period 1mo
/home/admin/miniconda3/bin/python3 skills/stock-query/scripts/stock_query.py financial 600519

# 政策采集
/home/admin/miniconda3/bin/python3 skills/policy-monitor/scripts/pipeline.py --sources pbc cls -o /tmp/policy.json
```

## 十、部署新实例检查清单

- [ ] 安装 OpenClaw + Node.js 22
- [ ] 安装 Miniconda3, 创建 stt 环境
- [ ] 安装 Playwright + chromium + 系统依赖
- [ ] 配置 openclaw.json (模型、飞书、端口)
- [ ] 启动 STT 服务 (systemd)
- [ ] 启动 Gateway (systemd)
- [ ] 飞书开放平台创建应用 + 配置权限 + 事件订阅
- [ ] 安装 Python 依赖 (yfinance, akshare, chromadb)
- [ ] 从 GitHub 克隆 workspace
- [ ] 测试飞书配对
- [ ] 测试 A股查询
- [ ] 测试政策采集
