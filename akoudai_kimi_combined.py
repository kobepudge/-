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
from datetime import datetime, time as datetime_time, timedelta
import threading
import random
# 某些运行环境未预装 requests，会在导入阶段直接报错，导致 on_init 日志都打印不出来。
try:
    import requests  # type: ignore
except Exception:
    requests = None
import traceback
from collections import deque
 
import math

# ---- Heartbeat: module imported ----
try:
    print('[MODULE] strategy imported OK')
except Exception:
    pass
try:
    Log('[MODULE] strategy imported OK')
except Exception:
    pass

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
    DEEPSEEK_API_KEY = "sk-44e096b16c2a4f0ea364368583ea097d"
    # 可选：多Key轮询/限流。若提供，将在这些Key之间轮询，每个Key同刻只跑1个请求。
    # 你也可以把旧Key放在此列表中。
    DEEPSEEK_API_KEYS = [
        "sk-44e096b16c2a4f0ea364368583ea097d",
        "sk-c7c94df2cbbb423698cb895f25534501",
    ]
    DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
    DEEPSEEK_MODEL = "deepseek-chat"
    DEEPSEEK_TEMPERATURE = 0.7
    # 注意：DeepSeek 通常限制 <= 8192；避免请求超限
    DEEPSEEK_MAX_TOKENS = 2000

    # AI决策频率 (秒) - 平衡成本和响应速度
    AI_DECISION_INTERVAL = 180  # 降频：默认3分钟一次决策，降低抖动/手续费
    # 多标的错峰触发的最大抖动秒数（每个标的固定随机相位，避免同刻并发）
    AI_STAGGER_MAX_SECS = 7

    # ====== 安全边界 (唯一的硬性约束) ======
    MAX_SINGLE_TRADE_LOSS_PCT = 0.02   # 单笔最大亏损2%
    MAX_DAILY_LOSS_PCT = 0.05          # 单日最大亏损5%
    # 强制平仓时间（白盘/夜盘拆分；夜盘跨日需要特殊处理）
    FORCE_CLOSE_TIME = "14:55:00"      # 兼容旧字段，默认白盘
    FORCE_CLOSE_TIME_DAY = "14:55:00"  # 白盘强平时间（收盘前）
    FORCE_CLOSE_TIME_NIGHT = "02:25:00"  # 夜盘强平时间（次日）
    MIN_AI_CONFIDENCE = 0.6            # 最小信心阈值(0-1)
    # 固定阈值（由动态参数改为写死常量）
    SPREAD_RATIO_LIMIT = 0.001
    LIQUIDITY_SCORE_THIN = 0.7
    LIQUIDITY_SCORE_THICK = 1.5
    TRADING_DAY_ROLLOVER_HOUR = 21
    # 启动阶段是否立即触发一次AI（快照就绪且未在冷却，tick触发关闭时）。默认关闭。
    ENABLE_STARTUP_AI_TRIGGER = False
    INTRADAY_PERSIST_INTERVAL_SECS = 60
    INITIAL_CASH = 2000000
    # 是否使用 _G 做持仓/均价等持久化（False 表示仅运行期内维护，重启后不恢复）
    USE_PERSISTENT_SNAPSHOT = False
    # 是否信任平台 get_pos() 的返回；False 时仅使用运行期本地持仓
    USE_PLATFORM_GET_POS = False
    # 执行前简要检查日志（定位静默跳过原因）
    DEBUG_EXEC_CHECK = True

    # 数据窗口大小
    TICK_WINDOW = 100        # 缓存最近100个tick
    KLINE_1M_WINDOW = 300    # 1分钟K线300根（用于5分钟聚合与指标计算）
    KLINE_1D_WINDOW = 50     # 日K线50根
    DEPTH_LIQ_WINDOW = 120   # 盘口深度流动性统计窗口（最近 N 个tick）
    # 触发AI所需的最小tick数（避免刚启动时过严）；原固定20调整为可配置，默认5
    TICKS_MIN_FOR_AI = 5
    # 触发AI所需的最少1m K线根数（确保不是纯tick噪音）
    MIN_1M_BARS_FOR_AI = 20
    # 是否允许在on_tick上触发AI（否则仅在on_bar节拍触发）
    ENABLE_TICK_TRIGGERED_AI = False
    # 为避免与 on_trade 重复，默认关闭基于 on_order 的成交填充
    USE_ON_ORDER_FOR_FILLS = False

    # API重试配置
    API_TIMEOUT = 30  # 增加到30秒,避免网络波动导致超时
    API_MAX_RETRIES = 3

    # 自适应与动态管理
    ADAPTIVE_PARAMS = {
        'UPTREND': {
            'ai_interval_secs': 180,
            'cooldown_minutes': 5,
            'position_size_pct': 0.6,
            'trailing_type': 'atr',
            'trailing_atr_mult': 2.0,
            'trailing_percent': 0.0,
        },
        'DOWNTREND': {
            'ai_interval_secs': 180,
            'cooldown_minutes': 5,
            'position_size_pct': 0.6,
            'trailing_type': 'atr',
            'trailing_atr_mult': 2.0,
            'trailing_percent': 0.0,
        },
        'SIDEWAYS': {
            'ai_interval_secs': 240,
            'cooldown_minutes': 10,
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
        # 上期所AU合约按平台实际占用核验：~14%
        "au2512.SHFE": 0.14,
        "lc2601.GFEX": 0.12,
    }
    DEFAULT_MARGIN_RATIO_SHORT = {
        # 上期所AU合约按平台实际占用核验：~14%
        "au2512.SHFE": 0.14,
        "lc2601.GFEX": 0.12,
    }

    # 新开仓资金安全系数（留出浮亏与费用空间）
    NEW_TRADE_MARGIN_BUFFER = 1.05
    # 最低担保比（equity / margin_used，越高越安全）；若低于该值则禁止新开仓
    MIN_GUARANTEE_RATIO = 1.3
    # 入场止损护栏（方向+最小间距）
    MIN_STOP_TICKS = 10
    MIN_STOP_ATR_MULT = 0.4
    LOW_CHURN_MODE = True
    TREND_MIN_RRR = 2.0
    CT_MIN_CONF = 0.70
    CT_MIN_RRR = 2.5
    CT_MIN_EDGE = 0.0
    CT_MAX_PCT = 0.15
    CT_COOLDOWN_MIN = 10
    # 反手/再入场冷却（秒）
    REENTRY_COOLDOWN_SECS = 120

    # 波浪/结构识别参数（ZigZag）默认值
    # 可被热参数覆盖：zigzag_threshold_pct
    ZIGZAG_THRESHOLD_PCT = 0.3
    # 新增：5分钟聚合级别的ZigZag阈值（用于大级别判断），可被热参数覆盖
    ZIGZAG_THRESHOLD_PCT_5M = 0.6

    # 日志控制
    # 打印AI推理全文（不截断）
    LOG_FULL_AI_REASONING = False
    # 打印AI完整JSON决策（可能较长）
    LOG_FULL_AI_JSON = False

# 交易员手册（Rulebook）— 作为System Prompt的“公司制度”与执行器熔断的基准
TRADER_RULEBOOK = {
    "max_position_pct": 0.6,                        # 账户权益的最大仓位占比
    "mandatory_stop_loss": True,                    # 每笔必须有止损
    "no_overnight": "14:55:00",                    # 最终平仓时间
    "max_daily_loss_pct": 0.05,                     # 单日最大亏损
    "min_reward_risk_ratio": 2.0                    # 最小可接受的RRR（顺势）；反趋势由执行器更高门槛约束
}




# ========================================
# AI决策核心Prompt
# ========================================

# 波浪/缠论优先的System Prompt（固定角色与铁律；User仅传动态数据）
SYSTEM_PROMPT_WAVE_FIRST = (
    "你是一位专业的期货日内交易员，严格以波浪理论(主) + 缠论结构(辅)为第一优先级进行交易推理。"
    "请遵循以下铁律并仅输出严格JSON："
    "\n1) 先波浪后其它：先给出主/备计数与失效位，再给缠论中枢/是否背驰。"
    "\n2) 双级别一致性：必须识别小级别(1m)与大级别(5m聚合)的波浪相位，明确是否同向或级别背离。"
    "\n3) 几何与结构：通道/斐波回撤与扩展位仅用于论证与目标位辅助。"
    "\n4) 动能与量能：MACD柱峰与量能状态仅作验证与风控，不得喧宾夺主。"
    "\n5) 风险与执行：硬止损必给（signal为buy/sell时，stop_loss为必填且在正确一侧）；止盈计划需基于大/小级别关系进行分层(缩写如下)。"
    "\n   - 若小级别顺大级别主升/主跌(IMPULSE同向)：允许更宽目标位/分批止盈在更远R倍数。"
    "\n   - 若小级别为大级别回撤/修正：采用更保守止盈(更近的R倍数/更高比例的早期减仓)。"
    "\n   - 若处于中枢震荡(CHAN_CENTER)：小仓位、快进快出、时间止盈优先。"
    "\n6) 少做反趋势：若与5m/日线趋势相反，仅在出现强证据（有效突破+回踩确认/背离+放量等）且胜率显著时才可交易；默认小仓位。"
    "\n7) 交易历史仅用于错误避免，不得因短期盈亏放大/缩小仓位。"
)

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

## 订单流摘要（近tick窗口）
- **Delta(20t)**: {market_data.get('of_delta_20', 0.0):.0f}
- **DeltaMA(5/10)**: {market_data.get('of_ma5', 0.0):.1f} / {market_data.get('of_ma10', 0.0):.1f}
- **Delta Z-Score(50)**: {market_data.get('of_z50', 0.0):.2f}

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
- **日内开盘价**: {market_data['daily_open']:.2f} (source: {market_data.get('today_open_source','intraday')})
- **日内最高**: {market_data['daily_high']:.2f}
- **日内最低**: {market_data['daily_low']:.2f}
- **日内涨跌幅**: {market_data['daily_change_pct']:.2f}%
- **昨收**: {market_data.get('prev_close','N/A')}

## 日线趋势 (已完成日线)
- **D_EMA20**: {d_ema_20_v:.2f}
- **D_EMA60**: {d_ema_60_v:.2f}
- **D_MACD**: {d_macd_v:.4f}
- **趋势判定**: {d_trend_v}

## 波浪/结构（双级别优先）
- 小级别(1m)相位: {market_data.get('wave_phase_1m','N/A')} | 枢轴: {zigzag_summary_v} | 斐波: {fib_summary_v}
- 大级别(5m)相位: {market_data.get('wave_phase_5m','N/A')} | 枢轴: {market_data.get('zigzag_summary_5m','N/A')} | 斐波: {market_data.get('fib_summary_5m','N/A')}
提示：若1m与5m同向的IMPULSE为主升/主跌→可放宽止盈目标并采用分批减仓；若1m为5m的修正→更保守、靠近结构位分批减仓；若处于中枢震荡→小仓、快进快出。

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

## 近期成交（近3笔）
- {market_data.get('recent_trades_summary','(无)')}

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
- 账户初始资金: {market_data.get('initial_cash', 0):.0f} 元
- 合约乘数: {market_data['contract_multiplier']}
- 建议下单手数 ≈ 初始资金 × position_size_pct ÷ (当前价格 × 合约乘数)

成交价格参考:
- 做多(buy)按卖一价(ask)成交, 做空(sell/short)按买一价(bid)成交；如盘口缺失则退化为最新价。

仓位与可交易性（建议，非强制）：
- 当流动性偏弱或点差偏大（liquidity_state=THIN 或 spread>2 ticks）→ 降低仓位或放弃入场
- 当盘口不平衡（imbalance_l5）与趋势一致 → 可适当提高仓位；相反则保守或等待确认

## 持仓时长与分批止盈（基于大/小级别）

你必须基于1m/5m相位关系设计止盈方案：
- 同向IMPULSE(1m顺5m)：目标位可使用斐波扩展(1.272/1.618)与通道上/下沿，共振位优先；scale-out级别可设更远的R倍数。
- 修正(1m逆5m)：优先使用回撤簇(0.382/0.5/0.618)附近做分批止盈，比例偏前置（例如50%在较近目标）。
- 中枢(CHAN_CENTER)：小仓、短时、目标贴近中枢外沿与最近摆动极值，时间止盈优先。

你可以自主判断持仓时长:
- 快速反转交易: 5-15分钟
- 趋势跟随: 30-120分钟
- 日内波段: 直到收盘前

# 输出格式 (严格JSON格式) —— 必须给出 stop_loss；并直接数浪（1m/5m）
"""

    # 追加：结构化数据（供AI直接计算/数浪）
    try:
        _structured_data = {
            'series': {
                'closes_1m': market_data.get('closes_1m', []),
                'closes_5m': market_data.get('closes_5m', []),
            },
            'zigzag': {
                'threshold_1m_pct': market_data.get('zigzag_threshold_1m'),
                'pivots_1m': market_data.get('zigzag_pivots_1m', []),
                'threshold_5m_pct': market_data.get('zigzag_threshold_5m'),
                'pivots_5m': market_data.get('zigzag_pivots_5m', []),
            },
            'indicators_1m': {
                'ema20': market_data.get('ema_20'),
                'ema60': market_data.get('ema_60'),
                'macd': market_data.get('macd'),
                'macd_signal': market_data.get('macd_signal'),
                'macd_hist': market_data.get('macd_hist'),
                'rsi': market_data.get('rsi'),
                'atr': market_data.get('atr')
            },
            'microstructure': {
                'tick_size': market_data.get('tick_size'),
                'spread': market_data.get('spread'),
                'imbalance_l1': market_data.get('imbalance_l1'),
                'imbalance_l5': market_data.get('imbalance_l5'),
                'liquidity_score': market_data.get('liquidity_score'),
                'liquidity_state': market_data.get('liquidity_state')
            }
        }
        _structured_json = json.dumps(_structured_data, ensure_ascii=False)
        head = head + "\n## 结构化数据(JSON)\n```json\n" + _structured_json + "\n```\n"
    except Exception:
        pass

    json_block = """
{
  "market_state": "UPTREND|DOWNTREND|SIDEWAYS|REVERSAL|VOLATILE|OTHER",
  "reasoning": "你的完整分析思路,包括: 1)量价与盘口 2)趋势结构 3)关键技术位 4)风险与可交易性",
  "signal": "buy|sell|hold|close|adjust_stop",
  "confidence": 0.75,
  "trend_alignment": "aligned|opposite|neutral",
  "countertrend": false,
  "edge_over_friction": 0.2,

  // 入场与目标（若 signal 为 buy/sell 建议给出）
  "entry_price": 550.50,
  "stop_loss": 548.00,  // 必填: buy时 < entry/当前; sell时 > entry/当前
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
  "min_interval_minutes": 10,

  // 波浪/结构（可选）
  "wave_primary": "Minor Impulse: now in 3",
  "wave_alt": "Alternate: ending diagonal",
  "wave_invalidation": 546.50
  ,
  // 直接数浪（必填，供执行/回测分析使用）
  "wave_detail_1m": {
    "primary": "impulse|corrective|triangle|other",
    "labels": ["i","ii","iii","iv","v"],
    "segments": [{"label":"i","start":"2025-11-05 13:35:00","end":"2025-11-05 13:48:00","start_price":552.2,"end_price":554.1}],
    "invalidation": 548.00,
    "alt": "optional brief alt count"
  },
  "wave_detail_5m": {
    "primary": "impulse|corrective|triangle|other",
    "labels": ["(1)","(2)","(3)","(4)","(5)"],
    "invalidation": 546.50,
    "alt": "optional"
  }
}
"""

    tail = """

**重要说明**:
- 如果signal是"hold"且已有持仓,可以输出"adjust_stop"来动态调整止损
- 如果市场状态变化导致原交易逻辑失效,应立即"close"
- 必须提供有效的stop_loss；若无，将被执行层拒绝新仓
- 必须直接数浪（wave_detail_1m/5m），并给出失效位与主/备计数
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
        # 订单流聚合（简版）：使用逐tick方向×成交量的delta累加
        from collections import deque as _dq
        self._of_last_price = None
        self._of_delta_win = _dq(maxlen=120)  # 近120 tick的delta窗口
        self._of_cum = 0.0

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

        # 订单流delta：按价格变动方向近似判定主动性；持平时用L1不平衡方向
        try:
            _lp = self._of_last_price
            _price_now = float(price)
            if _lp is None:
                sign = 0
            else:
                if _price_now > _lp:
                    sign = 1
                elif _price_now < _lp:
                    sign = -1
                else:
                    try:
                        # L1不平衡：买盘>卖盘→+1；否则-1；都缺失→0
                        sign = 1 if float(bid_vol or 0) > float(ask_vol or 0) else (-1 if float(ask_vol or 0) > float(bid_vol or 0) else 0)
                    except Exception:
                        sign = 0
            self._of_last_price = _price_now
            vol_tick = float(volume or 0)
            if vol_tick < 0:
                vol_tick = 0.0
            delta = sign * vol_tick
            self._of_cum += delta
            self._of_delta_win.append(delta)
        except Exception:
            pass

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
        pivots_1m = []
        if zigzag and zigzag.get('pivots'):
            piv = zigzag['pivots'][-6:]
            zigzag_summary = "; ".join([f"{p['type']}@{p['price']:.2f}" for p in piv])
            # 输出更完整的枢轴结构（裁剪到最近12个）
            try:
                piv_all = zigzag.get('pivots', [])
                for p in piv_all[-12:]:
                    pivots_1m.append({
                        'idx': int(p.get('idx', 0)),
                        'price': float(p.get('price', 0.0) or 0.0),
                        'type': str(p.get('type', ''))
                    })
            except Exception:
                pass
            fib = zigzag.get('fib', {})
            fr = fib.get('retracements', {})
            fe = fib.get('extensions', {})
            if fr or fe:
                fib_summary = f"ret:0.382={fr.get('0.382','')},0.5={fr.get('0.5','')},0.618={fr.get('0.618','')}; ext:1.272={fe.get('1.272','')},1.618={fe.get('1.618','')}"

        # ===== 5分钟聚合级别：大级别波浪/斐波与相位 =====
        kline_5m = self._aggregate_to_5min(self.kline_1m_buffer)
        zz5m_summary = 'N/A'
        fib_summary_5m = 'N/A'
        wave_phase_1m = 'N/A'
        wave_phase_5m = 'N/A'
        pivots_5m = []
        try:
            if kline_5m and len(kline_5m) >= 24:  # 至少两小时数据
                closes_5m = [k['close'] for k in kline_5m]
                ema20_5m = self._calculate_ema(closes_5m, 20) if len(closes_5m) >= 20 else closes_5m[-1]
                ema60_5m = self._calculate_ema(closes_5m, 60) if len(closes_5m) >= 60 else ema20_5m
                macd_5m, sig_5m, hist_5m = self._calculate_macd(closes_5m)
                zigzag_5m = self._calculate_zigzag(closes_5m, threshold_pct=getattr(Config, 'ZIGZAG_THRESHOLD_PCT_5M', 0.6))
                if zigzag_5m and zigzag_5m.get('pivots'):
                    piv5 = zigzag_5m['pivots'][-6:]
                    zz5m_summary = "; ".join([f"{p['type']}@{p['price']:.2f}" for p in piv5])
                    try:
                        piv_all5 = zigzag_5m.get('pivots', [])
                        for p in piv_all5[-12:]:
                            pivots_5m.append({
                                'idx': int(p.get('idx', 0)),
                                'price': float(p.get('price', 0.0) or 0.0),
                                'type': str(p.get('type', ''))
                            })
                    except Exception:
                        pass
                    fib5 = zigzag_5m.get('fib', {})
                    fr5 = fib5.get('retracements', {})
                    fe5 = fib5.get('extensions', {})
                    if fr5 or fe5:
                        fib_summary_5m = f"ret:0.382={fr5.get('0.382','')},0.5={fr5.get('0.5','')},0.618={fr5.get('0.618','')}; ext:1.272={fe5.get('1.272','')},1.618={fe5.get('1.618','')}"
                wave_phase_5m = self._classify_wave_phase(
                    ema20=ema20_5m, ema60=ema60_5m, macd=macd_5m, zigzag=(zigzag_5m or {'pivots': []}), closes=closes_5m
                )
        except Exception:
            pass

        # 小级别(1m)波浪相位
        try:
            wave_phase_1m = self._classify_wave_phase(
                ema20=ema_20, ema60=ema_60, macd=macd, zigzag=(zigzag or {'pivots': []}), closes=closes_1m
            )
        except Exception:
            pass

        return {
            'ema_20': ema_20,
            'ema_60': ema_60,
            'macd': macd,
            'macd_signal': signal,
            'macd_hist': hist,
            'rsi': rsi,
            'atr': atr,
            # 序列（用于AI波浪识别）
            'closes_1m': closes_1m[-120:],
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
            'zigzag_summary_5m': zz5m_summary,
            'fib_summary_5m': fib_summary_5m,
            'wave_phase_1m': wave_phase_1m,
            'wave_phase_5m': wave_phase_5m,
            # 枢轴与阈值（供AI直接使用）
            'zigzag_pivots_1m': pivots_1m,
            'zigzag_threshold_1m': float(getattr(Config, 'ZIGZAG_THRESHOLD_PCT', 0.3)),
            'zigzag_pivots_5m': pivots_5m,
            'zigzag_threshold_5m': float(getattr(Config, 'ZIGZAG_THRESHOLD_PCT_5M', 0.6)),
            # 5m收盘序列（若有）
            'closes_5m': [k['close'] for k in kline_5m][-40:] if kline_5m else []
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

    @staticmethod
    def _classify_wave_phase(ema20, ema60, macd, zigzag, closes):
        """基于EMA倾向 + MACD符号 + 最近ZigZag枢轴，粗略判别波浪相位。
        返回：IMPULSE_UP / IMPULSE_DOWN / CORRECTION / CHAN_CENTER
        """
        try:
            piv = zigzag.get('pivots', []) if isinstance(zigzag, dict) else []
            hh = ll = False
            if len(piv) >= 4:
                # 最近两次高/低是否创新
                highs = [p['price'] for p in piv if p['type'] == 'H'][-2:]
                lows = [p['price'] for p in piv if p['type'] == 'L'][-2:]
                if len(highs) == 2 and highs[-1] > highs[-2]:
                    hh = True
                if len(lows) == 2 and lows[-1] < lows[-2]:
                    ll = True
            trend_up = (ema20 > ema60 and macd > 0)
            trend_dn = (ema20 < ema60 and macd < 0)
            # 震荡：ema接近/交错，macd近0轴
            try:
                near_zero = abs(macd) < (0.002 * (sum(closes[-20:]) / 20.0))
            except Exception:
                near_zero = False
            if trend_up and hh:
                return 'IMPULSE_UP'
            if trend_dn and ll:
                return 'IMPULSE_DOWN'
            if near_zero or (not trend_up and not trend_dn):
                return 'CHAN_CENTER'
            return 'CORRECTION'
        except Exception:
            return 'N/A'


# ========================================
# AI决策引擎
# ========================================

class AIDecisionEngine:
    """AI决策引擎 - 调用DeepSeek API"""

    @staticmethod
    def call_deepseek_api(prompt, api_key: str = None):
        """调用DeepSeek API获取决策"""
        # 如果运行环境没有 requests，不要在导入阶段阻塞策略初始化
        if requests is None:
            return None, "requests 库未安装，已跳过AI调用（不影响策略初始化与日志输出）"
        # 选择使用的Key：优先参数，其次全局单Key
        key = api_key or getattr(Config, 'DEEPSEEK_API_KEY', '')
        if not key or "请在此填写" in key:
            return None, "未配置 DeepSeek Key：请在 Config.DEEPSEEK_API_KEY 填写你的 API Key"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {key}'
        }

        payload = {
            'model': Config.DEEPSEEK_MODEL,
            'messages': [
                {
                    'role': 'system',
                    'content': SYSTEM_PROMPT_WAVE_FIRST
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

def local_get_pos(context, symbol, state):
    """优先使用运行期维护的本地持仓数量；否则退化到平台 get_pos。"""
    try:
        lp = state.get('local_pos') if isinstance(state, dict) else None
        if lp is not None:
            return int(lp)
    except Exception:
        pass
    try:
        if getattr(Config, 'USE_PLATFORM_GET_POS', False):
            return int(get_pos(symbol))
        else:
            try:
                if isinstance(state, dict) and not state.get('_logged_no_pos_hint'):
                    Log(f"[{symbol}] [提示] 未初始化本地持仓且未启用平台get_pos，默认按空仓处理")
                    state['_logged_no_pos_hint'] = True
            except Exception:
                pass
            return 0
    except Exception:
        return 0

class TradeExecutor:
    """交易执行引擎 - 执行AI决策"""

    @staticmethod
    def execute_decision(context, symbol, decision, tick, state):
        """执行AI决策"""
        signal = decision.get('signal', 'hold')
        confidence = float(decision.get('confidence', 0) or 0)

        # 执行前熔断检查（基础理智校验）
        def _is_ai_insane(decision_obj, md_obj):
            try:
                sig = str(decision_obj.get('signal', 'hold') or 'hold').lower()
                if sig not in ('buy', 'sell', 'close', 'hold'):
                    return True, f"未知signal={sig}"
                if sig in ('buy', 'sell') and TRADER_RULEBOOK.get('mandatory_stop_loss', True):
                    _sl = decision_obj.get('stop_loss', None)
                    try:
                        _ = float(_sl)
                    except Exception:
                        return True, "新仓未提供有效止损"
                ps = float(decision_obj.get('position_size_pct', 0) or 0)
                if ps > float(TRADER_RULEBOOK.get('max_position_pct', 0.6)):
                    # 直接钳制并放行（避免过度拒单）；记录一次日志
                    decision_obj['position_size_pct'] = float(TRADER_RULEBOOK.get('max_position_pct', 0.6))
                # 顺势最小RRR
                try:
                    rrr = float(decision_obj.get('risk_reward_ratio', 0) or 0)
                    dtrend = str((md_obj or {}).get('d_trend') or 'SIDEWAYS').upper()
                    is_counter = ((dtrend == 'UPTREND' and sig == 'sell') or (dtrend == 'DOWNTREND' and sig == 'buy'))
                    if not is_counter and rrr and rrr < float(TRADER_RULEBOOK.get('min_reward_risk_ratio', 2.0)):
                        return True, f"顺势RRR不足({rrr:.2f}<{float(TRADER_RULEBOOK.get('min_reward_risk_ratio', 2.0)):.2f})"
                except Exception:
                    pass
                return False, "OK"
            except Exception as _e:
                return False, f"检查异常({_e})"

        insane, why = _is_ai_insane(decision, state.get('last_market_data') if isinstance(state, dict) else None)
        if insane:
            Log(f"[{symbol}] 执行熔断: {why}")
            return

        # 信心度检查（热参数）
        min_conf = float(Config.MIN_AI_CONFIDENCE)
        if confidence < min_conf:
            Log(f"AI信心度不足 ({confidence:.2f} < {min_conf}), 不执行交易")
            return

        # 新开仓必须提供有效止损
        if signal in ('buy', 'sell'):
            _sl = decision.get('stop_loss', None)
            if _sl is None:
                Log(f"[{symbol}] [警告] AI未给出止损，拒绝新仓")
                return
            try:
                _sl_v = float(_sl)
            except Exception:
                Log(f"[{symbol}] [警告] 止损格式无效({repr(_sl)}), 拒绝新仓")
                return

        # 获取当前持仓（优先运行期本地值；退化到平台）
        current_volume = local_get_pos(context, symbol, state)

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
        # AI提供的最小间隔（分钟）→ 作为pending冷却的下限
        try:
            ai_min_iv = float(decision.get('min_interval_minutes', 0) or 0)
            if ai_min_iv > 0:
                try:
                    st_cd = float(state.get('pending_cooldown_minutes') or 0)
                except Exception:
                    st_cd = 0.0
                state['pending_cooldown_minutes'] = max(st_cd, ai_min_iv)
        except Exception:
            pass
        # 动态止盈相关（交由AI管理）
        trailing_type = str(decision.get('trailing_type', 'none') or 'none').lower()
        trailing_atr_mult = float(decision.get('trailing_atr_mult', 0) or 0)
        trailing_percent = float(decision.get('trailing_percent', 0) or 0)
        time_stop_minutes = float(decision.get('time_stop_minutes', 0) or 0)

        # -- 日线自适应默认（若AI未提供则补齐） --
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
        # 数值化小工具，避免 None/NaN/Inf 导致格式化/比较异常
        def _nf(v, d=0.0):
            try:
                f = float(v)
                if not math.isfinite(f):
                    return float(d)
                return f
            except Exception:
                return float(d)

        last_price = _nf(last_price, 0.0)
        bid_price = _nf(bid_price, last_price)
        ask_price = _nf(ask_price, last_price)
        mid_px_val = _nf(md.get('mid_price') if isinstance(md, dict) else last_price, last_price)

        # 低换手/反趋势硬门槛
        try:
            if getattr(Config, 'LOW_CHURN_MODE', False) and signal in ('buy', 'sell'):
                trend_daily = str((md.get('d_trend') if isinstance(md, dict) else 'SIDEWAYS') or 'SIDEWAYS').upper()
                is_counter = ((trend_daily == 'UPTREND' and signal == 'sell') or (trend_daily == 'DOWNTREND' and signal == 'buy'))
                rrr = float(decision.get('risk_reward_ratio', 0) or 0)
                edge = decision.get('edge_over_friction', None)

                if is_counter:
                    reasons = []
                    if confidence < float(getattr(Config, 'CT_MIN_CONF', 0.7)):
                        reasons.append(f"conf {confidence:.2f}<{getattr(Config,'CT_MIN_CONF',0.7):.2f}")
                    if rrr < float(getattr(Config, 'CT_MIN_RRR', 2.5)):
                        reasons.append(f"RRR {rrr:.2f}<{getattr(Config,'CT_MIN_RRR',2.5):.2f}")
                    try:
                        if edge is not None and float(edge) <= float(getattr(Config, 'CT_MIN_EDGE', 0.0)):
                            reasons.append(f"edge {float(edge):.2f}<=CT_MIN_EDGE")
                    except Exception:
                        pass
                    if reasons:
                        Log(f"[{symbol}] 反趋势门槛未过({trend_daily} vs {signal}): {', '.join(reasons)}，忽略新仓")
                        return
                    # 缩仓与冷却
                    ps = float(decision.get('position_size_pct', 0.5) or 0.5)
                    max_pct = float(getattr(Config, 'CT_MAX_PCT', 0.15))
                    if ps > max_pct:
                        decision['position_size_pct'] = max_pct
                    cd_min = float(getattr(Config, 'CT_COOLDOWN_MIN', 10))
                    try:
                        st_cd = float(state.get('pending_cooldown_minutes') or 0)
                    except Exception:
                        st_cd = 0
                    state['pending_cooldown_minutes'] = max(st_cd, cd_min)
                    Log(f"[{symbol}] 反趋势单确认: 缩仓至{decision.get('position_size_pct')}, 冷却≥{int(cd_min)}min, RRR={rrr:.2f}, conf={confidence:.2f}")
                else:
                    min_rrr = float(getattr(Config, 'TREND_MIN_RRR', 2.0))
                    if rrr and rrr < min_rrr:
                        Log(f"[{symbol}] 顺势RRR不足({rrr:.2f}<{min_rrr:.2f})，忽略新仓")
                        return
        except Exception:
            pass

        # 执行前检查
        try:
            if getattr(Config, 'DEBUG_EXEC_CHECK', False):
                Log(f"[{symbol}] exec-check: pos={current_volume}, single_side={getattr(Config,'SINGLE_SIDE_MODE',True)}, same_side={getattr(Config,'ALLOW_SAME_SIDE_PYRAMIDING',True)}, size_pct={decision.get('position_size_pct')}, tradeability={tradeability_score:.2f}, style={order_price_style}")
        except Exception:
            pass

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
                spread_limit = float(Config.SPREAD_RATIO_LIMIT)
                if (spread_val / mid_px_val) > spread_limit:
                    pct = min(pct, 0.3)
            # 公司级上限
            pct = min(pct, float(TRADER_RULEBOOK.get('max_position_pct', 0.6)))
            return pct

        # 账户与合约参数 -- 用真实数据替代固定值
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

        def _align_price(p, side):
            """按方向对齐到合法价位：
            - buy/cover 向上取整（不低于盘口价，提升成交概率）
            - sell/short 向下取整
            """
            if not tick_size or tick_size <= 0:
                return p
            try:
                steps = p / tick_size
                if side in ('buy', 'cover'):
                    return math.ceil(steps) * tick_size
                else:
                    return math.floor(steps) * tick_size
            except Exception:
                return p

        # 反手/再入场冷却（避免刚止损立刻反向）
        if signal in ('buy', 'sell') and current_volume == 0:
            reentry_until = state.get('reentry_until') if isinstance(state, dict) else None
            if isinstance(reentry_until, (int, float)) and time.time() < reentry_until:
                left = int(reentry_until - time.time())
                Log(f"[{symbol}] 处于再入场冷却，剩余 {left}s，跳过新仓信号 {signal}")
                return

        # 与上次离场/入场价格的最小间距（可选）——防止同价反复开平
        if signal in ('buy', 'sell') and current_volume == 0:
            try:
                min_ticks_gap = int(getattr(Config, 'MIN_REENTRY_GAP_TICKS', 0) or 0)
            except Exception:
                min_ticks_gap = 0
            if min_ticks_gap and tick_size and tick_size > 0:
                ref_px = None
                try:
                    ref_px = state.get('last_exit_price') or state.get('last_entry_px')
                except Exception:
                    ref_px = None
                if isinstance(ref_px, (int, float)) and math.isfinite(ref_px):
                    gap = abs(last_price - float(ref_px))
                    if gap < (min_ticks_gap * tick_size):
                        Log(f"[{symbol}] 与上次价{ref_px:.2f}间距{gap:.2f} < {min_ticks_gap}tick，跳过新仓 {signal}")
                        return

        # 止损护栏与复位：确保方向正确+最小间距，并基于实际下单价复位
        def _guard_and_rebase_stop(side, entry_price, sl_in, atr_val, tick_sz):
            try:
                sl_val = float(sl_in)
            except Exception:
                return sl_in
            try:
                min_ticks = max(1, int(getattr(Config, 'MIN_STOP_TICKS', 5)))
            except Exception:
                min_ticks = 5
            try:
                atr_mult = float(getattr(Config, 'MIN_STOP_ATR_MULT', 0.25))
            except Exception:
                atr_mult = 0.25
            min_gap = 0.0
            if tick_sz and tick_sz > 0:
                min_gap = max(min_ticks * tick_sz, float(atr_val or 0) * atr_mult)
            else:
                min_gap = float(atr_val or 0) * atr_mult

            if side == 'long':
                R_ai = max(0.0, (entry_price - sl_val))
                R_new = max(R_ai, min_gap)
                sl_new = entry_price - R_new
                if tick_sz and tick_sz > 0:
                    try:
                        sl_new = math.floor((sl_new / tick_sz)) * tick_sz
                    except Exception:
                        pass
                return sl_new
            else:
                R_ai = max(0.0, (sl_val - entry_price))
                R_new = max(R_ai, min_gap)
                sl_new = entry_price + R_new
                if tick_sz and tick_sz > 0:
                    try:
                        sl_new = math.ceil((sl_new / tick_sz)) * tick_sz
                    except Exception:
                        pass
                return sl_new
            

        # 冷却期内禁止新开仓
        if signal in ('buy', 'sell') and current_volume == 0:
            cooldown_until = state.get('cooldown_until') if isinstance(state, dict) else None
            if isinstance(cooldown_until, (int, float)) and time.time() < cooldown_until:
                left = int(cooldown_until - time.time())
                Log(f"[{symbol}] 处于冷却期，剩余 {left}s，跳过新仓信号 {signal}")
                return

        # 单向模式：已有持仓且收到反向信号 → 先平仓，禁止反向新开
        if getattr(Config, 'SINGLE_SIDE_MODE', True):
            try:
                if signal == 'sell' and current_volume > 0:
                    Log(f"[{symbol}] 单向模式：持多{current_volume}，收到sell→执行平多")
                    send_target_order(symbol, 0)
                    state['ai_decision'] = None
                    state['position_avg_price'] = 0
                    return
                if signal == 'buy' and current_volume < 0:
                    Log(f"[{symbol}] 单向模式：持空{abs(current_volume)}，收到buy→执行平空")
                    send_target_order(symbol, 0)
                    state['ai_decision'] = None
                    state['position_avg_price'] = 0
                    return
            except Exception:
                pass

        def _normalize_price(p, tick, fallback):
            try:
                from decimal import Decimal, ROUND_HALF_UP
                try:
                    pf = float(p)
                    if not math.isfinite(pf):
                        pf = float(fallback)
                except Exception:
                    pf = float(fallback)
                tk = float(tick_size) if tick_size and tick_size > 0 else 0.01
                q = Decimal(str(pf)).quantize(Decimal(str(tk)), rounding=ROUND_HALF_UP)
                return float(q)
            except Exception:
                try:
                    return float(fallback)
                except Exception:
                    return 0.0

        # 同向加仓（多）
        if getattr(Config, 'ALLOW_SAME_SIDE_PYRAMIDING', True) and signal == 'buy' and current_volume > 0:
            position_size = _adjust_position_size(decision.get('position_size_pct', 0.5))
            if position_size <= 0:
                Log(f"[{symbol}] 同向加仓: 目标仓位占比=0，忽略")
                return
            tmp_price = _choose_price('buy')
            tmp_price = _nf(tmp_price, last_price)
            order_price = _align_price(tmp_price, 'buy')
            order_price = _normalize_price(order_price, tick_size, last_price)
            price_for_size = order_price if (isinstance(order_price, float) and order_price > 0) else last_price

            notional_per_lot = price_for_size * mult
            margin_per_lot = notional_per_lot * max(long_mr, 0.01)
            if margin_per_lot <= 0:
                Log(f"[{symbol}] 保证金率异常({long_mr:.4f}), 同向加仓跳过")
                return
            max_lots_by_margin = int((available / (margin_per_lot * float(Config.NEW_TRADE_MARGIN_BUFFER))))
            target_lots = int((equity * position_size) / notional_per_lot) if notional_per_lot > 0 else 0
            current_lots = int(abs(current_volume))
            volume = max(0, min(max_lots_by_margin, target_lots) - current_lots)
            if volume <= 0:
                Log(f"[{symbol}] 同向加仓: 已达到目标仓(当前={current_lots}, 目标={target_lots})，忽略")
                return
            margin_post = used_margin + volume * margin_per_lot
            guarantee_ratio = (equity / margin_post) if margin_post > 0 else 999
            min_gr = float(Config.MIN_GUARANTEE_RATIO)
            if guarantee_ratio < min_gr:
                Log(f"[{symbol}] 同向加仓: 担保比不足({guarantee_ratio:.2f} < {min_gr:.2f})，拒绝")
                return

            buy(symbol, order_price, volume)
            Log(f"[{symbol}] 同向加仓: 加多 {volume}手 @ {order_price:.2f}，当前={current_lots}→目标={target_lots}，信心度={confidence:.2f}")
            try:
                src_l = PlatformAdapter.get_margin_ratio_source(symbol, 'long')
                tick_dbg = PlatformAdapter.get_pricetick(symbol) or 0
                minv_dbg = PlatformAdapter.get_min_volume(symbol)
                Log(f"[{symbol}] 加仓规模: equity={equity:.0f}, available={available:.0f}, notional/lot={notional_per_lot:.0f}, margin/lot={margin_per_lot:.0f}, max_lots={max_lots_by_margin}; used_margin→{margin_post:.0f}, 担保比={guarantee_ratio:.2f} | 合约: size={mult}, tick={tick_dbg}, min={minv_dbg}, m_long={long_mr:.4f}({src_l})")
            except Exception:
                Log(f"[{symbol}] 加仓规模: equity={equity:.0f}, available={available:.0f}, notional/lot={notional_per_lot:.0f}, margin/lot={margin_per_lot:.0f}, max_lots={max_lots_by_margin}; used_margin→{margin_post:.0f}, 担保比={guarantee_ratio:.2f}")
            return

        # 同向加仓（空）
        if getattr(Config, 'ALLOW_SAME_SIDE_PYRAMIDING', True) and signal == 'sell' and current_volume < 0:
            position_size = _adjust_position_size(decision.get('position_size_pct', 0.5))
            if position_size <= 0:
                Log(f"[{symbol}] 同向加仓: 目标仓位占比=0，忽略")
                return
            tmp_price = _choose_price('sell')
            tmp_price = _nf(tmp_price, last_price)
            order_price = _align_price(tmp_price, 'sell')
            order_price = _normalize_price(order_price, tick_size, last_price)
            price_for_size = order_price if (isinstance(order_price, float) and order_price > 0) else last_price

            notional_per_lot = price_for_size * mult
            margin_per_lot = notional_per_lot * max(short_mr, 0.01)
            if margin_per_lot <= 0:
                Log(f"[{symbol}] 保证金率异常({short_mr:.4f}), 同向加仓跳过")
                return
            max_lots_by_margin = int((available / (margin_per_lot * float(Config.NEW_TRADE_MARGIN_BUFFER))))
            target_lots = int((equity * position_size) / notional_per_lot) if notional_per_lot > 0 else 0
            current_lots = int(abs(current_volume))
            volume = max(0, min(max_lots_by_margin, target_lots) - current_lots)
            if volume <= 0:
                Log(f"[{symbol}] 同向加仓: 已达到目标仓(当前={current_lots}, 目标={target_lots})，忽略")
                return
            margin_post = used_margin + volume * margin_per_lot
            guarantee_ratio = (equity / margin_post) if margin_post > 0 else 999
            min_gr = float(Config.MIN_GUARANTEE_RATIO)
            if guarantee_ratio < min_gr:
                Log(f"[{symbol}] 同向加仓: 担保比不足({guarantee_ratio:.2f} < {min_gr:.2f})，拒绝")
                return

            short(symbol, order_price, volume)
            Log(f"[{symbol}] 同向加仓: 加空 {volume}手 @ {order_price:.2f}，当前={current_lots}→目标={target_lots}，信心度={confidence:.2f}")
            try:
                src_s = PlatformAdapter.get_margin_ratio_source(symbol, 'short')
                tick_dbg = PlatformAdapter.get_pricetick(symbol) or 0
                minv_dbg = PlatformAdapter.get_min_volume(symbol)
                Log(f"[{symbol}] 加仓规模: equity={equity:.0f}, available={available:.0f}, notional/lot={notional_per_lot:.0f}, margin/lot={margin_per_lot:.0f}, max_lots={max_lots_by_margin}; used_margin→{margin_post:.0f}, 担保比={guarantee_ratio:.2f} | 合约: size={mult}, tick={tick_dbg}, min={minv_dbg}, m_short={short_mr:.4f}({src_s})")
            except Exception:
                Log(f"[{symbol}] 加仓规模: equity={equity:.0f}, available={available:.0f}, notional/lot={notional_per_lot:.0f}, margin/lot={margin_per_lot:.0f}, max_lots={max_lots_by_margin}; used_margin→{margin_post:.0f}, 担保比={guarantee_ratio:.2f}")
            return

        if signal == 'buy' and current_volume == 0:
            # 开多仓
            position_size = _adjust_position_size(decision.get('position_size_pct', 0.5))
            if position_size <= 0:
                Log(f"[{symbol}] 运行期gating后仓位=0，忽略新仓 buy")
                return
            tmp_price = _choose_price('buy')
            tmp_price = _nf(tmp_price, last_price)
            order_price = _align_price(tmp_price, 'buy')
            order_price = _normalize_price(order_price, tick_size, last_price)
            price_for_size = order_price if (isinstance(order_price, float) and order_price > 0) else last_price

            notional_per_lot = price_for_size * mult
            margin_per_lot = notional_per_lot * max(long_mr, 0.01)
            # 用账户可用资金推导最大可开手数（留一点安全边际）
            if margin_per_lot <= 0:
                Log(f"[{symbol}] 保证金率异常({long_mr:.4f}), 跳过新仓")
                return
            max_lots_by_margin = int((available / (margin_per_lot * float(Config.NEW_TRADE_MARGIN_BUFFER))))
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
                min_gr = float(Config.MIN_GUARANTEE_RATIO)
                if guarantee_ratio < min_gr:
                    Log(f"[{symbol}] 担保比不足({guarantee_ratio:.2f} < {min_gr:.2f}), 拒绝新仓")
                    return
                # 止损护栏与复位（方向正确+最小间距），基于实际下单价
                try:
                    md_for_sl = state.get('last_market_data') if isinstance(state, dict) else None
                    atr_val = (md_for_sl or {}).get('atr')
                except Exception:
                    atr_val = None
                try:
                    sl_in = decision.get('stop_loss')
                    sl_new = _guard_and_rebase_stop('long', order_price, sl_in, atr_val, tick_size)
                    decision['stop_loss'] = sl_new
                except Exception:
                    pass

                buy(symbol, order_price, volume)
                Log(f"[{symbol}] AI决策: 开多 {volume}手 @ {order_price:.2f}, 信心度={confidence:.2f}")
                # 记录下单价/时间用于滑点统计与再入场参考
                try:
                    state['last_order_price'] = float(order_price)
                    state['last_order_ts'] = time.time()
                    state['last_entry_px'] = float(order_price)
                except Exception:
                    pass
                _sl = decision.get('stop_loss')
                # 若AI给的止盈在错误一侧，则忽略以免误导日志
                _pt = decision.get('profit_target')
                try:
                    if _pt is not None and float(_pt) <= float(order_price):
                        _pt = None
                        decision['profit_target'] = None
                except Exception:
                    _pt = None
                _sl_txt = f"{float(_sl):.2f}" if isinstance(_sl, (int, float)) else "N/A"
                _pt_txt = f"{float(_pt):.2f}" if isinstance(_pt, (int, float)) else "N/A"
                # 计算首个分批目标（若AI提供levels_r）用于展示，避免方向误解
                try:
                    levels = decision.get('scale_out_levels_r') or []
                    first_tgt = None
                    if isinstance(levels, list) and levels:
                        levels_sorted = sorted([float(x) for x in levels if x is not None and float(x) > 0])
                        if _sl and order_price and levels_sorted:
                            R = float(order_price) - float(_sl)
                            first_tgt = float(order_price) + levels_sorted[0] * R
                    first_txt = f"{first_tgt:.2f}" if first_tgt is not None else "-"
                except Exception:
                    first_txt = "-"
                Log(f"止损={_sl_txt}, 止盈(AI)={_pt_txt}, 首个分批目标={first_txt}")
                try:
                    src_l = PlatformAdapter.get_margin_ratio_source(symbol, 'long')
                    tick_dbg = PlatformAdapter.get_pricetick(symbol) or 0
                    minv_dbg = PlatformAdapter.get_min_volume(symbol)
                    Log(f"[{symbol}] 规模: equity={equity:.0f}, available={available:.0f}, notional/lot={notional_per_lot:.0f}, margin/lot={margin_per_lot:.0f}, target_lots={lots_by_target}, max_lots={max_lots_by_margin}, choose={volume}; used_margin→{margin_post:.0f}, 担保比={guarantee_ratio:.2f} | 合约: size={mult}, tick={tick_dbg}, min={minv_dbg}, m_long={long_mr:.4f}({src_l})")
                except Exception:
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

                # 初始化分批止盈计划（基于R倍数）
                try:
                    lv = decision.get('scale_out_levels_r') or []
                    pc = decision.get('scale_out_pcts') or []
                    if isinstance(lv, list) and isinstance(pc, list) and len(lv) == len(pc) and len(lv) > 0:
                        # 过滤非法/负数，并按R从小到大排序
                        pairs = [(float(lv[i]), float(pc[i])) for i in range(len(lv)) if lv[i] is not None and pc[i] is not None]
                        pairs = [(r, p) for (r, p) in pairs if r > 0 and p > 0]
                        pairs.sort(key=lambda x: x[0])
                        if pairs:
                            levels = [r for r, _ in pairs]
                            pcts = [p for _, p in pairs]
                            # 归一化比例，最多到1.0（最后一档吃掉剩余）
                            tot = sum(pcts)
                            if tot > 0:
                                pcts = [min(1.0, p / tot) for p in pcts]
                            # 基于入场均价与止损计算目标价
                            sl = float(decision.get('stop_loss') or 0)
                            entry = float(order_price)
                            if sl > 0 and entry > 0 and entry > sl:
                                R = entry - sl
                                targets = [entry + r * R for r in levels]
                                state['scale_out_plan'] = {
                                    'levels_r': levels,
                                    'pcts': pcts,
                                    'targets': targets,
                                    'executed': [False] * len(levels),
                                    'init_volume': None,  # 在首次检查或成交回报时设置
                                    'entry_price': entry,
                                    'stop_loss': sl,
                                    'side': 'long'
                                }
                            else:
                                state['scale_out_plan'] = None
                    else:
                        state['scale_out_plan'] = None
                except Exception:
                    state['scale_out_plan'] = None

        elif signal == 'sell' and current_volume == 0:
            # 开空仓
            position_size = _adjust_position_size(decision.get('position_size_pct', 0.5))
            if position_size <= 0:
                Log(f"[{symbol}] 运行期gating后仓位=0，忽略新仓 sell")
                return
            tmp_price = _choose_price('sell')
            tmp_price = _nf(tmp_price, last_price)
            order_price = _align_price(tmp_price, 'sell')
            order_price = _normalize_price(order_price, tick_size, last_price)
            price_for_size = order_price if (isinstance(order_price, float) and order_price > 0) else last_price
            notional_per_lot = price_for_size * mult
            margin_per_lot = notional_per_lot * max(short_mr, 0.01)
            if margin_per_lot <= 0:
                Log(f"[{symbol}] 保证金率异常({short_mr:.4f}), 跳过新仓")
                return
            max_lots_by_margin = int((available / (margin_per_lot * float(Config.NEW_TRADE_MARGIN_BUFFER))))
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
                min_gr = float(Config.MIN_GUARANTEE_RATIO)
                if guarantee_ratio < min_gr:
                    Log(f"[{symbol}] 担保比不足({guarantee_ratio:.2f} < {min_gr:.2f}), 拒绝新仓")
                    return
                # 止损护栏与复位（方向正确+最小间距），基于实际下单价
                try:
                    md_for_sl = state.get('last_market_data') if isinstance(state, dict) else None
                    atr_val = (md_for_sl or {}).get('atr')
                except Exception:
                    atr_val = None
                try:
                    sl_in = decision.get('stop_loss')
                    sl_new = _guard_and_rebase_stop('short', order_price, sl_in, atr_val, tick_size)
                    decision['stop_loss'] = sl_new
                except Exception:
                    pass

                short(symbol, order_price, volume)
                Log(f"[{symbol}] AI决策: 开空 {volume}手 @ {order_price:.2f}, 信心度={confidence:.2f}")
                # 记录下单价/时间用于滑点统计与再入场参考
                try:
                    state['last_order_price'] = float(order_price)
                    state['last_order_ts'] = time.time()
                    state['last_entry_px'] = float(order_price)
                except Exception:
                    pass
                _sl = decision.get('stop_loss')
                _pt = decision.get('profit_target')
                try:
                    if _pt is not None and float(_pt) >= float(order_price):
                        _pt = None
                        decision['profit_target'] = None
                except Exception:
                    _pt = None
                _sl_txt = f"{float(_sl):.2f}" if isinstance(_sl, (int, float)) else "N/A"
                _pt_txt = f"{float(_pt):.2f}" if isinstance(_pt, (int, float)) else "N/A"
                # 计算首个分批目标（空头）
                try:
                    levels = decision.get('scale_out_levels_r') or []
                    first_tgt = None
                    if isinstance(levels, list) and levels:
                        levels_sorted = sorted([float(x) for x in levels if x is not None and float(x) > 0])
                        if _sl and order_price and levels_sorted:
                            R = float(_sl) - float(order_price)
                            first_tgt = float(order_price) - levels_sorted[0] * R
                    first_txt = f"{first_tgt:.2f}" if first_tgt is not None else "-"
                except Exception:
                    first_txt = "-"
                Log(f"止损={_sl_txt}, 止盈(AI)={_pt_txt}, 首个分批目标={first_txt}")
                try:
                    src_s = PlatformAdapter.get_margin_ratio_source(symbol, 'short')
                    tick_dbg = PlatformAdapter.get_pricetick(symbol) or 0
                    minv_dbg = PlatformAdapter.get_min_volume(symbol)
                    Log(f"[{symbol}] 规模: equity={equity:.0f}, available={available:.0f}, notional/lot={notional_per_lot:.0f}, margin/lot={margin_per_lot:.0f}, target_lots={lots_by_target}, max_lots={max_lots_by_margin}, choose={volume}; used_margin→{margin_post:.0f}, 担保比={guarantee_ratio:.2f} | 合约: size={mult}, tick={tick_dbg}, min={minv_dbg}, m_short={short_mr:.4f}({src_s})")
                except Exception:
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

                # 初始化分批止盈计划（基于R倍数）-- 空头
                try:
                    lv = decision.get('scale_out_levels_r') or []
                    pc = decision.get('scale_out_pcts') or []
                    if isinstance(lv, list) and isinstance(pc, list) and len(lv) == len(pc) and len(lv) > 0:
                        pairs = [(float(lv[i]), float(pc[i])) for i in range(len(lv)) if lv[i] is not None and pc[i] is not None]
                        pairs = [(r, p) for (r, p) in pairs if r > 0 and p > 0]
                        pairs.sort(key=lambda x: x[0])
                        if pairs:
                            levels = [r for r, _ in pairs]
                            pcts = [p for _, p in pairs]
                            tot = sum(pcts)
                            if tot > 0:
                                pcts = [min(1.0, p / tot) for p in pcts]
                            sl = float(decision.get('stop_loss') or 0)
                            entry = float(order_price)
                            if sl > 0 and entry > 0 and sl > entry:
                                R = sl - entry
                                targets = [entry - r * R for r in levels]
                                state['scale_out_plan'] = {
                                    'levels_r': levels,
                                    'pcts': pcts,
                                    'targets': targets,
                                    'executed': [False] * len(levels),
                                    'init_volume': None,
                                    'entry_price': entry,
                                    'stop_loss': sl,
                                    'side': 'short'
                                }
                            else:
                                state['scale_out_plan'] = None
                    else:
                        state['scale_out_plan'] = None
                except Exception:
                    state['scale_out_plan'] = None

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
# 后台AI任务（异步）
# ========================================

def _spawn_ai_job(context, sym):
    """在后台线程中发起AI调用，避免阻塞on_tick/CTP线程。
    要求 state['last_market_data'] 可用；否则跳过。
    """
    try:
        st = context.state.get(sym, {})
    except Exception:
        return False
    if not isinstance(st, dict):
        return False
    if st.get('ai_in_flight'):
        return False
    md = st.get('last_market_data')
    if not isinstance(md, dict):
        # 无有效市场数据快照时，不启动后台任务
        return False

    st['ai_in_flight'] = True
    # 分配序号，用于去重消费结果
    try:
        st['ai_job_seq'] = int(st.get('ai_job_seq') or 0) + 1
    except Exception:
        st['ai_job_seq'] = 1
    job_seq = int(st['ai_job_seq'])
    # 从Key池获取一个Key（保证同一Key同时只跑1个请求）
    key_idx, key_value, key_mask = None, None, None
    try:
        key_idx, key_value, key_mask = context.key_pool.acquire()
        if key_mask:
            try:
                Log(f"[{sym}] 使用Key {key_mask} 提交AI任务")
            except Exception:
                pass
    except Exception:
        # 获取失败时，降级使用默认Key
        key_idx, key_value, key_mask = None, None, None

    def _run():
        try:
            prompt = construct_autonomous_trading_prompt(md)
            decision, error = context.ai_engine.call_deepseek_api(prompt, api_key=key_value)
            if decision:
                # 将结果交回主循环处理
                st['pending_decision'] = decision
                st['pending_seq'] = job_seq
            else:
                try:
                    Log(f"[{sym}] AI后台任务失败: {error}")
                except Exception:
                    pass
        except Exception as e:
            try:
                Log(f"[{sym}] AI后台任务异常: {e}")
            except Exception:
                pass
        finally:
            st['ai_in_flight'] = False
            try:
                if key_idx is not None:
                    context.key_pool.release(key_idx)
            except Exception:
                pass

    th = threading.Thread(target=_run, name=f"AIJob-{sym}", daemon=True)
    try:
        th.start()
    except Exception:
        st['ai_in_flight'] = False
        return False
    return True


# ========================================
# 风控层 (唯一的硬性约束)
# ========================================

class APIKeyPool:
    """简单的DeepSeek多Key轮询池：
    - 每个Key同一时刻最多1个在途请求
    - 选择策略：优先选 in_flight==0 的Key，否则选 in_flight 最小的
    - 轮询指针用于在多个可用Key间均衡
    """
    def __init__(self, keys):
        self._keys = [k for k in (keys or []) if isinstance(k, str) and k.strip()]
        if not self._keys:
            self._keys = [getattr(Config, 'DEEPSEEK_API_KEY', '')]
        self._keys = [k for k in self._keys if k]
        self._n = len(self._keys)
        self._inflight = [0] * self._n
        self._ptr = 0
        self._lock = threading.Lock()

    def size(self):
        return self._n

    @staticmethod
    def _mask(k):
        try:
            if not k:
                return 'KEY-?'
            return k[:8] + '…' + k[-4:]
        except Exception:
            return 'KEY-?'

    def acquire(self):
        with self._lock:
            if self._n == 0:
                return None, None, None
            # 优先选择 in_flight == 0 的，从轮询指针开始
            for i in range(self._n):
                idx = (self._ptr + i) % self._n
                if self._inflight[idx] == 0:
                    self._inflight[idx] += 1
                    self._ptr = (idx + 1) % self._n
                    return idx, self._keys[idx], self._mask(self._keys[idx])
            # 否则选择 in_flight 最小的
            min_val = min(self._inflight)
            idx = self._inflight.index(min_val)
            self._inflight[idx] += 1
            self._ptr = (idx + 1) % self._n
            return idx, self._keys[idx], self._mask(self._keys[idx])

    def release(self, idx):
        with self._lock:
            try:
                if idx is not None and 0 <= idx < self._n:
                    self._inflight[idx] = max(0, self._inflight[idx] - 1)
            except Exception:
                pass

class RiskController:
    """风控控制器 - 执行安全边界"""

    @staticmethod
    def check_and_enforce(context, symbol, tick, state):
        """检查并执行风控规则"""
        # 获取当前持仓 (使用正确的Gkoudai API)
        position_volume = local_get_pos(context, symbol, state)

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

            # 1. 单笔最大亏损检查（固定参数）
            max_single = float(Config.MAX_SINGLE_TRADE_LOSS_PCT)
            if pnl_pct < -max_single:
                Log(f"[{symbol}] [警告] 触发单笔最大亏损限制 ({pnl_pct*100:.2f}%), 强制平仓!")
                send_target_order(symbol, 0)
                state['ai_decision'] = None
                state['position_avg_price'] = 0
                return

        # 2. 单日最大亏损检查（以本地估算的权益为基准）
        base_equity = max(1.0, float((estimate_account(context, symbol, current_price, state).get('equity') or 0.0)))
        daily_pnl_pct = context.daily_pnl / base_equity
        max_daily = float(Config.MAX_DAILY_LOSS_PCT)
        if daily_pnl_pct < -max_daily:
            Log(f"[{symbol}] [警告] 触发单日最大亏损限制 ({daily_pnl_pct*100:.2f}%), 停止交易!")
            if position_volume != 0:
                send_target_order(symbol, 0)
            context.trading_allowed = False
            state['ai_decision'] = None
            state['position_avg_price'] = 0
            return

        # 3. 强制平仓时间检查（白盘/夜盘动态处理，夜盘跨日）
        def _parse_dt_from_tick(t):
            ts = getattr(t, 'strtime', None)
            if ts:
                try:
                    # 纯字符串解析得到 naive 本地时间
                    return datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    pass
            dto = getattr(t, 'datetime', None)
            if isinstance(dto, datetime):
                # 如带 tzinfo，统一转为本地 naive，避免 offset-aware 比较错误
                try:
                    if dto.tzinfo is not None and dto.tzinfo.utcoffset(dto) is not None:
                        return dto.astimezone().replace(tzinfo=None)
                except Exception:
                    try:
                        # 退化：直接去掉 tzinfo
                        return dto.replace(tzinfo=None)
                    except Exception:
                        pass
                return dto
            return datetime.now()

        now_dt = _parse_dt_from_tick(tick)
        # 再保险：如果仍是 aware，则转为本地 naive
        try:
            if now_dt.tzinfo is not None and now_dt.tzinfo.utcoffset(now_dt) is not None:
                now_dt = now_dt.astimezone().replace(tzinfo=None)
        except Exception:
            try:
                now_dt = now_dt.replace(tzinfo=None)
            except Exception:
                pass

        def _force_close_deadline(now_dt_local):
            # 夜盘判断：>= rollover_hour 视为进入夜盘；次日03:00前也仍属于夜盘
            try:
                roll_h = int(Config.TRADING_DAY_ROLLOVER_HOUR)
            except Exception:
                roll_h = 21
            night_close_str = getattr(Config, 'FORCE_CLOSE_TIME_NIGHT', '02:25:00')
            day_close_str = getattr(Config, 'FORCE_CLOSE_TIME_DAY', '14:55:00')
            try:
                night_h, night_m, night_s = [int(x) for x in night_close_str.split(':')]
            except Exception:
                night_h, night_m, night_s = 2, 25, 0
            try:
                day_h, day_m, day_s = [int(x) for x in day_close_str.split(':')]
            except Exception:
                day_h, day_m, day_s = 14, 55, 0

            if (now_dt_local.hour >= roll_h) or (now_dt_local.hour < 3):
                # 夜盘：强平时间为次日 night_close
                base_date = now_dt_local.date()
                if now_dt_local.hour >= roll_h:
                    deadline = datetime.combine(base_date, datetime.min.time()).replace(hour=night_h, minute=night_m, second=night_s) + timedelta(days=1)
                else:
                    deadline = datetime.combine(base_date, datetime.min.time()).replace(hour=night_h, minute=night_m, second=night_s)
                label = night_close_str
            else:
                # 白盘：当日 day_close
                deadline = datetime.combine(now_dt_local.date(), datetime.min.time()).replace(hour=day_h, minute=day_m, second=day_s)
                label = day_close_str
            return deadline, label

        try:
            from datetime import timedelta
        except Exception:
            pass

        deadline_dt, deadline_label = _force_close_deadline(now_dt)

        # 统一比较（如遇类型错误，退化为双方转 naive 再比较）
        def _ge(a, b):
            try:
                return a >= b
            except TypeError:
                try:
                    a2 = a.replace(tzinfo=None)
                except Exception:
                    a2 = a
                try:
                    b2 = b.replace(tzinfo=None)
                except Exception:
                    b2 = b
                return a2 >= b2

        if _ge(now_dt, deadline_dt):
            Log(f"[{symbol}] [警告] 到达强制平仓时间 {deadline_label}, 强制平仓!")
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
                            # 使用%%格式化，规避某些平台对花括号/模板符号的误处理
                            Log("[%s] 触发动态追踪止损(long): %.2f <= %.2f" % (str(symbol), float(current_price), float(dyn_sl)))
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
                            # 使用%%格式化，规避某些平台对花括号/模板符号的误处理
                            Log("[%s] 触发动态追踪止损(short): %.2f >= %.2f" % (str(symbol), float(current_price), float(dyn_sl)))
                            send_target_order(symbol, 0)
                            state['ai_decision'] = None
                            state['position_avg_price'] = 0
                            return

            # 时间止盈（超时离场） - 尽量避免大块try，降低平台解析异常概率
            _ts_raw = trailing.get('time_stop_minutes') if isinstance(trailing, dict) else 0
            try:
                ts_min = float(_ts_raw or 0)
            except Exception:
                ts_min = 0.0
            if ts_min > 0 and state.get('entry_time'):
                hold_m = (datetime.now() - state['entry_time']).total_seconds() / 60.0
                if hold_m >= ts_min:
                    Log(f"[{symbol}] 触发时间离场: 持仓{hold_m:.1f}min >= {ts_min:.1f}min")
                    send_target_order(symbol, 0)
                    state['ai_decision'] = None
                    state['position_avg_price'] = 0
                    return

            # 分批止盈（R倍数触发的部分平仓）
            try:
                plan = state.get('scale_out_plan') if isinstance(state, dict) else None
                if isinstance(plan, dict) and plan.get('targets') and plan.get('pcts') and plan.get('levels_r'):
                    # 初始化基准手数
                    if plan.get('init_volume') in (None, 0):
                        try:
                            try:
                                plan['init_volume'] = abs(int(local_get_pos(context, symbol, state)))
                            except Exception:
                                plan['init_volume'] = plan.get('init_volume') or None
                        except Exception:
                            plan['init_volume'] = abs(position_volume)
                        # 基于初始持仓计算各档手数
                        base = int(plan['init_volume'] or 0)
                        if base > 0:
                            raw = [max(0, int(base * p)) for p in plan['pcts']]
                            # 确保至少有1手，并分配余数给最后一档
                            if sum(raw) == 0:
                                raw = [0] * len(plan['pcts'])
                                raw[-1] = 1
                            if sum(raw) < base:
                                raw[-1] += (base - sum(raw))
                            plan['tranche_qtys'] = raw
                        else:
                            plan['tranche_qtys'] = []
                        state['scale_out_plan'] = plan

                    # 选择成交价（平多用bid；平空用ask）并按最小跳动对齐
                    bid = (
                        getattr(tick, 'bid_price_1', None)
                        or getattr(tick, 'bid_price1', None)
                        or getattr(tick, 'bid_price', None)
                        or current_price
                    )
                    ask = (
                        getattr(tick, 'ask_price_1', None)
                        or getattr(tick, 'ask_price1', None)
                        or getattr(tick, 'ask_price', None)
                        or current_price
                    )
                    tick_size = PlatformAdapter.get_pricetick(symbol) or 0
                    def _round(p):
                        if tick_size and tick_size > 0:
                            try:
                                return round(p / tick_size) * tick_size
                            except Exception:
                                return p
                        return p
                    def _align(p, side):
                        if not tick_size or tick_size <= 0:
                            return p
                        try:
                            steps = p / tick_size
                            if side in ('sell',):
                                return math.floor(steps) * tick_size
                            else:  # cover
                                return math.ceil(steps) * tick_size
                        except Exception:
                            return p

                    # 逐档检查触发
                    side = plan.get('side', 'long')
                    executed = plan.get('executed') or [False] * len(plan.get('targets', []))
                    qtys = plan.get('tranche_qtys') or []
                    for i, tgt in enumerate(plan.get('targets', [])):
                        if i >= len(executed) or i >= len(qtys):
                            break
                        if executed[i]:
                            continue
                        vol_i = int(qtys[i] or 0)
                        if vol_i <= 0:
                            executed[i] = True
                            continue
                        try:
                            try:
                                cur_abs = abs(int(local_get_pos(context, symbol, state)))
                            except Exception:
                                cur_abs = abs(int(cur_pos))
                        except Exception:
                            cur_abs = abs(position_volume)
                        if cur_abs <= 0:
                            break
                        if vol_i > cur_abs:
                            vol_i = cur_abs
                        if side == 'long' and current_price >= tgt:
                            px = _align(bid, 'sell')
                            try:
                                sell(symbol, px, vol_i)
                                Log(f"[{symbol}] 分批止盈：平多 {vol_i}手 @ {px:.2f}，触发 {plan['levels_r'][i]:.2f}R，target={tgt:.2f}")
                                executed[i] = True
                                state['scale_out_plan']['executed'] = executed
                            except Exception as e:
                                Log(f"[{symbol}] 分批止盈下单失败(平多): {e}")
                        elif side == 'short' and current_price <= tgt:
                            px = _align(ask, 'cover')
                            try:
                                cover(symbol, px, vol_i)
                                Log(f"[{symbol}] 分批止盈：平空 {vol_i}手 @ {px:.2f}，触发 {plan['levels_r'][i]:.2f}R，target={tgt:.2f}")
                                executed[i] = True
                                state['scale_out_plan']['executed'] = executed
                            except Exception as e:
                                Log(f"[{symbol}] 分批止盈下单失败(平空): {e}")
            except Exception:
                pass


# ========================================
# 策略主函数
# ========================================

def on_init(context):
    """策略初始化"""
    # Heartbeat at very beginning
    try:
        print('[INIT] entering on_init')
    except Exception:
        pass
    try:
        # 兼容潜在的变量名拼写(contex)问题
        contex = context
        context.symbols = list(Config.SYMBOLS) if hasattr(Config, 'SYMBOLS') else [Config.SYMBOL]
        Log(f"========== AI自主交易策略启动 ==========")
        Log(f"交易品种: {', '.join(context.symbols)}")
        Log(f"AI决策间隔: {Config.AI_DECISION_INTERVAL}秒")
        Log(f"安全边界: 单笔最大亏损{Config.MAX_SINGLE_TRADE_LOSS_PCT*100:.1f}%, 单日最大亏损{Config.MAX_DAILY_LOSS_PCT*100:.1f}%")

        # 在 on_init 即发起数据订阅，确保平台能建立订阅流
        for sym in context.symbols:
            # 明确订阅tick流（有的平台需要单独订阅tick）
            try:
                subscribe(sym)
                Log(f"[{sym}] 已订阅tick流")
            except Exception:
                # 某些平台subscribe(sym)可能不需要/不支持；忽略异常
                pass
            subscribe(sym, '1m', Config.KLINE_1M_WINDOW)
            subscribe(sym, '1d', Config.KLINE_1D_WINDOW)

        # 初始化组件
        context.ai_engine = AIDecisionEngine()
        context.executor = TradeExecutor()
        context.risk_controller = RiskController()
        # 初始化多Key轮询池（每Key并发=1）
        try:
            keys = []
            try:
                arr = list(getattr(Config, 'DEEPSEEK_API_KEYS', []) or [])
                for k in arr:
                    if isinstance(k, str) and k.strip():
                        keys.append(k.strip())
            except Exception:
                pass
            primary = getattr(Config, 'DEEPSEEK_API_KEY', '')
            if isinstance(primary, str) and primary.strip():
                if primary.strip() not in keys:
                    keys.insert(0, primary.strip())
            context.key_pool = APIKeyPool(keys)
            Log(f"[AI] DeepSeek Key池已就绪: {context.key_pool.size()} 个Key")
        except Exception:
            context.key_pool = APIKeyPool([getattr(Config, 'DEEPSEEK_API_KEY', '')])
        # 订单累计成交量跟踪
        try:
            context.order_traded_map = {}
        except Exception:
            context.order_traded_map = {}

        # 载入热参数（context.params 可覆盖默认）
        # 移除动态参数加载，改为使用 Config 中的固定常量

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
                'last_indicators': None,
                'last_tick': None,
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
                'scale_out_plan': None,
                'ai_in_flight': False,
                'pending_decision': None,
                'pending_seq': 0,
                'ai_job_seq': 0,
                'last_consumed_seq': 0,
                # 为错峰触发设置固定相位偏移（0~AI_STAGGER_MAX_SECS）
                'stagger_offset': float(getattr(Config, 'AI_STAGGER_MAX_SECS', 7) or 0) * random.random(),
            }
        # 兼容旧字段（不再使用，保留以避免引用错误）
        context.ai_decision = None
        context.last_ai_call_time = 0
        context.entry_time = None
        context.trading_allowed = True

        # 初始化资金：支持热参数（默认200万）
        # 初始资金改为固定常量
        try:
            context.initial_cash = float(Config.INITIAL_CASH)
        except Exception:
            context.initial_cash = 2000000

        # 兼容旧字段（单标的模式），多标的时用 context.state[sym]['position_avg_price']
        context.position_avg_price = 0

        context.daily_pnl = 0
        context.daily_trades = 0
        context.daily_wins = 0

        # 最近成交记录（按标的）
        try:
            from collections import deque as _dq
            context.trades_by_symbol = {}
            for sym in context.symbols:
                context.trades_by_symbol[sym] = _dq(maxlen=50)
        except Exception:
            context.trades_by_symbol = {sym: [] for sym in context.symbols}

        # 读取 _G 轻持久化的持仓快照（均价/已实盈亏/追踪参数）（可选）
        for sym in context.symbols:
            if getattr(Config, 'USE_PERSISTENT_SNAPSHOT', False):
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
            if getattr(Config, 'USE_PERSISTENT_SNAPSHOT', False):
                try:
                    intr = _G(f"intraday:{sym}")
                    if isinstance(intr, dict):
                        context.state[sym]['intraday'].update(intr)
                except Exception:
                    pass

        # Tail heartbeat
        try:
            print('[INIT] on_init finished')
            Log('[INIT] 初始化完成，已订阅行情')
        except Exception:
            pass
    except Exception as e:
        # Any unexpected error during init — log via both channels
        try:
            print('[INIT-ERROR]', repr(e))
        except Exception:
            pass
        try:
            Log(f'[INIT-ERROR] {e}')
        except Exception:
            pass
        # 不抛出，让平台继续展示日志
        return


def on_start(context):
    # 策略启动后的回调 - 在on_init之后,数据订阅完成后执行
    # 兼容潜在的变量名拼写(contex)问题
    contex = context
    try:
        print('[START] entering on_start')
    except Exception:
        pass
    Log("策略启动完成,开始主动加载历史数据...")

    # 主动回填历史数据，确保启动即有足够的300根1分钟K线
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
            bar1m = None
        try:
            bar1d = get_current_bar(sym, '1d')
            if bar1d:
                Log(f"[{sym}] [提示] 当前1d最新时间: {bar1d.datetime.strftime('%Y-%m-%d')}")
        except Exception:
            bar1d = None
        # 夜盘交易日提示（rollover 后视为次日交易日）
        try:
            roll_h = int(Config.TRADING_DAY_ROLLOVER_HOUR)
        except Exception:
            roll_h = 21
        try:
            def _next_trading_date(base_dt):
                d = base_dt.date()
                if base_dt.time() >= datetime_time(roll_h, 0, 0):
                    d = d + timedelta(days=1)
                while d.weekday() >= 5:
                    d = d + timedelta(days=1)
                return d
            now_dt = datetime.now()
            td = _next_trading_date(now_dt)
            bar_day = bar1d.datetime.strftime('%Y-%m-%d') if bar1d else 'N/A'
            Log(f"[{sym}] [提示] 当前交易日(roll@{roll_h}): {td.isoformat()} (bar日:{bar_day})")
        except Exception:
            pass

        # 启动后立即尝试计算一次指标，验证数据是否充足
        indicators = dc.calculate_indicators()
        if indicators:
            Log(f"[{sym}] ✅ 技术指标计算成功, EMA20={indicators['ema_20']:.2f}, RSI={indicators['rsi']:.2f}")
            try:
                st = context.state[sym]
                st['last_indicators'] = indicators
                # 如已先收到tick，则直接构建一次快照，减少等待on_bar
                last_tick0 = st.get('last_tick')
                if last_tick0 is not None and st.get('last_market_data') is None:
                    try:
                        md0 = collect_market_data(context, sym, last_tick0, indicators, dc, st)
                        st['last_market_data'] = md0
                        Log(f"[{sym}] [提示] on_start 已构建首次快照(last_market_data)")
                        # 自适应参数节拍
                        adaptive0 = derive_adaptive_defaults(md0, None)
                        st['adaptive'] = adaptive0
                        st['ai_interval_secs'] = adaptive0.get('ai_interval_secs', Config.AI_DECISION_INTERVAL)
                    except Exception:
                        pass
            except Exception:
                pass
            # 若已具备快照且未在冷却中，且tick触发被关闭，可在启动时立即触发一次AI后台任务
            md_ready = isinstance(st.get('last_market_data'), dict)
            allow_tick_ai = bool(getattr(Config, 'ENABLE_TICK_TRIGGERED_AI', False))
            cooldown_until = st.get('cooldown_until') or 0
            in_cd = cooldown_until and (time.time() < float(cooldown_until))
            if getattr(Config, 'ENABLE_STARTUP_AI_TRIGGER', False):
                if md_ready and not allow_tick_ai and not in_cd and not st.get('ai_in_flight') and context.trading_allowed:
                    try:
                        if _spawn_ai_job(context, sym):
                            st['last_ai_call_time'] = time.time()
                            Log(f"[{sym}] 启动阶段已触发AI后台任务")
                    except Exception:
                        pass
        else:
            Log(f"[{sym}] ⏳ 数据暂不充足, 将在运行中继续累积...")

    Log("🚀 多标的策略已就绪, 等待首次AI决策...")


def on_tick(context, tick):
    """Tick级别回调 - 核心交易逻辑（轻量化，重活已移至 on_bar/后台线程）"""
    # 兼容潜在的变量名拼写(contex)问题
    contex = context
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
    # 保存最近tick供 on_bar/AI 使用
    try:
        state['last_tick'] = tick
    except Exception:
        pass
    # 若指标已就绪但快照未就绪，立即用现有指标+最新tick构建一次快照（减少对on_bar依赖）
    try:
        if state.get('last_market_data') is None and isinstance(state.get('last_indicators'), dict):
            md0 = collect_market_data(context, sym, tick, state['last_indicators'], dc, state)
            state['last_market_data'] = md0
            try:
                Log(f"[{sym}] on_tick 构建快照完成(last_market_data)")
            except Exception:
                pass
            # 同步节拍参数
            try:
                adaptive0 = derive_adaptive_defaults(md0, None)
                state['adaptive'] = adaptive0
                state['ai_interval_secs'] = adaptive0.get('ai_interval_secs', Config.AI_DECISION_INTERVAL)
            except Exception:
                pass
    except Exception:
        pass
    # 更新最新价（用于本地估算账户）- 直接在估算函数里使用 tick.last_price，无需保存

    # 更新本交易日的日内统计（开/高/低）-- 按方案A去掉大范围try，改用局部防御
    ts = getattr(tick, 'strtime', None)
    dt = None
    if ts:
        try:
            dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
        except Exception:
            dt = datetime.now()
    else:
        cand = getattr(tick, 'datetime', None)
        dt = cand if isinstance(cand, datetime) else datetime.now()

    # 交易日映射：可配置 rollover 小时（默认21点）
    try:
        roll_hour = int(Config.TRADING_DAY_ROLLOVER_HOUR)
    except Exception:
        roll_hour = 21
    try:
        def _next_trading_date(base_dt):
            d = base_dt.date()
            if base_dt.time() >= datetime_time(roll_hour, 0, 0):
                d = d + timedelta(days=1)
            while d.weekday() >= 5:
                d = d + timedelta(days=1)
            return d
        trading_day = _next_trading_date(dt).toordinal()
    except Exception:
        trading_day = (datetime.now().date()).toordinal()

    td_key = trading_day
    intr = state.get('intraday', {}) or {}
    cur_px = getattr(tick, 'last_price', getattr(tick, 'price', 0))

    if intr.get('trading_day') != td_key:
        # 新交易日初始化
        try:
            open_time_str = ts or dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            open_time_str = ts or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        intr = {
            'trading_day': td_key,
            'open': cur_px,
            'high': cur_px,
            'low': cur_px,
            'open_time': open_time_str,
            'prev_close': None,
            'source': 'intraday',
        }
        # 设置昨日收盘（若有）
        if getattr(dc, 'kline_1d_buffer', None):
            try:
                last_d = dc.kline_1d_buffer[-1]
                intr['prev_close'] = last_d['close']
            except Exception:
                pass
        state['intraday'] = intr
        # 新交易日重置交易允许状态（避免前一日风控触发后一直不交易）
        try:
            context.trading_allowed = True
            Log(f"[{sym}] 新交易日开始，重置 trading_allowed=True")
        except Exception:
            pass
    else:
        # 更新高低
        if cur_px is not None:
            try:
                if intr.get('high') is None or cur_px > intr['high']:
                    intr['high'] = cur_px
                if intr.get('low') is None or cur_px < intr['low']:
                    intr['low'] = cur_px
            except Exception:
                pass
        state['intraday'] = intr

    # 周期性持久化日内统计（每60秒一次）（可选）
    try:
        if getattr(Config, 'USE_PERSISTENT_SNAPSHOT', False):
            last_persist = state.get('last_intraday_persist_ts') or 0
            persist_iv = int(Config.INTRADAY_PERSIST_INTERVAL_SECS)
            if (time.time() - float(last_persist)) > persist_iv:
                _G(f"intraday:{sym}", state.get('intraday'))
                state['last_intraday_persist_ts'] = time.time()
    except Exception:
        pass

    # 热参数可能变更，定期重载
    # 已移除热参数重载

    # 检查是否应该调用AI（改为：提交后台任务，不在主线程阻塞）
    current_timestamp = time.time()
    time_since_last_call = current_timestamp - state['last_ai_call_time']
    ai_interval = state.get('ai_interval_secs', Config.AI_DECISION_INTERVAL)
    stagger = float(state.get('stagger_offset') or 0.0)
    # 冷却窗口（如有）
    cooldown_until = state.get('cooldown_until') or 0
    in_cooldown = False
    try:
        in_cooldown = cooldown_until and (current_timestamp < float(cooldown_until))
    except Exception:
        in_cooldown = False
    # 所需最小ticks
    try:
        ticks_min = int(getattr(Config, 'TICKS_MIN_FOR_AI', 5))
    except Exception:
        ticks_min = 5
    # 仅在on_tick触发开关打开、且1m K线数量达标时才考虑tick触发AI
    min_bars_ok = (len(dc.kline_1m_buffer) >= int(getattr(Config, 'MIN_1M_BARS_FOR_AI', 10)))
    allow_tick_ai = bool(getattr(Config, 'ENABLE_TICK_TRIGGERED_AI', False))
    should_call_ai = (
        allow_tick_ai
        and min_bars_ok
        and time_since_last_call >= (ai_interval + stagger)
        and len(dc.tick_buffer) >= ticks_min
        and context.trading_allowed
        and isinstance(state.get('last_market_data'), dict)
        and not in_cooldown
    )

    if should_call_ai and not state.get('ai_in_flight'):
        started = _spawn_ai_job(context, sym)
        if started:
            try:
                Log(f"[{sym}] 已提交AI后台任务")
            except Exception:
                pass
        state['last_ai_call_time'] = current_timestamp
    else:
        # 轻量调试：每隔约10秒输出一次不触发原因。
        # 若关闭了 tick 触发，则避免误导性的 tick 不足提示，改为提示等待 on_bar。
        last_log_t = getattr(context, 'last_noai_log_t', 0) or 0
        try:
            need_log = (current_timestamp - float(last_log_t)) > 10
        except Exception:
            need_log = True
        if need_log:
            if not allow_tick_ai:
                Log(f"[{sym}] 未触发AI: 已关闭tick触发，等待on_bar节拍")
            else:
                reason = []
                if time_since_last_call < (ai_interval + stagger):
                    reason.append("间隔未到")
                if len(dc.tick_buffer) < ticks_min:
                    reason.append(f"tick不足:{len(dc.tick_buffer)}/{ticks_min}")
                if not context.trading_allowed:
                    reason.append("交易未允许")
                if not isinstance(state.get('last_market_data'), dict):
                    reason.append("快照未就绪(last_market_data)")
                if state.get('ai_in_flight'):
                    reason.append("AI在途")
                if in_cooldown:
                    try:
                        remain = int(float(cooldown_until) - current_timestamp)
                    except Exception:
                        remain = -1
                    if remain >= 0:
                        reason.append(f"冷却中:{remain}s")
                    else:
                        reason.append("冷却中")
                if reason:
                    Log("[%s] 未触发AI: %s" % (sym, ",".join(reason)))
            context.last_noai_log_t = current_timestamp

    # 若后台AI任务已有结果，立即执行（不阻塞主线程）
    try:
        pending = state.get('pending_decision')
        pend_seq = int(state.get('pending_seq') or 0)
        last_seq = int(state.get('last_consumed_seq') or 0)
        if isinstance(pending, dict) and pend_seq > last_seq:
            state['last_consumed_seq'] = pend_seq
            try:
                Log(f"[{sym}] AI决策: {pending.get('signal')}, 市场状态: {pending.get('market_state')}")
                reason_full = str(pending.get('reasoning', 'N/A'))
                Log(f"[{sym}] AI分析: {reason_full[:200]}...")
                if getattr(Config, 'LOG_FULL_AI_REASONING', False):
                    Log(f"[{sym}] AI分析全文: {reason_full}")
                if getattr(Config, 'LOG_FULL_AI_JSON', False):
                    Log(f"[{sym}] AI决策JSON: {json.dumps(pending, ensure_ascii=False)}")
            except Exception:
                pass
            try:
                context.executor.execute_decision(context, sym, pending, tick, state)
            except Exception as e:
                try:
                    Log(f"[{sym}] [错误] 执行器异常: {e}")
                except Exception:
                    pass
            finally:
                state['pending_decision'] = None
        elif isinstance(pending, dict) and pend_seq <= last_seq:
            state['pending_decision'] = None
    except Exception:
        pass

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
    """K线回调 - 刷新K线/指标/市场快照（移出on_tick，避免阻塞）。"""
    try:
        items = []
        if isinstance(bars, dict):
            # 多标的一批：{symbol: bar}
            items = list(bars.items())
        elif isinstance(bars, (list, tuple)):
            # 平台回调 bars 可能是 list：同频多根/多标的聚合
            tmp = []
            for b in bars:
                sym_b = getattr(b, 'vt_symbol', None) or getattr(b, 'symbol', None)
                if sym_b:
                    tmp.append((sym_b, b))
            items = tmp
        else:
            # 单bar
            sym = getattr(bars, 'symbol', None) or getattr(bars, 'vt_symbol', None)
            if sym:
                items = [(sym, bars)]
        for sym, _bar in items:
            # 兼容平台可能传入的不同标识（如无交易所后缀/大小写差异）
            state_map = getattr(context, 'state', {})
            if sym not in state_map:
                su = str(sym).upper()
                resolved = None
                for k in state_map.keys():
                    ku = k.upper()
                    base = k.split('.')[0].upper()
                    if su == ku or su == base or su in ku or base in su:
                        resolved = k
                        break
                if resolved:
                    sym = resolved
                else:
                    # 找不到匹配，跳过
                    continue
            st = state_map[sym]
            dc = st['data_collector']
            # 刷新K线
            dc.update_klines(sym)
            # 计算指标
            ind = dc.calculate_indicators()
            if ind:
                st['last_indicators'] = ind
                # 构建市场快照（需要最近tick）
                last_tick = st.get('last_tick')
                if last_tick is not None:
                    try:
                        md = collect_market_data(context, sym, last_tick, ind, dc, st)
                        st['last_market_data'] = md
                        # 自适应参数
                        adaptive = derive_adaptive_defaults(md, None)
                        st['adaptive'] = adaptive
                        st['ai_interval_secs'] = adaptive.get('ai_interval_secs', Config.AI_DECISION_INTERVAL)
                        # on_bar就绪后，直接根据节拍/冷却触发一次AI（避免依赖tick阈值）
                        try:
                            now_ts = time.time()
                            last_call = float(st.get('last_ai_call_time') or 0)
                            ai_iv = float(st.get('ai_interval_secs') or Config.AI_DECISION_INTERVAL)
                            stag = float(st.get('stagger_offset') or 0.0)
                            cooldown_until = st.get('cooldown_until') or 0
                            in_cd = False
                            try:
                                in_cd = cooldown_until and (now_ts < float(cooldown_until))
                            except Exception:
                                in_cd = False
                            # 仅当1m K线至少 MIN_1M_BARS_FOR_AI 根时在bar上触发AI
                            min_bars_ok = (len(dc.kline_1m_buffer) >= int(getattr(Config, 'MIN_1M_BARS_FOR_AI', 10)))
                            if min_bars_ok and (now_ts - last_call) >= (ai_iv + stag) and not st.get('ai_in_flight') and not in_cd and context.trading_allowed:
                                if _spawn_ai_job(context, sym):
                                    st['last_ai_call_time'] = now_ts
                                    Log(f"[{sym}] 已在on_bar触发AI后台任务")
                        except Exception:
                            pass
                    except Exception:
                        pass
    except Exception:
        pass


def on_order_status(context, order):
    """订单状态回调（仅日志，不更新本地持仓/均价，避免与 on_trade/on_order 重复）。"""
    # 兼容潜在的变量名拼写(contex)问题
    contex = context
    # 根据Gkoudai文档, order.status为中文字符串, 如"全部成交"
    if order.status == "全部成交":
        try:
            Log(f"订单成交: {order.direction} {order.offset} {order.volume}手 @ {order.price:.2f}")
        except Exception:
            pass
        # 解析并记录符号，后续冷却/记录可用
        try:
            sym_raw = getattr(order, 'symbol', None)
            sym = sym_raw
            state_map = getattr(context, 'state', {})
            if sym and sym not in state_map:
                su = str(sym).upper(); resolved = None
                for k in state_map.keys():
                    ku = k.upper(); base = k.split('.')[0].upper()
                    if su == ku or su == base or su in ku or base in su:
                        resolved = k; break
                if resolved:
                    sym = resolved
            Log(f"[{sym_raw}] on_order_status: raw_symbol={sym_raw}→resolved={sym}, dir={getattr(order,'direction','')}, offset={getattr(order,'offset','')}, vol={getattr(order,'volume',0)}")
        except Exception:
            pass
        _delta_realized = 0.0

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
                    # 同步 scale-out 初始手数（若存在计划）
                    try:
                        plan = st.get('scale_out_plan')
                        if isinstance(plan, dict) and plan.get('init_volume') in (None, 0):
                            try:
                                plan['init_volume'] = abs(int(local_get_pos(context, sym, st)))
                            except Exception:
                                pass
                            base = int(plan['init_volume'] or 0)
                            if base > 0 and plan.get('pcts'):
                                raw = [max(0, int(base * p)) for p in plan['pcts']]
                                if sum(raw) == 0:
                                    raw[-1] = 1
                                if sum(raw) < base:
                                    raw[-1] += (base - sum(raw))
                                plan['tranche_qtys'] = raw
                            st['scale_out_plan'] = plan
                    except Exception:
                        pass
                else:
                    # 平仓后设置反手/再入场冷却，降低抖动
                    try:
                        pause = float(getattr(Config, 'REENTRY_COOLDOWN_SECS', 120))
                    except Exception:
                        pause = 120.0
                    try:
                        st['reentry_until'] = time.time() + pause
                        Log(f"[{sym}] 平仓后设置再入场冷却 {int(pause)} 秒")
                    except Exception:
                        pass
        except Exception:
            pass

        # 记录最近交易（便于Prompt展示与诊断）
        try:
            sym = getattr(order, 'symbol', None)
            if sym:
                side = str(getattr(order, 'direction', ''))
                off = str(getattr(order, 'offset', ''))
                vol = int(getattr(order, 'volume', 0) or 0)
                px = float(getattr(order, 'price', 0.0) or 0.0)
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                if off in ("开", "open", "OPEN"):
                    _su = side.upper()
                    tag = 'OpenLong' if ("买" in side or side.lower().startswith('buy') or ('LONG' in _su)) else 'OpenShort'
                else:
                    tag = 'CloseLong' if ("卖" in side or side.lower().startswith('sell')) else 'CloseShort'
                # realized_delta 此处不易精确获知，置0；成交明细由 on_trade 记录更准确的 realized_delta
                rec = {
                    'time': ts,
                    'tag': tag,
                    'volume': vol,
                    'price': px,
                    'realized_delta': 0.0,
                    'plan_id': context.state.get(sym, {}).get('current_plan_id') if hasattr(context, 'state') else None
                }
                try:
                    context.trades_by_symbol[sym].append(rec)
                except Exception:
                    # fallback: simple list
                    if sym not in context.trades_by_symbol:
                        context.trades_by_symbol[sym] = []
                    context.trades_by_symbol[sym].append(rec)
        except Exception:
            pass

    
def on_order(context, order):
    """订单状态回调（兼容 on_order）。若不启用 on_order 填充，则直接返回。"""
    if not getattr(Config, 'USE_ON_ORDER_FOR_FILLS', False):
        return
    try:
        status_raw = str(getattr(order, 'status', '') or '')
        status = status_raw.upper()
        sym = getattr(order, 'symbol', None)
        if not sym:
            return
        # 归一化 symbol
        try:
            state_map = getattr(context, 'state', {})
            if sym not in state_map:
                su = str(sym).upper()
                resolved = None
                for k in state_map.keys():
                    ku = k.upper(); base = k.split('.')[0].upper()
                    if su == ku or su == base or su in ku or base in su:
                        resolved = k; break
                if resolved:
                    sym = resolved
        except Exception:
            pass

        meaningful = status in ('PARTTRADED', 'ALLTRADED', 'PART TRADED', 'ALL TRADED', '成交', '部分成交', '全部成交')
        if not meaningful:
            return

        orderid = getattr(order, 'orderid', None)
        traded_total = None
        for name in ('traded', 'traded_volume', 'filled', 'filled_volume', 'volume_traded'):
            try:
                v = getattr(order, name)
                if v is not None:
                    traded_total = float(v)
                    break
            except Exception:
                continue
        if traded_total is None:
            try:
                if status.startswith('ALL'):
                    traded_total = float(getattr(order, 'volume', 0) or 0)
            except Exception:
                traded_total = 0.0

        last = 0.0
        try:
            last = float(context.order_traded_map.get(orderid, 0.0) or 0.0)
        except Exception:
            last = 0.0
        delta = max(0.0, float(traded_total or 0.0) - last)
        if delta <= 0:
            return

        dir_raw = str(getattr(order, 'direction', '') or '')
        _du = dir_raw.upper()
        is_buy = ('买' in dir_raw) or _du.startswith('BUY') or ('LONG' in _du)
        off_raw = str(getattr(order, 'offset', '') or '')

        st = context.state.get(sym, {})
        lp = int(st.get('local_pos') or 0)
        lp_before = lp
        try:
            lp = lp + int(delta) if is_buy else lp - int(delta)
        except Exception:
            lp = lp + (1 if is_buy else -1) * int(delta)
        st['local_pos'] = lp
        context.state[sym] = st
        context.order_traded_map[orderid] = float(traded_total or 0.0)

        try:
            px = float(getattr(order, 'price', 0.0) or 0)
        except Exception:
            px = 0.0
        try:
            update_pos_snapshot_on_fill(context, sym,
                direction=getattr(order, 'direction', ''),
                offset=getattr(order, 'offset', ''),
                price=px,
                volume=int(delta))
        except Exception:
            pass
        try:
            Log(f"[{getattr(order,'symbol',sym)}] on_order: status={status_raw}, raw_symbol={getattr(order,'symbol',None)}→resolved={sym}, dir={dir_raw}, offset={off_raw}, delta={int(delta)}, traded_total={traded_total}, local_pos: {lp_before}→{lp}")
        except Exception:
            pass
    except Exception:
        pass


def on_trade(context, trade):
    try:
        sym = getattr(trade, 'symbol', None)
        if not sym:
            return
        try:
            state_map = getattr(context, 'state', {})
            if sym not in state_map:
                su = str(sym).upper()
                resolved = None
                for k in state_map.keys():
                    ku = k.upper(); base = k.split('.')[0].upper()
                    if su == ku or su == base or su in ku or base in su:
                        resolved = k; break
                if resolved:
                    sym = resolved
        except Exception:
            pass
        dir_raw = str(getattr(trade, 'direction', '') or '')
        _du = dir_raw.upper()
        is_buy = ('买' in dir_raw) or _du.startswith('BUY') or ('LONG' in _du)
        off_raw = str(getattr(trade, 'offset', '') or '')
        vol = int(getattr(trade, 'volume', 0) or 0)
        px = float(getattr(trade, 'price', 0.0) or 0)
        orderid = getattr(trade, 'orderid', None)

        st = context.state.get(sym, {})
        lp = int(st.get('local_pos') or 0)
        lp_before = lp
        # 用于计算本笔成交带来的已实现变化
        try:
            realized_before = float(st.get('realized_pnl') or 0.0)
        except Exception:
            realized_before = 0.0
        if vol:
            lp = lp + vol if is_buy else lp - vol
            st['local_pos'] = lp
            context.state[sym] = st
            try:
                Log(f"[{getattr(trade,'symbol',sym)}] on_trade: raw_symbol={getattr(trade,'symbol',None)}→resolved={sym}, dir={dir_raw}, offset={off_raw}, price={px}, vol={vol}, local_pos: {lp_before}→{lp}")
            except Exception:
                pass
        try:
            update_pos_snapshot_on_fill(context, sym,
                direction=getattr(trade, 'direction', ''),
                offset=getattr(trade, 'offset', ''),
                price=px,
                volume=vol)
        except Exception:
            pass
        # 记录滑点（若有 last_order_price 且为新近下单）
        try:
            lo_px = float(st.get('last_order_price')) if st.get('last_order_price') is not None else None
            lo_ts = float(st.get('last_order_ts')) if st.get('last_order_ts') is not None else None
            tk = PlatformAdapter.get_pricetick(sym) or 0.0
            if lo_px is not None and lo_ts is not None and (time.time() - lo_ts) < 15:
                slip = float(px) - float(lo_px)
                ticks = (slip / tk) if (tk and tk > 0) else 0.0
                Log(f"[{sym}] 成交滑点: {slip:+.2f} ({ticks:+.1f} tick) vs 下单价{lo_px:.2f}")
        except Exception:
            pass
        # 成交后立即输出一次账户快照（估算）
        try:
            acc = estimate_account(context, sym, px, st)
            Log(f"[{sym}] 成交后 snapshot(估算): equity={acc['equity']:.0f}, available={acc['available']:.0f}, margin={acc['margin']:.0f}")
        except Exception:
            pass
        try:
            if orderid is not None:
                last = float(context.order_traded_map.get(orderid, 0.0) or 0.0)
                context.order_traded_map[orderid] = last + float(vol)
        except Exception:
            pass
        # 记录最近交易（含 plan_id 与本次已实现变动）
        try:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            _su = dir_raw.upper()
            tag = 'OpenLong' if (off_raw in ('开','open','OPEN') and ("买" in dir_raw or _su.startswith('BUY') or ('LONG' in _su))) \
                  else 'OpenShort' if (off_raw in ('开','open','OPEN')) \
                  else ('CloseLong' if ("卖" in dir_raw or _su.startswith('SELL')) else 'CloseShort')
            # realized delta after update
            st2 = context.state.get(sym, st)
            try:
                realized_after = float(st2.get('realized_pnl') or 0.0)
            except Exception:
                realized_after = realized_before
            rec = {
                'time': ts,
                'tag': tag,
                'volume': vol,
                'price': px,
                'realized_delta': float(realized_after - realized_before),
                'plan_id': st2.get('current_plan_id')
            }
            try:
                context.trades_by_symbol[sym].append(rec)
            except Exception:
                if sym not in context.trades_by_symbol:
                    context.trades_by_symbol[sym] = []
                context.trades_by_symbol[sym].append(rec)
        except Exception:
            pass
        # 完全平仓时，记录 last_exit_price/last_exit_time，便于再入场间距判定
        try:
            if int(context.state.get(sym, {}).get('local_pos') or 0) == 0:
                context.state[sym]['last_exit_price'] = float(px)
                context.state[sym]['last_exit_time'] = time.time()
        except Exception:
            pass
    except Exception:
        pass
def on_backtest_finished(context, indicator):
    """回测结束回调"""
    # 兼容潜在的变量名拼写(contex)问题
    contex = context
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

def format_recent_trades(context, symbol, n=3):
    """格式化最近n笔成交，简洁摘要用于Prompt。
    输出示例：
    14:35 OpenLong 2@553.2 | 14:58 CloseLong 1@556.0 PnL:+280
    """
    try:
        arr = list(context.trades_by_symbol.get(symbol, []))
    except Exception:
        arr = []
    if not arr:
        return "(无)"
    last = arr[-n:]
    out = []
    for tr in last:
        ts = str(tr.get('time', ''))
        hhmm = ts[11:16] if len(ts) >= 16 else ts
        tag = tr.get('tag', '')
        vol = tr.get('volume', 0)
        px = tr.get('price', 0)
        pnl = tr.get('realized_delta', 0.0)
        suf = "" if abs(float(pnl)) < 1e-6 else (f" PnL:{pnl:+.0f}")
        out.append(f"{hhmm} {tag} {vol}@{px:.2f}{suf}")
    return " | ".join(out)

# -------- 参数管理（热更新） --------
# 动态参数默认集合已移除，统一在 Config 中以常量写死

# 已移除动态参数系统（get_current_params/load_params/maybe_reload_params）

def derive_adaptive_defaults(market_data, _ignored=None):
    """根据日线趋势返回自适应参数（固定常量版）。
    直接使用 Config.ADAPTIVE_PARAMS，而不再从外部动态参数读取。
    """
    trend = str(market_data.get('d_trend') or 'SIDEWAYS').upper()
    param_map = Config.ADAPTIVE_PARAMS
    base = param_map.get(trend, param_map.get('SIDEWAYS', {})).copy()
    # 流动性很差则降仓
    try:
        if market_data.get('liquidity_state') == 'THIN':
            base['position_size_pct'] = min(float(base.get('position_size_pct', 0.6)), 0.3)
            base['cooldown_minutes'] = max(1, int(base.get('cooldown_minutes', 1)))
    except Exception:
        pass
    return base

def estimate_account(context, symbol, last_price, state):
    """基于 get_pos + _G 轻持久化的均价/已实盈亏，估算账户权益/可用/占用保证金。
    用于在平台无账户接口时，给AI/风控提供足够准确的视角。
    """
    try:
        pos = int(local_get_pos(context, symbol, state))
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

    try:
        init_cash = float(getattr(context, 'initial_cash', Config.INITIAL_CASH))
    except Exception:
        init_cash = float(getattr(context, 'initial_cash', 0.0))
    equity = init_cash + realized + float_pnl
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
    # 使用本地持仓（在 on_order_status 中已先行维护），退化到平台
    try:
        st0 = context.state.get(symbol, {}) if hasattr(context, 'state') else {}
        pos_after = int(local_get_pos(context, symbol, st0))
    except Exception:
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

    # 持久化到 _G（可选）
    try:
        if getattr(Config, 'USE_PERSISTENT_SNAPSHOT', False):
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
        if not hasattr(PlatformAdapter, '_MR_SOURCE'):
            PlatformAdapter._MR_SOURCE = {}
        c = PlatformAdapter.get_contract(symbol)
        source = 'default'
        v = None
        # 常见命名：long_margin_ratio/short_margin_ratio 或 *_rate 或统一 margin_ratio/margin_rate
        if c is not None:
            if direction == 'long':
                v = _safe_get(c, 'long_margin_ratio', 'long_margin_rate', 'margin_ratio', 'margin_rate')
            else:
                v = _safe_get(c, 'short_margin_ratio', 'short_margin_rate', 'margin_ratio', 'margin_rate')
            try:
                if v is not None:
                    val = float(v)
                    source = 'platform'
                    PlatformAdapter._MR_SOURCE[(symbol, direction)] = source
                    return val
            except Exception:
                pass
        if direction == 'long':
            val = float(Config.DEFAULT_MARGIN_RATIO_LONG.get(symbol, 0.1))
        else:
            val = float(Config.DEFAULT_MARGIN_RATIO_SHORT.get(symbol, 0.1))
        PlatformAdapter._MR_SOURCE[(symbol, direction)] = source
        return val

    @staticmethod
    def get_margin_ratio_source(symbol, direction='long'):
        try:
            return getattr(PlatformAdapter, '_MR_SOURCE', {}).get((symbol, direction), 'unknown')
        except Exception:
            return 'unknown'

    

 

def collect_market_data(context, symbol, tick, indicators, data_collector, state):
    """收集完整的市场数据用于AI决策"""

    # 持仓信息（优先运行期本地值；退化到平台）
    position_volume = local_get_pos(context, symbol, state)

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
    try:
        init_cash = float(getattr(context, 'initial_cash', Config.INITIAL_CASH))
    except Exception:
        init_cash = float(getattr(context, 'initial_cash', 0.0))
    base_equity = acc['equity'] if acc['equity'] > 0 else max(1.0, init_cash)
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
    thin_th = float(Config.LIQUIDITY_SCORE_THIN)
    thick_th = float(Config.LIQUIDITY_SCORE_THICK)
    if liquidity_score < thin_th:
        liquidity_state = 'THIN'
    elif liquidity_score > thick_th:
        liquidity_state = 'THICK'
    else:
        liquidity_state = 'NORMAL'

    # 规范化部分指标，避免 None 在字符串格式化时出错
    inds = indicators or {}
    def _coerce(k, default=0.0):
        try:
            v = inds.get(k, default)
            return float(v) if v is not None else float(default)
        except Exception:
            return float(default)

    ema_20_s = _coerce('ema_20', 0.0)
    ema_60_s = _coerce('ema_60', 0.0)
    macd_s = _coerce('macd', 0.0)
    macd_signal_s = _coerce('macd_signal', 0.0)
    macd_hist_s = _coerce('macd_hist', 0.0)
    rsi_s = _coerce('rsi', 0.0)
    atr_s = _coerce('atr', 0.0)
    high_20_s = _coerce('high_20', current_price)
    low_20_s = _coerce('low_20', current_price)
    pr_pct_s = _coerce('price_range_pct', 0.0)

    # 数值化以防 None 导致格式化报错
    def _nf(v, d=0.0):
        try:
            return float(v)
        except Exception:
            return float(d)
    bid_price = _nf(bid_price, current_price)
    ask_price = _nf(ask_price, current_price)

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
        'tick_size': (PlatformAdapter.get_pricetick(symbol) or 0.01),
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
        'prev_close': intr.get('prev_close'),
        'today_open_source': intr.get('source', 'intraday'),
        'recent_trades_summary': format_recent_trades(context, symbol, n=3),
        'daily_pnl': context.daily_pnl,
        'daily_pnl_pct': daily_pnl_pct,
        'daily_trades': context.daily_trades,
        'daily_win_rate': daily_win_rate,
        'contract_multiplier': PlatformAdapter.get_contract_size(symbol),
        'initial_cash': float(getattr(context, 'initial_cash', Config.INITIAL_CASH)),
        'd_ema_20': inds.get('d_ema_20', 0) if inds.get('d_ema_20') is not None else 0,
        'd_ema_60': inds.get('d_ema_60', 0) if inds.get('d_ema_60') is not None else 0,
        'd_macd': inds.get('d_macd', 0) if inds.get('d_macd') is not None else 0,
        'd_trend': inds.get('d_trend', 'N/A') if inds.get('d_trend') is not None else 'N/A',
        'zigzag_summary': inds.get('zigzag_summary', 'N/A') if inds.get('zigzag_summary') is not None else 'N/A',
        'fib_summary': inds.get('fib_summary', 'N/A') if inds.get('fib_summary') is not None else 'N/A',
        **inds,
        'ema_20': ema_20_s,
        'ema_60': ema_60_s,
        'macd': macd_s,
        'macd_signal': macd_signal_s,
        'macd_hist': macd_hist_s,
        'rsi': rsi_s,
        'atr': atr_s,
        'high_20': high_20_s,
        'low_20': low_20_s,
        'price_range_pct': pr_pct_s,
    }

    # 合并订单流摘要（简版）：基于近tick窗口的delta
    try:
        of_win = list(getattr(data_collector, '_of_delta_win', []))
        if of_win:
            import statistics as _stat
            last20 = of_win[-20:] if len(of_win) >= 20 else of_win
            market_data['of_delta_20'] = float(sum(last20))
            last5 = of_win[-5:] if len(of_win) >= 5 else of_win
            last10 = of_win[-10:] if len(of_win) >= 10 else of_win
            market_data['of_ma5'] = float(sum(last5)) / max(1, len(last5))
            market_data['of_ma10'] = float(sum(last10)) / max(1, len(last10))
            base50 = of_win[-50:] if len(of_win) >= 50 else of_win
            mu = float(sum(base50)) / max(1, len(base50))
            try:
                sigma = float(_stat.pstdev(base50)) if len(base50) > 1 else 0.0
            except Exception:
                sigma = 0.0
            last = of_win[-1]
            market_data['of_z50'] = ((last - mu) / sigma) if sigma and sigma > 0 else 0.0
        else:
            market_data['of_delta_20'] = 0.0
            market_data['of_ma5'] = 0.0
            market_data['of_ma10'] = 0.0
            market_data['of_z50'] = 0.0
    except Exception:
        market_data['of_delta_20'] = 0.0
        market_data['of_ma5'] = 0.0
        market_data['of_ma10'] = 0.0
        market_data['of_z50'] = 0.0

    return market_data



# ====== Kimi overlay (single-file) ======
try:
    import requests
except Exception:
    requests = None

class KimiConfig:
    BASE_URL = "https://api.moonshot.cn/v1/chat/completions"
    MODEL = "kimi-k2-turbo-preview"
    TEMPERATURE = getattr(Config, 'DEEPSEEK_TEMPERATURE', 0.7)
    MAX_TOKENS = getattr(Config, 'DEEPSEEK_MAX_TOKENS', 2000)
    API_TIMEOUT = getattr(Config, 'API_TIMEOUT', 30)
    API_MAX_RETRIES = getattr(Config, 'API_MAX_RETRIES', 3)
    API_KEYS = [
        "sk-Bn613HW6cWJQFdv7wIEmP0GjlNJdgloJUL34AmvYFqBuL6EF",
        "sk-CkIHuWsLYsKE1QOPKuRW0qPmD2BhrYmI2avLDwpau4MT35hY",
    ]

class AIDecisionEngineKimi:
    @staticmethod
    def call_deepseek_api(prompt: str, api_key: str = None):
        if requests is None:
            return None, "requests 未安装，跳过Kimi调用"
        key = api_key or (KimiConfig.API_KEYS[0] if KimiConfig.API_KEYS else "")
        if not key:
            return None, "未配置 Kimi API Key"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {key}',
        }
        payload = {
            'model': KimiConfig.MODEL,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT_WAVE_FIRST},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': KimiConfig.TEMPERATURE,
            'max_tokens': KimiConfig.MAX_TOKENS,
        }
        def _extract_json(txt: str) -> str:
            t = (txt or '').strip()
            # 1) 代码块优先
            if '```json' in t:
                try:
                    return t.split('```json', 1)[1].split('```', 1)[0].strip()
                except Exception:
                    pass
            if '```' in t:
                try:
                    return t.split('```', 1)[1].split('```', 1)[0].strip()
                except Exception:
                    pass
            # 2) 平衡大括号扫描（提取第一段完整 JSON 对象）
            i = t.find('{')
            if i != -1:
                depth = 0
                for j, ch in enumerate(t[i:], start=i):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            return t[i:j+1]
            return t

        def _clean_json(s: str) -> str:
            try:
                import re
                s2 = re.sub(r",\s*([}\]])", r"\\1", s)  # 去掉尾逗号
                s2 = s2.replace('None', 'null').replace('True', 'true').replace('False', 'false')
                return s2
            except Exception:
                return s

        for attempt in range(int(KimiConfig.API_MAX_RETRIES)):
            try:
                resp = requests.post(
                    KimiConfig.BASE_URL,
                    headers=headers,
                    json=payload,
                    timeout=KimiConfig.API_TIMEOUT,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    content = result['choices'][0]['message']['content']
                    raw = _extract_json(content)
                    try:
                        return json.loads(raw), None
                    except Exception:
                        try:
                            return json.loads(_clean_json(raw)), None
                        except Exception as e:
                            return None, f"Kimi返回解析失败: {e}"
                else:
                    err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if attempt < int(KimiConfig.API_MAX_RETRIES) - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return None, err
            except Exception as e:
                if attempt < int(KimiConfig.API_MAX_RETRIES) - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"Kimi调用异常: {e}"
        return None, "Kimi调用失败，已达最大重试次数"

_old_on_init = on_init

def on_init(context):
    _old_on_init(context)
    try:
        context.key_pool = APIKeyPool(KimiConfig.API_KEYS)
        context.ai_engine = AIDecisionEngineKimi()
        Log(f"[AI] Kimi引擎就绪: {context.key_pool.size()} 个Key, model={KimiConfig.MODEL}")
    except Exception as e:
        try:
            Log(f"[AI] Kimi初始化异常: {e}")
        except Exception:
            pass
