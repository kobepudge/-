# -*- coding: utf-8 -*-
"""
AI Trading Firm — Chief Wave Strategist (DeepSeek) — Pure Version

不兼容旧模式；仅按 1.md 的“AI交易公司”架构实现：
- CEO 制定 TRADER_RULEBOOK 与熔断；
- Python 助理在 on_bar 准备情报简报（订单流 + 波浪结构 + 上下文 + 账户）；
- AI 首席（DeepSeek）基于合同式 System Prompt 输出唯一 trade_plan；
- 执行器严格风控 + 下单。
"""

import json
import time
import math
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import os

try:
    import requests
except Exception:
    requests = None


# ================= CEO：交易员手册（公司章程） =================
TRADER_RULEBOOK = {
    "core_methodology": "Elliott Wave validated by Order Flow Delta",
    "max_position_pct": 0.6,
    "mandatory_stop_loss": True,
    "min_reward_risk_ratio": 1.5,
    "no_overnight": "14:55:00",
    "max_daily_loss_pct": 0.05,
}


# ================= DeepSeek 统一配置（集中管理） =================
class Config:
    # 基本参数（与旧主策略保持一致，便于统一管理）
    DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
    DEEPSEEK_MODEL = "deepseek-chat"
    DEEPSEEK_TEMPERATURE = 0.7
    DEEPSEEK_MAX_TOKENS = 2000
    API_TIMEOUT = 30
    API_MAX_RETRIES = 3

    # Key（建议用环境变量注入；这里提供占位/备用）
    # 如果使用环境变量，优先读取：DEEPSEEK_API_KEYS（逗号分隔）或 DEEPSEEK_API_KEY
    DEEPSEEK_API_KEY = "sk-44e096b16c2a4f0ea364368583ea097d"
    DEEPSEEK_API_KEYS = [
        "sk-44e096b16c2a4f0ea364368583ea097d",
        "sk-c7c94df2cbbb423698cb895f25534501",
        "sk-c2f94e4ed7f54018916aec6494677dd8",
    ]

    # 交易标的（默认）
    SYMBOL = "au2512.SHFE"
    SYMBOLS = [
        "au2512.SHFE",
        "lc2601.GFEX",
        "ag2512.SHFE",
    ]
    # 合约乘数（用于估算手数）
    CONTRACT_MULTIPLIER = {
        "au2512.SHFE": 1000,  # 1000克/手
        "lc2601.GFEX": 5,     # 5吨/手（示例值）
    }

    # 执行与风控护栏
    SINGLE_SIDE_MODE = True
    ALLOW_SAME_SIDE_PYRAMIDING = True
    REENTRY_COOLDOWN_SECS = 120
    MIN_REENTRY_GAP_TICKS = 0            # 与上次入/离场价的最小价差（tick）
    MIN_STOP_TICKS = 5                   # 止损至少 N tick
    MIN_STOP_ATR_MULT = 0.25             # 或 ATR×倍数
    NEW_TRADE_MARGIN_BUFFER = 1.12       # 新单保证金缓冲倍数
    MIN_GUARANTEE_RATIO = 1.15           # 最低担保比（权益/占用保证金）
    MIN_AI_CONFIDENCE = 0.0              # AI 信心度阈值（0 表示不启用）
    SPREAD_RATIO_LIMIT = 0.003           # 价差相对中价比例上限，超过则降仓

    # 自适应参数（按日线趋势）
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
        },
    }
    MIN_1M_BARS_FOR_AI = 10
    LIQUIDITY_SCORE_THIN = 0.8
    LIQUIDITY_SCORE_THICK = 1.2
    # 5分钟级别ZigZag阈值（相对1m更高）
    ZIGZAG_THRESHOLD_PCT_5M = 0.6

    # 交易时段与强平时间（白盘/夜盘）
    FORCE_CLOSE_TIME_DAY = "14:55:00"
    FORCE_CLOSE_TIME_NIGHT = "02:25:00"
    TRADING_DAY_ROLLOVER_HOUR = 21  # >=21点视为夜盘，会跨日

    # 是否信任平台 get_pos；False 时仅使用运行期本地持仓
    USE_PLATFORM_GET_POS = False
    # 执行前诊断日志开关
    DEBUG_EXEC_CHECK = True
    # 诊断日志：是否开启周期性状态日志，以及间隔（秒）
    DEBUG_VERBOSE = True
    DEBUG_STATUS_INTERVAL_SECS = 60
    # 行情订阅策略：'none' | 'init' | 'start' | 'deferred'
    SUBSCRIBE_MODE = 'init'
    SUBSCRIBE_INTERVALS = (
        ('tick', None),
        ('1m', 720),
        ('1d', 50),
    )


# ================= 平台适配（最小依赖） =================
class PlatformAdapter:
    @staticmethod
    def get_contract(symbol):
        for fn_name in [
            'get_contract', 'contract', 'get_instrument', 'get_contract_data'
        ]:
            fn = globals().get(fn_name)
            if callable(fn):
                try:
                    c = fn(symbol)
                    if c is not None:
                        return c
                except Exception:
                    pass
        return None

    @staticmethod
    def get_contract_size(symbol):
        c = PlatformAdapter.get_contract(symbol)
        sz = None
        if c is not None:
            for k in ('size', 'contract_size', 'multiplier'):
                try:
                    v = getattr(c, k)
                except Exception:
                    v = None
                if v is None and isinstance(c, dict):
                    v = c.get(k)
                if v is not None:
                    try:
                        sz = float(v); break
                    except Exception:
                        pass
        if sz:
            return sz
        # fallback to configured multipliers
        try:
            m = Config.CONTRACT_MULTIPLIER.get(symbol)
            if m:
                return float(m)
        except Exception:
            pass
        return 1000.0

    @staticmethod
    def get_pricetick(symbol):
        c = PlatformAdapter.get_contract(symbol)
        if c is not None:
            for k in ('pricetick', 'price_tick', 'tick_size'):
                v = getattr(c, k, None) if not isinstance(c, dict) else c.get(k)
                if v:
                    try:
                        return float(v)
                    except Exception:
                        pass
        return 0.01

    @staticmethod
    def get_margin_ratio(symbol, direction='long'):
        # 若平台未提供，统一用 0.1 兜底，并记录来源
        if not hasattr(PlatformAdapter, '_MR_SOURCE'):
            PlatformAdapter._MR_SOURCE = {}
        src = 'default'
        c = PlatformAdapter.get_contract(symbol)
        if c is not None:
            keys = ['margin_ratio', 'margin_rate']
            if direction == 'long':
                keys = ['long_margin_ratio', 'long_margin_rate'] + keys
            else:
                keys = ['short_margin_ratio', 'short_margin_rate'] + keys
            for k in keys:
                v = getattr(c, k, None) if not isinstance(c, dict) else c.get(k)
                if v is not None:
                    try:
                        val = float(v)
                        PlatformAdapter._MR_SOURCE[(symbol, direction)] = 'platform'
                        return val
                    except Exception:
                        pass
        PlatformAdapter._MR_SOURCE[(symbol, direction)] = src
        return 0.1

    @staticmethod
    def get_margin_ratio_source(symbol, direction='long'):
        try:
            return getattr(PlatformAdapter, '_MR_SOURCE', {}).get((symbol, direction), 'unknown')
        except Exception:
            return 'unknown'


# ================= 账户估算/仓位快照 =================
def local_get_pos(context, symbol, state=None):
    """
    优先使用运行期维护的本地持仓数量；可按配置退化到平台 get_pos(symbol)。
    避免与平台同名函数冲突（不再定义 get_pos(context, symbol)）。
    """
    # 运行期本地快照
    try:
        if isinstance(state, dict) and ('local_pos' in state):
            return int(state.get('local_pos') or 0)
        # 兼容旧字段（顶层）
        if hasattr(context, 'state') and isinstance(context.state, dict):
            st = context.state.get(symbol)
            if isinstance(st, dict) and ('local_pos' in st):
                return int(st.get('local_pos') or 0)
    except Exception:
        pass
    # 按配置退化到平台 get_pos(symbol)
    try:
        if getattr(Config, 'USE_PLATFORM_GET_POS', False):
            fn = globals().get('get_pos')
            if callable(fn):
                return int(fn(symbol))
    except Exception:
        pass
    return 0


def estimate_account(context, price, state):
    try:
        symbol = state.get('symbol')
    except Exception:
        symbol = None
    pos = local_get_pos(context, symbol, state) if symbol else int(state.get('local_pos', 0))
    avg = float(state.get('avg_price') or 0)
    realized = float(state.get('realized_pnl') or 0)
    mult = PlatformAdapter.get_contract_size(symbol) if symbol else 1000.0
    if pos > 0:
        float_pnl = (price - avg) * abs(pos) * mult
    elif pos < 0:
        float_pnl = (avg - price) * abs(pos) * mult
    else:
        float_pnl = 0.0
    init_cash = float(getattr(context, 'initial_cash', 200_000.0))
    equity = init_cash + realized + float_pnl
    # 粗略保证金：|pos|*price*mult*mr
    mr = PlatformAdapter.get_margin_ratio(symbol, 'long') if pos >= 0 else PlatformAdapter.get_margin_ratio(symbol, 'short')
    used_margin = abs(pos) * price * mult * max(0.01, mr)
    available = max(0.0, equity - used_margin)
    return {
        'equity': float(equity),
        'available': float(available),
        'margin': float(used_margin),
    }


# ================= 助理：结构/订单流计算 =================
def _zigzag_by_pct_hilo(klines_1m, threshold_pct=0.3):
    """ZigZag by percent using true highs/lows.
    - Uses bar highs to extend up legs and lows to extend down legs
    - Pivots recorded at extremes (H/L) with bar index relative to input
    """
    if not klines_1m or len(klines_1m) < 10:
        return []
    th = abs(float(threshold_pct)) / 100.0
    pivots = []
    # seed with first bar mid
    f = klines_1m[0]
    last_pivot_price = (float(f['high']) + float(f['low'])) / 2.0
    direction = 0   # 1 up, -1 down, 0 unknown
    extreme_price = float(f['high'])
    extreme_idx = 0
    for i, b in enumerate(klines_1m[1:], start=1):
        hi = float(b['high']); lo = float(b['low'])
        if direction >= 0:
            # extend up leg with highs
            if hi > extreme_price:
                extreme_price = hi; extreme_idx = i
            # drawdown from extreme high to current low
            dd = (extreme_price - lo) / extreme_price if extreme_price > 0 else 0.0
            if direction == 1 and dd >= th:
                pivots.append({"idx": extreme_idx, "price": extreme_price, "type": "H"})
                direction = -1
                last_pivot_price = extreme_price
                extreme_price = lo; extreme_idx = i
            elif direction == 0:
                up_move = (hi - last_pivot_price) / last_pivot_price if last_pivot_price > 0 else 0.0
                if up_move >= th:
                    direction = 1
                    extreme_price = hi; extreme_idx = i
        if direction <= 0:
            # extend down leg with lows
            if lo < extreme_price:
                extreme_price = lo; extreme_idx = i
            du = (hi - extreme_price) / abs(extreme_price) if extreme_price != 0 else 0.0
            if direction == -1 and du >= th:
                pivots.append({"idx": extreme_idx, "price": extreme_price, "type": "L"})
                direction = 1
                last_pivot_price = extreme_price
                extreme_price = hi; extreme_idx = i
            elif direction == 0:
                down_move = (last_pivot_price - lo) / last_pivot_price if last_pivot_price > 0 else 0.0
                if down_move >= th:
                    direction = -1
                    extreme_price = lo; extreme_idx = i
    return pivots


