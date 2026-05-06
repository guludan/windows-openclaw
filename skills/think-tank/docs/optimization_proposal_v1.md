# RAG系统架构优化方案 v1.0

## 优化1: 并行检索替代线性流水线

### 问题
当前检索是线性流水线：personas → cognitive → claims → evidence → tracing → tension
第一步personas的结果会锚定后续所有检索方向（卡尼曼的锚定效应）。如果personas匹配错了人，后续全部跑偏。

### 方案设计

#### 核心思路：并行检索 + 事后仲裁

```
用户问题
    ├──→ personas   (top 10) ──┐
    ├──→ cognitive  (top 10) ──┤
    ├──→ claims     (top 10) ──┤──→ 仲裁器(meta-judge) → 最终结果
    ├──→ evidence   (top 10) ──┤
    ├──→ tracing    (top 5)  ──┤
    └──→ tension    (top 5)  ──┘
```

#### 技术实现

```python
def parallel_search(query, top_k=10):
    """并行检索所有集合，返回仲裁后的结果"""
    q_emb = get_embedding(query)
    
    # 阶段1: 并行检索所有集合
    results = {}
    for col_name in COLLECTION_NAMES:
        col = client.get_collection(col_name)
        r = col.query(query_embeddings=[q_emb], n_results=top_k, 
                      include=["documents", "metadatas", "distances"])
        results[col_name] = r
    
    # 阶段2: 人物聚合
    person_scores = aggregate_person_scores(results)
    
    # 阶段3: 按人物打包返回
    final = package_by_person(results, person_scores)
    return final

def aggregate_person_scores(results):
    """从所有集合的结果中统计每个人物的得分"""
    person_scores = defaultdict(lambda: {"count": 0, "min_dist": float('inf'), "collections": set()})
    
    for col_name, r in results.items():
        for meta, dist in zip(r['metadatas'][0], r['distances'][0]):
            person = meta.get('person', '')
            person_scores[person]["count"] += 1
            person_scores[person]["min_dist"] = min(person_scores[person]["min_dist"], dist)
            person_scores[person]["collections"].add(col_name)
    
    # 评分公式：出现频次 + 最小距离权重 + 集合覆盖度
    for person, s in person_scores.items():
        coverage = len(s["collections"]) / len(COLLECTION_NAMES)
        s["score"] = s["count"] * 0.4 + (1 - s["min_dist"]) * 0.3 + coverage * 0.3
    
    return dict(sorted(person_scores.items(), key=lambda x: -x[1]["score"]))
```

#### 仲裁器评分公式
- **频次分 (40%)**: 该人物在多少条检索结果中出现
- **距离分 (30%)**: 最小距离（越近越好）
- **覆盖度分 (30%)**: 覆盖了多少个集合（覆盖6个集合的人比只出现在1个集合的更可信）

#### 预期效果
- 即使personas集合匹配错了人，claims/cognitive等集合的高质量匹配仍然能把正确人物拉回来
- 消除锚定效应：每个集合独立检索，不互相影响

---

## 优化2: Evidence入库最小质量门控

### 问题
evidence质量不稳定：有时只有标题（9个字）、有时是"References"、有时是章节号。这些低质量evidence污染检索结果。

### 方案设计

#### 质量门控规则

```python
EVIDENCE_QUALITY_RULES = {
    "min_length": 50,           # 最少50字符
    "junk_content": {           # 明确的垃圾内容黑名单
        "references", "reference", "bibliography", "acknowledgments",
        "致谢", "参考文献", "目录"
    },
    "max_title_similarity": 0.9, # excerpt与source文件标题相似度>0.9视为标题抄袭
}

def validate_evidence(excerpt, source_file, location=""):
    """验证单条evidence是否达到入库标准
    
    Returns: (is_valid, reason)
    """
    exc = excerpt.strip()
    
    # 规则1: 非空检查
    if not exc:
        return False, "空excerpt"
    
    # 规则2: 最小长度
    if len(exc) < EVIDENCE_QUALITY_RULES["min_length"]:
        return False, f"长度不足({len(exc)}<{EVIDENCE_QUALITY_RULES['min_length']})"
    
    # 规则3: 垃圾内容黑名单
    if exc.lower() in EVIDENCE_QUALITY_RULES["junk_content"]:
        return False, f"垃圾内容: {exc}"
    
    # 规则4: location标记为"标题"的拒绝
    if "标题" in location:
        return False, "location标记为标题"
    
    # 规则5: 纯数字/章节号
    if re.match(r'^[\d\s\.]+$', exc):
        return False, "纯数字/章节号"
    
    # 规则6: excerpt与source文件标题高度相似（Jaccard相似度）
    # 从source_file提取标题
    title = extract_title_from_source(source_file)
    if title:
        sim = jaccard_similarity(exc, title)
        if sim > EVIDENCE_QUALITY_RULES["max_title_similarity"]:
            return False, f"与标题高度相似(sim={sim:.2f})"
    
    return True, ""
```

