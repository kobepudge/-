# Release Notes - v1.0 (lianghua-1.0)

**发布日期**: 2025-11-04
**版本代号**: lianghua-1.0
**仓库**: https://github.com/kobepudge/-.git

---

## 🎉 版本亮点

这是**AI驱动的多标的期货交易策略**的第一个正式版本，实现了完整的自主交易能力。

### 核心创新

1. **AI完全自主决策** - 最小化人为规则，让DeepSeek AI自主判断市场
2. **多标的并行交易** - 同时管理黄金(au2512)和碳酸锂(lc2601)
3. **高级市场微结构分析** - 流动性评分、微价格、五档深度不平衡
4. **智能自适应仓位** - 根据流动性、可交易性、信心度动态调整
5. **秒级启动优化** - 主动加载历史数据，无需等待

---

## 📊 完整功能列表

### 🤖 AI决策引擎

- ✅ DeepSeek Chat 集成 (temperature=0.7, max_tokens=10000)
- ✅ 1分钟决策频率 (可配置)
- ✅ 完整市场数据Prompt (价格、量能、盘口、技术指标、持仓、账户)
- ✅ 标准化JSON决策格式
- ✅ 自主风险评估与止损止盈设计
- ✅ 可交易性自评分 (tradeability_score)
- ✅ 冷却期机制 (cooldown_minutes)

### 📈 技术分析系统

**5分钟趋势聚合**:
- 1分钟K线收集 (300根)
- 5分钟K线聚合 (60根)
- 过滤高频噪音，保留真实趋势

**技术指标**:
- EMA20/EMA60 (基于5分钟，观察窗口100/300分钟)
- MACD (12, 26, 9)
- RSI (14)
- ATR (14)
- 量能比与量能状态识别

**盘口分析**:
- 买卖价差 (Spread)
- 中间价 (Mid Price)
- 微价格 (Microprice) - 量加权中间价
- L1/L5 深度不平衡
- 五档累积深度
- 流动性评分与状态 (THIN/NORMAL/THICK)

### 💰 仓位与资金管理

**智能仓位计算**:
- 基于AI信心度 (0.6-0.7: 50%, 0.7-0.9: 70%, 0.9-1.0: 100%)
- 基于流动性状态 (THIN: 最多30%, NORMAL: 正常, THICK: 正常)
- 基于可交易性评分 (score<0.5: 拒绝, <0.7: 最多30%)
- 基于点差 (spread/mid>0.1%: 最多30%)

**保证金与风控**:
- 真实保证金率查询 (长/短仓分别计算)
- 账户权益/可用资金实时计算
- 担保比校验 (equity/margin_used ≥ 1.3)
- 新仓安全系数 (1.05x margin buffer)

**订单价格优化**:
- `best`: 最优价 (买用ask, 卖用bid)
- `mid`: 中间价
- `market`: 最新价
- `limit`: 限价单 (可配置offset)

### 🛡️ 风控系统

**硬性约束**:
- 单笔最大亏损: 2% (可配置)
- 单日最大亏损: 5% (可配置)
- 强制平仓时间: 14:55 (可配置)
- 最小AI信心度: 0.6 (可配置)

**AI动态止损止盈**:
- 根据技术位、ATR、风险收益比自主设计
- 实时监控触发
- 支持动态调整 (adjust_stop)

**失效条件监控**:
- AI主动判断交易逻辑失效条件
- 自动触发平仓

### 🔧 平台兼容性

**PlatformAdapter** - 自动适配不同平台:
- 合约信息查询 (size, pricetick, min_volume)
- 保证金率查询 (long/short)
- 账户信息查询 (balance, available, margin, equity)
- 兜底配置 (当API不可用时)

**多字段兼容**:
- Tick字段: `last_price/price`, `bid_price_1/bid_price1/bid_price`
- Volume字段: `last_volume/volume`, `bid_volume_1/bid_volume1`
- Time字段: `strtime/datetime`

### 🚀 性能优化

**启动优化**:
- 主动调用 `query_history()` 回填300根1分钟K线
- 启动后1秒即可进行AI决策 (vs 原5分钟等待)
- 失败时自动退化到逐步累积模式

**多标的并行**:
- 独立状态管理 (`context.state[symbol]`)
- 独立数据收集器
- 独立AI决策时间
- 按标的分别风控

