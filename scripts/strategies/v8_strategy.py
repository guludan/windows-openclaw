#!/usr/bin/env python3
"""
V8最终策略 — V8Strategy类

核心逻辑：
1. surplus_z三重微观信号状态机 → 牛熊态
2. Phase门控 → Phase动态矩阵查表
3. 价格结构弹簧（SMA偏离，行业密度动态调整阈值）
4. 资产价值硬锚（PB<1，重资产PB<0.5）
5. 行业密度加权（RARE/NORMAL/DENSE三档）

输出: 每只个股每日的买入信号、权重、止盈目标、止损线
"""
from __future__ import print_function
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, numpy as np, pandas as pd
from datetime import datetime, date, timedelta
from collections import OrderedDict

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'

# ===== V8 动态参数矩阵 =====
# Phase × 市场状态 → SMA阈值、PB条件、权重乘数
PARAMS_MATRIX = {
    'BULL': {
        'desc': '牛市 — 贝塔红利，整体放宽',
        'A_RECOVER':  {'sma': -0.05, 'pb': 1.0, 'w': 1.2},
        'A_ACCUM':    {'sma': -0.05, 'pb': 1.0, 'w': 1.0},
        'NEUTRAL':    {'sma': -0.10, 'pb': 1.0, 'w': 1.0},
        'B_ACCUM':    {'sma': -0.10, 'pb': 1.0, 'w': 0.8},
        'B_DISTRIB':  {'sma': -0.15, 'pb': 1.0, 'w': 0.6},
        'HI_EQ':      None,  # 关闭
    },
    'TOP_WARNING': {
        'desc': '冲顶预警 — 逐步收紧',
        'A_RECOVER':  {'sma': -0.05, 'pb': 1.0, 'w': 1.0},
        'A_ACCUM':    {'sma': -0.05, 'pb': 1.0, 'w': 1.0},
        'NEUTRAL':    {'sma': -0.10, 'pb': 1.0, 'w': 0.8},
        'B_ACCUM':    {'sma': -0.15, 'pb': 1.0, 'w': 0.6},
        'B_DISTRIB':  {'sma': -0.20, 'pb': 0.7, 'w': 0.5},
        'HI_EQ':      None,
    },
    'SIDEWAYS': {
        'desc': '震荡 — 基线，严控胜率',
        'A_RECOVER':  {'sma': -0.05, 'pb': 1.0, 'w': 1.0},
        'A_ACCUM':    {'sma': -0.05, 'pb': 1.0, 'w': 1.0},
        'NEUTRAL':    {'sma': -0.15, 'pb': 1.0, 'w': 1.0},
        'B_ACCUM':    {'sma': -0.15, 'pb': 1.0, 'w': 0.8},
        'B_DISTRIB':  {'sma': -0.20, 'pb': 1.0, 'w': 0.6},
        'HI_EQ':      None,
    },
    'BOTTOM_WARNING': {
        'desc': '底部预警 — 准备参与反弹',
        'A_RECOVER':  {'sma': -0.05, 'pb': 1.0, 'w': 1.2},
        'A_ACCUM':    {'sma': -0.05, 'pb': 1.0, 'w': 1.0},
        'NEUTRAL':    {'sma': -0.15, 'pb': 1.0, 'w': 1.0},
        'B_ACCUM':    {'sma': -0.15, 'pb': 1.0, 'w': 1.0},
        'B_DISTRIB':  {'sma': -0.20, 'pb': 1.0, 'w': 0.8},
        'HI_EQ':      None,
    },
    'BEAR': {
        'desc': '熊市 — 全面收窄，B_DISTRIB关闭',
        'A_RECOVER':  {'sma': -0.10, 'pb': 1.0, 'w': 0.7},
        'A_ACCUM':    {'sma': -0.10, 'pb': 1.0, 'w': 0.7},
        'NEUTRAL':    {'sma': -0.20, 'pb': 1.0, 'w': 0.6},
        'B_ACCUM':    {'sma': -0.25, 'pb': 0.7, 'w': 0.5},
        'B_DISTRIB':  None,  # 关闭
        'HI_EQ':      None,
    },
}

# 状态名修正映射（状态机输出 → PARAMS_MATRIX）
REGIME_ALIAS = {
    'BULL': 'BULL', 'TOP_WARNING': 'TOP_WARNING',
    'SIDEWAYS': 'SIDEWAYS', 'BOTTOM_WARNING': 'BOTTOM_WARNING',
    'BEAR': 'BEAR',
}

# 行业密度权重加成
DENSITY_WEIGHT = {'RARE': 1.5, 'NORMAL': 1.0, 'DENSE': 0.6}

# 重资产行业额外PB加严
DENSE_PB_HARDCORE = 0.5


