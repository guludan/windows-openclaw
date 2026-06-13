"""
情绪诊断框架 - 散户情绪被操控的全过程

基于黄老板的洞察：机构通过控制股价上下限，制造恐慌/狂热来操控散户情绪。
这个框架把散户作为观察对象，通过对话内容提取当前处于机构博弈的哪个阶段。

框架结构：
1. 散户情绪五阶段模型
2. 情绪信号检测（从对话内容提取）
3. 反周期操作建议

使用场景：
- 盘后复盘：标记今天黄老板的对话处于哪个阶段
- 盘中检测：如果检测到恐慌/狂热信号，给出逆向操作建议
"""

from enum import Enum
from datetime import datetime, date
from typing import List, Optional, Dict

# ==================== 阶段模型 ====================

class MarketPhase(Enum):
    """市场阶段（机构视角）"""
    BUILDING = "机构建仓期"
    TESTING = "机构测试期"      # ← 当前
    MANUFACTURING_PANIC = "机构制造恐慌期"
    COLLECTING = "机构低位吸筹期"
    LIFTING = "机构拉升出货期"
    DISTRIBUTING = "机构派发期"

class RetailPhase(Enum):
    """散户情绪阶段"""
    # 下跌阶段
    HOLD = "死扛期"            # 相信会涨回来
    ANXIOUS = "焦虑期"         # 开始怀疑判断
    DOUBTING = "怀疑期"        # "是不是机构在搞我？"
    PANIC = "恐慌期"           # 想割肉
    CAPITULATION = "崩溃期"    # 割肉出局
    # 上涨阶段
    DISBELIEF = "不信任期"     # 不敢追
    FOMO = "FOMO期"           # 终于买入
    GREED = "贪婪期"           # 加仓
    EUPHORIA = "狂热期"        # 全仓

# ==================== 情绪信号检测 ====================

# 黄老板对话中的关键信号词（基于实际对话提取）
SENTIMENT_SIGNALS = {
    RetailPhase.HOLD: [
        "硬底", "支撑", "拿着", "HOLD", "不动", "再等等", "反弹", "会回来的"
    ],
    RetailPhase.ANXIOUS: [
        "怎么看", "怎么回事", "为什么跌", "是不是", "不应该啊"
    ],
    RetailPhase.DOUBTING: [
        "认知陷阱", "操控", "机构在", "庞氏", "量化", "假信号", "假的"
    ],
    RetailPhase.PANIC: [
        "亏了", "止损", "割肉", "完了", "要不要卖", "跑不跑"
    ],
    RetailPhase.CAPITULATION: [
        "全卖了", "清仓", "不玩了", "认栽", "退出"
    ],
    # 上涨阶段
    RetailPhase.DISBELIEF: [
        "没道理涨", "假的反弹", "还会跌", "不敢买"
    ],
    RetailPhase.FOMO: [
        "涨了", "要不要追", "错过", "赶紧买"
    ],
    RetailPhase.GREED: [
        "加仓", "再加", "满仓", "干进去"
    ],
    RetailPhase.EUPHORIA: [
        "牛市来了", "赚翻了", "all in", "直接飞"
    ],
}

# ==================== 操作阶段建议 ====================

PHASE_ACTIONS = {
    # 在机构测试期/制造恐慌期 → 不能HOLD，要网格
    ("机构测试期", RetailPhase.HOLD): {
        "judgment": "❌ 错误反应",
        "reason": "机构在测试散户止损线，死扛等于被投喂。当前持仓亏6%，需要网格化操作反制",
        "action": "设定网格上下限，自动化低买高卖"
    },
    ("机构测试期", RetailPhase.ANXIOUS): {
        "judgment": "⚠️ 危险信号",
        "reason": "焦虑是正常的，但焦虑会演变成恐慌。需要量化工具来对抗情绪",
        "action": "看网格+分时数据，不要看账户总亏损"
    },
    ("机构测试期", RetailPhase.DOUBTING): {
        "judgment": "✅ 觉醒信号",
        "reason": "你发现了认知陷阱，这是反制的第一步",
        "action": "构建反收割策略"
    },
    ("机构测试期", RetailPhase.PANIC): {
        "judgment": "🔴 机构预期结果",
        "reason": "机构测试期最想看到散户恐慌割肉。现在割=把筹码送给机构",
        "action": "转为网格买入模式，机构越砸越买"
    },
    ("机构建仓期", RetailPhase.HOLD): {
        "judgment": "✅ 正确",
        "reason": "在建仓期HOLD是对的，机构还会拉升",
        "action": "持有，等待拉升信号"
    },
    ("机构拉升出货期", RetailPhase.FOMO): {
        "judgment": "🔴 机构预期结果",
        "reason": "拉升期散户追高=接盘。机构要出货了",
        "action": "分批卖出，不要追高"
    },
    ("机构制造恐慌期", RetailPhase.PANIC): {
        "judgment": "🔴 机构的完美作品",
        "reason": "恐慌期就是让散户割肉的。全出的机构正在接",
        "action": "反向加仓，等恐慌高潮"
    },
    ("机构低位吸筹期", RetailPhase.CAPITULATION): {
        "judgment": "🔴 最大错误",
        "reason": "机构吸筹你清仓，标准的追涨杀跌",
        "action": "认错，分批买入"
    },
    ("机构低位吸筹期", RetailPhase.HOLD): {
        "judgment": "✅ 正确",
        "reason": "机构还在吸筹，还没到拉升期",
        "action": "不操作，等机构动"
    },
}

