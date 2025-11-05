# -*- coding: utf-8 -*-
"""
AI驱动的黄金期货自主交易策略 (最大AI自主权版本)
策略名称: DeepSeek Autonomous Gold Futures Trading
交易品种: au2512.SHFE (黄金期货)
核心理念: 最小化人为规则,最大化AI决策自由度,只做最后风控

Gkoudai平台提供的全局函数 (无需import, 运行时自动可用):
- subscribe(): 订阅实时行情
- get_market_data(): 获取K线数据 (返回ArrayManager)
- query_history(): 查询历史K线数据 (返回BarData列表)
- get_current_bar(): 获取当前最新K线
- get_pos(): 获取持仓数量
- buy(), sell(), short(), cover(): 开平仓函数
- send_target_order(): 设置目标仓位
- Log(): 日志输出
"""

import json
import time
from datetime import datetime, time as datetime_time
import requests
import traceback
from collections import deque
 
import math

# ========================================
# 核心配置参数
# ========================================

class Config:
    """配置类 - 只包含安全边界和基础设置"""

    # 交易标的
    SYMBOL = "au2512.SHFE"  # 默认主标的: 黄金2025年12月合约
    # 多标的支持: 增加碳酸锂（广期所）
    SYMBOLS = [
        "au2512.SHFE",
        "lc2601.GFEX"
    ]
    # 合约乘数（用于估算下单手数）。单位按交易所定义，数值用于 notion 估算。
    CONTRACT_MULTIPLIER = {
        "au2512.SHFE": 1000,  # 1000克/手
        "lc2601.GFEX": 5      # 5吨/手（示例值）
    }

    # AI配置（为方便使用，先写死在这里，直接填写即可）
    # 注意：请将下面的 Key 替换为你的真实 Key。
    # 如果你更偏好用环境变量，可把此行改回 os.getenv('DEEPSEEK_API_KEY', '')
    DEEPSEEK_API_KEY = "sk-c7c94df2cbbb423698cb895f25534501"
    DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
    DEEPSEEK_MODEL = "deepseek-chat"
    DEEPSEEK_TEMPERATURE = 0.7
    # 注意：DeepSeek 通常限制 <= 8192；避免请求超限
    DEEPSEEK_MAX_TOKENS = 8192

    # AI决策频率 (秒) - 平衡成本和响应速度
    AI_DECISION_INTERVAL = 60  # 1分钟一次决策

    # ====== 安全边界 (唯一的硬性约束) ======
    MAX_SINGLE_TRADE_LOSS_PCT = 0.02   # 单笔最大亏损2%
    MAX_DAILY_LOSS_PCT = 0.05          # 单日最大亏损5%
    FORCE_CLOSE_TIME = "14:55:00"      # 强制平仓时间
    MIN_AI_CONFIDENCE = 0.6            # 最小信心阈值(0-1)

    # 数据窗口大小
    TICK_WINDOW = 100        # 缓存最近100个tick
    KLINE_1M_WINDOW = 300    # 1分钟K线300根（用于5分钟聚合与指标计算）
    KLINE_1D_WINDOW = 50     # 日K线50根
    DEPTH_LIQ_WINDOW = 120   # 盘口深度流动性统计窗口（最近 N 个tick）

    # API重试配置
    API_TIMEOUT = 30  # 增加到30秒,避免网络波动导致超时
    API_MAX_RETRIES = 3

    # 自适应与动态管理
    ADAPTIVE_PARAMS = {
        'UPTREND': {
            'ai_interval_secs': 60,
            'cooldown_minutes': 2,
            'position_size_pct': 0.6,
            'trailing_type': 'atr',
            'trailing_atr_mult': 2.0,
            'trailing_percent': 0.0,
        },
        'DOWNTREND': {
            'ai_interval_secs': 60,
            'cooldown_minutes': 2,
            'position_size_pct': 0.6,
            'trailing_type': 'atr',
            'trailing_atr_mult': 2.0,
            'trailing_percent': 0.0,
        },
        'SIDEWAYS': {
            'ai_interval_secs': 30,
            'cooldown_minutes': 1,
            'position_size_pct': 0.3,
            'trailing_type': 'percent',
            'trailing_atr_mult': 0.0,
            'trailing_percent': 0.7,
        }
    }

    # 本地持久化（运行期）
    STATE_DIR = "state"
    STATE_FILE = "state/portfolio_runtime.json"
    SAVE_INTERVAL_SECS = 10

    # 兜底保证金率（若平台未提供合约保证金率字段时使用; 数值需按交易所校准）
    DEFAULT_MARGIN_RATIO_LONG = {
        "au2512.SHFE": 0.07,
        "lc2601.GFEX": 0.12,
    }
    DEFAULT_MARGIN_RATIO_SHORT = {
        "au2512.SHFE": 0.07,
        "lc2601.GFEX": 0.12,
    }

    # 新开仓资金安全系数（留出浮亏与费用空间）
    NEW_TRADE_MARGIN_BUFFER = 1.05
    # 最低担保比（equity / margin_used，越高越安全）；若低于该值则禁止新开仓
    MIN_GUARANTEE_RATIO = 1.3

    # 波浪/结构识别参数（ZigZag）
    # 以百分比阈值识别转折（例如0.3%）；可根据品种调整
    ZIGZAG_THRESHOLD_PCT = 0.3

    


# ========================================
# AI决策核心Prompt
# ========================================

def construct_autonomous_trading_prompt(market_data):
    """
    构造最大化AI自主权的交易Prompt

    设计原则:
    1. 提供完整市场数据,不做预处理判断
    2. 不预设交易规则,完全由AI判断
    3. 只要求AI输出标准化格式
    4. 强调风险自我评估
    """

    # 处理可能为 0/None 的持仓均价，避免格式化错误
    _pap = market_data.get('position_avg_price')
    avg_price_str = f"{_pap:.2f}" if (_pap is not None and _pap > 0) else "N/A"

    # 为避免在 f-string 中出现复杂表达式导致解析问题，先取出需要的字段
    d_ema_20_v = market_data.get('d_ema_20', 0)
    d_ema_60_v = market_data.get('d_ema_60', 0)
    d_macd_v = market_data.get('d_macd', 0)
    d_trend_v = market_data.get('d_trend', 'N/A')
    zigzag_summary_v = market_data.get('zigzag_summary', 'N/A')
    fib_summary_v = market_data.get('fib_summary', 'N/A')

    head = f"""# 角色定义

你是一位专业的期货日内交易员,负责管理 {market_data['symbol']} 的交易头寸。你有完全的决策自主权,需要基于市场数据自主判断:
- 当前市场状态 (趋势/震荡/反转)
- 是否应该入场/出场/持有
- 止损止盈位置
- 仓位大小
- 持仓时间预期

# 当前市场数据

## 实时行情
- **当前价格**: {market_data['current_price']:.2f} 元/克
- **买一价**: {market_data['bid_price']:.2f}, 量: {market_data['bid_volume']}
- **卖一价**: {market_data['ask_price']:.2f}, 量: {market_data['ask_volume']}
- **最新成交量**: {market_data['last_volume']} (单笔)
- **当前时间**: {market_data['current_time']}

## 量能分析
- **当前成交量**: {market_data['current_volume']}
- **20周期均量**: {market_data['avg_volume_20']:.0f}
- **量能比**: {market_data['volume_ratio']:.2f}x (当前/均量)
- **量能状态**: {market_data['volume_state']}

## 盘口与流动性
- **点差**: {market_data['spread']:.4f}
- **中间价**: {market_data['mid_price']:.4f}
- **微价格**: {market_data['microprice']:.4f}
- **L1不平衡**: {market_data['imbalance_l1']:.2f}
- **L5不平衡**: {market_data['imbalance_l5']:.2f}
- **五档买深度/卖深度**: {market_data['sum_bid_5']} / {market_data['sum_ask_5']}
- **流动性评分**: {market_data['liquidity_score']:.2f} ({market_data['liquidity_state']})

## 技术指标 (1分钟周期 - 入场分析)
- **EMA20**: {market_data['ema_20']:.2f}
- **EMA60**: {market_data['ema_60']:.2f}
- **MACD**: {market_data['macd']:.4f}
- **Signal**: {market_data['macd_signal']:.4f}
- **Histogram**: {market_data['macd_hist']:.4f}
- **RSI(14)**: {market_data['rsi']:.2f}
- **ATR(14)**: {market_data['atr']:.2f} (波动率指标)

## 价格结构 (最近20根1分钟K线)
- **最高价**: {market_data['high_20']:.2f}
- **最低价**: {market_data['low_20']:.2f}
- **价格振幅**: {market_data['price_range_pct']:.2f}%

## 日线信息（按交易日聚合的日内口径）
- **日内开盘价**: {market_data['daily_open']:.2f}
- **日内最高**: {market_data['daily_high']:.2f}
- **日内最低**: {market_data['daily_low']:.2f}
- **日内涨跌幅**: {market_data['daily_change_pct']:.2f}%

## 日线趋势 (已完成日线)
- **D_EMA20**: {d_ema_20_v:.2f}
- **D_EMA60**: {d_ema_60_v:.2f}
- **D_MACD**: {d_macd_v:.4f}
- **趋势判定**: {d_trend_v}

## 波浪/结构提示（ZigZag 摘要）
- **最近枢轴点**: {zigzag_summary_v}
- **关键斐波水平**: {fib_summary_v}

## 当前持仓状态
 - **持仓方向**: {market_data['position_direction']}
 - **持仓数量**: {market_data['position_volume']}
 - **持仓均价**: {avg_price_str}
- **未实现盈亏**: {market_data['unrealized_pnl']:.2f} 元
- **未实现盈亏率**: {market_data['unrealized_pnl_pct']:.2f}%
- **持仓时长**: {market_data['holding_minutes']:.0f} 分钟

## 今日交易统计
- **今日盈亏**: {market_data['daily_pnl']:.2f} 元
- **今日盈亏率**: {market_data['daily_pnl_pct']:.2f}%
- **今日交易次数**: {market_data['daily_trades']}
- **今日胜率**: {market_data['daily_win_rate']:.1f}%

# 安全约束 (唯一的硬性规则)

1. **单笔最大亏损**: 账户净值的2% (系统会自动强平)
2. **单日最大亏损**: 账户净值的5% (系统会停止交易)
3. **强制平仓时间**: 14:55之前必须平仓,不得持仓过夜
4. **最小信心度**: 你的决策信心度必须≥0.6才会被执行

除此之外,你有完全的自主决策权。

# 交易决策框架 (建议,非强制)

## 自主量价分析（不预设模式）

你应基于提供的价格、成交量、盘口与流动性信息自行建模判断：
- 趋势强弱与结构（突破/回踩/假突破/趋势延续/反转）
- 量价配合（放量上行/缩量回调/量能衰竭/资金主动性）
- 盘口支撑与滑点风险（spread/imbalance/microprice/深度）

不限制使用固定的“放量反转”模板（如 CAPITULATION/BLOW_OFF），可自由给出你的量价逻辑与论证。

## 止损止盈设计

你需要自主判断:
- **止损价格**: 基于技术支撑位/压力位、ATR波动率、趋势强度等
- **止盈价格**: 基于风险收益比、目标位、阻力位等
- **失效条件**: 什么市场信号出现时,交易逻辑不再成立?

建议风险收益比≥2:1,但你可以根据市场状况调整。

## 仓位管理

你可以基于信心度调整仓位:
- 信心度0.9-1.0: 满仓 (100%)
- 信心度0.7-0.9: 重仓 (70%)
- 信心度0.6-0.7: 半仓 (50%)

仓位换算说明(用于执行层):
- 账户初始资金: 100,000元(示例)
- 合约乘数: {market_data['contract_multiplier']}
- 建议下单手数 ≈ 初始资金 × position_size_pct ÷ (当前价格 × 合约乘数)

成交价格参考:
- 做多(buy)按卖一价(ask)成交, 做空(sell/short)按买一价(bid)成交；如盘口缺失则退化为最新价。

仓位与可交易性（建议，非强制）：
- 当流动性偏弱或点差偏大（liquidity_state=THIN 或 spread>2 ticks）→ 降低仓位或放弃入场
- 当盘口不平衡（imbalance_l5）与趋势一致 → 可适当提高仓位；相反则保守或等待确认

## 持仓时长

你可以自主判断持仓时长:
- 快速反转交易: 5-15分钟
- 趋势跟随: 30-120分钟
- 日内波段: 直到收盘前

# 输出格式 (严格JSON格式)
"""

    json_block = """
{
  "market_state": "UPTREND|DOWNTREND|SIDEWAYS|REVERSAL|VOLATILE|OTHER",
  "reasoning": "你的完整分析思路,包括: 1)量价与盘口 2)趋势结构 3)关键技术位 4)风险与可交易性",
  "signal": "buy|sell|hold|close|adjust_stop",
  "confidence": 0.75,

  // 入场与目标（若 signal 为 buy/sell 建议给出）
  "entry_price": 550.50,
  "stop_loss": 548.00,
  "stop_loss_reason": "依据结构/ATR",
  // 止盈交给动态管理（可选初步目标位）
  "profit_target": null,
  "profit_target_reason": "可选：初步目标位或规模化减仓触发",
  "invalidation_condition": "什么情况下观点失效，需立即离场",

  // 仓位与可交易性
  "position_size_pct": 0.7,
  "tradeability_score": 0.8,
  "order_price_style": "best|mid|market|limit",
  "limit_offset_ticks": 0,

  // 动态管理与节拍
  "expected_holding_time_minutes": 15,
  "risk_reward_ratio": 2.0,
  "trailing_type": "atr|percent|none",
  "trailing_atr_mult": 2.0,
  "trailing_percent": 0,
  "scale_out_levels_r": [1.8, 2.5, 3.0],
  "scale_out_pcts": [0.33, 0.33, 0.34],
  "time_stop_minutes": 0,
  "cooldown_minutes": 0,

  // 波浪/结构（可选）
  "wave_primary": "Minor Impulse: now in 3",
  "wave_alt": "Alternate: ending diagonal",
  "wave_invalidation": 546.50
}
"""

    tail = """

**重要说明**:
- 如果signal是"hold"且已有持仓,可以输出"adjust_stop"来动态调整止损
- 如果市场状态变化导致原交易逻辑失效,应立即"close"
- reasoning字段非常重要,需要说明你的决策依据

现在,请基于以上数据给出你的交易决策。
"""

    prompt = head + "\n" + json_block + tail

    return prompt


