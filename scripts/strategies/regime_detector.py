#!/usr/bin/env python3
"""
牛熊识别模块 — 基于surplus_z微观结构信号判定Regime

直接从pool_energy.extra读取sz_5/sz_20/accel/surplus_z,
应用V8状态机规则判定当前Regime和预警信号。

规则:
  sz > +2.0 且 加速度为负 → TOP_WARNING (牛转熊预警)
  sz < -2.0 且 加速度为正 → BOTTOM_WARNING (熊转牛预警)
  sz_5金叉sz_20(sz_5 > sz_20) + sz < 0 + 加速度为正 → BOTTOM_WARNING
  sz_5金叉sz_20 + sz > 0 → BULL
  sz_5死叉sz_20(sz_5 < sz_20) + sz > +2.0 + 加速度为负 → TOP_WARNING
  sz_5死叉sz_20 + sz < -1.0 → BEAR
  sz > +2.0 → BULL
  默认 → 沿用上一日regime (通常SIDEWAYS)

输出: dict
"""
import sys, json
sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2
import numpy as np
from datetime import date, timedelta

DB = 'host=192.168.3.14 dbname=guludan user=guludan password=toto'


class RegimeDetector:
    """牛熊识别器"""
    
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
    
    def get_current_regime(self, target_date=None):
        """
        获取当前Regime + 信号
        target_date: date对象，默认当天
        """
        if target_date is None:
            target_date = date.today()
        
        # 从pool_energy取最新数据
        self.cur.execute("""
            SELECT date, extra->>'regime', extra->>'surplus_z', 
                   extra->>'sz_5', extra->>'sz_20', extra->>'accel'
            FROM pool_energy 
            WHERE level='market' AND extra ? 'regime' 
              AND date <= %s
            ORDER BY date DESC LIMIT 1
        """, (target_date,))
        row = self.cur.fetchone()
        
        if not row:
            return {'regime': 'SIDEWAYS', 'signal': '数据不足', 'surplus_z': 0, 'sz_5': 0, 'sz_20': 0, 'accel': 0}
        
        d, prev_regime, sz, sz5, sz20, accel = row
        
        surplus_z = float(sz) if sz else 0
        sz_5 = float(sz5) if sz5 else 0
        sz_20 = float(sz20) if sz20 else 0
        accel = float(accel) if accel else 0
        
        # 从sz_5/sz_20推导均线交叉信息
        golden_cross = sz_5 > sz_20  # 金叉等效
        
        # 取前一日判断交叉方向变化
        self.cur.execute("""
            SELECT extra->>'sz_5', extra->>'sz_20'
            FROM pool_energy
            WHERE level='market' AND extra ? 'sz_5' AND extra ? 'sz_20'
            ORDER BY date DESC LIMIT 2 OFFSET 1
        """)
        prev_row = self.cur.fetchone()
        cross_changed = False
        if prev_row and prev_row[0] and prev_row[1]:
            sz5_p = float(prev_row[0])
            sz20_p = float(prev_row[1])
            cross_changed = (sz_5 > sz_20) != (sz5_p > sz20_p)
        
        # 应用V8状态机规则
        regime, signal = self._apply_rules(
            surplus_z, sz_5, sz_20, accel,
            golden_cross, cross_changed, prev_regime
        )
        
        return {
            'date': d,
            'regime': regime,
            'surplus_z': round(surplus_z, 4),
            'sz_5': round(sz_5, 4),
            'sz_20': round(sz_20, 4),
            'accel': round(accel, 4),
            'sz5_golden': golden_cross,
            'cross_changed': cross_changed,
            'prev_regime': prev_regime,
            'signal': signal,
        }
    
    def _apply_rules(self, sz, sz5, sz20, accel, golden, cross_changed, prev_regime):
        """应用V8状态机规则"""
        
        # 规则1: sz>+2.0 + accel<0 → TOP_WARNING
        if sz > 2.0 and accel < 0:
            return 'TOP_WARNING', f'sz={sz:.1f}>+2.0 加速度{accel:.2f}<0 → 牛转熊预警，减仓'
        
        # 规则2: sz<-2.0 + accel>0 → BOTTOM_WARNING
        if sz < -2.0 and accel > 0:
            return 'BOTTOM_WARNING', f'sz={sz:.1f}<-2.0 加速度{accel:.2f}>0 → 熊转牛预警，准备加仓'
        
        # 规则3: 金叉 + sz<0 + accel>0 → BOTTOM_WARNING
        if golden and sz < 0 and accel > 0:
            return 'BOTTOM_WARNING', f'金叉(sz5={sz5:.2f}>sz20={sz20:.2f}) + sz={sz:.1f}<0 + 加速度{accel:.2f}>0 → 熊转牛'
        
        # 规则4: 金叉 + sz>0 → BULL
        if golden and sz > 0:
            return 'BULL', f'金叉(sz5={sz5:.2f}>sz20={sz20:.2f}) + sz={sz:.1f}>0 → 牛市持续'
        
        # 规则5: 死叉 + sz>+2.0 + accel<0 → TOP_WARNING
        if not golden and sz > 2.0 and accel < 0:
            return 'TOP_WARNING', f'死叉(sz5={sz5:.2f}<sz20={sz20:.2f}) + sz={sz:.1f}>+2.0 + 加速度{accel:.2f}<0 → 牛转熊'
        
        # 规则6: 死叉 + sz<-1.0 → BEAR
        if not golden and sz < -1.0:
            return 'BEAR', f'死叉(sz5={sz5:.2f}<sz20={sz20:.2f}) + sz={sz:.1f}<-1.0 → 熊市持续'
        
        # 规则7: sz>+2.0 → BULL
        if sz > 2.0:
            return 'BULL', f'sz={sz:.1f}>+2.0 → 牛市'
        
        # 规则8: sz<-2.0 → BEAR
        if sz < -2.0:
            return 'BEAR', f'sz={sz:.1f}<-2.0 → 熊市'
        
        # 默认：沿用上一日
        return prev_regime or 'SIDEWAYS', f'无明显信号, 沿用上一日{prev_regime or "SIDEWAYS"}'


