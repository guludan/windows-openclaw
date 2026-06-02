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
事件细粒度分级器 v2 — CCTV 语境适配版
修正：词库贴合新闻联播实际用语，SQL 收益查询修复
"""
import psycopg2, psycopg2.extras
import numpy as np
import pandas as pd
import time
from datetime import datetime, date, timedelta

OUTPUT = "/home/tearsyu/.openclaw/workspace/memory/reports/event_classifier_2026-05-25.md"
DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

print("=" * 60)
print("[事件分级器 v2]", datetime.now().strftime("%H:%M"))
print("=" * 60)

# ===== 词库（CCTV语境适配）=====
SYSTEMIC_STABILITY = [
    # 资本市场直接干预
    "资本市场运行", "听取资本市场", "研究资本市场",
    "资本市场改革发展", "资本市场稳定", "资本市场健康发展",
    "稳定资本市场", "维护资本市场",
    # 货币政策/流动性
    "降准", "降息", "存款准备金率", "逆回购",
    "中期借贷便利", "MLF", "再贷款", "再贴现",
    "释放长期资金", "流动性合理充裕",
    "公开市场操作",
    # 金融系统性
    "系统性金融", "金融风险防范", "防范化解金融风险",
    "防范化解重大风险", "金融安全", "金融稳定",
    "守住不发生系统性",
    # 汇金/国家队
    "汇金", "社保基金", "保险资金入市",
    "保险资金投资", "年金基金", "养老金入市",
    "长期资金", "权益投资",
    # 制度改革
    "注册制", "上市公司质量", "退市制度",
    "分红", "回购", "市值管理",
    "央企市值", "增持", "股份回购",
    # 融资质押
    "股票质押", "融资融券", "纾困基金", "纾困",
    "流动性支持",
    # 宏观逆周期
    "逆周期调节", "跨周期", "宏观政策",
    "积极财政", "专项债",
    # 房地产/地方债
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


def score_text(text, title=""):
    """对单条文本打分"""
    combined = ((title or "") + " " + (text or "")).lower()
    text_len = max(len(combined), 1)
    sqrt_len = np.sqrt(text_len)

    sys_count = sum(1 for kw in SYSTEMIC_STABILITY if kw.lower() in combined)
    routine_count = sum(1 for kw in ROUTINE_SUPPORT if kw.lower() in combined)
    risk_count = sum(1 for kw in RISK_WORDS if kw.lower() in combined)

    sys_score = (sys_count / sqrt_len) * 2.0
    routine_score = (routine_count / sqrt_len) * 0.5
    risk_score = (risk_count / sqrt_len) * (-1.0)

    net_score = sys_score - routine_score * 0.3 + risk_score * 0.5
    return {"sys_count": sys_count, "sys_score": sys_score, "routine_count": routine_count,
            "risk_count": risk_count, "net_score": net_score}


conn = psycopg2.connect(DB)

# ===== 1) 加载全部 CCTV 文本 =====
print("\n[1] 加载文本...")
cur = conn.cursor()
cur.execute("""
    SELECT event_id, event_date, title, full_text, category, direction
    FROM event_list
    WHERE source = 'news_cctv' AND event_date >= '2022-01-01'
      AND full_text IS NOT NULL AND LENGTH(full_text) > 20
    ORDER BY event_date
