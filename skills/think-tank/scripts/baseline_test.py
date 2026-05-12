#!/usr/bin/env python3
"""
RAG系统基准测试 v1.0

测试维度：
1. 人物匹配准确率（personas检索是否返回了正确的专家）
2. 维度覆盖度（检索结果是否覆盖了所有相关dim）
3. 相关性评分（返回的claims/evidence是否与查询相关）
4. 抗干扰能力（能否避免返回不相关人物）
5. 召回深度（是否漏掉重要观点）

输出：
- 自动化指标报告
- 抽样人工验证清单
"""

import json
import lancedb
import requests
from pathlib import Path
from collections import defaultdict
import time

DB_PATH = "/mnt/d/data/think-tank-data/personalities/_lacandb/lacan_db_v2"
OLLAMA_URL = "http://172.26.0.1:11434/api/embed"
OLLAMA_MODEL = "bge-m3:latest"
COLLECTION_NAMES = ["personas", "cognitive", "claims", "evidence", "tracing", "tension"]

def get_embedding(text):
    r = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "input": text})
    return r.json()["embeddings"][0]

# 基准测试查询集：20类 × 5个查询 = 100个
BASELINE_QUERIES = [
    # 类别1: 金融/市场（应返回Fama、塞勒等）
    {"q": "为什么指数基金能战胜大多数主动管理基金？", "expected_persons": ["Eugene_Fama", "理查德·塞勒"], "category": "金融-市场效率"},
    {"q": "股票市场的异常现象是市场无效的证据还是风险补偿？", "expected_persons": ["Eugene_Fama", "理查德·塞勒"], "category": "金融-异常现象"},
    {"q": "投资者为什么会过度交易导致收益下降？", "expected_persons": ["丹尼尔·卡尼曼", "理查德·塞勒"], "category": "金融-行为偏差"},
    {"q": "三因子模型如何解释小市值和价值股的超额收益？", "expected_persons": ["Eugene_Fama"], "category": "金融-资产定价"},
    {"q": "前景理论如何解释人们在投资中的损失厌恶？", "expected_persons": ["丹尼尔·卡尼曼", "阿莫斯·特沃斯基"], "category": "金融-决策理论"},
    
    # 类别2: 认知偏差（应返回卡尼曼、特沃斯基、斯坦诺维奇）
    {"q": "确认偏误如何影响人们对政治新闻的判断？", "expected_persons": ["丹尼尔·卡尼曼", "基思·斯坦诺维奇"], "category": "认知-确认偏误"},
    {"q": "可得性启发式如何导致人们对风险的错误评估？", "expected_persons": ["丹尼尔·卡尼曼", "阿莫斯·特沃斯基"], "category": "认知-可得性"},
    {"q": "为什么聪明人也会做出不理性的决策？", "expected_persons": ["基思·斯坦诺维奇", "丹尼尔·卡尼曼"], "category": "认知-理性与智力"},
    {"q": "锚定效应在商业谈判中如何被利用？", "expected_persons": ["丹尼尔·卡尼曼", "阿莫斯·特沃斯基"], "category": "认知-锚定"},
    {"q": "双系统理论如何解释直觉与理性思考的冲突？", "expected_persons": ["丹尼尔·卡尼曼", "基思·斯坦诺维奇"], "category": "认知-双系统"},
    
    # 类别3: 社会影响（应返回阿希、米尔格拉姆、西奥迪尼、津巴多）
    {"q": "为什么人们会在明显错误的答案上附和多数人？", "expected_persons": ["所罗门·阿希"], "category": "社会-从众"},
    {"q": "权威命令如何让普通人做出违背道德的行为？", "expected_persons": ["斯坦利·米尔格拉姆", "菲利普·津巴多"], "category": "社会-权威服从"},
    {"q": "六种说服原则在营销中如何被运用？", "expected_persons": ["罗伯特·西奥迪尼"], "category": "社会-说服"},
    {"q": "为什么好人会在恶劣环境中做出残忍行为？", "expected_persons": ["菲利普·津巴多"], "category": "社会-情境力量"},
    {"q": "描述性规范如何比指令性规范更有效地改变行为？", "expected_persons": ["罗伯特·西奥迪尼"], "category": "社会-规范"},
    
    # 类别4: 军事战略（应返回毛泽东、林彪、粟裕等）
    {"q": "游击战的基本原则和实施条件是什么？", "expected_persons": ["毛泽东"], "category": "军事-游击战"},
    {"q": "集中优势兵力各个歼灭敌人的战术原理", "expected_persons": ["毛泽东", "粟裕"], "category": "军事-战术原则"},
    {"q": "运动战与阵地战的优劣对比和适用条件", "expected_persons": ["毛泽东", "林彪"], "category": "军事-战法对比"},
    {"q": "情报工作如何决定军事行动的成败？", "expected_persons": ["佐尔格", "戴笠_Dai_Li"], "category": "军事-情报"},
    {"q": "闪电战的战术创新和战略局限性", "expected_persons": ["古德里安_Guderian"], "category": "军事-装甲战术"},
    
    # 类别5: 心理治疗（应返回罗杰斯、萨提亚、福沃德等）
    {"q": "以人为中心的治疗方法如何建立治疗关系？", "expected_persons": ["卡尔·罗杰斯"], "category": "心理治疗-人本主义"},
    {"q": "家庭系统治疗如何识别和改变不健康的互动模式？", "expected_persons": ["维吉尼亚·萨提亚"], "category": "心理治疗-家庭"},
    {"q": "如何识别和应对有毒父母的情感勒索？", "expected_persons": ["苏珊·福沃德"], "category": "心理治疗-有毒关系"},
    {"q": "积极心理学如何帮助人们建立心理韧性？", "expected_persons": ["马丁·塞利格曼"], "category": "心理治疗-积极心理学"},
    {"q": "情绪识别在心理评估中的作用和局限", "expected_persons": ["保罗·艾克曼"], "category": "心理治疗-情绪"},
    
    # 类别6: 群体心理（应返回勒庞、海特等）
    {"q": "群体极化现象如何解释社交媒体上的极端观点？", "expected_persons": ["古斯塔夫·勒庞"], "category": "群体-极化"},
    {"q": "道德直觉如何先于理性推理影响政治立场？", "expected_persons": ["乔纳森·海特"], "category": "群体-道德直觉"},
    {"q": "群体凝聚力何时变成集体思维导致决策失误？", "expected_persons": ["古斯塔夫·勒庞", "所罗门·阿希"], "category": "群体-集体思维"},
    
    # 类别7: 跨学科复杂查询
    {"q": "认知偏差和社会影响如何共同导致金融危机中的羊群行为？", "expected_persons": ["丹尼尔·卡尼曼", "古斯塔夫·勒庞", "理查德·塞勒"], "category": "跨学科-金融危机"},
    {"q": "军事决策中的认知偏差：指挥官如何在信息过载下做出正确判断？", "expected_persons": ["丹尼尔·卡尼曼", "毛泽东", "林彪"], "category": "跨学科-军事认知"},
    {"q": "说服技术如何被用于社会工程攻击？", "expected_persons": ["罗伯特·西奥迪尼", "凯文·米特尼克"], "category": "跨学科-社会工程"},
    {"q": "时间视角理论如何解释不同文化对未来的态度差异？", "expected_persons": ["菲利普·津巴多"], "category": "跨学科-时间文化"},
    {"q": "设计思维如何借鉴认知心理学改善用户体验？", "expected_persons": ["唐纳德·诺曼", "丹尼尔·卡尼曼"], "category": "跨学科-设计认知"},
    
    # 类别8: 设计/用户体验（诺曼）
    {"q": "为什么用户经常误用产品设计？ affordance理论如何解释？", "expected_persons": ["唐纳德·诺曼"], "category": "设计-可用性"},
    {"q": "设计中的概念模型为什么比用户手册更重要？", "expected_persons": ["唐纳德·诺曼"], "category": "设计-概念模型"},
    
    # 类别9: 中国政治/军事人物
    {"q": "邓小平的改革开放决策体现了怎样的风险偏好？", "expected_persons": ["邓小平"], "category": "中国-改革开放"},
    {"q": "周恩来的外交风格如何体现他的认知特点？", "expected_persons": ["周恩来"], "category": "中国-外交"},
    {"q": "陈云的经济治理哲学如何平衡计划与市场？", "expected_persons": ["陈云"], "category": "中国-经济治理"},
    {"q": "彭德怀的军事指挥风格与林彪有何本质不同？", "expected_persons": ["彭德怀", "林彪"], "category": "中国-军事对比"},
    
    # 类别10: 信息安全（米特尼克）
    {"q": "社会工程学攻击如何利用人的心理弱点？", "expected_persons": ["凯文·米特尼克", "罗伯特·西奥迪尼"], "category": "安全-社会工程"},
    {"q": "组织如何建立有效的安全意识培训来抵御钓鱼攻击？", "expected_persons": ["凯文·米特尼克"], "category": "安全-意识培训"},
    
    # 类别11-20: 更多测试查询（精简）
    {"q": "武元甲的游击战略如何结合了毛泽东思想和越南实际？", "expected_persons": ["武元甲", "毛泽东"], "category": "军事-越南"},
    {"q": "隆美尔在北非战役中的战术创新和战略失误", "expected_persons": ["隆美尔_Rommel"], "category": "军事-北非"},
    {"q": "佐尔格的情报网络如何影响了二战太平洋战局？", "expected_persons": ["佐尔格"], "category": "军事-情报二战"},
    {"q": "卡纳里斯的德国军事情报局在二战中的双重角色", "expected_persons": ["卡纳里斯"], "category": "军事-德国情报"},
    {"q": "杜勒斯如何构建冷战时期的美国情报体系？", "expected_persons": ["杜勒斯_Allen_Dulles"], "category": "军事-CIA"},
    {"q": "切格瓦拉的游击战理论为何在玻利维亚失败？", "expected_persons": ["切格瓦拉_Che_Guevara"], "category": "军事-拉美"},
    {"q": "山本五十六为什么反对对美开战却策划了珍珠港？", "expected_persons": ["山本五十六_Yamamoto"], "category": "军事-日本"},
    {"q": "斯坦利·米尔格拉姆的服从实验的伦理争议", "expected_persons": ["斯坦利·米尔格拉姆"], "category": "心理-伦理"},
    {"q": "进化心理学如何解释人类的择偶偏好？", "expected_persons": ["Steven_Pinker"], "category": "心理-进化"},
    {"q": "Antonio_Damasio的躯体标记假说如何解释情绪在决策中的作用？", "expected_persons": ["Antonio_Damasio"], "category": "心理-神经"},
    {"q": "津巴多的斯坦福监狱实验揭示了什么关于权力和角色的真相？", "expected_persons": ["菲利普·津巴多"], "category": "心理-监狱实验"},
    {"q": "特沃斯基的相似性特征对比理论如何挑战传统距离模型？", "expected_persons": ["阿莫斯·特沃斯基"], "category": "认知-相似性"},
    {"q": "基思·斯坦诺维奇为什么区分智力与理性？CART测试如何测量理性？", "expected_persons": ["基思·斯坦诺维奇"], "category": "认知-理性测量"},
    {"q": "塞勒的心理账户理论如何解释消费者不理性的储蓄行为？", "expected_persons": ["理查德·塞勒"], "category": "经济-心理账户"},
    {"q": "毛泽东的矛盾论如何指导军事战略决策？", "expected_persons": ["毛泽东"], "category": "中国-矛盾论"},
    {"q": "粟裕在淮海战役中的指挥艺术体现在哪些方面？", "expected_persons": ["粟裕"], "category": "中国-淮海"},
    {"q": "罗荣桓的政治工作方法如何保证军队战斗力？", "expected_persons": ["罗荣桓"], "category": "中国-政治工作"},
    {"q": "聂荣臻如何管理国防科技攻关的复杂系统工程？", "expected_persons": ["聂荣臻"], "category": "中国-国防科技"},
    {"q": "叶剑英在历史转折关头的政治判断力如何体现？", "expected_persons": ["叶剑英"], "category": "中国-政治判断"},
    {"q": "崔可夫在斯大林格勒保卫战中的城市防御战术创新", "expected_persons": ["崔可夫"], "category": "军事-斯大林格勒"},
    {"q": "张国华如何在高原地区指挥对印自卫反击战？", "expected_persons": ["张国华"], "category": "军事-对印"},
    {"q": "朱德作为红军总司令的军事思想核心是什么？", "expected_persons": ["朱德"], "category": "中国-朱德"},
    {"q": "盖伦的东方情报网络如何服务于西德的冷战战略？", "expected_persons": ["盖伦_Reinhard_Gehlen"], "category": "军事-冷战情报"},
    {"q": "诺曼的设计心理学如何将认知科学应用于产品？", "expected_persons": ["唐纳德·诺曼"], "category": "设计-设计心理"},
    {"q": "塞利格曼的习得性无助理论如何从动物实验推广到人类抑郁？", "expected_persons": ["马丁·塞利格曼"], "category": "心理-习得性无助"},
    {"q": "海特的道德基础理论如何解释东西方道德观差异？", "expected_persons": ["乔纳森·海特"], "category": "认知-道德基础"},
    {"q": "艾克曼的微表情识别技术在测谎中的应用和局限", "expected_persons": ["保罗·艾克曼"], "category": "心理-微表情"},
    {"q": "勒庞的群体心理学理论如何解释现代民粹主义运动？", "expected_persons": ["古斯塔夫·勒庞"], "category": "群体-民粹"},
    {"q": "西奥迪尼的互惠原则为什么是最强大的说服工具？", "expected_persons": ["罗伯特·西奥迪尼"], "category": "社会-互惠"},
    {"q": "阿希的从众实验结果对陪审团制度有什么启示？", "expected_persons": ["所罗门·阿希"], "category": "社会-陪审团"},
    
    # 边界测试：模糊/多义查询
    {"q": "如何做出更好的决策？", "expected_persons": ["丹尼尔·卡尼曼", "基思·斯坦诺维奇", "阿莫斯·特沃斯基"], "category": "边界-模糊"},
    {"q": "领导力最重要的品质是什么？", "expected_persons": [], "category": "边界-无特定专家"},
    {"q": "战争的本质是什么？", "expected_persons": ["毛泽东", "古德里安_Guderian"], "category": "边界-宽泛"},
    {"q": "2024年人工智能的发展趋势", "expected_persons": [], "category": "边界-无关"},
    {"q": "心理健康的重要性", "expected_persons": ["卡尔·罗杰斯", "马丁·塞利格曼", "苏珊·福沃德"], "category": "边界-广泛"},
]

