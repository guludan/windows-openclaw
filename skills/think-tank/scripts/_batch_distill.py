#!/usr/bin/env python3
"""
_batch_distill.py — 批量V3蒸馏（子session调用）

用法:
    python3 _batch_distill.py <person_dir>              # 蒸馏单人物
    python3 _batch_distill.py --list                    # 列出缺v3的人物
    python3 _batch_distill.py --all                     # 蒸馏所有缺v3的人物
    python3 _batch_distill.py --gen-record <person_dir> # 生成蒸馏记录文件
    python3 _batch_distill.py <person_dir> --per-file   # 生成逐篇蒸馏prompt
    python3 _batch_distill.py <person_dir> --merge-per-file  # 合并逐篇蒸馏结果

注意:
    - 大素材自动分段（>60000字符）
    - 逐篇蒸馏模式用于素材过多时LLM上下文不够的场景
"""

import json
import sys
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Dict, Any

# 导入公共切分函数
from _distill_utils import split_material_into_chunks

BASE_DIR = Path("/mnt/d/data/think-tank-data/personalities")
MAX_CHARS = 60000


def get_materials(person_dir: Path) -> List[Path]:
    """获取素材文件列表，从数据库读取非废弃状态的文件
    
    优先从 _db.sqlite 查询 status != 'discarded' 的文件，
    确保质量评分过滤生效。如果数据库不可用则回退到文件系统扫描。
    """
    db_path = person_dir / "_db.sqlite"
    
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            # 查询非废弃状态的文件
            rows = conn.execute(
                "SELECT filename, file_path FROM items WHERE status != 'discarded' ORDER BY char_count"
            ).fetchall()
            conn.close()
            
            files = []
            for row in rows:
                filename = row['filename']
                # 尝试从多个可能的位置找到文件
                for subdir in ["materials/raw", "papers/raw"]:
                    fp = person_dir / subdir / filename
                    if fp.exists() and fp.stat().st_size >= 200:
                        files.append(fp)
                        break
            
            if files:
                return sorted(files, key=lambda f: f.stat().st_size)
        except Exception as e:
            print(f"  ⚠ 数据库查询失败，回退到文件系统扫描: {e}", file=sys.stderr)
    
    # 回退：直接扫描文件系统（不检查质量评分）
    files = []
    for subdir in ["materials/raw", "papers/raw"]:
        raw_dir = person_dir / subdir
        if raw_dir.exists():
            for pattern in ["*.md", "*.txt"]:
                files.extend(f for f in raw_dir.glob(pattern) if f.stat().st_size >= 200)
    files.sort(key=lambda f: f.stat().st_size)
    return files


def get_db(person_dir: Path) -> sqlite3.Connection:
    """获取人物数据库连接"""
    db_path = person_dir / "_db.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}. Run init_db.py first.")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def get_category(person_dir: Path, filepath: Path) -> str:
    """判断素材属于 materials 还是 papers"""
    try:
        rel = filepath.relative_to(person_dir)
        parts = rel.parts
        if parts[0] == 'papers':
            return 'papers'
    except ValueError:
        pass
    return 'materials'


def generate_distilled_record(person_dir: Path):
    """为已蒸馏的人物生成 _distilled_files.json"""
    # 检查是否存在 personality_v3.json
    pv3_path = person_dir / "personality_v3.json"
    if not pv3_path.exists():
        print(f"错误: 找不到 {pv3_path}", file=sys.stderr)
        sys.exit(1)

    # 获取所有素材文件
    materials = get_materials(person_dir)
    if not materials:
        print(f"警告: {person_dir} 没有素材文件")
        return

    # 生成记录
    record_path = person_dir / "_distilled_files.json"
    record = {
        "last_distill_time": datetime.now().isoformat(),
        "distilled_files": sorted([f.name for f in materials])
    }

    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    print(f"已生成蒸馏记录: {record_path}")
    print(f"  - 素材文件数: {len(materials)}")
    print(f"  - 蒸馏时间: {record['last_distill_time']}")