#### 实施位置
在 `ingest_v3.py` 的 `_batch_add()` 函数中，evidence入库前调用 `validate_evidence()`。

同时在 `_batch_distill.py` 的 `merge_per_file_results()` 中也加过滤（蒸馏结果合并时就过滤）。

#### 数据修复
对已入库的evidence做一次扫描：
```python
# 扫描所有evidence，标记低质量的
evidence_col = client.get_collection("evidence")
all_ev = evidence_col.get(include=["documents", "metadatas"])
bad_ids = []
for i, (doc, meta) in enumerate(zip(all_ev['documents'], all_ev['metadatas'])):
    is_valid, reason = validate_evidence(doc, meta.get('source_file', ''), meta.get('location', ''))
    if not is_valid:
        bad_ids.append(all_ev['ids'][i])

if bad_ids:
    evidence_col.delete(ids=bad_ids)
    print(f"清理了 {len(bad_ids)} 条低质量evidence")
```

---

## 优化3: 自适应阈值替代固定0.55

### 问题
compact_insights() 用固定阈值0.55做Jaccard相似度聚类。但学术内容的语义空间比日常语言稀疏，0.55太高导致几乎不聚类（Fama 309条全是单条簇）。而日常语言0.55可能太低导致过度合并。

### 方案设计

#### 核心思路：基于相似度分布的最大间隙法（Largest Gap Method）

```python
def adaptive_cluster(claims, min_threshold=0.3, max_threshold=0.8):
    """自适应聚类：根据相似度分布动态确定阈值
    
    算法：
    1. 计算所有pairwise相似度
    2. 对相似度排序
    3. 找到最大间隙（largest gap）
    4. 在最大间隙处设阈值
    5. 如果最大间隙不明显（<0.05），回退到默认0.55
    """
    n = len(claims)
    if n <= 1:
        return [[0]]
    
    # 计算所有pairwise Jaccard相似度
    similarities = []
    for i in range(n):
        for j in range(i+1, n):
            sim = jaccard_similarity(claims[i], claims[j])
            similarities.append((sim, i, j))
    
    similarities.sort(reverse=True)
    sims_sorted = [s[0] for s in similarities]
    
    # 找最大间隙
    max_gap = 0
    gap_index = 0
    for i in range(1, len(sims_sorted)):
        gap = sims_sorted[i-1] - sims_sorted[i]
        if gap > max_gap:
            max_gap = gap
            gap_index = i
    
    # 确定阈值
    if max_gap > 0.05:  # 间隙足够明显
        threshold = (sims_sorted[gap_index-1] + sims_sorted[gap_index]) / 2
        threshold = max(min_threshold, min(max_threshold, threshold))
    else:
        threshold = 0.55  # 回退默认
    
    print(f"  自适应阈值: {threshold:.3f} (最大间隙={max_gap:.3f} at index={gap_index})")
    
    # 用确定的阈值做聚类
    return cluster_with_threshold(claims, threshold)
```

#### 备选方案：分dim自适应阈值

不同dim的内容密度不同：
- `method` 维度：技术术语多，语义稀疏 → 需要更低阈值
- `stance` 维度：观点表述相近 → 可以用较高阈值
- `cognitive` 维度：中间

```python
DIM_THRESHOLD_HINTS = {
    "method": 0.35,        # 技术内容更稀疏
    "stance": 0.55,        # 观点表述相对接近
    "cognitive": 0.45,     # 中间
    "decision": 0.45,
    "communication": 0.50,
    "logical_tracing": 0.40,
    "tension": 0.50,
    "situational": 0.45,
}

def dim_adaptive_cluster(claims_by_dim):
    """每个dim独立计算自适应阈值"""
    all_clusters = {}
    for dim, claims in claims_by_dim.items():
        # 先用largest gap法
        clusters, threshold = adaptive_cluster_with_threshold(claims)
        
        # 如果gap不明显，用dim的默认hint
        if threshold is None:
            threshold = DIM_THRESHOLD_HINTS.get(dim, 0.45)
        
        all_clusters[dim] = clusters
    return all_clusters
```

#### 预期效果
- Fama 309条method：0.55→自适应约0.35，从0个聚类变为几十个有意义的聚类
- 日常观点：0.55保持不变（gap在0.55附近很明显）
- 兼顾不同内容类型的特性

---

## 实施优先级

| 优化 | 影响范围 | 实施难度 | 风险 |
|------|---------|---------|------|
| 1. 并行检索 | 新增检索层 | 中（新增search_pipeline.py） | 低（不影响现有数据） |
| 2. evidence门控 | ingest_v3.py + _batch_distill.py | 低（加验证函数） | 低（只过滤垃圾） |
| 3. 自适应阈值 | regenerate_personality.py | 低（改compact_insights） | 中（需验证聚类质量） |

建议实施顺序：2 → 3 → 1
