# 口袋量化 Python 策略 API 关键要点（文档摘录）

说明：本文件汇总了当前策略涉及的关键 API（回调函数、交易函数、工具函数与周期枚举），从官方知识库页面抓取并整理，便于对照与排查。

## 回调函数（Callback）
- `on_init(context)`：策略首次启动时调用；应在此订阅所需 K 线。
- `on_start(context)`：每次启动时调用（在 on_init 之后）。
- `on_stop(context)`：策略停止时调用。
- `on_tick(context, tick)`：Tick 级回调（需订阅 Tick）。
- `on_bar(context, bars)`：K 线回调；`bars` 可能是 dict<symbol, bar> 或单个 bar（视平台与订阅方式）。主要交易逻辑通常放在此函数中按周期节拍触发。

## 交易函数（Trade）
- `buy(vt_symbol: str, price: float, volume: float) -> list[str]`
  - 买入开多；返回订单ID列表。
- `sell(vt_symbol: str, price: float, volume: float) -> list[str]`
  - 卖出平多；返回订单ID列表。
- `short(vt_symbol: str, price: float, volume: float) -> list[str]`
  - 卖出开空；返回订单ID列表。
- `cover(vt_symbol: str, price: float, volume: float) -> list[str]`
  - 买入平空；返回订单ID列表。
- `send_order(vt_symbol: str, direction: str, offset: str, price: float, volume: float) -> list[str]`
  - 直接指定 方向/开平/价格/数量 下单。
- `cancel_order(vt_orderid: str) -> None`
- `cancel_all() -> None`
- `send_target_order(vt_symbol: str, target: float) -> None`
  - 设置目标仓位。注意：官方页面示例仅展示 2 个参数（合约、目标仓位）；无需传价格参数。

## 工具函数（Tools / Data）
- `get_pos(vt_symbol: str) -> int`
  - 查询指定合约当前持仓。
- `subscribe(vt_symbol: str, interval: str, count: int = 200, wait_group: bool = False) -> bool`
  - 在 `on_init` 中订阅指定周期的 K 线。
  - `wait_group=True` 时，同频所有 bar 就绪后一次性返回（仅 bar 频率有效）。
- `get_market_data(vt_symbol: str, interval: str) -> ArrayManager`
  - 获取已合成完成的 K 线数据（不含正在合成的当前 K 线）。
- `get_current_bar(vt_symbol: str, interval: str) -> BarData`
  - 获取当前正在合成的 K 线（可用于“正在生成中的这一根”）。
- `query_history(vt_symbol: str, interval_str: str, start: datetime|None = None, end: datetime|None = None, number: int|None = None) -> list[BarData]|list[TickData]`
  - 获取历史数据；注：Tick/秒级无法用 `number` 查询，且只支持查询期货真实合约。
- `get_contract(vt_symbol: str) -> ContractData`
  - 获取合约信息（乘数、交易所等）。

## 周期枚举（Interval）
- 分钟：`1m, 3m, 5m, 10m, 15m, 30m`
- 小时：`1h, 4h`
- 日/周/月/年：`1d, w, month, year`
- 秒：`s, 5s, 10s, 15s, 30s`
- Tick：`tick`

> 注：官方“枚举常量/Interval”明确使用 `1m/5m/15m` 等短写；个别示例中出现 `15min` 的写法，建议以枚举为准（`1m/5m/15m`），避免兼容性问题。

---

## 与当前代码的对照检查

- 订阅周期：
  - 代码：`subscribe(symbol, '1m', KLINE_1M_WINDOW)` 在 `on_init` 中调用，符合文档；使用 `1m` 与枚举一致。
- 获取数据：
  - `get_market_data(symbol, '1m'|'1d')`：OK；返回 `ArrayManager`，用于指标计算。
  - `get_current_bar(symbol, '1m'|'1d')`：OK；仅用于打印最新时间或做合成中的观测。
  - `query_history(symbol, '1m'|'1d', number=N)`：OK；分钟/日级别使用 `number` 合规（Tick/秒级禁止）。
- 下单/目标仓位：
  - `buy/sell/short/cover(symbol, price, volume)`：OK。
  - `send_target_order(symbol, 0)`：OK。文档示例展示的是 2 参数版本（symbol, target），无需价格参数。
- 回调处理：
  - `on_tick` 与 `on_bar` 均已实现。AI 触发改为 1m 节拍后，休市时段（无新 bar）不会触发是预期的；交易时段需确保分钟线推送正常。

## 运行侧注意事项（触发节拍相关）
- 当关闭 tick 触发 AI 时，AI 调用仅依赖 `on_bar` 的 1m 节拍：
  - 休市时段（如 11:30–13:30）无 1m bar 推送 → 不会触发 AI，这是预期行为。
  - 夜盘时段跨日，`get_current_bar('1d')` 显示的 bar 日期可能仍为前一日；已在日志中新增“交易日(roll@21)”提示，仅为显示层辅助。
- 若交易时段内始终没有 `on_bar` 日志（如“✅ 技术指标计算成功 …”）：
  - 首先检查分钟线是否在图表端推进；
  - 其次对照平台实际要求的周期字符串是否必须是 `1m`（而非 `1min`）；
  - 再查看 `on_bar` 的 `sym` 与 `state` 键的匹配（大小写/去后缀/包含关系已在代码中做兼容）。

---
最后更新：2025-11-07
