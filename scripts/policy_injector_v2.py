#!/usr/bin/env python3
"""
policy_injector_v2.py — V6注入调节器 v2

基于一手信源（高管增减持/龙虎榜机构行为）调节S3信号的注入强度

核心发现（2026-05-25 回测确认）:
  S3 + 7d内高管增持 → +26.04% T+20, 92.9%胜率 → inject_pct +15~20%
  S3 + 7d内高管减持 → +3.54% T+20, 38.1%胜率 → inject_pct -6~8%
  S3 + 龙虎榜(当日) → -24.66% T+20, 6.2%胜率 → 直接回避
"""
import psycopg2
from datetime import date, timedelta

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

class PolicyInjectorV2:
    def __init__(self):
        self.conn = psycopg2.connect(DB)
    
    def close(self):
        self.conn.close()
    
    def check_incr_decr(self, stock_code: str, trade_date: date, lookback: int = 7) -> tuple:
        """返回 (has_incr, has_decr, total_incr, total_decr)"""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(CASE WHEN is_increase THEN abs_change ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN is_decrease THEN abs_change ELSE 0 END), 0),
                   COUNT(*) FILTER (WHERE is_increase),
                   COUNT(*) FILTER (WHERE is_decrease)
            FROM incr_decr
            WHERE code = %s AND change_date BETWEEN %s AND %s
        """, (stock_code, trade_date - timedelta(days=lookback), trade_date - timedelta(days=1)))
        total_incr, total_decr, n_incr, n_decr = cur.fetchone()
        cur.close()
        return (n_incr > 0, n_decr > 0, total_incr, total_decr)
    
    def check_lhb(self, stock_code: str, trade_date: date, lookback: int = 0) -> tuple:
        """检查龙虎榜（当天）"""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(inst_net_buy), 0), COUNT(*)
            FROM csi_lhb_detail
            WHERE code = %s AND trade_date = %s
        """, (stock_code, trade_date))
        inst_net, n_entry = cur.fetchone()
        cur.close()
        return (n_entry > 0, inst_net)
    
    def get_inject_adjustment(self, stock_code: str, trade_date: date) -> dict:
        """
        返回注入调节建议
        
        Returns:
            dict: { 'tier': int, 'adjustment': float, 'reason': str }
            tier: 1(强增强) / 2(中等) / 3(抑制) / -1(回避)
            adjustment: 注入强度调整百分比（相对基线）
        """
        incr, decr, total_incr, total_decr = self.check_incr_decr(stock_code, trade_date, lookback=7)
        on_lhb, inst_net = self.check_lhb(stock_code, trade_date)
        
        # 规则1: 龙虎榜直接回避
        if on_lhb:
            return {'tier': -1, 'adjustment': -100, 'reason': f'龙虎榜当日上榜(inst_net={inst_net:+.0f})'}
        
        # 规则2: 高管增持 → 强增强
        if incr and not decr:
            if total_incr >= 5000:
                return {'tier': 1, 'adjustment': 20, 'reason': f'7d内高管增持{total_incr}股'}
            else:
                return {'tier': 2, 'adjustment': 10, 'reason': f'7d内小额增持{total_incr}股'}
        
        # 规则3: 高管减持 → 抑制
        if decr and not incr:
            return {'tier': 3, 'adjustment': -8, 'reason': f'7d内高管减持{total_decr}股'}
        
        # 规则4: 都有 → 看净方向
        if incr and decr:
            if total_incr > total_decr:
                return {'tier': 2, 'adjustment': 8, 'reason': f'增>减({total_incr}vs{total_decr})'}
            else:
                return {'tier': 3, 'adjustment': -5, 'reason': f'减>增({total_decr}vs{total_incr})'}
        
        # 规则5: 无信号 → 基线
        return {'tier': 0, 'adjustment': 0, 'reason': '无一手信号'}

# 测试
if __name__ == '__main__':
    pi = PolicyInjectorV2()
    # 测试几个已知S3信号
    test_cases = [
        ('601318', date(2024, 1, 22)),
        ('600519', date(2024, 1, 22)),
        ('600036', date(2024, 1, 22)),
        ('603551', date(2023, 9, 6)),  # 有减持案例
    ]
    print("测试 policy_injector_v2:")
    for code, d in test_cases:
        adj = pi.get_inject_adjustment(code, d)
        print(f"  {code} @ {d}: tier={adj['tier']}, adj={adj['adjustment']:+.0f}% — {adj['reason']}")
    pi.close()