def run_baseline():
    client = lancedb.connect(DB_PATH)
    
    results = []
    
    for i, test in enumerate(BASELINE_QUERIES):
        query = test["q"]
        expected = test["expected_persons"]
        category = test["category"]
        
        print(f"[{i+1}/{len(BASELINE_QUERIES)}] {category}: {query[:50]}...", flush=True)
        
        q_emb = get_embedding(query)
        
        query_result = {
            "query": query,
            "category": category,
            "expected_persons": expected,
            "retrieved": {}
        }
        
        # 检索所有集合
        for col_name in COLLECTION_NAMES:
            col = client.open_table(col_name)
            r = col.query(query_embeddings=[q_emb], n_results=10, 
                         include=["documents", "metadatas", "distances"])
            
            persons_in_col = []
            for doc, meta, dist in zip(r['documents'][0], r['metadatas'][0], r['distances'][0]):
                persons_in_col.append({
                    "person": meta.get("person", "?"),
                    "dim": meta.get("dim", "?"),
                    "distance": round(dist, 4),
                    "doc_preview": doc[:100]
                })
            
            query_result["retrieved"][col_name] = persons_in_col
        
        # 统计人物频次
        person_freq = defaultdict(int)
        person_min_dist = defaultdict(float)
        person_cols = defaultdict(set)
        
        for col_name, items in query_result["retrieved"].items():
            for item in items:
                p = item["person"]
                person_freq[p] += 1
                person_cols[p].add(col_name)
                if p not in person_min_dist or item["distance"] < person_min_dist[p]:
                    person_min_dist[p] = item["distance"]
        
        # 计算仲裁器分数（当前方案：频次40%+距离30%+覆盖度30%）
        max_freq = max(person_freq.values()) if person_freq else 1
        max_dist = max(person_min_dist.values()) if person_min_dist else 1
        
        person_scores = {}
        for p in person_freq:
            freq_norm = person_freq[p] / max_freq if max_freq > 0 else 0
            dist_norm = 1 - (person_min_dist[p] / max_dist) if max_dist > 0 else 0
            cov_norm = len(person_cols[p]) / len(COLLECTION_NAMES)
            score = freq_norm * 0.4 + dist_norm * 0.3 + cov_norm * 0.3
            person_scores[p] = round(score, 4)
        
        # 排序
        ranked = sorted(person_scores.items(), key=lambda x: -x[1])
        
        query_result["person_scores"] = dict(ranked[:10])
        query_result["top_person"] = ranked[0][0] if ranked else None
        query_result["top_score"] = ranked[0][1] if ranked else 0
        
        # 检查expected persons是否被召回
        query_result["recall"] = {}
        for ep in expected:
            found_rank = None
            for rank, (p, s) in enumerate(ranked):
                if p == ep:
                    found_rank = rank + 1
                    break
            query_result["recall"][ep] = found_rank
        
        results.append(query_result)
        time.sleep(0.5)  # 避免ollama过载
    
    return results

