#!/home/tearsyu/miniconda3/envs/tt/bin/python3
"""
============================================================================
全周期回测框架 (Cycle Backtest Framework)
============================================================================

三层架构：
  Layer 1 — 市场周期层 (CycleClassifier)  从 pool_energy 取 regime/phase/surplus_z/accel
  Layer 2 — 庄家阶段层 (StageDetector)     复用 zhuangjia_backtest_v2.py 的信号逻辑
  Layer 3 — V8 信号层  (V8SignalDetector)  SMA偏离深度 × 波动率异常

全组合交叉统计：regime × phase × stage × tier → 收益/胜率/尾部风险

数据源：
  - 日线: daily_prices (symbol='A.{code}')
  - 相空间: pool_energy (level='market')
  - DB: host=192.168.3.14 dbname=guludan user=guludan password=toto

用法:
  # 采样测试（20只/2022-2026）
  ~/miniconda3/envs/tt/bin/python3 scripts/cycle_backtest_framework.py --sample

  # 全量回测
  ~/miniconda3/envs/tt/bin/python3 scripts/cycle_backtest_framework.py --full

  # 指定股票池和日期范围
  ~/miniconda3/envs/tt/bin/python3 scripts/cycle_backtest_framework.py \
      --codes 000001 000002 600519 --start 2018-01-01 --end 2026-06-02

作者: 小狗蛋 🐶
版本: 1.0
日期: 2026-06-03
============================================================================
"""
import sys, os, json, argparse, random
import numpy as np
import pandas as pd
import psycopg2
from datetime import date, datetime, timedelta
from collections import defaultdict, Counter

# ──────────────────────────────────────────────
# 全局配置
# ──────────────────────────────────────────────
DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'
LOOKBACK_DAYS = 120     # 所需最少历史日线
MIN_HISTORY   = 200     # 每股最少总数据天数
FORECAST_DAYS = [5, 10, 20, 40]

# 庄家阶段判定参数（来自 zhuangjia_backtest_v2.py）
STAGE_PARAMS = {
    'yang_20_threshold': 0.55,
    'vol_ratio_lo': 1.0,
    'vol_ratio_hi': 1.8,
    'amp_20_max': 0.05,
    'shadow_ratio_threshold': 0.5,
    'vol_ratio_today_min': 1.5,
    'shadow_pct_min': 0.03,
    'pump_yang_5_min': 3,
    'pump_vol_ratio_min': 1.1,
    'pump_amp_20_min': 0.035,
    'distribute_shadow_count': 3,
    'distribute_vol_increase': 1.3,
    'wash_decline_min': -0.03,
    'wash_conseq_days': 2,
    'wash_post_vol_max_ratio': 0.4,
}

# V8 信号层级阈值 (dev_thresh: 偏离<该值, volz_thresh: vol_z>该值)
V8_TIER_THRESHOLDS = [
    ('V8', -0.20, 2.0),   # 偏离<-20%  且 波动率z>2.0
    ('S1', -0.15, 1.5),   # 偏离<-15%  且 波动率z>1.5
    ('S2', -0.10, 1.0),   # 偏离<-10%  且 波动率z>1.0
    ('S3', -0.05, 0.5),   # 偏离<-5%   且 波动率z>0.5
]