# ========================================
# 市场数据处理
# ========================================

class MarketDataCollector:
    """市场数据收集器 - 只做数据聚合,不做判断"""

    def __init__(self):
        self.tick_buffer = deque(maxlen=Config.TICK_WINDOW)
        self.kline_1m_buffer = []
        self.kline_1d_buffer = []
        self.depth5_buffer = deque(maxlen=Config.DEPTH_LIQ_WINDOW)

    def add_tick(self, tick):
        """添加tick数据"""
        # 兼容不同平台 Tick 字段命名，使用安全读取
        price = getattr(tick, 'last_price', getattr(tick, 'price', 0))
        volume = getattr(tick, 'last_volume', getattr(tick, 'volume', 0))
        # 盘口字段优先使用 *_price_1 命名，其次 *_price1，再次 *_price
        bid = (
            getattr(tick, 'bid_price_1', None)
            or getattr(tick, 'bid_price1', None)
            or getattr(tick, 'bid_price', None)
        )
        ask = (
            getattr(tick, 'ask_price_1', None)
            or getattr(tick, 'ask_price1', None)
            or getattr(tick, 'ask_price', None)
        )
        bid_vol = (
            getattr(tick, 'bid_volume_1', None)
            or getattr(tick, 'bid_volume1', None)
            or getattr(tick, 'bid_volume', None)
            or 0
        )
        ask_vol = (
            getattr(tick, 'ask_volume_1', None)
            or getattr(tick, 'ask_volume1', None)
            or getattr(tick, 'ask_volume', None)
            or 0
        )
        ts = getattr(tick, 'strtime', None)
        if not ts:
            try:
                ts = tick.datetime.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 计算五档深度与价差
        def _get_level(name_patterns):
            for attr in name_patterns:
                val = getattr(tick, attr, None)
                if val is not None:
                    return val
            return None

        # L1 prices for spread
        l1_bid = _get_level(["bid_price_1", "bid_price1", "bid_price"])
        l1_ask = _get_level(["ask_price_1", "ask_price1", "ask_price"])
        spread = (l1_ask - l1_bid) if (l1_ask is not None and l1_bid is not None) else (
            (ask - bid) if (ask is not None and bid is not None) else 0
        )

        # Sum depth of 1-5 levels (fallback到L1)
        sum_bid_5 = 0
        sum_ask_5 = 0
        for i in range(1, 6):
            bv = _get_level([f"bid_volume_{i}", f"bid_volume{i}"])
            av = _get_level([f"ask_volume_{i}", f"ask_volume{i}"])
            if bv is not None:
                sum_bid_5 += bv
            if av is not None:
                sum_ask_5 += av
        if sum_bid_5 == 0 and sum_ask_5 == 0:
            sum_bid_5 = bid_vol or 0
            sum_ask_5 = ask_vol or 0

        depth5 = sum_bid_5 + sum_ask_5
        if depth5 > 0:
            self.depth5_buffer.append(depth5)

        self.tick_buffer.append({
            'price': price,
            'volume': volume,
            'bid': bid if bid is not None else price,
            'ask': ask if ask is not None else price,
            'bid_vol': bid_vol,
            'ask_vol': ask_vol,
            'timestamp': ts,
            'spread': spread,
            'depth5': depth5
        })

    def update_klines(self, symbol):
        """更新K线数据"""
        # 使用正确的Gkoudai API: get_market_data() 返回 ArrayManager 对象
        # 1分钟K线
        try:
            am_1m = get_market_data(symbol, '1m')
        except Exception as e:
            Log(f"[警告] get_market_data('1m') 异常: {e}")
            am_1m = None

        if am_1m is not None and getattr(am_1m, 'count', 0) > 0:
            # ArrayManager对象有open, high, low, close, volume等numpy数组
            # 转换为字典列表格式供后续使用
            self.kline_1m_buffer = []
            for i in range(am_1m.count):
                self.kline_1m_buffer.append({
                    'open': am_1m.open[i],
                    'high': am_1m.high[i],
                    'low': am_1m.low[i],
                    'close': am_1m.close[i],
                    'volume': am_1m.volume[i]
                })
        else:
            # Fallback: 使用 query_history 回填，避免因实时通道未就绪而无数据
            try:
                bars_1m = query_history(symbol, '1m', number=Config.KLINE_1M_WINDOW)
                if bars_1m:
                    self.kline_1m_buffer = []
                    for bar in bars_1m:
                        self.kline_1m_buffer.append({
                            'open': bar.open_price,
                            'high': bar.high_price,
                            'low': bar.low_price,
                            'close': bar.close_price,
                            'volume': bar.volume
                        })
                    Log(f"[提示] 1m 使用历史数据回填: {len(self.kline_1m_buffer)} 根")
            except Exception as e:
                Log(f"[警告] query_history('1m') 异常: {e}")

        # 日K线
        try:
            am_1d = get_market_data(symbol, '1d')
        except Exception as e:
            Log(f"[警告] get_market_data('1d') 异常: {e}")
            am_1d = None

        if am_1d is not None and getattr(am_1d, 'count', 0) > 0:
            self.kline_1d_buffer = []
            for i in range(am_1d.count):
                self.kline_1d_buffer.append({
                    'open': am_1d.open[i],
                    'high': am_1d.high[i],
                    'low': am_1d.low[i],
                    'close': am_1d.close[i],
                    'volume': am_1d.volume[i]
                })
        else:
            # Fallback: 使用 query_history 回填日线
            try:
                bars_1d = query_history(symbol, '1d', number=Config.KLINE_1D_WINDOW)
                if bars_1d:
                    self.kline_1d_buffer = []
                    for bar in bars_1d:
                        self.kline_1d_buffer.append({
                            'open': bar.open_price,
                            'high': bar.high_price,
                            'low': bar.low_price,
                            'close': bar.close_price,
                            'volume': bar.volume
                        })
                    Log(f"[提示] 1d 使用历史数据回填: {len(self.kline_1d_buffer)} 根")
            except Exception as e:
                Log(f"[警告] query_history('1d') 异常: {e}")

    def calculate_indicators(self):
        """计算技术指标 - 以1分钟为节拍，并附带日线趋势与ZigZag摘要"""
        if len(self.kline_1m_buffer) < 120:
            Log(f"[调试] K线数据不足: {len(self.kline_1m_buffer)}/120, 等待更多数据...")
            return None

        # 1分钟序列
        closes_1m = [k['close'] for k in self.kline_1m_buffer]
        highs_1m = [k['high'] for k in self.kline_1m_buffer]
        lows_1m = [k['low'] for k in self.kline_1m_buffer]
        volumes_1m = [k['volume'] for k in self.kline_1m_buffer]

        # 指标（1分钟）
        ema_20 = self._calculate_ema(closes_1m, 20)
        ema_60 = self._calculate_ema(closes_1m, 60)
        macd, signal, hist = self._calculate_macd(closes_1m)
        rsi = self._calculate_rsi(closes_1m, 14)
        atr = self._calculate_atr(highs_1m, lows_1m, closes_1m, 14)

        # 量能分析（最近20根1分钟）
        avg_volume_20 = sum(volumes_1m[-20:]) / 20
        current_volume = volumes_1m[-1]
        volume_ratio = current_volume / avg_volume_20 if avg_volume_20 > 0 else 1.0
        if volume_ratio > 3.0:
            volume_state = "EXTREME_SURGE"
        elif volume_ratio > 1.5:
            volume_state = "SURGE"
        elif volume_ratio < 0.8:
            volume_state = "LOW"
        else:
            volume_state = "NORMAL"

        # 价格结构（最近20根1分钟）
        window_highs = highs_1m[-20:]
        window_lows = lows_1m[-20:]
        high_20 = max(window_highs)
        low_20 = min(window_lows)
        price_range_pct = ((high_20 - low_20) / low_20) * 100 if low_20 > 0 else 0

        # 日线趋势（仅使用已完成日线）
        d_ema_20 = d_ema_60 = d_macd = None
        d_trend = None
        if len(self.kline_1d_buffer) >= 60:
            d_closes = [k['close'] for k in self.kline_1d_buffer]
            d_ema_20 = self._calculate_ema(d_closes, 20)
            d_ema_60 = self._calculate_ema(d_closes, 60)
            d_macd, d_sig, d_hist = self._calculate_macd(d_closes)
            if d_ema_20 and d_ema_60:
                if d_ema_20 > d_ema_60 and d_macd > 0:
                    d_trend = 'UPTREND'
                elif d_ema_20 < d_ema_60 and d_macd < 0:
                    d_trend = 'DOWNTREND'
                else:
                    d_trend = 'SIDEWAYS'

        # ZigZag（基于1分钟收盘），输出简要摘要供AI分析
        zigzag = self._calculate_zigzag(closes_1m, threshold_pct=Config.ZIGZAG_THRESHOLD_PCT)
        zigzag_summary = None
        fib_summary = None
        if zigzag and zigzag.get('pivots'):
            piv = zigzag['pivots'][-6:]
            zigzag_summary = "; ".join([f"{p['type']}@{p['price']:.2f}" for p in piv])
            fib = zigzag.get('fib', {})
            fr = fib.get('retracements', {})
            fe = fib.get('extensions', {})
            if fr or fe:
                fib_summary = f"ret:0.382={fr.get('0.382','')},0.5={fr.get('0.5','')},0.618={fr.get('0.618','')}; ext:1.272={fe.get('1.272','')},1.618={fe.get('1.618','')}"

        return {
            'ema_20': ema_20,
            'ema_60': ema_60,
            'macd': macd,
            'macd_signal': signal,
            'macd_hist': hist,
            'rsi': rsi,
            'atr': atr,
            'avg_volume_20': avg_volume_20,
            'current_volume': current_volume,
            'volume_ratio': volume_ratio,
            'volume_state': volume_state,
            'high_20': high_20,
            'low_20': low_20,
            'price_range_pct': price_range_pct,
            'd_ema_20': d_ema_20 if d_ema_20 is not None else 0,
            'd_ema_60': d_ema_60 if d_ema_60 is not None else 0,
            'd_macd': d_macd if d_macd is not None else 0,
            'd_trend': d_trend if d_trend else 'N/A',
            'zigzag_summary': zigzag_summary if zigzag_summary else 'N/A',
            'fib_summary': fib_summary if fib_summary else 'N/A',
        }

    @staticmethod
    def _aggregate_to_5min(kline_1m_buffer):
        """将1分钟K线聚合为5分钟K线"""
        if len(kline_1m_buffer) < 5:
            return []

        kline_5m = []
        # 从最早的数据开始,每5根1分钟K线聚合成1根5分钟K线
        for i in range(0, len(kline_1m_buffer) - 4, 5):
            bars_5 = kline_1m_buffer[i:i+5]

            # 聚合OHLCV
            aggregated = {
                'open': bars_5[0]['open'],       # 第1根的开盘价
                'high': max(b['high'] for b in bars_5),  # 5根中的最高价
                'low': min(b['low'] for b in bars_5),    # 5根中的最低价
                'close': bars_5[-1]['close'],    # 第5根的收盘价
                'volume': sum(b['volume'] for b in bars_5)  # 5根的成交量之和
            }
            kline_5m.append(aggregated)

        return kline_5m

    @staticmethod
    def _calculate_ema(prices, period):
        """计算EMA"""
        multiplier = 2 / (period + 1)
        ema = prices[0]
        for price in prices[1:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def _calculate_macd(prices, fast=12, slow=26, signal=9):
        """计算MACD"""
        ema_fast = prices[0]
        ema_slow = prices[0]
        mult_fast = 2 / (fast + 1)
        mult_slow = 2 / (slow + 1)

        for price in prices[1:]:
            ema_fast = (price - ema_fast) * mult_fast + ema_fast
            ema_slow = (price - ema_slow) * mult_slow + ema_slow

        macd_line = ema_fast - ema_slow

        # Signal line (简化计算,实际应该用MACD序列的EMA)
        signal_line = macd_line * 0.8  # 简化
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    @staticmethod
    def _calculate_rsi(prices, period=14):
        """计算RSI"""
        if len(prices) < period + 1:
            return 50

        gains = []
        losses = []
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def _calculate_atr(highs, lows, closes, period=14):
        """计算ATR"""
        if len(highs) < period + 1:
            return 0

        trs = []
        for i in range(1, len(highs)):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i-1])
            low_close = abs(lows[i] - closes[i-1])
            tr = max(high_low, high_close, low_close)
            trs.append(tr)

        atr = sum(trs[-period:]) / period
        return atr

    @staticmethod
    def _calculate_zigzag(closes, threshold_pct=0.3):
        """简单ZigZag：当价差超过阈值百分比时确认枢轴点。返回最近枢轴与斐波位。"""
        if not closes or len(closes) < 10:
            return None
        th = abs(float(threshold_pct)) / 100.0
        pivots = []
        last_pivot_price = closes[0]
        last_pivot_type = 'H'  # seed, will flip quickly
        extreme_price = closes[0]
        extreme_idx = 0
        direction = 0  # 1 up, -1 down, 0 unknown
        for i, px in enumerate(closes[1:], start=1):
            if direction >= 0:
                # looking for new high
                if px > extreme_price:
                    extreme_price = px
                    extreme_idx = i
                drawdown = (extreme_price - px) / extreme_price if extreme_price > 0 else 0
                if direction == 1 and drawdown >= th:
                    # confirm high
                    pivots.append({'idx': extreme_idx, 'price': extreme_price, 'type': 'H'})
                    last_pivot_price = extreme_price
                    last_pivot_type = 'H'
                    direction = -1
                    extreme_price = px
                    extreme_idx = i
                elif direction == 0:
                    # establish initial direction
                    up_move = (px - last_pivot_price) / last_pivot_price if last_pivot_price > 0 else 0
                    if up_move >= th:
                        direction = 1
                        extreme_price = px
                        extreme_idx = i
            if direction <= 0:
                # looking for new low
                if px < extreme_price:
                    extreme_price = px
                    extreme_idx = i
                drawup = (px - extreme_price) / extreme_price if extreme_price != 0 else 0
                if direction == -1 and drawup >= th:
                    # confirm low
                    pivots.append({'idx': extreme_idx, 'price': extreme_price, 'type': 'L'})
                    last_pivot_price = extreme_price
                    last_pivot_type = 'L'
                    direction = 1
                    extreme_price = px
                    extreme_idx = i
                elif direction == 0:
                    down_move = (last_pivot_price - px) / last_pivot_price if last_pivot_price > 0 else 0
                    if down_move >= th:
                        direction = -1
                        extreme_price = px
                        extreme_idx = i

        # 斐波水平（基于最后一段）
        fib = {}
        if len(pivots) >= 2:
            a = pivots[-2]['price']
            b = pivots[-1]['price']
            leg = b - a
            if leg != 0:
                def _fmt(v):
                    return f"{v:.2f}"
                retr = {
                    '0.382': _fmt(b - 0.382*leg),
                    '0.5': _fmt(b - 0.5*leg),
                    '0.618': _fmt(b - 0.618*leg)
                }
                ext = {
                    '1.272': _fmt(b + 1.272*leg),
                    '1.618': _fmt(b + 1.618*leg)
                }
                fib = {'retracements': retr, 'extensions': ext}
        return {'pivots': pivots, 'fib': fib}


