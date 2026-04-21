#!/usr/bin/env python3
"""
历史参照长效化引擎
- 缓存历史事件到Redis，避免重复搜索
- 关联关键个体决策逻辑
- 历史事件 → 个体决策 → 逻辑溯源 → 可预测性分析

Redis数据结构：
- historical_events:{event_id} → hash (事件详情)
- event_personalities:{event_id} → set (相关人格)
- personality_decisions:{person}_{event_id} → hash (该人对此事件的决策/判断)
- market_impacts:{event_id} → list (市场影响记录)
- event_search_index → sorted set (按关键词匹配)
"""

import redis
import json
import hashlib
import time
import os
from datetime import datetime

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 0  # 历史事件用DB0

#chromadb路径
CHROMA_PATH = "/mnt/d/data/think-tank-data/_chromadb/lacan_db"

def get_redis():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

# ==================== 预置历史事件库 ====================
# 这些是长效化存储的"历史参照"，不需要每次搜索

PRELOADED_EVENTS = {
    "gulf_war_1991": {
        "name": "1991年海湾战争",
        "name_en": "Gulf War 1991",
        "date_range": "1990-08 - 1991-02",
        "description": "伊拉克入侵科威特，美国领导的多国部队解放科威特。从石油禁运到空袭到地面战，42天结束。",
        "key_actors": ["布什(老)", "萨达姆", "施瓦茨科普夫", "贝克", "玛格丽特·撒切尔"],
        "key_decisions": [
            {"actor": "布什(老)", "decision": "决定出兵而非仅制裁", "reasoning": "维护国际秩序+石油安全，拒绝重蹈越战覆辙但坚持有限目标"},
            {"actor": "萨达姆", "decision": "入侵科威特并错误判断美国反应", "reasoning": "误读美国大使葛拉斯皮信号，认为美国不会军事干预"},
        ],
        "market_impacts": {
            "brent_crude": {"pre_event": 18, "peak": 46, "post_event_6m": 19, "unit": "USD/barrel", "notes": "油价翻倍后快速回落"},
            "gold": {"pre_event": 390, "peak": 425, "post_event_6m": 360, "unit": "USD/oz"},
            "usd_index": {"pre_event": 89, "peak": 97, "post_event_6m": 93, "unit": "DXY"},
            "s_p_500": {"pre_event": 310, "trough": 295, "post_event_6m": 380, "unit": "index", "notes": "战争胜利后牛市启动"},
        },
        "personalities_relevant": ["杜勒斯_Allen_Dulles", "朱可夫", "盖伦_Reinhard_Gehlen", "周恩来"],
        "geo_factors": ["霍尔木兹海峡威胁", "中东石油运输", "联合国授权合法性", "联盟战争模式"],
        "lessons": [
            "短期油价冲击剧烈但持续时间有限（除非供应被永久破坏）",
            "美国快速军事优势→市场在战争初期就已price in",
            "联盟合法性是关键变量",
            "独裁者误判民主国家意志是常见模式",
        ],
        "tags": ["石油", "中东战争", "美国军事干预", "油价冲击", "联盟战争", "联合国授权"],
    },
    "iraq_war_2003": {
        "name": "2003年伊拉克战争",
        "name_en": "Iraq War 2003",
        "date_range": "2003-03 - 2011-12",
        "description": "美国以WMD为名入侵伊拉克，萨达姆政权被推翻。后续陷入多年反叛乱战争。",
        "key_actors": ["布什(小)", "切尼", "拉姆斯菲尔德", "鲍威尔", "布莱尔", "萨达姆"],
        "key_decisions": [
            {"actor": "布什(小)", "decision": "先发制人入侵", "reasoning": "9/11后安全范式转变，情报误判WMD存在，新保守主义意识形态驱动"},
            {"actor": "拉姆斯菲尔德", "decision": "压低兵力部署", "reasoning": "军事转型理念（少而精），低估战后治理复杂度"},
        ],
        "market_impacts": {
            "brent_crude": {"pre_event": 30, "peak": 78, "post_event_12m": 50, "unit": "USD/barrel", "notes": "油价长期结构性上升，不仅是战争因素"},
            "gold": {"pre_event": 350, "peak": 730, "post_event_12m": 580, "unit": "USD/oz"},
            "usd_index": {"pre_event": 100, "trough": 85, "post_event_12m": 88, "unit": "DXY", "notes": "美元因战争开支和双赤字走弱"},
        },
        "personalities_relevant": ["杜勒斯_Allen_Dulles", "周恩来", "盖伦_Reinhard_Gehlen", "Chomsky"],
        "geo_factors": ["先发制人战略", "情报误判", "战后治理失败", "什叶派vs逊尼派权力重组", "伊朗影响力扩张"],
        "lessons": [
            "战争后果的不可预测性远超军事胜利本身",
            "情报误判可以驱动重大战略决策（类比：阿希的从众实验→决策层集体思维盲区）",
            "战后权力真空→地区格局重组→次生危机（ISIS、伊朗扩张）",
            "美元霸权下的战争融资→长期货币压力",
        ],
        "tags": ["石油", "中东战争", "先发制人", "战后治理", "情报误判", "美元", "伊朗"],
    },
    "iran_iraq_war_1980": {
        "name": "1980-1988年两伊战争",
        "name_en": "Iran-Iraq War 1980-1988",
        "date_range": "1980-09 - 1988-08",
        "description": "萨达姆趁伊朗革命后虚弱入侵，8年消耗战，霍尔木兹海峡受威胁，油轮战。",
        "key_actors": ["萨达姆", "霍梅尼", "里根", "戈尔巴乔夫"],
        "key_decisions": [
            {"actor": "萨达姆", "decision": "入侵伊朗", "reasoning": "利用伊朗革命后军力空虚和内部混乱，误判伊朗恢复速度"},
            {"actor": "霍梅尼", "decision": "拒绝停火直到伊拉克赔款", "reasoning": "革命意识形态驱动，将战争神圣化为伊斯兰革命输出，低估经济代价"},
        ],
        "market_impacts": {
            "brent_crude": {"pre_event": 35, "peak": 39, "post_1986_low": 14, "unit": "USD/barrel", "notes": "油价长期高位震荡，1986沙特增产引发暴跌"},
            "gold": {"pre_event": 650, "peak": 850, "post_1982": 300, "unit": "USD/oz"},
        },
        "personalities_relevant": ["Chomsky", "周恩来", "杜勒斯_Allen_Dulles"],
        "geo_factors": ["霍尔木兹海峡封锁威胁", "油轮战", "化学武器", "代理人战争", "革命输出"],
        "lessons": [
            "消耗战对油价是长期支撑而非短期冲击",
            "宗教意识形态可以延长远超理性的战争（霍梅尼的renunciation of reason）",
            "大国往往选择支持较弱方维持平衡（里根同时支持两方）",
            "海峡封锁威胁的定价：并非封锁发生才涨价，威胁本身就是溢价",
        ],
        "tags": ["石油", "两伊战争", "霍尔木兹海峡", "油轮战", "革命", "消耗战"],
    },
    "suez_crisis_1956": {
        "name": "1956年苏伊士运河危机",
        "name_en": "Suez Crisis 1956",
        "date_range": "1956-07 - 1956-12",
        "description": "纳赛尔国有化苏伊士运河，英法以联军入侵但被美苏迫使撤军。标志着英法帝国终结。",
        "key_actors": ["纳赛尔", "艾登", "摩勒", "本-古里安", "艾森豪威尔", "赫鲁晓夫"],
        "key_decisions": [
            {"actor": "艾森豪威尔", "decision": "迫使英法撤军", "reasoning": "维护美国在中东的主导权，避免苏联借机扩张，美元与石油挂钩战略"},
        ],
        "market_impacts": {
            "brent_crude": {"pre_event": 3, "peak": 5, "post_event_6m": 3.5, "unit": "USD/barrel"},
        },
        "personalities_relevant": ["周恩来", "杜勒斯_Allen_Dulles", "Chomsky"],
        "geo_factors": ["运河封锁", "帝国权力转移", "冷战格局", "石油美元雏形"],
        "lessons": [
            "海上通道封锁的冲击是暂时的（替代路线+压力解除）",
            "帝国衰落期的决策往往带有过度反应特征（英法的最后一搏）",
            "超级大国博弈决定区域冲突的终局，而非参战方本身",
        ],
        "tags": ["运河封锁", "帝国衰落", "冷战", "海上通道", "权力转移"],
    },
    "yom_kippur_1973": {
        "name": "1973年十月战争（赎罪日战争）",
        "name_en": "Yom Kippur War 1973",
        "date_range": "1973-10 - 1974-05",
        "description": "埃及和叙利亚突袭以色列。美国支持以色列→阿拉伯石油禁运→全球石油危机。",
        "key_actors": ["萨达特", "阿萨德(老)", "梅厄", "尼克松", "基辛格", "费萨尔国王"],
        "key_decisions": [
            {"actor": "费萨尔国王", "decision": "对美实施石油禁运", "reasoning": "报复美国支持以色列，将石油武器化作为政治工具"},
            {"actor": "尼克松/基辛格", "decision": "大规模空运以色列", "reasoning": "维护以色列生存+遏制苏联在中东影响力"},
        ],
        "market_impacts": {
            "brent_crude": {"pre_event": 3, "peak": 12, "post_event_12m": 11, "unit": "USD/barrel", "notes": "油价4倍暴涨，石油危机改变全球经济结构"},
            "gold": {"pre_event": 120, "peak": 197, "post_event_12m": 180, "unit": "USD/oz", "notes": "布雷顿森林体系刚崩溃，石油危机加速滞胀"},
            "s_p_500": {"pre_event": 105, "trough": 62, "post_event_12m": 90, "unit": "index", "notes": "1973-1974熊市，石油是核心触发因素"},
        },
        "personalities_relevant": ["周恩来", "杜勒斯_Allen_Dulles", "田文林", "殷罡"],
        "geo_factors": ["石油禁运", "阿拉伯团结", "赎罪日突袭情报失败", "超级大国介入", "OPEC力量崛起"],
        "lessons": [
            "石油禁运的冲击远大于军事冲突本身——供应链武器化是最强杠杆",
            "情报失败（以色列）可以改变整个地缘格局",
            "能源危机催生结构性变化：OPEC定价权、节能技术投资、非OPEC供应扩张",
            "滞胀环境下黄金和实物资产是最佳对冲",
        ],
        "tags": ["石油禁运", "OPEC", "中东战争", "滞胀", "黄金", "油价4倍"],
    },
    "tanker_war_2019": {
        "name": "2019年霍尔木兹海峡油轮袭击事件",
        "name_en": "Strait of Hormuz Tanker Attacks 2019",
        "date_range": "2019-05 - 2020-01",
        "description": "多艘油轮在霍尔木兹海峡附近遭袭，美伊紧张升级。特朗普退出伊核协议后的后果。",
        "key_actors": ["特朗普", "鲁哈尼", "苏莱曼尼(被暗杀)", "约翰逊"],
        "key_decisions": [
            {"actor": "特朗普", "decision": "退出伊核协议+制裁最大化+暗杀苏莱曼尼", "reasoning": "极限施压策略，相信经济压力可以迫使伊朗屈服，低估伊朗抵抗意志"},
            {"actor": "伊朗", "decision": "逐步突破伊核协议限制+袭击油轮", "reasoning": "不对称回应——在核协议框架内制造最大压力，避免直接军事冲突"},
        ],
        "market_impacts": {
            "brent_crude": {"pre_event": 70, "peak": 72, "post_event_6m": 65, "unit": "USD/barrel", "notes": "短期冲击有限，市场已price in中东紧张"},
            "gold": {"pre_event": 1300, "peak": 1600, "post_event_6m": 1550, "unit": "USD/oz"},
        },
        "personalities_relevant": ["田文林", "殷罡", "John_Mearsheimer", "Chomsky"],
        "geo_factors": ["霍尔木兹海峡", "不对称冲突", "极限施压", "代理人网络"],
        "lessons": [
            "局部袭击对油价的冲击越来越有限（页岩油改变了供需弹性）",
            "暗杀作为政策工具的不可预测后果（苏莱曼尼死后伊朗报复有限但长期仇恨积累）",
            "极限施压策略的有效性取决于对手的脆弱性（伊朗比预期更能承受）",
        ],
        "tags": ["霍尔木兹海峡", "油轮袭击", "极限施压", "暗杀", "伊朗", "页岩油"],
    },
    "russia_ukraine_2022": {
        "name": "2022年俄乌战争",
        "name_en": "Russia-Ukraine War 2022",
        "date_range": "2022-02 - present",
        "description": "俄罗斯全面入侵乌克兰。二战后欧洲最大冲突。能源格局重塑，全球供应链冲击。",
        "key_actors": ["普京", "泽连斯基", "拜登", "朔尔茨", "马克龙"],
        "key_decisions": [
            {"actor": "普京", "decision": "全面入侵乌克兰", "reasoning": "误判乌克兰抵抗意志+西方团结度，认为72小时内可以完成政权更迭"},
        ],
        "market_impacts": {
            "brent_crude": {"pre_event": 95, "peak": 128, "post_event_6m": 100, "unit": "USD/barrel", "notes": "欧洲天然气价格暴涨10倍"},
            "gold": {"pre_event": 1800, "peak": 2070, "post_event_6m": 1850, "unit": "USD/oz"},
            "usd_index": {"pre_event": 96, "peak": 114, "post_event_6m": 106, "unit": "DXY", "notes": "美元因避险+美联储加息走强"},
            "eur_usd": {"pre_event": 1.13, "trough": 0.95, "post_event_6m": 1.00, "unit": "EUR/USD", "notes": "欧元兑美元跌破平价"},
            "wheat": {"pre_event": 780, "peak": 1360, "post_event_6m": 900, "unit": "USD/ton", "notes": "全球粮食危机"},
        },
        "personalities_relevant": ["朱可夫", "崔可夫", "杜勒斯_Allen_Dulles", "John_Mearsheimer", "阎学通"],
        "geo_factors": ["能源武器化", "粮食安全", "北约扩张", "制裁有效性", "核威慑"],
        "lessons": [
            "能源武器是双刃剑（俄罗斯收入增加但欧洲加速脱俄）",
            "制裁需要时间生效，但长期可能改变对手行为",
            "粮食+能源同时冲击→新兴市场危机（斯里兰卡等）",
            "核威慑下大国直接冲突仍有红线",
        ],
        "tags": ["欧洲战争", "能源危机", "粮食危机", "制裁", "北约", "核威慑", "能源武器化"],
    },
}


