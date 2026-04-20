#!/usr/bin/env python3
"""
topic_decomposer.py — 议题拆分搜索引擎
将复杂议题拆分为子问题，分别检索最相关人格，生成多视角分析框架
"""
import sys, json, time, gc
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
from personality_vecdb import PersonalityVectorDB

DB_PATH = '/mnt/d/data/think-tank-data/_chromadb/lacan_db'

# 议题分类模板
TOPIC_TEMPLATES = {
    'geo_politics': {
        'keywords': ['地缘政治', '领土', '主权', '制裁', '同盟', '核武器', '战争', '冲突', '军事', '国防', '台海', '台湾', '南海', '中美', '俄乌', '中东', '朝鲜', '印太'],
        'dimensions': ['战略评估', '风险评估', '外交博弈', '军事分析', '历史类比', '经济影响'],
        'default_persons': 5
    },
    'economics': {
        'keywords': ['经济', '贸易', '关税', '通胀', '货币', '供应链', '市场', '产业', '金融', '债务'],
        'dimensions': ['政策分析', '市场预期', '历史比较', '结构性因素', '博弈论视角', '风险评估'],
        'default_persons': 4
    },
    'social_psychology': {
        'keywords': ['社会', '心理', '群体', '认知', '舆论', '文化', '身份', '民族主义', '意识形态'],
        'dimensions': ['认知机制', '群体动力', '历史模式', '心理驱动', '传播分析'],
        'default_persons': 4
    },
    'military_strategy': {
        'keywords': ['战术', '战略', '兵力', '武器', '作战', '情报', '防御', '进攻', '威慑'],
        'dimensions': ['战略判断', '战术评估', '情报分析', '后勤保障', '历史战例', '心理战'],
        'default_persons': 5
    },
    'technology': {
        'keywords': ['技术', 'AI', '芯片', '科技', '创新', '网络安全', '太空', '量子'],
        'dimensions': ['技术趋势', '战略影响', '竞争格局', '风险分析', '伦理考量'],
        'default_persons': 3
    },
    'general': {
        'keywords': [],
        'dimensions': ['核心分析', '多角度评估', '历史类比', '风险与机遇'],
        'default_persons': 5
    }
}

def classify_topic(topic):
    """议题分类"""
    for cat, tmpl in TOPIC_TEMPLATES.items():
        if cat == 'general':
            continue
        hits = sum(1 for kw in tmpl['keywords'] if kw in topic)
        if hits >= 2:
            return cat
    return 'general'

def decompose_topic(topic, category=None):
    """将议题拆分为子问题维度"""
    if not category:
        category = classify_topic(topic)
    dimensions = TOPIC_TEMPLATES[category]['dimensions']
    
    # 生成子问题
    sub_questions = []
    for dim in dimensions:
        sub_questions.append(f"{topic} —— {dim}角度")
    
    return {
        'category': category,
        'main_topic': topic,
        'sub_questions': sub_questions,
        'dimensions': dimensions
    }

def search_personalities(db, topic, category=None, n_persons=5, n_per_dim=3):
    """
    对每个子维度搜索最相关的人格
    返回: {dimension: [{person, text, distance, collection}]}
    """
    decomp = decompose_topic(topic, category)
    
    results = {}
    all_persons = {}  # person -> best_distance
    
    for i, sq in enumerate(decomp['sub_questions']):
        dim_name = decomp['dimensions'][i]
        dim_results = []
        
        # 从 insights 和 materials 搜索
        for collection in ['insights', 'materials', 'core_profile']:
            try:
                r = db.find_similar(sq, collection=collection, n=n_per_dim)
                for item in r:
                    # 去重
                    if not any(x['person'] == item['person'] for x in dim_results):
                        dim_results.append(item)
                        # 记录全局最佳
                        p = item['person']
                        if p not in all_persons or item['distance'] < all_persons[p]:
                            all_persons[p] = item['distance']
            except Exception as e:
                pass
        
        results[dim_name] = dim_results[:n_per_dim]
        gc.collect()
    
    # 排序选出最相关人物
    ranked_persons = sorted(all_persons.items(), key=lambda x: x[1])[:n_persons]
    
    return {
        'decomposition': decomp,
        'ranked_persons': [{'person': p, 'best_distance': d} for p, d in ranked_persons],
        'dimension_results': results
    }

def format_report(search_result):
    """格式化搜索结果"""
    decomp = search_result['decomposition']
    lines = []
    lines.append(f"# 议题拆分: {decomp['main_topic']}")
    lines.append(f"分类: {decomp['category']} | 维度: {len(decomp['dimensions'])}个")
    lines.append("")
    
    lines.append("## 最相关人物")
    for p in search_result['ranked_persons']:
        lines.append(f"  {p['person']} (相关度: {p['best_distance']:.3f})")
    lines.append("")
    
    lines.append("## 各维度检索结果")
    for dim, items in search_result['dimension_results'].items():
        lines.append(f"### {dim}")
        if not items:
            lines.append("  (暂无数据)")
            continue
        for item in items:
            text = item['text'][:150].replace('\n', ' ')
            lines.append(f"  [{item['person']}] {text}...")
        lines.append("")
    
    return '\n'.join(lines)


if __name__ == '__main__':
    db = PersonalityVectorDB(db_path=DB_PATH)
    
    if len(sys.argv) < 2:
        # 演示
        topic = "美国对中国半导体制裁升级的影响"
    else:
        topic = ' '.join(sys.argv[1:])
    
    print(f"议题: {topic}")
    cat = classify_topic(topic)
    print(f"分类: {cat}")
    
    result = search_personalities(db, topic, category=cat)
    print(format_report(result))
