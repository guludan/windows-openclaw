# ====================================================================
# [ARCHIVED] 事件驱动路线 — 2026-05-25 证伪结论
# ====================================================================
# 验证结果：CCTV 文本在 S1/S2/S3 信号增强/门控中无统计显著区分度
#
# 1. 宏观政策底 → S3 门控：L0 无底日 S3 反转最强(+22%)，政策底日弱(+3.69%)
# 2. 行业共振 → S2 增强：所有回看窗口(3~14d)，有共振的 S2 均比无共振差
# 3. surplus_z ρ 规则：无单调性，极端恐慌区非最优
#
# 根因：CCTV 是滞后确认信号(产业政策已消化)，不适用于日频增强
# 用途切换：IndustryLexicon 可复用做中线行业轮动，不参与信号层
# ====================================================================

#!/usr/bin/env python3
"""
industry_lexicon.py — 新闻联播文本行业政策词库

按同花顺一级行业分类，提供文本匹配引擎。
用于 policy_injector v2 的 S1/S2 行业政策共振判断。

使用:
    from industry_lexicon import IndustryMatcher
    
    matcher = IndustryMatcher()
    matches = matcher.match("国务院常务会议研究新能源汽车产业发展...")
    # -> [('电力设备', 2, 0.8), ...]
    
    # 批量匹配
    results = matcher.batch_match(events_list, verbose=True)
"""
import re
from typing import List, Tuple, Dict, Optional

# ===== 行业词库 =====
# 每组: {行业代码, 核心词, 政策动作词, 匹配规则}