# ==================== 对话分析 ====================

class SentimentAnalyzer:
    """从对话内容分析当前散户情绪阶段"""

    def __init__(self):
        self.history: List[Dict] = []
        self.current_phase = RetailPhase.ANXIOUS

    def analyze_message(self, text: str) -> Dict:
        """
        分析一条消息中的情绪信号
        返回分析结果
        """
        signals = []
        for phase, keywords in SENTIMENT_SIGNALS.items():
            for kw in keywords:
                if kw.lower() in text.lower():
                    signals.append({
                        "phase": phase.name,
                        "phase_label": phase.value,
                        "keyword": kw,
                        "confidence": "high" if kw in text else "low"
                    })

        # 统计各阶段信号数
        phase_counts = {}
        for s in signals:
            p = s["phase"]
            phase_counts[p] = phase_counts.get(p, 0) + 1

        # 确定当前阶段（按出现频率最高的）
        current = None
        if phase_counts:
            max_phase = max(phase_counts, key=phase_counts.get)
            for phase in RetailPhase:
                if phase.name == max_phase:
                    current = phase
                    break

        result = {
            "text": text[:200],
            "signals_found": signals,
            "phase_counts": phase_counts,
            "inferred_phase": current.value if current else "未知",
            "confidence": max(phase_counts.values()) / max(len(text)//10, 1) if phase_counts else 0,
        }

        self.history.append(result)
        if current:
            self.current_phase = current
        return result

    def get_market_context(self, phase: RetailPhase, market_state: str = "机构测试期") -> Dict:
        """
        根据情绪阶段+市场状态给出操作建议
        """
        key = (market_state, phase.value)
        if key in PHASE_ACTIONS:
            return PHASE_ACTIONS[key]
        return {
            "judgment": "⚠️ 未知组合",
            "reason": f"市场状态={market_state}, 情绪={phase.value}",
            "action": "需要进一步分析"
        }

    def summary(self) -> str:
        """输出当前对话分析摘要"""
        if not self.history:
            return "暂无分析数据"

        lines = []
        lines.append(f"== 情绪分析报告 ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ==")
        lines.append(f"会话数: {len(self.history)}")
        lines.append(f"当前推定阶段: {self.current_phase.value}")
        lines.append("")

        # 情绪轨迹
        phases_seen = []
        for h in self.history:
            p = h.get("inferred_phase", "未知")
            if p != (phases_seen[-1] if phases_seen else None):
                phases_seen.append(p)

        if phases_seen:
            lines.append(f"情绪轨迹: {' → '.join(phases_seen)}")

        lines.append("")
        lines.append("阶段特征:")
        for phase in RetailPhase:
            count = sum(1 for h in self.history if h.get("inferred_phase") == phase.value)
            if count > 0:
                lines.append(f"  {phase.value}: {count}次")

        return "\n".join(lines)


# ==================== 命令行 ====================

def main():
    """命令行测试"""
    import sys
    analyzer = SentimentAnalyzer()

    # 如果传了文件参数，分析文件内容
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        try:
            with open(filepath, 'r') as f:
                text = f.read()
            result = analyzer.analyze_message(text)
            print("分析结果:")
            print(f"  推断阶段: {result['inferred_phase']}")
            print(f"  信号数: {len(result['signals_found'])}")
            for s in result['signals_found']:
                print(f"    [{s['phase']}] 关键词'{s['keyword']}'")
            print()
            print(analyzer.summary())
        except FileNotFoundError:
            print(f"文件不存在: {filepath}")
        return

    # 交互模式
    print("=== 情绪诊断控制台 ===")
    print("输入对话文本（或输入:market <阶段> 切换市场状态）")
    print("输入:summary 查看总分析")
    print("输入:exit 退出")
    print()

    market_state = "机构测试期"
    while True:
        try:
            text = input("> ").strip()
            if text == ":exit":
                break
            if text.startswith(":market"):
                market_state = text[8:].strip()
                print(f"市场状态切换为: {market_state}")
                continue
            if text == ":summary":
                print(analyzer.summary())
                continue

            result = analyzer.analyze_message(text)
            ctx = analyzer.get_market_context(analyzer.current_phase, market_state)

            print(f"\n推定阶段: {result['inferred_phase']}")
            print(f"判定: {ctx.get('judgment', '')}")
            print(f"原因: {ctx.get('reason', '')}")
            print(f"建议操作: {ctx.get('action', '')}")
            print()
        except KeyboardInterrupt:
            print()
            break
        except EOFError:
            break


if __name__ == "__main__":
    main()
