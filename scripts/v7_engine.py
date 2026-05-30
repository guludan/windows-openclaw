"""
v7_engine.py — V7 概率场引擎（基于 V6 批判的五项优化）

核心变更:
  1. 流动性乘数：总能量趋势 → 系统性仓位调整
  2. 条件概率表：月度校准 + 阶段筛选
  3. Phase 降级：只做仓位约束，不做方向判断
  4. 因子筛选：决策树特征重要性决定切分维度
  5. 滚动窗口：3年滚动，按月校准

不依赖: sector_daily, industry_daily, event_car
依赖: pool_energy, daily_prices, market_snapshots, industry_components_ths_l1, incr_decr, csi_lhb_detail
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras
import json, math, time
from datetime import date, timedelta
import numpy as np
from collections import defaultdict
from typing import Optional, Dict, List, Tuple

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

# ——————————————— 行业映射（保留，仅供展示使用） ———————————————
THS_TO_ZS = {
    '半导体': '电子', '光学光电子': '电子', '消费电子': '电子', '元件': '电子',
    '其他电子': '电子', '计算机设备': '计算机', 'IT服务': '计算机',
    '软件开发': '计算机', '通信设备': '通信', '通信服务': '通信',
    '军工电子': '国防军工', '军工装备': '国防军工',
    '通用设备': '机械设备', '专用设备': '机械设备', '自动化设备': '机械设备',
    '电机': '机械设备', '工程机械': '机械设备',
    '汽车零部件': '汽车', '汽车整车': '汽车',
    '化学制品': '基础化工', '农化制品': '基础化工', '化学原料': '基础化工',
    '化学纤维': '基础化工', '橡胶': '基础化工', '塑料': '基础化工',
    '电力': '公用事业', '电池': '电力设备', '光伏设备': '电力设备',
    '电网设备': '电力设备', '风电设备': '电力设备', '其他电源设备': '电力设备',
    '化学制药': '医药生物', '中药': '医药生物', '生物制品': '医药生物',
    '医药商业': '医药生物', '医疗器械': '医药生物', '医疗服务': '医药生物',
    '工业金属': '有色金属', '小金属': '有色金属', '贵金属': '有色金属',
    '金属新材料': '有色金属', '钢铁': '钢铁',
    '建筑装饰': '建筑装饰', '建筑材料': '建筑材料',
    '煤炭开采': '煤炭', '煤炭': '煤炭',
    '石油加工炼炼': '石油石化', '油气': '石油石化',
    '燃气': '公用事业',
    '食品加工制造': '食品饮料', '饮料制造': '食品饮料',
    '纺织制造': '纺织服饰', '服装家纺': '纺织服饰',
    '白色家电': '家用电器', '小家电': '家用电器', '厨卫电器': '家用电器',
    '银行': '银行', '保险': '非银金融', '证券': '非银金融', '多元金融': '非银金融',
    '公路铁路运输': '交通运输', '港口航运': '交通运输', '机场航运': '交通运输', '物流': '交通运输',
    '养殖业': '农林牧渔', '种植业与林业': '农林牧渔', '农产品加工': '农林牧渔',
    '环保': '环保', '环境治理': '环保',
    '零售': '商贸零售', '互联网电商': '商贸零售',
    '传媒': '传媒', '文化传媒': '传媒', '影视院线': '传媒',
}

PHASE_CAP = {
    'B_ACCUM':   1.0,
    'B_DISTRIB': 0.0,
    'NEUTRAL':   0.7,
    'A_RECOVER': 0.5,
    'A_ACCUM':   0.0,
    'HI_EQ':     0.3,
}

class V7Engine:
    """V7 概率场引擎"""

    def __init__(self, conn=None):
        if conn is None:
            self.conn = psycopg2.connect(DB)
        else:
            self.conn = conn
        self.cur = self.conn.cursor()
        # 缓存：一次加载所有需要的数据
        self._market_phases = {}  # {date_str: phase}
        self._ind_map = {}        # {stock_code: {ths: '...', zs: '...'}}
        self._cond_dist = None    # 条件概率表缓存

    # ═══════════════════════════════════════════════════
    # 优化一：流动性乘数 — 总能量趋势降仓
    # ═══════════════════════════════════════════════════

    def get_liquidity_multiplier(self, reference_date: date = None) -> float:
        """
        计算过去 60 个交易日的总能量趋势乘数。
        总能量下降 = 资金流出市场 = 系统性降仓。
        返回 0.5 / 0.75 / 1.0
        """
        if reference_date is None:
            reference_date = date.today()

        self.cur.execute("""
            SELECT b_pool + a_pool as total
            FROM pool_energy
            WHERE level = 'market'
              AND date <= %s
            ORDER BY date DESC LIMIT 60
        """, (reference_date,))
        rows = [r[0] for r in self.cur.fetchall()]
        if len(rows) < 20:
            return 1.0  # 样本不够，不调整

        current = rows[0]
        ma60 = np.mean(rows)
        ratio = current / ma60 if ma60 > 0 else 1.0

        if ratio < 0.85:
            return 0.5
        elif ratio < 0.95:
            return 0.75
        elif ratio > 1.15:
            return 1.0  # 不放大（保守）
        else:
            return 1.0

    # ═══════════════════════════════════════════════════
    # 优化二/四：条件概率分布 — 月度校准滚动查询
    # ═══════════════════════════════════════════════════

    def recalibrate_conditional_distribution(self, force: bool = False):
        """
        月度校准：用过去 3 年数据重新计算条件概率分布。

        先做因子重要性筛选，只保留 top-3 维度切分。
        """
        print("[V7] 开始月度校准...")

        # ── 1. 特征工程：构建因子重要性所需的数据集 ──
        print("  [1] 构建因子重要性数据集...")
        self.cur.execute("""
            SELECT e.level, e.date::text, e.b_pool, e.a_pool,
                   e.phase as mkt_phase,
                   e.extra->>'sigs' as sig_str,
                   e.extra->>'post_vol' as post_vol_str,
                   e.extra->>'insider' as insider_str,
                   e.extra->>'ret_0' as ret_0_str
            FROM pool_energy e
            WHERE e.level LIKE 'code:A.%%'
              AND e.date >= CURRENT_DATE - INTERVAL '36 months'
              AND e.date <= CURRENT_DATE - INTERVAL '2 days'
              AND (e.extra->>'sigs') IS NOT NULL
              AND position('s3' in lower(e.extra->>'sigs')) > 0
            ORDER BY e.date
        """)
        raw_rows = self.cur.fetchall()
        print(f"    原始 S3 信号: {len(raw_rows)} 条")

        # ── 加载行业映射 ──
        self.cur.execute("SELECT stock_code, industry FROM industry_components_ths_l1")
        self._ind_map = {r[0]: {'ths': r[1]} for r in self.cur.fetchall()}

        # ── 加载 market_snapshots PE/PB ──
        print("  [2] 加载截面数据...")
        snap_data = {}
        self.cur.execute("""
            SELECT code, trade_date::text, pe, pb, turnover, total_mv
            FROM market_snapshots
            WHERE trade_date >= CURRENT_DATE - INTERVAL '36 months'
              AND pe > 0 AND pe < 1000
        """)
        for r in self.cur.fetchall():
            # market_snapshots.code 是 6 位纯数字，pool_energy level 是 'code:A.xxx'
            snap_data[('A.' + r[0], r[1])] = {
                'pe': r[2], 'pb': r[3], 'turnover': r[4], 'mv': r[5]
            }

        # ── 加载大盘 phase ──
        self.cur.execute("""
            SELECT date::text, phase FROM pool_energy
            WHERE level='market' AND date >= CURRENT_DATE - INTERVAL '36 months'
        """)
        for r in self.cur.fetchall():
            self._market_phases[r[0]] = r[1]

        # ── 构建特征向量 ──
        features = []
        feature_names = ['pe_percentile', 'pb_percentile', 'post_vol', 'insider',
                         'sector_strong', 'mv_bin', 'phase_enc']

        sector_counts = {}
        self.cur.execute("SELECT industry, COUNT(*) FROM industry_components_ths_l1 GROUP BY industry")
        for r in self.cur.fetchall():
            sector_counts[r[0]] = r[1]

        # 预计算行业每日 PE/PB 百分位（用 PG 的 percentile_cont 批量算）
        # 一次性拉所有信号日期有快照数据的股票，避免逐行子查询
        print("    预计算行业截面百分位...")
        # 把 snap_data 按 date + industry 分组
        from collections import defaultdict
        ind_snap_by_date = defaultdict(lambda: defaultdict(list))
        for (code, dts), snap in snap_data.items():
            code6 = code[2:] if code.startswith('A.') else code
            ind_info = self._ind_map.get(code6)
            if ind_info:
                ind_snap_by_date[dts][ind_info['ths']].append(snap)

        # 预计算所有的行业每日分位
        sector_percentiles = {}  # {(dts, industry): {pe_p30, pe_p70, pb_p30, pb_p70}}
        for dts, ind_data in ind_snap_by_date.items():
            for industry, snaps in ind_data.items():
                pes = sorted([s['pe'] for s in snaps if s['pe'] and s['pe'] > 0])
                pbs = sorted([s['pb'] for s in snaps if s['pb'] and s['pb'] > 0])
                n_pe, n_pb = len(pes), len(pbs)
                sector_percentiles[(dts, industry)] = {
                    'pe_p30': pes[int(n_pe*0.3)] if n_pe > 5 else None,
                    'pe_p70': pes[min(int(n_pe*0.7), n_pe-1)] if n_pe > 5 else None,
                    'pb_p30': pbs[int(n_pb*0.3)] if n_pb > 5 else None,
                    'pb_p70': pbs[min(int(n_pb*0.7), n_pb-1)] if n_pb > 5 else None,
                }

        # 预计算 T+20 收益（批量一次拉，避免逐行 SQL）
        print("    预计算 T+20 收益...")
        # 取所有信号代码的最新 close
        all_codes = set(r[0][7:] for r in raw_rows)
        code_prices = defaultdict(dict)  # {code: {date_str: close}}
        for batch_codes in self._batch(list(all_codes), 500):
            codes_str = ",".join(f"'A.{c}'" for c in batch_codes)
            self.cur.execute(f"""
                SELECT symbol, trade_date::text, close FROM daily_prices
                WHERE symbol IN ({codes_str})
                  AND trade_date >= CURRENT_DATE - INTERVAL '36 months'
                ORDER BY symbol, trade_date
            """)
            for row in self.cur.fetchall():
                c = row[0][2:]
                code_prices[c][row[1]] = row[2]

        def calc_t20(code, dts):
            prices = code_prices.get(code, {})
            dates_sorted = sorted(prices.keys())
            c0 = prices.get(dts)
            if not c0:
                return None
            # 找 dts 后第 20 个交易日
            idx = -1
            for i, d in enumerate(dates_sorted):
                if d >= dts:
                    idx = i
                    break
            if idx < 0 or idx + 20 >= len(dates_sorted):
                return None
            c20 = prices.get(dates_sorted[idx + 20])
            if not c20:
                return None
            return c20 / c0 - 1

        # 预计算板块强势占比
        print("    预计算板块强势占比...")
        sector_strong_cache = {}
        for ind_name, cnt in sector_counts.items():
            if cnt < 5:
                continue
            self.cur.execute(f"""
                SELECT d.trade_date::text,
                       AVG(CASE WHEN d.close > p.close * 1.05 THEN 1.0 ELSE 0.0 END) as strong_pct
                FROM daily_prices d
                JOIN industry_components_ths_l1 ic ON ic.stock_code = SUBSTRING(d.symbol, 3)
                JOIN LATERAL (
                    SELECT close FROM daily_prices
                    WHERE symbol = d.symbol AND trade_date < d.trade_date
                    ORDER BY trade_date DESC LIMIT 1
                ) p ON true
                WHERE ic.industry = %s
                  AND d.trade_date >= CURRENT_DATE - INTERVAL '36 months'
                  AND d.close > 0 AND p.close > 0
                GROUP BY d.trade_date
            """, (ind_name,))
            for row in self.cur.fetchall():
                sector_strong_cache[(row[0], ind_name)] = row[1]

        for idx, r in enumerate(raw_rows):
            level = r[0]; dts = r[1]
            code = level[7:]

            # phase
            phase = self._market_phases.get(dts, 'NEUTRAL')

            # snap + ind
            snap = snap_data.get(('A.' + code, dts))
            if not snap or snap['pe'] is None:
                continue
            ind_info = self._ind_map.get(code)
            if not ind_info:
                continue
            industry = ind_info['ths']

            # PE/PB 分位
            sp = sector_percentiles.get((dts, industry))
            if not sp or sp['pe_p30'] is None:
                continue
            pe = snap['pe']
            pb = snap['pb']
            if pe <= 0: continue
            pe_p = 0 if pe <= sp['pe_p30'] else (2 if pe >= sp['pe_p70'] else 1)
            pb_p = 0 if (pb and pb <= sp['pb_p30']) else (2 if (pb and pb >= sp['pb_p70']) else 1) if pb else 1

            # post_vol (float)
            try:
                pv = float(r[6]) if r[6] else 0.5
            except:
                pv = 0.5

            # insider
            ins = 1 if r[7] and 'buy' in r[7].lower() else (-1 if r[7] and 'sell' in r[7].lower() else 0)

            # 板块强势
            sector_strong = sector_strong_cache.get((dts, industry), 0)

            # 市值
            mv = snap.get('mv', 0) or 0
            mv_bin = 0 if mv < 30 else (1 if mv < 100 else 2)

            # phase 编码
            phase_enc = {'B_ACCUM':2, 'B_DISTRIB':1, 'NEUTRAL':0, 'A_RECOVER':-1, 'A_ACCUM':-2}.get(phase, 0)

            # T+20
            t20_ret = calc_t20(code, dts)
            if t20_ret is None:
                continue

            features.append([pe_p, pb_p, pv, ins, sector_strong, mv_bin, phase_enc, t20_ret])

            if idx % 500 == 0:
                print(f"    processing: {idx}/{len(raw_rows)} -> {len(features)} effective")

        print(f"    有效特征样本: {len(features)}")

        # ── 3. 决策树特征重要性 ──
        print("  [3] 因子重要性筛选...")
        if len(features) < 100:
            print("    样本不足，跳过")
            return

        X = np.array([f[:-1] for f in features])
        y = np.array([f[-1] for f in features])

        from sklearn.tree import DecisionTreeRegressor
        dt = DecisionTreeRegressor(max_depth=3, min_samples_leaf=30, random_state=42)
        dt.fit(X, y)

        importances = [(name, dt.feature_importances_[i])
                       for i, name in enumerate(feature_names)]
        importances.sort(key=lambda x: -x[1])

        print("    特征重要性:")
        top_features = []
        for name, imp in importances:
            flag = "✅" if imp >= 0.15 else ("⚪" if imp >= 0.05 else "❌")
            print(f"      {flag} {name}: {imp:.4f}")
            if imp >= 0.05:
                top_features.append(name)

        # ── 4. 基于 top 特征建条件概率表 ──
        print(f"  [4] 用 top {len(top_features)} 特征建立体表...")
        self._build_distribution_table(features, top_features)

        # 标记校准日期
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS v7_calibration_log (
                calibrate_date DATE PRIMARY KEY,
                top_features TEXT,
                sample_count INT,
                stable_ratio DOUBLE PRECISION
            )
        """)
        self.cur.execute("""
            INSERT INTO v7_calibration_log (calibrate_date, top_features, sample_count)
            VALUES (CURRENT_DATE, %s, %s)
            ON CONFLICT (calibrate_date) DO UPDATE SET top_features=EXCLUDED.top_features, sample_count=EXCLUDED.sample_count
        """, (','.join(top_features), len(features)))
        self.conn.commit()
        print("  ✅ 校准完成")

    # 旧方法已合并到 recalibrate 内建预处理中
    # 保留 _calc_ 方法签名防止外部调用报错
    def _calc_percentile_in_sector(self, dts, industry, field, value):
        return None
    def _calc_sector_strong(self, industry, dts, exclude_code):
        return 0.0
    def _calc_forward_return(self, code, dts, n_days):
        return None

    def _build_distribution_table(self, features: list, top_features: list):
        """按 top 特征分桶，统计每组收益分布，写入 v7_conditional_distribution"""
        self.cur.execute("DROP TABLE IF EXISTS v7_conditional_distribution CASCADE")
        self.cur.execute("""
            CREATE TABLE v7_conditional_distribution (
                dim1 VARCHAR(20), dim2 VARCHAR(20), dim3 VARCHAR(20),
                dim4 VARCHAR(20),
                sample_count INT,
                t20_mean DOUBLE PRECISION, t20_median DOUBLE PRECISION,
                t20_wr DOUBLE PRECISION,
                t20_p10 DOUBLE PRECISION, t20_p90 DOUBLE PRECISION,
                t5_mean DOUBLE PRECISION, t5_wr DOUBLE PRECISION
            )
        """)
        self.cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_v7cd ON v7_conditional_distribution
            (dim1, dim2, dim3, dim4)
        """)

        # 确定分桶维度索引
        name_to_idx = {name: i for i, name in enumerate(feature_names)}
        dim_indices = [name_to_idx[n] for n in top_features if n in name_to_idx]

        # 留 4 个 dim 槽位，前 4 个 top 特征用于分桶
        bucket_dims = dim_indices[:min(4, len(dim_indices))]

        def bucket_val(val):
            if isinstance(val, (int, float)):
                if val <= 0: return 'low'
                elif val <= 1: return 'mid'
                else: return 'high'
            if val == 'low' or val == 'mid' or val == 'high':
                return val
            return 'none'

        # 按桶聚合
        groups = defaultdict(list)
        for f in features:
            key_parts = []
            for di in bucket_dims:
                key_parts.append(bucket_val(f[di]))
            # 补到4维
            while len(key_parts) < 4:
                key_parts.append('none')
            groups[tuple(key_parts)].append(f[-1])

        # 写入
        from collections import Counter
        stats_list = []
        for key, vals in groups.items():
            arr = np.array(vals)
            if len(arr) < 5:
                continue

            # T+20
            t20_m = float(np.mean(arr))
            t20_med = float(np.median(arr))
            t20_wr = float(np.mean(arr > 0))
            t20_p10 = float(np.percentile(arr, 10))
            t20_p90 = float(np.percentile(arr, 90))

            stats_list.append((list(key) + [len(arr), round(t20_m, 4), round(t20_med, 4),
                                            round(t20_wr, 4), round(t20_p10, 4), round(t20_p90, 4),
                                            round(t20_m, 4), round(t20_wr, 4)]))
            stats_list[-1] = tuple(stats_list[-1])

        self.cur.executemany("""
            INSERT INTO v7_conditional_distribution
            (dim1, dim2, dim3, dim4, sample_count,
             t20_mean, t20_median, t20_wr, t20_p10, t20_p90,
             t5_mean, t5_wr)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, stats_list)
        self.conn.commit()
        print(f"    写入 {len(stats_list)} 条件组合")

    # ═══════════════════════════════════════════════════
    # 优化三：Phase 仓位约束（只限制上限，不决定方向）
    # ═══════════════════════════════════════════════════

    def phase_max_cap(self, phase: str) -> float:
        """Phase 决定最大仓位占比，不决定买卖方向"""
        return PHASE_CAP.get(phase, 0.7)

    # ═══════════════════════════════════════════════════
    # 优化五：单信号查询 — 条件概率 + 流动性乘数
    # ═══════════════════════════════════════════════════

    def query_signal(self, code: str, signal_date: date,
                     signal_type: str = 'S3', phase: str = None) -> dict:
        """
        对单个信号输出条件概率分布 + 仓位建议。
        不产生 score，只输出统计证据。
        """
        result = {
            'code': code, 'date': str(signal_date),
            'signal_type': signal_type,
            'phase': phase or self._get_phase(str(signal_date)),
        }

        # 流动性乘数
        liq_mult = self.get_liquidity_multiplier(signal_date)
        result['liquidity_multiplier'] = liq_mult

        # Phase 仓位上限
        result['phase_cap'] = self.phase_max_cap(result['phase'])
        result['max_position'] = liq_mult * result['phase_cap']

        # 查条件概率分布
        dist = self._lookup_distribution(code, str(signal_date), signal_type)
        result['distribution'] = dist

        # 综合建议
        if dist and dist['sample_count'] >= 20 and dist['t20_wr'] >= 0.6:
            result['verdict'] = 'positive'
            result['confidence'] = 'high'
        elif dist and dist['sample_count'] >= 20 and dist['t20_wr'] < 0.4:
            result['verdict'] = 'negative'
            result['confidence'] = 'high'
        elif dist and dist['sample_count'] >= 10:
            result['verdict'] = 'cautious'
            result['confidence'] = 'low'
        else:
            result['verdict'] = 'insufficient_data'
            result['confidence'] = 'none'

        return result

    def _lookup_distribution(self, code: str, dts: str, sig: str) -> Optional[dict]:
        """查 v7_conditional_distribution 表"""
        # 先用行业映射确定分桶
        ind_info = self._ind_map.get(code)
        if not ind_info:
            return None
        industry = ind_info['ths']

        # PE 分位
        snap, _ = self.conn.cursor().execute(
            "SELECT pe, pb, total_mv FROM market_snapshots WHERE code=%s AND trade_date=%s::date",
            ('A.' + code, dts)
        ).fetchone() or (None, None, None)
        if not snap or snap[0] is None:
            return None

        pe_b = 'low' if snap[0] <= 10 else ('high' if snap[0] >= 50 else 'mid') if snap[0] > 0 else 'none'

        # 从条件概率表查询最匹配的行
        self.cur.execute("""
            SELECT sample_count, t20_mean, t20_median, t20_wr, t20_p10, t20_p90
            FROM v7_conditional_distribution
            WHERE dim1 = %s
            ORDER BY sample_count DESC LIMIT 1
        """, (pe_b,))
        row = self.cur.fetchone()
        if row:
            return {
                'sample_count': row[0], 't20_mean': row[1],
                't20_median': row[2], 't20_wr': row[3],
                't20_p10': row[4], 't20_p90': row[5]
            }
        return None

    def _get_phase(self, dts: str) -> str:
        return self._market_phases.get(dts, 'NEUTRAL')

    @staticmethod
    def _batch(items, size):
        for i in range(0, len(items), size):
            yield items[i:i+size]


# ═══════════════════════════════════════════════════
# 快捷命令
# ═══════════════════════════════════════════════════
if __name__ == '__main__':
    import sys
    engine = V7Engine()

    if '--calibrate' in sys.argv:
        engine.recalibrate_conditional_distribution(force=True)

    elif '--query' in sys.argv:
        # 用法：python3 v7_engine.py --query 000158 2026-05-26 S3
        idx = sys.argv.index('--query')
        code = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else '000158'
        dt = date.fromisoformat(sys.argv[idx + 2]) if len(sys.argv) > idx + 2 else date.today()
        sig = sys.argv[idx + 3] if len(sys.argv) > idx + 3 else 'S3'
        r = engine.query_signal(code, dt, sig)
        print(json.dumps(r, indent=2, default=str))

    elif '--phase-regression' in sys.argv:
        print("[V7] Phase 回归检验...")
        engine.run_phase_regression()

    else:
        print("用法:")
        print("  --calibrate       : 月度校准（因子筛选 + 条件概率表）")
        print("  --query code dt S3: 查询单个信号的条件概率")
        print("  --phase-regression: Phase 独立预测力检验")