# ========================================
# AI决策引擎
# ========================================

class AIDecisionEngine:
    """AI决策引擎 - 调用DeepSeek API"""

    @staticmethod
    def call_deepseek_api(prompt):
        """调用DeepSeek API获取决策"""
        if not Config.DEEPSEEK_API_KEY or "请在此填写" in Config.DEEPSEEK_API_KEY:
            return None, "未配置 DeepSeek Key：请在 Config.DEEPSEEK_API_KEY 填写你的 API Key"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {Config.DEEPSEEK_API_KEY}'
        }

        payload = {
            'model': Config.DEEPSEEK_MODEL,
            'messages': [
                {
                    'role': 'system',
                    'content': '你是一位专业的期货交易员,擅长技术分析和风险管理。请严格按照JSON格式输出决策。'
                },
                {
                    'role': 'user',
                    'content': prompt
                }
            ],
            'temperature': Config.DEEPSEEK_TEMPERATURE,
            'max_tokens': Config.DEEPSEEK_MAX_TOKENS
        }

        for attempt in range(Config.API_MAX_RETRIES):
            try:
                response = requests.post(
                    Config.DEEPSEEK_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=Config.API_TIMEOUT
                )

                if response.status_code == 200:
                    result = response.json()
                    content = result['choices'][0]['message']['content']

                    # 提取JSON (可能被markdown代码块包裹)
                    if '```json' in content:
                        content = content.split('```json')[1].split('```')[0].strip()
                    elif '```' in content:
                        content = content.split('```')[1].split('```')[0].strip()

                    decision = json.loads(content)
                    return decision, None
                else:
                    error_msg = f"API错误: {response.status_code} - {response.text}"
                    if attempt < Config.API_MAX_RETRIES - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return None, error_msg

            except Exception as e:
                error_msg = f"API调用异常: {str(e)}"
                if attempt < Config.API_MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None, error_msg

        return None, "API调用失败,已达最大重试次数"


# ========================================
# 交易执行引擎
# ========================================

