#!/usr/bin/env python3
"""
关键词密度引擎 v0.1 — 基于NLP的政策信息势能计算

功能：
  1. 对政策文档/新闻稿做中文分词
  2. 匹配预设政策词库，计算关键词密度
  3. 将关键词映射到同花顺行业，生成行业势能向量
  4. 计算重要性综合评分（来源权威×影响范围×政策层级）
  5. 支持指数衰减/线性衰减/幂律衰减三种模型

用法：
  python3 keyword_density_engine.py --doc "国务院印发《关于促进人工智能发展的若干意见》..." [--verbose]
  python3 keyword_density_engine.py --db --list-events  (列出未解析的事件)
  python3 keyword_density_engine.py --db --process-all  (批量处理event_list中未解析的文档)

依赖：jieba, numpy, scipy, asyncpg
"""

import re, json, sys, os, argparse, logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field, asdict
import numpy as np

# ─── 日志 ───
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('density_engine')

# ─── 同花顺行业（90个）───
THS_INDUS = [
    "IT服务", "专用设备", "中药", "互联网电商", "保险", "元件",
    "光伏设备", "光学光电子", "公路铁路运输", "其他电子", "其他电源设备",
    "其他社会服务", "养殖业", "军工电子", "军工装备", "农产品加工",
    "农化制品", "包装印刷", "化学制品", "化学制药", "化学原料",
    "化学纤维", "医疗器械", "医疗服务", "医药商业", "半导体",
    "厨卫电器", "塑料制品", "多元金融", "家居用品", "小家电",
    "小金属", "工业金属", "工程机械", "建筑材料", "建筑装饰",
    "影视院线", "房地产", "教育", "文化传媒", "旅游及酒店",
    "服装家纺", "机场航运", "橡胶制品", "汽车整车", "汽车服务及其他",
    "汽车零部件", "油气开采及服务", "消费电子", "港口航运", "游戏",
    "煤炭开采加工", "燃气", "物流", "环保设备", "环境治理",
    "生物制品", "电力", "电子化学品", "电机", "电池",
    "电网设备", "白色家电", "白酒", "石油加工贸易", "种植业与林业",
    "纺织制造", "综合", "美容护理", "能源金属", "自动化设备",
    "计算机设备", "证券", "贵金属", "贸易", "轨交设备",
    "软件开发", "通信服务", "通信设备", "通用设备", "造纸",
    "金属新材料", "钢铁", "银行", "零售", "非金属材料",
    "风电设备", "食品加工制造", "饮料制造", "黑色家电"
]

# ─── 默认参数（v2 — 2026-05-23 宏观周期校准）───
DEFAULT_CONFIG = {
    "lambda": 0.027,           # 全局信息衰减率（半衰期 ln2/0.027 ≈ 25.7天）
    "decay_model": "exponential",  # exponential | linear | power_law
    "w_authority": 0.30,     # 来源权威权重
    "w_scope": 0.40,         # 影响范围权重
    "w_level": 0.30,         # 政策层级权重
    "asymmetry_enabled": True,  # √ 利好/利空非对称性（v2校准开启）
    "asymmetry_pos_lambda": 0.023,  # 利好衰减更慢
    "asymmetry_neg_lambda": 0.030,  # 利空衰减略快
}

# ─── 地区/企业/人名正则（排除项）───
# 这些词虽然不是政策词但也会被匹配，先做排除列表
BLOCKWORDS = [
    "北京", "上海", "广州", "深圳", "天津", "重庆",
    "研究", "发展", "建设", "支持", "推动", "促进", "加强", "完善",
    "提高", "提升", "扩大", "深化", "优化", "调整", "稳定", "保障",
    "企业", "市场", "产业", "行业", "项目", "重点",
    "集团", "有限公司", "股份公司",
    "中国", "中央", "国务院", "国家",
    "通知", "意见", "办法", "方案", "规划", "报告", "规定",
]

# ─── 词库 → 行业映射 ───
# 每个关键词：[{"industries": [...], "weight": float, "direction": 1|-1|0, "scope": str}]
# scope: "broad" 宏观 / "sector" 行业 / "company" 公司级
# weight: 0.1~1.0, 默认专家赋值，后续通过事件回测校准