class V8StateMachine:
    """surplus_z三重微观信号状态机"""
    
    TRANSITIONS = {
        'INIT': {
            'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > -0.5,
            'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < 0.5,
            'default': 'SIDEWAYS',
        },
        'BULL': {
            'TOP_WARNING': lambda sz5, sz20, sz, acc: (sz5 < sz20 and sz > 1.5) or (sz5 < sz20 and acc < -0.3 and sz > 0.5),
            'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 < sz20 and -1.0 < sz < 1.5 and abs(acc) < 0.3,
            'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -0.5,
            'default': 'BULL',
        },
        'TOP_WARNING': {
            'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > 0,
            'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -0.5,
            'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 < sz20 and -0.5 < sz < 1.5 and acc > -0.3,
            'default': 'TOP_WARNING',
        },
        'SIDEWAYS': {
            'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > 0.5,
            'TOP_WARNING': lambda sz5, sz20, sz, acc: sz > 2.0 and acc < -0.5,
            'BOTTOM_WARNING': lambda sz5, sz20, sz, acc: sz < -2.0 and acc > 0.5,
            'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -1.0 and acc < 0,
            'default': 'SIDEWAYS',
        },
        'BOTTOM_WARNING': {
            'BULL': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz > -0.5,
            'BEAR': lambda sz5, sz20, sz, acc: sz5 < sz20 and sz < -1.5,
            'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 > sz20 and -1.5 < sz < -0.5,
            'default': 'BOTTOM_WARNING',
        },
        'BEAR': {
            'BOTTOM_WARNING': lambda sz5, sz20, sz, acc: sz5 > sz20 and sz < -0.5,
            'SIDEWAYS': lambda sz5, sz20, sz, acc: sz5 > sz20 and -0.5 < sz < 0.5,
            'default': 'BEAR',
        },
    }
    
    @staticmethod
    def resolve(current, sz5, sz20, sz, acc):
        trans = V8StateMachine.TRANSITIONS.get(current, V8StateMachine.TRANSITIONS['INIT'])
        for target, cond in trans.items():
            if target == 'default': continue
            try:
                if cond(sz5, sz20, sz, acc):
                    return REGIME_ALIAS.get(target, target)
            except:
                continue
        return REGIME_ALIAS.get(trans.get('default', 'SIDEWAYS'), 'SIDEWAYS')