class TradeExecutor:
    """交易执行引擎 - 执行AI决策"""

    @staticmethod
    def execute_decision(context, symbol, decision, tick, state):
        """执行AI决策"""
        signal = decision.get('signal', 'hold')
        confidence = decision.get('confidence', 0)

        # 信心度检查
        if confidence < Config.MIN_AI_CONFIDENCE:
            Log(f"AI信心度不足 ({confidence:.2f} < {Config.MIN_AI_CONFIDENCE}), 不执行交易")
            return

        # 获取当前持仓 (使用正确的Gkoudai API)
        current_volume = get_pos(symbol)  # 返回整数: 正数=多头, 负数=空头, 0=空仓

        # 读取盘口价格，用于更贴近可成交价
        last_price = getattr(tick, 'last_price', getattr(tick, 'price', 0))
        bid_price = (
            getattr(tick, 'bid_price_1', None)
            or getattr(tick, 'bid_price1', None)
            or getattr(tick, 'bid_price', None)
            or last_price
        )
        ask_price = (
            getattr(tick, 'ask_price_1', None)
            or getattr(tick, 'ask_price1', None)
            or getattr(tick, 'ask_price', None)
            or last_price
        )

        # 读取AI可选字段
        order_price_style = str(decision.get('order_price_style', 'best')).lower()
        tradeability_score = float(decision.get('tradeability_score', 1.0))
        cooldown_minutes = float(decision.get('cooldown_minutes', 0) or 0)
        # 动态止盈相关（交由AI管理）
        trailing_type = str(decision.get('trailing_type', 'none') or 'none').lower()
        trailing_atr_mult = float(decision.get('trailing_atr_mult', 0) or 0)
        trailing_percent = float(decision.get('trailing_percent', 0) or 0)
        time_stop_minutes = float(decision.get('time_stop_minutes', 0) or 0)

        # —— 日线自适应默认（若AI未提供则补齐） ——
        adaptive = state.get('adaptive') or {}
        if cooldown_minutes <= 0:
            cooldown_minutes = float(adaptive.get('cooldown_minutes') or 0)
        # 填仓位默认
        if not decision.get('position_size_pct') or float(decision.get('position_size_pct') or 0) <= 0:
            decision['position_size_pct'] = float(adaptive.get('position_size_pct') or 0.5)
        # 填追踪默认
        if trailing_type == 'none':
            ttype = str(adaptive.get('trailing_type') or 'none').lower()
            if ttype != 'none':
                trailing_type = ttype
                if ttype == 'atr' and trailing_atr_mult <= 0:
                    trailing_atr_mult = float(adaptive.get('trailing_atr_mult') or 0)
                if ttype == 'percent' and trailing_percent <= 0:
                    trailing_percent = float(adaptive.get('trailing_percent') or 0)

        # 结合市场流动性对仓位/新仓进行 gating
        md = state.get('last_market_data') if isinstance(state, dict) else None
        liq_state = md.get('liquidity_state') if isinstance(md, dict) else None
        spread_val = md.get('spread') if isinstance(md, dict) else None
        mid_px_val = md.get('mid_price') if isinstance(md, dict) else last_price

        def _choose_price(side):
            # side: 'buy' or 'sell'
            if order_price_style == 'mid' and mid_px_val:
                return mid_px_val
            if order_price_style == 'market':
                return last_price
            # 'best' or default: 用盘口最优价
            if side == 'buy':
                return ask_price
            else:
                return bid_price

        def _adjust_position_size(base_pct):
            pct = max(0.0, min(1.0, float(base_pct)))
            # AI 自评可交易性 gating
            if tradeability_score < 0.5:
                Log(f"AI自评可交易性较差({tradeability_score:.2f})，拒绝新仓")
                return 0.0
            if tradeability_score < 0.7:
                pct = min(pct, 0.3)

            # 市场流动性 gating
            if liq_state == 'THIN':
                pct = min(pct, 0.3)
            if spread_val and mid_px_val and mid_px_val > 0:
                if (spread_val / mid_px_val) > 0.001:  # 点差>万分之10
                    pct = min(pct, 0.3)
            return pct

        # 账户与合约参数 —— 用真实数据替代固定值
        # 账户快照：使用本地轻量估算（基于 get_pos + _G 均价/已实盈亏）
        acc = estimate_account(context, symbol, last_price, state)
        equity = acc['equity']
        available = acc['available']
        used_margin = acc['margin']

        mult = PlatformAdapter.get_contract_size(symbol)
        tick_size = PlatformAdapter.get_pricetick(symbol) or 0
        min_vol = PlatformAdapter.get_min_volume(symbol)
        long_mr = PlatformAdapter.get_margin_ratio(symbol, 'long')
        short_mr = PlatformAdapter.get_margin_ratio(symbol, 'short')

        def _round_price(p):
            if not tick_size or tick_size <= 0:
                return p
            try:
                return round(p / tick_size) * tick_size
            except Exception:
                return p

        # 冷却期内禁止新开仓
        if signal in ('buy', 'sell') and current_volume == 0:
            cooldown_until = state.get('cooldown_until') if isinstance(state, dict) else None
            if isinstance(cooldown_until, (int, float)) and time.time() < cooldown_until:
                left = int(cooldown_until - time.time())
                Log(f"[{symbol}] 处于冷却期，剩余 {left}s，跳过新仓信号 {signal}")
                return

        if signal == 'buy' and current_volume == 0:
            # 开多仓
            position_size = _adjust_position_size(decision.get('position_size_pct', 0.5))
            if position_size <= 0:
                return
            order_price = _round_price(_choose_price('buy'))
            price_for_size = order_price if order_price > 0 else last_price

            notional_per_lot = price_for_size * mult
            margin_per_lot = notional_per_lot * max(long_mr, 0.01)
            # 用账户可用资金推导最大可开手数（留一点安全边际）
            if margin_per_lot <= 0:
                Log(f"[{symbol}] 保证金率异常({long_mr:.4f}), 跳过新仓")
                return
            max_lots_by_margin = int((available / (margin_per_lot * Config.NEW_TRADE_MARGIN_BUFFER)))
            # 同时用 position_size 控制仓位（按权益比例）
            target_notional = equity * position_size
            lots_by_target = int(target_notional / notional_per_lot) if notional_per_lot > 0 else 0
            volume = min(max_lots_by_margin, lots_by_target)
            min_lots = max(1, int(min_vol))
            if volume < min_lots:
                # 目标仓位不足1手但保证金充足 → 按最小手数尝试
                if lots_by_target < min_lots and max_lots_by_margin >= min_lots:
                    volume = min_lots
                    Log(f"[{symbol}] 目标仓位不足{min_lots}手，按最小单位下单。可用:{available:.0f}, 单手保证金:{margin_per_lot:.0f}")
                else:
                    Log(f"[{symbol}] 保证金不足，拒绝新仓。可用:{available:.0f}, 单手保证金:{margin_per_lot:.0f}")
                    return

            if volume > 0:
                # 下单前担保比校验
                margin_post = used_margin + volume * margin_per_lot
                guarantee_ratio = (equity / margin_post) if margin_post > 0 else 999
                if guarantee_ratio < Config.MIN_GUARANTEE_RATIO:
                    Log(f"[{symbol}] 担保比不足({guarantee_ratio:.2f} < {Config.MIN_GUARANTEE_RATIO:.2f}), 拒绝新仓")
                    return

                buy(symbol, order_price, volume)
                Log(f"[{symbol}] AI决策: 开多 {volume}手 @ {order_price:.2f}, 信心度={confidence:.2f}")
                _sl = decision.get('stop_loss')
                _pt = decision.get('profit_target')
                _sl_txt = f"{float(_sl):.2f}" if isinstance(_sl, (int, float)) else "N/A"
                _pt_txt = f"{float(_pt):.2f}" if isinstance(_pt, (int, float)) else "N/A"
                Log(f"止损={_sl_txt}, 止盈={_pt_txt}")
                Log(f"[{symbol}] 规模: equity={equity:.0f}, available={available:.0f}, notional/lot={notional_per_lot:.0f}, margin/lot={margin_per_lot:.0f}, target_lots={lots_by_target}, max_lots={max_lots_by_margin}, choose={volume}; used_margin→{margin_post:.0f}, 担保比={guarantee_ratio:.2f}")

                # 记录决策和持仓均价
                state['ai_decision'] = decision
                state['entry_time'] = datetime.now()
                state['position_avg_price'] = order_price
                # 初始化追踪与峰值/谷值
                state['trailing'] = {
                    'type': trailing_type,
                    'atr_mult': trailing_atr_mult,
                    'percent': trailing_percent,
                    'time_stop_minutes': time_stop_minutes,
                }
                state['peak_price'] = order_price
                state['trough_price'] = order_price

        elif signal == 'sell' and current_volume == 0:
            # 开空仓
            position_size = _adjust_position_size(decision.get('position_size_pct', 0.5))
            if position_size <= 0:
                return
            order_price = _round_price(_choose_price('sell'))
            price_for_size = order_price if order_price > 0 else last_price
            notional_per_lot = price_for_size * mult
            margin_per_lot = notional_per_lot * max(short_mr, 0.01)
            if margin_per_lot <= 0:
                Log(f"[{symbol}] 保证金率异常({short_mr:.4f}), 跳过新仓")
                return
            max_lots_by_margin = int((available / (margin_per_lot * Config.NEW_TRADE_MARGIN_BUFFER)))
            target_notional = equity * position_size
            lots_by_target = int(target_notional / notional_per_lot) if notional_per_lot > 0 else 0
            volume = min(max_lots_by_margin, lots_by_target)
            min_lots = max(1, int(min_vol))
            if volume < min_lots:
                if lots_by_target < min_lots and max_lots_by_margin >= min_lots:
                    volume = min_lots
                    Log(f"[{symbol}] 目标仓位不足{min_lots}手，按最小单位下单。可用:{available:.0f}, 单手保证金:{margin_per_lot:.0f}")
                else:
                    Log(f"[{symbol}] 保证金不足，拒绝新仓。可用:{available:.0f}, 单手保证金:{margin_per_lot:.0f}")
                    return

            if volume > 0:
                margin_per_lot = notional_per_lot * max(short_mr, 0.01)
                margin_post = used_margin + volume * margin_per_lot
                guarantee_ratio = (equity / margin_post) if margin_post > 0 else 999
                if guarantee_ratio < Config.MIN_GUARANTEE_RATIO:
                    Log(f"[{symbol}] 担保比不足({guarantee_ratio:.2f} < {Config.MIN_GUARANTEE_RATIO:.2f}), 拒绝新仓")
                    return

                short(symbol, order_price, volume)
                Log(f"[{symbol}] AI决策: 开空 {volume}手 @ {order_price:.2f}, 信心度={confidence:.2f}")
                _sl = decision.get('stop_loss')
                _pt = decision.get('profit_target')
                _sl_txt = f"{float(_sl):.2f}" if isinstance(_sl, (int, float)) else "N/A"
                _pt_txt = f"{float(_pt):.2f}" if isinstance(_pt, (int, float)) else "N/A"
                Log(f"止损={_sl_txt}, 止盈={_pt_txt}")
                Log(f"[{symbol}] 规模: equity={equity:.0f}, available={available:.0f}, notional/lot={notional_per_lot:.0f}, margin/lot={margin_per_lot:.0f}, target_lots={lots_by_target}, max_lots={max_lots_by_margin}, choose={volume}; used_margin→{margin_post:.0f}, 担保比={guarantee_ratio:.2f}")

                state['ai_decision'] = decision
                state['entry_time'] = datetime.now()
                state['position_avg_price'] = order_price
                state['trailing'] = {
                    'type': trailing_type,
                    'atr_mult': trailing_atr_mult,
                    'percent': trailing_percent,
                    'time_stop_minutes': time_stop_minutes,
                }
                state['peak_price'] = order_price
                state['trough_price'] = order_price

        elif signal == 'close' and current_volume != 0:
            # 平仓 - 使用send_target_order设置目标仓位为0
            send_target_order(symbol, 0)
            Log(f"[{symbol}] AI决策: 平仓 {abs(current_volume)}手 @ {last_price:.2f}")
            Log(f"原因: {decision.get('reasoning', 'N/A')}")

            state['ai_decision'] = None
            state['entry_time'] = None
            state['position_avg_price'] = 0

        elif signal == 'adjust_stop' and current_volume != 0:
            # 动态调整止损
            if isinstance(state.get('ai_decision'), dict):
                state['ai_decision']['stop_loss'] = decision.get('stop_loss')
                state['ai_decision']['profit_target'] = decision.get('profit_target')
                # 同步追踪配置
                ttype = str(decision.get('trailing_type', state.get('trailing',{}).get('type','none')) or 'none').lower()
                state['trailing'] = {
                    'type': ttype,
                    'atr_mult': float(decision.get('trailing_atr_mult', state.get('trailing',{}).get('atr_mult',0)) or 0),
                    'percent': float(decision.get('trailing_percent', state.get('trailing',{}).get('percent',0)) or 0),
                    'time_stop_minutes': float(decision.get('time_stop_minutes', state.get('trailing',{}).get('time_stop_minutes',0)) or 0),
                }
            _sl = decision.get('stop_loss')
            _sl_txt = f"{float(_sl):.2f}" if isinstance(_sl, (int, float)) else "N/A"
            Log(f"[{symbol}] AI决策: 调整止损至 {_sl_txt}")

        # 冷却时间（改为：仅在成交后由 on_order_status 生效）
        try:
            state['pending_cooldown_minutes'] = float(cooldown_minutes or 0)
        except Exception:
            state['pending_cooldown_minutes'] = None