INDUSTRY_LEXICON = {
    "电力设备": {
        "ths_code": "电力设备",
        "core": [
            "新能源汽车", "新能源车", "电动汽车", "电动车",
            "锂电池", "锂电", "动力电池", "电池回收",
            "充电桩", "充电设施", "充换电",
            "光伏", "太阳能", "光伏发电", "分布式光伏",
            "风电", "风力发电", "海上风电", "陆上风电",
            "储能", "新型储能", "抽水蓄能",
            "特高压", "输电通道", "电网改造",
            "氢能", "氢燃料电池",
        ],
        "action": [
            "推广", "推广应用", "示范应用",
            "补贴", "购置税减免", "免征",
            "规划", "发展规划", "产业规划",
            "装机", "装机规模", "新增装机",
            "并网", "全额并网", "保障性并网",
            "下乡",
            "以旧换新",
        ],
    },
    "电子": {
        "ths_code": "电子",
        "core": [
            "半导体", "芯片", "集成电路", "晶圆",
            "光刻", "光刻机",
            "封装测试", "先进封装",
            "存储芯片", "存储器",
            "第三代半导体", "碳化硅", "氮化镓",
            "面板", "显示面板", "OLED", "Mini LED",
            "传感器", "图像传感器",
            "消费电子", "智能手机",
        ],
        "action": [
            "攻关", "技术攻关", "联合攻关",
            "自主可控", "国产替代", "自主创新",
            "大基金",
            "流片", "试产", "量产",
            "减税", "所得税减免",
        ],
    },
    "计算机": {
        "ths_code": "计算机",
        "core": [
            "人工智能", "AI", "大模型", "深度学习",
            "算力", "算力中心", "智算中心", "超算",
            "数据", "数据要素", "数据资产", "数据安全",
            "信创", "信息技术应用创新",
            "操作系统", "基础软件", "工业软件",
            "云计算", "云服务",
            "区块链",
            "数字经济", "数字中国",
        ],
        "action": [
            "部署", "开放", "共享",
            "数据交易", "数据流通",
            "基础设施", "新型基础设施", "新基建",
            "试点", "示范区",
            "安全审查",
        ],
    },
    "汽车": {
        "ths_code": "汽车",
        "core": [
            "乘用车", "商用车", "重卡",
            "自动驾驶", "智能驾驶", "无人驾驶",
            "车联网", "智能网联汽车",
            "汽车出口", "整车出口",
            "零部件", "汽车零部件",
            "飞行汽车",
        ],
        "action": [
            "开放", "放开", "取消限制",
            "出口退税",
            "路测", "测试示范区",
            "准入", "市场准入",
        ],
    },
    "通信": {
        "ths_code": "通信",
        "core": [
            "5G", "5G-A", "6G",
            "光通信", "光纤", "光缆",
            "卫星通信", "卫星互联网", "低轨卫星",
            "物联网", "工业互联网",
            "东数西算",
            "千兆光网",
        ],
        "action": [
            "覆盖", "全覆盖",
            "商用", "规模商用",
            "频谱", "频率分配",
            "基站建设",
        ],
    },
    "医药生物": {
        "ths_code": "医药生物",
        "core": [
            "创新药", "新药审批", "临床试验",
            "生物医药", "生物制药",
            "医疗器械", "医疗设备", "高值耗材",
            "中药", "中医药", "配方颗粒",
            "疫苗", "mRNA疫苗",
            "集采",
            "医保", "医保目录", "医保谈判",
        ],
        "action": [
            "审批", "加快审批", "优先审评",
            "纳入", "纳入医保",
            "创新", "鼓励创新",
            "基层", "基层医疗",
        ],
    },
    "食品饮料": {
        "ths_code": "食品饮料",
        "core": [
            "白酒", "酒类",
            "乳业", "奶业", "乳制品",
            "预制菜",
            "食品安全",
            "粮食", "粮食安全",
            "种业", "种子",
        ],
        "action": [
            "消费", "促进消费", "提振消费",
            "标准", "国家标准",
            "补贴", "农机补贴",
            "安全", "安全监管",
        ],
    },
    "房地产": {
        "ths_code": "房地产",
        "core": [
            "房地产", "楼市", "住房",
            "保障房", "保障性住房", "租赁住房",
            "城中村", "城中村改造",
            "房企", "开发商",
            "房贷", "按揭", "首付",
            "限购", "限售", "放开限购",
            "白名单", "融资白名单",
            "城市更新", "老旧小区改造",
        ],
        "action": [
            "取消", "放开", "调整",
            "降低", "下调",
            "支持", "合理融资",
            "保交楼", "保交付",
            "因城施策",
        ],
    },
    "国防军工": {
        "ths_code": "国防军工",
        "core": [
            "军工", "国防", "军队",
            "装备", "武器装备",
            "采购", "军品采购",
            "预算", "国防预算",
            "军民融合",
        ],
        "action": [
            "现代化", "国防现代化",
            "列装", "批量列装",
        ],
    },
    "银行": {
        "ths_code": "银行",
        "core": [
            "银行", "商业银行",
            "降准", "存款准备金率",
            "降息", "LPR", "贷款利率",
            "再贷款", "再贴现",
            "汇金", "中央汇金",
            "长期资金", "长线资金", "耐心资本",
        ],
        "action": [
            "下调", "降低",
            "释放", "注入",
            "稳定", "维护稳定",
            "听取", "汇报",
        ],
    },
    "有色金属": {
        "ths_code": "有色金属",
        "core": [
            "稀土", "稀土永磁",
            "锂", "锂矿", "碳酸锂",
            "钴", "镍",
            "铜", "铝",
            "黄金", "黄金储备",
        ],
        "action": [
            "储备", "战略储备",
            "开采", "矿权",
            "出口管制",
            "整合", "行业整合",
        ],
    },
    "基础化工": {
        "ths_code": "基础化工",
        "core": [
            "磷化工", "氟化工",
            "新材料", "化工新材料",
            "化肥", "农药",
            "可降解塑料",
        ],
        "action": [
            "准入", "产能置换",
            "环保", "排放标准",
        ],
    },
}

# 需要同时搭配行业词的通用排除词（单独出现不构成匹配）
NEEDS_CORE_ACTION = True  # 默认要求 core + action 都命中


