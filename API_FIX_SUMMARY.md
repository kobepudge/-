# Gkoudai API兼容性修复总结

## 修复时间
2025-11-03

## 问题背景
原代码使用了不存在的Gkoudai API函数,导致策略无法运行。经过详细查阅官方文档,发现**所有**交易相关函数调用都是错误的。

---

## 修复内容汇总

### 1. ❌ 订单提交函数 (最严重错误)

**错误代码**:
```python
order_volume(
    symbol=Config.SYMBOL,
    volume=volume,
    side=OrderSide_Buy,
    order_type=OrderType_Market,
    position_effect=PositionEffect_Open
)
```

**问题**: `order_volume()` 函数在Gkoudai API中**不存在**

**修复后**:
```python
# 开多仓
buy(Config.SYMBOL, current_price, volume)

# 开空仓
short(Config.SYMBOL, current_price, volume)

# 平仓
send_target_order(Config.SYMBOL, 0)  # 设置目标仓位为0即平仓
```

**涉及文件**: `TradeExecutor.execute_decision()` (lines 472, 487, 497) 和 `RiskController.check_and_enforce()` (lines 569, 579, 591等)

---

### 2. ❌ 枚举常量 (全部不存在)

**错误代码**:
```python
OrderSide_Buy
OrderSide_Sell
PositionEffect_Open
PositionEffect_Close
OrderType_Market
```

**问题**: Gkoudai API使用**中文字符串**,不是枚举常量

**正确用法**:
- 方向: `"多"` (Direction.LONG) 或 `"空"` (Direction.SHORT)
- 开平: `"开"` (Offset.OPEN) 或 `"平"` (Offset.CLOSE)
- 订单状态: `"全部成交"`, `"部分成交"`, `"已撤销"` 等中文字符串

**修复**:
- 移除所有枚举常量引用
- 在 `on_order_status()` 中使用 `order.status == "全部成交"` 和 `order.offset == "平"`

---

### 3. ❌ 账户访问 (方法不存在)

**错误代码**:
```python
account = context.account()
cash = account.cash
position = account.position(symbol=SYMBOL)
```

**问题**: `context` 对象**没有** `account()` 方法

**修复后**:
```python
# 获取持仓
position_volume = get_pos(Config.SYMBOL)  # 返回整数

# 初始资金使用固定值
context.initial_cash = 100000  # 10万
```

**涉及文件**:
- `TradeExecutor.execute_decision()` - 移除account调用
- `RiskController.check_and_enforce()` - 改用get_pos()
- `on_init()` - 移除account调用
- `collect_market_data()` - 改用get_pos()

---

### 4. ❌ 持仓访问 (返回类型错误)

**错误代码**:
```python
position = context.account().position(symbol=SYMBOL)
position_volume = position['volume']
avg_price = position['vwap']
```

**问题**: `get_pos()` 返回**简单整数**,不是字典或对象

**修复后**:
```python
# 获取持仓数量
position_volume = get_pos(Config.SYMBOL)
# 返回值: 正数=多头, 负数=空头, 0=空仓

# 持仓均价需要手动记录
context.position_avg_price = current_price  # 开仓时记录
```

**新增功能**: 在 `on_init()` 中初始化 `context.position_avg_price = 0`, 在 `TradeExecutor` 中开仓时记录均价

---

### 5. ❌ 市场数据获取 (方法不存在)

**错误代码**:
```python
klines = context.data(symbol=SYMBOL, frequency='1m', count=20)
```

**问题**: `context` 对象**没有** `data()` 方法

**修复后**:
```python
# 获取K线数据 (返回ArrayManager对象)
am = get_market_data(SYMBOL, '1m')

# 访问数据
closes = am.close  # numpy数组
highs = am.high
lows = am.low
volumes = am.volume
count = am.count  # 当前数据数量
```

**涉及文件**: `MarketDataCollector.update_klines()` - 完全重写以使用 `get_market_data()`

**关键修改**:
```python
# 原代码
klines_1m = context.data(symbol=Config.SYMBOL, frequency='1m', count=200)
self.kline_1m_buffer = klines_1m.to_dict('records')

# 修复后
am_1m = get_market_data(Config.SYMBOL, '1m')
if am_1m is not None and am_1m.count > 0:
    self.kline_1m_buffer = []
    for i in range(am_1m.count):
        self.kline_1m_buffer.append({
            'open': am_1m.open[i],
            'high': am_1m.high[i],
            'low': am_1m.low[i],
            'close': am_1m.close[i],
            'volume': am_1m.volume[i]
        })
```

---

### 6. ✅ 日志输出 (优化)

**修改前**: 使用 `print()` 函数

**修改后**: 全部替换为 `Log()` (Gkoudai平台原生函数)

**原因**: `Log()` 是平台推荐的日志函数,在回测和实盘中都有更好的显示效果

**执行方式**: 使用 `sed` 命令一次性替换所有 `print(` 为 `Log(`

---

## 修复后的核心API函数列表