def preload_events():
    """预置历史事件到Redis"""
    r = get_redis()
    count = 0
    for event_id, event in PRELOADED_EVENTS.items():
        key = f"historical_events:{event_id}"
        # 只在不存在时写入
        if not r.exists(key):
            r.hset(key, mapping={
                "name": event["name"],
                "name_en": event["name_en"],
                "date_range": event["date_range"],
                "description": event["description"],
                "key_actors": json.dumps(event["key_actors"], ensure_ascii=False),
                "key_decisions": json.dumps(event["key_decisions"], ensure_ascii=False),
                "market_impacts": json.dumps(event["market_impacts"], ensure_ascii=False),
                "lessons": json.dumps(event["lessons"], ensure_ascii=False),
                "geo_factors": json.dumps(event["geo_factors"], ensure_ascii=False),
                "tags": json.dumps(event["tags"], ensure_ascii=False),
                "updated_at": datetime.now().isoformat(),
            })
            # 关联人格
            for p in event.get("personalities_relevant", []):
                r.sadd(f"event_personalities:{event_id}", p)
            # 标签索引
            for tag in event.get("tags", []):
                r.sadd(f"tag_events:{tag}", event_id)
            count += 1
    
    print(f"Preloaded {count} new historical events (total: {len(PRELOADED_EVENTS)})")
    return count


