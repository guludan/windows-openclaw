#!/usr/bin/env python3
"""
V6 实战买入卖出规则 — 泡沫率 + 精确点位 + 退出规则

从 signal_qualifier 输出的信号，结合基本面+量价给具体操作指令。
"""
import psycopg2, numpy as np, json
from datetime import date, timedelta
import pandas as pd

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

class V6TradingRules:
    """实战交易规则引擎"""
    
    def __init__(self):
        self.conn = psycopg2.connect(DB)
        self.cur = self.conn.cursor()
        
        # 预加载行业成分映射
        self._load_industry()
        # 预加载财务数据（每只股票最新一季的ROE/BVPS/营收）
        self._load_financials()
        # 预加载行业财务中枢
        self._load_industry_medians()
    
    def _load_industry(self):
        self.cur.execute("SELECT stock_code, industry FROM industry_components_ths_l1")
        self.industry = {r[0]: r[1] for r in self.cur.fetchall()}
    
    def _load_financials(self):
        """加载每只股票最新一期财务数据"""
        self.cur.execute("""
            SELECT DISTINCT ON (symbol) symbol, end_date, roe, bvps, revenue, eps
            FROM financial_data
            WHERE symbol ~ '^[0-9]'
            ORDER BY symbol, end_date DESC
        """)
        self.finance = {}
        for r in self.cur.fetchall():
            # bvps 实际是净资产总额, eps 是净利总额
            # 用当前股价来算市盈率
            self.finance[r[0]] = {
                'end_date': r[1],
                'roe': r[2],         # ROE 是正常的
                'net_assets': r[3],  # 总净资产（元）
                'revenue': r[4],     # 总营收（元）
                'net_profit': r[5],  # 总净利（元）
            }
    
    def _load_industry_medians(self):
        """计算每个行业的 ROE/PE 中位数"""
        self.cur.execute("""
            WITH fin AS (
                SELECT DISTINCT ON (symbol) symbol, roe, eps, bvps
                FROM financial_data
                WHERE symbol ~ '^[0-9]'
                ORDER BY symbol, end_date DESC
            ),
            prices AS (
                SELECT symbol, close
                FROM daily_prices
                WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices)
            ),
            combined AS (
                SELECT i.industry, f.symbol,
                       f.roe,
                       CASE WHEN f.eps > 0 AND p.close > 0 
                            THEN p.close / (f.eps / 1e8) ELSE NULL END as pe_ttm,
                       CASE WHEN f.bvps > 0 AND p.close > 0
                            THEN p.close / (f.bvps / 1e8) ELSE NULL END as pb
                FROM fin f
                JOIN prices p ON f.symbol = 'A.' || p.symbol
                JOIN industry_components_ths_l1 i ON i.stock_code = f.symbol
            )
            SELECT industry,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY roe) as m_roe,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pe_ttm) as m_pe,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pb) as m_pb
            FROM combined
            WHERE roe IS NOT NULL
            GROUP BY industry
        """)
        self.industry_median = {}
        for r in self.cur.fetchall():
            self.industry_median[r[0]] = {
                'median_roe': r[1],
                'median_pe': r[2],
                'median_pb': r[3],
            }
    
    def calc_bubble(self, code):
        """
        计算泡沫率：价格相对基本面的偏离度
        
        输入: code='000001'
        输出: {
            'bubble_pct': 0~100,      # 泡沫百分数（越大越泡沫）
            'pe': xx,                  # 市盈率
            'pb': xx,                  # 市净率   
            'roe': xx,                 # ROE
            'industry_bias': xx,       # 行业偏离度（标准差倍数）
            'industry': '银行',
            'valuation_tier': '低估|合理|高估|泡沫',
        }
        """
        sym = 'A.' + code
        fin = self.finance.get(code)
        ind_name = self.industry.get(code)
        
        if not fin or not ind_name:
            return None
        
        # 最新价格
        self.cur.execute("""
            SELECT close FROM daily_prices 
            WHERE symbol = %s ORDER BY trade_date DESC LIMIT 1
        """, (sym,))
        row = self.cur.fetchone()
        if not row:
            return None
        price = row[0]
        
        # PE（净利 -> 每股）
        # fin['net_profit'] 是元级数据（如 131442000000）
        # 需要股价/每股净利
        net_profit = fin.get('net_profit', None)
        
        # 流通股数 = 净利 / EPS（每股净利）
        # 但这里 eps 实际上是净利总额，所以无法推导股数
        # 换一种方式：直接用净资产/bvps = 股数
        # bvps 实际上是净资产总额，所以 净资产/BVPS... 不行
        # 更简单：从市值算
        # 从 daily_prices 拿成交量信息不算股数
        # 用总市值估计
        self.cur.execute("""
            SELECT close, turnover FROM daily_prices 
            WHERE symbol = %s 
            ORDER BY trade_date DESC LIMIT 1
        """, (sym,))
        row2 = self.cur.fetchone()
        if not row2:
            return None
        close, turnover = row2
        # turnover 是换手率，没用
        
        # 用总资产负债算净资产总量
        net_assets = fin.get('net_assets', 0)  # total net assets (元)
        
        # 用行业平均 ROE -> PB -> 合理PB
        ind_median = self.industry_median.get(ind_name)
        
        # 用 PB 估值：PB = 总市值 / 净资产
        # 总市值 = 总股本 × 股价，但我们没总股本
        
        # 用 return on equity: 
        # 合理PB = ROE / 行业平均ROE × 行业平均PB
        
        roe = fin.get('roe', None)
        
        result = {
            'code': code,
            'price': close,
            'roe': roe,
            'industry': ind_name,
            'ind_median_roe': ind_median['median_roe'] if ind_median else None,
            'ind_median_pe': ind_median['median_pe'] if ind_median else None,
            'ind_median_pb': ind_median['median_pb'] if ind_median else None,
        }
        
        # PE: 没有准确股数的情况下，用 roe_based_pb 推算
        if roe and ind_median and ind_median['median_pb'] and ind_median['median_roe'] and ind_median['median_roe'] > 0:
            # 合理PB = 行业均值PB × (个股ROE / 行业ROE中位数)
            fair_pb = ind_median['median_pb'] * (roe / ind_median['median_roe'])
            
            # 实际PB（以4季前确认的总资产为基数... 没股数信息）
            # 用增长率估算
            # ROE × BV = 净利。PE = 股价 / (净利/总股本) 
            # 但我们有净利总额 net_profit 和总净资产 net_assets
            # 总股本 = 净利 / 每股净利... 还是卡在这里
            
            # PB = 总市值 / 总净资产
            # 没有总市值，但有价格和总净资产
            # 总股本 = 净资产 / BVPS ... BVPS 存的是净资产总额
            # 所以 BVPS_col = 总净资产，那 股价 / (净利/总净资产) ... 不对
            
            # 简化：用净利 / 净资产 = ROE
            # PE = 市值 / 净利 = PB / ROE
            # PB 我们不知道因为没总股本
            pass
        
        return result
    
    def buy_signal(self, code, s3_date, extra):
        """
        给出精确买入信号
        
        输入:
            code: '000001'
            s3_date: S3 触发日期
            extra: pool_energy extra dict (含 vol_z, v_z, ret_0 等)
        
        输出: {
            'action': 'buy' | 'wait' | 'pass',
            'entry': '明日开盘' | 'T+3确认后' | 'N/A',
            'target_price': xx,        # 建议买入价
            'stop_loss': xx,           # 止损价
            'exit_targets': [...],     # 止盈目标
            'bubble': '低估'|'合理'|'高估'|'泡沫',
            'position': '重仓'|'中等'|'轻仓'|'不参与',
            'reason': '...'
        }
        """
        sym = 'A.' + code
        
        # 1) 泡沫率
        bubble_info = self.calc_bubble(code)
        
        # 2) 均线支撑位（S3 触发前）
        self.cur.execute("""
            SELECT close, volume, high, low
            FROM daily_prices
            WHERE symbol = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (sym, s3_date - timedelta(days=30), s3_date))
        rows = self.cur.fetchall()
        if len(rows) < 5:
            return {'action': 'pass', 'reason': '数据不足'}
        
        df = pd.DataFrame(rows, columns=['close','volume','high','low'])
        
        # MA20 支撑
        ma20 = df['close'].rolling(20).mean().iloc[-1]
        ma60 = df['close'].rolling(60).mean().iloc[-min(len(df), 60)]
        if isinstance(ma60, pd.Series):
            ma60 = ma60.iloc[-1]
        
        # S3 当日最低价
        s3_low = df['low'].iloc[-1] if len(df) > 0 else df['low'].min()
        
        # 3) 确定买入策略
        vol_z = extra.get('vol_z', 0)
        v_z = extra.get('v_z', 0)
        ret_0 = extra.get('ret_0', 0)
        
        action = 'pass'
        entry = 'N/A'
        target_price = 0
        stop_loss = 0
        exit_targets = []
        position = '不参与'
        
        # 买入条件组合
        ma_level = max(ma20, ma60)
        
        # 安全边际：S3 最低价低于 MA 支撑 <= 15%
        discount = ma_level / s3_low - 1 if s3_low > 0 else 1
        
        if discount < 0.15 and discount > -0.05:
            # 接近或稍低于 MA 支撑
            if bubble_info and bubble_info.get('valuation_tier') in ['低估', '合理']:
                action = 'buy'
                entry = f'明日开盘约{s3_low*1.01:.2f}'
                target_price = round(s3_low * 1.015, 2)
                stop_loss = round(min(s3_low * 0.98, ma_level * 0.95), 2)
                exit_targets = [
                    round(s3_low * 1.08, 2),
                    round(s3_low * 1.15, 2),
                    round(s3_low * 1.25, 2),
                ]
                position = '中等'
        
        if action == 'buy' and vol_z > 4:
            # 极高波动 -> 降低仓位
            position = '轻仓'
        
        return {
            'action': action,
            'entry': entry,
            'target_price': target_price,
            'stop_loss': stop_loss,
            'exit_targets': exit_targets,
            'bubble': bubble_info,
            'position': position,
            'reason': f'S3触发: {vol_z:.1f}σ波动, 跌幅{ret_0:.1%}, MA支撑{ma_level:.2f}, S3最低{s3_low:.2f}'
        }
    
    def sell_signal(self, code, entry_price, entry_date):
        """
        给定买入价格和日期，持续追踪退出信号
        
        输出: {
            'action': 'hold' | 'sell' | 'stop',
            'exit_price': xx,
            'return': xx,
            'reason': '...'
        }
        """
        # 这个函数是给持仓后每日检查用的
        # 暂做退出规则框架
        pass
    
    def close(self):
        self.cur.close()
        self.conn.close()


def main():
    rules = V6TradingRules()
    
    # 用 4月13日的 S3 验证
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT e.level, e.date, e.extra
        FROM pool_energy e
        WHERE e.level LIKE 'code:A.%%'
          AND position('s3' in e.extra->>'sigs') > 0
          AND e.date = '2026-04-13'
        ORDER BY e.level
        LIMIT 5
    """)
    
    print("=" * 65)
    print("V6 实战买入信号 — 2026-04-13 S3")
    print("=" * 65)
    
    for r in cur.fetchall():
        code = r[0][7:]
        extra = json.loads(r[2]) if isinstance(r[2], str) else r[2]
        
        sig = rules.buy_signal(code, r[1], extra)
        print(f"\n📈 A.{code}")
        print(f"   action={sig['action']}, position={sig['position']}")
        print(f"   entry={sig['entry']}")
        print(f"   target={sig['target_price']:.2f}, stop={sig['stop_loss']:.2f}")
        print(f"   exits={sig['exit_targets']}")
        print(f"   reason={sig['reason']}")
        
        if sig['bubble']:
            b = sig['bubble']
            print(f"   行业={b['industry']}, ROE={b['roe']:.1%}")
    
    cur.close(); conn.close()
    rules.close()


if __name__ == '__main__':
    main()
