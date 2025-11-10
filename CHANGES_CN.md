# 更改说明（2025-11-08）

本次更新同时覆盖 DeepSeek 版与 Kimi 版（两个 `.min.py` 都已重打包）：

## 重要修复与增强

1) 回调链路与持仓同步（关键）
- 新增回调 on_trade()：以成交事实逐笔增量更新本地持仓 `local_pos`、均价与已实现盈亏；打印 `raw_symbol→resolved, dir/offset, price/vol, local_pos old→new` 日志。
- 新增回调 on_order()：对订单状态做兜底增量更新，统一支持 `PARTTRADED/ALLTRADED/部分成交/全部成交`；按 `order.traded` 与 `context.order_traded_map` 计算 `delta` 后再更新，避免与 on_trade 重复累加。
- 统一符号归一（如 `au2512`→`au2512.SHFE`），以及方向/开平的中英文归一（BUY/SELL/OPEN/CLOSE 与 买/卖/开/平）。
- on_order_status() 保留并增强日志，但不再单一依赖它来更新持仓。

2) 启动与异常时的持仓一致性
- `ALIGN_POS_ON_INIT=True`（默认开）：on_init 启动后对每个标的调用一次平台 `get_pos`，把平台持仓数量对齐到本地视图（仅数量）。
- 引入 `context.order_traded_map`（per-orderid），配合 on_order/on_trade 去重，解决“回调顺序/重复”导致的错误累加。

3) 执行与风控
- 单向模式（默认开 `SINGLE_SIDE_MODE=True`）：已有持仓收到反向信号→先平仓，禁止反向新开；打印“单向模式：持多/持空X，收到sell/buy→执行平仓”。
- 同向加仓（默认开 `ALLOW_SAME_SIDE_PYRAMIDING=True`、方案A）：按 AI 的 `position_size_pct` 计算“目标手数−当前手数”的差额加仓，保留保证金/担保比校验；打印“同向加仓 … 当前→目标/规模”日志。
- 价格规范化：选价→按 tick 对齐→Decimal 量化，杜绝 `decimal.ConversionSyntax` 等异常。
- 执行前检查日志（`exec-check`）：打印 `pos/single_side/same_side/size_pct/tradeability/style`，便于定位“视图空仓/节拍/风控”原因。

4) AI 调用与日志去重
- 任务结果去重：`pending_seq/ai_job_seq/last_consumed_seq`，只消费一次；执行器异常也会清空，避免重复打印同一决策。
- 关闭启动阶段提前触发 AI（`ENABLE_STARTUP_AI_TRIGGER=False`），避免启动期 None 导致的异常；改为 on_bar 触发。
- Prompt 构造前对易为 None 的指标做数值化（ema/macd/rsi/atr 等），避免 `:.2f`/比较运算命中 None。

5) 交易日口径
- 夜盘 `roll@21` 后跳过周末到下一个工作日（周五夜盘→周一），日志中显示 `当前交易日(roll@21)` 已修正。

6) Kimi API/解析
- Kimi 终端点：`https://api.moonshot.cn/v1/chat/completions`。
- 容错 JSON 解析：支持代码块/大括号提取、去尾逗号、Python 布尔/None 转 JSON。

## 新/改配置（默认）
- `SINGLE_SIDE_MODE = True`：禁反向新开。
- `ALLOW_SAME_SIDE_PYRAMIDING = True`：允许同向加仓（差额加仓）。
- `ALIGN_POS_ON_INIT = True`：启动对齐平台持仓到本地。
- `ENABLE_STARTUP_AI_TRIGGER = False`：不在启动阶段立即触发 AI。

## 典型排查路径
- 若出现“有持仓仍反向新开/available≈equity”：
  1) 看 `exec-check` 的 `pos` 是否为 0；
  2) 查是否出现 `on_trade/on_order` 日志（raw→resolved 与 local_pos old→new）；
  3) 若完全无回调，临时将 `USE_PLATFORM_GET_POS=True` 稳定视图，并检查平台实际回调函数名与状态文案是否走到了 on_trade/on_order。

## 打包
- 已重新打包：
  - `gkoudai_au_strategy_autonomous.min.py`
  - `akoudai_kimi_min.py`

---

# 更改说明（2025-11-06）

本文档记录本次针对 Kimi 版与主自主策略的关键改动，方便后续排查与复现。

## 变更摘要
- 加入硬性止损护栏（方向 + 最小间距），并在实际下单前按真实下单价对止损进行“复位”（抵消滑点/盘口对齐/时延带来的偏差）。
- 新增“反手/再入场冷却”，在平仓后一定时间内禁止立即反向开仓，降低抖动和频繁翻单。
- AI 决策触发节拍改为基于 1 分钟 K 线：要求至少 10 根 1m 数据；默认关闭 tick 触发（仍使用 tick 执行与风控）。
- 在震荡（SIDEWAYS）环境下，延长冷却时间与 AI 决策间隔，降低高频抖动。
- 分批止盈（按 R 倍数）逻辑保留：当 AI 提供 scale-out 配置时生效。
- 退出以止损/追踪/时间止盈为主；AI 返回的 profit_target 仅记录，不强制成交。

