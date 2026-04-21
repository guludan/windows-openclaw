# 人格蒸馏 Prompt v3（含逻辑溯源）

你是人格蒸馏专家。根据提供的人物素材，生成结构化的人格模型 JSON。

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
      "inherited": [
        {"source": "人物/学派/传统", "nature": "思想继承|方法论继承|精神传承", "description": "继承了什么，如何继承", "source_ref": "素材出处"}
      ],
      "transformed": [
        {"source": "人物/学派", "original": "原始版本", "transformation": "如何改造", "source_ref": "素材出处"}
      ],
      "ruptured": [
        {"target": "被断裂/背叛的对象", "reason": "为什么断裂", "source_ref": "素材出处"}
      ],
      "converged": [
        {"streams": ["源头1", "源头2"], "product": "融合产生了什么", "source_ref": "素材出处"}
      ]
    },
    "turning_points": [
      {
        "period": "时间段",
        "title": "转折标题",
        "narrative": "2-4句渐进描述：从什么状态，经历什么过程，到达什么新状态",
        "catalyst": "最终触发转折的关键因素",
        "before_state": "转折前的思想状态",
        "after_state": "转折后的思想状态",
        "significance": "这个转折对整体思想的意义",
        "source_ref": "素材出处"
      }
    ],
    "grounding": {
      "embodied": [
        {"context": "具体场景/环境", "role": "此人在场景中的角色", "duration": "时间段", "impact": "如何塑造了思想", "source_ref": "素材出处"}
      ],
      "contextual": [
        {"context": "宏观背景（历史时代、文化环境）", "role": "观察者/参与者/反抗者", "impact": "如何塑造了思想", "source_ref": "素材出处"}
      ]
    },
    "collaborations": [
      {"with": "合作者", "nature": "互补型|对抗型|师徒型", "product": "合作产出", "significance": "对思想的影响", "source_ref": "素材出处"}
    ],
    "renunciations": [
      {"what": "拒绝/放弃的对象或立场", "reason": "为什么拒绝", "aftermath": "拒绝后走向什么", "source_ref": "素材出处"}
    ],
    "adversaries": [
      {"who": "论敌", "axis": "对立维度", "dynamic": "如何交锋，如何塑造思想", "source_ref": "素材出处"}
    ],
    "tension_explanation": {
      "note": "张力分数的溯源解释",
      "tracing": "高分来自X经历，低分来自Y选择..."
    }
  },
  "decision_patterns": {
    "risk_tolerance": "风险偏好",
    "information_processing": "信息处理方式",
    "temporal_orientation": "时间取向",
    "conflict_resolution": "冲突解决方式"
  },
  "situational_triggers": {
    "description": "情境触发条件矩阵：记录该人物在不同情境下判断和行为的变化。人格不是跨情境稳定的，情境力量可以压倒人格特质（津巴多），同一个人在损失框架和收益框架下的判断截然不同（卡尼曼）。",
    "triggers": [
      {
        "condition": "触发条件描述（如：面临领土/主权损失框架时）",
        "behavioral_shift": "行为/判断如何变化（如：风险偏好急剧上升，接受极端手段的概率增加）",
        "confidence": "高|中|低",
        "source_ref": "素材出处"
      }
    ],
    "collapse_point": "情境崩溃点：在什么极端条件下，此人的判断会完全偏离其一贯模式（如：山本五十六在忠诚职责压倒个人判断时会做出违背理性的决策）",
    "default_behavior": "高不确定性/信息不足时的默认行为模式",
    "public_commitment_lock": "公开承诺锁定：该人物已做出的公开立场声明，这些承诺如何约束其后续判断空间"
  },
  "communication_profile": {
    "style": "表达风格",
    "persuasion_tactics": ["说服策略"],
    "rhetorical_devices": ["修辞手法"],
    "emotional_range": "情感范围"
  },
  "evolution_trajectory": "思想演变轨迹总述",
  "key_aphorisms": ["标志性语录"],
  "_distill_meta": {
    "source_count": 0,
    "model": "glm-5-turbo",
    "timestamp": "",
    "version": "v3-logical-tracing"
  }
}
```

## 逻辑溯源提取指南

logical_tracing 是本版本的核心新增字段。它回答的不是"这个人怎么想"，而是"是什么让这个人这么想"。

### 提取三层法

1. **显性信息**（直接提取）：自述中的"我年轻时……"、"受到X的影响……"、"在Y的经历让我……"
2. **半隐性信号**（推理提取）：
   - 频繁引用的人物/文献 → inherited 的来源
   - "反对/拒绝/超越/批判"相关语句 → renunciations 的来源
   - 否定句和反问句 → ruptured 或 adversaries 的线索
3. **隐性痕迹**（深度推理）：
   - 讨论抽象理论时突然嵌入的具体场景描述 → embodied grounding
   - 反复出现的"焦虑点"或"警告" → historical_contextual grounding
   - 行文/论证模式暗示的训练背景 → transformed lineage

### 字段说明

- **lineage.inherited**：直接继承的思想来源。可以是师承、学派、文化传统。
- **lineage.transformed**：改造了别人的思想。记录"原始版本"→"改造后"。
- **lineage.ruptured**：断裂/背叛。这个人明确反对的前导师、前学派、前立场。
- **lineage.converged**：多个源头融合。跨学科碰撞、合作产生的新思想。
- **turning_points**：思想转折的渐进叙事。不是瞬间切换，是过程。包含 before→catalyst→after。
- **grounding.embodied**：肉身在场获得的认知（战场、临床、贫困、监禁等亲历）。
- **grounding.contextual**：长期沉浸观察获得的认知（历史时代、社会运动、文化环境）。
- **collaborations**：关键合作者及合作性质（互补/对抗/师徒）。
- **renunciations**：拒绝/放弃的东西。一个人拒绝了什么，比接受了什么更定义他。
- **adversaries**：论敌及交锋如何塑造了思想。
- **tension_explanation**：用溯源故事解释张力分数。张力是思想史的化石。
- **所有 source_ref**：指向具体素材文件名或片段，确保可追溯。

### situational_triggers 字段说明

这是心理学家评审后新增的核心字段。回答的不是「这个人怎么想」，而是「在什么条件下这个人的想法会变」。

- **triggers[]**：2-5个最重要的情境触发条件。每个包含：
  - condition：什么情境（损失框架、时间压力、群体一致性>80%、公开承诺已做出等）
  - behavioral_shift：行为如何变化
  - confidence：推断的置信度（素材有直接证据=高，间接推断=中，理论推测=低）
- **collapse_point**：一句话描述极端情境下的行为偏离
- **default_behavior**：信息不足时的默认行为
- **public_commitment_lock**：已公开的立场声明及其约束力

提取方法：
1. 从素材中找「如果…」「在…情况下」「面临…时」等条件语句
2. 从人物的公开演讲/著作中找已明确的立场承诺
3. 从理论框架推断（如：卡尼曼学派的人物必然有损失厌恶触发条件）

### 注意事项

- 数组字段如果素材中信息不足，可以空数组 []，但不要凭空编造
- turning_points 通常1-3个最重要的转折就够
- 如果是军事家，lineage 可能没有学术师承，改为战场经验传承（老指挥员带新指挥员）
- 如果是设计师，grounding 可能是某段旅行、某个工坊经历
- tension_explanation 需要结合张力分析的维度来写

## 人物素材（来自{count}篇素材的原始材料）

{raw_materials}