# ========================================
# 风控层 (唯一的硬性约束)
# ========================================

class RiskController:
    """风控控制器 - 执行安全边界"""

    @staticmethod
    def check_and_enforce(context, symbol, tick, state):
        """检查并执行风控规则"""
        # 获取当前持仓 (使用正确的Gkoudai API)
        position_volume = get_pos(symbol)

        if position_volume == 0:
            return  # 无持仓, 无需风控检查

        current_price = getattr(tick, 'last_price', getattr(tick, 'price', 0))

        # 注意: Gkoudai的get_pos()只返回数量, 无法直接获取持仓均价
        # 我们需要在开仓时记录均价, 这里使用context保存的持仓信息
        avg_price_in_state = state.get('position_avg_price') if isinstance(state, dict) else 0
        if not avg_price_in_state:
            # 如果没有记录均价, 暂时无法计算盈亏, 跳过单笔亏损检查
            Log(f"[{symbol}] [警告] 无持仓均价记录, 跳过单笔亏损检查")
        else:
            avg_price = avg_price_in_state

            # 计算盈亏
            mult = PlatformAdapter.get_contract_size(symbol)
            if position_volume > 0:  # 多头
                unrealized_pnl = (current_price - avg_price) * abs(position_volume) * mult
            else:  # 空头
                unrealized_pnl = (avg_price - current_price) * abs(position_volume) * mult

            # 账户权益：使用本地轻量估算
            acc = estimate_account(context, symbol, current_price, state)
            account_value = acc['equity'] + 0.0
            pnl_pct = unrealized_pnl / account_value if account_value > 0 else 0

            # 1. 单笔最大亏损检查
            if pnl_pct < -Config.MAX_SINGLE_TRADE_LOSS_PCT:
                Log(f"[{symbol}] [警告] 触发单笔最大亏损限制 ({pnl_pct*100:.2f}%), 强制平仓!")
                send_target_order(symbol, 0)
                state['ai_decision'] = None
                state['position_avg_price'] = 0
                return

        # 2. 单日最大亏损检查（以本地估算的权益为基准）
        base_equity = max(1.0, float(estimate_account(context, symbol, current_price, state)['equity'] or 0.0))
        daily_pnl_pct = context.daily_pnl / base_equity
        if daily_pnl_pct < -Config.MAX_DAILY_LOSS_PCT:
            Log(f"[{symbol}] [警告] 触发单日最大亏损限制 ({daily_pnl_pct*100:.2f}%), 停止交易!")
            if position_volume != 0:
                send_target_order(symbol, 0)
            context.trading_allowed = False
            state['ai_decision'] = None
            state['position_avg_price'] = 0
            return

        # 3. 强制平仓时间检查（兼容无 strtime 的 TickData）
        try:
            ts = getattr(tick, 'strtime', None)
            if ts:
                current_time = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S').time()
            else:
                dt_obj = getattr(tick, 'datetime', None)
                if isinstance(dt_obj, datetime):
                    current_time = dt_obj.time()
                else:
                    current_time = datetime.now().time()
        except Exception:
            current_time = datetime.now().time()
        force_close_time = datetime.strptime(Config.FORCE_CLOSE_TIME, '%H:%M:%S').time()

        if current_time >= force_close_time:
            Log(f"[{symbol}] [警告] 到达强制平仓时间 {Config.FORCE_CLOSE_TIME}, 强制平仓!")
            send_target_order(symbol, 0)
            context.trading_allowed = False
            state['ai_decision'] = None
            state['position_avg_price'] = 0
            return

        # 4. AI设定的止损 + 动态追踪/时间止盈检查（止盈不再刚性）
        if state.get('ai_decision'):
            stop_loss = state['ai_decision'].get('stop_loss')
            # 硬止损：始终生效
            if stop_loss:
                if position_volume > 0 and current_price <= stop_loss:
                    Log(f"[{symbol}] 触发AI止损 ({current_price:.2f} <= {float(stop_loss):.2f}), 平仓!")
                    send_target_order(symbol, 0)
                    state['ai_decision'] = None
                    state['position_avg_price'] = 0
                    return
                if position_volume < 0 and current_price >= stop_loss:
                    Log(f"[{symbol}] 触发AI止损 ({current_price:.2f} >= {float(stop_loss):.2f}), 平仓!")
                    send_target_order(symbol, 0)
                    state['ai_decision'] = None
                    state['position_avg_price'] = 0
                    return

            # 动态追踪止损（atr/percent）
            trailing = state.get('trailing') or {}
            ttype = str(trailing.get('type', 'none') or 'none').lower()
            if ttype != 'none':
                try:
                    # 更新峰值/谷值
                    if position_volume > 0:
                        state['peak_price'] = max(float(state.get('peak_price') or current_price), current_price)
                    else:
                        state['trough_price'] = min(float(state.get('trough_price') or current_price), current_price)
                except Exception:
                    state['peak_price'] = current_price
                    state['trough_price'] = current_price

                dyn_sl = None
                atr_mult = float(trailing.get('atr_mult') or 0)
                pct = float(trailing.get('percent') or 0)
                md = state.get('last_market_data') if isinstance(state, dict) else None
                atr_val = (md.get('atr') if isinstance(md, dict) else None) or 0
                if position_volume > 0:
                    candidates = []
                    if ttype == 'atr' and atr_mult > 0 and atr_val > 0:
                        candidates.append((state.get('peak_price', current_price) - atr_mult * atr_val))
                    if ttype == 'percent' and pct > 0:
                        candidates.append((state.get('peak_price', current_price) * (1 - pct/100)))
                    if candidates:
                        dyn_sl = max(candidates)
                        if current_price <= dyn_sl:
                            Log(f"[{symbol}] 触发动态追踪止损(long): {current_price:.2f} <= {dyn_sl:.2f}")
                            send_target_order(symbol, 0)
                            state['ai_decision'] = None
                            state['position_avg_price'] = 0
                            return
                if position_volume < 0:
                    candidates = []
                    if ttype == 'atr' and atr_mult > 0 and atr_val > 0:
                        candidates.append((state.get('trough_price', current_price) + atr_mult * atr_val))
                    if ttype == 'percent' and pct > 0:
                        candidates.append((state.get('trough_price', current_price) * (1 + pct/100)))
                    if candidates:
                        dyn_sl = min(candidates)
                        if current_price >= dyn_sl:
                            Log(f"[{symbol}] 触发动态追踪止损(short): {current_price:.2f} >= {dyn_sl:.2f}")
                            send_target_order(symbol, 0)
                            state['ai_decision'] = None
                            state['position_avg_price'] = 0
                            return

            # 时间止盈（超时离场）
            try:
                ts_min = float(trailing.get('time_stop_minutes') or 0)
                if ts_min > 0 and state.get('entry_time'):
                    hold_m = (datetime.now() - state['entry_time']).total_seconds() / 60.0
                    if hold_m >= ts_min:
                        Log(f"[{symbol}] 触发时间离场: 持仓{hold_m:.1f}min ≥ {ts_min:.1f}min")
                        send_target_order(symbol, 0)
                        state['ai_decision'] = None
                        state['position_avg_price'] = 0
                        return
            except Exception:
                pass


# ========================================
# 策略主函数
# ========================================