def search_historical_events(query, tags=None):
    """搜索历史事件"""
    r = get_redis()
    results = []
    
    # 按标签匹配
    all_event_ids = set()
    if tags:
        for tag in tags:
            ids = r.smembers(f"tag_events:{tag}")
            all_event_ids.update(ids)
    
    # 全量搜索（如果没标签或标签结果太少）
    if len(all_event_ids) < 3:
        all_event_ids = {k.split(":")[1] for k in r.keys("historical_events:*")}
    
    for eid in all_event_ids:
        event_data = r.hgetall(f"historical_events:{eid}")
        if not event_data:
            continue
        results.append({
            "id": eid,
            "name": event_data.get("name", ""),
            "date_range": event_data.get("date_range", ""),
            "description": event_data.get("description", ""),
            "tags": json.loads(event_data.get("tags", "[]")),
            "lessons": json.loads(event_data.get("lessons", "[]")),
            "market_impacts": json.loads(event_data.get("market_impacts", "{}")),
            "key_decisions": json.loads(event_data.get("key_decisions", "[]")),
            "geo_factors": json.loads(event_data.get("geo_factors", "[]")),
        })
    
    return results


def add_custom_event(event_id, event_data):
    """添加自定义历史事件"""
    r = get_redis()
    key = f"historical_events:{event_id}"
    r.hset(key, mapping={
        "name": event_data["name"],
        "name_en": event_data.get("name_en", ""),
        "date_range": event_data.get("date_range", ""),
        "description": event_data.get("description", ""),
        "key_actors": json.dumps(event_data.get("key_actors", []), ensure_ascii=False),
        "key_decisions": json.dumps(event_data.get("key_decisions", []), ensure_ascii=False),
        "market_impacts": json.dumps(event_data.get("market_impacts", {}), ensure_ascii=False),
        "lessons": json.dumps(event_data.get("lessons", []), ensure_ascii=False),
        "geo_factors": json.dumps(event_data.get("geo_factors", []), ensure_ascii=False),
        "tags": json.dumps(event_data.get("tags", []), ensure_ascii=False),
        "updated_at": datetime.now().isoformat(),
    })
    for p in event_data.get("personalities_relevant", []):
        r.sadd(f"event_personalities:{event_id}", p)
    for tag in event_data.get("tags", []):
        r.sadd(f"tag_events:{tag}", event_id)
    print(f"Added event: {event_data['name']}")


def get_redis_status():
    """Redis状态"""
    r = get_redis()
    info = r.info()
    event_count = len(r.keys("historical_events:*"))
    return {
        "connected": r.ping(),
        "used_memory_human": info.get("used_memory_human", "?"),
        "total_keys": r.dbsize(),
        "historical_events": event_count,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "preload":
        preload_events()
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        status = get_redis_status()
        print(json.dumps(status, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
        tags = sys.argv[2:] if len(sys.argv) > 2 else None
        results = search_historical_events("", tags)
        for ev in results:
            print(f"\n{'='*60}")
            print(f"【{ev['name']}】({ev['date_range']})")
            print(f"  标签: {', '.join(ev['tags'])}")
            print(f"  市场影响: {json.dumps(ev['market_impacts'], ensure_ascii=False, indent=2)}")
            print(f"  历史教训: {'; '.join(ev['lessons'][:3])}")
    else:
        preload_events()
        status = get_redis_status()
        print(f"\nStatus: {json.dumps(status, indent=2)}")