def calculate_zigzag_pivots(context):
    """ZigZag pivots on 1m using true highs/lows with percent threshold."""
    bars = list(getattr(context, 'klines_1m', []) or [])
    th = float(getattr(context, 'zigzag_threshold_pct', 0.3))
    return _zigzag_by_pct_hilo(bars, threshold_pct=th)


def find_fib_clusters(pivots, tick_size=0.01, bin_ticks=3, min_votes=2):
    # 简化：对最近的 swing 计算 retracement/extension 候选，落入直方图聚类
    if not pivots or len(pivots) < 3:
        return {"support": [], "resistance": []}
    prices = [p['price'] for p in pivots]
    candidates = []
    # 用最近3-6段 swing
    swings = []
    for i in range(1, len(prices)):
        swings.append((prices[i-1], prices[i]))
    swings = swings[-6:]
    fib_r = [0.382, 0.5, 0.618]
    fib_e = [1.272, 1.618]
    for a, b in swings:
        leg = b - a
        if abs(leg) < 1e-6:
            continue
        # retracements 回到 a->b 的比例位置
        for r in fib_r:
            x = b - r * leg
            candidates.append(x)
        # extensions 从 b 再延伸
        for e in fib_e:
            x = b + e * leg
            candidates.append(x)
    # 直方图聚类
    buckets = {}
    width = max(tick_size * bin_ticks, 1e-6)
    for x in candidates:
        key = round(x / width)
        buckets[key] = buckets.get(key, []) + [x]
    centers = [sum(v)/len(v) for v in buckets.values() if len(v) >= min_votes]
    # 简单分簇：以最近收盘分割支持/压力
    last_close = context.klines_1m[-1]['close'] if context.klines_1m else 0.0
    support = sorted([c for c in centers if c <= last_close])
    resist = sorted([c for c in centers if c > last_close])
    return {"support": support, "resistance": resist}