**调试增强**:
- 详细日志 (标的前缀, 关键事件标记)
- 异常捕获与堆栈输出
- 未触发AI原因定期输出

---

## 📁 文件结构

### 核心代码
```
gkoudai_au_strategy_autonomous.py  (1472行)
├── Config                         # 配置类
├── construct_autonomous_trading_prompt()  # AI Prompt构造
├── MarketDataCollector           # 市场数据收集
│   ├── add_tick()
│   ├── update_klines()
│   ├── calculate_indicators()
│   └── _aggregate_to_5min()      # 5分钟聚合
├── AIDecisionEngine              # AI决策引擎
│   └── call_deepseek_api()
├── TradeExecutor                 # 交易执行
│   └── execute_decision()
├── RiskController                # 风控
│   └── check_and_enforce()
├── PlatformAdapter               # 平台适配器
│   ├── get_contract()
│   ├── get_account()
│   ├── get_contract_size()
│   ├── get_pricetick()
│   ├── get_min_volume()
│   ├── get_margin_ratio()
│   └── get_account_snapshot()
├── on_init()                     # 初始化
├── on_start()                    # 启动回调
├── on_tick()                     # Tick回调
├── on_bar()                      # K线回调
├── on_order_status()             # 订单回调
├── on_backtest_finished()        # 回测结束
└── collect_market_data()         # 数据收集
```

### 文档
```
README.md                         # 主文档
README_AUTONOMOUS.md              # AI自主策略说明
API_FIX_SUMMARY.md               # API修复总结
DEPLOYMENT_GUIDE.md              # 部署指南
QUICK_FIX_SUMMARY.md             # 快速修复
PRE_DEPLOYMENT_CHECKLIST.md     # 部署检查清单
5MIN_AGGREGATION_SUMMARY.md     # 5分钟聚合实现
LATEST_UPDATE_SUMMARY.md         # 最新更新
QUICK_REFERENCE.md               # 快速参考
STARTUP_OPTIMIZATION.md          # 启动优化
VERSION_COMPARISON.md            # 版本对比
GIT_PUSH_GUIDE.md               # Git推送指南
RELEASE_NOTES_v1.0.md           # 本文档
```

### 配置与版本
```
VERSION                          # 版本号: lianghua-1.0
.gitignore                       # Git忽略规则
config_template.py               # 配置模板
```

---

## 🔧 配置说明

### 必需配置

```python
class Config:
    # 交易标的
    SYMBOL = "au2512.SHFE"
    SYMBOLS = ["au2512.SHFE", "lc2601.GFEX"]

    # AI配置
    DEEPSEEK_API_KEY = "sk-your-api-key-here"

    # 合约乘数 (用于仓位计算)
    CONTRACT_MULTIPLIER = {
        "au2512.SHFE": 1000,  # 1000克/手
        "lc2601.GFEX": 5      # 5吨/手
    }
```

### 可选配置

```python
    # AI决策频率
    AI_DECISION_INTERVAL = 60  # 秒

    # 风控参数
    MAX_SINGLE_TRADE_LOSS_PCT = 0.02   # 2%
    MAX_DAILY_LOSS_PCT = 0.05          # 5%
    FORCE_CLOSE_TIME = "14:55:00"
    MIN_AI_CONFIDENCE = 0.6

    # 数据窗口
    KLINE_1M_WINDOW = 300  # 1分钟K线
    KLINE_1D_WINDOW = 50   # 日K线
    DEPTH_LIQ_WINDOW = 120 # 盘口深度统计窗口

    # 资金管理
    NEW_TRADE_MARGIN_BUFFER = 1.05  # 保证金安全系数
    MIN_GUARANTEE_RATIO = 1.3       # 最低担保比
```

---

## 📊 性能预期

| 指标 | 预期值 | 说明 |
|------|--------|------|
| **启动时间** | 1-3秒 | 主动加载历史数据 |
| **AI调用频率** | 60次/小时 | 每分钟1次 |
| **日均交易次数** | 3-8次/标的 | 高信心度(>0.6)才入场 |
| **目标胜率** | 55-65% | 依赖AI质量 |
| **目标盈亏比** | 2:1 - 3:1 | AI动态设计 |
| **API成本** | ~¥0.02/天 | DeepSeek: 60次×2标的×¥0.00014 |
| **单笔风险** | ≤2% | 硬性约束 |
| **单日风险** | ≤5% | 硬性约束 |