class V8Strategy:
    """V8 多层共振策略"""
    
    def __init__(self):
        self.state_machine = V8StateMachine()
        self.conn = None
        
    def load_regime_series(self):
        """从pool_energy.extra读取预计算的regime（pool_regime_integration.py写入）"""
        self.conn = psycopg2.connect(DB)
        cur = self.conn.cursor()
        cur.execute(
            """SELECT date, extra->>'regime' FROM pool_energy 
               WHERE level='market' AND extra IS NOT NULL AND extra ? 'regime'
               ORDER BY date"""
        )
        rows = cur.fetchall()
        cur.close()
        
        states = {}
        for r in rows:
            if r[1]:
                states[r[0]] = r[1]
        
        if not states:
            # 降级：自己计算（旧路径作为fallback）
            print("WARN: pool_energy无regime数据，降级到自算")
            return self._load_regime_legacy()
        
        self.regime_map = states
        return states
    
    def _load_regime_legacy(self):
        """旧版：自算surplus_z+状态机（降级fallback）"""
        self.conn = psycopg2.connect(DB)
        cur = self.conn.cursor()
        cur.execute("SELECT date, surplus FROM pool_energy WHERE level='market' ORDER BY date")
        rows = cur.fetchall()
        cur.close()
        
        dates = [r[0] for r in rows]
        surplus = np.array([float(r[1] or 0) for r in rows], dtype=float)
        n = len(surplus)
        
        sz = np.full(n, np.nan)
        for i in range(59, n):
            seg = surplus[i-59:i+1]
            mu, sd = np.mean(seg), np.std(seg)
            if sd > 0:
                sz[i] = (surplus[i] - mu) / sd
        
        def thr_sma(arr, w):
            res = np.full(n, np.nan)
            for i in range(len(arr)):
                if i < w-1 or np.isnan(arr[i]): continue
                seg = arr[i-w+1:i+1]
                if np.any(np.isnan(seg)): continue
                res[i] = np.mean(seg)
            return res
        
        sz_5 = thr_sma(sz, 5)
        sz_20 = thr_sma(sz, 20)
        accel = np.full(n, np.nan)
        for i in range(2, n):
            if not np.isnan(sz[i]) and not np.isnan(sz[i-1]) and not np.isnan(sz[i-2]):
                accel[i] = sz[i] - 2*sz[i-1] + sz[i-2]
        
        states = {}
        for i in range(n):
            if not np.isnan(sz_5[i]) and not np.isnan(sz_20[i]):
                break
        
        for i in range(n):
            d = dates[i]
            if np.isnan(sz_5[i]) or np.isnan(sz_20[i]) or np.isnan(sz[i]):
                states[d] = states.get(dates[i-1], 'SIDEWAYS') if i > 0 else 'SIDEWAYS'
                continue
            acc = 0.0 if np.isnan(accel[i]) else accel[i]
            prev = states.get(dates[i-1], 'SIDEWAYS')
            states[d] = V8StateMachine.resolve(prev, sz_5[i], sz_20[i], sz[i], acc)
        
        self.regime_map = states
        return states
    
    def get_params(self, regime, phase):
        """获取给定regime和phase的参数"""
        matrix = PARAMS_MATRIX.get(regime, PARAMS_MATRIX['SIDEWAYS'])
        params = matrix.get(phase)
        if params is None:
            return None  # 该Phase在当前regime下关闭
        return params.copy()
    
    def generate_signals(self, date_start, date_end=None, verbose=False):
        """生成V8信号"""
        if not hasattr(self, 'regime_map'):
            self.load_regime_series()
        
        if date_end is None:
            date_end = date_start
            
        if self.conn is None or self.conn.closed:
            self.conn = psycopg2.connect(DB)
        
        cur = self.conn.cursor()
        
        # 加载行业密度
        cur.execute("SELECT stock_code, bk_density_level FROM industry_components_ths_l1")
        density_map = {r[0]: r[1] for r in cur.fetchall()}
        
        # 加载BVPS
        cur.execute("SELECT DISTINCT ON (symbol) symbol, bvps FROM financial_data WHERE bvps IS NOT NULL AND bvps>0 ORDER BY symbol, end_date DESC")
        bvps_map = {r[0]: r[1] for r in cur.fetchall()}
        
        # 日线和SMA120
        symbols = [s for s in bvps_map if s.startswith('A.')]
        
        signals = []
        
        # 逐只股票处理
        for sym in symbols[:100]:  # 限制数量以控制时间
            code = sym[2:]  # A.xxxxxx → xxxxxx
            density = density_map.get(code, 'NORMAL')
            bvps = bvps_map[sym]
            
            cur.execute("""
                SELECT trade_date, close FROM daily_prices 
                WHERE symbol=%s AND close>0 
                ORDER BY trade_date
            """, (sym,))
            rows = cur.fetchall()
            if len(rows) < 200: continue
            
            trade_dates = [r[0] for r in rows]
            closes = np.array([float(r[1]) for r in rows], dtype=float)
            n = len(closes)
            
            # SMA120
            cumsum = np.cumsum(closes)
            sma120 = np.full(n, np.nan)
            sma120[119] = cumsum[119]/120
            sma120[120:] = (cumsum[120:] - cumsum[:n-120]) / 120
            
            sma_dev = closes / sma120 - 1
            pb = closes / bvps
            
            # 前向收益
            ret5 = np.full(n, np.nan); ret20 = np.full(n, np.nan)
            ret5[:-5] = closes[5:]/closes[:-5]-1
            ret20[:-20] = closes[20:]/closes[:-20]-1
            
            # 加载phase
            cur.execute("SELECT date, phase FROM pool_energy WHERE level='market' AND date>=%s AND date<=%s ORDER BY date",
                       (trade_dates[119], trade_dates[-1]))
            phase_map = {r[0]: r[1] for r in cur.fetchall()}
            
            prev_regime = 'SIDEWAYS'
            prev_holding = None  # (entry_price, sma120_at_entry, entry_date)
            
            for i in range(200, n):
                d = trade_dates[i]
                if d < date_start or d > date_end:
                    continue
                if np.isnan(ret20[i]): continue
                if np.isnan(sma120[i]) or sma120[i] <= 0: continue
                
                regime = self.regime_map.get(d, 'SIDEWAYS')
                phase = phase_map.get(d, 'NEUTRAL')
                
                params = self.get_params(regime, phase)
                if params is None:
                    continue  # Phase关闭
                
                t_sma = params['sma']
                t_pb = params['pb']
                w = params['w']
                
                # 行业密度调整
                if density == 'RARE':
                    t_sma *= 0.8
                    w *= DENSITY_WEIGHT['RARE']
                elif density == 'DENSE':
                    if pb[i] >= DENSE_PB_HARDCORE:
                        continue  # 重资产PB不够深
                    w *= DENSITY_WEIGHT['DENSE']
                else:
                    w *= DENSITY_WEIGHT['NORMAL']
                
                # 入场检查
                if sma_dev[i] < t_sma and pb[i] < t_pb:
                    # 入场信号
                    entry_px = closes[i]
                    target1 = sma120[i] * 0.95  # SMA-5%
                    target2 = sma120[i]         # SMA回归
                    stop_loss = entry_px * 0.95
                    
                    signals.append({
                        'date': d, 'code': code, 'industry': density,
                        'close': float(entry_px), 'sma120': float(sma120[i]),
                        'sma_dev': float(sma_dev[i]), 'pb': float(pb[i]),
                        'regime': regime, 'phase': phase,
                        'weight': round(w, 2),
                        'target1': float(target1), 'target2': float(target2),
                        'stop_loss': float(stop_loss),
                        'ret5': float(ret5[i]) if not np.isnan(ret5[i]) else None,
                        'ret20': float(ret20[i]) if not np.isnan(ret20[i]) else None,
                    })

        self.conn.close()
        return signals


def get_daily_regime(date_obj):
    """获取给定日期的regime（供外部调用）"""
    strategy = V8Strategy()
    strategy.load_regime_series()
    return strategy.regime_map.get(date_obj, 'SIDEWAYS')
