#!/usr/bin/env python3
"""
🏛️ 智库辩论引擎 v1 — 议会制学术辩论
让学者们真正吵起来：预设阵营 → 多层结构 → 交叉质询 → 心理监控

Usage:
    python3 debate_engine.py "议题命题"           # 自动跑完整辩论
    python3 debate_engine.py "命题" --dry-run     # 只看阵营配置
    python3 debate_engine.py --list-scholars      # 查看所有可用学者
"""

import json, sys, os, re
from pathlib import Path
from datetime import date

# ===== 配置 =====
WORKSPACE = os.path.expanduser("~/.openclaw/workspace")
PERSONALITIES_DIR = "/mnt/d/data/think-tank/personalities"
REPORT_DIR = "/mnt/d/data/a-stock/reports"
EXA_API_KEY = "7be7a599-fa7b-4107-9ef1-ad96646156c2"

# ===== 人物索引 =====
ALL_PERSONS = {}

def build_index():
    for cat in ["scholars", "psychologists", "military"]:
        base = f"{PERSONALITIES_DIR}/{cat}"
        if not os.path.isdir(base):
            continue
        for sub in os.listdir(base):
            sub_path = f"{base}/{sub}"
            if not os.path.isdir(sub_path):
                continue
            for name in os.listdir(sub_path):
                pfile = f"{sub_path}/{name}/personality_v3.json"
                if os.path.exists(pfile):
                    ALL_PERSONS[name] = {
                        "name": name, "category": cat,
                        "subdomain": sub, "file": pfile
                    }

def load_personality(name):
    info = ALL_PERSONS.get(name)
    if not info:
        for cat in ["scholars", "psychologists", "military"]:
            base = f"{PERSONALITIES_DIR}/{cat}"
            if not os.path.isdir(base):
                continue
            for sub in os.listdir(base):
                pfile = f"{base}/{sub}/{name}/personality_v3.json"
                if os.path.exists(pfile):
                    info = {"name": name, "category": cat, "subdomain": sub, "file": pfile}
                    ALL_PERSONS[name] = info
                    break
        if not info:
            return None, None
    try:
        with open(info["file"]) as f:
            return info, json.load(f)
    except:
        return None, None

def extract_data(data):
    """统一从v3人物文件中提取信息"""
    domain = data.get("domain", data.get("school", data.get("domains", "")))
    if isinstance(domain, list):
        domain = ", ".join(domain[:3])
    brief = data.get("core_brief", data.get("brief", ""))
    if isinstance(brief, list):
        brief = ", ".join(str(b) for b in brief[:2])
    stance = data.get("stance", data.get("stance_anchors", ""))
    if isinstance(stance, list):
        stance = " ".join(str(s) for s in stance[:3])[:200]
    elif isinstance(stance, dict):
        stance = str(stance)[:200]
    
    raw = data.get("insights_with_evidence", data.get("insights", []))
    if not raw:
        raw = data.get("cognitive_patterns", [])
    top_n = []
    for ins in raw[:12]:
        if isinstance(ins, str):
            top_n.append(ins[:200])
        elif isinstance(ins, dict):
            txt = ins.get("excerpt", ins.get("text", ins.get("claim", "")))
            if isinstance(txt, str) and txt.strip():
                top_n.append(txt.strip()[:200])
    return domain, brief, stance, top_n