def _structure_complexity(context, pivots=None, lookback_bars=20):
    """改进版结构复杂度：
    - 枢轴密度（基于Hi/Lo ZigZag）
    - 有幅度门槛的方向翻转计数（基于1m中价序列）
    - 趋势减权（5m/1d EMA20/60 一致时下调）
    输出区间约 0-10
    """
    try:
        bars = getattr(context, 'klines_1m', [])
        seq = bars[-lookback_bars:]
        if not seq or len(seq) < 5:
            return 0.0
        if pivots is None:
            pivots = calculate_zigzag_pivots(context)
        # 1) 枢轴密度分数（近12个）
        piv_ct = len(pivots[-12:]) if pivots else 0
        piv_score = min(6.0, (piv_ct / 12.0) * 6.0)
        # 2) 有幅度门槛的翻转计数
        mids = [ (float(b['high']) + float(b['low']))/2.0 for b in seq ]
        tick = float(PlatformAdapter.get_pricetick(getattr(context, 'symbol', None))) or 0.01
        # 近20/30的ATR作为动态门槛的一部分
        highs = [float(b['high']) for b in seq]; lows = [float(b['low']) for b in seq]; closes = [float(b['close']) for b in seq]
        atr_local = _atr(highs, lows, closes, period=min(14, max(5, len(seq)//2))) or 0.0
        min_amp = max(3.0 * tick, 0.1 * float(atr_local))
        flips = 0
        ref = mids[0]
        last_dir = 0
        for m in mids[1:]:
            ch = m - ref
            if abs(ch) >= min_amp:
                cur_dir = 1 if ch > 0 else -1
                if last_dir != 0 and cur_dir != last_dir:
                    flips += 1
                last_dir = cur_dir
                ref = m
        flip_score = min(4.0, (flips / 8.0) * 4.0)  # 约8次翻转即4分上限
        raw = piv_score + flip_score
        # 3) 趋势减权：5m与1d EMA20>EMA60 同向时，最多减2分
        try:
            k5 = _aggregate_to_5min_anchored(bars)
            close5 = [b['close'] for b in k5]
            ema20_5 = _ema(close5[-60:], 20) if len(close5) >= 20 else _ema(close5, max(1, len(close5)))
            ema60_5 = _ema(close5, 60) if len(close5) >= 60 else _ema(close5, max(1, len(close5)))
            sign5 = 1 if ema20_5 > ema60_5 else (-1 if ema20_5 < ema60_5 else 0)
        except Exception:
            sign5 = 0
        try:
            k1d = context.state.get('klines_1d', []) if isinstance(context.state, dict) else []
            closes1d = [float(b.get('close', 0.0) or 0.0) for b in k1d]
            ema20_d = _ema(closes1d[-60:], 20) if len(closes1d) >= 20 else _ema(closes1d, max(1, len(closes1d)))
            ema60_d = _ema(closes1d, 60) if len(closes1d) >= 60 else _ema(closes1d, max(1, len(closes1d)))
            signd = 1 if ema20_d > ema60_d else (-1 if ema20_d < ema60_d else 0)
        except Exception:
            signd = 0
        red = 0.0
        if sign5 != 0 and signd != 0 and sign5 == signd:
            red = 2.0
        elif (sign5 != 0) or (signd != 0):
            red = 1.0
        scx = max(0.0, round(raw - red, 2))
        return scx
    except Exception:
        return 0.0

def _last_leg_direction(pivots):
    """Return 1 for up, -1 for down, 0 unknown using last two pivots prices."""
    try:
        if not pivots or len(pivots) < 2:
            return 0
        a = float(pivots[-2]['price']); b = float(pivots[-1]['price'])
        return 1 if b > a else (-1 if b < a else 0)
    except Exception:
        return 0

def _htf_validation(context, pivots):
    """Compute higher timeframe agreement relative to last 1m leg.
    tf5m/tf1d = support|neutral|oppose
    """
    leg = _last_leg_direction(pivots)
    # 5m trend sign via EMA20/60
    try:
        k5 = _aggregate_to_5min_anchored(getattr(context, 'klines_1m', []))
        c5 = [b['close'] for b in k5]
        if len(c5) >= 3:
            e20 = _ema(c5[-60:], 20) if len(c5) >= 20 else _ema(c5, max(1, len(c5)))
            e60 = _ema(c5, 60) if len(c5) >= 60 else _ema(c5, max(1, len(c5)))
            s5 = 1 if e20 > e60 else (-1 if e20 < e60 else 0)
        else:
            s5 = 0
    except Exception:
        s5 = 0
    # 1d trend sign via EMA20/60
    try:
        k1d = context.state.get('klines_1d', []) if isinstance(context.state, dict) else []
        cd = [float(b.get('close', 0.0) or 0.0) for b in k1d]
        if len(cd) >= 3:
            e20d = _ema(cd[-60:], 20) if len(cd) >= 20 else _ema(cd, max(1, len(cd)))
            e60d = _ema(cd, 60) if len(cd) >= 60 else _ema(cd, max(1, len(cd)))
            sd = 1 if e20d > e60d else (-1 if e20d < e60d else 0)
        else:
            sd = 0
    except Exception:
        sd = 0
    def _mks(v):
        if leg == 0 or v == 0:
            return 'neutral'
        return 'support' if v == leg else 'oppose'
    return { 'tf5m': _mks(s5), 'tf1d': _mks(sd) }


def _aggregate_to_5min(kline_1m):
    """将1分钟序列按5根聚合为近似5分钟K线。按序列切片，不强依赖自然时间对齐。"""
    if not kline_1m or len(kline_1m) < 5:
        return []
    out = []
    # 每5根聚合一次，忽略尾部不足5根
    for i in range(0, len(kline_1m) - 4, 5):
        seg = kline_1m[i:i+5]
        out.append({
            'open': seg[0]['open'],
            'high': max(b['high'] for b in seg),
            'low': min(b['low'] for b in seg),
            'close': seg[-1]['close'],
            'volume': sum(b.get('volume', 0.0) or 0.0 for b in seg),
        })
    return out


def _aggregate_to_5min_anchored(kline_1m):
    """按时间锚定的5分钟聚合，使用每根1m的 ts 字段。
    若缺少 ts 字段则回退到序列切片版。
    """
    if not kline_1m or len(kline_1m) < 5:
        return []
    try:
        first_has_ts = isinstance(kline_1m[0].get('ts'), datetime)
    except Exception:
        first_has_ts = False
    if not first_has_ts:
        return _aggregate_to_5min(kline_1m)
    buckets = []
    cur = None
    for b in kline_1m:
        ts = b.get('ts')
        if not isinstance(ts, datetime):
            return _aggregate_to_5min(kline_1m)
        base = ts.replace(minute=(ts.minute - ts.minute % 5), second=0, microsecond=0)
        if (not cur) or (cur['ts'] != base):
            cur = {'ts': base, 'open': b['open'], 'high': b['high'], 'low': b['low'], 'close': b['close'], 'volume': b.get('volume', 0.0) or 0.0}
            buckets.append(cur)
        else:
            cur['high'] = max(cur['high'], b['high'])
            cur['low'] = min(cur['low'], b['low'])
            cur['close'] = b['close']
            cur['volume'] = (cur.get('volume', 0.0) or 0.0) + (b.get('volume', 0.0) or 0.0)
    return buckets


def _calc_daily_trend(kline_1d):
    """基于日线序列粗略判别趋势：EMA20 vs EMA60。返回 'UPTREND'/'DOWNTREND'/'SIDEWAYS'。"""
    try:
        closes = [float(b.get('close', 0.0) or 0.0) for b in (kline_1d or [])]
        if not closes:
            return 'N/A'
        ema20 = _ema(closes[-60:], 20) if len(closes) >= 20 else _ema(closes, min(20, len(closes)))
        ema60 = _ema(closes, 60) if len(closes) >= 60 else _ema(closes, len(closes))
        if ema20 > ema60:
            return 'UPTREND'
        if ema20 < ema60:
            return 'DOWNTREND'
        return 'SIDEWAYS'
    except Exception:
        return 'N/A'


def calc_vwap(context):
    # 会话 VWAP（从当日第一根开始）
    if not context.klines_1m:
        return 0.0
    num = 0.0; den = 0.0
    for b in context.klines_1m:
        tp = (b['high'] + b['low'] + b['close']) / 3.0
        v = max(0.0, float(b.get('volume', 0) or 0))
        num += tp * v; den += v
    return (num/den) if den > 0 else context.klines_1m[-1]['close']


def get_pos_detail(context):
    try:
        symbol = context.symbol
    except Exception:
        symbol = None
    if not symbol:
        return {"pos": 0, "avg": 0}
    # 使用本地持仓视图
    try:
        st = context.state if isinstance(context.state, dict) else {}
        st_sym = st.get(symbol) if isinstance(st, dict) else {}
    except Exception:
        st_sym = {}
    pos = local_get_pos(context, symbol, st_sym)
    avg = float(context.state.get('avg_price') or 0)
    return {"pos": pos, "avg": avg}


def get_traders_view(context, bar):
    last_dom = context.dom_bars[-1] if hasattr(context, 'dom_bars') and context.dom_bars else {}
    # 订单流滑窗统计（用bar级delta近似）
    try:
        deltas = [d.get('delta', 0.0) for d in (context.dom_bars[-50:] if getattr(context, 'dom_bars', None) else [])]
        last20 = deltas[-20:] if deltas else []
        last5 = deltas[-5:] if deltas else []
        last10 = deltas[-10:] if deltas else []
        import statistics as _stat
        mu = (sum(deltas)/len(deltas)) if deltas else 0.0
        sigma = (_stat.pstdev(deltas) if deltas and len(deltas) > 1 else 0.0)
        sigma = max(float(sigma), 1e-6)  # 避免除0
        z50 = ((deltas[-1] - mu)/sigma) if deltas else 0.0
        of_features = {
            "of_delta_20": float(sum(last20)) if last20 else 0.0,
            "delta_ma5": (sum(last5)/len(last5)) if last5 else 0.0,
            "delta_ma10": (sum(last10)/len(last10)) if last10 else 0.0,
            "delta_z50": float(z50),
        }
    except Exception:
        of_features = {"of_delta_20": 0.0, "delta_ma5": 0.0, "delta_ma10": 0.0, "delta_z50": 0.0}
    order_flow_report = {
        "bar_delta": last_dom.get("delta", 0),
        "bar_volume_profile": last_dom.get("price_levels", []),
        **of_features,
        "bar_delta_note": "Delta = 主动买 - 主动卖 (近似)"
    }
    pivots = calculate_zigzag_pivots(context)
    fib_clusters = find_fib_clusters(pivots, tick_size=PlatformAdapter.get_pricetick(context.symbol))
    scx = _structure_complexity(context, pivots=pivots, lookback_bars=20)
    # 5m 级别ZigZag（基于1m聚合，时间锚定）
    try:
        k5 = _aggregate_to_5min_anchored(context.klines_1m)
        th5 = float(getattr(Config, 'ZIGZAG_THRESHOLD_PCT_5M', 0.6))
        piv_5m = _zigzag_by_pct_hilo(k5, threshold_pct=th5) if k5 else []
    except Exception:
        piv_5m = []
    htf = _htf_validation(context, pivots)
    structure_report = {
        "zigzag_pivots_recent": pivots[-10:],
        "zigzag_pivots_5m": piv_5m[-6:],
        "fib_support_clusters": fib_clusters.get("support", []),
        "fib_resistance_clusters": fib_clusters.get("resistance", []),
        "structure_complexity": scx,
        "higher_timeframe_validation": htf,
        "note": "枢轴=高低点；structure_complexity为参考提示（0-10 越大越混乱）"
    }
    last3 = context.klines_1m[-3:] if context.klines_1m else []
    context_report = {
        "last_3_candles_ohlc": [[b['open'], b['high'], b['low'], b['close']] for b in last3],
        "session_vwap": calc_vwap(context)
    }
    # 日线趋势传递
    try:
        k1d = context.state.get('klines_1d', []) if isinstance(context.state, dict) else []
        context_report['daily_trend'] = _calc_daily_trend(k1d)
    except Exception:
        context_report['daily_trend'] = 'N/A'
    acc = estimate_account(context, bar['close'], context.state)
    acc["current_position"] = get_pos_detail(context)
    briefing = {
        "report_order_flow": order_flow_report,
        "report_structure": structure_report,
        "report_context": context_report,
        "report_account": acc
    }
    return json.dumps(briefing, ensure_ascii=False)


# ================= AI首席（DeepSeek） =================
SYSTEM_PROMPT_WAVE_CHIEF = (
    "你是我基金的首席波浪策略师 (Chief Elliott Wave Strategist)。\n"
    "你的唯一方法论是波浪理论，唯一的验证工具是订单流(Delta)。你的唯一目标是盈利。\n\n"
    "# 你的工作流程\n"
    "你每个K线周期会收到一份来自‘量化工程团队’(Python助理)的JSON情报简报：\n"
    "1. report_order_flow: 当前Bar的订单流Delta和成交剖面；\n"
    "2. report_structure: 市场结构骨架(ZigZag枢轴点)和关键斐波支撑/阻力集群；\n"
    "3. report_context: 最近K线形态和VWAP；\n"
    "4. report_account: 当前持仓与账户状态。\n\n"
    "你的工作是：\n"
    "- 解读：用波浪理论解读 structure 中的枢轴形成主要/备选计数；\n"
    "- 验证：用 order_flow 的Delta验证推动/修正；\n"
    "- 决策：基于主要计数，输出唯一、可执行的 trade_plan；\n"
    "- 记录：在 internal_reasoning 完整记录波浪分析与Delta验证过程。\n\n"
    "# 你的底线 (公司制度)\n"
    "你必须严格遵守 TRADER_RULEBOOK：\n{RULEBOOK_JSON}\n\n"
    "# 你的输出（仅一个JSON对象）\n"
    "必须严格返回一个JSON对象，字段为：hypothesis_primary, hypothesis_alternate, internal_reasoning, signal, trade_plan。\n"
    "- signal 必须为小写枚举：buy|sell|hold|close，不得返回 SELL/Buy 等变体。\n"
    "- trade_plan 必须包含 entry_price, position_size_pct, stop_loss, profit_target_1, plan_id。\n"
    "- 强制要求：当 signal 为 buy 或 sell 时，position_size_pct 必须在 (0, {max_pos}] 区间，禁止返回0或缺省。\n"
    "\n# 你的自律与责任\n"
    "- 对每次决策，在 internal_reasoning 写出：\n"
    "  1) 若亏损最可能的原因是什么？\n"
    "  2) 出现什么具体信号会提前认输（如跌破某结构、Delta反转、关键位被突破）？\n"
    "\n# 结构复杂度（structure_complexity）\n"
    "- 简报可能包含 structure_complexity（0-10，越大越混乱）。\n"
    "- 这是提示而非硬规则：请结合多周期直觉自行权衡，不要机械使用阈值。\n"
    .replace("{max_pos}", str(TRADER_RULEBOOK['max_position_pct']))
)


class DeepSeekChiefEngine:
    def __init__(self, api_keys):
        self.api_keys = list(api_keys or [])

    def call(self, briefing_json: str, rulebook: dict):
        if requests is None:
            return None, "requests 未安装"
        key = self.api_keys[0] if self.api_keys else ""
        if not key:
            return None, "未配置 DeepSeek API Key"
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'}
        sys_prompt = SYSTEM_PROMPT_WAVE_CHIEF.replace("{RULEBOOK_JSON}", json.dumps(rulebook, ensure_ascii=False))
        payload = {
            'model': Config.DEEPSEEK_MODEL,
            'messages': [
                {'role': 'system', 'content': sys_prompt},
                {'role': 'user', 'content': briefing_json},
            ],
            'temperature': Config.DEEPSEEK_TEMPERATURE,
            'max_tokens': Config.DEEPSEEK_MAX_TOKENS,
        }
        for attempt in range(int(Config.API_MAX_RETRIES)):
            try:
                r = requests.post(Config.DEEPSEEK_API_URL, headers=headers, json=payload, timeout=Config.API_TIMEOUT)
                if r.status_code == 200:
                    data = r.json()
                    content = data['choices'][0]['message']['content']
                    # 提取JSON
                    def _extract(s):
                        t = s.strip()
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
                        i = t.find('{')
                        if i >= 0:
                            depth = 0
                            for j in range(i, len(t)):
                                ch = t[j]
                                if ch == '{': depth += 1
                                elif ch == '}':
                                    depth -= 1
                                    if depth == 0:
                                        return t[i:j+1]
                        return t
                    raw = _extract(content)
                    try:
                        return json.loads(raw), None
                    except Exception:
                        s = raw.replace('None', 'null').replace('True', 'true').replace('False', 'false')
                        try:
                            return json.loads(s), None
                        except Exception as e:
                            return None, f"解析失败: {e}"
                else:
                    err = f"HTTP {r.status_code}: {r.text[:200]}"
                    if attempt < int(Config.API_MAX_RETRIES) - 1:
                        time.sleep(2 ** attempt); continue
                    return None, err
            except Exception as e:
                if attempt < int(Config.API_MAX_RETRIES) - 1:
                    time.sleep(2 ** attempt); continue
                return None, f"请求异常: {e}"
        return None, "调用失败"


# ================== 工具与指标 ==================
def _ema(vals, n):
    if not vals:
        return 0.0
    k = 2.0 / (n + 1)
    ema = vals[0]
    for v in vals[1:]:
        ema = v * k + ema * (1 - k)
    return float(ema)


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i-1]
        gains.append(max(0.0, ch))
        losses.append(max(0.0, -ch))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(highs)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        trs.append(max(hl, hc, lc))
    return sum(trs[-period:]) / period


def _normalize_price(p, tick_size, fallback):
    try:
        pf = float(p)
        if not math.isfinite(pf):
            pf = float(fallback)
    except Exception:
        pf = float(fallback)
    tk = float(tick_size) if tick_size and tick_size > 0 else 0.01
    try:
        q = Decimal(str(pf)).quantize(Decimal(str(tk)), rounding=ROUND_HALF_UP)
        return float(q)
    except Exception:
        try:
            return float(fallback)
        except Exception:
            return 0.0


def _align_price(p, side, tick_size):
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


# ================= 风控熔断 =================
def is_ai_insane(context, ai_decision, current_price, state):
    try:
        signal = str((ai_decision or {}).get('signal') or '').strip().lower()
        plan = (ai_decision or {}).get('trade_plan', {})
        size_pct = float(plan.get('position_size_pct', 0) or 0)
        # 仅对开仓信号校验仓位比例与止损
        if signal in ('buy', 'sell'):
            if size_pct <= 0 or size_pct > float(TRADER_RULEBOOK['max_position_pct']):
                Log(f"[熔断] 仓位比例非法: {size_pct} signal={signal} plan={plan}"); return True
            stop_loss = plan.get('stop_loss')
            if TRADER_RULEBOOK['mandatory_stop_loss'] and stop_loss in (None, ""):
                Log("[熔断] 未提供止损"); return True
            try:
                sl = float(stop_loss)
            except Exception:
                Log("[熔断] 止损格式无效"); return True
            if signal == 'buy' and sl >= current_price:
                Log(f"[熔断] 多单止损({sl:.2f})>=现价({current_price:.2f})"); return True
            if signal == 'sell' and sl <= current_price:
                Log(f"[熔断] 空单止损({sl:.2f})<=现价({current_price:.2f})"); return True
            tgt = plan.get('profit_target_1')
            if tgt is not None:
                try:
                    rr = abs(float(tgt) - current_price) / max(1e-6, abs(current_price - sl))
                    if rr < float(TRADER_RULEBOOK['min_reward_risk_ratio']):
                        Log(f"[熔断] R:R不足({rr:.2f})"); return True
                except Exception:
                    pass
    except Exception as e:
        Log(f"[熔断] 决策包异常: {e}"); return True
    return False


# ================= 执行器 =================
class TradeExecutor:
    def execute_decision(self, context, symbol, ai_decision, bar):
        # 兼容 _SymContext：其 state 已是该标的的 dict，而非 {symbol: {...}}
        if isinstance(context.state, dict) and symbol in context.state:
            state = context.state[symbol]
            st = state
        else:
            state = context.state
            st = context.state
        last_price = float(bar['close'])
        # 1) 统一信号与兜底: signal 归一化为小写，并做常见别名映射
        try:
            raw_sig = str((ai_decision or {}).get('signal', '') or '')
            s = raw_sig.strip().lower()
            alias = {
                '买': 'buy', '买入': 'buy', 'long': 'buy',
                '卖': 'sell', '卖出': 'sell', 'short': 'sell',
                '平': 'close', '平仓': 'close', 'exit': 'close'
            }
            s = alias.get(s, s)
            if ai_decision is not None:
                ai_decision['signal'] = s
        except Exception:
            s = (ai_decision or {}).get('signal')
        # 2) position_size_pct 兜底：当 buy/sell 且缺失/<=0，用自适应默认值
        try:
            plan = (ai_decision or {}).get('trade_plan') or {}
            size_pct = float(plan.get('position_size_pct', 0) or 0)
            if s in ('buy', 'sell') and (size_pct <= 0):
                dtrend = str(st.get('last_market_data', {}).get('d_trend', 'SIDEWAYS')).upper()
                adapt = Config.ADAPTIVE_PARAMS.get(dtrend, Config.ADAPTIVE_PARAMS['SIDEWAYS'])
                sp = float(adapt.get('position_size_pct') or 0.3)
                # 公司上限夹紧
                try:
                    sp = min(sp, float(TRADER_RULEBOOK.get('max_position_pct', 0.6)))
                except Exception:
                    pass
                plan['position_size_pct'] = sp
                if ai_decision is not None:
                    ai_decision['trade_plan'] = plan
                try:
                    Log(f"[{symbol}] 兜底仓位: size_pct 未提供/<=0，填充 {sp:.2f} (trend={dtrend})")
                except Exception:
                    pass
        except Exception:
            pass
        if is_ai_insane(context, ai_decision, last_price, st):
            return
        signal = (ai_decision or {}).get('signal')
        plan = (ai_decision or {}).get('trade_plan', {})
        size_pct = float(plan.get('position_size_pct', 0) or 0)
        target = plan.get('profit_target_1')
        plan_id = plan.get('plan_id')

        # 信心度门槛
        conf = float((ai_decision or {}).get('confidence', 0) or 0)
        if conf < float(getattr(Config, 'MIN_AI_CONFIDENCE', 0) or 0):
            Log(f"[{symbol}] AI信心度不足 ({conf:.2f}<{float(Config.MIN_AI_CONFIDENCE):.2f})，拒绝执行")
            return

        # 当前持仓与最小价差/冷却
        pos_now = int(st.get('local_pos') or 0)
        tick_size = PlatformAdapter.get_pricetick(symbol)
        if signal in ('buy', 'sell') and pos_now == 0:
            reu = st.get('reentry_until')
            if isinstance(reu, (int, float)) and time.time() < reu:
                Log(f"[{symbol}] 再入场冷却中，跳过新仓")
                return
            gap_ticks = int(getattr(Config, 'MIN_REENTRY_GAP_TICKS', 0) or 0)
            if gap_ticks and tick_size and tick_size > 0:
                ref_px = st.get('last_exit_price') or st.get('last_entry_px')
                if isinstance(ref_px, (int, float)):
                    if abs(last_price - float(ref_px)) < gap_ticks * tick_size:
                        Log(f"[{symbol}] 与上次价距不足 {gap_ticks}tick，跳过新仓")
                        return
        # 单向模式
        if getattr(Config, 'SINGLE_SIDE_MODE', True):
            if signal == 'sell' and pos_now > 0:
                Log(f"[{symbol}] 单向模式：持多{pos_now}，收到sell→平多")
                send_target_order(symbol, 0)
                st['current_plan_id'] = None
                return
            if signal == 'buy' and pos_now < 0:
                Log(f"[{symbol}] 单向模式：持空{abs(pos_now)}，收到buy→平空")
                send_target_order(symbol, 0)
                st['current_plan_id'] = None
                return

        # 报价选择
        md = st.get('last_market_data', {})
        bid = float(md.get('bid_price') or last_price)
        ask = float(md.get('ask_price') or last_price)
        mid = float(md.get('mid_price') or ((bid + ask) / 2.0 if bid and ask else last_price))
        order_price_style = str(plan.get('order_price_style', 'best')).lower()
        def _choose_price(side):
            if order_price_style == 'mid':
                return mid
            if order_price_style == 'market':
                return last_price
            return ask if side == 'buy' else bid

        # 仓位比例的流动性/价差降档与公司上限
        size_pct_eff = size_pct
        try:
            liq_state = str(md.get('liquidity_state', 'NORMAL'))
            spread = float(md.get('spread') or 0.0)
            mid_px = float(md.get('mid_price') or last_price)
            if liq_state == 'THIN':
                size_pct_eff = min(size_pct_eff, 0.3)
            if mid_px > 0 and spread > 0:
                if (spread / mid_px) > float(getattr(Config, 'SPREAD_RATIO_LIMIT', 0.003)):
                    size_pct_eff = min(size_pct_eff, 0.3)
        except Exception:
            pass
        try:
            size_pct_eff = min(size_pct_eff, float(TRADER_RULEBOOK.get('max_position_pct', 0.6)))
        except Exception:
            pass
        if getattr(Config, 'DEBUG_EXEC_CHECK', False):
            try:
                Log(f"[{symbol}] exec-check: size_pct={size_pct:.2f} -> eff={size_pct_eff:.2f}, liq={md.get('liquidity_state','?')}, spread={md.get('spread',0)}")
            except Exception:
                pass

        # 账户与合约
        acc = estimate_account(context, last_price, st)
        equity, available = acc['equity'], acc['available']
        mult = PlatformAdapter.get_contract_size(symbol)
        long_mr = PlatformAdapter.get_margin_ratio(symbol, 'long')
        short_mr = PlatformAdapter.get_margin_ratio(symbol, 'short')
        min_vol = 1
        try:
            c = PlatformAdapter.get_contract(symbol); mv = getattr(c, 'min_volume', None)
            if mv is None and isinstance(c, dict): mv = c.get('min_volume')
            if mv: min_vol = max(1, int(mv))
        except Exception:
            pass

        if signal == 'buy':
            tmp_price = _choose_price('buy')
            tmp_price = _normalize_price(tmp_price, tick_size, last_price)
            order_price = _align_price(tmp_price, 'buy', tick_size)
            notional_per_lot = order_price * mult
            if notional_per_lot <= 0:
                Log("[执行] 面值异常，拒绝开多"); return
            margin_per_lot = notional_per_lot * max(0.01, long_mr)
            try:
                Log(f"[{symbol}] 资金测算: notional/lot={notional_per_lot:.2f}, mr_long={long_mr:.4f}({PlatformAdapter.get_margin_ratio_source(symbol,'long')}), avail={available:.2f}")
            except Exception:
                pass
            # 基于保证金预算的目标手数（你的要求：按资金能开几手）
            need_min_per_lot = margin_per_lot * float(getattr(Config, 'NEW_TRADE_MARGIN_BUFFER', 1.12) or 1.12)
            lots_target_budget = (equity * size_pct_eff) / max(1e-9, need_min_per_lot)
            lots_target = int(lots_target_budget)
            lots_target = max(min_vol, lots_target)
            try:
                max_by_margin = int(available / max(1e-9, need_min_per_lot)) if need_min_per_lot > 0 else 0
                # 预估计划增量手数（不含最小手数修正，仅便于日志观测）
                _cur_lots = abs(pos_now) if pos_now > 0 else 0
                _vol_plan = max(0, min(max_by_margin, lots_target) - _cur_lots)
                Log(f"[{symbol}] 计划手数(保证金法): budget={lots_target_budget:.2f} → int={lots_target}, max_by_margin={max_by_margin}, need_min/lot≈{need_min_per_lot:.2f}, min_vol={min_vol}, pos_now={pos_now}, vol_plan={_vol_plan}")
            except Exception:
                pass
            max_lots_by_margin = int(available / max(1e-9, need_min_per_lot)) if need_min_per_lot > 0 else 0
            if pos_now > 0 and not getattr(Config, 'ALLOW_SAME_SIDE_PYRAMIDING', True):
                Log("[执行] 禁止同向加仓"); return
            if pos_now > 0:
                current_lots = abs(pos_now)
                vol = max(0, min(max_lots_by_margin, lots_target) - current_lots)
                # 目标已满足/超出：不需要同向加仓
                if current_lots >= lots_target:
                    try:
                        Log(f"[{symbol}] 目标手数已满足(现有>=目标)，不加仓")
                    except Exception:
                        pass
                    return
            else:
                vol = min(max_lots_by_margin, lots_target)
            if vol < min_vol:
                can_fund_min = (max_lots_by_margin >= min_vol)
                if not can_fund_min:
                    try:
                        need_min = need_min_per_lot * min_vol
                        Log(f"[执行] 可用资金不足(资金维度)，拒绝开多 | max_by_margin={max_lots_by_margin}, lots_target={lots_target}, min_vol={min_vol}, need_min≈{need_min:.2f}, avail={available:.2f}")
                    except Exception:
                        Log("[执行] 可用资金不足(资金维度)，拒绝开多")
                    return
                # lots_target 小于交易所最小手数，且资金允许 → 修正到最小手
                if lots_target < min_vol and can_fund_min:
                    vol = min_vol
            used_margin = equity - available
            margin_post = used_margin + vol * margin_per_lot
            gr = (equity / margin_post) if margin_post > 0 else 999
            if gr < float(Config.MIN_GUARANTEE_RATIO):
                Log(f"[执行] 担保比不足({gr:.2f} < {float(Config.MIN_GUARANTEE_RATIO):.2f})，拒绝开多"); return
            # 止损护栏
            try:
                atr_val = float(st.get('last_indicators', {}).get('atr') or 0)
            except Exception:
                atr_val = 0.0
            sl = plan.get('stop_loss')
            if sl is not None:
                try:
                    sl = float(sl)
                    min_ticks = int(getattr(Config, 'MIN_STOP_TICKS', 5) or 5)
                    atr_mult = float(getattr(Config, 'MIN_STOP_ATR_MULT', 0.25) or 0.25)
                    min_gap = max(min_ticks * tick_size, atr_val * atr_mult)
                    R_ai = max(0.0, (order_price - sl))
                    R_new = max(R_ai, min_gap)
                    sl = order_price - R_new
                    sl = math.floor(sl / tick_size) * tick_size if tick_size > 0 else sl
                    try:
                        Log(f"[{symbol}] 止损调整: ai_sl={plan.get('stop_loss')} → use_sl={sl:.2f}, min_gap={min_gap:.4f}, atr={atr_val:.4f}")
                    except Exception:
                        pass
                except Exception:
                    pass
            try:
                Log(f"[{symbol}] 下单前: side=BUY, vol={vol}, order_price={order_price:.2f}, SL={sl}, TP={str(target)}")
            except Exception:
                pass
            try:
                buy(symbol, order_price, vol)
                Log(f"[执行] 开多 {vol}手 @ {order_price:.2f} | SL={sl} TP={str(target)} plan={plan_id}")
            except Exception as e:
                Log(f"[执行] 开多下单失败: {e}"); return
            st['avg_price'] = order_price; st['local_pos'] = st.get('local_pos', 0) + vol
            st['current_plan_id'] = plan_id
            st['last_entry_px'] = order_price
            st['last_order_price'] = order_price; st['last_order_ts'] = time.time()
            st['ai_decision'] = {'stop_loss': sl, 'profit_target': target}
            try:
                cd_min = float((ai_decision or {}).get('cooldown_minutes', 0) or 0)
                if cd_min > 0:
                    st['cooldown_until'] = time.time() + cd_min * 60
                    Log(f"[{symbol}] 设置冷却 {int(cd_min)} 分钟")
            except Exception:
                pass
        elif signal == 'sell':
            tmp_price = _choose_price('sell')
            tmp_price = _normalize_price(tmp_price, tick_size, last_price)
            order_price = _align_price(tmp_price, 'sell', tick_size)
            notional_per_lot = order_price * mult
            if notional_per_lot <= 0:
                Log("[执行] 面值异常，拒绝开空"); return
            margin_per_lot = notional_per_lot * max(0.01, short_mr)
            try:
                Log(f"[{symbol}] 资金测算: notional/lot={notional_per_lot:.2f}, mr_short={short_mr:.4f}({PlatformAdapter.get_margin_ratio_source(symbol,'short')}), avail={available:.2f}")
            except Exception:
                pass
            need_min_per_lot = margin_per_lot * float(getattr(Config, 'NEW_TRADE_MARGIN_BUFFER', 1.12) or 1.12)
            lots_target_budget = (equity * size_pct_eff) / max(1e-9, need_min_per_lot)
            lots_target = int(lots_target_budget)
            lots_target = max(min_vol, lots_target)
            try:
                max_by_margin = int(available / max(1e-9, need_min_per_lot)) if need_min_per_lot > 0 else 0
                _cur_lots = abs(pos_now) if pos_now < 0 else 0
                _vol_plan = max(0, min(max_by_margin, lots_target) - _cur_lots)
                Log(f"[{symbol}] 计划手数(保证金法): budget={lots_target_budget:.2f} → int={lots_target}, max_by_margin={max_by_margin}, need_min/lot≈{need_min_per_lot:.2f}, min_vol={min_vol}, pos_now={pos_now}, vol_plan={_vol_plan}")
            except Exception:
                pass
            max_lots_by_margin = int(available / max(1e-9, need_min_per_lot)) if need_min_per_lot > 0 else 0
            if pos_now < 0 and not getattr(Config, 'ALLOW_SAME_SIDE_PYRAMIDING', True):
                Log("[执行] 禁止同向加仓"); return
            if pos_now < 0:
                current_lots = abs(pos_now)
                vol = max(0, min(max_lots_by_margin, lots_target) - current_lots)
                # 目标已满足/超出：不需要同向加仓
                if current_lots >= lots_target:
                    try:
                        Log(f"[{symbol}] 目标手数已满足(现有>=目标)，不加仓")
                    except Exception:
                        pass
                    return
            else:
                vol = min(max_lots_by_margin, lots_target)
            if vol < min_vol:
                can_fund_min = (max_lots_by_margin >= min_vol)
                if not can_fund_min:
                    try:
                        need_min = need_min_per_lot * min_vol
                        Log(f"[执行] 可用资金不足(资金维度)，拒绝开空 | max_by_margin={max_lots_by_margin}, lots_target={lots_target}, min_vol={min_vol}, need_min≈{need_min:.2f}, avail={available:.2f}")
                    except Exception:
                        Log("[执行] 可用资金不足(资金维度)，拒绝开空")
                    return
                # lots_target 小于交易所最小手数，且资金允许 → 修正到最小手
                if lots_target < min_vol and can_fund_min:
                    vol = min_vol
            used_margin = equity - available
            margin_post = used_margin + vol * margin_per_lot
            gr = (equity / margin_post) if margin_post > 0 else 999
            if gr < float(Config.MIN_GUARANTEE_RATIO):
                Log(f"[执行] 担保比不足({gr:.2f} < {float(Config.MIN_GUARANTEE_RATIO):.2f})，拒绝开空"); return
            try:
                atr_val = float(st.get('last_indicators', {}).get('atr') or 0)
            except Exception:
                atr_val = 0.0
            sl = plan.get('stop_loss')
            if sl is not None:
                try:
                    sl = float(sl)
                    min_ticks = int(getattr(Config, 'MIN_STOP_TICKS', 5) or 5)
                    atr_mult = float(getattr(Config, 'MIN_STOP_ATR_MULT', 0.25) or 0.25)
                    min_gap = max(min_ticks * tick_size, atr_val * atr_mult)
                    R_ai = max(0.0, (sl - order_price))
                    R_new = max(R_ai, min_gap)
                    sl = order_price + R_new
                    sl = math.ceil(sl / tick_size) * tick_size if tick_size > 0 else sl
                    try:
                        Log(f"[{symbol}] 止损调整: ai_sl={plan.get('stop_loss')} → use_sl={sl:.2f}, min_gap={min_gap:.4f}, atr={atr_val:.4f}")
                    except Exception:
                        pass
                except Exception:
                    pass
            try:
                Log(f"[{symbol}] 下单前: side=SELL, vol={vol}, order_price={order_price:.2f}, SL={sl}, TP={str(target)}")
            except Exception:
                pass
            try:
                short(symbol, order_price, vol)
                Log(f"[执行] 开空 {vol}手 @ {order_price:.2f} | SL={sl} TP={str(target)} plan={plan_id}")
            except Exception as e:
                Log(f"[执行] 开空下单失败: {e}"); return
            st['avg_price'] = order_price; st['local_pos'] = st.get('local_pos', 0) - vol
            st['current_plan_id'] = plan_id
            st['last_entry_px'] = order_price
            st['last_order_price'] = order_price; st['last_order_ts'] = time.time()
            st['ai_decision'] = {'stop_loss': sl, 'profit_target': target}
            try:
                cd_min = float((ai_decision or {}).get('cooldown_minutes', 0) or 0)
                if cd_min > 0:
                    st['cooldown_until'] = time.time() + cd_min * 60
                    Log(f"[{symbol}] 设置冷却 {int(cd_min)} 分钟")
            except Exception:
                pass
        elif signal == 'close':
            try:
                send_target_order(symbol, 0)
                Log("[执行] 平仓(目标仓位=0)")
                st['local_pos'] = 0; st['avg_price'] = 0
                st['last_exit_price'] = last_price; st['last_exit_time'] = time.time()
                st['reentry_until'] = time.time() + float(getattr(Config,'REENTRY_COOLDOWN_SECS',120) or 120)
                try:
                    cd_min = float((ai_decision or {}).get('cooldown_minutes', 0) or 0)
                    if cd_min > 0:
                        st['cooldown_until'] = time.time() + cd_min * 60
                        Log(f"[{symbol}] 设置冷却 {int(cd_min)} 分钟")
                except Exception:
                    pass
                try:
                    cd_min = float((ai_decision or {}).get('cooldown_minutes', 0) or 0)
                    if cd_min > 0:
                        st['cooldown_until'] = time.time() + cd_min * 60
                except Exception:
                    pass
            except Exception as e:
                Log(f"[执行] 平仓失败: {e}")
        else:
            Log(f"[{symbol}] 暂不处理 signal={signal}")


# ================= 生命周期回调 =================
def on_init(context):
    # 多标的初始化
    symbols = []
    if hasattr(context, 'symbols') and isinstance(context.symbols, (list, tuple)) and context.symbols:
        symbols = list(context.symbols)
    else:
        # 默认读取本地配置的 SYMBOLS/SYMBOL
        try:
            symbols = list(Config.SYMBOLS)
        except Exception:
            symbols = []
        if not symbols:
            sym = getattr(context, 'symbol', None) or getattr(context, 'vt_symbol', None) or getattr(Config, 'SYMBOL', 'SYMBOL')
            symbols = [sym]
    context.state = {}
    context.trades_by_symbol = {}
    for sym in symbols:
        context.state[sym] = {
            'symbol': sym,
            'local_pos': 0,
            'avg_price': 0.0,
            'realized_pnl': 0.0,
            'entry_time': None,
            'klines_1m': [],
            'klines_1d': [],
            'dom_bars': [],
            'map_pens': {},            # 分笔价格聚合: {price: {bid_volume, ask_volume}}
            'divide_pen_list': [],     # 分笔列表
            'tick_buffer': [],
            'last_tick': None,
            'last_indicators': {},
            'last_market_data': {},
            'ai_interval_secs': 180,
            'last_ai_call_time': 0.0,
            'cooldown_until': 0.0,
            'reentry_until': 0.0,
            'ai_in_flight': False,
            'stagger_offset': (hash(sym) % 5),
            'trailing': None,
            'scale_out_plan': None,
            'current_plan_id': None,
            'history_backfilled': False,
        }
        context.trades_by_symbol[sym] = []
    context.zigzag_threshold_pct = 0.3
    # 读取 DeepSeek Key（优先环境变量，其次本地Config占位）
    ds_keys_env = os.environ.get('DEEPSEEK_API_KEYS')
    if ds_keys_env:
        keys = [k.strip() for k in ds_keys_env.split(',') if k.strip()]
    else:
        k1 = os.environ.get('DEEPSEEK_API_KEY')
        if k1:
            keys = [k1]
        else:
            keys = list(Config.DEEPSEEK_API_KEYS or ([] if not Config.DEEPSEEK_API_KEY else [Config.DEEPSEEK_API_KEY]))
    context.ai_engine = DeepSeekChiefEngine(keys)
    try:
        context.key_pool = _KeyPool(keys)
    except Exception:
        context.key_pool = None
    context.executor = TradeExecutor()
    try:
        Log("[INIT] AI Trading Firm v2 ready (DeepSeek Chief)")
        Log("交易品种: %s" % ", ".join(symbols))
    except Exception:
        pass
    # 订阅方式：完全对齐 gk 策略（同步订阅，不降级、不延迟）
    for sym in symbols:
        # 明确订阅 tick（有的平台需要单独订阅 tick）
        try:
            subscribe(sym)
            Log(f"[{sym}] 已订阅tick流")
        except Exception:
            # 某些平台 subscribe(sym) 可能不需要/不支持；忽略异常
            pass
        try:
            subscribe(sym, '1m', 720)
            Log(f"[{sym}] 订阅成功: 1m")
        except Exception:
            try:
                Log(f"[{sym}] 订阅失败(忽略): 1m")
            except Exception:
                pass
        try:
            subscribe(sym, '1d', 50)
            Log(f"[{sym}] 订阅成功: 1d")
        except Exception:
            try:
                Log(f"[{sym}] 订阅失败(忽略): 1d")
            except Exception:
                pass
    # 启动交易门控
    try:
        context.trading_allowed = True
    except Exception:
        pass
    # 默认初始资金：20万（可被外部注入覆盖）
    try:
        if not hasattr(context, 'initial_cash') or not float(getattr(context, 'initial_cash') or 0):
            context.initial_cash = 200_000.0
    except Exception:
        pass


def on_start(context):
    Log("[START] running")
    # 若配置为 start，再执行订阅，避免 on_init 阻塞
    try:
        if str(getattr(Config, 'SUBSCRIBE_MODE', 'deferred')) == 'start':
            tasks = list(getattr(context, '_subscribe_tasks', []) or [])
            for sym, itv, n in tasks:
                try:
                    if itv == 'tick' or itv is None:
                        subscribe(sym)
                    else:
                        subscribe(sym, itv, n)
                    Log(f"[{sym}] 订阅成功(start): {itv or 'tick'}")
                except Exception:
                    try:
                        Log(f"[{sym}] 订阅失败(start): {itv or 'tick'}")
                    except Exception:
                        pass
            context._subscribe_tasks = []
    except Exception:
        pass


def on_tick(context, tick):
    # 多标的：路由到对应状态
    sym = getattr(tick, 'symbol', None) or getattr(tick, 'vt_symbol', None)
    if not sym:
        # 兼容只有单标的
        syms = list(context.state.keys()) if isinstance(context.state, dict) else []
        sym = syms[0] if syms else getattr(context, 'symbol', None)
        if not sym:
            return
    st = context.state.get(sym)
    if not st:
        return
    try:
        bidv = getattr(tick, 'bid_volume_1', None) or getattr(tick, 'bid_volume', None) or 0
        askv = getattr(tick, 'ask_volume_1', None) or getattr(tick, 'ask_volume', None) or 0
        last_price = getattr(tick, 'last_price', getattr(tick, 'price', 0))
        tb = st['tick_buffer']
        tb.append({'delta': (bidv - askv), 'price': last_price, 'depth5': (bidv + askv)})
        if len(tb) > 200:
            del tb[:len(tb)-200]
        # L1订单流分笔：基于 tick 推导方向与现手/增仓，把量累计到价位
        _on_divide_pen(context, sym, tick)
        st['last_tick'] = tick
        # 若尚未形成1m节拍，周期性输出心跳日志，便于确认订阅已生效
        try:
            if getattr(Config, 'DEBUG_VERBOSE', False):
                now = time.time()
                prev = float(st.get('last_tick_log_ts') or 0.0)
                if (now - prev) >= float(getattr(Config, 'DEBUG_STATUS_INTERVAL_SECS', 60) or 60):
                    Log(f"[{sym}] tick心跳: px={float(last_price):.2f}, depth1={int(bidv)+int(askv)}; 1m_bars={len(st.get('klines_1m', []))}")
                    st['last_tick_log_ts'] = now
        except Exception:
            pass
    except Exception:
        pass


def on_bar(context, bars):
    # 多标的/多根归一化
    items = []
    if isinstance(bars, dict):
        items = list(bars.items())
    elif isinstance(bars, (list, tuple)):
        tmp = []
        for b in bars:
            sym_b = getattr(b, 'vt_symbol', None) or getattr(b, 'symbol', None)
            if not sym_b:
                syms = list(context.state.keys()) if isinstance(context.state, dict) else []
                sym_b = syms[0] if syms else None
            if sym_b:
                tmp.append((sym_b, b))
        items = tmp
    else:
        sym_b = getattr(bars, 'symbol', None) or getattr(bars, 'vt_symbol', None)
        if not sym_b:
            syms = list(context.state.keys()) if isinstance(context.state, dict) else []
            sym_b = syms[0] if syms else None
        if sym_b:
            items = [(sym_b, bars)]
    if not items:
        return

    for sym, b in items:
        st = context.state.get(sym)
        if not st:
            continue
        # 启动期历史回填（仅一次）
        try:
            if not st.get('history_backfilled'):
                _maybe_backfill_history(context, sym, st)
                st['history_backfilled'] = True
        except Exception:
            pass
        bar = {
            'ts': getattr(b, 'datetime', datetime.now()),
            'open': float(getattr(b, 'open_price', getattr(b, 'open', 0)) or 0),
            'high': float(getattr(b, 'high_price', getattr(b, 'high', 0)) or 0),
            'low': float(getattr(b, 'low_price', getattr(b, 'low', 0)) or 0),
            'close': float(getattr(b, 'close_price', getattr(b, 'close', 0)) or 0),
            'volume': float(getattr(b, 'volume', 0) or 0),
        }
        # 刷新K线
        st['klines_1m'].append(bar)
        if len(st['klines_1m']) > 720:
            del st['klines_1m'][:len(st['klines_1m'])-720]
        # 封口订单流柱（使用分笔价位聚合 map_pens → price_levels + delta）
        price_levels = []
        bar_delta = 0
        try:
            for key in sorted(st['map_pens'].keys(), reverse=True):
                pv = st['map_pens'].get(key, {}) or {}
                bid_volume = int(pv.get('bid_volume', 0) or 0)
                ask_volume = int(pv.get('ask_volume', 0) or 0)
                price_levels.append({'price': key, 'bid_volume': bid_volume, 'ask_volume': ask_volume})
                bar_delta += (bid_volume - ask_volume)
        except Exception:
            price_levels = []
        # 若本bar内没有分笔，退化用近窗口delta估算
        if not price_levels:
            tb = st['tick_buffer']
            bar_delta = sum([t.get('delta', 0) for t in tb[-60:]]) if tb else 0
        st['dom_bars'].append({'datetime': bar['ts'], 'price_levels': price_levels, 'delta': bar_delta, 'ohlc': [bar['open'],bar['high'],bar['low'],bar['close']], 'volume': bar['volume']})
        st['map_pens'] = {}
        if len(st['dom_bars']) > 720:
            del st['dom_bars'][:len(st['dom_bars'])-720]
        # 指标
        closes = [x['close'] for x in st['klines_1m']]
        highs = [x['high'] for x in st['klines_1m']]
        lows = [x['low'] for x in st['klines_1m']]
        ind = {
            'ema_20': _ema(closes[-60:], 20) if len(closes) >= 20 else 0.0,
            'ema_60': _ema(closes, 60) if len(closes) >= 60 else 0.0,
            'rsi': _rsi(closes, 14) if len(closes) >= 15 else 0.0,
            'atr': _atr(highs, lows, closes, 14) if len(closes) >= 15 else 0.0,
            'high_20': max(closes[-20:]) if len(closes) >= 20 else (closes[-1] if closes else 0.0),
            'low_20': min(closes[-20:]) if len(closes) >= 20 else (closes[-1] if closes else 0.0),
        }
        try:
            ind['price_range_pct'] = (ind['high_20'] - ind['low_20']) / ind['low_20'] * 100 if ind['low_20'] else 0.0
        except Exception:
            ind['price_range_pct'] = 0.0
        st['last_indicators'] = ind
        # 市场数据
        try:
            md = _collect_market_data(context, sym, st, bar)
            st['last_market_data'] = md
        except Exception:
            pass
        # 自适应节拍
        dtrend = str(st.get('last_market_data', {}).get('d_trend','SIDEWAYS')).upper()
        adapt = Config.ADAPTIVE_PARAMS.get(dtrend, Config.ADAPTIVE_PARAMS['SIDEWAYS'])
        st['ai_interval_secs'] = adapt.get('ai_interval_secs', 180)
        # 调用AI（节拍+冷却+错峰）
        now = time.time(); last_call = float(st.get('last_ai_call_time') or 0.0)
        stag = float(st.get('stagger_offset') or 0.0)
        cd_until = float(st.get('cooldown_until') or 0.0)
        in_cd = cd_until and now < cd_until
        enough_bars = len(st['klines_1m']) >= int(getattr(Config,'MIN_1M_BARS_FOR_AI', 10))
        # 周期性状态日志（便于定位为何没有触发AI或执行）
        try:
            if getattr(Config, 'DEBUG_VERBOSE', False):
                prev = float(st.get('last_status_log_ts') or 0.0)
                if (now - prev) >= float(getattr(Config, 'DEBUG_STATUS_INTERVAL_SECS', 60) or 60):
                    reasons = []
                    if not enough_bars:
                        reasons.append(f"bars={len(st['klines_1m'])}/{int(getattr(Config,'MIN_1M_BARS_FOR_AI', 10))}")
                    wait_more = (float(st['ai_interval_secs']) + stag) - (now - last_call)
                    if wait_more > 0:
                        reasons.append(f"interval_wait={int(wait_more)}s")
                    if in_cd:
                        reasons.append(f"cooldown_left={int(cd_until - now)}s")
                    if st.get('ai_in_flight', False):
                        reasons.append("ai_in_flight")
                    if not getattr(context, 'trading_allowed', True):
                        reasons.append("trading_stopped")
                    if reasons:
                        Log(f"[{sym}] 状态: {', '.join(reasons)}")
                    st['last_status_log_ts'] = now
        except Exception:
            pass
        # 若有后台结果待执行，先消费
        try:
            if st.get('pending_decision') is not None:
                seq = int(st.get('pending_seq') or 0)
                last_exec = int(st.get('last_executed_seq') or 0)
                if seq > last_exec:
                    try:
                        Log(f"[{sym}] 消费AI结果: seq={seq}")
                        # 摘要打印，便于关联执行与AI返回
                        try:
                            _dec = st.get('pending_decision') or {}
                            _sig2 = str(_dec.get('signal') or '')
                            _pl2 = (_dec.get('trade_plan') or {})
                            Log(f"[{sym}] AI计划摘要(执行前): signal={_sig2}, size_pct={_pl2.get('position_size_pct')}, entry={_pl2.get('entry_price')}, SL={_pl2.get('stop_loss')}, TP1={_pl2.get('profit_target_1')}, plan_id={_pl2.get('plan_id')}")
                        except Exception:
                            pass
                    except Exception:
                        pass
                    context.executor.execute_decision(_SymContext(context, sym), sym, st.get('pending_decision'), bar)
                    st['last_executed_seq'] = seq
        except Exception:
            pass
        # 新任务触发条件
        if enough_bars and (now - last_call) >= (float(st['ai_interval_secs']) + stag) and not in_cd and not st.get('ai_in_flight', False) and getattr(context, 'trading_allowed', True):
            try:
                st['ai_in_flight'] = True
                try:
                    # 记录结构复杂度，便于观察AI在复杂结构下的行为
                    scx_log = 0.0
                    try:
                        scx_log = _structure_complexity(_SymContext(context, sym), lookback_bars=20)
                    except Exception:
                        scx_log = 0.0
                    Log(f"[{sym}] 调用AI：bars={len(st['klines_1m'])}, px={bar['close']:.2f}, interval={int(st['ai_interval_secs'])}s, scx={scx_log}")
                except Exception:
                    pass
                # 异步后台任务（避免阻塞 on_bar 线程）
                key_idx = None; key_val = None; key_mask = None
                try:
                    if getattr(context, 'key_pool', None):
                        key_idx, key_val, key_mask = context.key_pool.acquire()
                        if key_mask:
                            Log(f"[{sym}] 使用Key {key_mask} 提交AI任务")
                except Exception:
                    key_idx = key_val = key_mask = None
                if getattr(context, 'key_pool', None) and key_val is None:
                    # Key池忙，延后
                    st['ai_in_flight'] = False
                    try:
                        Log(f"[{sym}] Key池忙，延后本次AI调用")
                    except Exception:
                        pass
                else:
                    try:
                        st['ai_job_seq'] = int(st.get('ai_job_seq') or 0) + 1
                    except Exception:
                        st['ai_job_seq'] = 1
                    job_seq = int(st['ai_job_seq'])
                    import threading
                    def _run_ai(ctx, sym_local, bar_local, kidx, kval, kmark, seq):
                        try:
                            # 某些平台/路径下有代码意外引用全局 context 名称，临时注入以避免 NameError
                            _prev_ctx = globals().get('context', None)
                            globals()['context'] = ctx
                            sym_ctx2 = _SymContext(ctx, sym_local)
                            briefing2 = get_traders_view(sym_ctx2, bar_local)
                            decision2, err2 = ctx.ai_engine.call(briefing2, TRADER_RULEBOOK)
                            if err2:
                                try:
                                    Log(f"[{sym_local}] AI后台任务失败: {err2}")
                                except Exception:
                                    pass
                            else:
                                try:
                                    Log(f"[{sym_local}] AI后台返回: ok, seq={seq}")
                                    # 打印AI返回JSON（截断）与计划摘要
                                    try:
                                        _j = json.dumps(decision2, ensure_ascii=False)
                                        if len(_j) > 800:
                                            _j = _j[:800] + '…'
                                        Log(f"[{sym_local}] AI返回JSON: {_j}")
                                    except Exception:
                                        pass
                                    try:
                                        _sig = str((decision2 or {}).get('signal') or '')
                                        _plan = (decision2 or {}).get('trade_plan') or {}
                                        Log(f"[{sym_local}] AI计划摘要: signal={_sig}, size_pct={_plan.get('position_size_pct')}, entry={_plan.get('entry_price')}, SL={_plan.get('stop_loss')}, TP1={_plan.get('profit_target_1')}, plan_id={_plan.get('plan_id')}")
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                                st_local = ctx.state[sym_local]
                                st_local['pending_decision'] = decision2
                                st_local['pending_seq'] = seq
                        except Exception as e2:
                            try:
                                Log(f"[{sym_local}] AI后台任务异常: {e2}")
                            except Exception:
                                pass
                        finally:
                            try:
                                # 恢复全局context占位
                                globals()['context'] = _prev_ctx
                            except Exception:
                                pass
                            try:
                                st_local = ctx.state[sym_local]
                                st_local['last_ai_call_time'] = time.time()
                                st_local['ai_in_flight'] = False
                            except Exception:
                                pass
                            try:
                                if getattr(ctx, 'key_pool', None) and kidx is not None:
                                    ctx.key_pool.release(kidx)
                            except Exception:
                                pass
                    th = threading.Thread(target=_run_ai, args=(context, sym, bar, key_idx, key_val, key_mask, job_seq), name=f"AIJob-{sym}", daemon=True)
                    try:
                        th.start()
                    except Exception:
                        st['ai_in_flight'] = False
            except Exception as e:
                Log(f"[{sym}] 调用AI异常: {e}")
            finally:
                # 由后台任务负责重置 ai_in_flight
                pass
        # 风控心跳
        try:
            _risk_heartbeat(_SymContext(context, sym), sym, bar)
        except Exception:
            pass


def on_order(context, order):
    # 简记本地持仓（实际以 on_trade 为准）
    try:
        sym = getattr(order, 'symbol', None) or getattr(order, 'vt_symbol', None)
        if not sym:
            syms = list(context.state.keys()) if isinstance(context.state, dict) else []
            sym = syms[0] if syms else None
        if not sym: return
        st = context.state.get(sym)
        if not st: return
        vol = int(getattr(order, 'volume', 0) or 0)
        px = float(getattr(order, 'price', 0) or 0)
        direction = str(getattr(order, 'direction', ''))
        offset = str(getattr(order, 'offset', ''))
        status = str(getattr(order, 'status', ''))
        if status and status.upper().startswith('ALL'):
            if offset in ('开','open','OPEN'):
                if '买' in direction or direction.lower().startswith('buy'):
                    st['local_pos'] = st.get('local_pos', 0) + vol
                else:
                    st['local_pos'] = st.get('local_pos', 0) - vol
                st['avg_price'] = px
            # 记录交易rec（realized_delta 后续在on_trade精确）
            try:
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                _su = direction.upper()
                tag = 'OpenLong' if (offset in ('开','open','OPEN') and ("买" in direction or _su.startswith('BUY'))) else ('OpenShort' if (offset in ('开','open','OPEN')) else ('CloseLong' if ("卖" in direction or _su.startswith('SELL')) else 'CloseShort'))
                rec = {'time': ts, 'tag': tag, 'volume': vol, 'price': px, 'realized_delta': 0.0, 'plan_id': st.get('current_plan_id')}
                context.trades_by_symbol[sym].append(rec)
            except Exception:
                pass
    except Exception:
        pass


def on_trade(context, trade):
    # 成交回写 + 滑点日志 + 交易记录
    try:
        sym = getattr(trade, 'symbol', None) or getattr(trade, 'vt_symbol', None)
        if not sym:
            syms = list(context.state.keys()) if isinstance(context.state, dict) else []
            sym = syms[0] if syms else None
        if not sym: return
        st = context.state.get(sym)
        if not st: return
        dir_raw = str(getattr(trade, 'direction', '') or '')
        is_buy = ('买' in dir_raw) or dir_raw.lower().startswith('buy')
        off_raw = str(getattr(trade, 'offset', '') or '')
        vol = int(getattr(trade, 'volume', 0) or 0)
        px = float(getattr(trade, 'price', 0) or 0)
        # 本地持仓
        lp = int(st.get('local_pos') or 0)
        st['local_pos'] = lp + (vol if is_buy else -vol)
        # 滑点
        try:
            lo_px = float(st.get('last_order_price')) if st.get('last_order_price') is not None else None
            lo_ts = float(st.get('last_order_ts')) if st.get('last_order_ts') is not None else None
            tk = PlatformAdapter.get_pricetick(sym) or 0.0
            if lo_px is not None and lo_ts is not None and (time.time() - lo_ts) < 15:
                slip = float(px) - float(lo_px)
                ticks = (slip / tk) if (tk and tk > 0) else 0.0
                Log(f"[{sym}] 成交滑点: {slip:+.2f} ({ticks:+.1f}tick) vs 下单价{lo_px:.2f}")
        except Exception:
            pass
        # 交易记录
        try:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            tag = 'OpenLong' if (off_raw in ('开','open','OPEN') and is_buy) else ('OpenShort' if (off_raw in ('开','open','OPEN')) else ('CloseLong' if not is_buy else 'CloseShort'))
            rec = {'time': ts, 'tag': tag, 'volume': vol, 'price': px, 'realized_delta': 0.0, 'plan_id': st.get('current_plan_id')}
            context.trades_by_symbol[sym].append(rec)
        except Exception:
            pass
        # 平仓后记录last_exit
        try:
            if int(st.get('local_pos') or 0) == 0:
                st['last_exit_price'] = float(px)
                st['last_exit_time'] = time.time()
                st['reentry_until'] = time.time() + float(getattr(Config,'REENTRY_COOLDOWN_SECS',120) or 120)
        except Exception:
            pass
    except Exception:
        pass


def on_backtest_finished(context, indicator):
    Log("========== 回测结束 ==========")
    try:
        Log(f"总收益率: {indicator['累计收益率']*100:.2f}%")
        Log(f"最大回撤: {indicator['最大回撤']*100:.2f}%")
    except Exception:
        pass


# =============== 运行期辅助 ===============
def _maybe_backfill_history(context, sym, st):
    """尝试回填1m/1d历史，避免启动初期上下文不足。容错处理，不影响正常运行。"""
    # 1m
    try:
        am_1m = None
        try:
            am_1m = get_market_data(sym, '1m')
        except Exception:
            am_1m = None
        if am_1m is not None and getattr(am_1m, 'count', 0) > 0:
            st['klines_1m'] = []
            for i in range(am_1m.count):
                st['klines_1m'].append({
                    'open': float(am_1m.open[i]),
                    'high': float(am_1m.high[i]),
                    'low': float(am_1m.low[i]),
                    'close': float(am_1m.close[i]),
                    'volume': float(am_1m.volume[i]),
                })
            try:
                Log(f"[{sym}] ✅ 1m 实时数据加载成功: {len(st['klines_1m'])} 根")
            except Exception:
                pass
        else:
            try:
                bars_1m = query_history(sym, '1m', number=300)
                if bars_1m:
                    st['klines_1m'] = []
                    for b in bars_1m:
                        st['klines_1m'].append({
                            'open': float(getattr(b, 'open_price', getattr(b, 'open', 0)) or 0.0),
                            'high': float(getattr(b, 'high_price', getattr(b, 'high', 0)) or 0.0),
                            'low': float(getattr(b, 'low_price', getattr(b, 'low', 0)) or 0.0),
                            'close': float(getattr(b, 'close_price', getattr(b, 'close', 0)) or 0.0),
                            'volume': float(getattr(b, 'volume', 0) or 0.0),
                        })
                    try:
                        Log(f"[{sym}] ✅ 1m 历史回填成功: {len(st['klines_1m'])} 根")
                    except Exception:
                        pass
            except Exception as e:
                try:
                    Log(f"[{sym}] [警告] 1m 历史回填异常: {e}")
                except Exception:
                    pass
    except Exception:
        pass
    # 1d
    try:
        am_1d = None
        try:
            am_1d = get_market_data(sym, '1d')
        except Exception:
            am_1d = None
        if am_1d is not None and getattr(am_1d, 'count', 0) > 0:
            st['klines_1d'] = []
            for i in range(am_1d.count):
                st['klines_1d'].append({
                    'open': float(am_1d.open[i]),
                    'high': float(am_1d.high[i]),
                    'low': float(am_1d.low[i]),
                    'close': float(am_1d.close[i]),
                    'volume': float(am_1d.volume[i]),
                })
            try:
                Log(f"[{sym}] ✅ 1d 实时数据加载成功: {len(st['klines_1d'])} 根")
            except Exception:
                pass
        else:
            try:
                bars_1d = query_history(sym, '1d', number=50)
                if bars_1d:
                    st['klines_1d'] = []
                    for b in bars_1d:
                        st['klines_1d'].append({
                            'open': float(getattr(b, 'open_price', getattr(b, 'open', 0)) or 0.0),
                            'high': float(getattr(b, 'high_price', getattr(b, 'high', 0)) or 0.0),
                            'low': float(getattr(b, 'low_price', getattr(b, 'low', 0)) or 0.0),
                            'close': float(getattr(b, 'close_price', getattr(b, 'close', 0)) or 0.0),
                            'volume': float(getattr(b, 'volume', 0) or 0.0),
                        })
                    try:
                        Log(f"[{sym}] ✅ 1d 历史回填成功: {len(st['klines_1d'])} 根")
                    except Exception:
                        pass
            except Exception as e:
                try:
                    Log(f"[{sym}] [警告] 1d 历史回填异常: {e}")
                except Exception:
                    pass
    except Exception:
        pass

def _collect_market_data(context, sym, st, bar):
    tick = st.get('last_tick')
    current_price = float(bar['close'])
    def _gv(obj, names, default=None):
        for n in names:
            v = getattr(obj, n, None)
            if v is not None:
                return v
        return default
    bid_price = _gv(tick, ['bid_price_1','bid_price1','bid_price'], current_price) if tick else current_price
    ask_price = _gv(tick, ['ask_price_1','ask_price1','ask_price'], current_price) if tick else current_price
    bid_volume = _gv(tick, ['bid_volume_1','bid_volume1','bid_volume'], 0) if tick else 0
    ask_volume = _gv(tick, ['ask_volume_1','ask_volume1','ask_volume'], 0) if tick else 0
    mid_price = (bid_price + ask_price)/2.0 if (bid_price and ask_price) else current_price
    spread = (ask_price - bid_price) if (ask_price and bid_price) else 0
    denom = (bid_volume + ask_volume) if (bid_volume + ask_volume) > 0 else 1
    imbalance_l1 = (bid_volume - ask_volume) / denom
    tb = st.get('tick_buffer') or []
    avg_depth = sum([t.get('depth5',0) for t in tb]) / len(tb) if tb else 0
    liquidity_score = ((bid_volume + ask_volume) / avg_depth) if avg_depth > 0 else 1.0
    liq_state = 'THIN' if liquidity_score < float(Config.LIQUIDITY_SCORE_THIN) else ('THICK' if liquidity_score > float(Config.LIQUIDITY_SCORE_THICK) else 'NORMAL')
    # 趋势近似（ema20/60）
    ind = st.get('last_indicators', {})
    ema20 = float(ind.get('ema_20') or 0); ema60 = float(ind.get('ema_60') or 0)
    d_trend = 'UPTREND' if ema20 > ema60 else ('DOWNTREND' if ema20 < ema60 else 'SIDEWAYS')
    return {
        'symbol': sym,
        'current_price': current_price,
        'bid_price': bid_price,
        'ask_price': ask_price,
        'bid_volume': bid_volume,
        'ask_volume': ask_volume,
        'spread': spread,
        'mid_price': mid_price,
        'imbalance_l1': imbalance_l1,
        'liquidity_score': liquidity_score,
        'liquidity_state': liq_state,
        'd_trend': d_trend,
        'atr': float(ind.get('atr') or 0),
    }


def _risk_heartbeat(context, sym, bar):
    st = context.state.get(sym, {})
    pos = int(st.get('local_pos') or 0)
    px = float(bar['close'])
    # 1) 强制平仓时间检查（白盘/夜盘）
    try:
        now_dt = bar.get('ts') if isinstance(bar, dict) else None
        if not isinstance(now_dt, datetime):
            now_dt = datetime.now()
        # 计算当期强平deadline
        def _force_close_deadline(now_dt_local):
            try:
                roll_h = int(getattr(Config, 'TRADING_DAY_ROLLOVER_HOUR', 21))
            except Exception:
                roll_h = 21
            day_str = str(getattr(Config, 'FORCE_CLOSE_TIME_DAY', '14:55:00'))
            night_str = str(getattr(Config, 'FORCE_CLOSE_TIME_NIGHT', '02:25:00'))
            try:
                dh, dm, ds = [int(x) for x in day_str.split(':')]
            except Exception:
                dh, dm, ds = 14, 55, 0
            try:
                nh, nm, ns = [int(x) for x in night_str.split(':')]
            except Exception:
                nh, nm, ns = 2, 25, 0
            if (now_dt_local.hour >= roll_h) or (now_dt_local.hour < 3):
                base_date = now_dt_local.date()
                if now_dt_local.hour >= roll_h:
                    deadline = datetime.combine(base_date, datetime.min.time()).replace(hour=nh, minute=nm, second=ns) + timedelta(days=1)
                else:
                    deadline = datetime.combine(base_date, datetime.min.time()).replace(hour=nh, minute=nm, second=ns)
                label = night_str
            else:
                deadline = datetime.combine(now_dt_local.date(), datetime.min.time()).replace(hour=dh, minute=dm, second=ds)
                label = day_str
            return deadline, label
        deadline_dt, dl_label = _force_close_deadline(now_dt)
        if now_dt >= deadline_dt:
            if pos != 0:
                Log(f"[{sym}] [警告] 到达强制平仓时间 {dl_label}，强制平仓！")
                send_target_order(sym, 0)
                st['local_pos'] = 0; st['avg_price'] = 0
            try:
                context.trading_allowed = False
            except Exception:
                pass
            return
    except Exception:
        pass

    # 2) AI止损（硬止损）
    if pos != 0:
        ai = st.get('ai_decision') or {'stop_loss': None}
        stop_loss = ai.get('stop_loss')
        if stop_loss:
            try:
                sl = float(stop_loss)
                if pos > 0 and px <= sl:
                    Log(f"[{sym}] 触发AI止损 ({px:.2f} <= {sl:.2f})，平仓!")
                    send_target_order(sym, 0)
                    st['local_pos'] = 0; st['avg_price'] = 0
                    st['last_exit_price'] = px; st['last_exit_time'] = time.time()
                    st['reentry_until'] = time.time() + float(getattr(Config,'REENTRY_COOLDOWN_SECS',120) or 120)
                    return
                if pos < 0 and px >= sl:
                    Log(f"[{sym}] 触发AI止损 ({px:.2f} >= {sl:.2f})，平仓!")
                    send_target_order(sym, 0)
                    st['local_pos'] = 0; st['avg_price'] = 0
                    st['last_exit_price'] = px; st['last_exit_time'] = time.time()
                    st['reentry_until'] = time.time() + float(getattr(Config,'REENTRY_COOLDOWN_SECS',120) or 120)
                    return
            except Exception:
                pass

    # 3) 单日亏损护栏（粗略：相对初始资金）
    try:
        md = st.get('last_market_data', {})
        eq = float(estimate_account(context, px, st).get('equity') or 0.0)
        init_cash = float(getattr(context, 'initial_cash', 200_000.0))
        max_daily = float(TRADER_RULEBOOK.get('max_daily_loss_pct', 0.05))
        if eq <= init_cash * (1.0 - max_daily):
            Log(f"[{sym}] [警告] 触发单日最大亏损限制（{max_daily*100:.2f}%），停止交易并强平！")
            if pos != 0:
                send_target_order(sym, 0)
                st['local_pos'] = 0; st['avg_price'] = 0
            try:
                context.trading_allowed = False
            except Exception:
                pass
            return
    except Exception:
        pass
    # TODO: 可扩展：追踪止损与分批止盈


class _SymContext:
    """按标的视图代理（提供与单标的相同的上下文接口）。"""
    def __init__(self, ctx, sym):
        self._ctx = ctx; self._sym = sym
        self.state = ctx.state[sym]
        self.symbol = sym
        self.initial_cash = getattr(ctx, 'initial_cash', 1_000_000.0)
        # 暴露 per-symbol 的简报数据容器
        self.klines_1m = self.state['klines_1m']
        self.dom_bars = self.state['dom_bars']
        self.zigzag_threshold_pct = getattr(ctx, 'zigzag_threshold_pct', 0.3)
    def __getattr__(self, item):
        return getattr(self._ctx, item)


# =============== 简易 Key 池（限制每Key并发） ===============
class _KeyPool:
    def __init__(self, keys):
        self._keys = list(keys or [])
        self._n = len(self._keys)
        self._inflight = [0] * self._n
        self._lock = None
        try:
            import threading
            self._lock = threading.Lock()
        except Exception:
            self._lock = None

    def acquire(self):
        if not self._keys:
            return None, None, None
        if self._lock:
            self._lock.acquire()
        try:
            # 每个Key最多1并发
            for idx, k in enumerate(self._keys):
                if self._inflight[idx] <= 0:
                    self._inflight[idx] = 1
                    mask = f"K{idx+1}/{self._n}"
                    return idx, k, mask
            return None, None, None
        finally:
            if self._lock:
                self._lock.release()

    def release(self, idx):
        if idx is None or self._keys is None:
            return
        if self._lock:
            self._lock.acquire()
        try:
            if 0 <= idx < len(self._inflight):
                self._inflight[idx] = max(0, self._inflight[idx] - 1)
        finally:
            if self._lock:
                self._lock.release()


# =============== 分笔订单流（L1价量驱动） ===============
def _calculate_pen_type(chg_vol, chg_hld):
    if chg_hld == 0 and chg_vol == 0:
        return 0
    if chg_hld == 0 and chg_vol > 0:
        return 1
    if chg_hld > 0:
        if chg_hld - chg_vol == 0:
            return 2
        else:
            return 3
    if chg_hld < 0:
        if chg_hld + chg_vol == 0:
            return 4
        else:
            return 5


def _calculate_divide_pen(st, tick):
    last_tick = st.get('last_tick')
    if not last_tick:
        return None
    try:
        chg_vol = int(getattr(tick, 'volume', 0) or 0) - int(getattr(last_tick, 'volume', 0) or 0)
        if chg_vol == 0:
            return None
        chg_hld = int(getattr(tick, 'open_interest', 0) or 0) - int(getattr(last_tick, 'open_interest', 0) or 0)
        last_price = float(getattr(tick, 'last_price', getattr(tick, 'price', 0)) or 0.0)
        last_ask1 = float(getattr(last_tick, 'ask_price_1', getattr(last_tick, 'ask_price1', 0)) or 0.0)
        last_bid1 = float(getattr(last_tick, 'bid_price_1', getattr(last_tick, 'bid_price1', 0)) or 0.0)
        ask1 = float(getattr(tick, 'ask_price_1', getattr(tick, 'ask_price1', 0)) or 0.0)
        bid1 = float(getattr(tick, 'bid_price_1', getattr(tick, 'bid_price1', 0)) or 0.0)

        if last_price >= last_ask1:
            direction = 1
        elif last_price <= last_bid1:
            direction = -1
        else:
            if last_price >= ask1:
                direction = 1
            elif last_price <= bid1:
                direction = -1
            else:
                direction = 0

        if st['divide_pen_list']:
            last_div = st['divide_pen_list'][-1]
            pre_chg_vol = int(last_div.get('volume_change', 0) or 0)
            pre_chg_hld = int(last_div.get('open_interest_change', 0) or 0)
            pre_t = _calculate_pen_type(pre_chg_vol, pre_chg_hld)
            if pre_t == 2 and pre_t == 4:
                prev_last_price = float(getattr(last_tick, 'last_price', getattr(last_tick, 'price', 0)) or 0.0)
                if last_price > prev_last_price:
                    direction = 1
                elif last_price < prev_last_price:
                    direction = -1
                else:
                    direction = 0

        t = _calculate_pen_type(chg_vol, chg_hld)
        if t == 0:
            direction_desc = '没有变化'
        elif t == 1:
            direction_desc = '多换' if direction == 1 else ('空换' if direction == -1 else '未知')
        elif t == 2:
            direction_desc = '双开'
        elif t == 3:
            direction_desc = '多开' if direction == 1 else ('空开' if direction == -1 else '未知')
        elif t == 4:
            direction_desc = '双平'
        else:
            direction_desc = '空平' if direction == 1 else ('多平' if direction == -1 else '未知')

        return {
            'datetime': getattr(tick, 'datetime', datetime.now()),
            'price': last_price,
            'volume_change': chg_vol,
            'open_interest_change': chg_hld,
            'direction': direction,
            'direction_desc': direction_desc,
        }
    except Exception:
        return None


def _on_divide_pen(context, sym, tick):
    st = context.state.get(sym)
    if not st:
        return
    # 时间回退保护
    try:
        lt = st.get('last_tick')
        if lt is not None:
            cur_ts = getattr(tick, 'datetime', None)
            last_ts = getattr(lt, 'datetime', None)
            if cur_ts and last_ts and cur_ts < last_ts:
                return
    except Exception:
        pass

    pen = _calculate_divide_pen(st, tick)
    st['last_tick'] = tick
    if not pen:
        return
    try:
        price = float(getattr(tick, 'last_price', getattr(tick, 'price', 0)) or 0.0)
        pv = st['map_pens'].get(price, {}) or {}
        if int(pen.get('direction') or 0) == 1:
            bid_volume = int(pv.get('bid_volume', 0) or 0) + int(pen.get('volume_change', 0) or 0)
            pv['bid_volume'] = bid_volume
        elif int(pen.get('direction') or 0) == -1:
            ask_volume = int(pv.get('ask_volume', 0) or 0) + int(pen.get('volume_change', 0) or 0)
            pv['ask_volume'] = ask_volume
        st['map_pens'][price] = pv
        st['divide_pen_list'].append(pen)
        if len(st['divide_pen_list']) > 50:
            st['divide_pen_list'] = st['divide_pen_list'][-50:]
    except Exception:
        pass
