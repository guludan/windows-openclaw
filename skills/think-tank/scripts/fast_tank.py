#!/usr/bin/env python3
"""
快速智库作战室（Rapid War Room）
v0.1  —  议题 → 自动匹配相关人格 → 并行讨论 → 交叉验证 → 冲突矩阵输出

用法:
  python3 fast_tank.py "议题描述" [--scholars A,B,C] [--output 输出路径]

示例:
  python3 fast_tank.py "A股系统性恐慌下的量化T+0策略评估" \\
    --scholars "Nassim_Taleb,Hyman_Minsky,Cliff_Asness,Jim_Simons" \\
    --output "议题数据.json"

依赖:
  - 人格数据: /mnt/d/data/think-tank/personalities/scholars/<domain>/<name>/personality_v3.json
  - sessions_spawn: 父进程会处理subagent调度
"""

import argparse
import json
import os
import sys

# 智库根目录
THINK_TANK_DATA = "/mnt/d/data/think-tank/personalities"

# 议题 → 专家匹配规则（人工维护的映射表）
TOPIC_EXPERT_MAP = {
    "恐慌": ["Nassim_Taleb", "Hyman_Minsky", "Ray_Dalio"],
    "崩盘": ["Hyman_Minsky", "Nassim_Taleb", "George_Soros"],
    "量化": ["Jim_Simons", "Cliff_Asness", "Edward_Thorp", "Myron_Scholes"],
    "因子": ["Cliff_Asness", "Eugene_Fama", "Kenneth_French"],
    "T+0": ["Jim_Simons", "Cliff_Asness", "David_Shaw"],
    "特征向量": ["Jim_Simons", "Cliff_Asness", "Emanuel_Derman"],
    "板块": ["Ray_Dalio", "Aaron_Brown", "Hyman_Minsky"],
    "宏观": ["Ray_Dalio", "Hyman_Minsky", "Nassim_Taleb"],
    "庄家": ["Cliff_Asness", "Jim_Simons", "Emanuel_Derman"],
    "行为": ["Daniel_Kahneman", "Richard_Thaler", "Robert_Shiller"],
    "尾部风险": ["Nassim_Taleb", "Aaron_Brown", "Emanuel_Derman"],
    "流动性": ["Nassim_Taleb", "Cliff_Asness", "Myron_Scholes"],
    "杠杆": ["Hyman_Minsky", "Ray_Dalio", "Myron_Scholes"],
    "策略": ["Edward_Thorp", "Jim_Simons", "Cliff_Asness"],
    "回测": ["Cliff_Asness", "Emanuel_Derman", "Fischer_Black"],
    "派系": ["Jim_Simons", "Cliff_Asness"],
    "存量": ["Aaron_Brown", "Nassim_Taleb"],
    "止损": ["Edward_Thorp", "Fischer_Black"],
    "底部": ["Nassim_Taleb", "George_Soros", "Hyman_Minsky"],
}

SCHOLAR_DOMAIN = {
    "Nassim_Taleb": "economics",
    "Hyman_Minsky": "economics",
    "Ray_Dalio": "economics",
    "George_Soros": "economics",
    "Aaron_Brown": "financial-engineering",
    "Cliff_Asness": "financial-engineering",
    "David_Shaw": "financial-engineering",
    "Edward_Thorp": "financial-engineering",
    "Emanuel_Derman": "financial-engineering",
    "Fischer_Black": "financial-engineering",
    "Jim_Simons": "financial-engineering",
    "Myron_Scholes": "financial-engineering",
    "Eugene_Fama": "economics",
    "Kenneth_French": "economics",
    "Daniel_Kahneman": "behavioral",
    "Richard_Thaler": "behavioral",
    "Robert_Shiller": "economics",
}


def match_scholars(topic, max_results=6):
    """根据议题关键词自动匹配学者"""
    topic_lower = topic.lower()
    scores = {}

    for keyword, scholars in TOPIC_EXPERT_MAP.items():
        if keyword.lower() in topic_lower:
            for s in scholars:
                scores[s] = scores.get(s, 0) + 1

    if not scores:
        # 默认: 按议题类型匹配
        if any(w in topic_lower for w in ["风险", "恐慌", "尾部"]):
            scores = {"Nassim_Taleb": 1, "Hyman_Minsky": 1, "Aaron_Brown": 1}
        elif any(w in topic_lower for w in ["量化", "T+0", "因子", "策略", "回测"]):
            scores = {"Jim_Simons": 1, "Cliff_Asness": 1, "Edward_Thorp": 1}
        elif any(w in topic_lower for w in ["板块", "宏观", "经济"]):
            scores = {"Ray_Dalio": 1, "Hyman_Minsky": 1, "Nassim_Taleb": 1}
        else:
            # 通用匹配：选4个跨领域学者
            scores = {
                "Nassim_Taleb": 1,
                "Cliff_Asness": 1,
                "Hyman_Minsky": 1,
                "Jim_Simons": 1,
            }

    sorted_scholars = sorted(scores.items(), key=lambda x: -x[1])[:max_results]
    return sorted_scholars


def verify_personality(name, domain):
    """验证人格文件是否存在"""
    fp = os.path.join(THINK_TANK_DATA, "scholars", domain, name, "personality_v3.json")
    if os.path.exists(fp):
        return fp
    # 尝试换个domain找
    for alt_domain in os.listdir(os.path.join(THINK_TANK_DATA, "scholars")):
        afp = os.path.join(THINK_TANK_DATA, "scholars", alt_domain, name, "personality_v3.json")
        if os.path.exists(afp):
            return afp
    return None