def generate_per_file_prompts(person_dir: Path) -> None:
    """生成逐篇蒸馏prompt

    为每个素材文件生成独立的蒸馏prompt，保存到 _per_file_prompts/ 目录
    大文件会自动按段落切分成多个chunks（每个chunk不超过15000字符）
    """
    # 获取素材文件
    materials = get_materials(person_dir)
    if not materials:
        print(f"错误: {person_dir} 没有素材文件", file=sys.stderr)
        sys.exit(1)

    # 创建输出目录
    prompts_dir = person_dir / "_per_file_prompts"
    prompts_dir.mkdir(exist_ok=True)

    # 读取人物名
    person_name = person_dir.name

    # 为每个素材文件生成prompt
    print(f"生成逐篇蒸馏prompts: {len(materials)} 个文件")
    prompt_index = 0
    for i, material_file in enumerate(materials, 1):
        # 读取素材内容
        material_content = material_file.read_text(encoding='utf-8', errors='ignore')

        # 按段落切分（大文件自动切分）
        chunks = split_material_into_chunks(material_content, material_file.name, max_chars=15000, overlap_paragraphs=2)

        # 打印切分统计
        if len(chunks) > 1:
            print(f"  [{i}/{len(materials)}] {material_file.name}: 切分为 {len(chunks)} 个chunks (共{len(material_content):,}字符)")
        else:
            print(f"  [{i}/{len(materials)}] {material_file.name}: {len(material_content):,}字符")

        # 为每个chunk生成prompt
        for chunk in chunks:
            prompt_index += 1

            # 构建chunk说明（如果有切分）
            chunk_info = ""
            if chunk["total_chunks"] > 1:
                chunk_info = f"\n**说明**: 本文件较大，已切分为第 {chunk['index']}/{chunk['total_chunks']} 部分，chunk之间有2个段落重叠以保持上下文连贯性。"
                result_filename = chunk["result_filename"]
            else:
                result_filename = f"{material_file.name}.json"

            # 生成prompt
            prompt = f"""# 人格蒸馏 Prompt v3（逐篇蒸馏模式）

你是人格蒸馏专家。根据提供的人物素材，提取结构化的 insights_with_evidence。

## 任务说明

这是逐篇蒸馏模式：你只需要从这一篇素材中提取 insights_with_evidence，不需要生成完整的人格模型。{chunk_info}

## 输出格式

只输出 insights_with_evidence 的 JSON 数组，格式如下：

```json
[
  {{
    "claim": "观点表述（100-300字，蒸馏后的核心论断）",
    "dim": "维度分类（如: stance, cognitive, method, decision, communication, logical_tracing, tension, situational）",
    "evidence": [
      {{
        "excerpt": "原文中支撑此观点的关键段落（100-500字，必须是原文原文原文，不是转述）",
        "source_file": "{material_file.name}",
        "location": "在文件中的位置描述（如：第3段、第12-15行）"
      }}
    ]
  }}
]
```

## ⚠️ 核心提取原则

**不要只提取显式观点！** 以下四类信息都必须提取：

1. **显式观点/论断**：人物明确提出的主张、理论、信念
2. **行为中的决策模式**：从具体行为、选择、决策中推断出的模式（如"在X情境下选择Y而非Z"→揭示风险偏好）
3. **叙事中的转折和背景**：时间线事件、环境变化、关键时刻→提炼为turning_points、grounding、situational相关的insight
4. **人物互动**：与他人合作、对抗、师承、决裂→提炼为logical_tracing、tension相关的insight

**学术论文**：提取论文中关于该人物思想/理论的分析和论点。如果论文与该人物无关（同名不同人），输出空数组 `[]`。

**千万不要因为"没有明确观点"就输出空数组！** 叙事、纪实、传记、百科、新闻报道中都有可提取的决策模式和背景信息。

### insights_with_evidence 字段说明

这是本模板最重要的输出字段，直接决定后续RAG检索质量。必须从原文中精确提取，excerpt一字不改。

- **claim**: 观点表述（100-300字）。不是简单的摘录，而是蒸馏后的核心论断。可以是：
  - 显式观点/论断
  - 行为推断的决策模式（如"在面临X时倾向于Y"）
  - 思想/立场转折描述
  - 人际互动模式（合作/对抗/师承）
- **dim**: 维度分类。复用现有维度：stance, cognitive, method, decision, communication, logical_tracing, tension, situational。
- **evidence**: 支撑该观点的原文证据。
  - **excerpt**: 原文原文原文（100-500字），一字不改，直接摘录。
  - **source_file**: 素材文件名（必须是 {material_file.name}）。
  - **location**: 在文件中的位置描述（如：第3段、第12-15行）。

### 多段原文处理

如果同一篇素材中有多段原文表述同一个观点，归入同一个 insight 下作为多条 evidence。

## 人物素材

**人物**: {person_name}
**素材文件**: {material_file.name}
**字符数**: {chunk['char_count']:,}

--- 素材开始 ---
{chunk['content']}
--- 素材结束 ---

## 输出要求

1. 只输出 insights_with_evidence 的 JSON 数组，不要输出其他字段
2. excerpt 必须是原文原文原文，一字不改
3. **只有当素材与该人物完全无关时才输出空数组**。叙事、纪实、传记、百科、新闻中都有可提取的决策模式和背景信息
4. 输出必须是合法 JSON 数组
5. 每个insight至少1条evidence，重要观点2-5条
6. 不要因为"没有明确观点"就偷懒输出空数组
7. **excerpt 长度必须在50字以上**。禁止以下行为：
   - ❌ 用文章标题作为excerpt（如「林彪何以执掌东北帅印」只有9个字）
   - ❌ 用章节标题作为excerpt（如「References」「总序」「楔子」）
   - ❌ 用文件名或标题行作为excerpt
   - ✅ excerpt必须是正文段落的逐字摘录（50-500字）
   - ✅ 如果该素材确实支撑某个观点，从正文中找到具体的段落摘录
"""

            # 保存prompt
            # 如果是chunk，文件名格式: _prompt_{index}_{filename}_chunk{N}.md
            chunk_suffix = chunk["chunk_suffix"]
            # 去掉素材文件扩展名，避免 .md.md 双扩展名
            stem = material_file.stem  # 不含扩展名
            prompt_filename = f"_prompt_{prompt_index:03d}_{stem}{chunk_suffix}.md"
            prompt_path = prompts_dir / prompt_filename
            prompt_path.write_text(prompt, encoding='utf-8')

            if chunk["total_chunks"] > 1:
                print(f"    - chunk {chunk['index']}/{chunk['total_chunks']}: {prompt_filename}")

    print(f"\n已生成 {prompt_index} 个prompts到: {prompts_dir}")
    print(f"\n下一步：")
    print(f"  1. 在 AI 助手中逐个执行这些 prompts")
    print(f"  2. 将每个结果保存到 {person_dir}/_per_file_results/{{filename}}.json")
    print(f"     （如果文件被切分，结果文件名会带有 _chunkN 后缀）")
    print(f"  3. 执行: python3 _batch_distill.py {person_dir} --merge-per-file")