# ===== 预设辩论模板 =====
DEBATE_TEMPLATES = [
    {
        "name": "金融危机",
        "keywords": ["金融海啸", "危机", "衰退", "崩盘", "萧条", "海啸", "债务", "恐慌"],
        "motion": "当前全球处于金融危机前夜，中国将遭受次生震荡而非原生海啸",
        "pro": ["Ray_Dalio", "Hyman_Minsky", "Carmen_Reinhart"],
        "con": ["Peter_Zeihan", "Nassim_Taleb", "Joseph_Stiglitz"],
        "observers": ["Thomas_Schelling", "Nouriel_Roubini"],
        "psychs": ["丹尼尔·卡尼曼", "古斯塔夫·勒庞", "理查德·塞勒", "乔纳森·海特", "菲利普·泰特洛克"]
    },
    {
        "name": "中美脱钩",
        "keywords": ["脱钩", "关税", "贸易战", "制裁", "封锁", "中美"],
        "motion": "中美经济捆绑已不可逆转地断裂，中国无法找到替代方案",
        "pro": ["Peter_Zeihan", "Nouriel_Roubini", "John_Mearsheimer"],
        "con": ["Joseph_Stiglitz", "Ian_Bremmer", "Thomas_Schelling"],
        "observers": ["Ray_Dalio", "Carmen_Reinhart"],
        "psychs": ["丹尼尔·卡尼曼", "古斯塔夫·勒庞", "所罗门·阿希", "斯坦利·米尔格拉姆", "阿尔伯特·班杜拉"]
    },
    {
        "name": "房地产",
        "keywords": ["房地产", "房价", "楼市", "地产", "一线城市", "广州", "深圳"],
        "motion": "一线城市房地产回暖是结构性反转而非阶段性反弹",
        "pro": ["Ray_Dalio", "Peter_Zeihan"],
        "con": ["Carmen_Reinhart", "Hyman_Minsky", "Thomas_Schelling"],
        "observers": ["Joseph_Stiglitz", "Nouriel_Roubini"],
        "psychs": ["丹尼尔·卡尼曼", "古斯塔夫·勒庞", "罗伯特·西奥迪尼", "丹·阿瑞利", "阿莫斯·特沃斯基"]
    },
    {
        "name": "产业升级",
        "keywords": ["AI", "人工智能", "科技", "芯片", "半导体", "产业升级", "三产", "服务业"],
        "motion": "深圳高成本驱动的第三产业繁荣是结构性升级红利，而非被动膨胀",
        "pro": ["Peter_Zeihan"],
        "con": ["Joseph_Stiglitz", "Ray_Dalio", "Hyman_Minsky", "Carmen_Reinhart"],
        "observers": ["Thomas_Schelling", "Nouriel_Roubini"],
        "psychs": ["丹尼尔·卡尼曼", "古斯塔夫·勒庞", "理查德·塞勒", "米哈里·契克森米哈赖", "马丁·塞利格曼"]
    },
    {
        "name": "地缘冲突",
        "keywords": ["战争", "冲突", "地缘", "台湾", "南海", "中东", "乌克兰"],
        "motion": "中美在台海的冲突风险将在未来三年内达到冷战以来最高水平",
        "pro": ["John_Mearsheimer", "Peter_Zeihan", "Graham_Allison"],
        "con": ["Thomas_Schelling", "Ian_Bremmer", "阎学通"],
        "observers": ["Nouriel_Roubini", "Carmen_Reinhart", "金灿荣"],
        "psychs": ["古斯塔夫·勒庞", "乔纳森·海特", "斯坦利·米尔格拉姆", "菲利普·津巴多", "克里斯·哈德纳吉"]
    },
    {
        "name": "人口",
        "keywords": ["人口", "老龄化", "少子化", "劳动力", "生育"],
        "motion": "人口萎缩将从根本上改变中国经济增长模式，而非周期性波动",
        "pro": ["Peter_Zeihan", "Carmen_Reinhart"],
        "con": ["Joseph_Stiglitz", "Ray_Dalio"],
        "observers": ["Thomas_Schelling", "Hyman_Minsky"],
        "psychs": ["丹尼尔·卡尼曼", "古斯塔夫·勒庞", "丹·阿瑞利", "欧文·亚隆", "马丁·塞利格曼"]
    },
]


def match_debate(topic):
    for tpl in DEBATE_TEMPLATES:
        for kw in tpl["keywords"]:
            if kw in topic:
                return tpl
    print("  [fallback] 未匹配到专用模板，使用金融危机模板")
    return DEBATE_TEMPLATES[0]


