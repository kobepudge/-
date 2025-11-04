"""
==============================================================================
黄金期货 AI 高频日内交易策略
==============================================================================

策略名称: 趋势 + 量能驱动的 Tick 级别高频交易
交易品种: au2512.SHFE (黄金期货)
策略类型: AI 趋势判断 + 分时量能突增 + 高频交易
风控规则: 日内交易，收盘前强制平仓，不过夜

核心逻辑:
1. AI 每 3 分钟判断当前是否存在趋势行情（上涨/下跌/震荡）
2. 如果有趋势，监控 Tick 级别的量能变化
3. 当量能突增（超过均值 1.5 倍）时，在趋势方向快速开仓
4. 设置严格的止损止盈（0.5%/1%）
5. 14:55 前强制平掉所有持仓

作者: Claude + User
创建日期: 2025-01-03
版本: v1.0
==============================================================================
"""

import requests
import json
import time
from datetime import datetime, timedelta

# ==============================================================================
# 配置参数
# ==============================================================================

# ============== 交易配置 ==============
SYMBOL = "au2512.SHFE"  # 黄金期货 2025年12月合约
TRADE_VOLUME = 1  # 每次交易手数

# ============== AI 配置 ==============
DEEPSEEK_API_KEY = "sk-c7c94df2cbbb423698cb895f25534501"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
AI_DECISION_INTERVAL = 180  # AI 决策间隔（秒），3 分钟
AI_REQUEST_TIMEOUT = 10  # API 请求超时（秒）

# ============== Tick 执行配置 ==============
TICK_BUFFER_SIZE = 100  # 缓存最近 N 个 tick
VOLUME_SURGE_THRESHOLD = 1.5  # 量能突增阈值（1.5 倍平均值）
VOLUME_CHECK_WINDOW = 20  # 量能统计窗口（最近 N 个 tick）

# ============== 风控配置 ==============
STOP_LOSS_PCT = 0.005  # 止损 0.5%
TAKE_PROFIT_PCT = 0.01  # 止盈 1%
MIN_CONFIDENCE = 60  # AI 最低置信度阈值
MAX_TRADES_PER_DAY = 20  # 每日最大交易次数
FORCE_CLOSE_TIME = "14:55:00"  # 强制平仓时间（日内交易）

# ============== 调试配置 ==============
ENABLE_DEBUG_LOG = True  # 是否启用详细日志
API_CALL_COUNT = 0  # API 调用计数器


# ==============================================================================
# 策略初始化
# ==============================================================================

def on_init(context):
    """策略初始化 - 只在首次启动时调用一次"""

    Log("=" * 80)
    Log("黄金期货 AI 高频日内交易策略 - 初始化")
    Log("=" * 80)

    # ============== 交易参数 ==============
    context.symbol = SYMBOL
    context.trade_volume = TRADE_VOLUME

    # ============== AI 状态 ==============
    context.ai_state = {
        'trend': 'SIDEWAYS',  # UPTREND/DOWNTREND/SIDEWAYS
        'action': 'HOLD',  # BUY/SELL/HOLD
        'entry_zone': [0, 0],  # 入场价位区间
        'stop_loss': 0,
        'take_profit': 0,
        'confidence': 0,
        'reason': '',
        'last_update': 0
    }
    context.last_ai_call_time = 0

    # ============== Tick 数据缓存 ==============
    context.tick_buffer = []
    context.tick_buffer_size = TICK_BUFFER_SIZE

    # ============== 交易统计 ==============
    context.daily_trade_count = 0
    context.entry_price = 0  # 开仓价格
    context.entry_time = None  # 开仓时间

    # ============== 订阅数据 ==============
    # 订阅 1 分钟 K 线（用于 AI 分析分时走势）
    subscribe(context.symbol, "1m", 200, wait_group=False)
    # 订阅日 K 线（用于判断大趋势）
    subscribe(context.symbol, "1d", 50, wait_group=False)

    Log(f"✓ 交易品种: {context.symbol}")
    Log(f"✓ 交易手数: {context.trade_volume}")
    Log(f"✓ AI 决策间隔: {AI_DECISION_INTERVAL} 秒")
    Log(f"✓ 量能突增阈值: {VOLUME_SURGE_THRESHOLD}x")
    Log(f"✓ 止损/止盈: {STOP_LOSS_PCT*100:.1f}% / {TAKE_PROFIT_PCT*100:.1f}%")
    Log(f"✓ 强制平仓时间: {FORCE_CLOSE_TIME}")
    Log("=" * 80)