def merge_per_file_results(person_dir: Path) -> None:
    """合并逐篇蒸馏结果

    读取 _per_file_results/ 下所有 JSON 文件，合并 insights_with_evidence
    """
    results_dir = person_dir / "_per_file_results"
    if not results_dir.exists():
        print(f"错误: 结果目录不存在: {results_dir}", file=sys.stderr)
        print(f"请先生成逐篇蒸馏prompts并执行蒸馏", file=sys.stderr)
        sys.exit(1)

    # 读取所有结果文件
    result_files = sorted(results_dir.glob("*.json"))
    if not result_files:
        print(f"错误: {results_dir} 中没有 JSON 文件", file=sys.stderr)
        sys.exit(1)

    print(f"读取逐篇蒸馏结果: {len(result_files)} 个文件")

    # 读取所有insights
    all_insights = []
    for result_file in result_files:
        try:
            insights = json.loads(result_file.read_text(encoding='utf-8'))
            if isinstance(insights, list):
                all_insights.extend(insights)
                print(f"  ✓ {result_file.name}: {len(insights)} insights")
            else:
                print(f"  ⚠ {result_file.name}: 不是数组格式，跳过")
        except Exception as e:
            print(f"  ✗ {result_file.name}: 读取失败 - {e}")

    if not all_insights:
        print("错误: 没有有效的 insights 数据", file=sys.stderr)
        sys.exit(1)

    # === excerpt质量过滤 ===
    # 规则：
    # 1. 空excerpt → 删除
    # 2. location包含"标题"且excerpt<50字 → 大概率是文章标题不是正文 → 删除
    # 3. excerpt是纯英文"References"等无意义内容 → 删除
    before_ev = sum(len(ins.get('evidence', [])) for ins in all_insights)
    JUNK_EXCERPTS = {'references', 'reference', 'bibliography', 'acknowledgments'}
    for ins in all_insights:
        if 'evidence' in ins and isinstance(ins['evidence'], list):
            filtered = []
            for ev in ins['evidence']:
                if not isinstance(ev, dict):
                    continue
                exc = ev.get('excerpt', '').strip()
                loc = ev.get('location', '').strip()
                # 空excerpt
                if not exc:
                    continue
                # location标记为"标题"且内容太短 → 是文章标题
                if '标题' in loc and len(exc) < 50:
                    continue
                # 纯参考文献/章节标记
                if exc.lower() in JUNK_EXCERPTS:
                    continue
                filtered.append(ev)
            ins['evidence'] = filtered
    after_ev = sum(len(ins.get('evidence', [])) for ins in all_insights)
    removed_ev = before_ev - after_ev
    # 过滤掉无evidence的空insight
    before_ins = len(all_insights)
    all_insights = [ins for ins in all_insights if ins.get('evidence')]
    after_ins = len(all_insights)
    if removed_ev > 0 or before_ins != after_ins:
        print(f"  excerpt质量过滤: 移除 {removed_ev} 条标题/空excerpt, 移除 {before_ins - after_ins} 条无evidence的insight")

    print(f"\n总计: {len(all_insights)} insights")

    # Save per-file distillate results to distillates/ directories
    # Also update _db.sqlite
    db_path = person_dir / "_db.sqlite"
    db_conn = None
    try:
        if db_path.exists():
            db_conn = sqlite3.connect(str(db_path))
            db_conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"  ⚠ DB连接失败: {e}", file=sys.stderr)

    now_iso = datetime.now().isoformat()

    for result_file in result_files:
        try:
            insights = json.loads(result_file.read_text(encoding='utf-8'))
            if isinstance(insights, list) and insights:
                # Determine category by checking actual file location in raw dirs
                category = "materials"
                source_file = insights[0].get("evidence", [{}])[0].get("source_file", "") if insights else ""
                # Check if the source file exists in papers/raw/
                if (person_dir / "papers" / "raw" / source_file).exists():
                    category = "papers"
                elif not (person_dir / "materials" / "raw" / source_file).exists() and source_file.startswith("paper_"):
                    category = "papers"
                # NOTE: distillates/ removed 2026-05-14, results live only in _per_file_results/
                # dist_dir = person_dir / category / "distillates"
                # dist_dir.mkdir(parents=True, exist_ok=True)
                # dist_path = dist_dir / f"{result_file.stem}.json"
                # dist_path.write_text(json.dumps(insights, ensure_ascii=False, indent=2), encoding='utf-8')

                # Update DB
                if db_conn:
                    try:
                        filename = source_file or result_file.stem
                        rel_distillate = str(dist_path.relative_to(person_dir))
                        db_conn.execute(
                            "UPDATE items SET distilled=1, distillate_path=?, distill_time=?, status='distilled', insight_count=? WHERE filename=?",
                            (rel_distillate, now_iso, len(insights), filename)
                        )
                        # Write distillate_insights
                        item_row = db_conn.execute("SELECT id FROM items WHERE filename=? ORDER BY id LIMIT 1", (filename,)).fetchone()
                        if item_row:
                            item_id = item_row[0]
                            for idx, ins in enumerate(insights):
                                claim_preview = ins.get("claim", "")[:100]
                                dim = ins.get("dim", "")
                                ev_count = len(ins.get("evidence", []))
                                db_conn.execute(
                                    "INSERT INTO distillate_insights (item_id, insight_idx, claim_preview, dim, evidence_count) VALUES (?, ?, ?, ?, ?)",
                                    (item_id, idx, claim_preview, dim, ev_count)
                                )
                        db_conn.execute(
                            "INSERT INTO processing_log (person, action, details) VALUES (?, 'merge_per_file', ?)",
                            (person_dir.name, f"merged {len(insights)} insights from {result_file.name}")
                        )
                    except Exception as e:
                        print(f"  ⚠ DB更新失败 {source_file}: {e}", file=sys.stderr)
        except Exception:
            pass

    if db_conn:
        try:
            db_conn.commit()
        finally:
            db_conn.close()

    # 读取或创建 personality_v3.json
    pv3_path = person_dir / "personality_v3.json"

    if pv3_path.exists():
        # 读取现有的 personality_v3.json
        existing_data = json.loads(pv3_path.read_text(encoding='utf-8'))
        existing_insights = existing_data.get("insights_with_evidence", [])
        print(f"现有 insights: {len(existing_insights)}")

        # 合并 insights（相同claim的合并evidence）
        merged_insights = merge_insights(existing_insights + all_insights)
        print(f"合并后 insights: {len(merged_insights)}")

        # 更新数据
        existing_data["insights_with_evidence"] = merged_insights
        existing_data["_distill_meta"]["timestamp"] = datetime.now().isoformat()
        if "source_count" in existing_data["_distill_meta"]:
            materials = get_materials(person_dir)
            existing_data["_distill_meta"]["source_count"] = len(materials)

        final_data = existing_data
    else:
        # 创建新的 personality_v3.json
        materials = get_materials(person_dir)
        final_data = {
            "brief": "",
            "school": "",
            "domains": [],
            "stance_anchors": {
                "core_belief": "",
                "methodology": "",
                "key_debates": [],
                "value_hierarchy": []
            },
            "cognitive_patterns": {
                "thinking_style": "",
                "reasoning_approach": "",
                "typical_biases": [],
                "intellectual_strengths": [],
                "intellectual_blindspots": []
            },
            "analytical_frameworks": [],
            "logical_tracing": {
                "summary": "",
                "lineage": {
                    "inherited": [],
                    "transformed": [],
                    "ruptured": [],
                    "converged": []
                },
                "turning_points": [],
                "grounding": {
                    "embodied": [],
                    "contextual": []
                },
                "collaborations": [],
                "renunciations": [],
                "adversaries": [],
                "tension_explanation": {
                    "note": "",
                    "tracing": ""
                }
            },
            "decision_patterns": {
                "risk_tolerance": "",
                "information_processing": "",
                "temporal_orientation": "",
                "conflict_resolution": ""
            },
            "situational_triggers": {
                "description": "",
                "triggers": [],
                "collapse_point": "",
                "default_behavior": "",
                "public_commitment_lock": ""
            },
            "communication_profile": {
                "style": "",
                "persuasion_tactics": [],
                "rhetorical_devices": [],
                "emotional_range": ""
            },
            "evolution_trajectory": "",
            "key_aphorisms": [],
            "insights_with_evidence": merge_insights(all_insights),
            "_distill_meta": {
                "source_count": len(materials),
                "model": "glm-5-turbo",
                "timestamp": datetime.now().isoformat(),
                "version": "v3-logical-tracing"
            }
        }

    # 保存 personality_v3.json
    with open(pv3_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print(f"\n已保存: {pv3_path}")

    # 生成 _distilled_files.json
    generate_distilled_record(person_dir)

    print(f"\n合并完成！")
    print(f"\n清理建议（可选）：")
    print(f"  rm -rf {person_dir}/_per_file_prompts")
    print(f"  rm -rf {person_dir}/_per_file_results")


def _claim_similarity(a: str, b: str) -> float:
    """计算两个claim的相似度（基于关键词重叠）
    
    使用改进的重叠系数：交集词数/min(len_a, len_b)
    这样对短文本更敏感，不会因为长度差异过大而稀释相似度
    """
    import re
    # 中英文分词
    def tokenize(text):
        tokens = set()
        # 英文单词
        for w in re.findall(r'[a-zA-Z]{2,}', text.lower()):
            tokens.add(w)
        # 中文字符级（2-4字窗口）
        cn = re.findall(r'[\u4e00-\u9fff]+', text)
        for seg in cn:
            for n in range(2, min(5, len(seg)+1)):
                for i in range(len(seg)-n+1):
                    tokens.add(seg[i:i+n])
        return tokens
    
    tokens_a = tokenize(a)
    tokens_b = tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    # Overlap coefficient: 交集/min(len_a, len_b)
    min_len = min(len(tokens_a), len(tokens_b))
    return len(intersection) / min_len


def merge_insights(insights: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并 insights，相似 claim 的合并 evidence

    Args:
        insights: insights 列表

    Returns:
        合并后的 insights 列表
    """
    SIMILARITY_THRESHOLD = 0.6  # Jaccard相似度阈值
    
    # 两阶段合并：先精确匹配，再模糊匹配
    groups: List[Dict[str, Any]] = []
    used_indices = set()

    for i, insight in enumerate(insights):
        if i in used_indices:
            continue
        claim_i = insight.get("claim", "").strip()
        if not claim_i:
            used_indices.add(i)
            continue

        dim_i = insight.get("dim", "")
        evidence_list = list(insight.get("evidence", []))

        # 寻找后续相似claims
        for j in range(i + 1, len(insights)):
            if j in used_indices:
                continue
            claim_j = insights[j].get("claim", "").strip()
            if not claim_j:
                used_indices.add(j)
                continue

            # 同维度优先合并，不同维度但高度相似也合并
            sim = _claim_similarity(claim_i, claim_j)
            same_dim = insights[j].get("dim", "") == dim_i

            if (same_dim and sim >= SIMILARITY_THRESHOLD) or sim >= 0.8:
                # 合并evidence
                for ev in insights[j].get("evidence", []):
                    key = (ev.get("excerpt", ""), ev.get("source_file", ""))
                    if not any(
                        (e.get("excerpt", ""), e.get("source_file", "")) == key
                        for e in evidence_list
                    ):
                        evidence_list.append(ev)
                used_indices.add(j)

        # 保留较长/更好的claim
        groups.append({
            "claim": claim_i,
            "dim": dim_i,
            "evidence": evidence_list
        })
        used_indices.add(i)

    return groups


def estimate_total_chars(files: List[Path]) -> int:
    return sum(f.stat().st_size for f in files)


def build_distill_task(person_name: str, person_dir: Path, person_domain: str = "") -> str:
    """构建蒸馏任务prompt（给sessions_spawn用）"""

    # 读取模板

    # 读取素材
    files = get_materials(person_dir)
    if not files:
        return f"ERROR: {person_name} 无素材文件"

    total = estimate_total_chars(files)

    # 构建素材引用
    if total <= MAX_CHARS:
        # 小素材：直接合并
        parts = []
        for f in files:
            content = f.read_text(encoding='utf-8', errors='ignore')
            # Use relative path from person_dir for display
            try:
                rel = f.relative_to(person_dir)
            except ValueError:
                rel = f.name
            parts.append(f"[{rel}]\n{content}")
        materials_text = "\n\n---\n\n".join(parts)

        task = f"""你是人格蒸馏专家。完成以下任务：

1. {template_ref}
2. 以下是{person_name}的全部素材（{total}字符，{len(files)}篇）：
--- 素材开始 ---
{materials_text}
--- 素材结束 ---

3. 按照模板格式，生成 personality_v3.json
4. 用 write 工具保存到：{person_dir}/personality_v3.json

关键要求：
- 学派/领域根据素材内容判断，预填: {person_domain}
- source_ref 必须指向素材文件名
- 信息不足用空数组 []，绝不编造
- 输出必须是合法JSON
- _distill_meta.model 填 "glm-5-turbo"，_distill_meta.version 填 "v3-logical-tracing" """
    else:
        # 大素材：分段处理
        chunks = []
        current = ""
        for f in files:
            content = f.read_text(encoding='utf-8', errors='ignore')
            if len(current) + len(content) > MAX_CHARS - 2000:
                chunks.append(current)
                current = f"[{f.name}]\n{content}\n\n"
            else:
                current += f"[{f.name}]\n{content}\n\n"
        if current:
            chunks.append(current)

        task = f"""你是人格蒸馏专家。{person_name}的素材较大（{total}字符，{len(files)}篇，分{len(chunks)}段），需要分段处理。

1. {template_ref}
2. 逐段读取素材文件并理解：

素材文件列表：
{chr(10).join(f'  - {f.relative_to(person_dir) if f.is_relative_to(person_dir) else f}' for f in files)}

分段策略：素材总量超过{MAX_CHARS}字符，你必须：
  a) 先读取前1/3素材，提取关键信息
  b) 再读取中间1/3素材，补充信息
  c) 最后读取后1/3素材，完善细节
  d) 综合所有信息，生成完整的 personality_v3.json