def load_personality(fp):
    """加载人格摘要"""
    try:
        with open(fp, "r") as f:
            data = json.load(f)
        return {
            "name": os.path.basename(os.path.dirname(fp)),
            "brief": data.get("brief", ""),
            "school": data.get("school", ""),
            "stance_anchors": data.get("stance_anchors", {}).get("core_belief", ""),
            "cognitive_patterns": data.get("cognitive_patterns", {}).get("thinking_style", ""),
        }
    except:
        return None


def generate_prompt(name, personality, topic, background_data):
    """生成学者讨论prompt"""

    # 根据学者专业方向自动适配角色
    domain_hints = {
        "Nassim_Taleb": "你是 Nassim Taleb。从反脆弱、黑天鹅、厚尾风险角度分析。尖锐直接，不客气。",
        "Hyman_Minsky": "你是 Hyman Minsky。从金融不稳定假说(FIH)框架诊断。严谨经济学范式。",
        "Cliff_Asness": "你是 Cliff Asness。从因子投资、量化价值角度分析。用因子暴露分解方法。尖锐反直觉实证主义。",
        "Jim_Simons": "你是 Jim Simons。从信号处理、模式识别、统计套利角度。用PCA/ICA方法论。简洁有力。",
        "Edward_Thorp": "你是 Edward Thorp。从统计套利、凯利准则、风险管理角度。用数学说话。",
        "Ray_Dalio": "你是 Ray Dalio。从周期模型、宏观框架、风险平价角度。机器视角。",
        "Aaron_Brown": "你是 Aaron Brown。从风险管理、对冲、博弈角度分析。华尔街实战派。",
        "Emanuel_Derman": "你是 Emanuel Derman。从模型风险、物理方法、量化哲学角度。严谨方法论。",
        "Fischer_Black": "你是 Fischer Black。从期权定价、平衡理论、噪声交易者角度。",
        "Myron_Scholes": "你是 Myron Scholes。从动态对冲、衍生品、风险管理角度。",
        "David_Shaw": "你是 David Shaw。从统计套利、计算金融、市场微观结构角度。",
    }

    role = domain_hints.get(name, f"你是 {name}，{personality.get('school', '金融学者')}。以你的专业视角分析。")

    prompt = f"""{role}

## 你的专业背景
- 学派: {personality.get('school', '未指定')}
- 核心观点: {personality.get('stance_anchors', '')}
- 思维风格: {personality.get('cognitive_patterns', '')}

## 议题
{topic}

## 背景数据
{json.dumps(background_data, ensure_ascii=False, indent=2)}

## 任务
1. 基于你的专业框架，独立分析这个议题
2. 对关键问题给出明确判断（是/否/条件，不要模糊表述）
3. 说明你的判断边界——什么条件下你的建议会改变
4. 不要重复其他人的观点，专注你的独特视角

回答要简练、有力、有框架。用你的标志性风格说话。
"""
    return prompt.strip()


def main():
    parser = argparse.ArgumentParser(description="快速智库作战室")
    parser.add_argument("topic", help="议题描述")
    parser.add_argument("--scholars", help="指定学者，逗号分隔（可选，否则自动匹配）")
    parser.add_argument("--data", help="背景数据JSON文件路径（可选）")
    parser.add_argument("--output", help="输出路径（可选）")
    args = parser.parse_args()

    # 1. 匹配学者
    if args.scholars:
        scholar_names = [s.strip() for s in args.scholars.split(",")]
        matched = [(n, 99) for n in scholar_names]
    else:
        matched = match_scholars(args.topic)

    if not matched:
        print("❌ 未匹配到学者，请手动指定 --scholars")
        sys.exit(1)

    # 2. 验证人格文件
    print(f"\n📋 议题: {args.topic}")
    print(f"📊 匹配学者: {[s[0] for s in matched]}")
    print()

    verified = []
    for name, score in matched:
        domain = SCHOLAR_DOMAIN.get(name, "economics")
        fp = verify_personality(name, domain)
        if fp:
            p = load_personality(fp)
            if p:
                verified.append((name, p))
                print(f"  ✅ {name:20} ({domain:22}) 匹配度{score}")
            else:
                print(f"  ⚠️ {name:20} 人格文件解析失败")
        else:
            print(f"  ❌ {name:20} 未找到人格文件")

    if not verified:
        print("❌ 没有可用的人格文件")
        sys.exit(1)

    # 3. 显示生成的prompt示例
    print(f"\n{'='*60}")
    print(f"📝 生成的讨论prompt示例（{verified[0][0]}）")
    print(f"{'='*60}")

    topic_data = {}
    if args.data:
        with open(args.data, "r") as f:
            topic_data = json.load(f)

    sample_prompt = generate_prompt(verified[0][0], verified[0][1], args.topic, topic_data)
    print(sample_prompt[:500])
    print(f"\n... (完整 {len(sample_prompt)} 字符)")

    # 4. 输出调度指令
    print(f"\n{'='*60}")
    print("🚀 调度指令（手动复制到父session）")
    print(f"{'='*60}")
    for name, p in verified:
        prompt = generate_prompt(name, p, args.topic, topic_data)
        print(f"""
sessions_spawn(
    label="智库-{name}",
    model="deepseek/deepseek-chat",
    runTimeoutSeconds=180,
    task=\"\"\"{prompt[:200]}...
\"\"\")""")

    print(f"\n总 {len(verified)} 位学者准备就绪")


if __name__ == "__main__":
    main()