def gen_psych_monitor(name, data, motion, pro_names, con_names, obs_names):
    """生成心理学家监控prompt"""
    domain, brief, stance, top_ins = extract_data(data)
    monitor_dims = []
    if "勒庞" in name:
        monitor_dims.append("- 群体思维和虚假共识")
        monitor_dims.append("- 情绪传染和群体暗示")
    if "卡尼曼" in name:
        monitor_dims.append("- 确认偏误和可得性启发")
        monitor_dims.append("- 锚定效应和框架效应")
    if "塞勒" in name:
        monitor_dims.append("- 框架效应和心理账户")
        monitor_dims.append("- 沉没成本谬误")
    if "海特" in name:
        monitor_dims.append("- 意识形态动机推理和道德基础极化")
    if "泰特洛克" in name:
        monitor_dims.append("- 过度自信和确认偏差（超级预测者方法）")
    if "阿希" in name:
        monitor_dims.append("- 从众效应和群体压力")
    if "米尔格拉姆" in name or "津巴多" in name:
        monitor_dims.append("- 权威服从和情境压力")
    if "西奥迪尼" in name:
        monitor_dims.append("- 互惠/承诺/社会认同说服策略")
    if "阿瑞利" in name:
        monitor_dims.append("- 认知失调和归因谬误")
    if "特沃斯基" in name:
        monitor_dims.append("- 可得性启发和代表性启发")
    if "契克森" in name:
        monitor_dims.append("- 心流状态与认知效率（辩论质量条件）")
    if "塞利格曼" in name:
        monitor_dims.append("- 习得性无助与乐观偏差")
    if "艾克曼" in name or "哈德纳吉" in name:
        monitor_dims.append("- 微表情和欺骗检测")
    if not monitor_dims:
        monitor_dims.append("- 认知偏差和思维谬误（通用）")
    
    prompt = f"""# 🧠 心理学家监控指令

## 你的身份
**{name}**（{domain}）
{str(brief)[:200] if brief else ""}

## 辩论命题
> **{motion}**

## 你的任务
你**不参与辩论**。你的工作是监控以下学者群体在辩论中的心理现象：

- 正方: {', '.join(pro_names)}
- 反方: {', '.join(con_names)}
- 观察员: {', '.join(obs_names)}

## 监控维度
{chr(10).join(monitor_dims)}

## 你的核心观点
{chr(10).join([f"- {ins}" for ins in top_ins])}

## 输出格式
### 个人层面
[每位学者的认知偏差诊断]

### 群体层面
[虚假共识/群体极化/情感传染]

### 辩论质量评估
[论证深度、逻辑一致性、对抗性]

### 去偏差建议
[如果辩论偏了，怎么拉回来]
"""
    return prompt


def gen_scholar_prompt(name, data, tpl, camp, opponent_list, topic, top_ins):
    """生成学者辩论prompt"""
    domain, brief, stance, _ = extract_data(data)
    
    if camp == "观察员":
        prompt = f"""# 🏛️ 智库辩论 - 中立观察员指令

## 你的身份
**{name}**（{domain}）
{str(brief)[:200] if brief else ""}
{str(stance)[:200] if stance else ""}

## 辩论命题
> **{tpl['motion']}**

## 你的角色
你是中立观察员。

## 你的核心观点
{chr(10).join([f"- {ins}" for ins in top_ins])}

## 辩论规则
1. 先倾听正方和反方的论证
2. 评估双方论证的逻辑强度
3. 给出你的独立判断

## 输出格式
### 正方论证评估
[你同意的部分和理由]

### 反方论证评估
[你同意的部分和理由]

### 你的独立判断
[基于你的框架，给出条件树分析]
"""
    else:
        prompt = f"""# 🏛️ 智库辩论 - 角色指令

## 你的身份
**{name}**（{domain}）
{str(brief)[:200] if brief else ""}
{str(stance)[:200] if stance else ""}

## 辩论命题
> **{tpl['motion']}**

## 你的角色
你是**【{camp}】方**。
{camp}需要为命题"{'辩护' if camp == '正方' else '反驳'}"。你的对手是：{', '.join(opponent_list)}

## 你的核心观点
{chr(10).join([f"- {ins}" for ins in top_ins])}

## 辩论规则
1. **开场陈述**：明确你的核心论点（用条件链：若X则Y则Z）
2. **反驳对方**：针对{', '.join(opponent_list[:3])}的潜在观点准备反驳
3. **质询**：提出1-2个尖锐问题反问对方
4. **总结陈词**：最终判断

## 输出格式
### 开场陈述
[条件树形式的论点]

### 反驳
[对方可能怎么说，你怎么回]

### 质询问题
[你反问对方的问题]

### 总结陈词
[你的最终立场]
"""
    return prompt


