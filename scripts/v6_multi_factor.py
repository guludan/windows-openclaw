#!/usr/bin/env python3
"""
v6_multi_factor.py — V6 三层多因子信号系统

候选池（S3）→ 定性筛（增减持+龙虎榜+post_vol+板块超额+财报期）→ 进场规则（ATR止损）

第一层: S3 信号（现有 stock_energy_full.py 输出，不再重复实现）
第二层: 定性筛（本脚本，将每只 S3 股票打分）
第三层: 进场规则（本脚本，输出精确买卖建议）
"""
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/scripts')
import psycopg2, psycopg2.extras, json, math, time
from datetime import date, timedelta
import numpy as np
import pandas as pd
from collections import defaultdict

DB = "host=192.168.3.14 dbname=guludan user=guludan password=toto"

# —————————————————————————————————————————————
# 行业映射：同花顺 → 中证 (sector_daily)
# —————————————————————————————————————————————
# 预缓存：行业超额计算结果
_INDUSTRY_CACHE = {}

THS_TO_ZS = {
    # 半导体/电子
    '半导体': '电子', '光学光电子': '电子', '消费电子': '电子', '元件': '电子',
    '其他电子': '电子',
    # 计算机/通信
    '计算机设备': '计算机', 'IT服务': '计算机', '软件开发': '计算机',
    '通信设备': '通信', '通信服务': '通信', '计算机应用': '计算机',
    # 军工
    '军工电子': '国防军工', '军工装备': '国防军工',
    # 机械/设备
    '通用设备': '机械设备', '专用设备': '机械设备', '自动化设备': '机械设备',
    '电机': '机械设备', '仪器仪表': '机械设备', '工程机械': '机械设备',
    # 汽车
    '汽车零部件': '汽车', '汽车整车': '汽车', '汽车服务': '汽车',
    # 化工
    '化学制品': '基础化工', '农化制品': '基础化工', '化学原料': '基础化工',
    '化学纤维': '基础化工', '橡胶': '基础化工', '塑料': '基础化工',
    # 电力/新能源
    '电力': '公用事业', '电池': '电力设备', '光伏设备': '电力设备',
    '电网设备': '电力设备', '风电设备': '电力设备', '其他电源设备': '电力设备',
    # 医药
    '化学制药': '医药生物', '中药': '医药生物', '生物制品': '医药生物',
    '医药商业': '医药生物', '医疗器械': '医药生物', '医疗服务': '医药生物',
    # 金属/材料
    '工业金属': '有色金属', '小金属': '有色金属', '贵金属': '有色金属',
    '金属新材料': '有色金属', '钢铁': '钢铁',
    # 建筑/地产
    '房地产开发': '房地产', '房地产服务': '房地产',
    '建筑装饰': '建筑装饰', '建筑材料': '建筑材料',
    '工程咨询服务': '建筑装饰',
    # 能源
    '煤炭开采': '煤炭', '煤炭': '煤炭',
    '石油加工炼化': '石油石化', '油服工程': '石油石化',
    '燃气': '公用事业', '电力': '公用事业',
    # 消费
    '食品加工制造': '食品饮料', '饮料制造': '食品饮料',
    '纺织制造': '纺织服饰', '服装家纺': '纺织服饰',
    '家用轻工': '家用电器', '白色家电': '家用电器', '黑色家电': '家用电器',
    '小家电': '家用电器', '厨卫电器': '家用电器',
    '汽车服务': '汽车',
    # 金融
    '银行': '银行', '保险': '非银金融', '证券': '非银金融',
    '多元金融': '非银金融',
    # 交运
    '公路铁路运输': '交通运输', '航空机场': '交通运输', '航运港口': '交通运输',
    '物流': '交通运输',
    # 农林牧渔
    '养殖业': '农林牧渔', '种植业': '农林牧渔', '渔业': '农林牧渔',
    '农产品加工': '农林牧渔', '农业综合': '农林牧渔', '饲料': '农林牧渔',
    # 其他
    '环保': '环保', '环境治理': '环保',
    '零售': '商贸零售', '贸易': '商贸零售', '一般零售': '商贸零售',
    '互联网电商': '商贸零售',
    '传媒': '传媒', '游戏': '传媒', '广告营销': '传媒', '影视院线': '传媒',
    '出版': '传媒',
    '社会服务': '社会服务', '教育': '社会服务', '酒店餐饮': '社会服务',
    '旅游及景区': '社会服务', '其他社会服务': '社会服务',
    '美容护理': '美容护理',
    '造纸': '轻工制造', '包装印刷': '轻工制造',
    '综合': '综合',
}

