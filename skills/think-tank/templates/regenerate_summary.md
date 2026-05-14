# 人格模型顶层字段生成

你是人格蒸馏专家。根据以下人物的维度分布摘要，生成人格模型的**顶层字段**。

## 重要说明
- 你**只需要输出顶层字段**，不需要输出insights_with_evidence
- insights的合并由其他批次独立完成
- 你根据维度分布推断人物的全局特征

## 人物维度分布
共{dim_count}个维度，{total_insights}条insights。

{dim_summaries}

## 输出格式
```json
{
  "brief": "一句话定位（10-20字）",
  "school": "所属学派/流派/风格",
  "domains": ["核心领域1", "核心领域2"],
  "stance_anchors": {
    "core_belief": "最核心的信念",
    "methodology": "核心方法论",
    "key_debates": ["核心争论/对立"],
    "value_hierarchy": ["价值优先级排序"]
  },
  "cognitive_patterns": {
    "thinking_style": "思维风格",
    "reasoning_approach": "推理方式",
    "typical_biases": ["典型偏见"],
    "intellectual_strengths": ["核心优势"],
    "intellectual_blindspots": ["核心盲区"]
  },
  "analytical_frameworks": [
    {
      "name": "框架名称",
      "components": ["组件1", "组件2"],
      "application": "应用场景"
    }
  ],
  "logical_tracing": {
    "summary": "一句话概括思想来源的本质",
    "lineage": {
      "inherited": [{"source": "人物/学派", "nature": "思想继承", "description": "继承了什么", "source_ref": "素材出处"}],
      "transformed": [],
      "ruptured": [],
      "converged": []
    },
    "turning_points": [],
    "grounding": {"embodied": [], "contextual": []},
    "collaborations": [],
    "renunciations": [],
    "adversaries": [],
    "tension_explanation": {"note": "", "tracing": ""}
  },
  "decision_patterns": {
    "risk_tolerance": "风险偏好",
    "information_processing": "信息处理方式",
    "temporal_orientation": "时间取向",
    "conflict_resolution": "冲突解决方式"
  },
  "situational_triggers": {
    "description": "情境触发条件矩阵",
    "triggers": [],
    "collapse_point": "",
    "default_behavior": "",
    "public_commitment_lock": ""
  },
  "communication_profile": {
    "style": "表达风格",
    "persuasion_tactics": ["说服策略"],
    "rhetorical_devices": ["修辞手法"],
    "emotional_range": "情感范围"
  },
  "evolution_trajectory": "思想演变轨迹总述",
  "key_aphorisms": ["标志性语录"]
}
```

## 关键要求

1. **信息不足的字段用空数组 [] 或空字符串**，绝不编造
2. 字段名必须严格按上方模板结构，禁止自造字段名
3. 输出必须是合法JSON（不要包含insights_with_evidence字段）

### turning_points 子字段：
```json
{"period": "时间段", "title": "转折标题", "narrative": "2-4句渐进描述", "catalyst": "触发因素", "before_state": "转折前状态", "after_state": "转折后状态", "significance": "意义", "source_ref": "素材出处"}
```

### situational_triggers.triggers 子字段：
```json
{"condition": "触发条件", "behavioral_shift": "行为如何变化", "confidence": "高|中|低", "source_ref": "素材出处"}
```

### grounding.embodied/contextual 子字段：
```json
{"context": "具体场景", "role": "角色", "duration": "时间段", "impact": "如何塑造思想", "source_ref": "素材出处"}
```
