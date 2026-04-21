#!/usr/bin/env python3
"""
gen_report_segmented.py — 分段生成智库报告
把大task拆成多个子session，每段写一个章节，最后拼接。
用法: python3 gen_report_segmented.py "问题"
"""
import json, sys, os, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.think_tank_pipeline_v3 import run_pipeline, REPORT_DIR

CHAPTERS = [
    {
        "name": "01_问题解构",
        "prompt": "撰写报告第1章：问题解构（800-1000字）。\n- 核心问题的多维度解构\n- 关键变量识别\n- 分析框架说明\n{rag_note}",
    },
    {
        "name": "02_历史参照矩阵",
        "prompt": "撰写报告第2章：历史参照矩阵（800-1000字）。\n- 5个历史事件的类比分析\n- 每个类比要指出异同点\n- 历史教训的提炼\n{rag_note}",
    },
    {
        "name": "03_必要条件分析",
        "prompt": "撰写报告第3章：必要条件深度分析（1500-2000字）。\n- 军事条件：反介入/区域拒止、两栖作战能力\n- 政治条件：国际环境、国内稳定\n- 经济条件：供应链韧性、金融风险\n- 技术条件：半导体、信息战\n- 每个判断标注[RAG: 人格名]\n{rag_note}",
    },
    {
        "name": "04_心理画像与决策偏差",
        "prompt": "撰写报告第4章：心理画像与集体决策偏差（1500-2000字）。\n- 大陆决策层心理画像\n- 台湾社会心理画像\n- 美国华盛顿决策圈画像\n- 群体决策偏差分析（集体思维、从众、损失厌恶、道德脱离）\n- 每个分析标注[RAG: 人格名]，引用💡和📄数据\n{rag_note}",
    },
    {
        "name": "05_交叉验证与窗口期",
        "prompt": "撰写报告第5章：跨学科交叉验证与战略窗口期判断（1500-2000字）。\n- 军事视角vs政治视角vs经济视角的交叉验证\n- 战略窗口期的三个候选时段分析\n- 窗口期开启和关闭的条件判断\n- 概率评估（所有情景概率归一化，总和=100%）\n{rag_note}",
    },
    {
        "name": "06_情景推演与投资影响",
        "prompt": "撰写报告第6章：情景推演与投资影响（1000-1500字）。\n- 3-4个情景的详细推演（基线、升级、缓和、极端）\n- 每个情景的市场影响\n- 相关资产（台股、半导体SOX、美元、人民币、日经、黄金、VIX）的具体影响路径\n- 投资策略建议\n{rag_note}",
    },
]

# 每个章节使用的RAG topic子集（控制大小）
CHAPTER_TOPICS = {
    "01_问题解构": ["台湾_必要条件", "心理_认知偏差"],
    "02_历史参照矩阵": ["台湾_历史类比", "心理_群体决策"],
    "03_必要条件分析": ["台湾_军事窗口", "台湾_必要条件", "台湾_半导体", "台湾_日本"],
    "04_心理画像与决策偏差": ["台湾_心理_大陆", "台湾_心理_台湾", "台湾_心理_美国", "心理_权力道德"],
    "05_交叉验证与窗口期": ["台湾_美国干预", "台湾_军事窗口", "台湾_必要条件", "心理_群体决策"],
    "06_情景推演与投资影响": ["台湾_美国干预", "心理_认知偏差", "心理_权力道德"],
}

def extract_topic_rag(full_task_text: str, topics: list) -> str:
    """从完整task中提取指定topics的RAG数据"""
    lines = full_task_text.split("\n")
    in_rag = False
    result = []
    current_topic = None
    collecting = False

    for line in lines:
        if "## RAG参考数据" in line:
            in_rag = True
            continue
        if in_rag and line.startswith("## "):
            break
        if in_rag and line.startswith("**") and line.endswith(":**"):
            # 新topic
            topic_name = line.strip("*: ")
            current_topic = topic_name
            collecting = topic_name in topics
            if collecting:
                result.append(line)
            continue
        if in_rag and collecting:
            result.append(line)

    return "\n".join(result)


def extract_history(full_task_text: str) -> str:
    """提取历史参照部分"""
    lines = full_task_text.split("\n")
    result = []
    in_hist = False
    for line in lines:
        if "## 历史参照" in line:
            in_hist = True
            result.append(line)
            continue
        if in_hist:
            if line.startswith("## ") and "历史参照" not in line:
                break
            result.append(line)
    return "\n".join(result)