def on_init(context):
    """策略初始化"""
    context.symbols = list(Config.SYMBOLS) if hasattr(Config, 'SYMBOLS') else [Config.SYMBOL]
    Log(f"========== AI自主交易策略启动 ==========")
    Log(f"交易品种: {', '.join(context.symbols)}")
    Log(f"AI决策间隔: {Config.AI_DECISION_INTERVAL}秒")
    Log(f"安全边界: 单笔最大亏损{Config.MAX_SINGLE_TRADE_LOSS_PCT*100:.1f}%, 单日最大亏损{Config.MAX_DAILY_LOSS_PCT*100:.1f}%")

    # 在 on_init 即发起数据订阅，确保平台能建立订阅流
    for sym in context.symbols:
        subscribe(sym, '1m', Config.KLINE_1M_WINDOW)
        subscribe(sym, '1d', Config.KLINE_1D_WINDOW)

    # 初始化组件
    context.ai_engine = AIDecisionEngine()
    context.executor = TradeExecutor()
    context.risk_controller = RiskController()

    # 状态变量
    # 每个标的的独立状态
    context.state = {}
    for sym in context.symbols:
        context.state[sym] = {
            'data_collector': MarketDataCollector(),
            'ai_decision': None,
            'last_ai_call_time': 0,
            'entry_time': None,
            'position_avg_price': 0,
            'last_market_data': None,
            'cooldown_until': None,
            'intraday': {
                'trading_day': None,
                'open': None,
                'high': None,
                'low': None,
                'prev_close': None,
                'open_time': None,
                'source': 'intraday'
            },
            'trailing': None,
            'peak_price': None,
            'trough_price': None,
        }
    # 兼容旧字段（不再使用，保留以避免引用错误）
    context.ai_decision = None
    context.last_ai_call_time = 0
    context.entry_time = None
    context.trading_allowed = True

    # 初始化资金 (Gkoudai平台无context.account()方法, 使用固定初始资金)
    context.initial_cash = 100000  # 默认10万初始资金

    # 兼容旧字段（单标的模式），多标的时用 context.state[sym]['position_avg_price']
    context.position_avg_price = 0

    context.daily_pnl = 0
    context.daily_trades = 0
    context.daily_wins = 0

    # 读取 _G 轻持久化的持仓快照（均价/已实盈亏/追踪参数）
    for sym in context.symbols:
        try:
            snap = _G(f"pos:{sym}")
            if isinstance(snap, dict):
                # 恢复均价/已实现盈亏/追踪参数/入场时间
                context.state[sym]['position_avg_price'] = float(snap.get('avg_price') or 0)
                context.state[sym]['realized_pnl'] = float(snap.get('realized_pnl') or 0)
                context.state[sym]['trailing'] = snap.get('trailing') or None
                et = snap.get('entry_time')
                if et:
                    try:
                        context.state[sym]['entry_time'] = datetime.strptime(et, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        context.state[sym]['entry_time'] = None
        except Exception:
            pass
        # 填充默认键
        if 'realized_pnl' not in context.state[sym]:
            context.state[sym]['realized_pnl'] = 0.0
        if 'pending_cooldown_minutes' not in context.state[sym]:
            context.state[sym]['pending_cooldown_minutes'] = None
        # 恢复日内统计（可选）
        try:
            intr = _G(f"intraday:{sym}")
            if isinstance(intr, dict):
                context.state[sym]['intraday'].update(intr)
        except Exception:
            pass


def on_start(context):
    """策略启动后的回调 - 在on_init之后,数据订阅完成后执行"""
    try:
        Log("策略启动完成,开始主动加载历史数据...")

        # ===== 为每个标的主动回填历史数据，确保启动时就有足够的300根1分钟K线 =====
        for sym in context.symbols:
            dc = context.state[sym]['data_collector']
            try:
                bars_1m = query_history(sym, '1m', number=300)
                if bars_1m and len(bars_1m) >= 300:
                    dc.kline_1m_buffer = []
                    for bar in bars_1m:
                        dc.kline_1m_buffer.append({
                            'open': bar.open_price,
                            'high': bar.high_price,
                            'low': bar.low_price,
                            'close': bar.close_price,
                            'volume': bar.volume
                        })
                    Log(f"[{sym}] ✅ 1分钟历史数据加载成功: {len(dc.kline_1m_buffer)} 根")
                else:
                    actual_count = len(bars_1m) if bars_1m else 0
                    Log(f"[{sym}] ⚠️ 1分钟历史数据不足: 获取到 {actual_count}/300 根, 将在运行中累积")
            except Exception as e:
                Log(f"[{sym}] ⚠️ 1分钟历史数据加载失败: {e}, 将在运行中累积")

            try:
                bars_1d = query_history(sym, '1d', number=50)
                if bars_1d:
                    dc.kline_1d_buffer = []
                    for bar in bars_1d:
                        dc.kline_1d_buffer.append({
                            'open': bar.open_price,
                            'high': bar.high_price,
                            'low': bar.low_price,
                            'close': bar.close_price,
                            'volume': bar.volume
                        })
                    Log(f"[{sym}] ✅ 日线历史数据加载成功: {len(dc.kline_1d_buffer)} 根")
                else:
                    Log(f"[{sym}] ⚠️ 日线历史数据加载失败")
            except Exception as e:
                Log(f"[{sym}] ⚠️ 日线历史数据加载失败: {e}")

            # 轻量自检：显示最新bar时间
            try:
                bar1m = get_current_bar(sym, '1m')
                if bar1m:
                    Log(f"[{sym}] [提示] 当前1m最新时间: {bar1m.datetime.strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception:
                pass
            try:
                bar1d = get_current_bar(sym, '1d')
                if bar1d:
                    Log(f"[{sym}] [提示] 当前1d最新时间: {bar1d.datetime.strftime('%Y-%m-%d')}")
            except Exception:
                pass

            # 启动后立即尝试计算一次指标，验证数据是否充足
            indicators = dc.calculate_indicators()
            if indicators:
                Log(f"[{sym}] ✅ 技术指标计算成功, EMA20={indicators['ema_20']:.2f}, RSI={indicators['rsi']:.2f}")
            else:
                Log(f"[{sym}] ⏳ 数据暂不充足, 将在运行中继续累积...")

        Log("🚀 多标的策略已就绪, 等待首次AI决策...")
    except Exception as e:
        # 兜底，避免异常向外抛出导致平台判定 on_start 失败
        try:
            Log(f"[致命] on_start 异常: {e}")
            Log(traceback.format_exc()[:1000])
        except Exception:
            pass


def on_tick(context, tick):
    """Tick级别回调 - 核心交易逻辑"""
    # 识别tick所属标的
    # 尝试解析tick所属标的，尽量匹配到我们订阅的 key（形如 'au2512.SHFE'）
    raw_candidates = []
    for attr in [
        'symbol', 'vt_symbol', 'code', 'ins', 'instrument', 'contract', 'symbol_id', 'security', 'security_id'
    ]:
        val = getattr(tick, attr, None)
        if val:
            raw_candidates.append(str(val))

    def _resolve_symbol(candidates):
        keys = list(getattr(context, 'state', {}).keys())
        if not keys:
            return None
        for cand in candidates:
            cu = cand.upper()
            for key in keys:
                ku = key.upper()
                base = key.split('.')[0].upper()
                # 直接等于 / 等于去掉交易所后缀 / 包含关系
                if cu == ku or cu == base or cu in ku or base in cu:
                    return key
        # 兜底：返回第一个订阅的品种
        return keys[0]

    sym = _resolve_symbol(raw_candidates)
    if not sym:
        return

    # 首次tick到达提示，便于确认订阅已生效
    first_flag_name = "first_tick_logged__%s" % sym
    if not hasattr(context, first_flag_name):
        first_ts = getattr(tick, 'strtime', None)
        if not first_ts:
            try:
                first_ts = tick.datetime.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                first_ts = ''
        first_price = getattr(tick, 'last_price', getattr(tick, 'price', 0))
        Log(f"[{sym}] [提示] 首次tick: {first_ts} 价:{first_price}")
        setattr(context, first_flag_name, True)

    # 缓存tick数据（按标的）
    state = context.state[sym]
    dc = state['data_collector']
    dc.add_tick(tick)
    # 更新最新价（用于本地估算账户）—直接在估算函数里使用 tick.last_price，无需保存

    # 更新本交易日的日内统计（开/高/低）
    try:
        ts = getattr(tick, 'strtime', None)
        if ts:
            dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
        else:
            dt = getattr(tick, 'datetime', None) or datetime.now()
        # 交易日映射：夜盘21:00起归属次一自然日
        if dt.time() >= datetime_time(21,0,0):
            trading_day = (dt.date()).toordinal() + 1
        else:
            trading_day = dt.date().toordinal()
        td_key = trading_day
        intr = state.get('intraday', {})
        cur_px = getattr(tick, 'last_price', getattr(tick, 'price', 0))
        if intr.get('trading_day') != td_key:
            # 新交易日初始化
            intr = {
                'trading_day': td_key,
                'open': cur_px,
                'high': cur_px,
                'low': cur_px,
                'open_time': ts or dt.strftime('%Y-%m-%d %H:%M:%S'),
                'prev_close': None,
                'source': 'intraday',
            }
            # 设置昨日收盘（若有）
            try:
                if dc.kline_1d_buffer:
                    last_d = dc.kline_1d_buffer[-1]
                    intr['prev_close'] = last_d['close']
            except Exception:
                pass
            state['intraday'] = intr
        else:
            # 更新高低
            try:
                if cur_px is not None:
                    if intr.get('high') is None or cur_px > intr['high']:
                        intr['high'] = cur_px
                    if intr.get('low') is None or cur_px < intr['low']:
                        intr['low'] = cur_px
            except Exception:
                pass
            state['intraday'] = intr
    except Exception:
        pass

    # 周期性持久化日内统计（每60秒一次）
    try:
        last_persist = state.get('last_intraday_persist_ts') or 0
        if (time.time() - float(last_persist)) > 60:
            _G(f"intraday:{sym}", state.get('intraday'))
            state['last_intraday_persist_ts'] = time.time()
    except Exception:
        pass

    # 检查是否应该调用AI
    current_timestamp = time.time()
    time_since_last_call = current_timestamp - state['last_ai_call_time']

    # 自适应AI调用间隔（基于日线趋势），默认回退为配置值
    ai_interval = state.get('ai_interval_secs', Config.AI_DECISION_INTERVAL)

    should_call_ai = (
        time_since_last_call >= ai_interval
        and len(dc.tick_buffer) >= 20
        and context.trading_allowed
    )

    if should_call_ai:
        Log(f"[调试] 满足AI调用条件, 开始更新数据...")

        # 更新K线数据（按标的）
        dc.update_klines(sym)

        # 计算技术指标
        indicators = dc.calculate_indicators()

        if indicators:
            Log(f"[调试] 技术指标计算成功")
            # 收集市场数据
            market_data = collect_market_data(context, sym, tick, indicators, dc, state)
            # 记录最新市场快照供执行层做流动性与点差gating
            try:
                state['last_market_data'] = market_data
            except Exception:
                pass

            # 自适应参数（频率/冷却/仓位/追踪）
            try:
                adaptive = derive_adaptive_defaults(market_data)
                state['adaptive'] = adaptive
                state['ai_interval_secs'] = adaptive.get('ai_interval_secs', Config.AI_DECISION_INTERVAL)
                _t = market_data.get('d_trend')
                _intv = state['ai_interval_secs']
                _cd = adaptive.get('cooldown_minutes')
                _pos = adaptive.get('position_size_pct')
                _trl = adaptive.get('trailing_type')
                Log("[{}] 自适应: trend={}, interval={}s, cooldown={}m, pos={}, trailing={}".format(sym, _t, _intv, _cd, _pos, _trl))
            except Exception:
                pass

            # 构造Prompt
            prompt = construct_autonomous_trading_prompt(market_data)

            # 调用AI
            Log("正在调用AI进行决策...")
            decision, error = context.ai_engine.call_deepseek_api(prompt)

            if decision:
                Log(f"[{sym}] AI决策: {decision.get('signal')}, 市场状态: {decision.get('market_state')}")
                Log(f"[{sym}] AI分析: {decision.get('reasoning', 'N/A')[:200]}...")  # 截断过长内容

                # 执行决策
                context.executor.execute_decision(context, sym, decision, tick, state)
            else:
                Log(f"[错误] AI调用失败: {error}")

            state['last_ai_call_time'] = current_timestamp
    else:
        # 轻量调试：每隔约10秒输出一次不触发原因
        last_log_t = getattr(context, 'last_noai_log_t', 0) or 0
        try:
            need_log = (current_timestamp - float(last_log_t)) > 10
        except Exception:
            need_log = True
        if need_log:
            reason = []
            if time_since_last_call < ai_interval:
                reason.append("间隔未到")
            if len(dc.tick_buffer) < 20:
                reason.append("tick不足:%d/20" % len(dc.tick_buffer))
            if not context.trading_allowed:
                reason.append("交易未允许")
            if reason:
                Log("[%s] 未触发AI: %s" % (sym, ",".join(reason)))
            context.last_noai_log_t = current_timestamp

    # 每60秒打印一次本地账户快照（估算），便于观察可用资金/担保比
        try:
            last_pf_log = getattr(context, 'last_portfolio_log_ts', 0) or 0
            if (current_timestamp - float(last_pf_log)) > 60:
                lp = getattr(tick, 'last_price', getattr(tick, 'price', 0))
                snap = estimate_account(context, sym, lp, state)
                Log(f"[账户] snapshot(估算): equity={snap['equity']:.0f}, available={snap['available']:.0f}, margin={snap['margin']:.0f}")
                context.last_portfolio_log_ts = current_timestamp
        except Exception:
            pass

    # 风控层检查 (每个tick都执行、按标的)
    context.risk_controller.check_and_enforce(context, sym, tick, state)


def on_bar(context, bars):
    """K线回调 - 用于数据更新"""
    # K线数据已经在on_tick中通过data_collector.update_klines()更新
    pass


def on_order_status(context, order):
    """订单状态回调"""
    # 根据Gkoudai文档, order.status为中文字符串, 如"全部成交"
    if order.status == "全部成交":
        Log(f"订单成交: {order.direction} {order.offset} {order.volume}手 @ {order.price:.2f}")

        # 本地持仓均价/已实盈亏更新 + _G 持久化
        try:
            sym = getattr(order, 'symbol', None)
            if sym:
                update_pos_snapshot_on_fill(context, sym,
                    direction=getattr(order, 'direction', ''),
                    offset=getattr(order, 'offset', ''),
                    price=float(getattr(order, 'price', 0.0) or 0),
                    volume=int(getattr(order, 'volume', 0) or 0))
        except Exception as e:
            try:
                Log(f"[警告] 本地持仓快照更新失败: {e}")
            except Exception:
                pass

        # 更新交易统计 (order.offset为"平"时是平仓)
        try:
            if getattr(order, 'offset', '') == "平":
                context.daily_trades += 1
        except Exception:
            pass

        # 成交后进入冷却（仅限开仓）
        try:
            sym = getattr(order, 'symbol', None)
            if sym:
                st = context.state.get(sym, {})
                if str(getattr(order, 'offset', '')) in ("开", "open", "OPEN"):
                    cd = st.get('pending_cooldown_minutes')
                    if not cd or cd <= 0:
                        # 按日线趋势自适应
                        md = st.get('last_market_data') or {}
                        trend = str(md.get('d_trend') or 'SIDEWAYS')
                        params = Config.ADAPTIVE_PARAMS.get(trend, Config.ADAPTIVE_PARAMS['SIDEWAYS'])
                        cd = float(params.get('cooldown_minutes') or 0)
                    if cd and cd > 0:
                        st['cooldown_until'] = time.time() + cd * 60
                        Log(f"[{sym}] 成交后进入冷却 {cd:.0f} 分钟")
                    st['pending_cooldown_minutes'] = None
        except Exception:
            pass


def on_backtest_finished(context, indicator):
    """回测结束回调"""
    Log("========== 回测结束 ==========")
    Log(f"总收益率: {indicator['累计收益率']*100:.2f}%")
    Log(f"夏普比率: {indicator.get('夏普比率', 'N/A')}")
    Log(f"最大回撤: {indicator['最大回撤']*100:.2f}%")
    Log(f"总交易次数: {context.daily_trades}")
    Log(f"胜率: {context.daily_wins / context.daily_trades * 100 if context.daily_trades > 0 else 0:.1f}%")


# ========================================
# 辅助函数
# ========================================

def _safe_get(obj, *names, **kw):
    default = kw.get('default', None)
    for n in names:
        try:
            if isinstance(obj, dict):
                if n in obj: return obj[n]
            else:
                if hasattr(obj, n): return getattr(obj, n)
        except Exception:
            pass
    return default

def derive_adaptive_defaults(market_data):
    """根据日线趋势返回自适应参数（频率/冷却/仓位/追踪）。"""
    trend = str(market_data.get('d_trend') or 'SIDEWAYS').upper()
    params = Config.ADAPTIVE_PARAMS.get(trend, Config.ADAPTIVE_PARAMS['SIDEWAYS']).copy()
    # 如果流动性很差则进一步降仓
    try:
        if market_data.get('liquidity_state') == 'THIN':
            params['position_size_pct'] = min(params['position_size_pct'], 0.3)
            # 在震荡里也可缩短冷却避免久等
            params['cooldown_minutes'] = max(1, int(params.get('cooldown_minutes', 2)))
    except Exception:
        pass
    return params

def estimate_account(context, symbol, last_price, state):
    """基于 get_pos + _G 轻持久化的均价/已实盈亏，估算账户权益/可用/占用保证金。
    用于在平台无账户接口时，给AI/风控提供足够准确的视角。
    """
    try:
        pos = int(get_pos(symbol))
    except Exception:
        pos = 0
    avg = float(state.get('position_avg_price') or 0)
    realized = float(state.get('realized_pnl') or 0)
    mult = PlatformAdapter.get_contract_size(symbol)
    long_mr = PlatformAdapter.get_margin_ratio(symbol, 'long')
    short_mr = PlatformAdapter.get_margin_ratio(symbol, 'short')

    # 浮动盈亏
    if pos > 0:
        float_pnl = (last_price - avg) * abs(pos) * mult
        used_margin = abs(pos) * last_price * mult * max(long_mr, 0.01)
    elif pos < 0:
        float_pnl = (avg - last_price) * abs(pos) * mult
        used_margin = abs(pos) * last_price * mult * max(short_mr, 0.01)
    else:
        float_pnl = 0.0
        used_margin = 0.0

    equity = float(getattr(context, 'initial_cash', 0.0)) + realized + float_pnl
    available = max(0.0, equity - used_margin)
    return {
        'equity': float(equity),
        'available': float(available),
        'margin': float(used_margin),
        'source': 'local_estimate'
    }

def update_pos_snapshot_on_fill(context, symbol, direction, offset, price, volume):
    """在全部成交后，基于成交信息更新本地均价与已实现盈亏，并持久化到 _G。
    规则：
    - pos_after = get_pos(symbol)
    - 根据方向/offset 推算 delta 与 pos_before
    - 按同向加仓/平仓/反手的情形更新 avg/realized
    """
    try:
        pos_after = int(get_pos(symbol))
    except Exception:
        pos_after = 0
    side_buy = ("买" in str(direction)) or (str(direction).lower().startswith('buy'))
    is_open = ("开" in str(offset)) or (str(offset).lower().startswith('open'))
    delta = (volume if side_buy else -volume) if is_open else (0)  # 开仓才改变符号方向
    # 平仓的 delta 不直接使用；用 pos_after 与逻辑推回 pos_before
    if is_open:
        pos_before = pos_after - delta
    else:
        # 平仓：pos_after = pos_before - close_qty*sign(pos_before)
        # 推回 pos_before
        if pos_after > 0:
            pos_before = pos_after + volume  # 平多 volume 后剩 pos_after
        elif pos_after < 0:
            pos_before = pos_after - volume  # 平空 volume 后剩 pos_after
        else:
            # 完全平仓：pos_before 的符号取决于方向
            pos_before = volume if not side_buy else -volume

    st = context.state.get(symbol, {})
    avg = float(st.get('position_avg_price') or 0)
    realized = float(st.get('realized_pnl') or 0)
    mult = PlatformAdapter.get_contract_size(symbol)

    # 计算
    if is_open:
        # 同向加仓 or 反向“开仓”
        if pos_before == 0 or (pos_before > 0 and side_buy) or (pos_before < 0 and not side_buy):
            # 同向加仓
            total_qty = abs(pos_before) + volume
            new_avg = ((abs(pos_before) * avg) + volume * price) / total_qty if total_qty > 0 else price
            st['position_avg_price'] = new_avg
        else:
            # 反向但标注为开：先平掉一部分，再把余额视为反手
            close_qty = min(abs(pos_before), volume)
            if pos_before > 0 and not side_buy:
                realized += (avg - price) * close_qty * mult
            elif pos_before < 0 and side_buy:
                realized += (price - avg) * close_qty * mult
            remain = volume - close_qty
            if pos_after == 0:
                st['position_avg_price'] = 0.0
            else:
                if remain > 0:
                    st['position_avg_price'] = price
        st['realized_pnl'] = realized
    else:
        # 平仓
        close_qty = volume
        if pos_before > 0 and not side_buy:
            realized += (price - avg) * close_qty * mult
        elif pos_before < 0 and side_buy:
            realized += (avg - price) * close_qty * mult
        st['realized_pnl'] = realized
        if pos_after == 0:
            st['position_avg_price'] = 0.0
        else:
            # 部分平仓保留原均价
            st['position_avg_price'] = avg

    # 入场时间维护：首次持仓或反手更新
    try:
        if st.get('entry_time') is None and pos_after != 0:
            st['entry_time'] = datetime.now()
        if pos_after == 0:
            st['entry_time'] = None
    except Exception:
        pass

    # 持久化到 _G
    try:
        et_str = st.get('entry_time').strftime('%Y-%m-%d %H:%M:%S') if st.get('entry_time') else None
        _G(f"pos:{symbol}", {
            'avg_price': float(st.get('position_avg_price') or 0.0),
            'realized_pnl': float(st.get('realized_pnl') or 0.0),
            'entry_time': et_str,
            'trailing': st.get('trailing') or None,
        })
    except Exception:
        pass

class PlatformAdapter:
    """封装平台相关的取数，尽量兼容不同接口命名。"""

    @staticmethod
    def get_contract(symbol):
        # 常见可能的接口名：get_contract, contract, get_instrument, get_contract_data
        for fn_name in [
            'get_contract', 'contract', 'get_instrument', 'get_contract_data'
        ]:
            try:
                fn = globals().get(fn_name)
                if callable(fn):
                    c = fn(symbol)
                    # 粗检：应至少带有 size 或 pricetick
                    if c is not None and (_safe_get(c, 'size') is not None or _safe_get(c, 'pricetick') is not None):
                        return c
            except Exception:
                pass
        return None

    @staticmethod
    def get_account(context=None):
        # 优先 context.account()，其次 account() 或 get_account()
        try:
            acc_method = getattr(context, 'account', None)
            if callable(acc_method):
                return acc_method()
        except Exception:
            pass
        for fn_name in ['account', 'get_account']:
            try:
                fn = globals().get(fn_name)
                if callable(fn):
                    return fn()
            except Exception:
                pass
        return None

    @staticmethod
    def get_contract_size(symbol):
        c = PlatformAdapter.get_contract(symbol)
        if c is not None:
            size = _safe_get(c, 'size')
            if size:
                try:
                    return float(size)
                except Exception:
                    pass
        return float(Config.CONTRACT_MULTIPLIER.get(symbol, 1000))

    @staticmethod
    def get_pricetick(symbol):
        c = PlatformAdapter.get_contract(symbol)
        tick = _safe_get(c, 'pricetick') if c is not None else None
        try:
            return float(tick) if tick else None
        except Exception:
            return None

    @staticmethod
    def get_min_volume(symbol):
        c = PlatformAdapter.get_contract(symbol)
        mv = _safe_get(c, 'min_volume') if c is not None else None
        try:
            return float(mv) if mv else 1.0
        except Exception:
            return 1.0

    @staticmethod
    def get_margin_ratio(symbol, direction='long'):
        c = PlatformAdapter.get_contract(symbol)
        # 常见命名：long_margin_ratio/short_margin_ratio 或 *_rate
        if c is not None:
            if direction == 'long':
                v = _safe_get(c, 'long_margin_ratio', 'long_margin_rate', 'margin_ratio', 'margin_rate')
            else:
                v = _safe_get(c, 'short_margin_ratio', 'short_margin_rate', 'margin_ratio', 'margin_rate')
            try:
                if v is not None:
                    return float(v)
            except Exception:
                pass
        # 兜底配置
        if direction == 'long':
            return float(Config.DEFAULT_MARGIN_RATIO_LONG.get(symbol, 0.1))
        return float(Config.DEFAULT_MARGIN_RATIO_SHORT.get(symbol, 0.1))

    

 

def collect_market_data(context, symbol, tick, indicators, data_collector, state):
    """收集完整的市场数据用于AI决策"""

    # 持仓信息 (使用正确的Gkoudai API)
    position_volume = get_pos(symbol)

    # 计算未实现盈亏
    current_price = getattr(tick, 'last_price', getattr(tick, 'price', 0))
    mult = PlatformAdapter.get_contract_size(symbol)
    position_avg_price = state.get('position_avg_price') or 0
    if position_volume != 0:
        if position_volume > 0:
            unrealized_pnl = (current_price - position_avg_price) * abs(position_volume) * mult
        else:
            unrealized_pnl = (position_avg_price - current_price) * abs(position_volume) * mult

        unrealized_pnl_pct = (unrealized_pnl / (position_avg_price * abs(position_volume) * mult)) * 100 if position_avg_price > 0 else 0
    else:
        unrealized_pnl = 0
        unrealized_pnl_pct = 0

    # 持仓时长（按标的）
    try:
        entry_time = context.state.get(symbol, {}).get('entry_time')
    except Exception:
        entry_time = None
    if entry_time:
        holding_minutes = (datetime.now() - entry_time).total_seconds() / 60.0
    else:
        holding_minutes = 0

    # 日内统计（以交易日为口径）
    intr = state.get('intraday', {})
    daily_open = intr.get('open', tick.last_price)
    daily_high = intr.get('high', tick.last_price)
    daily_low = intr.get('low', tick.last_price)
    daily_change_pct = ((tick.last_price - daily_open) / daily_open) * 100 if daily_open else 0

    # 持仓方向
    if position_volume > 0:
        position_direction = "多头"
    elif position_volume < 0:
        position_direction = "空头"
    else:
        position_direction = "无持仓"

    # 账户快照：本地估算（get_pos + _G 均价/已实盈亏）
    acc = estimate_account(context, symbol, current_price, state)
    base_equity = acc['equity'] if acc['equity'] > 0 else max(1.0, float(getattr(context, 'initial_cash', 0.0)))
    daily_pnl_pct = (context.daily_pnl / base_equity) * 100 if base_equity > 0 else 0

    # 今日胜率
    daily_win_rate = (context.daily_wins / context.daily_trades * 100) if context.daily_trades > 0 else 0

    # 盘口与时间字段的安全读取（若无则退化为当前价/0/当前时间）
    bid_price = (
        getattr(tick, 'bid_price_1', None)
        or getattr(tick, 'bid_price1', None)
        or getattr(tick, 'bid_price', None)
        or current_price
    )
    ask_price = (
        getattr(tick, 'ask_price_1', None)
        or getattr(tick, 'ask_price1', None)
        or getattr(tick, 'ask_price', None)
        or current_price
    )
    bid_volume = (
        getattr(tick, 'bid_volume_1', None)
        or getattr(tick, 'bid_volume1', None)
        or getattr(tick, 'bid_volume', None)
        or 0
    )
    ask_volume = (
        getattr(tick, 'ask_volume_1', None)
        or getattr(tick, 'ask_volume1', None)
        or getattr(tick, 'ask_volume', None)
        or 0
    )
    last_volume = getattr(tick, 'last_volume', getattr(tick, 'volume', 0))
    cur_time = getattr(tick, 'strtime', None)
    if not cur_time:
        try:
            cur_time = tick.datetime.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            cur_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 五档累积深度与不平衡（若无则基于L1）
    def _get_level(name_patterns):
        for attr in name_patterns:
            val = getattr(tick, attr, None)
            if val is not None:
                return val
        return None

    sum_bid_5 = 0
    sum_ask_5 = 0
    for i in range(1, 6):
        bv = _get_level([f"bid_volume_{i}", f"bid_volume{i}"])
        av = _get_level([f"ask_volume_{i}", f"ask_volume{i}"])
        if bv is not None:
            sum_bid_5 += bv
        if av is not None:
            sum_ask_5 += av
    if sum_bid_5 == 0 and sum_ask_5 == 0:
        sum_bid_5 = bid_volume
        sum_ask_5 = ask_volume

    spread = (ask_price - bid_price) if (ask_price and bid_price) else 0
    mid_price = (ask_price + bid_price) / 2 if (ask_price and bid_price) else current_price
    denom = (bid_volume + ask_volume) if (bid_volume + ask_volume) > 0 else 1
    imbalance_l1 = (bid_volume - ask_volume) / denom
    denom5 = (sum_bid_5 + sum_ask_5) if (sum_bid_5 + sum_ask_5) > 0 else 1
    imbalance_l5 = (sum_bid_5 - sum_ask_5) / denom5
    microprice = (
        (ask_price * bid_volume + bid_price * ask_volume) / denom
        if denom > 0 else mid_price
    )

    # 流动性评分：与最近N个tick的五档总深度均值之比
    recent_depths = [t.get('depth5', 0) for t in data_collector.tick_buffer if 'depth5' in t]
    avg_depth = (sum(recent_depths) / len(recent_depths)) if recent_depths else 0
    liquidity_score = ((sum_bid_5 + sum_ask_5) / avg_depth) if avg_depth > 0 else 1.0
    if liquidity_score < 0.7:
        liquidity_state = 'THIN'
    elif liquidity_score > 1.5:
        liquidity_state = 'THICK'
    else:
        liquidity_state = 'NORMAL'

    market_data = {
        'account_equity': acc['equity'],
        'account_available': acc['available'],
        'account_margin': acc['margin'],
        'account_source': acc.get('source', 'local_estimate'),
        'symbol': symbol,
        'current_price': current_price,
        'bid_price': bid_price,
        'ask_price': ask_price,
        'bid_volume': bid_volume,
        'ask_volume': ask_volume,
        'last_volume': last_volume,
        'current_time': cur_time,
        'spread': spread,
        'mid_price': mid_price,
        'microprice': microprice,
        'sum_bid_5': sum_bid_5,
        'sum_ask_5': sum_ask_5,
        'imbalance_l1': imbalance_l1,
        'imbalance_l5': imbalance_l5,
        'liquidity_score': liquidity_score,
        'liquidity_state': liquidity_state,
        'position_direction': position_direction,
        'position_volume': position_volume,
        'position_avg_price': position_avg_price,
        'unrealized_pnl': unrealized_pnl,
        'unrealized_pnl_pct': unrealized_pnl_pct,
        'holding_minutes': holding_minutes,
        'daily_open': daily_open,
        'daily_high': daily_high,
        'daily_low': daily_low,
        'daily_change_pct': daily_change_pct,
        'daily_pnl': context.daily_pnl,
        'daily_pnl_pct': daily_pnl_pct,
        'daily_trades': context.daily_trades,
        'daily_win_rate': daily_win_rate,
        'contract_multiplier': PlatformAdapter.get_contract_size(symbol),
        'd_ema_20': indicators.get('d_ema_20', 0),
        'd_ema_60': indicators.get('d_ema_60', 0),
        'd_macd': indicators.get('d_macd', 0),
        'd_trend': indicators.get('d_trend', 'N/A'),
        'zigzag_summary': indicators.get('zigzag_summary', 'N/A'),
        'fib_summary': indicators.get('fib_summary', 'N/A'),
        **indicators  # 展开技术指标
    }

    return market_data
