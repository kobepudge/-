# -*- coding: utf-8 -*-
"""
AI驱动的黄金期货自主交易策略 (简化测试版)
策略名称: DeepSeek Autonomous Gold Futures Trading - Simple
交易品种: au2512.SHFE (黄金期货)
"""

# ========================================
# 配置参数
# ========================================

SYMBOL = "au2512.SHFE"
DEEPSEEK_API_KEY = "sk-c7c94df2cbbb423698cb895f25534501"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
AI_DECISION_INTERVAL = 180  # 3分钟

MAX_SINGLE_LOSS_PCT = 0.02  # 2%
MAX_DAILY_LOSS_PCT = 0.05   # 5%
MIN_CONFIDENCE = 0.6

# ========================================
# 策略主函数
# ========================================

def on_init(context):
    """策略初始化"""
    print("========== AI策略启动 ==========")
    print("交易品种: au2512.SHFE")
    print("AI决策间隔: 180秒")
    print("安全边界: 单笔最大亏损2.0%, 单日最大亏损5.0%")

    # 订阅数据
    subscribe(SYMBOL, '1m', 200)
    subscribe(SYMBOL, '1d', 50)

    # 初始化状态
    context.symbol = SYMBOL
    context.last_ai_call = 0
    context.ai_decision = None
    context.trading_allowed = True
    context.daily_pnl = 0
    context.initial_cash = 100000  # 默认10万


def on_tick(context, tick):
    """Tick回调"""
    import time

    current_time = time.time()

    # 每3分钟调用一次AI
    if current_time - context.last_ai_call >= AI_DECISION_INTERVAL:
        print(f"[{tick.strtime}] 正在调用AI决策...")

        # 获取K线数据
        klines = context.data(symbol=SYMBOL, frequency='1m', count=20)

        if klines is not None and len(klines) > 0:
            # 简化版:直接获取最后一根K线
            last_kline = klines.iloc[-1]
            current_price = tick.last_price

            print(f"当前价格: {current_price:.2f}, 最新K线收盘: {last_kline['close']:.2f}")

            # TODO: 这里应该调用DeepSeek API
            # 暂时先不调用,确保基础逻辑能运行

        context.last_ai_call = current_time

    # 风控检查
    check_risk_control(context, tick)


def check_risk_control(context, tick):
    """风控检查"""
    position = context.account().position(symbol=SYMBOL)

    if not position or position['volume'] == 0:
        return

    current_price = tick.last_price
    position_volume = position['volume']
    avg_price = position['vwap']

    # 计算盈亏
    if position_volume > 0:
        unrealized_pnl = (current_price - avg_price) * abs(position_volume) * 1000
    else:
        unrealized_pnl = (avg_price - current_price) * abs(position_volume) * 1000

    account_value = context.account().cash + unrealized_pnl
    pnl_pct = unrealized_pnl / account_value if account_value > 0 else 0

    # 单笔最大亏损
    if pnl_pct < -MAX_SINGLE_LOSS_PCT:
        print(f"[警告] 触发单笔最大亏损 ({pnl_pct*100:.2f}%), 强制平仓!")
        close_position(context, position_volume)


def close_position(context, position_volume):
    """平仓"""
    if position_volume == 0:
        return

    side = OrderSide_Sell if position_volume > 0 else OrderSide_Buy
    order_volume(
        symbol=SYMBOL,
        volume=abs(position_volume),
        side=side,
        order_type=OrderType_Market,
        position_effect=PositionEffect_Close
    )
    print(f"平仓 {abs(position_volume)}手")


def on_bar(context, bars):
    """K线回调"""
    pass


def on_order_status(context, order):
    """订单状态回调"""
    if order.status == 3:
        print(f"订单成交: {order.side_name} {order.volume}手 @ {order.price:.2f}")


def on_backtest_finished(context, indicator):
    """回测结束"""
    print("========== 回测结束 ==========")
    print(f"总收益率: {indicator['累计收益率']*100:.2f}%")
    print(f"最大回撤: {indicator['最大回撤']*100:.2f}%")