def generate_debate_package(tpl, topic):
    """生成完整辩论包"""
    today = date.today().strftime("%Y-%m-%d")
    today_compact = date.today().strftime("%Y%m%d")
    
    all_speakers = tpl["pro"] + tpl["con"] + tpl["observers"]
    
    print(f"\n{'='*60}")
    print(f"🏛️ 智库议会辩论")
    print(f"命题: {tpl['motion']}")
    print(f"日期: {today}")
    print(f"总人数: {len(all_speakers)}学者 + {len(tpl['psychs'])}心理学家")
    print(f"{'='*60}")
    
    # 验证
    print("\n📋 验证参与者...")
    pro_names, con_names, obs_names = [], [], []
    psych_names = []
    
    for name in all_speakers:
        info, data = load_personality(name)
        if data:
            camp = "正方" if name in tpl["pro"] else ("反方" if name in tpl["con"] else "观察员")
            domain, _, _, _ = extract_data(data)
            print(f"  [OK] {name} ({domain}) [{camp}]")
            if camp == "正方":
                pro_names.append(name)
            elif camp == "反方":
                con_names.append(name)
            else:
                obs_names.append(name)
        else:
            print(f"  [!!] {name} 无人格文件，跳过")
    
    for name in tpl["psychs"]:
        info, data = load_personality(name)
        if data:
            psych_names.append(name)
            domain, _, _, _ = extract_data(data)
            print(f"  [PSY] {name} ({domain})")
        else:
            print(f"  [!!] {name} 无人格文件，跳过")
    
    # 生成prompt
    prompt_dir = "/tmp/debate_prompts"
    os.makedirs(prompt_dir, exist_ok=True)
    
    print("\n📝 生成辩论prompt...")
    
    for name in pro_names + con_names + obs_names:
        info, data = load_personality(name)
        _, _, _, top_ins = extract_data(data)
        
        if name in pro_names:
            camp, opp = "正方", con_names
        elif name in con_names:
            camp, opp = "反方", pro_names
        else:
            camp, opp = "观察员", pro_names + con_names
        
        prompt = gen_scholar_prompt(name, data, tpl, camp, opp, topic, top_ins)
        pfile = f"{prompt_dir}/debate_{name}.md"
        with open(pfile, "w") as f:
            f.write(prompt)
        print(f"  -> {pfile}")
    
    for name in psych_names:
        info, data = load_personality(name)
        prompt = gen_psych_monitor(name, data, tpl['motion'], pro_names, con_names, obs_names)
        pfile = f"{prompt_dir}/debate_{name}.md"
        with open(pfile, "w") as f:
            f.write(prompt)
        print(f"  -> {pfile}")
    
    # 调度指令
    print(f"\n{'='*60}")
    print("🚀 调度指令（先spawn学者，再spawn心理学家）")
    print(f"{'='*60}")
    
    for name in pro_names + con_names + obs_names:
        camp = "正方" if name in pro_names else ("反方" if name in con_names else "观察员")
        print(f"""
# {name} [{camp}]
sessions_spawn(
    label="辩论-{camp}-{name}",
    runTimeoutSeconds=300,
    task="Read /tmp/debate_prompts/debate_{name}.md and follow your role."
)""")
    
    print(f"\n【然后心理学家——在学者全部返回后】")
    for name in psych_names:
        print(f"""
# 🧠 {name}
sessions_spawn(
    label="辩论-心理-{name}",
    runTimeoutSeconds=300,
    task="Read /tmp/debate_prompts/debate_{name}.md and analyze scholar debate biases."
)""")
    
    outpath = f"{REPORT_DIR}/{today}/辩论_{tpl['name']}_{today_compact}.md"
    print(f"\n📄 最终报告: {outpath}")
    return outpath


def main():
    import argparse
    parser = argparse.ArgumentParser(description="智库辩论引擎 v1")
    parser.add_argument("topic", nargs="?", default="", help="议题")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-scholars", action="store_true")
    args = parser.parse_args()
    
    print("📚 构建人物索引...")
    build_index()
    print(f"  总计 {len(ALL_PERSONS)} 人")
    
    if args.list_scholars:
        for name, info in sorted(ALL_PERSONS.items()):
            print(f"  {name} [{info['category']}/{info['subdomain']}]")
        return
    
    if not args.topic:
        print("用法: python3 debate_engine.py \"议题命题\"")
        return
    
    tpl = match_debate(args.topic)
    
    if args.dry_run:
        print(f"\n命题: {tpl['motion']}")
        print(f"正方: {', '.join(tpl['pro'])}")
        print(f"反方: {', '.join(tpl['con'])}")
        print(f"观察员: {', '.join(tpl['observers'])}")
        print(f"心理学家: {', '.join(tpl['psychs'])}")
        return
    
    generate_debate_package(tpl, args.topic)


if __name__ == "__main__":
    main()