# ═══════════════════════════════════════════════════════════════════════════════
#  关键词 → 同花顺行业 映射表
#  版本: v2 — 2026-05-23 扩展至224词
#  利好: 134词 | 利空: 65词 | 中性/双刃: 18词 | 修饰词: 7词
# ═══════════════════════════════════════════════════════════════════════════════
DEFAULT_KEYWORD_MAP = {

    # =========================================================================
    # 1. 宏观总量/货币政策 (利好14 + 利空3)
    # =========================================================================
    "降准": [{"industries": ["银行", "证券", "保险", "多元金融", "房地产"], "weight": 0.85, "direction": 1, "scope": "broad"}],
    "降息": [{"industries": ["银行", "证券", "保险", "房地产", "建筑装饰"], "weight": 0.85, "direction": 1, "scope": "broad"}],
    "加息": [{"industries": ["银行", "证券", "保险", "房地产"], "weight": 0.75, "direction": -1, "scope": "broad"}],
    "MLF": [{"industries": ["银行", "证券"], "weight": 0.70, "direction": 1, "scope": "broad"}],
    "LPR": [{"industries": ["银行", "房地产"], "weight": 0.70, "direction": 1, "scope": "broad"}],
    "逆回购": [{"industries": ["银行", "证券"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "公开市场操作": [{"industries": ["银行", "证券"], "weight": 0.50, "direction": 0, "scope": "broad"}],
    "赤字率": [{"industries": ["建筑装饰", "建筑材料", "电力"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "特别国债": [{"industries": ["银行", "证券", "建筑装饰"], "weight": 0.80, "direction": 1, "scope": "broad"}],
    "地方专项债": [{"industries": ["建筑装饰", "建筑材料", "电力"], "weight": 0.65, "direction": 1, "scope": "broad"}],
    "积极财政": [{"industries": ["建筑装饰", "建筑材料", "银行"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "稳健货币": [{"industries": ["银行", "证券"], "weight": 0.50, "direction": 0, "scope": "broad"}],
    "量化宽松": [{"industries": ["银行", "证券", "保险"], "weight": 0.80, "direction": 1, "scope": "broad"}],
    "流动性投放": [{"industries": ["银行", "证券"], "weight": 0.65, "direction": 1, "scope": "broad"}],
    "宽松": [{"industries": ["银行", "证券", "保险", "房地产"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "收紧": [{"industries": ["银行", "证券", "房地产"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "去杠杆": [{"industries": ["银行", "证券", "房地产", "建筑装饰"], "weight": 0.70, "direction": -1, "scope": "broad"}],

    # =========================================================================
    # 2. 产业政策 — 科技/制造 (利好30 + 利空2)
    # =========================================================================
    "新能源": [{"industries": ["电池", "光伏设备", "风电设备", "电网设备", "汽车整车"], "weight": 0.80, "direction": 1, "scope": "sector"}],
    "光伏": [{"industries": ["光伏设备"], "weight": 0.80, "direction": 1, "scope": "sector"}],
    "风电": [{"industries": ["风电设备"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "新能源汽车": [{"industries": ["汽车整车", "电池", "汽车零部件", "能源金属"], "weight": 0.85, "direction": 1, "scope": "sector"}],
    "碳达峰": [{"industries": ["环保设备", "光伏设备", "风电设备", "电力", "化学制品"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "碳中和": [{"industries": ["环保设备", "光伏设备", "风电设备", "电力", "化学制品"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "节能减排": [{"industries": ["环保设备", "光伏设备", "化学制品"], "weight": 0.50, "direction": 1, "scope": "sector"}],
    "半导体": [{"industries": ["半导体", "计算机设备", "软件开发"], "weight": 0.85, "direction": 1, "scope": "sector"}],
    "集成电路": [{"industries": ["半导体", "计算机设备", "软件开发"], "weight": 0.85, "direction": 1, "scope": "sector"}],
    "芯片": [{"industries": ["半导体", "元件", "计算机设备"], "weight": 0.80, "direction": 1, "scope": "sector"}],
    "光刻": [{"industries": ["半导体", "专用设备", "电子化学品"], "weight": 0.80, "direction": 1, "scope": "sector"}],
    "算力": [{"industries": ["计算机设备", "通信设备", "IT服务", "半导体"], "weight": 0.80, "direction": 1, "scope": "sector"}],
    "人工智能": [{"industries": ["软件开发", "计算机设备", "IT服务", "文化传媒", "游戏"], "weight": 0.80, "direction": 1, "scope": "sector"}],
    "AI": [{"industries": ["软件开发", "计算机设备", "IT服务", "文化传媒"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "大数据": [{"industries": ["软件开发", "IT服务", "计算机设备", "文化传媒"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "云计算": [{"industries": ["软件开发", "IT服务", "通信设备", "文化传媒"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "数字经济": [{"industries": ["软件开发", "IT服务", "半导体", "消费电子", "通信设备", "通信服务"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "5G": [{"industries": ["通信设备", "通信服务", "消费电子"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "6G": [{"industries": ["通信设备", "通信服务", "消费电子"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "机器人": [{"industries": ["自动化设备", "专用设备", "计算机设备"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "工业互联网": [{"industries": ["软件开发", "IT服务", "通信设备"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "智能制造": [{"industries": ["自动化设备", "专用设备", "通用设备", "IT服务"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "低空经济": [{"industries": ["军工装备", "汽车零部件", "汽车整车", "通信设备"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "量子计算": [{"industries": ["计算机设备", "软件开发", "通信设备"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "量子通信": [{"industries": ["通信设备", "通信服务", "计算机设备"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "云计算": [{"industries": ["IT服务", "软件开发", "通信设备"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "区块链": [{"industries": ["软件开发", "IT服务", "计算机设备"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "信创": [{"industries": ["软件开发", "IT服务", "计算机设备", "半导体"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "国产替代": [{"industries": ["半导体", "软件开发", "专用设备", "IT服务", "计算机设备"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "自主可控": [{"industries": ["半导体", "软件开发", "计算机设备", "IT服务"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "制造业升级": [{"industries": ["专用设备", "通用设备", "自动化设备", "工程机械"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "核心部件": [{"industries": ["半导体", "元件", "专用设备", "通用设备"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "固态电池": [{"industries": ["电池", "化学制品", "汽车零部件", "能源金属"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "钙钛矿": [{"industries": ["光伏设备", "电力", "专用设备"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "氢能": [{"industries": ["电池", "光伏设备", "环保设备", "电力"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "储能": [{"industries": ["电池", "电网设备", "光伏设备", "风电设备"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "核能": [{"industries": ["电力", "通用设备", "专用设备"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "钠离子电池": [{"industries": ["电池", "化学制品", "能源金属"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "HBM": [{"industries": ["半导体", "元件", "计算机设备"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "先进封装": [{"industries": ["半导体", "专用设备", "元件"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "自动驾驶": [{"industries": ["汽车整车", "汽车零部件", "软件开发", "计算机设备"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "智能驾驶": [{"industries": ["汽车整车", "汽车零部件", "软件开发", "元件"], "weight": 0.70, "direction": 1, "scope": "sector"}],

    # 利空/中性
    "产能过剩": [{"industries": ["光伏设备", "电池", "钢铁", "化学制品", "汽车整车"], "weight": 0.70, "direction": -1, "scope": "sector"}],
    "反垄断": [{"industries": ["软件开发", "IT服务", "互联网电商", "文化传媒", "零售"], "weight": 0.75, "direction": -1, "scope": "sector"}],

    # =========================================================================
    # 3. 产业政策 — 新能源/汽车 (利好8 + 利空2)
    # =========================================================================
    "充电桩": [{"industries": ["电网设备", "汽车零部件", "电力"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "换电": [{"industries": ["汽车整车", "汽车零部件", "电网设备"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "光伏建筑": [{"industries": ["光伏设备", "建筑装饰", "建筑材料"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "海上风电": [{"industries": ["风电设备", "电网设备", "电力"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "电力现货": [{"industries": ["电力", "电网设备", "计算机设备"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "特高压": [{"industries": ["电网设备", "电力", "电气设备"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "新型电力系统": [{"industries": ["电网设备", "电力", "电气设备", "光伏设备"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "电动化": [{"industries": ["汽车整车", "电池", "汽车零部件", "能源金属"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "补贴退坡": [{"industries": ["新能源汽车", "电池", "汽车整车", "光伏设备"], "weight": 0.65, "direction": -1, "scope": "sector"}],
    "供过于求": [{"industries": ["光伏设备", "钢铁", "化学制品", "电池"], "weight": 0.60, "direction": -1, "scope": "sector"}],

    # =========================================================================
    # 4. 产业政策 — 消费电子/通讯 (利好6 + 利空1)
    # =========================================================================
    "消费电子": [{"industries": ["消费电子", "元件", "半导体", "光学光电子"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "折叠屏": [{"industries": ["消费电子", "元件", "光学光电子"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "卫星互联网": [{"industries": ["通信设备", "军工装备", "通信服务", "计算机设备"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "东数西算": [{"industries": ["IT服务", "通信设备", "计算机设备", "通信服务"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "光模块": [{"industries": ["通信设备", "消费电子", "元件"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "液冷": [{"industries": ["计算机设备", "通信设备", "通用设备"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "制裁": [{"industries": ["半导体", "消费电子", "通信设备", "软件与开发"], "weight": 0.80, "direction": -1, "scope": "broad"}],

    # =========================================================================
    # 5. 产业政策 — 医药/医疗 (利好8 + 利空5)
    # =========================================================================
    "集采": [{"industries": ["化学制药", "医疗器械", "中药", "生物制品"], "weight": 0.80, "direction": -1, "scope": "sector"}],
    "医保": [{"industries": ["化学制药", "中药", "医疗器械", "医药商业"], "weight": 0.60, "direction": 0, "scope": "sector"}],
    "创新药": [{"industries": ["生物制品", "化学制药", "医疗服务"], "weight": 0.80, "direction": 1, "scope": "sector"}],
    "中药": [{"industries": ["中药"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "生物医药": [{"industries": ["生物制品", "化学制药", "医疗服务"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "CXO": [{"industries": ["医疗服务", "化学制药", "生物制品"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "医疗设备": [{"industries": ["医疗器械", "专用设备"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "基因": [{"industries": ["生物制品", "医疗服务", "医疗器械"], "weight": 0.60, "direction": 0, "scope": "sector"}],
    "GLP-1": [{"industries": ["化学制药", "生物制品", "食品加工制造"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "疫苗": [{"industries": ["生物制品", "化学制药"], "weight": 0.65, "direction": 0, "scope": "sector"}],
    "医疗反腐": [{"industries": ["化学制药", "医疗器械", "医疗服务", "医药商业"], "weight": 0.70, "direction": -1, "scope": "sector"}],
    "医保控费": [{"industries": ["化学制药", "医疗器械", "中药"], "weight": 0.60, "direction": -1, "scope": "sector"}],
    "带量采购": [{"industries": ["化学制药", "医疗器械", "生物制品"], "weight": 0.78, "direction": -1, "scope": "sector"}],

    # =========================================================================
    # 6. 大金融 (利好10 + 利空4)
    # =========================================================================
    "注册制": [{"industries": ["证券", "银行"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "科创板": [{"industries": ["证券", "半导体", "软件开发", "化学制药"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "资本市场改革": [{"industries": ["证券", "银行"], "weight": 0.55, "direction": 1, "scope": "broad"}],
    "中长期资金": [{"industries": ["证券", "银行", "保险"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "保险": [{"industries": ["保险"], "weight": 0.50, "direction": 0, "scope": "sector"}],
    "养老金": [{"industries": ["保险", "银行", "证券"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "平准基金": [{"industries": ["证券", "银行", "保险"], "weight": 0.80, "direction": 1, "scope": "broad"}],
    "汇金": [{"industries": ["银行", "证券", "保险"], "weight": 0.75, "direction": 1, "scope": "broad"}],
    "国家队": [{"industries": ["银行", "证券"], "weight": 0.70, "direction": 1, "scope": "broad"}],
    "印花税": [{"industries": ["证券", "银行"], "weight": 0.75, "direction": 1, "scope": "broad"}],
    "减税降费": [{"industries": ["零售", "白酒", "食品加工制造", "饮料制造", "服装家纺"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "地方债务": [{"industries": ["银行", "建筑装饰", "房地产"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "城投": [{"industries": ["银行", "建筑装饰", "房地产"], "weight": 0.60, "direction": -1, "scope": "sector"}],
    "坏账": [{"industries": ["银行", "证券", "多元金融"], "weight": 0.60, "direction": -1, "scope": "sector"}],
    "系统性风险": [{"industries": ["银行", "证券", "保险", "房地产"], "weight": 0.75, "direction": -1, "scope": "broad"}],
    "信用紧缩": [{"industries": ["银行", "证券", "房地产", "建筑装饰"], "weight": 0.65, "direction": -1, "scope": "broad"}],

    # =========================================================================
    # 7. 地产/基建 (利好10 + 利空5)
    # =========================================================================
    "房地产调控": [{"industries": ["房地产", "银行", "建筑材料"], "weight": 0.75, "direction": 0, "scope": "sector"}],
    "房住不炒": [{"industries": ["房地产"], "weight": 0.70, "direction": -1, "scope": "sector"}],
    "保交楼": [{"industries": ["房地产", "建筑装饰", "建筑材料"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "棚改": [{"industries": ["房地产", "建筑装饰", "建筑材料"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "城中村改造": [{"industries": ["房地产", "建筑装饰", "建筑材料"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "新基建": [{"industries": ["通信设备", "软件开发", "电网设备", "光伏设备", "建筑装饰"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "基础设施": [{"industries": ["建筑装饰", "建筑材料", "公路铁路运输", "机场航运", "港口航运"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "水利": [{"industries": ["建筑装饰", "电力", "环保设备", "环境治理"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "交通强国": [{"industries": ["公路铁路运输", "机场航运", "港口航运", "工程机械", "轨交设备"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "城市更新": [{"industries": ["建筑装饰", "建筑材料", "房地产", "环保设备"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "保障房": [{"industries": ["房地产", "建筑装饰", "建筑材料"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "REITs": [{"industries": ["房地产", "多元金融", "建筑装饰", "银行"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "烂尾": [{"industries": ["房地产", "银行", "建筑装饰"], "weight": 0.70, "direction": -1, "scope": "sector"}],
    "房企暴雷": [{"industries": ["房地产", "银行", "建筑材料"], "weight": 0.80, "direction": -1, "scope": "sector"}],
    "停工": [{"industries": ["房地产", "建筑装饰"], "weight": 0.55, "direction": -1, "scope": "sector"}],

    # =========================================================================
    # 8. 消费 (利好15 + 利空3)
    # =========================================================================
    "扩大内需": [{"industries": ["白酒", "食品加工制造", "饮料制造", "零售", "汽车整车", "白色家电"], "weight": 0.65, "direction": 1, "scope": "broad"}],
    "消费": [{"industries": ["白酒", "食品加工制造", "饮料制造", "零售", "旅游及酒店", "白色家电"], "weight": 0.50, "direction": 1, "scope": "broad"}],
    "以旧换新": [{"industries": ["白色家电", "黑色家电", "汽车整车", "消费电子"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "家电下乡": [{"industries": ["白色家电", "黑色家电", "小家电", "汽车整车"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "汽车下乡": [{"industries": ["汽车整车"], "weight": 0.75, "direction": 1, "scope": "sector"}],
    "乡村振兴": [{"industries": ["种植业与林业", "养殖业", "农产品加工", "食品加工制造", "建筑装饰"], "weight": 0.50, "direction": 1, "scope": "broad"}],
    "免税": [{"industries": ["零售", "旅游及酒店", "机场航运", "服装家纺", "美容护理"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "促消费": [{"industries": ["白酒", "食品加工制造", "饮料制造", "零售", "汽车整车", "旅游及酒店"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "消费券": [{"industries": ["零售", "食品加工制造", "旅游及酒店", "文化传媒"], "weight": 0.55, "direction": 1, "scope": "broad"}],
    "直播电商": [{"industries": ["互联网电商", "文化传媒", "物流", "服装家纺"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "跨境电商": [{"industries": ["物流", "互联网电商", "纺织制造", "服装家纺", "小商品"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "预制菜": [{"industries": ["食品加工制造", "饮料制造", "旅游及酒店", "农产品加工"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "消费降级": [{"industries": ["零售", "服装家纺", "纺织制造"], "weight": 0.50, "direction": -1, "scope": "broad"}],
    "需求不足": [{"industries": ["有色金属", "钢铁", "化学制品", "房地产", "白酒"], "weight": 0.55, "direction": -1, "scope": "broad"}],
    "通缩": [{"industries": ["银行", "保险", "房地产", "零售", "白酒", "食品加工制造"], "weight": 0.75, "direction": -1, "scope": "broad"}],
    "生育": [{"industries": ["食品加工制造", "服装家纺", "家居用品", "教育", "医疗服务"], "weight": 0.50, "direction": 1, "scope": "broad"}],
    "老龄化": [{"industries": ["医疗服务", "保险", "中药", "家用电器"], "weight": 0.50, "direction": 1, "scope": "broad"}],
    "银发经济": [{"industries": ["医疗服务", "保险", "中药", "家居用品", "旅游及酒店"], "weight": 0.55, "direction": 1, "scope": "sector"}],

    # =========================================================================
    # 9. 军工/安全 (利好10 + 利空5)
    # =========================================================================
    "国防": [{"industries": ["军工电子", "军工装备", "半导体", "通信设备"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "军工": [{"industries": ["军工电子", "军工装备"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "军民融合": [{"industries": ["军工电子", "军工装备", "半导体", "软件开发", "专用设备"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "数据安全": [{"industries": ["软件开发", "IT服务", "通信设备", "计算机设备"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "粮食安全": [{"industries": ["种植业与林业", "养殖业", "农产品加工", "食品加工制造"], "weight": 0.55, "direction": 1, "scope": "broad"}],
    "能源安全": [{"industries": ["油气开采及服务", "煤炭开采加工", "电力", "光伏设备", "风电设备"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "产业链安全": [{"industries": ["半导体", "元件", "专用设备", "化学制品", "工业金属"], "weight": 0.65, "direction": 1, "scope": "broad"}],
    "弹药": [{"industries": ["军工装备", "军工电子", "化学制品"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "隐身": [{"industries": ["军工装备", "军工电子", "航天装备"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "国产航母": [{"industries": ["军工装备", "军工电子", "专用设备"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "地缘冲突": [{"industries": ["军工装备", "军工电子", "石油开采", "贵金属", "港口航运"], "weight": 0.65, "direction": 0, "scope": "broad"}],
    "局部冲突": [{"industries": ["军工装备", "军工电子", "石油开采"], "weight": 0.60, "direction": 0, "scope": "broad"}],
    "台海": [{"industries": ["军工装备", "军工电子", "半导体"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "军费": [{"industries": ["军工装备", "军工电子", "专用设备", "半导体"], "weight": 0.65, "direction": 1, "scope": "broad"}],
    "军演": [{"industries": ["军工装备", "军工电子", "贵金属", "港口航运"], "weight": 0.50, "direction": 0, "scope": "sector"}],

    # =========================================================================
    # 10. 贸易/外交 (利好4 + 利空7)
    # =========================================================================
    "关税": [{"industries": ["消费电子", "汽车整车", "专用设备", "纺织制造", "化学制品"], "weight": 0.85, "direction": -1, "scope": "broad"}],
    "贸易战": [{"industries": ["消费电子", "汽车整车", "专用设备", "纺织制造", "种植业与林业", "养殖业"], "weight": 0.80, "direction": -1, "scope": "broad"}],
    "一带一路": [{"industries": ["建筑装饰", "工程机械", "专用设备", "港口航运", "公路铁路运输", "油气开采及服务"], "weight": 0.65, "direction": 1, "scope": "broad"}],
    "反倾销": [{"industries": ["钢铁", "工业金属", "化学制品", "纺织制造"], "weight": 0.70, "direction": -1, "scope": "sector"}],
    "实体清单": [{"industries": ["半导体", "通信设备", "计算机设备", "消费电子"], "weight": 0.80, "direction": -1, "scope": "broad"}],
    "脱钩": [{"industries": ["消费电子", "半导体", "纺织制造", "专用设备"], "weight": 0.75, "direction": -1, "scope": "broad"}],
    "301调查": [{"industries": ["消费电子", "汽车整车", "半导体", "光电子"], "weight": 0.70, "direction": -1, "scope": "broad"}],
    "中美关系": [{"industries": ["消费电子", "半导体", "房地产"], "weight": 0.60, "direction": 0, "scope": "broad"}],
    "RCEP": [{"industries": ["港口航运", "物流", "纺织制造", "汽车整车"], "weight": 0.60, "direction": 1, "scope": "broad"}],
    "CPTPP": [{"industries": ["港口航运", "纺织制造", "农业"], "weight": 0.55, "direction": 1, "scope": "broad"}],
    "出口管制": [{"industries": ["半导体", "通信设备", "计算机设备", "专用设备"], "weight": 0.70, "direction": -1, "scope": "broad"}],

    # =========================================================================
    # 11. 环保/气候变化 (利好6 + 利空1)
    # =========================================================================
    "环保": [{"industries": ["环保设备", "环境治理"], "weight": 0.50, "direction": 1, "scope": "sector"}],
    "绿色发展": [{"industries": ["环保设备", "环境治理", "光伏设备", "风电设备", "化学制品"], "weight": 0.50, "direction": 1, "scope": "broad"}],
    "污染防治": [{"industries": ["环保设备", "环境治理", "化学制品"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "生物多样性": [{"industries": ["环境治理", "种植业与林业", "养殖业", "生物"], "weight": 0.40, "direction": 1, "scope": "broad"}],
    "限塑令": [{"industries": ["环保设备", "塑料制品", "造纸"], "weight": 0.55, "direction": 1, "scope": "sector"}],
    "碳交易": [{"industries": ["电力", "环保设备", "光伏设备", "化学制品"], "weight": 0.60, "direction": 1, "scope": "sector"}],
    "碳关税": [{"industries": ["钢铁", "工业金属", "化学制品", "环保设备"], "weight": 0.60, "direction": -1, "scope": "broad"}],

    # =========================================================================
    # 12. 会议/政策文件 (利好6 + 利空1)
    # =========================================================================
    "中央经济工作会议": [{"industries": ["银行", "证券", "建筑装饰", "房地产"], "weight": 0.80, "direction": 1, "scope": "broad"}],
    "政治局会议": [{"industries": ["银行", "证券", "建筑装饰"], "weight": 0.70, "direction": 0, "scope": "broad"}],
    "两会": [{"industries": ["证券", "银行", "建筑装饰"], "weight": 0.60, "direction": 0, "scope": "broad"}],
    "国常会": [{"industries": ["银行", "证券", "建筑装饰"], "weight": 0.65, "direction": 0, "scope": "broad"}],
    "政府工作报告": [{"industries": ["证券", "银行", "建筑装饰", "房地产", "汽车整车"], "weight": 0.75, "direction": 1, "scope": "broad"}],
    "五年规划": [{"industries": ["证券", "半导体", "光伏", "电池", "新能源汽车"], "weight": 0.75, "direction": 1, "scope": "broad"}],
    "稳增长": [{"industries": ["银行", "建筑装饰", "房地产", "建筑材料"], "weight": 0.70, "direction": 1, "scope": "broad"}],

    # =========================================================================
    # 13. 大宗商品/资源 (利好4 + 利空5)
    # =========================================================================
    "原油": [{"industries": ["油气开采及服务", "石油加工贸易", "化学制品", "化学纤维"], "weight": 0.65, "direction": 0, "scope": "broad"}],
    "OPEC": [{"industries": ["油气开采及服务", "石油加工贸易", "化学制品"], "weight": 0.60, "direction": 0, "scope": "broad"}],
    "锂": [{"industries": ["能源金属", "小金属", "电池"], "weight": 0.65, "direction": 1, "scope": "sector"}],
    "稀土": [{"industries": ["小金属", "金属新材料", "工业金属"], "weight": 0.70, "direction": 1, "scope": "sector"}],
    "钢铁": [{"industries": ["钢铁"], "weight": 0.60, "direction": 0, "scope": "sector"}],
    "限产": [{"industries": ["钢铁", "煤炭开采加工", "化学制品"], "weight": 0.60, "direction": 0, "scope": "sector"}],
    "煤价": [{"industries": ["煤炭开采加工", "电力", "化学制品"], "weight": 0.60, "direction": 0, "scope": "sector"}],
    "猪周期": [{"industries": ["养殖业", "农产品加工", "食品加工制造"], "weight": 0.55, "direction": 0, "scope": "sector"}],
    "猪肉": [{"industries": ["养殖业", "农产品加工", "食品加工制造"], "weight": 0.55, "direction": 0, "scope": "sector"}],

    # =========================================================================
    # 14. 利空/风险词汇 (新增19个利空词)
    # =========================================================================
    "立案": [{"industries": ["证券", "银行", "保险", "房地产"], "weight": 0.65, "direction": -1, "scope": "sector"}],
    "调查": [{"industries": [], "weight": 0.40, "direction": -1, "scope": "broad"}],
    "处罚": [{"industries": [], "weight": 0.55, "direction": -1, "scope": "broad"}],
    "退市": [{"industries": [], "weight": 0.70, "direction": -1, "scope": "broad"}],
    "ST": [{"industries": [], "weight": 0.70, "direction": -1, "scope": "broad"}],
    "违约": [{"industries": ["银行", "证券", "房地产"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "万亿地方债": [{"industries": ["银行", "建筑装饰"], "weight": 0.55, "direction": -1, "scope": "broad"}],
    "减持": [{"industries": [], "weight": 0.55, "direction": -1, "scope": "broad"}],
    "做空": [{"industries": ["证券"], "weight": 0.50, "direction": -1, "scope": "broad"}],
    "危机": [{"industries": [], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "暴跌": [{"industries": [], "weight": 0.70, "direction": -1, "scope": "broad"}],
    "熔断": [{"industries": ["证券"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "强平": [{"industries": [], "weight": 0.60, "direction": -1, "scope": "broad"}],
    "暂停上市": [{"industries": [], "weight": 0.70, "direction": -1, "scope": "broad"}],
    "财务造假": [{"industries": [], "weight": 0.70, "direction": -1, "scope": "broad"}],
    "下调评级": [{"industries": ["银行", "证券", "房地产"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "通胀": [{"industries": ["银行", "保险", "房地产", "食品加工制造"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "滞胀": [{"industries": ["银行", "保险", "证券", "零售"], "weight": 0.75, "direction": -1, "scope": "broad"}],
    "贬值": [{"industries": ["银行", "保险", "房地产"], "weight": 0.60, "direction": -1, "scope": "broad"}],
    "汇率波动": [{"industries": ["银行", "保险", "多元金融"], "weight": 0.50, "direction": -1, "scope": "broad"}],
    "债务上限": [{"industries": ["银行", "证券"], "weight": 0.55, "direction": -1, "scope": "broad"}],
    "全球衰退": [{"industries": ["消费电子", "银行", "保险", "汽车整车"], "weight": 0.75, "direction": -1, "scope": "broad"}],
    "疫情": [{"industries": ["旅游及酒店", "机场航运", "零售", "白酒", "食品加工制造"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "封控": [{"industries": ["旅游及酒店", "机场航运", "零售", "文化传媒"], "weight": 0.70, "direction": -1, "scope": "broad"}],
    "洪水": [{"industries": ["建筑装饰", "电力", "养殖业", "种植业与林业"], "weight": 0.55, "direction": -1, "scope": "broad"}],
    "地震": [{"industries": ["建筑装饰", "保险", "建筑材料"], "weight": 0.55, "direction": -1, "scope": "broad"}],
    "资本外流": [{"industries": ["银行", "证券", "保险", "房地产"], "weight": 0.65, "direction": -1, "scope": "broad"}],
    "做空中国": [{"industries": ["证券", "银行", "保险"], "weight": 0.75, "direction": -1, "scope": "broad"}],

    # =========================================================================
    # 15. 强度修饰词 (改为0.7~2.0范围)
    # =========================================================================
    "大力": [{"industries": [], "weight": 2.0, "direction": 0, "scope": "modifier"}],
    "坚决": [{"industries": [], "weight": 1.8, "direction": 0, "scope": "modifier"}],
    "严格": [{"industries": [], "weight": 1.5, "direction": 0, "scope": "modifier"}],
    "积极": [{"industries": [], "weight": 1.3, "direction": 0, "scope": "modifier"}],
    "审慎": [{"industries": [], "weight": 0.5, "direction": 0, "scope": "modifier"}],
    "有序": [{"industries": [], "weight": 0.7, "direction": 0, "scope": "modifier"}],
    "适时": [{"industries": [], "weight": 0.5, "direction": 0, "scope": "modifier"}],
    "稳妥": [{"industries": [], "weight": 0.6, "direction": 0, "scope": "modifier"}],
    "探索": [{"industries": [], "weight": 0.5, "direction": 0, "scope": "modifier"}],
    "试行": [{"industries": [], "weight": 0.5, "direction": 0, "scope": "modifier"}],
    "出台": [{"industries": [], "weight": 1.2, "direction": 0, "scope": "modifier"}],
    "印发": [{"industries": [], "weight": 1.2, "direction": 0, "scope": "modifier"}],
    "部署": [{"industries": [], "weight": 1.3, "direction": 0, "scope": "modifier"}],
}

# ─── 来源权威分 ───
SOURCE_AUTHORITY = {
    "国务院": 1.0,
    "中共中央": 1.0,
    "新闻联播": 0.95,
    "央行": 0.95,
    "财政部": 0.90,
    "发改委": 0.90,
    "证监会": 0.90,
    "银保监会": 0.85,
    "金管局": 0.85,
    "工信部": 0.80,
    "科技部": 0.75,
    "商务部": 0.80,
    "住建部": 0.80,
    "农业农村部": 0.70,
    "交通运输部": 0.70,
    "人社部": 0.65,
    "财联社": 0.60,
    "新华社": 0.60,
    "人民日报": 0.55,
    "证券时报": 0.45,
    "第一财经": 0.40,
    "default": 0.30,
}

# ─── 影响范围 ───
SCOPE_LEVEL = {
    "broad": 1.0,
    "sector": 0.6,
    "modifier": 0.2,
}


@dataclass
class DensityResult:
    """单文档的分析结果"""
    doc_id: Optional[int] = None
    title: str = ""
    direction: int = 0          # 1利好 -1利空 0中性
    macro_score: float = 0.0    # 宏观定调强度 0~1
    industry_scores: Dict[str, float] = field(default_factory=dict)  # {行业: 势能值}
    importance_source: float = 0.0
    importance_scope: float = 0.0
    importance_level: float = 0.0
    importance_total: float = 0.0
    keyword_hits: Dict[str, float] = field(default_factory=dict)  # {关键词: 权重贡献}
    version: str = "default"
    config: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG))


class KeywordDensityEngine:
    """关键词密度计算引擎"""

    def __init__(self, config: Optional[Dict] = None, keyword_map: Optional[Dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.keyword_map = {**DEFAULT_KEYWORD_MAP, **(keyword_map or {})}

        # 加载jieba分词
        import jieba
        self.jieba = jieba

        # 预编译关键词正则（按词长降序，优先匹配长词）
        keywords = sorted(self.keyword_map.keys(), key=len, reverse=True)
        # 对含特殊字符的关键词做转义
        escaped = [re.escape(k) for k in keywords]
        self.keyword_pattern = re.compile('|'.join(escaped))

        # 预加载行业映射逆索引: 行业 → [(关键词, 原始weight, direction)]
        self.industry_keywords: Dict[str, List[Tuple[str, float, int]]] = {}
        for kw, entries in self.keyword_map.items():
            for entry in entries:
                if entry['scope'] == 'modifier':
                    continue
                for ind in entry['industries']:
                    if ind not in self.industry_keywords:
                        self.industry_keywords[ind] = []
                    self.industry_keywords[ind].append(
                        (kw, entry['weight'], entry['direction'])
                    )

        # 统计
        self._keyword_count = len(self.keyword_map)
        self._industry_count = len(THS_INDUS)
        log.info(f"引擎初始化: {self._keyword_count}关键词, {self._industry_count}行业")

    def analyze_text(self, text: str, title: str = "",
                     source: str = "", doc_id: Optional[int] = None) -> DensityResult:
        """分析单篇文档，返回完整密度结构"""
        result = DensityResult(doc_id=doc_id)
        result.title = title

        # 1) 分词
        words = list(self.jieba.cut(text))
        word_set = set(words)
        text_lower = text.lower()

        # 2) 关键词匹配（正则先过，再用分词补漏）
        matched_kw = set()
        for m in self.keyword_pattern.finditer(text):
            matched_kw.add(m.group())
        # 分词层面也对一遍
        for w in words:
            if w in self.keyword_map:
                matched_kw.add(w)

        # 过滤BLOCKWORDS
        matched_kw = {k for k in matched_kw if k not in BLOCKWORDS}

        if not matched_kw:
            log.debug(f"无匹配关键词: {title[:60]}")
            return result

        # 3) 分离修饰词与非修饰词
        modifiers = {}
        content_kw = set()
        for kw in matched_kw:
            entries = self.keyword_map.get(kw, [])
            is_mod = any(e['scope'] == 'modifier' for e in entries)
            if is_mod:
                # 修饰词取weight最大值
                w = max(e['weight'] for e in entries if e['scope'] == 'modifier')
                modifiers[kw] = w
            else:
                content_kw.add(kw)
        
        # 如果没有内容关键词，返回空
        if not content_kw:
            log.debug(f"仅有修饰词: {title[:60]}")
            return result
        
        # 计算修饰词总乘数：取所有修饰词的几何平均值
        if modifiers:
            mod_product = 1.0
            for w in modifiers.values():
                mod_product *= w
            # 用几何平均的平方根避免过度缩放
            modifier_multiplier = mod_product ** (1.0 / len(modifiers))
        else:
            modifier_multiplier = 1.0
        
        # 4) 计算关键词贡献（仅内容关键词）
        direction_sum = 0.0
        macro_sum = 0.0
        industry_scores: Dict[str, float] = {}
        keyword_hits: Dict[str, float] = {}

        for kw in content_kw:
            entries = self.keyword_map.get(kw, [])
            for entry in entries:
                if entry['scope'] == 'modifier':
                    continue  # 跳过（已处理）
                w = entry['weight']
                d = entry['direction']
                keyword_hits[kw] = keyword_hits.get(kw, 0.0) + w

                direction_sum += d * w
                macro_sum += abs(d) * w

                for ind in entry['industries']:
                    industry_scores[ind] = industry_scores.get(ind, 0.0) + w
        
        # 修饰词倍率应用到行业势能和关键词贡献
        if modifier_multiplier != 1.0:
            for ind in industry_scores:
                industry_scores[ind] *= modifier_multiplier
            keyword_hits = {k: v * modifier_multiplier for k, v in keyword_hits.items()}
            macro_sum *= modifier_multiplier
            direction_sum *= modifier_multiplier
        
        # 5) 计算宏观方向
        if macro_sum > 0:
            result.direction = 1 if direction_sum > 0 else (-1 if direction_sum < 0 else 0)
            result.macro_score = min(1.0, macro_sum / max(len(content_kw), 1))
            # 修饰词影响纳入最终分数
            if modifiers:
                # 有修饰词的信号，macro_score用modifier_multiplier做最后微调
                result.macro_score = min(1.0, result.macro_score * modifier_multiplier)
        else:
            result.direction = 0
            result.macro_score = 0.0

        # 6) 行业势能归一化
        total_weight = sum(industry_scores.values())
        if total_weight > 0:
            result.industry_scores = {k: v / total_weight * 10 for k, v in industry_scores.items()}
        else:
            result.industry_scores = {}

        result.keyword_hits = keyword_hits

        # 7) 重要性评分
        result.importance_source = self._calc_source_authority(source)
        result.importance_scope = self._calc_scope_score(matched_kw)
        result.importance_level = self._calc_level_score(title, text)
        w_auth = self.config.get('w_authority', 0.30)
        w_scope = self.config.get('w_scope', 0.40)
        w_level = self.config.get('w_level', 0.30)
        result.importance_total = (
            w_auth * result.importance_source +
            w_scope * result.importance_scope +
            w_level * result.importance_level
        )

        result.config = dict(self.config)
        return result

    def calc_residual_potential(self, result: DensityResult, t_days: float) -> Dict[str, float]:
        """计算t天后各行业的剩余信息势能（残存值）
        
        信息势能随时间衰减，残存值 = E × e^{-λt}
        
        Args:
            result: 密度分析结果
            t_days: 距事件日天数（t>0）
        
        Returns:
            {行业: 残存势能}
        """
        if t_days <= 0:
            return dict(result.industry_scores)

        lam = self.config['lambda']
        model = self.config.get('decay_model', 'exponential')

        if model == 'exponential':
            decay = np.exp(-lam * t_days)
        elif model == 'linear':
            decay = max(0.0, 1.0 - lam * t_days)
        elif model == 'power_law':
            decay = 1.0 / (1.0 + lam * t_days)
        else:
            decay = np.exp(-lam * t_days)

        return {k: v * decay for k, v in result.industry_scores.items()}

    def calc_cumulative_impact(self, result: DensityResult, t_days: float) -> Dict[str, float]:
        """计算到t天为止的累积信息冲击（用于拟合CAR）
        
        累积冲击 = E × (1 - e^{-λt})
        这个值从0单调增长到E, 对应事件后市场价格逐步消化的过程
        """
        if t_days <= 0:
            return {k: 0.0 for k in result.industry_scores}

        lam = self.config['lambda']
        model = self.config.get('decay_model', 'exponential')

        if model == 'exponential':
            cum = 1.0 - np.exp(-lam * t_days)
        elif model == 'linear':
            cum = min(1.0, lam * t_days)
        elif model == 'power_law':
            cum = 1.0 - (1.0 / (1.0 + lam * t_days))
        else:
            cum = 1.0 - np.exp(-lam * t_days)

        return {k: v * cum for k, v in result.industry_scores.items()}

    def _calc_source_authority(self, source: str) -> float:
        for key, val in SOURCE_AUTHORITY.items():
            if key in source:
                return val
        return SOURCE_AUTHORITY["default"]

    def _calc_scope_score(self, matched_kw: set) -> float:
        """基于匹配词的影响范围计算范围评分"""
        scopes = set()
        for kw in matched_kw:
            entries = self.keyword_map.get(kw, [])
            for e in entries:
                scopes.add(e['scope'])
        max_level = 0.2
        for s in scopes:
            lvl = SCOPE_LEVEL.get(s, 0.0)
            if lvl > max_level:
                max_level = lvl
        return max_level

    def _calc_level_score(self, title: str, text: str) -> float:
        """基于标题和文本判断政策层级"""
        text_combined = title + "\n" + text[:1000]
        if any(kw in text_combined for kw in ["中共中央", "国务院", "习近平", "李克强"]):
            return 1.0
        if any(kw in text_combined for kw in ["国务院批准", "国常会", "中办国办"]):
            return 0.95
        if any(kw in text_combined for kw in ["部委", "发改委", "工信部", "央行", "财政部"]):
            return 0.80
        if any(kw in text_combined for kw in ["省", "市", "地方"]):
            return 0.50
        return 0.30

    def get_config_summary(self) -> dict:
        return {
            "keyword_count": self._keyword_count,
            "industry_count": self._industry_count,
            "config": self.config,
            "source_authority_map": {k: v for k, v in SOURCE_AUTHORITY.items()},
        }


# ═══════════════════════════════════════════
#   CLI + DB 交互
# ═══════════════════════════════════════════

def cli_analyze(engine, args):
    """命令行模式：分析输入文本"""
    text = args.doc
    if not text and args.file:
        text = Path(args.file).read_text(encoding='utf-8')

    if not text:
        print("请提供 --doc 文本或 --file 文件路径")
        return

    result = engine.analyze_text(
        text=text,
        title=args.title or "",
        source=args.source or "",
    )

    print(f"\n{'='*60}")
    print(f"关键词密度分析结果")
    print(f"{'='*60}")
    print(f"宏观方向: {'📈利好' if result.direction > 0 else '📉利空' if result.direction < 0 else '➖中性'} (score={result.macro_score:.3f})")
    print(f"重要性: 来源={result.importance_source:.2f} 范围={result.importance_scope:.2f} 层级={result.importance_level:.2f} 总分={result.importance_total:.2f}")
    print(f"\n命中关键词 ({len(result.keyword_hits)}个):")
    for kw, w in sorted(result.keyword_hits.items(), key=lambda x: -x[1]):
        print(f"  [{kw}] weight={w:.3f}")
    print(f"\n行业势能向量:")
    for ind, score in sorted(result.industry_scores.items(), key=lambda x: -x[1]):
        print(f"  {ind}: {score:.3f}")
    print(f"\n残存势能 ({engine.config['decay_model']}, λ={engine.config['lambda']}):")
    for t in [0, 1, 3, 5, 10, 20]:
        potentials = engine.calc_residual_potential(result, t)
        if potentials:
            top = max(potentials, key=potentials.get)
            print(f"  t+{t:2d}日: {top} 残存势能={potentials[top]:.3f} (原始{max(result.industry_scores.values()):.3f})")
    print(f"\n累积冲击 (用于拟合CAR):")
    for t in [1, 3, 5, 10, 20]:
        cum = engine.calc_cumulative_impact(result, t)
        if cum:
            top = max(cum, key=cum.get)
            print(f"  t+{t:2d}日 累积: {top}({cum[top]:.3f})")


async def db_process(engine, args):
    """数据库模式"""
    import asyncpg

    conn = await asyncpg.connect('postgresql://guludan:toto@192.168.3.14:5432/guludan')

    if args.list_events:
        rows = await conn.fetch("""
            SELECT event_id, event_date, source, title
            FROM event_list
            WHERE event_id NOT IN (SELECT event_id FROM nlp_event_scores)
            ORDER BY event_date DESC
            LIMIT 50
        """)
        print(f"\n待解析事件 ({len(rows)}条):")
        for r in rows:
            print(f"  #{r['event_id']} [{r['event_date']}] {r['source']}: {r['title'][:80]}")

    elif args.process_all:
        rows = await conn.fetch("""
            SELECT event_id, title, full_text, source
            FROM event_list
            WHERE event_id NOT IN (SELECT event_id FROM nlp_event_scores)
              AND full_text IS NOT NULL
            ORDER BY event_id
        """)
        print(f"处理 {len(rows)} 个事件...")
        count = 0
        for r in rows:
            result = engine.analyze_text(
                text=r['full_text'],
                title=r['title'],
                source=r['source'],
                doc_id=r['event_id']
            )
            # 写入 nlp_event_scores
            await conn.execute("""
                INSERT INTO nlp_event_scores
                    (event_id, direction, macro_score, industry_json,
                     importance_source, importance_scope, importance_level,
                     importance_total, keyword_weights_json, version)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (event_id) DO UPDATE SET
                    direction=EXCLUDED.direction,
                    macro_score=EXCLUDED.macro_score,
                    industry_json=EXCLUDED.industry_json,
                    importance_source=EXCLUDED.importance_source,
                    importance_scope=EXCLUDED.importance_scope,
                    importance_level=EXCLUDED.importance_level,
                    importance_total=EXCLUDED.importance_total,
                    keyword_weights_json=EXCLUDED.keyword_weights_json,
                    updated_at=NOW()
            """,
                result.doc_id, result.direction, result.macro_score,
                json.dumps(result.industry_scores, ensure_ascii=False),
                result.importance_source, result.importance_scope,
                result.importance_level, result.importance_total,
                json.dumps(result.keyword_hits, ensure_ascii=False),
                result.version
            )
            count += 1
            if count % 50 == 0:
                log.info(f"  已处理 {count}/{len(rows)}")

        print(f"✅ 完成: 解析 {count} 个事件")

    await conn.close()


def main():
    parser = argparse.ArgumentParser(description="关键词密度引擎")
    parser.add_argument('--doc', type=str, help='待分析文本')
    parser.add_argument('--file', type=str, help='待分析文件路径')
    parser.add_argument('--title', type=str, default='', help='文档标题')
    parser.add_argument('--source', type=str, default='', help='来源（如国务院/央行）')
    parser.add_argument('--verbose', action='store_true', help='详细日志')
    parser.add_argument('--config', type=str, help='JSON配置文件路径')

    # DB mode
    parser.add_argument('--db', action='store_true', help='数据库模式')
    parser.add_argument('--list-events', action='store_true', help='列出未解析事件')
    parser.add_argument('--process-all', action='store_true', help='批量处理所有未解析事件')

    # Config dump
    parser.add_argument('--show-config', action='store_true', help='显示当前配置')

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 加载外部配置
    config_override = {}
    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config_override = json.load(f)

    engine = KeywordDensityEngine(config=config_override)

    if args.show_config:
        c = engine.get_config_summary()
        print(json.dumps(c, ensure_ascii=False, indent=2))
        return

    if args.db:
        import asyncio
        asyncio.run(db_process(engine, args))
    else:
        cli_analyze(engine, args)


if __name__ == '__main__':
    main()
