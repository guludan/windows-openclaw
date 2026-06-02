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
policy_injector.py — 政策底门控引擎

职责: 基于新闻联播文本的政策底事件识别，对 S3 恐慌信号做注入门控。
不依赖任何预计算的存储，所有词库匹配实时执行。

用法:
    from policy_injector import PolicyInjector
    
    pi = PolicyInjector(host='192.168.3.14', dbname='guludan', user='guludan', password='toto')
    
    # 检查某日是否有政策底信号
    has = pi.has_backstop(date(2024, 1, 22), lookback=5)
    
    # 调整注入系数
    s_b, u_b = pi.adjust_injection('S3', 0.15, 0.05, date(2024, 1, 22))
    
    # 批量回测
    stats = pi.backtest(print_table=True)
"""
import psycopg2, psycopg2.extras
import numpy as np
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, List

# ===== 词库（与 event_classifier_minimal.py 保持一致）=====
SYSTEMIC_STABILITY = [
    "资本市场运行", "听取资本市场", "研究资本市场",
    "资本市场改革发展", "资本市场稳定", "资本市场健康发展",
    "稳定资本市场", "维护资本市场",
    "降准", "降息", "存款准备金率", "逆回购",
    "中期借贷便利", "MLF", "再贷款", "再贴现",
    "释放长期资金", "流动性合理充裕",
    "公开市场操作",
    "系统性金融", "金融风险防范", "防范化解金融风险",
    "防范化解重大风险", "金融安全", "金融稳定",
    "守住不发生系统性",
    "汇金", "社保基金", "保险资金入市",
    "保险资金投资", "年金基金", "养老金入市",
    "长期资金", "权益投资",
    "注册制", "上市公司质量", "退市制度",
    "分红", "回购", "市值管理",
    "央企市值", "增持", "股份回购",
    "股票质押", "融资融券", "纾困基金", "纾困",
    "流动性支持",
    "逆周期调节", "跨周期", "宏观政策",
    "积极财政", "专项债",
    "房地产金融", "地方债务化解",
    "一揽子化债", "债务风险",
]

ROUTINE_SUPPORT = [
    "稳中求进", "高质量发展", "稳增长",
    "减税降费", "助企纾困", "退减缓",
    "扩大内需", "提振消费", "扩内需",
    "改革开放", "营商环境", "市场主体活力",
    "平稳运行", "优化结构", "供给侧结构性改革",
    "稳预期", "保就业", "保民生", "六稳", "六保",
    "稳字当头", "稳步推进", "平稳健康",
    "稳经济", "稳物价", "稳外资", "稳外贸",
    "经济运行", "持续恢复", "企稳回升",
    "回升向好", "增长潜力",
    "政策性金融", "普惠金融",
    "小微企业", "制造业", "科技创新",
    "新质生产力", "现代化产业体系",
    "支持实体经济", "金融服务",
    "金融支持", "融资支持", "信贷支持",
    "实体经济", "中小微企业",
]

RISK_WORDS = [
    "风险暴露", "违约", "不良贷款", "坏账",
    "信用风险", "流动性风险", "市场风险",
    "风险事件", "风险防控",
    "监管收紧", "压降", "清理整顿",
    "退市", "ST", "风险警示",
    "地方债务", "房地产风险",
    "影子银行", "金融去杠杆",
    "风险排查", "专项整治",
    "加强监管", "引导规范",
]

BEST_THR = 0.15


class PolicyInjector:
    """政策底门控引擎"""

    def __init__(self, host: str = "192.168.3.14", dbname: str = "guludan",
                 user: str = "guludan", password: str = "toto"):
        self.conn = psycopg2.connect(host=host, dbname=dbname, user=user, password=password)
        self._cached_cctv: Optional[dict] = None  # date -> events list

    def close(self):
        self.conn.close()

    # ----- 词库匹配 -----

    def _score_text(self, text: str, title: str = "") -> dict:
        """同 event_classifier_minimal.score_text"""
        combined = ((title or "") + " " + (text or "")).lower()
        text_len = max(len(combined), 1)
        sqrt_len = np.sqrt(text_len)

        sys_count = sum(1 for kw in SYSTEMIC_STABILITY if kw.lower() in combined)
        sys_score = (sys_count / sqrt_len) * 2.0

        routine_count = sum(1 for kw in ROUTINE_SUPPORT if kw.lower() in combined)
        risk_count = sum(1 for kw in RISK_WORDS if kw.lower() in combined)

        routine_score = (routine_count / sqrt_len) * 0.5
        risk_score = (risk_count / sqrt_len) * (-1.0)
        net_score = sys_score - routine_score * 0.3 + risk_score * 0.5

        return {
            "sys_count": sys_count,
            "sys_score": round(sys_score, 4),
            "net_score": round(net_score, 4),
            "routine_count": routine_count,
            "risk_count": risk_count,
        }

    def _load_cctv(self, start_date: date, end_date: date) -> dict:
        """加载日期范围内的 CCTV 数据并评分"""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT event_id, event_date, title, full_text
            FROM event_list
            WHERE source = 'news_cctv' AND event_date >= %s AND event_date <= %s
              AND full_text IS NOT NULL AND LENGTH(full_text) > 20
            ORDER BY event_date
        """, (start_date, end_date))
        rows = cur.fetchall()
        cur.close()

        by_date = {}
        for row in rows:
            d = row[1] if isinstance(row[1], date) else row[1].date() if hasattr(row[1], "date") else row[1]
            sc = self._score_text(row[3], row[2])
            if d not in by_date:
                by_date[d] = []
            by_date[d].append(sc)
        return by_date

    # ----- 核心门控 -----

    def has_backstop(self, signal_date: date, lookback: int = 5) -> Tuple[bool, float, int]:
        """
        检查信号日期前 N 个交易日内是否有政策底事件。

        策略：使用三档置信度，而不是简单的二分。
        因为 CCTV 文本不一定能覆盖所有政策底（如汇金公告发在其它渠道），
        低置信度只做部分抑制而非完全阻断。

        Returns:
            (has_policy_bottom, max_net_score, n_sys_events)
        """
        # 日历窗：5个交易日 ≈ 7个日历日，留 2 天余量
        window_days = int(lookback * 1.4) + 3
        start = signal_date - timedelta(days=window_days)

        events = self._load_cctv(start, signal_date)

        # 计算三档：
        # Level 1 (明确系统性)：max_net > 0.20  — 有真正的高分政策底事件
        # Level 2 (模糊底)：    max_net > 0.10  — 有一些偏政策的关键词
        # Level 3 (完全无底)：  默认
        max_net = 0.0
        n_sys = 0
        n_high_sys = 0  # sys_count >= 2 的事件数
        for d in sorted(events.keys()):
            for ev in events[d]:
                max_net = max(max_net, ev["net_score"])
                if ev["sys_count"] > 0:
                    n_sys += ev["sys_count"]
                if ev["sys_count"] >= 2:
                    n_high_sys += 1

        # 判定层级
        if max_net > 0.20 or n_high_sys >= 3:
            level = 2  # 明确政策底
        elif max_net > 0.10 or (n_sys >= 3 and max_net > 0.05):
            level = 1  # 模糊底
        else:
            level = 0  # 无底

        return level, max_net, n_sys

    def adjust_injection(self, signal_type: str, s_b_orig: float, u_b_orig: float,
                         signal_date: date, lookback: int = 5) -> Tuple[float, float]:
        """
        根据政策底门控调整注入系数。

        S3 三级门控：
          Level 2 (明确政策底) → 正常注入 (1.0x)
          Level 1 (模糊底)     → 部分抑制 (0.5x)
          Level 0 (无底)       → 完全抑制 (0.0x)

        S1/S2: 维持不变（预留接口）
        """
        if signal_type.upper() != "S3":
            return s_b_orig, u_b_orig

        level, max_net, n_sys = self.has_backstop(signal_date, lookback)

        if level == 2:
            # 明确政策底：正常注入
            return s_b_orig, u_b_orig
        elif level == 1:
            # 模糊底：减半注入
            return s_b_orig * 0.5, u_b_orig * 0.5
        else:
            # 无底：完全抑制
            return 0.0, 0.0

    # ----- 回测验证 -----

    def backtest(self, print_table: bool = True) -> dict:
        """
        在全部 S3 信号上验证 policy_injector 效果。
        """
        cur = self.conn.cursor()

        cur.execute("""
            WITH s3_raw AS (
                SELECT level, date
                FROM pool_energy
                WHERE level LIKE 'code:%%' AND date >= '2022-01-01'
                  AND (extra->>'sigs') LIKE '%%s3%%'
            ),
            s3_prices AS (
                SELECT s.level, s.date,
                       p.close as c0,
                       p2.close as c20
                FROM s3_raw s
                JOIN daily_prices p ON p.symbol = SUBSTRING(s.level FROM 6) AND p.trade_date = s.date
                JOIN LATERAL (
                    SELECT p2.close FROM daily_prices p2
                    WHERE p2.symbol = SUBSTRING(s.level FROM 6) AND p2.trade_date > s.date
                    ORDER BY p2.trade_date
                    OFFSET 19 LIMIT 1
                ) p2 ON true
                WHERE p.close > 0 AND p2.close > 0
            )
            SELECT level, date, c0, c20
            FROM s3_prices
            ORDER BY date
        """)
        rows = cur.fetchall()
        cur.close()

        if len(rows) == 0:
            print("[policy_injector] ❌ 无 S3 信号数据")
            return {}

        records = []
        for row in rows:
            d = row[1].date() if hasattr(row[1], "date") else row[1]
            ret20 = row[3] / row[2] - 1 if row[2] > 0 else 0
            records.append({
                "date": d,
                "ret_20": ret20,
                "period": "train" if d <= date(2023, 12, 31) else "test",
            })

        # 2) 逐日期缓存
        unique_dates = sorted(set(r["date"] for r in records))
        date_cache = {}
        for d in unique_dates:
            date_cache[d] = self.has_backstop(d, lookback=5)

        for r in records:
            lvl, mn, ns = date_cache[r["date"]]
            r["level"] = lvl
            r["max_net"] = mn
            r["n_sys"] = ns

        # 3) 统计
        total = len(records)
        for period_name, lbl in [("train", "2022~2023"), ("test", "2024~2026")]:
            sub = [r for r in records if r["period"] == period_name]
            if len(sub) < 3:
                continue

            n_total = len(sub)

            if print_table:
                print(f"\n  [{lbl}] S3 = {n_total} 条")
                print(f"  {'Level':<12} {'n':<8} {'T+20均值':<14} {'胜率':<10}")
                print(f"  {'-'*44}")

                for lvl, lname in [(2, 'L2 明确底'), (1, 'L1 模糊底'), (0, 'L0 无底'), (-1, '全部')]:
                    if lvl == -1:
                        items = sub
                    else:
                        items = [r for r in sub if r["level"] == lvl]
                    if len(items) < 3:
                        continue
                    rets = [r["ret_20"] for r in items]
                    print(f"  {lname:<12} {len(items):<8} {np.mean(rets):+9.2%}    {np.mean(np.array(rets) > 0):<7.1%}")

                # 三级门控后组合
                gated = []
                for r in sub:
                    if r["level"] == 2:
                        gated.append(r["ret_20"] * 1.0)
                    elif r["level"] == 1:
                        gated.append(r["ret_20"] * 0.5)
                    else:
                        gated.append(0.0)
                gm = np.mean(gated)
                gw = np.mean(np.array(gated) > 0)
                orig = [r["ret_20"] for r in sub]
                print(f"  {'门控后 (L2全/L1半)':<12} {n_total:<8} {gm:+9.2%}    {gw:<7.1%}")
                print(f"  {'原始组合':<12} {n_total:<8} {np.mean(orig):+9.2%}    {np.mean(np.array(orig) > 0):<7.1%}")

        return {"n_total": total}


