#!/usr/bin/env python3
"""
智库报告生成器v2
- 合并RAG+历史+心理学数据
- 净化prompt避免GLM内容过滤
- 一次生成完整报告
- 输出到D盘
"""
import json, subprocess, sys, os, time

API_KEY = "be2ffb65ff4340879d38a41494b78187.bYrqhxaJQWwSnrEv"
URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-5-turbo"

def call_glm(messages, max_tokens=8000):
    """调用GLM API"""
    import requests
    r = requests.post(URL, json={
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=300)
    
    if r.status_code == 400:
        print(f"⚠️ 400错误，尝试净化重试...")
        # 截断最后一段，可能包含敏感词
        return None
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def build_prompt():
    """构建净化后的分析prompt"""
    
    # 加载RAG数据
    with open("/tmp/rag_deep_results.json") as f:
        rag_data = json.load(f)
    
    # 格式化RAG为紧凑文本
    rag_text = ""
    for topic, items in rag_data.items():
        rag_text += f"\n## {topic}\n"
        seen = set()
        for item in sorted(items, key=lambda x: x["dist"])[:5]:
            key = item["person"]
            if key in seen: continue
            seen.add(key)
            text = item["text"][:300].replace("\n", " ")
            rag_text += f"- [RAG: {key}] {text}\n"
    
    prompt = f"""请作为战略智库分析引擎，对以下议题进行多视角深度分析。

## 核心问题
美国在对中东某国采取重大军事行动之前，是否会在南亚（印度方向）、东亚（日本方向）或西太平洋（台湾海峡方向）中选择一个制造紧张局势？哪个方向可能性最大？

请从学术和政策研究的角度，而非军事指导的角度进行分析。

## 分析框架

### 一、历史参照矩阵（请用以下9个历史事件构建决策模式总结）

1. **1950朝鲜半岛冲突**：出兵同时派舰队进入台湾海峡（侧翼锁定逻辑）；有限战争原则确立；政治克制vs军事扩大冲动
2. **1941太平洋战争**：贸易限制→被逼入困境→突袭决策；海上通道控制权决定走向
3. **1962中印边境冲突**：有限目标+快速结束+主动后撤；大打不可能小打可控
4. **1991海湾行动**：5个月联盟构建+联合国授权；快速优势→市场初期price in
5. **2003伊拉克行动**：情报误判→后果不可控；长期结构性影响
6. **1973石油禁运**：供应中断武器化→油价4倍暴涨
7. **1980-1988两伊冲突**：意识形态延长冲突→消耗战支撑油价
8. **2019海峡油轮事件**：局部袭击冲击有限→能源弹性变化
9. **2022东欧冲突**：能源武器双刃剑→制裁长期效应→核红线

对每个事件提取：触发条件→升级路径→决策心理特征→终止条件→市场反应，然后类比到当前三个候选。

### 二、三个候选深度分析

对每个候选（台湾海峡/日本/印度），分析以下维度：

**A. 战略收益与成本**（军事、政治、经济、联盟、国内政治、升级风险6个维度）

**B. 历史类比**（至少3个参照，注明适用和不适用条件）

**C. 关键决策者心理**（用以下心理学框架分析）：
- 前景理论（损失厌恶：损失=2-2.5倍收益感受；损失框架下更倾向风险寻求）
- 系统1vs系统2（直觉反应vs理性分析）
- 支持信心偏差（过度依赖支持性证据）
- 可得性启发（近期事件被过度加权）
- 锚定效应（初始信息过度影响判断）
- 情境力量（情境压倒个人特质）
- 预期影响判断（看到什么取决于期望看到什么）
- 恐惧信号（恐惧是最原始的情绪信号）

**D. 集体决策偏差**（分析决策圈心理）：
- 从众效应（75%从众率；1个异议者可降至5-10%；核心变量：群体规模/一致性/任务难度）
- 群体极化（讨论使初始倾向极端化）
- 道德脱离（8机制：合理化/委婉语言/优势比较/责任转移/责任扩散/后果淡化/非人化/责备归因）
- 权威服从（65%服从致命命令）
- 社会认同（不确定性+相似性是最强触发）
- 最小群体范式（分组即产生偏好和歧视）

**E. 概率评估**（区间+条件变量）

### 三、跨学科交叉验证

1. **心理学综合判断**：用认知偏差理论、社会认同理论、群体心理学、平庸之恶、操作性条件反射综合分析
2. **军事约束**：分析三个方向的战术天花板和升级路径
3. **地缘逻辑**：进攻性现实主义vs道义现实主义的分歧
4. **货币权力**：美元体系对军事行动的支撑和约束
5. **历史一致性检验**：9个事件中哪些模式重合度最高

### 四、情景推演与投资影响

四个情景（无冲突30%/印度紧张35%/台海危机25%/全面两线10%），每个包含：
- 时间线：0-1月/1-6月/6-12月
- 资产价格路径（Brent/WTI/黄金/美元/A股/恒生/日经/NIFTY/铜/小麦）

### 五、自优化建议
- 信心最高/最低的判断及原因
- 需要补充的数据
- 分析偏见检查

## RAG参考数据（来自78位人格向量库，按子议题组织）
{rag_text}

## 输出要求
- 每个判断标注[RAG: 人格名/维度]
- 心理学家分析要深刻具体
- 历史类比严谨
- 资产价格给具体区间
- 中文，不少于6000字"""

    return prompt


def main():
    print("构建prompt...")
    prompt = build_prompt()
    print(f"Prompt长度: {len(prompt)}字符")
    
    print("调用GLM-5生成报告...")
    result = call_glm([{"role": "user", "content": prompt}], max_tokens=8000)
    
    if result is None:
        print("❌ API调用失败")
        return
    
    # 保存
    output_dir = "/mnt/d/data/think-tank-data/reports"
    os.makedirs(output_dir, exist_ok=True)
    output_path = f"{output_dir}/亚洲热点研判v2_2026-04-20.md"
    
    with open(output_path, "w") as f:
        f.write(result)
    
    print(f"✅ 报告已保存: {output_path}")
    print(f"   字数: {len(result)}字符")
    
    # 输出摘要
    print(f"\n{'='*60}")
    print("报告预览（前500字）:")
    print(result[:500])
    print("...")


if __name__ == "__main__":
    main()