# ──────────────────────────────────────────────
# Layer 1: 市场周期分类器 (CycleClassifier)
# ──────────────────────────────────────────────
class CycleClassifier:
    """从pool_energy取regime + phase + surplus_z + accel"""

    REGIME_LABELS = {
        'BEAR': 'BEAR',
        'BOTTOM_WARNING': 'BEAR_BOTTOM',
        'BULL': 'BULL',
        'TOP_WARNING': 'BULL_TOP',
        'SIDEWAYS': 'SIDEWAYS',
    }

    PHASE_GROUPS = {
        'B_ACCUM':   'bullish',
        'A_DISTRIB': 'bearish',
        'A_ACCUM':   'bearish',
        'B_DISTRIB': 'bullish',
        'B_RECOVER': 'bullish',
        'A_RECOVER': 'bearish',
        'NEUTRAL':   'neutral',
        'HI_EQ':     'neutral',
    }

    def __init__(self, conn=None):
        self._data = None   # DataFrame indexed by date
        self._conn = conn or psycopg2.connect(DB)

    def load(self):
        """加载pool_energy全部数据"""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT date, b_pool, a_pool, surplus, imbalance, phase, extra
            FROM pool_energy WHERE level='market'
            ORDER BY date
        """)
        rows = cur.fetchall()
        records = []
        for r in rows:
            dt = r[0]
            extra = r[6] if isinstance(r[6], dict) else (
                json.loads(r[6]) if isinstance(r[6], str) else {}
            )
            records.append({
                'date': dt,
                'b_pool': float(r[1]),
                'a_pool': float(r[2]),
                'surplus': float(r[3]),
                'imbalance': float(r[4]),
                'phase': r[5],
                'regime': extra.get('regime', 'SIDEWAYS'),
                'surplus_z': extra.get('surplus_z', 0.0),
                'accel': extra.get('accel', 0.0),
                'sz_5': extra.get('sz_5', 0.0),
                'sz_20': extra.get('sz_20', 0.0),
            })
        self._data = pd.DataFrame(records).set_index('date')
        self._data.index = pd.to_datetime(self._data.index)
        return self

    def classify(self, trade_date):
        """对某个交易日进行分类 → {regime, phase, surplus_z, accel, sz_5, sz_20, regime_label}"""
        dt = pd.Timestamp(trade_date)
        if self._data is None or self._data.empty:
            return self._default()
        # 找到 <= dt 的最新行
        mask = self._data.index <= dt
        if not mask.any():
            return self._default()
        row = self._data[mask].iloc[-1]
        raw_regime = row.get('regime', 'SIDEWAYS')
        return {
            'regime': raw_regime,
            'phase': row.get('phase', 'NEUTRAL'),
            'surplus_z': float(row.get('surplus_z', 0.0)),
            'accel': float(row.get('accel', 0.0)),
            'sz_5': float(row.get('sz_5', 0.0)),
            'sz_20': float(row.get('sz_20', 0.0)),
            'regime_label': self.REGIME_LABELS.get(raw_regime, 'SIDEWAYS'),
            'phase_group': self.PHASE_GROUPS.get(row.get('phase', 'NEUTRAL'), 'neutral'),
        }

    @staticmethod
    def _default():
        return {
            'regime': 'SIDEWAYS', 'phase': 'NEUTRAL',
            'surplus_z': 0.0, 'accel': 0.0,
            'sz_5': 0.0, 'sz_20': 0.0,
            'regime_label': 'SIDEWAYS', 'phase_group': 'neutral',
        }


# ──────────────────────────────────────────────
# Layer 2: 庄家阶段检测器 (StageDetector)
# ──────────────────────────────────────────────
class StageDetector:
    """检测庄家行为阶段: 建仓/试盘/洗盘/拉升/出货/无主力特征"""
    
    STAGE_NAMES = ['建仓期', '试盘期', '洗盘期', '拉升期', '出货期', '无主力特征']
    STAGE_PRIORITY = {'出货期': 5, '拉升期': 4, '洗盘期': 3, '试盘期': 2, '建仓期': 1, '无主力特征': 0}

    def detect(self, closes, opens, highs, lows, volumes):
        """
        输入: 各为 numpy array，长度一致
        输出: 每个位置的 stage_name(str)
        """
        n = len(closes)
        stage = np.full(n, '无主力特征', dtype=object)

        if n < LOOKBACK_DAYS:
            return stage.tolist()

        # 基础序列
        r1d = np.full(n, np.nan)
        r1d[1:] = closes[1:] / closes[:-1] - 1

        candle_range = highs - lows
        shadow_upper = highs - np.maximum(closes, opens)
        shadow_lower = np.minimum(closes, opens) - lows
        shadow_ratio = np.divide(shadow_upper, candle_range, out=np.zeros(n), where=candle_range > 0)
        upper_pct = shadow_upper / np.maximum(closes, 1)

        yang = closes >= opens
        yang_20 = pd.Series(yang).rolling(20).mean().values
        yang_5 = pd.Series(yang).rolling(5).sum().values

        # 量比
        vol_10 = pd.Series(volumes).rolling(10).mean().values
        vol_60 = pd.Series(volumes).rolling(60).mean().values
        vol_ratio = vol_10 / np.maximum(vol_60, 1)
        
        vol_5 = pd.Series(volumes).rolling(5).mean().values
        vol_20 = pd.Series(volumes).rolling(20).mean().values
        vol_ratio_5v20 = vol_5 / np.maximum(vol_20, 1)

        # 振幅
        amp_20 = pd.Series(candle_range / np.maximum(closes, 1)).rolling(20).mean().values

        # 高60
        high_60 = pd.Series(highs).rolling(60).max().shift(1).values

        # 上影线计数(10日)
        shadow_cond = (shadow_ratio > 0.4) & (candle_range > 0)
        shadow_count_10 = pd.Series(shadow_cond).rolling(10).sum().values

        # 连跌
        conseq_dn = np.zeros(n, dtype=int)
        total_decline = np.zeros(n)
        for i in range(1, n):
            if r1d[i] < 0:
                conseq_dn[i] = conseq_dn[i-1] + 1
                total_decline[i] = closes[i] / closes[i - conseq_dn[i]] - 1

        # 逐日判定（从 LOOKBACK_DAYS 开始）
        for i in range(LOOKBACK_DAYS, n):
            # --- 拉升期 ---
            if (yang_5[i] >= STAGE_PARAMS['pump_yang_5_min']
                    and vol_ratio[i] > STAGE_PARAMS['pump_vol_ratio_min']
                    and r1d[i] > 0 and r1d[i] < 0.105
                    and amp_20[i] > STAGE_PARAMS['pump_amp_20_min']
                    and highs[i] > high_60[i] * 0.95):
                stage[i] = '拉升期'
                continue

            # --- 出货期 ---
            if (vol_ratio_5v20[i] > STAGE_PARAMS['distribute_vol_increase']
                    and shadow_count_10[i] >= STAGE_PARAMS['distribute_shadow_count']):
                stage[i] = '出货期'
                continue

            # --- 建仓期 ---
            if (yang_20[i] > STAGE_PARAMS['yang_20_threshold']
                    and STAGE_PARAMS['vol_ratio_lo'] <= vol_ratio[i] <= STAGE_PARAMS['vol_ratio_hi']
                    and amp_20[i] < STAGE_PARAMS['amp_20_max']):
                stage[i] = '建仓期'

            # --- 试盘期 ---
            if (shadow_ratio[i] > STAGE_PARAMS['shadow_ratio_threshold']
                    and shadow_upper[i] > shadow_lower[i]
                    and closes[i] < opens[i]
                    and -0.03 < r1d[i] < 0.05
                    and volumes[i] / max(vol_60[i], 1) > STAGE_PARAMS['vol_ratio_today_min']
                    and upper_pct[i] > STAGE_PARAMS['shadow_pct_min']):
                stage[i] = '试盘期'

            # --- 洗盘期 ---
            if (stage[i] in ('建仓期', '试盘期', '拉升期', '无主力特征')
                    and conseq_dn[i] >= STAGE_PARAMS['wash_conseq_days']
                    and r1d[i] < STAGE_PARAMS['wash_decline_min']):
                # 检查后续3天缩量
                if i + 3 < n:
                    post_vol = volumes[i:i+3]
                    pre_vol = np.mean(volumes[max(0, i-5):i])
                    if post_vol.max() < STAGE_PARAMS['wash_post_vol_max_ratio'] * max(pre_vol, 1):
                        stage[i] = '洗盘期'

        return stage.tolist()


# ──────────────────────────────────────────────
# Layer 3: V8 信号检测器 (V8SignalDetector)
# ──────────────────────────────────────────────
class V8SignalDetector:
    """SMA偏离深度 × 波动率异常 → V8/S1/S2/S3/NONE"""

    def __init__(self, sma_window=120, vol_window=60):
        self.sma_window = sma_window
        self.vol_window = vol_window

    def detect(self, closes, volumes, at_idx):
        """
        在 at_idx 处检测V8信号
        返回: {sma_dev, vol_z, tier}
        """
        n = len(closes)
        if n < self.sma_window + 1 or at_idx < self.sma_window:
            return {'sma_dev': 0.0, 'vol_z': 0.0, 'tier': 'NONE'}

        # SMA 偏离
        sma = np.mean(closes[at_idx - self.sma_window : at_idx + 1])
        sma_dev = closes[at_idx] / sma - 1

        # 波动率 z-score
        window_start = max(0, at_idx - self.vol_window + 1)
        vol_slice = volumes[window_start : at_idx + 1]
        vol_mean = np.mean(vol_slice)
        vol_std = np.std(vol_slice)
        vol_z = (volumes[at_idx] - vol_mean) / max(vol_std, 1e-8)

        # 定级: dev_thresh 已经是负数 (如-0.20)，所以 sma_dev < dev_thresh
        # 同时检查是否卖出偏离（sma_dev > 0.15 视为泡沫）
        tier = 'NONE'
        if sma_dev > 0.15 and vol_z > 1.5:
            tier = 'BUBBLE'
        else:
            for name, dev_thresh, volz_thresh in V8_TIER_THRESHOLDS:
                if sma_dev < dev_thresh and vol_z > volz_thresh:
                    tier = name
                    break

        return {'sma_dev': sma_dev, 'vol_z': vol_z, 'tier': tier}


# ──────────────────────────────────────────────
# 全周期回测引擎 (CycleBacktestEngine)
# ──────────────────────────────────────────────
class CycleBacktestEngine:
    """
    逐日滚动遍历，全栈信号生成
    输出: DataFrame (每行 = 一次信号)
    """

    def __init__(self, codes=None, start='2018-01-01', end='2026-06-02',
                 lookback_days=LOOKBACK_DAYS, min_history=MIN_HISTORY,
                 forecast_days=None, sample=False, sample_count=20):
        self.codes = codes or []
        self.start = start
        self.end = end
        self.lookback_days = lookback_days
        self.min_history = min_history
        self.forecast_days = forecast_days or FORECAST_DAYS
        self.sample = sample
        self.sample_count = sample_count

        self.conn = psycopg2.connect(DB)
        self.classifier = CycleClassifier(self.conn)
        self.stage_detector = StageDetector()
        self.v8_detector = V8SignalDetector()

    def load_codes(self):
        """加载股票代码"""
        if self.codes:
            return self.codes

        cur = self.conn.cursor()
        if self.sample:
            cur.execute("""
                SELECT code FROM market_snapshots 
                WHERE trade_date = (SELECT MAX(trade_date) FROM market_snapshots)
                AND float_mv >= 10
                AND code ~ '^[0-9]{6}$'
            """)
            all_codes = [r[0] for r in cur.fetchall()]
            random.seed(42)
            codes = random.sample(all_codes, min(self.sample_count, len(all_codes)))
            print(f"  采样模式: {len(codes)}只股票 (从{len(all_codes)}只中抽取)")
            return codes
        else:
            cur.execute("""
                SELECT code FROM market_snapshots 
                WHERE trade_date = (SELECT MAX(trade_date) FROM market_snapshots)
                AND float_mv >= 10
                AND code ~ '^[0-9]{6}$'
                ORDER BY float_mv DESC
            """)
            codes = [r[0] for r in cur.fetchall()]
            print(f"  全量模式: {len(codes)}只股票")
            return codes

    def load_daily(self, code):
        """加载单只股票日线"""
        cur = self.conn.cursor()
        cur.execute(f"""
            SELECT trade_date, close, open, high, low, volume
            FROM daily_prices
            WHERE symbol='A.{code}' AND trade_date >= %s AND trade_date <= %s
              AND close > 0 AND open > 0 AND high > 0 AND low > 0
            ORDER BY trade_date
        """, (self.start, self.end))
        rows = cur.fetchall()
        if len(rows) < self.min_history:
            return None
        df = pd.DataFrame(rows, columns=['trade_date', 'close', 'open', 'high', 'low', 'volume'])
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df

    def compute_forward_returns(self, closes, idx, n):
        """计算未来 n 日收益"""
        if idx + n >= len(closes):
            return np.nan
        return closes[idx + n] / closes[idx] - 1

    def run(self):
        """主运行循环"""
        print(f"\n{'='*70}")
        print(f"  全周期回测引擎 v1.0")
        print(f"  {'日期':>10s}: {self.start} ~ {self.end}")
        print(f"  {'展望日':>10s}: {self.forecast_days}")
        if self.sample:
            print(f"  {'采样':>10s}: {self.sample_count}只")
        print(f"{'='*70}")

        # 1. 加载大盘周期数据
        print("\n[1/4] 加载市场周期数据...")
        self.classifier.load()
        print(f"  已加载 {len(self.classifier._data)} 天周期数据")

        # 2. 加载股票列表
        print("\n[2/4] 加载股票列表...")
        codes = self.load_codes()
        total = len(codes)

        # 3. 逐只回测
        print("\n[3/4] 逐股回测...")
        all_signals = []

        for idx, code in enumerate(codes):
            df = self.load_daily(code)
            if df is None:
                continue

            closes = df['close'].values.astype(float)
            opens = df['open'].values.astype(float)
            highs = df['high'].values.astype(float)
            lows = df['low'].values.astype(float)
            volumes = df['volume'].values.astype(float)
            dates = df['trade_date'].values
            n = len(closes)

            # 批量检测庄家阶段 (per-day)
            stages = self.stage_detector.detect(closes, opens, highs, lows, volumes)

            # 逐日扫描信号
            signal_count = 0
            for i in range(self.lookback_days, n):
                d = pd.Timestamp(dates[i])

                # 市场周期
                cycle = self.classifier.classify(d.date())

                # V8 信号
                v8 = self.v8_detector.detect(closes, volumes, i)

                # 庄家阶段
                stage = stages[i]

                # 未来收益
                rets = {}
                for fd in self.forecast_days:
                    rets[f'ret_{fd}'] = self.compute_forward_returns(closes, i, fd)

                all_signals.append({
                    'trade_date': d,
                    'code': code,
                    'close': float(closes[i]),
                    'sma_dev': v8['sma_dev'],
                    'vol_z': v8['vol_z'],
                    'tier': v8['tier'],
                    'stage': stage,
                    'regime': cycle['regime'],
                    'regime_label': cycle['regime_label'],
                    'phase': cycle['phase'],
                    'phase_group': cycle['phase_group'],
                    'surplus_z': cycle['surplus_z'],
                    'accel': cycle['accel'],
                    'sz_5': cycle['sz_5'],
                    'sz_20': cycle['sz_20'],
                    **{f'ret_{fd}': rets[f'ret_{fd}'] for fd in self.forecast_days},
                })
                signal_count += 1

            if (idx + 1) % 50 == 0:
                print(f"  [{idx+1}/{total}] {code} → {signal_count}条信号 累计:{len(all_signals)}")

        self.conn.close()

        if not all_signals:
            print("\n⚠️ 没有产生任何信号！")
            return pd.DataFrame()

        result = pd.DataFrame(all_signals)
        print(f"\n  总信号: {len(result)}条")
        print(f"  日期范围: {result['trade_date'].min().date()} ~ {result['trade_date'].max().date()}")
        print(f"  覆盖股票: {result['code'].nunique()}只")
        return result


# ──────────────────────────────────────────────
# 报告生成 (generate_report)
# ──────────────────────────────────────────────
def generate_report(df):
    """
    多维度交叉统计分析报告
    """
    print(f"\n{'='*70}")
    print(f"  全周期回测分析报告")
    print(f"{'='*70}")
    print(f"  总信号数:   {len(df):,}")
    print(f"  覆盖股票:   {df['code'].nunique():,}")
    print(f"  日期范围:   {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")

    TARGETS = [5, 10, 20, 40]
    ret_cols = [f'ret_{t}' for t in TARGETS]

    # ── 0. 全量统计 ──
    print(f"\n{'─'*70}")
    print(f"  0. 全量收益统计")
    print(f"{'─'*70}")
    print(f"  {'展望':>6s} {'N':>8s} {'均值':>9s} {'中位':>9s} {'胜率':>7s} {'Std':>9s} {'夏普(年)':>10s}")
    for t, rc in zip(TARGETS, ret_cols):
        rs = df[rc].dropna()
        if len(rs) == 0: continue
        sharpe = np.mean(rs) / max(np.std(rs), 1e-6) * np.sqrt(252 / t)
        print(f"  {'T+'+str(t):>6s} {len(rs):>8,} {np.mean(rs):+9.2%} {np.median(rs):+9.2%} {np.mean(rs>0):>7.1%} {np.std(rs):>9.2%} {sharpe:>10.2f}")

    # ── 1. 按 regime 分类 ──
    print(f"\n{'─'*70}")
    print(f"  1. 按市场周期 (regime) 分类 — T+20收益")
    print(f"{'─'*70}")
    rc20 = 'ret_20'
    for reg in ['BULL', 'BEAR', 'SIDEWAYS', 'BEAR_BOTTOM', 'BULL_TOP']:
        sub = df[df['regime_label'] == reg]
        rs = sub[rc20].dropna()
        if len(rs) < 5: continue
        sharpe = np.mean(rs) / max(np.std(rs), 1e-6) * np.sqrt(252 / 20)
        print(f"  {reg:<14s} (n={len(rs):>8,}): "
              f"均值{np.mean(rs):+9.2%} 中位{np.median(rs):+9.2%} "
              f"胜率{np.mean(rs>0):>6.1%} 夏普{sharpe:>7.2f}")

    # ── 2. 按 stage 交叉 regime ──
    print(f"\n{'─'*70}")
    print(f"  2. 按庄家阶段 × 市场周期 — T+20收益")
    print(f"{'─'*70}")
    for stage_name in StageDetector.STAGE_NAMES:
        sdf = df[df['stage'] == stage_name]
        if len(sdf) < 10:
            continue
        print(f"\n  📍 {stage_name} (总{len(sdf):,}条)")
        for reg in ['BULL', 'BEAR', 'SIDEWAYS', 'BEAR_BOTTOM', 'BULL_TOP']:
            ss = sdf[sdf['regime_label'] == reg]
            rs = ss[rc20].dropna()
            if len(rs) < 3: continue
            sharpe = np.mean(rs) / max(np.std(rs), 1e-6) * np.sqrt(252 / 20)
            print(f"    {reg:<14s} (n={len(rs):>6,}): "
                  f"均值{np.mean(rs):+8.2%} 中位{np.median(rs):+8.2%} 胜率{np.mean(rs>0):>6.1%} "
                  f"夏普{sharpe:>6.2f}")

    # ── 3. 按 tier 交叉 regime ──
    print(f"\n{'─'*70}")
    print(f"  3. 按 V8 层级 × 市场周期 — T+20收益")
    print(f"{'─'*70}")
    tier_order = ['V8', 'S1', 'S2', 'S3', 'NONE']
    for tier in tier_order:
        tdf = df[df['tier'] == tier]
        if len(tdf) < 10:
            continue
        print(f"\n  📡 {tier} (总{len(tdf):,}条, sma_dev中位{np.median(tdf['sma_dev']):+.2%})")
        for reg in ['BULL', 'BEAR', 'SIDEWAYS', 'BEAR_BOTTOM', 'BULL_TOP']:
            ss = tdf[tdf['regime_label'] == reg]
            rs = ss[rc20].dropna()
            if len(rs) < 3: continue
            sharpe = np.mean(rs) / max(np.std(rs), 1e-6) * np.sqrt(252 / 20)
            print(f"    {reg:<14s} (n={len(rs):>6,}): "
                  f"均值{np.mean(rs):+8.2%} 中位{np.median(rs):+8.2%} 胜率{np.mean(rs>0):>6.1%} "
                  f"夏普{sharpe:>6.2f}")

    # ── 4. 全组合交叉 (regime + phase + stage + tier) ──
    print(f"\n{'─'*70}")
    print(f"  4. 全组合交叉 (前20组合) — T+20收益")
    print(f"{'─'*70}")

    # 对 phase 做简化分组
    df['combo'] = (df['regime_label'] + '/' + df['stage'] + 
                   '/' + df['tier'] + '/' + df['phase'])
    
    combo_stats = []
    for combo, grp in df.groupby('combo'):
        rs = grp[rc20].dropna()
        if len(rs) < 20:  # 组合至少20个样本
            continue
        sharpe = np.mean(rs) / max(np.std(rs), 1e-6) * np.sqrt(252 / 20)
        combo_stats.append({
            'combo': combo,
            'n': len(rs),
            'mean_ret': np.mean(rs),
            'median_ret': np.median(rs),
            'win_rate': np.mean(rs > 0),
            'sharpe': sharpe,
            'tail_10pct': np.mean(rs < -0.10),  # 亏损>10%概率
            'tail_15pct': np.mean(rs < -0.15),  # 亏损>15%概率
            'tail_20pct': np.mean(rs < -0.20),  # 亏损>20%概率
        })

    if combo_stats:
        cd = pd.DataFrame(combo_stats).sort_values('sharpe', ascending=False)
        print(f"  {'组合':>40s} {'N':>6s} {'均值':>8s} {'中位':>8s} {'胜率':>6s} {'夏普':>7s} {'>10%↓':>7s} {'>15%↓':>7s} {'>20%↓':>7s}")
        print(f"  {'─'*40} {'─'*6} {'─'*8} {'─'*8} {'─'*6} {'─'*7} {'─'*7} {'─'*7} {'─'*7}")
        for _, rw in cd.head(20).iterrows():
            print(f"  {rw['combo']:>40s} {rw['n']:>6,} "
                  f"{rw['mean_ret']:+7.2%} {rw['median_ret']:+7.2%} "
                  f"{rw['win_rate']:>5.1%} {rw['sharpe']:>6.2f} "
                  f"{rw['tail_10pct']:>6.1%} {rw['tail_15pct']:>6.1%} {rw['tail_20pct']:>6.1%}")

    # ── 5. 尾部风险汇总 ──
    print(f"\n{'─'*70}")
    print(f"  5. 尾部风险统计 (T+20 亏损概率)")
    print(f"{'─'*70}")
    print(f"  {'维度':>30s} {'N':>8s} {'亏损>10%':>10s} {'亏损>15%':>10s} {'亏损>20%':>10s}")
    print(f"  {'─'*30} {'─'*8} {'─'*10} {'─'*10} {'─'*10}")

    # 按regime
    for reg in ['BULL', 'BEAR', 'SIDEWAYS', 'BEAR_BOTTOM', 'BULL_TOP']:
        sub = df[df['regime_label'] == reg]
        rs = sub[rc20].dropna()
        if len(rs) < 5: continue
        print(f"  {'regime='+reg:<30s} {len(rs):>8,} "
              f"{np.mean(rs<-0.10):>9.1%} {np.mean(rs<-0.15):>9.1%} {np.mean(rs<-0.20):>9.1%}")

    # 按stage
    for stage_name in StageDetector.STAGE_NAMES:
        sub = df[df['stage'] == stage_name]
        rs = sub[rc20].dropna()
        if len(rs) < 10: continue
        print(f"  {'stage='+stage_name:<30s} {len(rs):>8,} "
              f"{np.mean(rs<-0.10):>9.1%} {np.mean(rs<-0.15):>9.1%} {np.mean(rs<-0.20):>9.1%}")

    # ── 6. MaxDD 分布 ──
    print(f"\n{'─'*70}")
    print(f"  6. MaxDD 分布 (按regime)")
    print(f"{'─'*70}")

    def compute_maxdd(ser):
        """从日收益率序列计算最大回撤"""
        cum = (1 + ser).cumprod()
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / peak
        return dd.min()

    for reg in ['BULL', 'BEAR', 'SIDEWAYS', 'BEAR_BOTTOM', 'BULL_TOP']:
        sub = df[df['regime_label'] == reg].copy()
        if len(sub) < 20:
            continue
        sub = sub.sort_values('trade_date')
        # 按T+20计算累计回撤（粗略估计: 用T+20收益序列做MaxDD）
        rs = sub[rc20].dropna()
        if len(rs) < 10:
            continue
        mdd = compute_maxdd(rs)
        print(f"  {reg:<14s} (n={len(rs):>6,}): MaxDD={mdd:.2%}")

    print(f"\n{'='*70}")
    print(f"  报告完毕")
    print(f"{'='*70}")

    return combo_stats


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='全周期回测框架')
    parser.add_argument('--sample', action='store_true', help='采样模式 (20只股票, 2022-2026)')
    parser.add_argument('--full', action='store_true', help='全量回测')
    parser.add_argument('--codes', nargs='+', help='指定股票代码列表')
    parser.add_argument('--start', default='2018-01-01', help='起始日期 (默认2018-01-01)')
    parser.add_argument('--end', default='2026-06-02', help='截止日期 (默认2026-06-02)')
    parser.add_argument('--forecast', type=int, nargs='+', default=[5, 10, 20, 40],
                        help='展望天数 (默认5 10 20 40)')
    parser.add_argument('--output', default=None, help='输出文件路径 (.pkl)')
    parser.add_argument('--save', action='store_true', help='保存结果到文件')

    args = parser.parse_args()

    if args.sample:
        print("\n🔬 采样模式启动: 20只股票, 2022-01-01 ~ 2026-06-02")
        engine = CycleBacktestEngine(
            sample=True, sample_count=20,
            start='2022-01-01', end='2026-06-02',
            forecast_days=args.forecast,
        )
    elif args.full:
        print("\n🚀 全量回测启动")
        engine = CycleBacktestEngine(
            sample=False,
            start=args.start, end=args.end,
            forecast_days=args.forecast,
        )
    elif args.codes:
        print(f"\n🎯 指定股票模式: {args.codes}")
        engine = CycleBacktestEngine(
            codes=args.codes,
            start=args.start, end=args.end,
            forecast_days=args.forecast,
        )
    else:
        parser.print_help()
        print("\n💡 提示: 使用 --sample 采样测试, 或 --full 全量运行")
        return

    # 运行回测
    df = engine.run()
    if df.empty:
        print("\n⚠️ 无数据，退出")
        return

    # 生成报告
    stats = generate_report(df)

    # 保存
    if args.save or args.output:
        out_path = args.output or f'/mnt/d/data/backtest/cycle_bt_{date.today()}.pkl'
        os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else '.', exist_ok=True)
        df.to_pickle(out_path)
        print(f"\n💾 结果已保存: {out_path}")
        if stats:
            stats_df = pd.DataFrame(stats).sort_values('sharpe', ascending=False)
            stats_path = out_path.replace('.pkl', '_stats.csv')
            stats_df.to_csv(stats_path, index=False)
            print(f"💾 统计已保存: {stats_path}")

    print("\n✅ 回测完成!")


if __name__ == '__main__':
    main()