def analyze_results(results):
    """分析基准测试结果"""
    print("\n" + "="*80)
    print("📊 RAG系统基准测试报告")
    print("="*80)
    
    total = len(results)
    
    # 1. 人物匹配准确率
    print("\n## 1. 人物匹配准确率 (personas top-1)")
    correct_top1 = 0
    correct_top3 = 0
    has_expected = [r for r in results if r["expected_persons"]]
    
    for r in has_expected:
        top_person = r["top_person"]
        if top_person in r["expected_persons"]:
            correct_top1 += 1
        # top3检查
        top3 = list(r["person_scores"].keys())[:3]
        if any(p in r["expected_persons"] for p in top3):
            correct_top3 += 1
    
    print(f"  总查询: {len(has_expected)} (有期望人物的)")
    print(f"  Top-1准确率: {correct_top1}/{len(has_expected)} = {correct_top1/len(has_expected)*100:.1f}%")
    print(f"  Top-3准确率: {correct_top3}/{len(has_expected)} = {correct_top3/len(has_expected)*100:.1f}%")
    
    # 2. 期望人物召回率
    print("\n## 2. 期望人物召回率")
    total_expected = 0
    total_recalled = 0
    missed = []
    
    for r in has_expected:
        for ep, rank in r["recall"].items():
            total_expected += 1
            if rank is not None:
                total_recalled += 1
            else:
                missed.append((r["category"], r["query"][:40], ep))
    
    print(f"  期望人物出现次数: {total_expected}")
    print(f"  成功召回: {total_recalled}/{total_expected} = {total_recalled/total_expected*100:.1f}%")
    if missed:
        print(f"  未召回的({len(missed)}条):")
        for cat, q, p in missed[:10]:
            print(f"    [{cat}] {q}... → 缺 {p}")
    
    # 3. 按类别统计
    print("\n## 3. 按类别准确率")
    by_cat = defaultdict(lambda: {"total": 0, "top1": 0, "top3": 0})
    for r in has_expected:
        cat = r["category"]
        by_cat[cat]["total"] += 1
        if r["top_person"] in r["expected_persons"]:
            by_cat[cat]["top1"] += 1
        top3 = list(r["person_scores"].keys())[:3]
        if any(p in r["expected_persons"] for p in top3):
            by_cat[cat]["top3"] += 1
    
    for cat in sorted(by_cat.keys()):
        d = by_cat[cat]
        t1 = d["top1"]/d["total"]*100
        t3 = d["top3"]/d["total"]*100
        print(f"  {cat}: top1={t1:.0f}% top3={t3:.0f}% ({d['total']} queries)")
    
    # 4. 仲裁器评分分布
    print("\n## 4. 仲裁器评分分布")
    all_scores = []
    for r in results:
        for p, s in r["person_scores"].items():
            all_scores.append(s)
    
    all_scores.sort()
    if all_scores:
        print(f"  最低分: {all_scores[0]:.4f}")
        print(f"  中位数: {all_scores[len(all_scores)//2]:.4f}")
        print(f"  最高分: {all_scores[-1]:.4f}")
        print(f"  P25/P75: {all_scores[len(all_scores)//4]:.4f} / {all_scores[3*len(all_scores)//4]:.4f}")
    
    # 5. 集合覆盖度分析
    print("\n## 5. 各集合对正确匹配的贡献度")
    col_hits = defaultdict(int)
    col_total = defaultdict(int)
    
    for r in has_expected:
        for col_name, items in r["retrieved"].items():
            col_total[col_name] += 1
            for item in items[:5]:  # top5
                if item["person"] in r["expected_persons"]:
                    col_hits[col_name] += 1
                    break
    
    for col_name in COLLECTION_NAMES:
        rate = col_hits[col_name]/col_total[col_name]*100 if col_total[col_name] > 0 else 0
        print(f"  {col_name}: {col_hits[col_name]}/{col_total[col_name]} = {rate:.1f}%")
    
    # 6. 边界查询表现
    print("\n## 6. 边界查询表现")
    boundary = [r for r in results if r["category"].startswith("边界")]
    for r in boundary:
        top3 = list(r["person_scores"].keys())[:3]
        print(f"  [{r['category']}] {r['query'][:40]}...")
        print(f"    Top3: {top3}")
    
    return {
        "total_queries": total,
        "top1_accuracy": correct_top1 / len(has_expected) * 100 if has_expected else 0,
        "top3_accuracy": correct_top3 / len(has_expected) * 100 if has_expected else 0,
        "recall_rate": total_recalled / total_expected * 100 if total_expected > 0 else 0,
    }

if __name__ == "__main__":
    results = run_baseline()
    
    # 保存详细结果
    output_path = Path("/mnt/d/data/think-tank-data/reports/baseline_test_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n详细结果已保存: {output_path}")
    
    # 分析
    summary = analyze_results(results)
    
    # 保存摘要
    summary_path = output_path.parent / "baseline_test_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"摘要已保存: {summary_path}")