class IndustryMatcher:
    """行业政策文本匹配器"""

    def __init__(self, lexicon: dict = None):
        self.lexicon = lexicon or INDUSTRY_LEXICON
        # 行业优先顺序：命中词数 > 词库中定义的顺序
        self._order = list(self.lexicon.keys())

        # 编译正则（加快匹配）
        self._compiled = {}
        for ind_name, kw in self.lexicon.items():
            # 如果 core 或 action 为空列表，编译空模式
            core_pat = "|".join(re.escape(w) for w in kw["core"]) if kw["core"] else "(?!)"
            action_pat = "|".join(re.escape(w) for w in kw["action"]) if kw["action"] else "(?!)"
            self._compiled[ind_name] = {
                "core_re": re.compile(core_pat, re.IGNORECASE),
                "action_re": re.compile(action_pat, re.IGNORECASE),
            }

    def match(self, text: str) -> List[Tuple[str, int, float]]:
        """
        匹配单条文本。

        Returns:
            [(行业码, 总命中词数, 置信度 0.0~1.0), ...]  按置信度降序
        """
        if not text or len(text) < 10:
            return []

        results = []
        for ind_name in self._order:
            kw = self.lexicon[ind_name]
            cr = self._compiled[ind_name]["core_re"]
            ar = self._compiled[ind_name]["action_re"]

            core_matches = cr.findall(text)
            action_matches = ar.findall(text)

            n_core = len(core_matches)
            n_action = len(action_matches)

            # 要求至少命中一个 core 词
            if n_core == 0:
                continue

            # 要求至少命中一个 action 词
            if NEEDS_CORE_ACTION and n_action == 0:
                continue

            total = n_core + n_action
            # 置信度: core 占比 0.4, action 占比 0.6, 上限 1.0
            confidence = min(n_core * 0.4 + n_action * 0.6, 1.0)
            results.append((kw["ths_code"], total, round(confidence, 2)))

        # 按置信度降序，同置信度按词库顺序
        results.sort(key=lambda x: (-x[2], self._order.index(x[0]) if x[0] in self._order else 999))
        return results

    def match_batch(self, texts: List[Tuple], verbose: bool = False) -> Dict[str, Dict]:
        """
        批量匹配。

        Args:
            texts: [(event_id或日期, text), ...]

        Returns:
            {key: {"industries": [...], "n_core": int, "n_action": int}, ...}
        """
        results = {}
        for key, text in texts:
            matches = self.match(text)
            if matches:
                results[key] = {
                    "industries": [m[0] for m in matches],
                    "total_hits": sum(m[1] for m in matches),
                    "confidence": matches[0][2] if matches else 0,
                }
        return results

    def compute_daily_industry_score(self, events_by_date: dict) -> dict:
        """
        计算逐日的行业政策评分。

        Args:
            events_by_date: {date: [event_row, ...]}  每行应有 full_text, title

        Returns:
            {date: {"industry": {"电力设备": n_hits, ...}}, "has_industry_policy": True/False}
        """
        daily = {}
        for d, evts in events_by_date.items():
            ind_counts = {}
            for ev in evts:
                text = ((ev.get("title") or "") + " " + (ev.get("full_text") or ""))
                matches = self.match(text)
                for ind_code, total, conf in matches:
                    ind_counts[ind_code] = ind_counts.get(ind_code, 0) + total
            daily[d] = {
                "industries": ind_counts,
                "has_any": len(ind_counts) > 0,
            }
        return daily


if __name__ == "__main__":
    # 快速测试
    matcher = IndustryMatcher()

    test_texts = [
        ("国常会", "李强主持召开国务院常务会议 研究促进新能源汽车产业高质量发展的政策措施"),
        ("金融", "坚定不移走中国特色金融发展之路 推动金融高质量发展"),
        ("半导体", "工信部：加快芯片研发和产业化 推动集成电路产业自主可控"),
        ("房地产", "住建部：取消限购 支持合理住房需求 保交楼工作取得积极进展"),
        ("日常", "全国各地春节期间旅游消费火爆 增长态势良好"),
    ]

    print("=" * 60)
    print("[industry_lexicon] 测试")
    print("=" * 60)

    for label, text in test_texts:
        matches = matcher.match(text)
        print(f"\n  [{label}] {text[:50]}...")
        if matches:
            for ind, total, conf in matches:
                print(f"    → {ind}: {total} 词命中, 置信度 {conf}")
        else:
            print(f"    → 无匹配")

    print(f"\n✅ 词库: {len(INDUSTRY_LEXICON)} 个行业")
    total_kw = sum(len(v["core"]) + len(v["action"]) for v in INDUSTRY_LEXICON.values())
    print(f"   总关键词: {total_kw}")