def regime_dynamic_params(regime):
    """Regime动态参数表"""
    params = {
        'BEAR': {
            'consec_required': 3,
            'extreme_threshold': -5,
            'safety_margin': 1.0,
            'l4_winrate': 70,
            'max_position': 0.6,
            'color': '🔴',
            'desc': '熊市极空区，严控仓位',
        },
        'BEAR_NORMAL': {
            'consec_required': 4,
            'extreme_threshold': -6,
            'safety_margin': 1.0,
            'l4_winrate': 65,
            'max_position': 0.4,
            'color': '🟠',
            'desc': '正常熊市，谨慎入场',
        },
        'BOTTOM_WARNING': {
            'consec_required': 2,
            'extreme_threshold': -4,
            'safety_margin': 0.95,
            'l4_winrate': 60,
            'max_position': 0.8,
            'color': '🟢',
            'desc': '熊转牛预警，可积极加仓',
        },
        'BULL': {
            'consec_required': 0,
            'extreme_threshold': -10,
            'safety_margin': 0.85,
            'l4_winrate': 55,
            'max_position': 1.0,
            'color': '🟢',
            'desc': '牛市，积极入场',
        },
        'SIDEWAYS': {
            'consec_required': 3,
            'extreme_threshold': -5,
            'safety_margin': 1.0,
            'l4_winrate': 60,
            'max_position': 0.3,
            'color': '🟡',
            'desc': '震荡市，控制仓位',
        },
        'TOP_WARNING': {
            'consec_required': 0,
            'extreme_threshold': 0,
            'safety_margin': 0,
            'l4_winrate': 0,
            'max_position': 0.0,
            'color': '🔴',
            'desc': '牛转熊预警，不开新仓',
        },
    }
    return params.get(regime, params['SIDEWAYS'])


if __name__ == '__main__':
    conn = psycopg2.connect(DB)
    detector = RegimeDetector(conn)
    
    target = date(2026, 6, 5)
    result = detector.get_current_regime(target)
    
    print(f"{'='*60}")
    print(f"牛熊识别 — {target}")
    print(f"{'='*60}")
    print(f"Regime: {result['regime']}")
    print(f"surplus_z: {result['surplus_z']}")
    print(f"sz_5: {result['sz_5']}  sz_20: {result['sz_20']}  sz5金叉: {result['sz5_golden']}")
    print(f"accel: {result['accel']}  交叉变化: {result['cross_changed']}")
    print(f"信号: {result['signal']}")
    
    params = regime_dynamic_params(result['regime'])
    print(f"\nRegime参数:")
    print(f"  连跌要求: {params['consec_required']}天")
    print(f"  极端跌: {params['extreme_threshold']}%")
    print(f"  L4胜率要求: {params['l4_winrate']}%")
    print(f"  仓位上限: {params['max_position']*100:.0f}%")
    print(f"  说明: {params['desc']}")
    
    conn.close()
