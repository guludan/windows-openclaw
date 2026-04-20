# Think-Tank — 人格智库系统

多视角人格智库：军事/学者/心理学家，通过RAG驱动的多维专家分析生成战略研判报告。

## 目录结构

```
think-tank/
├── personalities/          # 人格数据（symlink → D盘）
│   ├── military/           #   联参25人（毛泽东、彭德怀、粟裕等）
│   ├── scholars/           #   学者（按领域分组）
│   │   ├── central-asia/   #   中亚研究
│   │   ├── economics/      #   经济学（卢麒元、张维迎、温铁军）
│   │   ├── ir-security/    #   国际安全（沈逸、艾跃进）
│   │   ├── ir-theory/      #   国际关系理论（Mearsheimer、阎学通）
│   │   ├── middle-east/    #   中东研究
│   │   ├── persian-islamic/#   波斯-伊斯兰研究
│   │   └── religious-studies/ # 宗教研究
│   └── psychologists/      #   心理学家
├── templates/
│   └── distill_prompt_v3.md #  V3蒸馏prompt模板
└── reports/                # 分析报告（symlink → D盘）
```

## 核心脚本（workspace/scripts/）

| 脚本 | 用途 |
|------|------|
| `ingest_v3.py` | 单人v3入库（materials+insights+core_profile） |
| `ingest_batch.py` | 批量入库 |
| `rebuild_materials.py` | 批量重建materials向量 |
| `collect_new_scholars.py` | 新学者素材采集 |
| `think_tank_pipeline_v2.py` | 智库报告生成pipeline |
| `gen_report.py` | RAG驱动报告生成 |
| `topic_decomposer.py` | 子议题拆分 |
| `historical_context.py` | 历史事件引擎（Redis） |
| `distill_deng_v3.py` | V3蒸馏示例 |
| `cache_utils.py` | 缓存工具 |
| `split_transcribe.py` | 音频转写 |
| `stt_server.py` | STT服务 |

## 向量库

- **路径**: `/mnt/d/data/think-tank-data/_chromadb/lacan_db`
- **Embedding**: Ollama bge-m3 (1024维, 本地)
- **Collections**: core_profile, insights, materials, unconscious, repetition, transference, drive, tension

## 蒸馏工作流

详见 `WORKFLOWS.md`

```
素材采集 → 提取 → V3蒸馏（含逻辑溯源） → 向量入库 → RAG报告生成
```

## 每个人物目录结构

```
<人物名>/
├── meta.json              # 基本信息
├── personality_v3.json    # V3人格模型（含logical_tracing）
├── materials/             # 原始素材（论文、网页、视频转录）
└── papers/texts/          # 学术论文全文
```