if __name__ == "__main__":
    print("=" * 60)
    print(f"[policy_injector] 政策底门控引擎")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    pi = PolicyInjector()
    print(f"\n词库: {len(SYSTEMIC_STABILITY)} 个系统性托底词")
    print(f"阈值: net_score > {BEST_THR} 或 sys_count >= 2")
    print(f"门控规则: S3 + 有政策底 → 正常注入, 否则 → 完全抑制")

    # 测试几个关键日期
    print(f"\n{'=' * 60}")
    print("门控测试")
    print(f"{'=' * 60}")

    test_dates = [
        (date(2022, 3, 16), "2022-03-16 金融委会议"),
        (date(2022, 4, 26), "2022-04-26 深跌+财经委"),
        (date(2022, 10, 28), "2022-10-28 深跌无托底"),
        (date(2023, 8, 28), "2023-08-28 印花税减半"),
        (date(2024, 1, 22), "2024-01-22 国常会资本市场"),
        (date(2024, 2, 5), "2024-02-05 雪球敲入"),
        (date(2024, 2, 6), "2024-02-06 汇金增持"),
        (date(2024, 10, 18), "2024-10-18 924行情"),
    ]

    for d, desc in test_dates:
        has, max_net, n_sys = pi.has_backstop(d, lookback=5)
        orig = (0.15, 0.05)
        adj = pi.adjust_injection("S3", *orig, d)
        print(f"\n  {desc}:")
        print(f"    has_backstop={has}, max_net={max_net:.3f}, n_sys={n_sys}")
        print(f"    注入系数: ({orig[0]:.2f}, {orig[1]:.2f}) → ({adj[0]:.2f}, {adj[1]:.2f})")

    # 全量回测
    print(f"\n{'=' * 60}")
    print("全量回测")
    print(f"{'=' * 60}")
    pi.backtest(print_table=True)
    pi.close()
    print(f"\n✅ 完成")