def on_start(context):
    """策略启动 - 每次启动时调用"""
    Log(f"[启动] 策略启动成功 - 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 恢复持久化数据
    last_ai_state = _G("last_ai_state")
    if last_ai_state:
        Log(f"[恢复] 上次 AI 状态: {last_ai_state}")


def on_stop(context):
    """策略停止"""
    current_pos = get_pos(context.symbol)
    Log(f"[停止] 策略停止 - 当前持仓: {current_pos} 手 | 今日交易次数: {context.daily_trade_count}")

    # 持久化 AI 状态
    _G("last_ai_state", context.ai_state)


# ==============================================================================
# Tick 级别回调（核心交易逻辑）
# ==============================================================================

def on_tick(context, tick):
    """Tick 级别回调 - 每个 tick 都会触发"""

    try:
        # ============== 步骤 1: 缓存 Tick 数据 ==============
        cache_tick_data(context, tick)

        # ============== 步骤 2: 定期调用 AI 更新决策 ==============
        current_time = time.time()
        if should_call_ai(context, current_time):
            update_ai_decision(context, tick)

        # ============== 步骤 3: Tick 执行层（量能驱动）==============
        if context.ai_state['action'] != 'HOLD':
            execute_tick_level_entry(context, tick)

        # ============== 步骤 4: 风控层（实时监控）==============
        risk_control_layer(context, tick)

    except Exception as e:
        Log(f"[错误] on_tick 异常: {str(e)}")


def cache_tick_data(context, tick):
    """缓存 Tick 数据用于量能分析"""
    context.tick_buffer.append({
        'price': tick.last_price,
        'volume': tick.last_volume,
        'time': tick.datetime
    })

    # 保持缓存大小
    if len(context.tick_buffer) > context.tick_buffer_size:
        context.tick_buffer.pop(0)


def should_call_ai(context, current_time):
    """判断是否应该调用 AI"""
    time_since_last_call = current_time - context.last_ai_call_time
    return time_since_last_call >= AI_DECISION_INTERVAL


# ==============================================================================
# AI 决策引擎
# ==============================================================================

def update_ai_decision(context, tick):
    """调用 DeepSeek API 更新 AI 决策"""

    global API_CALL_COUNT
    API_CALL_COUNT += 1

    Log(f"[AI] 第 {API_CALL_COUNT} 次决策 | 价格: {tick.last_price:.2f}")

    try:
        # 1. 收集市场数据
        market_data = collect_market_data(context, tick)

        # 2. 构造 Prompt
        prompt = construct_trading_prompt(market_data)

        # 3. 调用 DeepSeek API
        decision = call_deepseek_api(prompt)

        # 4. 更新 AI 状态
        context.ai_state.update(decision)
        context.ai_state['last_update'] = time.time()
        context.last_ai_call_time = time.time()

        Log(f"[AI] 趋势: {decision['trend']} | 动作: {decision['action']} | 置信度: {decision['confidence']}%")
        Log(f"[AI] 理由: {decision['reason']}")

    except Exception as e:
        Log(f"[AI] 决策失败: {str(e)}")
        # 失败时保持观望
        context.ai_state['action'] = 'HOLD'


def collect_market_data(context, tick):
    """收集市场数据用于 AI 分析"""

    # 获取日 K 线（最近 10 根）
    try:
        daily_bars = query_history(context.symbol, "1d", number=10)
    except:
        daily_bars = []

    # 获取 1 分钟 K 线（最近 60 根）
    try:
        minute_bars = query_history(context.symbol, "1m", number=60)
    except:
        minute_bars = []

    # 获取当前持仓
    current_pos = get_pos(context.symbol)

    # 计算技术指标
    ma20 = 0
    if len(minute_bars) >= 20:
        close_prices = [bar.close_price for bar in minute_bars[-20:]]
        ma20 = sum(close_prices) / len(close_prices)

    # Tick 级别量能统计
    tick_volume_avg = 0
    if len(context.tick_buffer) >= VOLUME_CHECK_WINDOW:
        recent_volumes = [t['volume'] for t in context.tick_buffer[-VOLUME_CHECK_WINDOW:]]
        tick_volume_avg = sum(recent_volumes) / len(recent_volumes)

    return {
        'symbol': context.symbol,
        'current_price': tick.last_price,
        'current_volume': tick.last_volume,
        'tick_volume_avg': tick_volume_avg,
        'daily_bars': daily_bars[-5:] if len(daily_bars) >= 5 else daily_bars,
        'minute_bars': minute_bars[-20:] if len(minute_bars) >= 20 else minute_bars,
        'ma20': ma20,
        'current_position': current_pos,
        'daily_trade_count': context.daily_trade_count
    }


def construct_trading_prompt(data):
    """构造 DeepSeek Prompt"""

    # 格式化日 K 线
    daily_kline_str = "【无日 K 数据】"
    if data['daily_bars']:
        daily_kline_str = ""
        for bar in data['daily_bars']:
            change_pct = 0
            if bar.open_price > 0:
                change_pct = ((bar.close_price - bar.open_price) / bar.open_price) * 100
            daily_kline_str += f"  {bar.datetime.strftime('%m-%d')} | 开:{bar.open_price:.2f} 收:{bar.close_price:.2f} 涨跌:{change_pct:+.2f}% 量:{bar.volume:.0f}\n"

    # 格式化分钟 K 线（最近 10 根）
    minute_kline_str = "【无分钟 K 数据】"
    if data['minute_bars']:
        minute_kline_str = ""
        for bar in data['minute_bars'][-10:]:
            minute_kline_str += f"  {bar.datetime.strftime('%H:%M')} | 收:{bar.close_price:.2f} 量:{bar.volume:.0f}\n"

    # 技术指标
    price_vs_ma = 0
    if data['ma20'] > 0:
        price_vs_ma = ((data['current_price'] - data['ma20']) / data['ma20']) * 100

    prompt = f"""你是专业的黄金期货日内高频交易员，专注于捕捉趋势行情中的量能突增机会。

## 当前市场状态

**交易品种**: {data['symbol']}
**当前价格**: {data['current_price']:.2f}
**最新成交量**: {data['current_volume']:.0f} 手
**Tick 平均量**: {data['tick_volume_avg']:.0f} 手

**日 K 线趋势（最近 5 天）**：
{daily_kline_str}

**分时走势（最近 10 分钟）**：
{minute_kline_str}

**技术指标**：
- 20 周期均线: {data['ma20']:.2f}
- 价格与均线偏离: {price_vs_ma:+.2f}%

**当前状态**：
- 持仓: {data['current_position']} 手
- 今日已交易: {data['daily_trade_count']} 次

---

## 交易规则

1. **趋势判断**: 首先判断当前是否存在明确的趋势行情
   - 上涨趋势（UPTREND）: 日 K 连续上涨 + 价格在均线之上 + 成交量放大
   - 下跌趋势（DOWNTREND）: 日 K 连续下跌 + 价格在均线之下 + 成交量放大
   - 震荡行情（SIDEWAYS）: 无明确方向，价格反复波动

2. **交易策略**:
   - 如果是上涨趋势 → 等待分时量能放大时做多（BUY）
   - 如果是下跌趋势 → 等待分时量能放大时做空（SELL）
   - 如果是震荡行情 → 保持观望（HOLD）

3. **量能确认**:
   - 只在量能突增（超过平均 1.5 倍）时入场
   - 量能突增代表市场力量集中，方向明确

4. **风险控制**:
   - 日内交易，不过夜
   - 止损 0.5%，止盈 1%
   - 每日最大交易 20 次

---

## 决策要求

请基于以上信息，给出你的交易决策。必须以 JSON 格式返回：

{{
  "trend": "UPTREND|DOWNTREND|SIDEWAYS",
  "action": "BUY|SELL|HOLD",
  "entry_zone": [最低价, 最高价],
  "stop_loss": 止损价格,
  "take_profit": 止盈价格,
  "confidence": 0-100,
  "reason": "简要说明你的判断依据（50字以内）"
}}

**重要说明**：
- trend 是趋势判断，action 是交易动作
- 如果 trend 是 SIDEWAYS，action 必须是 HOLD
- entry_zone 是价格区间，用于 Tick 执行层判断入场时机
- confidence < 60 时，action 应该是 HOLD
- 止损止盈价格必须合理（基于当前价格 ±0.5% 和 ±1%）
"""

    return prompt


def call_deepseek_api(prompt):
    """调用 DeepSeek API"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是专业的期货交易员，擅长趋势判断和量能分析。必须严格以 JSON 格式返回决策，不要包含其他文字。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.3,
        "max_tokens": 500
    }

    response = requests.post(
        DEEPSEEK_API_URL,
        headers=headers,
        json=payload,
        timeout=AI_REQUEST_TIMEOUT
    )

    if response.status_code != 200:
        raise Exception(f"API 错误: {response.status_code} - {response.text}")

    result = response.json()
    content = result['choices'][0]['message']['content']

    # 解析决策
    decision = parse_ai_decision(content)

    return decision


def parse_ai_decision(content):
    """解析 AI 返回的决策"""
    import re

    # 提取 JSON（可能包含其他文本）
    json_match = re.search(r'\{[\s\S]*?\}', content)
    if not json_match:
        raise Exception(f"无法解析 JSON: {content}")

    json_str = json_match.group(0)
    decision = json.loads(json_str)

    # 验证必需字段
    required_fields = ['trend', 'action', 'entry_zone', 'stop_loss', 'take_profit', 'confidence', 'reason']
    for field in required_fields:
        if field not in decision:
            raise Exception(f"缺少字段: {field}")

    # 验证 trend
    if decision['trend'] not in ['UPTREND', 'DOWNTREND', 'SIDEWAYS']:
        decision['trend'] = 'SIDEWAYS'
        decision['action'] = 'HOLD'

    # 验证 action
    if decision['action'] not in ['BUY', 'SELL', 'HOLD']:
        decision['action'] = 'HOLD'

    # 如果震荡，强制 HOLD
    if decision['trend'] == 'SIDEWAYS':
        decision['action'] = 'HOLD'

    # 如果置信度低，强制 HOLD
    if decision['confidence'] < MIN_CONFIDENCE:
        decision['action'] = 'HOLD'

    return decision


# ==============================================================================
# Tick 执行层（量能驱动）
# ==============================================================================

def execute_tick_level_entry(context, tick):
    """Tick 级别执行 - 量能突增时入场"""

    # 检查是否超过今日交易次数限制
    if context.daily_trade_count >= MAX_TRADES_PER_DAY:
        return

    # 检查是否已有持仓
    current_pos = get_pos(context.symbol)
    if current_pos != 0:
        return

    # 检查是否在入场价位区间内
    state = context.ai_state
    current_price = tick.last_price

    in_entry_zone = state['entry_zone'][0] <= current_price <= state['entry_zone'][1]
    if not in_entry_zone:
        return

    # 检查量能是否突增
    if not is_volume_surge(context, tick):
        return

    # 执行交易
    if state['action'] == 'BUY':
        Log(f"[开仓] 做多 {context.trade_volume} 手 @ {current_price:.2f} | 量能突增: {tick.last_volume:.0f} 手")
        buy(context.symbol, current_price, context.trade_volume)
        context.entry_price = current_price
        context.entry_time = tick.datetime
        context.daily_trade_count += 1

    elif state['action'] == 'SELL':
        Log(f"[开仓] 做空 {context.trade_volume} 手 @ {current_price:.2f} | 量能突增: {tick.last_volume:.0f} 手")
        short(context.symbol, current_price, context.trade_volume)
        context.entry_price = current_price
        context.entry_time = tick.datetime
        context.daily_trade_count += 1


def is_volume_surge(context, tick):
    """判断是否量能突增"""

    if len(context.tick_buffer) < VOLUME_CHECK_WINDOW:
        return False

    # 计算最近 N 个 tick 的平均成交量
    recent_volumes = [t['volume'] for t in context.tick_buffer[-VOLUME_CHECK_WINDOW:]]
    avg_volume = sum(recent_volumes) / len(recent_volumes)

    # 当前 tick 成交量 > 平均的阈值倍数
    is_surge = tick.last_volume > avg_volume * VOLUME_SURGE_THRESHOLD

    if is_surge and ENABLE_DEBUG_LOG:
        Log(f"[量能] 突增! 当前: {tick.last_volume:.0f} | 平均: {avg_volume:.0f} | 倍数: {tick.last_volume/avg_volume:.2f}x")

    return is_surge


# ==============================================================================
# 风控层（硬编码规则）
# ==============================================================================

def risk_control_layer(context, tick):
    """风控层 - 止损止盈 + 收盘平仓"""

    current_pos = get_pos(context.symbol)
    if current_pos == 0:
        return

    current_price = tick.last_price
    current_time_str = tick.datetime.strftime("%H:%M:%S")

    # ============== 风控 1: 止损止盈 ==============
    check_stop_loss_take_profit(context, tick, current_pos, current_price)

    # ============== 风控 2: 收盘前强制平仓 ==============
    if current_time_str >= FORCE_CLOSE_TIME:
        force_close_before_market_close(context, tick, current_pos, current_price)


def check_stop_loss_take_profit(context, tick, current_pos, current_price):
    """检查止损止盈"""

    if context.entry_price == 0:
        return

    # 计算盈亏比例
    if current_pos > 0:  # 多头
        pnl_pct = (current_price - context.entry_price) / context.entry_price

        if pnl_pct <= -STOP_LOSS_PCT:  # 止损
            Log(f"[止损] 多头止损 @ {current_price:.2f} | 亏损: {pnl_pct*100:.2f}%")
            sell(context.symbol, current_price, abs(current_pos))
            context.ai_state['action'] = 'HOLD'
            context.entry_price = 0

        elif pnl_pct >= TAKE_PROFIT_PCT:  # 止盈
            Log(f"[止盈] 多头止盈 @ {current_price:.2f} | 盈利: {pnl_pct*100:.2f}%")
            sell(context.symbol, current_price, abs(current_pos))
            context.ai_state['action'] = 'HOLD'
            context.entry_price = 0

    elif current_pos < 0:  # 空头
        pnl_pct = (context.entry_price - current_price) / context.entry_price

        if pnl_pct <= -STOP_LOSS_PCT:  # 止损
            Log(f"[止损] 空头止损 @ {current_price:.2f} | 亏损: {pnl_pct*100:.2f}%")
            cover(context.symbol, current_price, abs(current_pos))
            context.ai_state['action'] = 'HOLD'
            context.entry_price = 0

        elif pnl_pct >= TAKE_PROFIT_PCT:  # 止盈
            Log(f"[止盈] 空头止盈 @ {current_price:.2f} | 盈利: {pnl_pct*100:.2f}%")
            cover(context.symbol, current_price, abs(current_pos))
            context.ai_state['action'] = 'HOLD'
            context.entry_price = 0


def force_close_before_market_close(context, tick, current_pos, current_price):
    """收盘前强制平仓"""

    Log(f"[强平] 收盘前强制平仓 @ {current_price:.2f} | 持仓: {current_pos} 手")

    if current_pos > 0:
        sell(context.symbol, current_price, abs(current_pos))
    elif current_pos < 0:
        cover(context.symbol, current_price, abs(current_pos))

    context.ai_state['action'] = 'HOLD'
    context.entry_price = 0


# ==============================================================================
# K 线回调（可选）
# ==============================================================================

def on_bar(context, bars):
    """K 线回调"""
    if ENABLE_DEBUG_LOG:
        for vt_symbol, bar in bars.items():
            Log(f"[K线] {vt_symbol} | {bar.datetime.strftime('%H:%M')} | 收: {bar.close_price:.2f} | 量: {bar.volume:.0f}")


# ==============================================================================
# 交易回报监控
# ==============================================================================

def on_trade(context, trade):
    """成交回报"""
    pnl_info = ""
    if context.entry_price > 0 and trade.offset.value == "平":
        pnl = (trade.price - context.entry_price) if trade.direction.value == "多" else (context.entry_price - trade.price)
        pnl_pct = (pnl / context.entry_price) * 100
        pnl_info = f"| 盈亏: {pnl_pct:+.2f}%"

    Log(f"[成交] {trade.symbol} | {trade.direction.value}{trade.offset.value} | 价:{trade.price:.2f} | 量:{trade.volume:.0f} {pnl_info}")


def on_order(context, order):
    """委托状态更新"""
    if ENABLE_DEBUG_LOG:
        Log(f"[委托] {order.symbol} | {order.status.value} | 已成交:{order.traded}/{order.volume}")


# ==============================================================================
# 工具函数
# ==============================================================================

def Log(message):
    """增强的日志函数"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


# ==============================================================================
# 策略说明
# ==============================================================================

"""
使用说明：

1. 部署到高宽代平台：
   - 复制本文件全部内容
   - 粘贴到高宽代平台的策略编辑器
   - 配置交易参数（品种、K 线周期）
   - 开始回测或运行

2. 参数调整：
   - AI_DECISION_INTERVAL: AI 决策间隔（默认 3 分钟）
   - VOLUME_SURGE_THRESHOLD: 量能突增阈值（默认 1.5 倍）
   - STOP_LOSS_PCT: 止损比例（默认 0.5%）
   - TAKE_PROFIT_PCT: 止盈比例（默认 1%）

3. 监控要点：
   - 观察 AI 决策日志：趋势判断是否准确
   - 观察量能触发日志：是否在关键时刻入场
   - 观察止损止盈日志：风控是否正常触发
   - 观察每日交易次数：是否过度交易

4. 优化方向：
   - 如果趋势判断不准：调整 Prompt，增加更多技术指标
   - 如果交易次数过多：提高量能阈值或 AI 置信度
   - 如果止损频繁：调整止损比例或优化入场时机
   - 如果盈利不足：调整止盈比例或持仓时间

5. 风险提示：
   - 本策略为示例代码，实盘前请充分测试
   - 建议先在 SimNow 模拟盘运行 1-2 周
   - 初期使用小资金，逐步验证效果
   - 密切关注 API 调用成本和延迟

版本历史：
- v1.0 (2025-01-03): 初始版本，支持趋势+量能驱动的高频交易
"""