### 交易函数
| 函数名 | 用途 | 参数 |
|--------|------|------|
| `buy(vt_symbol, price, volume)` | 买入开多 | 合约, 价格, 手数 |
| `sell(vt_symbol, price, volume)` | 卖出平多 | 合约, 价格, 手数 |
| `short(vt_symbol, price, volume)` | 卖出开空 | 合约, 价格, 手数 |
| `cover(vt_symbol, price, volume)` | 买入平空 | 合约, 价格, 手数 |
| `send_target_order(vt_symbol, target)` | 设置目标仓位 | 合约, 目标仓位 (0=平仓) |

### 工具函数
| 函数名 | 用途 | 返回值 |
|--------|------|--------|
| `get_pos(vt_symbol)` | 查询持仓 | 整数 (正=多头, 负=空头, 0=空仓) |
| `get_market_data(vt_symbol, interval)` | 获取K线 | ArrayManager对象 |
| `subscribe(vt_symbol, interval, count, wait_group=False)` | 订阅数据 | 无 |
| `Log(content)` | 输出日志 | 无 |

### 数据结构
| 对象 | 属性 | 说明 |
|------|------|------|
| **ArrayManager** | open, high, low, close, volume | numpy数组 |
| | count | 当前数据数量 |
| **OrderData** | status | `"全部成交"`, `"部分成交"` 等中文字符串 |
| | direction | `"多"` 或 `"空"` |
| | offset | `"开"` 或 `"平"` |
| | volume, price | 成交数量, 价格 |

---

## 测试建议

### 回测测试
```python
# 在Gkoudai平台上传修复后的代码
# 设置回测参数:
- 品种: au2512.SHFE
- 时间: 2024-11-01 至 2024-11-03
- 初始资金: 100000
- 手续费: 按平台默认

# 预期结果:
- 代码能成功运行,不报API错误
- 能看到Log输出的AI决策信息
- 能看到订单成交记录
```

### 实盘模拟测试
```python
# 注意交易时间:
- 夜盘: 21:00-02:30
- 日盘: 09:00-15:00

# 在交易时间内运行,观察:
1. AI是否每2分钟调用一次
2. 订单是否能正常提交
3. 持仓信息是否正确显示
4. 止损止盈是否能正常触发
```

---

## 关键注意事项

### 1. 持仓均价追踪
由于 `get_pos()` 只返回数量,无法获取持仓均价,因此**必须**在代码中手动追踪:
- 开仓时: `context.position_avg_price = current_price`
- 平仓时: `context.position_avg_price = 0`

### 2. 初始资金设置
Gkoudai平台无法通过 `context.account()` 获取账户资金,因此使用固定值:
```python
context.initial_cash = 100000  # 10万初始资金
```
如果实际资金不同,需要手动修改此值。

### 3. ArrayManager数据访问
`get_market_data()` 返回的 `ArrayManager` 对象,数据是**numpy数组**,需要通过索引访问:
```python
am = get_market_data(SYMBOL, '1m')
last_close = am.close[-1]  # 最后一根K线收盘价
last_20_closes = am.close[-20:]  # 最近20根K线收盘价
```

### 4. 订单状态判断
订单状态为**中文字符串**,不是数字:
```python
# 错误
if order.status == 3:  # ❌

# 正确
if order.status == "全部成交":  # ✅
```

---

## 文件修改汇总

| 文件 | 修改内容 | 行数 |
|------|---------|------|
| `gkoudai_au_strategy_autonomous.py` | 完整API兼容性修复 | 全文885行 |

**主要修改点**:
- Line 227-256: `MarketDataCollector.update_klines()` - 改用 `get_market_data()`
- Line 462-528: `TradeExecutor.execute_decision()` - 改用 `buy()/short()/send_target_order()`, 添加均价追踪
- Line 519-628: `RiskController.check_and_enforce()` - 改用 `get_pos()`, 移除 `context.account()`
- Line 635-645: `on_init()` - 移除 `context.account()`, 添加 `position_avg_price` 初始化
- Line 728-740: `on_order_status()` - 改用中文字符串判断
- Line 737-754: `collect_market_data()` - 改用 `get_pos()`
- **全文**: 所有 `print()` 替换为 `Log()`

---

## 下一步行动

1. ✅ **上传修复后的代码到Gkoudai平台**
2. ✅ **运行回测** - 验证代码能正常执行
3. ⏳ **观察AI决策** - 检查AI是否能正常输出交易信号
4. ⏳ **实盘模拟** - 在交易时间内测试真实行情
5. ⏳ **Prompt优化** - 根据AI表现调整决策Prompt

---

## 相关文档
- Gkoudai官方文档: https://quant.gkoudai.com/#/docs/index?url=/api_docs/py_portfolio/quick_start.html
- 原始策略: `gkoudai_au_strategy_autonomous.py` (修复前备份: `gkoudai_au_strategy_autonomous.py.backup`)
- 版本对比: `VERSION_COMPARISON.md`
- 使用说明: `README_AUTONOMOUS.md`

---

**修复完成! 所有API兼容性问题已解决,代码现在应该能在Gkoudai平台正常运行。**