def build_chapter_task(chapter: dict, rag_snippet: str, history: str, question: str) -> str:
    """构建单章task（控制在4000字符以内）"""
    prompt = chapter["prompt"].format(rag_note="使用以下RAG数据支撑分析。")
    header = f"""你是一个学术战略研究分析引擎。

## 核心问题
{question}

## 写作要求
{prompt}

## RAG参考数据（本章节相关）
{rag_snippet}

## 历史参照
{history}

## 输出要求
- 直接输出markdown正文，不要输出标题"第X章"（后面会拼接）
- 字数：{chapter['prompt'].split('（')[1].split('字）')[0]}字
- 每个判断标注[RAG: 人格名]
- 💡是深层逻辑，📄是原始素材"""
    return header


def main():
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = "中国收复台湾的战略窗口期是什么时候？必要条件是什么？"

    os.makedirs(REPORT_DIR, exist_ok=True)

    # Step 1: 跑pipeline获取框架
    print("🧠 运行Pipeline v3获取RAG数据...")
    full_task, framework = run_pipeline(question, auto_save=False)

    # Step 2: 保存完整task
    with open("/tmp/think_tank_v3_full_task.txt", "w") as f:
        f.write(full_task)
    print(f"  完整task: {len(full_task)} 字符")

    # Step 3: 提取历史参照
    history = extract_history(full_task)

    # Step 4: 分段生成
    print(f"\n📝 分段生成 {len(CHAPTERS)} 个章节...")
    chapter_files = []

    for ch in CHAPTERS:
        fname = f"/tmp/chapter_{ch['name']}.md"
        # 检查是否已生成
        if os.path.exists(fname) and os.path.getsize(fname) > 200:
            print(f"  {ch['name']}: 已存在")
            chapter_files.append(fname)
            continue

        # 提取该章节需要的RAG
        topics = CHAPTER_TOPICS.get(ch['name'], [])
        rag_snippet = extract_topic_rag(full_task, topics)
        if not rag_snippet:
            rag_snippet = "（本章节无特定RAG数据，使用通用知识）"

        # 构建章节task
        ch_task = build_chapter_task(ch, rag_snippet, history, question)
        print(f"  {ch['name']}: task {len(ch_task)} 字符...", end=" ", flush=True)

        # 保存task文件
        task_fname = f"/tmp/chapter_task_{ch['name']}.txt"
        with open(task_fname, "w") as f:
            f.write(ch_task)

        # 通过子session生成
        result = subprocess.run(
            ["openclaw", "sessions", "spawn", "--runtime", "subagent", "--mode", "run",
             "--run-timeout", "300",
             "--label", f"ch-{ch['name']}",
             "--",
             f"读取 /tmp/chapter_task_{ch['name']}.txt 获取写作指令。按要求撰写报告章节，然后用write工具保存到 {fname}。写完后执行 wc -c {fname} 确认。"],
            capture_output=True, text=True, timeout=360, cwd="/home/tearsyu/.openclaw/workspace"
        )

        if os.path.exists(fname) and os.path.getsize(fname) > 200:
            print(f"✅ {os.path.getsize(fname)} bytes")
            chapter_files.append(fname)
        else:
            print(f"❌ 生成失败")
            # 尝试从stdout提取
            if result.stdout:
                with open(fname, "w") as f:
                    f.write(result.stdout)
                chapter_files.append(fname)

    # Step 5: 拼接
    if len(chapter_files) < len(CHAPTERS):
        print(f"\n⚠️ 只有 {len(chapter_files)}/{len(CHAPTERS)} 章节生成成功")

    report_path = os.path.join(REPORT_DIR, f"{''.join(c for c in question[:20] if c not in '？?，,、：:！!')}_2026-04-21.md")

    title = f"# {question}\n\n> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}\n> 智库分析Pipeline v3 | {framework['rag_total']}条RAG | {framework['historical_events']}个历史事件\n\n---\n\n"

    with open(report_path, "w") as f:
        f.write(title)
        for fname in chapter_files:
            content = open(fname).read()
            f.write(content.strip() + "\n\n---\n\n")

    total_size = os.path.getsize(report_path)
    total_chars = len(open(report_path).read())
    print(f"\n✅ 报告生成完成: {report_path}")
    print(f"   {total_size} bytes, {total_chars} 字符, {len(chapter_files)} 章节")

    return report_path


if __name__ == "__main__":
    main()