---

## ⚠️ 已知限制

1. **平台依赖**: 需要Gkoudai平台或兼容API
2. **网络要求**: 需要稳定访问DeepSeek API (可能需要代理)
3. **数据要求**: 需要至少300根1分钟历史K线
4. **交易时间**: 夜盘21:00-02:30, 日盘09:00-15:00 (非交易时间会报错)
5. **持仓均价**: 需要手动追踪 (get_pos()不返回均价)
6. **初始资金**: 默认10万，无法自动获取账户余额 (需手动配置)

---

## 🐛 已修复问题

### v1.0 修复列表

1. ✅ API兼容性 (order_volume → buy/sell/short/cover/send_target_order)
2. ✅ 枚举常量 (移除不存在的OrderSide_*, OrderType_*, PositionEffect_*)
3. ✅ 账户访问 (context.account() → PlatformAdapter.get_account())
4. ✅ 市场数据 (context.data() → get_market_data())
5. ✅ 日志函数 (print() → Log())
6. ✅ 订阅格式 (移除wait_group参数)
7. ✅ 合约代码 (支持多标的)
8. ✅ on_start()缺失 (新增数据验证)
9. ✅ 启动等待时间过长 (5分钟 → 1秒)
10. ✅ DeepSeek API超时 (10秒 → 30秒)
11. ✅ 仓位计算不准确 (改用真实保证金率)
12. ✅ 流动性未考虑 (新增流动性分析与gating)

---

## 🚀 部署步骤

### 1. 上传代码
```
1. 复制 gkoudai_au_strategy_autonomous.py 全部内容
2. 登录Gkoudai平台 → 新建策略
3. 粘贴代码并保存
```

### 2. 配置参数
```python
# 修改 Config 类中的必需参数:
DEEPSEEK_API_KEY = "sk-your-real-api-key"
SYMBOLS = ["au2512.SHFE", "lc2601.GFEX"]  # 根据需要调整
```

### 3. 测试验证

**回测测试**:
```
品种: au2512.SHFE
时间: 最近3天
初始资金: 100000
```

**模拟盘测试**:
```
观察1-3天
检查: AI决策合理性, 订单成交, 止损止盈触发
```

### 4. 实盘运行

**小资金测试**: 1-2万测试3-5天
**正式运行**: 确认稳定后逐步加仓

---

## 📈 升级计划

### v1.1 (计划中)

- [ ] 实盘回测数据分析与优化
- [ ] Prompt版本迭代 (根据实际表现)
- [ ] 多周期确认机制 (1分钟+5分钟+15分钟)
- [ ] 动态参数调整 (根据市场状态)
- [ ] 交易日志与统计报表

### v2.0 (远期)

- [ ] 更多标的支持 (股指、商品、贵金属)
- [ ] 组合优化 (跨品种对冲)
- [ ] 机器学习增强 (特征工程、模型训练)
- [ ] 实时监控看板
- [ ] 移动端推送

---

## 🙏 致谢

- **DeepSeek**: 提供强大的AI决策能力
- **Gkoudai**: 提供期货交易平台和API
- **Claude Code**: 协助开发与调试

---

## 📞 支持与反馈

### 问题反馈

如遇到问题，请提供:
1. 完整日志截图
2. 配置参数 (脱敏后)
3. 运行环境 (回测/模拟盘/实盘)
4. 预期行为 vs 实际行为

### 文档

- **快速开始**: QUICK_REFERENCE.md
- **详细说明**: README_AUTONOMOUS.md
- **部署指南**: DEPLOYMENT_GUIDE.md
- **API修复**: API_FIX_SUMMARY.md

---

## ⚖️ 免责声明

**本策略仅供学习和研究使用，不构成任何投资建议。**

- ⚠️ 期货交易具有高风险，可能导致本金全部损失
- ⚠️ 过往表现不代表未来收益
- ⚠️ 使用本策略造成的任何损失，开发者不承担责任
- ⚠️ 请在充分了解风险的前提下谨慎使用
- ⚠️ 建议从小资金、模拟盘开始测试

---

## 📜 许可证

待定 (建议使用 MIT License)

---

**发布完成! 🎉**

**版本**: lianghua-1.0
**发布日期**: 2025-11-04
**状态**: ✅ Ready for Production (需谨慎测试)
**仓库**: https://github.com/kobepudge/-.git