## 涉及文件
- `gkoudai_au_strategy_autonomous.py`
- `akoudai_kimi_combined.py`
- 重新打包的最小化文件：
  - `gkoudai_au_strategy_autonomous.min.py`
  - `akoudai_kimi_min.py`

## 关键逻辑改动
1) 止损护栏与复位
- 新增辅助方法 `_guard_and_rebase_stop(side, entry_price, sl_in, atr_val, tick_sz)`：
  - 校验止损方向是否正确（多头 SL 必须在入场价下方；空头 SL 必须在入场价上方）。
  - 计算最小止损间距 `min_gap = max(MIN_STOP_TICKS * tick_size, MIN_STOP_ATR_MULT * ATR_1m)`。
  - 以“实际下单价”为基准，根据 AI 给定距离 R 与 `min_gap` 取较大值重算 SL，并按 tick 对齐。
- 在开多/开空下单前调用该方法，将决策中的 `stop_loss` 替换为复位后的值。

2) 反手/再入场冷却
- 在 `on_order_status` 中对“平仓”设置 `reentry_until = now + REENTRY_COOLDOWN_SECS`，在此窗口内拒绝新的反向开仓请求。

3) AI 触发节拍（bar 优先）
- 仅当 1m K 线数量≥ `MIN_1M_BARS_FOR_AI` 才触发 AI 调用。
- 默认关闭 `ENABLE_TICK_TRIGGERED_AI`（tick 上不触发 AI），AI 调用主要在 `on_bar` 节拍触发；tick 仍用于下单价格与风控执行。

4) 震荡环境降频
- `SIDEWAYS` 环境下：`cooldown_minutes` 从 1 提升至 3；`ai_interval_secs` 从 30s 提升至 60s。

## 新增/调整的配置项（默认值）
- `MIN_1M_BARS_FOR_AI = 10`：触发 AI 所需的最少 1m K 线根数。
- `ENABLE_TICK_TRIGGERED_AI = False`：是否允许在 tick 上触发 AI（默认关闭）。
- `MIN_STOP_TICKS = 5`：止损最小 tick 间距。
- `MIN_STOP_ATR_MULT = 0.25`：止损最小 ATR 倍数。
- `REENTRY_COOLDOWN_SECS = 120`：平仓后的反手/再入场冷却秒数。
- `ADAPTIVE_PARAMS['SIDEWAYS']`：`cooldown_minutes = 3`，`ai_interval_secs = 60`。

> 以上参数在两份主文件中保持一致：
> - `gkoudai_au_strategy_autonomous.py`
> - `akoudai_kimi_combined.py`

## 默认行为变化
- 新开仓时，若 AI 给出的止损方向/距离不合理，将被复位到安全范围；不再出现“入场即触发错误方向 SL”的极端情况。
- 平仓后短时间内不允许立刻反手，避免在同一价位附近频繁来回开平。
- AI 决策频率下降，尤其在震荡市里，整体交易更稳健。

## 常用调参指南
- 想提高交易频率：
  - 将 `ENABLE_TICK_TRIGGERED_AI = True`（需保证 1m≥`MIN_1M_BARS_FOR_AI`）。
  - 缩短 `ADAPTIVE_PARAMS['SIDEWAYS']['cooldown_minutes']` 或 `ai_interval_secs`。
- 想更保守：
  - 提高 `MIN_STOP_TICKS` 或 `MIN_STOP_ATR_MULT`。
  - 增大 `REENTRY_COOLDOWN_SECS`。
- 想与 AI 目标更贴近：
  - 可将 `order_price_style` 默认改为 `mid`（成交概率略降）。

## 打包与发布
- 已使用脚本 `scripts/minify_strategy.py --pack` 重新生成：
  - `gkoudai_au_strategy_autonomous.min.py`
  - `akoudai_kimi_min.py`
- 若平台加载的仍是旧版本，重启运行进程或强制热更新，以确保新逻辑生效。

## 回滚与排查建议
- 若行为与预期不符，检查：
  - 运行时加载的是否为最新的 `.min.py` 文件。
  - 配置常量是否被外部覆盖。
  - 盘口对齐/滑点与 ATR 计算是否合理（`tick_size` 与 `ATR_1m` 来源）。
- 如需快速回退：
  - 将 `ENABLE_TICK_TRIGGERED_AI = True` 并恢复旧的冷却/间隔，即可恢复更高频的节拍。

---
最后更新：2025-11-06