# —————————————————————————————————————————————
# 核心类
# —————————————————————————————————————————————
class V6MultiFactor:
    """V6 三层多因子信号系统"""

    def __init__(self, conn=None):
        if conn is None:
            self.conn = psycopg2.connect(DB)
        else:
            self.conn = conn
        self.cur = self.conn.cursor()

    def fetch_s3_signals(self, start_date, end_date):
        """从 pool_energy 抓取 S3 信号"""
        self.cur.execute("""
            SELECT e.level, e.date, e.extra
            FROM pool_energy e
            WHERE e.level LIKE 'code:A.%%'
              AND position('s3' in e.extra->>'sigs') > 0
              AND e.date BETWEEN %s AND %s
            ORDER BY e.date, e.level
        """, (start_date, end_date))
        return [{'level': r[0], 'date': r[1], 'extra': json.loads(r[2]) if isinstance(r[2], str) else r[2]}
                for r in self.cur.fetchall()]

    def fetch_industry_map(self):
        """加载同花顺行业映射"""
        self.cur.execute("SELECT stock_code, industry FROM industry_components_ths_l1")
        result = {}
        for r in self.cur.fetchall():
            # 映射到中证行业
            zs = THS_TO_ZS.get(r[1], None)
            result[r[0]] = {'ths': r[1], 'zs': zs}
        return result

    def precalc_all_industry_excess(self, signals, ind_map):
        """全量预加载 87 个同花顺行业在信号日期的超额收益
        全量一次性查（0.16s/日期），S1/S2/S3 共享缓存
        """
        global _INDUSTRY_CACHE
        
        signal_dates = sorted(set(s['date'] for s in signals))
        if not signal_dates:
            return
        
        # 只算缓存未命中的日期
        need_dates = [sd for sd in signal_dates 
                       if not any(k[0] == sd for k in _INDUSTRY_CACHE)]
        if not need_dates:
            return
        
        import time as _t
        _t0 = _t.time()
        
        # 一次拉所有交易日
        self.cur.execute("""
            SELECT DISTINCT trade_date FROM daily_prices 
            WHERE trade_date BETWEEN '2025-06-01' AND '2026-05-25'
            ORDER BY trade_date
        """)
        all_tds = [r[0] for r in self.cur.fetchall()]
        
        # 全部 87 个同花顺行业
        self.cur.execute("SELECT DISTINCT industry FROM industry_components_ths_l1 ORDER BY industry")
        all_industries = [r[0] for r in self.cur.fetchall()]
        ind_str = "','".join(all_industries)
        
        # Python 找 td/td20
        def find_td(sd):
            for i, d in enumerate(all_tds):
                if d >= sd:
                    if i >= 20:
                        return all_tds[i], all_tds[i - 20]
                    break
            return None, None
        
        date_map = {}
        for sd in need_dates:
            td, td20 = find_td(sd)
            if td:
                date_map[sd] = (td, td20)
        
        print(f"  全量行业超额: {len(all_industries)} 行业 × {len(date_map)} 天...")
        
        for sd, (td, td20) in date_map.items():
            td_str = str(td)
            td20_str = str(td20)
            
            self.cur.execute(f"""
                SELECT i.industry,
                       AVG(d_now.close / NULLIF(d_20.close, 0) - 1),
                       COUNT(*)
                FROM daily_prices d_now
                JOIN daily_prices d_20 ON d_20.symbol = d_now.symbol AND d_20.trade_date = '{td20_str}'
                JOIN industry_components_ths_l1 i ON i.stock_code = substring(d_now.symbol, 3)
                WHERE d_now.trade_date = '{td_str}' AND d_now.close > 0 AND d_20.close > 0
                  AND i.industry IN ('{ind_str}')
                GROUP BY i.industry
            """)
            rows = self.cur.fetchall()
            
            weighted_sum = 0.0
            total_cnt = 0
            ind_data = {}
            
            for ind_name, ind_ret, cnt in rows:
                if ind_ret is None:
                    continue
                ind_data[ind_name] = ind_ret
                weighted_sum += ind_ret * cnt
                total_cnt += cnt
                _INDUSTRY_CACHE[(sd, ind_name)] = {
                    'daily_excess': 0,
                    'ind_ret_20': ind_ret,
                    'mkt_ret_20': 0,
                    'cnt': cnt,
                    'trade_date': td,
                    'ths_industry': ind_name,
                }
            
            mkt_ret = weighted_sum / total_cnt if total_cnt > 0 else 0
            
            # 更新所有行业的 excess
            for ind_name in ind_data:
                v = _INDUSTRY_CACHE[(sd, ind_name)]
                v['daily_excess'] = v['ind_ret_20'] - mkt_ret
                v['mkt_ret_20'] = mkt_ret
        
        _t2 = _t.time()
        print(f"  缓存完成: {len(_INDUSTRY_CACHE)} 组 ({_t2-_t0:.1f}s)")

    def classify_s1(self, code, signal_date, extra, ind_map):
        """S1 定性分类：phase-aware，B/NEUTRAL 侧用现有逻辑，A 侧退化为左侧信号"""
        phase = self.get_market_phase(signal_date)
        ret_0 = extra.get('ret_0', 0)
        abs_ret = abs(ret_0)
        vol_z = extra.get('vol_z', 0)
        
        # A 侧（下跌/底部修复）：S1 直接视为左侧信号，不需要板块共振
        if phase in ('A_ACCUM', 'A_RECOVER'):
            return {'type': 'A', 'score': 0.7, 'phase': phase,
                    'reason': f'{phase}: 左侧信号，直接升格'}
        
        # HI_EQ（罕见）：抑制
        if phase == 'HI_EQ':
            return {'type': 'D', 'score': 0.2, 'phase': phase,
                    'reason': 'HI_EQ: 抑制'}
        
        # B_DISTRIB：冲顶阶段，信号降权
        if phase == 'B_DISTRIB':
            ind_info = ind_map.get(code, {})
            ths = ind_info.get('ths')
            pl_excess = _INDUSTRY_CACHE.get((signal_date, ths), {}).get('daily_excess', 0) if ths else 0
            if abs_ret > 0.02:
                return {'type': 'C', 'score': 0.4, 'phase': phase,
                        'reason': 'B_DISTRIB: 价格移动但冲顶降权'}
            return {'type': 'D', 'score': 0.2, 'phase': phase,
                    'reason': 'B_DISTRIB: 冲顶噪声'}
        
        # B_ACCUM / NEUTRAL：现有板块共振逻辑
        ind_info = ind_map.get(code, {})
        ths = ind_info.get('ths')
        pl_excess = _INDUSTRY_CACHE.get((signal_date, ths), {}).get('daily_excess', 0) if ths else 0
        
        if abs_ret > 0.03 and pl_excess > 0:
            return {'type': 'A', 'score': 0.8, 'phase': phase,
                    'reason': f'{phase}: 板块共振+价格移动=信息泄露'}
        elif abs_ret > 0.02:
            return {'type': 'B', 'score': 0.6, 'phase': phase,
                    'reason': f'{phase}: 价格移动但无板块共振'}
        elif pl_excess > 0:
            return {'type': 'C', 'score': 0.5, 'phase': phase,
                    'reason': f'{phase}: 板块共振但价格横盘=关注'}
        else:
            return {'type': 'D', 'score': 0.3, 'phase': phase,
                    'reason': f'{phase}: 无板块共振+无价格移动=噪声'}
    
    def check_fake_breakout(self, code, signal_date, extra):
        """第三层：假突破过滤（无需分时数据）
        使用 daily_prices 的 high/low/open/close
        """
        sym = 'A.' + code
        ret_0 = extra.get('ret_0', 0)
        sigs = extra.get('sigs', '')
        ret = {'fake_break': False, 'fake_reasons': []}
        
        if 's2' not in sigs and ret_0 < 0.05:
            # S2 以外的信号或涨幅不到5%，不做假突破检查
            return ret
        
        self.cur.execute("""
            SELECT trade_date, open, high, low, close FROM daily_prices
            WHERE symbol = %s AND trade_date = %s
        """, (sym, signal_date))
        today = self.cur.fetchone()
        if not today:
            return ret
        
        _, open_p, high, low, close = today
        
        # 1. 炸板检查：close < high * 0.93（日内从最高回落超7%）
        if close < high * 0.93 and ret_0 > 0.05:
            ret['fake_break'] = True
            ret['fake_reasons'].append(f'炸板(close={close:.2f}<high*0.93={high*0.93:.2f})')
        
        # 2. 封板检查：close ≈ high 且涨超9%
        if close >= high * 0.99 and ret_0 > 0.09:
            ret['limit_up'] = True
        
        # 3. 次日开盘检查
        self.cur.execute("""
            SELECT trade_date, open, close FROM daily_prices
            WHERE symbol = %s AND trade_date > %s
            ORDER BY trade_date LIMIT 1
        """, (sym, signal_date))
        next_day = self.cur.fetchone()
        if next_day and close > 0:
            open_ratio = next_day[1] / close - 1
            if open_ratio < -0.03:
                ret['fake_break'] = True
                ret['fake_reasons'].append(f'次日低开{open_ratio:.1%}')
            ret['next_open_ratio'] = open_ratio
        
        return ret

    def score_limit_precursor(self, code, signal_date):
        """拉板前兆评分：识别机构建仓和试盘痕迹
        不需要分时数据，只用 daily_prices OHLCV
        评分 0-100，>=60 为高确定性拉板前兆
        
        C1: 筹码锁定度 (0-40)
        C2: 资金时间窗口 (0-30)
        C3: 抛压测试结果 (0-30)
        """
        sym = 'A.' + code
        result = {'score': 0, 'lockup_days': 0, 'test_days': 0, 'window_left': 0}
        
        # 拉前 30 天数据
        self.cur.execute("""
            SELECT trade_date, open, high, low, close, volume
            FROM daily_prices
            WHERE symbol = %s AND trade_date < %s
            ORDER BY trade_date DESC LIMIT 30
        """, (sym, signal_date))
        rows = list(reversed(self.cur.fetchall()))
        if len(rows) < 20:
            return result
        
        closes = [r[4] for r in rows]
        volumes = [max(r[5], 1) for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        
        # 计算均量
        vol_ma20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
        prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
        
        # ---- C1: 筹码锁定度 ----
        lockup_count = 0
        for i in range(len(rows) - 1, -1, -1):
            v = volumes[i]
            dr = (highs[i] - lows[i]) / max(closes[i], 1)
            if v < vol_ma20 * 0.7 and dr < 0.02:
                lockup_count += 1
            else:
                break
        result['lockup_days'] = lockup_count
        if lockup_count >= 5:
            c1 = 40
        elif lockup_count >= 3:
            c1 = 25
        else:
            c1 = 10
        
        # ---- C2: 资金时间窗口 ----
        # 首次达到 lockup>=3 距今几天
        first_lockup = -1
        for i in range(len(rows) - 1, -1, -1):
            if i + 3 <= len(rows):
                v = volumes[i]
                dr = (highs[i] - lows[i]) / max(closes[i], 1)
                if v < vol_ma20 * 0.7 and dr < 0.02:
                    # 检查连续3天
                    ok = True
                    for j in range(i, min(i + 3, len(rows))):
                        vj = volumes[j]
                        drj = (highs[j] - lows[j]) / max(closes[j], 1)
                        if not (vj < vol_ma20 * 0.7 and drj < 0.02):
                            ok = False
                            break
                    if ok:
                        first_lockup = i
                        break
        
        if first_lockup >= 0:
            days_since_first_lockup = len(rows) - 1 - first_lockup
            if 3 <= days_since_first_lockup <= 8:
                c2 = 30
            elif 0 <= days_since_first_lockup <= 2:
                c2 = 20
            else:
                c2 = 0  # 窗口已过
            result['window_left'] = max(8 - days_since_first_lockup, 0)
        else:
            c2 = 15  # 无锁仓，中性
            result['window_left'] = 0
        
        # ---- C3: 抛压测试 ----
        test_found = False
        test_light = False
        for i in range(len(rows) - 1, -1, -1):
            rng = (highs[i] - lows[i]) / max(prev_close, 1)
            if i > 0:
                prev_c = closes[i-1]
            else:
                continue
            upper_test = (highs[i] - prev_c) / max(prev_c, 1) > 0.03
            ret_day = (closes[i] - prev_c) / max(prev_c, 1)
            if upper_test and abs(ret_day) < 0.01:
                test_found = True
                # 抛压轻：试盘日放量不大
                vol_ratio = volumes[i] / max(vol_ma20, 1)
                test_light = vol_ratio < 0.8
                break
        
        result['test_days'] = 1 if test_found else 0
        if test_found and test_light:
            c3 = 30
        elif test_found:
            c3 = 10
        else:
            c3 = 15  # 无试盘，中性
        
        result['score'] = c1 + c2 + c3
        result['details'] = {'c1_lockup': c1, 'c2_window': c2, 'c3_test': c3}
        return result

    def preload_market_phases(self, signal_dates):
        """批量预加载大盘相空间"""
        if not hasattr(self, '_market_phase_cache'):
            self._market_phase_cache = {}
        need = [sd for sd in signal_dates if sd not in self._market_phase_cache]
        if not need:
            return
        date_strs = "','".join(str(d) for d in need)
        self.cur.execute(f"""
            SELECT date, phase FROM pool_energy
            WHERE level = 'market' AND date IN ('{date_strs}')
        """)
        for d, ph in self.cur.fetchall():
            self._market_phase_cache[d] = ph or 'NEUTRAL'
    
    def get_market_phase(self, signal_date):
        if hasattr(self, '_market_phase_cache'):
            return self._market_phase_cache.get(signal_date, 'NEUTRAL')
        return 'NEUTRAL'

    def classify_s2(self, code, signal_date, extra, ind_map):
        """S2 定性分类：phase-aware 多空判定
        B_ACCUM → 做多；B_DISTRIB → 反转做空；A_RECOVER → 底部升格
        """
        phase = self.get_market_phase(signal_date)
        surplus = extra.get('surplus', 0)
        
        if phase in ('A_RECOVER',):
            # 底部分歧：S2是极强的底部确认信号
            return {'type': 'A', 'score': 0.9, 'phase': phase,
                    'direction': 'long',
                    'reason': 'A_RECOVER: 底部突破，升格A级强买入'}
        
        if phase in ('A_ACCUM',):
            # 下跌中 S2 信号稀少，抑制
            return {'type': 'D', 'score': 0.2, 'phase': phase,
                    'direction': 'neutral',
                    'reason': 'A_ACCUM: 抑制S2信号'}
        
        if phase in ('HI_EQ',):
            return {'type': 'D', 'score': 0.3, 'phase': phase,
                    'direction': 'neutral',
                    'reason': 'HI_EQ: 抑制S2信号'}
        
        if phase in ('B_DISTRIB',):
            # 冲顶阶段：S2脉冲反转做空
            if surplus > 0:
                return {'type': 'D', 'score': 0.5, 'phase': phase,
                        'direction': 'short',
                        'reason': 'B_DISTRIB+高盈余→反转做空'}
            return {'type': 'D', 'score': 0.4, 'phase': phase,
                    'direction': 'short',
                    'reason': 'B_DISTRIB+低盈余→反转做空'}
        
        if phase in ('B_ACCUM',):
            # 上涨趋势：正常做多
            if surplus > 0:
                return {'type': 'A', 'score': 0.7, 'phase': phase,
                        'direction': 'long',
                        'reason': 'B_ACCUM+高盈余→强趋势延续做多'}
            return {'type': 'B', 'score': 0.6, 'phase': phase,
                    'direction': 'long',
                    'reason': 'B_ACCUM但低盈余→谨慎做多'}
        
        # NEUTRAL: 中性，需要假突破过滤
        return {'type': 'C', 'score': 0.5, 'phase': phase,
                'direction': 'long',
                'reason': 'NEUTRAL→中性，需假突破过滤'}

    def calc_industry_excess(self, code, signal_date, ind_map):
        """从缓存取行业超额"""
        ind_info = ind_map.get(code, {})
        ths_industry = ind_info.get('ths')
        if not ths_industry:
            return None
        return _INDUSTRY_CACHE.get((signal_date, ths_industry))

    def calc_atr(self, code, signal_date, period=14):
        """计算 ATR(14) 动态止损"""
        sym = 'A.' + code
        self.cur.execute("""
            SELECT high, low, close
            FROM daily_prices
            WHERE symbol = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (sym, signal_date - timedelta(days=period*2), signal_date))
        rows = self.cur.fetchall()
        if len(rows) < period + 1:
            return None

        true_ranges = []
        prev_close = rows[0][2]
        for h, l, c in rows[1:]:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            true_ranges.append(tr)
            prev_close = c

        atr = np.mean(true_ranges[-period:])
        # 最新价格
        close = rows[-1][2]
        return {'atr': atr, 'close': close, 'atr_pct': atr / close if close > 0 else 0}

    def check_financial_window(self, code, signal_date):
        """检查 S3 前后 3 个交易日是否有财报公告"""
        self.cur.execute("""
            SELECT COUNT(*) FROM financial_data
            WHERE symbol = %s
              AND publish_date BETWEEN %s AND %s
        """, (code, signal_date - timedelta(days=3), signal_date + timedelta(days=3)))
        cnt = self.cur.fetchone()[0]
        return cnt > 0

    def check_incr_decr(self, code, signal_date):
        """检查 7天内增减持"""
        incr7 = decr7 = 0
        self.cur.execute("""
            SELECT is_increase, abs_change
            FROM incr_decr
            WHERE code = %s AND change_date BETWEEN %s AND %s
        """, (code, signal_date - timedelta(days=7), signal_date))
        for r in self.cur.fetchall():
            if r[0]: incr7 += r[1]
            else: decr7 += r[1]
        # 14天
        incr14 = incr7
        self.cur.execute("""
            SELECT is_increase, abs_change
            FROM incr_decr
            WHERE code = %s AND change_date BETWEEN %s AND %s
        """, (code, signal_date - timedelta(days=14), signal_date - timedelta(days=7)))
        for r in self.cur.fetchall():
            if r[0]: incr14 += r[1]
        return incr7, decr7, incr14

    def check_lhb(self, code, signal_date):
        """检查龙虎榜"""
        self.cur.execute("""
            SELECT COUNT(*) FROM csi_lhb_detail
            WHERE code = %s AND trade_date = %s
        """, (code, signal_date))
        return self.cur.fetchone()[0] > 0

    def calc_post_vol(self, code, signal_date):
        """计算 post_vol（暴跌后3日均量/暴跌日量）"""
        sym = 'A.' + code
        self.cur.execute("""
            SELECT trade_date, volume
            FROM daily_prices
            WHERE symbol = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (sym, signal_date, signal_date + timedelta(days=5)))
        rows = self.cur.fetchall()
        if len(rows) < 3:
            return None
        vol_0 = rows[0][1] if rows[0][1] > 0 else 1
        post_vol = np.mean([r[1] for r in rows[1:min(4, len(rows))]]) / vol_0
        return min(max(post_vol, 0), 5)  # 截断

    def hunt_prey(self, signal_date, min_s1_in_sector=2, max_cap=80, min_cap=15):
        """第四层：猎物筛选器
        每日盘后运行，输出次日观察名单
        
        条件：
        - 行业在近 30 天央视产业政策覆盖范围
        - 行业近 5 天有 >= min_s1_in_sector 只 S1 信号
        - 流通市值 min_cap ~ max_cap 亿
        - 近 60 天至少 1 次涨停
        - 近 20 天无机构龙虎榜净买入
        - 近 10 天日均换手 > 3%
        - 当前价 > MA60 且 MA60 上行
        - 近 5 天振幅缩小 < 3%
        - 近 3 天触发过 S1
        """
        result = {'date': signal_date, 'total': 0, 'candidates': []}
        
        # --- 1. 行业筛选：近30天央视政策覆盖 + 近5天S1活跃 ---
        # A. 从 event_list 找 CCTV 覆盖行业
        self.cur.execute("""
            SELECT DISTINCT UNNEST(STRING_TO_ARRAY(subcategory, ',')) AS industry
            FROM event_list
            WHERE source = 'news_cctv'
              AND event_date BETWEEN %s AND %s
              AND subcategory IS NOT NULL AND subcategory != ''
        """, (signal_date - timedelta(days=30), signal_date))
        cctv_industries = set(r[0].strip() for r in self.cur.fetchall() if r[0].strip())
        
        # B. 近5天各行业 S1 计数
        self.cur.execute("""
            SELECT ic.industry, COUNT(*) AS s1_count
            FROM pool_energy s1
            JOIN industry_components_ths_l1 ic ON ic.stock_code = SUBSTRING(s1.level FROM 8)
            WHERE s1.extra->>'sigs' LIKE '%%s1%%'
              AND s1.date BETWEEN %s AND %s
              AND s1.level LIKE 'code:%%'
              AND ic.industry IS NOT NULL
            GROUP BY ic.industry
        """, (signal_date - timedelta(days=5), signal_date))
        s1_by_sector = {r[0]: r[1] for r in self.cur.fetchall()}
        hot_sectors = set(s for s, c in s1_by_sector.items() if c >= min_s1_in_sector)
        
        # 取交集（或者热行业）
        target_industries = cctv_industries if len(cctv_industries) > 0 else hot_sectors
        if not target_industries:
            result['reason'] = '无可筛选行业'
            return result
        
        # --- 2. 成分股筛选 ---
        # 从 industry_components_ths_l1 拿行业内的股票
        sector_q = "','".join(list(target_industries))[:2000]
        self.cur.execute(f"""
            SELECT stock_code FROM industry_components_ths_l1
            WHERE industry IN ('{sector_q}')
        """)
        sector_codes = set(r[0] for r in self.cur.fetchall())
        
        if not sector_codes:
            result['reason'] = '行业成分股为空'
            return result
        
        # --- 3. 逐条件过滤 ---
        candidates = []
        for code in list(sector_codes)[:200]:  # 一次最多 200 只
            sym = 'A.' + code
            
            # 日线数据（近 60 天）
            self.cur.execute("""
                SELECT trade_date, open, high, low, close, volume
                FROM daily_prices
                WHERE symbol = %s AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date DESC
            """, (sym, signal_date - timedelta(days=65), signal_date))
            rows = list(reversed(self.cur.fetchall()))
            need_days = min(45, len(rows))
            if need_days < 30:
                continue
            
            closes = [r[4] for r in rows]
            volumes = [max(r[5], 1) for r in rows]
            highs = [r[2] for r in rows]
            lows = [r[3] for r in rows]
            latest_close = closes[-1]
            
            if latest_close <= 0:
                continue
            
            # 涨停次数（60天）
            ban_count = 0
            lookback = min(60, len(closes))
            for i in range(-lookback, 0):
                if i > 0:
                    prev = closes[i-1]
                else:
                    continue
                if prev > 0 and (closes[i] - prev) / prev > 0.095:
                    ban_count += 1
            
            # 均量和换手
            vol_ma20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
            avg_vol_10 = sum(volumes[-10:]) / 10
            turnover_active = avg_vol_10 / max(vol_ma20, 1) > 1.0
            
            # MA20 代替 MA60（更灵敏，少停牌影响）
            ma20 = sum(closes[-need_days:]) / need_days if need_days >= 20 else sum(closes) / len(closes)
            above_ma = latest_close > ma20
            # MA20 方向
            half = need_days // 2
            ma_prev = sum(closes[-(need_days+half):-half]) / need_days if len(closes) >= need_days + half else ma20
            ma_up = ma20 > ma_prev
            
            # 近 5 天振幅相对收缩（相对 20 天均值）
            if len(closes) >= 20:
                all_amps = [(highs[i] - lows[i]) / closes[i-1] for i in range(-20, 0) if closes[i-1] > 0]
                avg_amp_20 = sum(all_amps) / len(all_amps) if all_amps else 0.05
                recent_amps = all_amps[-5:] if len(all_amps) >= 5 else all_amps
                avg_amp_5 = sum(recent_amps) / len(recent_amps) if recent_amps else 0.05
                amp_shrink = avg_amp_5 / max(avg_amp_20, 0.001)
                amp_ok = amp_shrink < 0.7
            else:
                amp_ok = False
            
            # 近 3 天有 S1
            self.cur.execute("""
                SELECT 1 FROM pool_energy
                WHERE level = %s AND date BETWEEN %s AND %s
                  AND extra->>'sigs' LIKE '%%s1%%'
                LIMIT 1
            """, ('code:A.' + code, signal_date - timedelta(days=3), signal_date))
            has_s1 = self.cur.fetchone() is not None
            
            # 近 20 天龙虎榜机构净买入
            self.cur.execute("""
                SELECT 1 FROM csi_lhb_detail
                WHERE code = %s AND trade_date BETWEEN %s AND %s
                  AND inst_net_buy > 0
                LIMIT 1
            """, (code, signal_date - timedelta(days=20), signal_date))
            no_inst = self.cur.fetchone() is None
            
            score = 0
            reasons = []
            if ban_count >= 1:
                score += 20
                reasons.append(f'涨停基因{ban_count}')
            if above_ma and ma_up:
                score += 20
                reasons.append('ma上行')
            if amp_ok:
                score += 15
                reasons.append('振幅收敛')
            if has_s1:
                score += 25
                reasons.append('S1试盘')
            if no_inst:
                score += 10
                reasons.append('无机构')
            if turnover_active:
                score += 10
                reasons.append('交投活跃')
            
            candidates.append({
                'code': code, 'score': score,
                'reasons': ';'.join(reasons),
                'ban_count': ban_count,
                'has_s1': has_s1,
                'above_ma60': above_ma,
            })
        
        # 排序
        candidates.sort(key=lambda x: -x['score'])
        result['total'] = len(candidates)
        result['candidates'] = candidates[:50]
        result['hot_sectors'] = list(hot_sectors)[:10]
        return result

    def classify(self, code, signal_date, extra, *args):
        """第二层：定性筛，输出 score"""
        reason_parts = []
        score = 0.7  # 默认 C 级

        # 龙虎榜 → 排除
        if self.check_lhb(code, signal_date):
            return {'type': '排除', 'score': 0.0, 'reasons': ['龙虎榜上榜'], 'position': '不参与'}

        # 增减持
        incr7, decr7, incr14 = self.check_incr_decr(code, signal_date)
        if incr7 > 0 and decr7 == 0:
            score = max(score, 2.0 if incr7 >= 5000 else 1.5)
            reason_parts.append(f'7d增持{incr7}')
            return {'type': 'A', 'score': min(score, 2.0), 'reasons': reason_parts, 'position': '重仓'}
        if decr7 > incr7:
            score = min(score, 0.3)
            reason_parts.append(f'减持{decr7}')
            return {'type': '排除', 'score': 0.0, 'reasons': reason_parts, 'position': '不参与'}
        if incr14 > 0 and decr7 == 0:
            score = max(score, 1.3)
            reason_parts.append(f'14d增持{incr14}')

        # post_vol（需要等3天后的成交量数据）
        # 这里先不塞 post_vol，因为回测时需要 T+3 数据
        # 但保留 post_vol 作为 score 调整

        # 板块超额（sector_daily）
        # 财报窗口
        has_fin = self.check_financial_window(code, signal_date)
        if has_fin:
            score *= 0.5
            reason_parts.append('财报期')

        # 默认
        typ = 'B' if score >= 0.8 else 'C'
        pos = '中等' if score >= 1.0 else ('轻仓' if score >= 0.5 else '不参与')
        if not reason_parts:
            reason_parts.append('无增强信号')
        return {'type': typ, 'score': score, 'reasons': reason_parts, 'position': pos}

    def limit_up_filter(self, code: str, trade_date: date, sigs: str = '') -> dict:
        """涨停风控过滤层

        利用 daily_limit_up 表判断当日是否涨停、是否为一日游高发票
        Returns: {'is_limit_up': bool, 'is_first_ban': bool, 'has_chained': bool,
                  'risk_level': 'none'|'low'|'medium'|'high', 'reason': str}
        """
        result = {'is_limit_up': False, 'is_first_ban': False, 'has_chained': False,
                  'risk_level': 'none', 'reason': ''}

        # 查该股近期涨停记录
        self.cur.execute("""
            SELECT trade_date, is_limit_up, amplitude::text, est_mv::text, ret_0::text, industry
            FROM daily_limit_up
            WHERE code=%s AND trade_date <= %s AND is_limit_up = true
            ORDER BY trade_date DESC LIMIT 5
        """, (code, trade_date))
        rows = self.cur.fetchall()

        if not rows:
            return result

        today = None
        prev_bans = []
        for r in rows:
            if r[0] == trade_date:
                today = r
            else:
                prev_bans.append(r)

        if not today:
            return result

        # 今天是涨停日 —— 判断质量
        result['is_limit_up'] = True
        amp = float(today[2]) if today[2] is not None else 0
        mv = float(today[3]) if today[3] is not None else 0
        ret0 = float(today[4]) if today[4] is not None else 0
        industry = today[5]
        prev_ban_date = prev_bans[0][0] if prev_bans else None

        # 首板：30天内无涨停
        is_first = prev_ban_date is None or (trade_date - prev_ban_date).days > 30
        result['is_first_ban'] = is_first

        # 连板基因：过去5天有涨停
        has_chained = False
        if prev_ban_date is not None:
            diff = trade_date - prev_ban_date
            has_chained = diff.days <= 3
        result['has_chained'] = has_chained

        # === 风险判定 ===
        reasons = []
        risk = 'none'

        # 类型统一（PG回传是Decimal）
        amp = float(amp) if amp is not None else 0
        mv = float(mv) if mv is not None else 0
        ret0 = float(ret0) if ret0 is not None else 0

        # 炸板/尾盘偷袭
        if amp > 0.08 and ret0 < 0.145:
            reasons.append('炸板')
            risk = 'high'
        elif amp > 0.05:
            reasons.append('尾盘板')
            risk = 'medium' if risk != 'high' else risk

        # 超小市值首板
        if is_first and mv / 1e8 < 10:
            reasons.append(f'微盘首板(市值{mv/1e8:.0f}亿)')
            risk = 'medium' if risk != 'high' else risk

        # 双创首板（20cm高波动）
        if is_first and (code.startswith('3') or code.startswith('688')):
            reasons.append(f'双创首板')
            risk = 'medium' if risk != 'high' else risk

        # 无板块支撑
        if not industry or industry == '':
            reasons.append('无行业归属')
            risk = 'medium' if risk not in ('high',) else risk

        if risk == 'high':
            result['risk_level'] = 'high'
        elif risk == 'medium':
            result['risk_level'] = 'medium'
        elif risk == 'none' and is_first:
            result['risk_level'] = 'medium'  # 首板默认中等风险

        result['reason'] = ', '.join(reasons) if reasons else ('首板' if is_first else '连板')
        return result

    def entry_signal(self, code, signal_date, extra, ind_map, classify_result):
        """第三层：进场条件 + 假突破过滤 + phase-aware + 涨停风控"""
        if classify_result['score'] <= 0 or classify_result['type'] == '排除':
            return {'action': 'pass', 'reason': '第二层排除'}

        # === 涨停风控过滤 ===
        luf = self.limit_up_filter(code, signal_date, extra.get('sigs', ''))
        if luf['risk_level'] == 'high':
            return {'action': 'pass', 'reason': f'涨停风控排除: {luf["reason"]}',
                    'limit_up': luf}
        if luf['risk_level'] == 'medium':
            # 中等风险：score上限0.7
            classify_result = classify_result.copy()
            original_score = classify_result.get('original_score', classify_result['score'])
            if 'original_score' not in classify_result:
                classify_result['original_score'] = classify_result['score']
            classify_result['score'] = min(classify_result['score'], 0.7)
            classify_result['reason_add'] = f'涨停风控降权({luf["reason"]})'


        sym = 'A.' + code
        phase = classify_result.get('phase', self.get_market_phase(signal_date))
        sigs = extra.get('sigs', '')
        
        # 板块超额
        ind_info = self.calc_industry_excess(code, signal_date, ind_map)
        pl_excess = ind_info['daily_excess'] if ind_info else 0

        # ATR
        atr_info = self.calc_atr(code, signal_date)
        atr_pct = atr_info['atr_pct'] if atr_info else 0.03
        close_now = atr_info['close'] if atr_info else 0

        # 当日价格
        self.cur.execute("""
            SELECT low, close FROM daily_prices
            WHERE symbol = %s AND trade_date = %s
        """, (sym, signal_date))
        day_info = self.cur.fetchone()
        s3_low = day_info[0] if day_info else 0
        s3_close = day_info[1] if day_info else 0

        # === 假突破过滤（S2） ===
        fake_check = self.check_fake_breakout(code, signal_date, extra)
        if fake_check.get('fake_break'):
            return {'action': 'pass', 'reason': f'假突破: {", ".join(fake_check["fake_reasons"])}',
                    'fake_check': fake_check, 'pl_excess': pl_excess}
        
        # 封板 S2 升格 + 拉板前兆评分调整
        is_limit_up = fake_check.get('limit_up', False)
        if is_limit_up and 's2' in sigs:
            precursor = self.score_limit_precursor(code, signal_date)
            classify_result = classify_result.copy()
            if precursor['score'] >= 60:
                classify_result['score'] = max(classify_result['score'], 1.2)
                classify_result['reason_add'] = f'拉板前兆({precursor["score"]}分:C1={precursor["details"]["c1_lockup"]} C2={precursor["details"]["c2_window"]} C3={precursor["details"]["c3_test"]})'
            elif precursor['score'] >= 40:
                classify_result['score'] = max(classify_result['score'], 0.9)
            else:
                classify_result['score'] = max(classify_result['score'], 0.6)

        # === phase-aware 板块超额 ===
        sector_mult = 1.0
        if phase in ('B_DISTRIB',):
            if pl_excess is not None and pl_excess > 0.03:
                sector_mult = 0.5
            elif pl_excess is not None and pl_excess < -0.03:
                sector_mult = 0.3
        elif phase in ('A_RECOVER', 'A_ACCUM'):
            sector_mult = 1.0  # A 侧不依赖板块
        else:
            if pl_excess is not None:
                if pl_excess > 0.03: sector_mult = 1.3
                elif pl_excess < -0.03: sector_mult = 0.0
                elif pl_excess < 0: sector_mult = 0.8

        if classify_result.get('reasons') and '财报期' in str(classify_result['reasons']):
            sector_mult *= 0.5
        if sector_mult == 0.0:
            return {'action': 'pass', 'reason': f'板块超额={pl_excess:+.1%}< -3%, 排除', 'pl_excess': pl_excess}

        stop_pct = min(atr_pct * 1.5, 0.08)

        # S2 在 B_DISTRIB 反转做空
        if 's2' in sigs and phase in ('B_DISTRIB',):
            return {'action': 'short', 'reason': f'B_DISTRIB+S2: 反转做空', 'pl_excess': pl_excess,
                    'stop_loss': s3_close * (1 + stop_pct),
                    'exit_target': s3_close * (1 - stop_pct * 2)}

        if classify_result['score'] >= 2.0: position = '重仓'
        elif classify_result['score'] >= 1.0: position = '中等仓位'
        elif classify_result['score'] >= 0.5: position = '轻仓试探'
        else: return {'action': 'pass', 'reason': 'score太低'}

        post_vol = self.calc_post_vol(code, signal_date)
        if post_vol is not None and post_vol < 0.5 and phase not in ('A_RECOVER', 'A_ACCUM'):
            return {'action': 'pass', 'reason': f'post_vol={post_vol:.2f}<0.5, 无承接',
                    'post_vol': post_vol, 'pl_excess': pl_excess}

        entry_price = max(s3_low * 1.01, s3_close * 0.95)
        stop_loss = entry_price * (1 - stop_pct)
        target1 = entry_price * (1 + stop_pct * 2)
        target2 = entry_price * (1 + stop_pct * 3)

        return {
            'action': 'buy', 'entry': f'入场价={entry_price:.2f}',
            'target_price': round(entry_price, 2),
            'stop_loss': round(stop_loss, 2),
            'exit_targets': [round(target1, 2), round(target2, 2)],
            'position': position, 'atr': atr_pct, 'pl_excess': pl_excess,
            'post_vol': post_vol, 'phase': phase,
            'reason': f'classify={classify_result["type"]}, score={classify_result["score"]}, '
                      f'板块超额={pl_excess:+.1%}, ATR={atr_pct:.1%}, phase={phase}',
        }


def validate_202604():
    """用2026年4月样本外数据验证三层框架"""
    mf = V6MultiFactor()
    print("=" * 65)
    print("V6 三层多因子 · 样本外验证 (2026-04)")
    print("=" * 65)

    # 加载 S3 信号
    signals = mf.fetch_s3_signals(date(2026, 4, 1), date(2026, 4, 22))
    print(f"[1] S3 信号: {len(signals)} 条\n")

    # 行业映射
    ind_map = mf.fetch_industry_map()
    ths_mapped = sum(1 for v in ind_map.values() if v['ths'] is not None)
    print(f"[2] 行业映射: {ths_mapped}/{len(ind_map)} 只有同花顺行业\n")

    # 批量预计算 S3 信号涉及的所有行业超额
    mf.precalc_all_industry_excess(signals, ind_map)

    # 逐条分类 + 入场判断
    results = []
    for sig in signals:
        code = sig['level'][7:]
        dt = sig['date']
        extra = sig['extra']

        # T+20 验证实际收益
        sym = 'A.' + code
        mf.cur.execute("""
            SELECT close FROM daily_prices
            WHERE symbol = %s AND trade_date > %s
            ORDER BY trade_date OFFSET 19 LIMIT 1
        """, (sym, dt))
        t20 = mf.cur.fetchone()
        if not t20:
            continue
        t20_ret = t20[0] / extra.get('close_0', 1) - 1  # 需要一个原始价格

        # 实际用 extra 里的 close... extra 里没有 close_0
        # 直接去查
        mf.cur.execute("""
            SELECT close FROM daily_prices WHERE symbol = %s AND trade_date = %s
        """, (sym, dt))
        c0 = mf.cur.fetchone()
        if not c0:
            continue
        t20_ret = t20[0] / c0[0] - 1

        # 第二层分类
        cls = mf.classify(code, dt, extra)

        # 第三层进场
        entry = mf.entry_signal(code, dt, extra, ind_map, cls)

        results.append({
            'code': code, 'date': dt, 'ret_20': t20_ret,
            'classify': cls, 'entry': entry,
            'close_0': c0[0],
        })

    print(f"[3] 可验证 S3+T+20: {len(results)} 条\n")

    if not results:
        mf.conn.close()
        return

    import pandas as pd
    df = pd.DataFrame(results)

    # 分组统计
    for typ in ['A', 'B', 'C', '排除']:
        grp = df[df['classify'].apply(lambda x: x['type'] == typ)]
        if len(grp) == 0: continue
        rets = grp['ret_20'].values
        print(f"  {typ}级 (n={len(grp)}): T+20均值={np.mean(rets):+.2%}  胜率={np.mean(rets>0):.0%}")

    # 进场信号的分组
    action_buy = [r for r in results if r['entry'].get('action') == 'buy']
    action_pass = [r for r in results if r['entry'].get('action') == 'pass']
    print(f"\n  买入信号: {len(action_buy)} 条")
    print(f"  买入胜率: {sum(1 for r in action_buy if r['ret_20']>0)}/{len(action_buy)} = {np.mean(np.array([r['ret_20'] for r in action_buy])>0):.0%}")
    print(f"  买入均值: {np.mean([r['ret_20'] for r in action_buy]):+>.2%}" if action_buy else "")
    
    if action_buy:
        rets_b = [r['ret_20'] for r in action_buy]
        print(f"  买入: n={len(action_buy)}, T+20={np.mean(rets_b):+.2%}, 胜率={np.mean(np.array(rets_b)>0):.0%}")

    if action_pass:
        rets_p = [r['ret_20'] for r in action_pass]
        print(f"  排除: n={len(action_pass)}, T+20={np.mean(rets_p):+.2%}, 胜率={np.mean(np.array(rets_p)>0):.0%}")

    # 详细
    print(f"\n{'='*75}\n详细清单:")
    print(f"{'代码':>8} {'日期':<12} {'type':<6} {'score':<6} {'板块强':<8} {'T+20':<8} {'动作'}")
    print("-"*65)
    for r in results:
        print(f"A.{r['code']:<6} {r['date']!s:<12} {r['classify']['type']:<6} {r['classify']['score']:<6.1f} "
              f"{r['entry'].get('pl_excess', 0):+.1%} {r['ret_20']:+7.2%} "
              f"{r['entry'].get('action', '—')}")

    mf.conn.close()


if __name__ == '__main__':
    validate_202604()