每次读取用 read 工具读取具体的素材文件路径，不要一次性全部读入。

3. 综合所有素材，按照模板格式生成 personality_v3.json
4. 用 write 工具保存到：{person_dir}/personality_v3.json

关键要求：
- 学派/领域根据素材内容判断，预填: {person_domain}
- source_ref 必须指向素材文件名
- 信息不足用空数组 []，绝不编造
- 输出必须是合法JSON
- _distill_meta.model 填 "glm-5-turbo"，_distill_meta.version 填 "v3-logical-tracing" """

    return task


def find_missing(base_dir: Path = BASE_DIR) -> List[Tuple[str, Path, str]]:
    """找出缺 personality_v3.json 的人物"""
    missing = []
    for group_dir in sorted(base_dir.iterdir()):
        if not group_dir.is_dir():
            continue
        # Check if this group has sub-directories or persons directly
        subdirs = sorted(d for d in group_dir.iterdir() if d.is_dir())
        for candidate in subdirs:
            v3 = candidate / "personality_v3.json"
            if v3.exists():
                continue
            # Skip non-person dirs (like 'materials' at group level)
            if not (candidate / "materials").exists() and not any((candidate / f).is_file() for f in ["personality_v3.json"]):
                # Might be a sub-category, check deeper
                for person_dir in sorted(d for d in candidate.iterdir() if d.is_dir()):
                    if (person_dir / "personality_v3.json").exists():
                        continue
                    name = person_dir.name
                    domain = f"{group_dir.name}/{candidate.name}"
                    missing.append((name, person_dir, domain))
            else:
                name = candidate.name
                domain = group_dir.name
                missing.append((name, candidate, domain))
    return missing


def main():
    import argparse
    parser = argparse.ArgumentParser(description="批量V3蒸馏")
    parser.add_argument("person_dir", nargs="?", help="人物目录")
    parser.add_argument("--list", action="store_true", help="列出缺v3的人物")
    parser.add_argument("--all", action="store_true", help="蒸馏所有缺v3的人物")
    parser.add_argument("--print-task", action="store_true", help="打印任务prompt（调试用）")
    parser.add_argument("--gen-record", metavar="DIR", help="为指定人物目录生成_distilled_files.json")
    parser.add_argument("--per-file", action="store_true", help="生成逐篇蒸馏prompts")
    parser.add_argument("--merge-per-file", action="store_true", help="合并逐篇蒸馏结果")
    args = parser.parse_args()

    if args.gen_record:
        pdir = Path(args.gen_record)
        if not pdir.is_dir():
            print(f"错误: 目录不存在: {pdir}", file=sys.stderr)
            sys.exit(1)
        generate_distilled_record(pdir)
        return

    if args.per_file:
        if not args.person_dir:
            print("错误: --per-file 需要指定人物目录", file=sys.stderr)
            sys.exit(1)
        pdir = Path(args.person_dir)
        if not pdir.is_dir():
            print(f"错误: 目录不存在: {pdir}", file=sys.stderr)
            sys.exit(1)
        generate_per_file_prompts(pdir)
        return

    if args.merge_per_file:
        if not args.person_dir:
            print("错误: --merge-per-file 需要指定人物目录", file=sys.stderr)
            sys.exit(1)
        pdir = Path(args.person_dir)
        if not pdir.is_dir():
            print(f"错误: 目录不存在: {pdir}", file=sys.stderr)
            sys.exit(1)
        merge_per_file_results(pdir)
        return

    if args.list:
        missing = find_missing()
        if not missing:
            print("所有人物已有 personality_v3.json ✅")
        else:
            print(f"缺 v3 的人物: {len(missing)}")
            for name, pdir, domain in missing:
                files = get_materials(pdir)
                total = estimate_total_chars(files)
                print(f"  {name} ({domain}): {len(files)}篇素材, {total:,}字符")
        return

    if args.all:
        missing = find_missing()
        if not missing:
            print("所有人物已有 personality_v3.json ✅")
            return
        print(f"需要蒸馏: {len(missing)} 人")
        print("请对每个人物执行 sessions_spawn，任务prompt如下：")
        print("=" * 60)
        for name, pdir, domain in missing:
            task = build_distill_task(name, pdir, domain)
            print(f"\n### {name} ({domain})")
            print(f"任务长度: {len(task):,} 字符")
            if args.print_task:
                print(task[:500] + "...")
            else:
                print(f"(加 --print-task 查看完整prompt)")
        return

    if args.person_dir:
        pdir = Path(args.person_dir)
        name = pdir.name
        task = build_distill_task(name, pdir)
        print(task)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
