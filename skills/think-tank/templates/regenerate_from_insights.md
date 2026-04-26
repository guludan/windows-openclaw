# 人格模型生成 Prompt（基于已蒸馏的insights）

你是人格蒸馏专家。根据提供的人物**已蒸馏的insights列表**，生成完整的人格模型 JSON。

与从原始素材蒸馏不同，这里的输入已经是结构化的观点和证据。你的任务是将这些insights综合归纳为完整的人格模型。

## 输出结构

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
  "key_aphorisms": ["标志性语录"],
  "insights_with_evidence": [],
  "_distill_meta": {
    "source_count": 0,
    "model": "glm-5-turbo",
    "timestamp": "",
    "version": "v3-regenerated"
  }
}
```

## 关键要求

1. **insights_with_evidence 字段直接使用输入数据，不要修改**——原样保留
2. 其他字段（brief, school, domains, stance_anchors 等）根据insights内容归纳生成
3. 信息不足的字段用空数组 [] 或空字符串，**绝不编造**
4. logical_tracing 从insights中推断思想来源，标注source_ref
5. 输出必须是合法JSON

## 人物已蒸馏的insights（共{count}条）

{insights_json}