""")
rows = cur.fetchall()
cur.close()
print(f"  {len(rows)} 条")

df_txt = pd.DataFrame(rows, columns=["event_id", "event_date", "title", "full_text", "category", "direction"])
df_txt["event_date"] = pd.to_datetime(df_txt["event_date"]).apply(lambda x: x.date())

# ===== 2) 打分 =====
print("\n[2] 打分...")
scores = []
for _, r in df_txt.iterrows():
    sc = score_text(r["full_text"], r["title"])
    sc["event_id"] = r["event_id"]
    sc["event_date"] = r["event_date"]
    sc["direction"] = r["direction"]
    scores.append(sc)
df_sc = pd.DataFrame(scores)
print(f"  完成 {len(df_sc)} 条")
print(f"  匹配到 sys 词: {(df_sc['sys_count']>0).sum()} 条 ({(df_sc['sys_count']>0).mean():.0%})")

# ===== 3) 评分分布 =====
print(f"\n{'=' * 60}")
print("评分分布")
print(f"{'=' * 60}")

for period_name, start, end in [
    ("2022~2023", date(2022, 1, 1), date(2023, 12, 31)),
    ("2024 Q1", date(2024, 1, 1), date(2024, 3, 1)),
    ("2024全年", date(2024, 1, 1), date(2024, 12, 31)),
]:
    sub = df_sc[(df_sc["event_date"] >= start) & (df_sc["event_date"] <= end)]
    if len(sub) == 0:
        continue
    print(f"\n  [{period_name}] {len(sub)} 条:")
    print(f"    sys_count>0: {(sub['sys_count']>0).sum()}条 ({(sub['sys_count']>0).mean():.0%})")
    print(f"    sys_score: P50={sub['sys_score'].median():.3f} P90={sub['sys_score'].quantile(0.9):.3f} Max={sub['sys_score'].max():.3f}")
    print(f"    net_score: P50={sub['net_score'].median():.3f} P90={sub['net_score'].quantile(0.9):.3f} Max={sub['net_score'].max():.3f}")
    top5 = sub.nlargest(5, "net_score")
    for _, r in top5.iterrows():
        t = (str(r.get("title", "")) or "")[:60]
        print(f"      [{r['event_date']}] net={r['net_score']:.2f} sys={r['sys_score']:.2f} — {t}")

# ===== 4) 阈值选择 =====
print(f"\n{'=' * 60}")
print("阈值筛选")
print(f"{'=' * 60}")

daily_max = df_sc.groupby("event_date")["net_score"].max().reset_index()
daily_max.columns = ["event_date", "max_net"]
daily_high = df_sc.groupby("event_date")["sys_count"].agg(lambda x: (x > 0).sum()).reset_index()
daily_high.columns = ["event_date", "n_sys"]
daily = daily_max.merge(daily_high, on="event_date")

for thr in [x * 0.05 for x in range(1, 16)]:
    daily["hb"] = daily["max_net"] > thr
    train = daily[(daily["event_date"] >= date(2022, 1, 1)) & (daily["event_date"] <= date(2023, 12, 31))]
    test = daily[(daily["event_date"] >= date(2024, 1, 1)) & (daily["event_date"] <= date(2024, 3, 1))]
    tr = train["hb"].mean() if len(train) > 0 else 0
    te = test["hb"].mean() if len(test) > 0 else 0
    print(f"  thr={thr:.2f}:  2022~2023={tr:.0%}  2024Q1={te:.0%}")

BEST_THR = 0.15
daily["policy_bottom"] = (daily["max_net"] > BEST_THR) | (daily["n_sys"] >= 2)

# ===== 5) S3 日期 =====
print(f"\n{'=' * 60}")
print("[5] S3 + 收益关联 (新SQL)")
print(f"{'=' * 60}")

# 更健壮的 S3 收益查询
cur = conn.cursor()
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
print(f"  {len(rows)} 条 S3 信号有完整收益")

if len(rows) == 0:
    print("  ❌ SQL 返回 0 条！检查 SUBSTRING 逻辑")
    # debug
    print("  检查单个信号:")
    cur = conn.cursor()
    cur.execute("SELECT level, date FROM pool_energy WHERE level LIKE 'code:%%' AND extra->>'sigs' LIKE '%%s3%%' LIMIT 3")
    for r in cur.fetchall():
        print(f"    level={r[0]}, 子串={r[0][5:]}")
    cur.close()
    conn.close()
    exit()

df_s3 = pd.DataFrame(rows, columns=["level", "date", "c0", "c20"])
df_s3["ret_20"] = df_s3["c20"] / df_s3["c0"] - 1
df_s3["date_d"] = df_s3["date"].apply(lambda x: x.date() if hasattr(x, "date") else x)
df_s3["period"] = df_s3["date_d"].apply(lambda x: "train" if x <= date(2023, 12, 31) else "test")

# 政策底关联
def get_before(d, daily_df, days=7):
    s = d - timedelta(days=days)
    w = daily_df[(daily_df["event_date"] > s) & (daily_df["event_date"] <= d)]
    if len(w) == 0:
        return False, 0, 0.0
    return w["policy_bottom"].any(), w["policy_bottom"].sum(), w["max_net"].max()

policy_map = {}
for d in df_s3["date_d"].unique():
    policy_map[d] = get_before(d, daily)

df_s3["has_policy"] = df_s3["date_d"].map(lambda d: policy_map.get(d, (False, 0, 0))[0])
df_s3["n_policy_days"] = df_s3["date_d"].map(lambda d: policy_map.get(d, (False, 0, 0))[1])
df_s3["max_net_before"] = df_s3["date_d"].map(lambda d: policy_map.get(d, (False, 0, 0))[2])

# ===== 表 A =====
print(f"\n{'=' * 60}")
print("[表A] S3 日前 5/10 日政策底信号频率")
print(f"{'=' * 60}")

for days, lbl in [(7, "前5日"), (14, "前10日")]:
    print(f"\n  window = {lbl}")
    print(f"  {'时期':<18} {'S3总天数':<10} {'有政策底':<10} {'占比':<10}")
    for p, pl in [("train", "2022~2023"), ("test", "2024~2026")]:
        sub = df_s3[df_s3["period"] == p]
        unique_dates = sub["date_d"].unique()
        n_total = len(unique_dates)
        # 对每个 uq 日期检查 policy
        has_cnt = 0
        for ud in unique_dates:
            h, _, _ = get_before(ud, daily, days)
            if h:
                has_cnt += 1
        print(f"  {pl:<18} {n_total:<10} {has_cnt:<10} {has_cnt/n_total:.0%}" if n_total > 0 else "")

# ===== 表 B =====
print(f"\n{'=' * 60}")
print("[表B] 政策底 × S3 收益")
print(f"{'=' * 60}")

# 直接用 df_s3 的 ret_20 做
print(f"\n{'时期':<12} {'前5日政策底':<14} {'信号数':<8} {'T+20均值':<12} {'胜率':<10}")
print("-" * 60)

for p, pl in [("train", "2022~2023"), ("test", "2024~2026")]:
    sub = df_s3[df_s3["period"] == p]
    if len(sub) < 3:
        continue
    for pf, pfl in [(True, "有政策底"), (False, "无政策底")]:
        ss = sub[sub["has_policy"] == pf]
        if len(ss) < 3:
            print(f"  {pl:<12} {pfl:<14} {len(ss):<8} {'<3条，跳过':<20}")
            continue
        print(f"  {pl:<12} {pfl:<14} {len(ss):<8} {ss['ret_20'].mean():+7.2%}   {ss['ret_20'].gt(0).mean():.1%}")

# ===== 典型日期 =====
print(f"\n{'=' * 60}")
print("[典型日期]")
print(f"{'=' * 60}")

for d in [date(2022, 3, 16), date(2022, 4, 26), date(2024, 1, 22), date(2024, 2, 5), date(2024, 2, 6)]:
    print(f"\n  {d}:")
    # 前7日评分高的事件
    s = d - timedelta(days=7)
    evs = df_txt[(df_txt["event_date"] >= s) & (df_txt["event_date"] <= d)]
    high = []
    for _, ev in evs.iterrows():
        sc = score_text(ev["full_text"], ev["title"])
        if sc["net_score"] > BEST_THR or sc["sys_count"] > 0:
            t = (str(ev.get("title", "")) or "")[:65]
            high.append((ev["event_date"], sc, t))
    if high:
        for hd, sc, t in high[:8]:
            print(f"    [{hd}] net={sc['net_score']:.2f} sys={sc['sys_count']} — {t}")
    else:
        print(f"    (无匹配)")
    # 当日S3
    sig = df_s3[df_s3["date_d"] == d]
    if len(sig) > 0:
        print(f"    S3信号: {len(sig)}次, T+20={sig['ret_20'].mean():+.2%}, 胜率={sig['ret_20'].gt(0).mean():.0%}")

# ===== 汇总 =====
print(f"\n{'=' * 60}")
print("汇总")
print(f"{'=' * 60}")

print(f"\n  词库匹配率:")
print(f"    总文本: {len(df_sc)}")
print(f"    匹配到 sys 词: {(df_sc['sys_count']>0).sum()} ({(df_sc['sys_count']>0).mean():.0%})")
print(f"    匹配到 routine 词: {(df_sc['routine_count']>0).sum()} ({(df_sc['routine_count']>0).mean():.0%})")
print(f"    阈值={BEST_THR}")

print(f"\n  政策底×S3:")
for p, pl in [("train", "2022~2023"), ("test", "2024~2026")]:
    sub = df_s3[df_s3["period"] == p]
    if len(sub) < 3:
        continue
    for pf, pfl in [(True, "有政策底"), (False, "无政策底")]:
        ss = sub[sub["has_policy"] == pf]
        if len(ss) >= 3:
            print(f"    {pl} {pfl}: n={len(ss)}, T+20={ss['ret_20'].mean():+.2%}, 胜率={ss['ret_20'].gt(0).mean():.1%}")

# 写入报告
with open(OUTPUT, "w") as f:
    f.write(f"# 事件细粒度分级器报告 (2026-05-25 v2)\n\n")
    for p, pl in [("train", "2022~2023"), ("test", "2024~2026")]:
        sub = df_s3[df_s3["period"] == p]
        if len(sub) < 3:
            continue
        f.write(f"## {pl}\n\n")
        f.write("| 政策底 | 信号数 | T+20 均值 | 胜率 |\n")
        f.write("|--------|--------|----------|------|\n")
        for pf, pfl in [(True, "有政策底"), (False, "无政策底")]:
            ss = sub[sub["has_policy"] == pf]
            if len(ss) >= 3:
                f.write(f"| {pfl} | {len(ss)} | {ss['ret_20'].mean():+.2%} | {ss['ret_20'].gt(0).mean():.0%} |\n")
    f.write(f"\n生成时间: {datetime.now()}\n")

conn.close()
print(f"\n✅ 完成 {datetime.now().strftime('%H:%M')}")
print(f"报告: {OUTPUT}")
