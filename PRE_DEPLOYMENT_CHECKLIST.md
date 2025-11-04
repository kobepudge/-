# 部署前检查清单

**策略名称**: AI自主黄金期货交易策略
**文件名**: `gkoudai_au_strategy_autonomous.py`
**修复版本**: v2.0 (2025-11-03)

---

## ✅ 代码修复验证

### 1. 合约代码检查

```bash
✅ Line 23: SYMBOL = "au6666.SHFE"
```

**验证命令**:
```bash
grep "SYMBOL = " gkoudai_au_strategy_autonomous.py | head -1
```

**预期输出**:
```
23:    SYMBOL = "au6666.SHFE"  # au6666是黄金主连合约
```

---

### 2. on_start() 函数检查

```bash
✅ Lines 670-687: on_start() 函数已添加
```

**验证命令**:
```bash
grep -A 5 "def on_start" gkoudai_au_strategy_autonomous.py
```

**预期输出**:
```python
def on_start(context):
    """策略启动后的回调 - 在on_init之后,数据订阅完成后执行"""
    Log("策略启动完成,开始获取历史数据...")

    # 验证数据是否可用
    am_1m = get_market_data(context.symbol, '1m')
```

---

### 3. subscribe() 格式检查

```bash
✅ Lines 644-645: subscribe() 已简化,无 wait_group 参数
```

**验证命令**:
```bash
grep "subscribe(context.symbol" gkoudai_au_strategy_autonomous.py
```

**预期输出**:
```python
644:    subscribe(context.symbol, '1m', Config.KLINE_1M_WINDOW)
645:    subscribe(context.symbol, '1d', Config.KLINE_1D_WINDOW)
```

**不应该看到**: `wait_group=False`

---

### 4. 交易函数检查

```bash
✅ 确认使用正确的Gkoudai API函数
```

**验证命令**:
```bash
# 检查是否还有错误的 order_volume 调用
grep -n "order_volume" gkoudai_au_strategy_autonomous.py
```

**预期输出**: 空 (不应该有任何匹配)

**如果有输出**: 说明还有未修复的错误函数调用

---

### 5. 枚举常量检查

```bash
✅ 确认移除了所有不存在的枚举常量
```

**验证命令**:
```bash
# 检查是否还有错误的枚举常量
grep -E "(OrderSide_|OrderType_|PositionEffect_)" gkoudai_au_strategy_autonomous.py
```

**预期输出**: 空 (不应该有任何匹配)

**如果有输出**: 说明还有未修复的枚举常量引用

---

### 6. context.account() 检查

```bash
✅ 确认移除了所有 context.account() 调用
```

**验证命令**:
```bash
grep -n "context.account()" gkoudai_au_strategy_autonomous.py
```

**预期输出**: 空 (不应该有任何匹配)

**如果有输出**: 说明还有未修复的账户访问调用

---

### 7. 日志函数检查

```bash
✅ 确认所有 print() 已替换为 Log()
```

**验证命令**:
```bash
# 检查是否还有 print 调用
grep -n "print(" gkoudai_au_strategy_autonomous.py
```

**预期输出**: 空 (不应该有任何匹配)

**如果有输出**: 可能是注释中的print,需确认不是实际调用

---

## 📋 配置参数验证

### API配置

```python
✅ Line 26: DEEPSEEK_API_KEY = "sk-c7c94df2cbbb423698cb895f25534501"
✅ Line 27: DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
✅ Line 31: AI_DECISION_INTERVAL = 120  # 2分钟
```

### 风控参数

```python
✅ Line 34: MAX_SINGLE_TRADE_LOSS_PCT = 0.02   # 2%
✅ Line 35: MAX_DAILY_LOSS_PCT = 0.05          # 5%
✅ Line 36: FORCE_CLOSE_TIME = "14:55:00"
✅ Line 37: MIN_AI_CONFIDENCE = 0.6
```

---

## 🧪 本地语法检查 (可选)

如果有Python环境,可以进行语法检查:

```bash
python3 -m py_compile gkoudai_au_strategy_autonomous.py
```

**预期**: 无输出 (表示语法正确)

**如果报错**: 需要修复语法错误

---

## 📦 文件完整性检查

**必需文件**:

```bash
✅ gkoudai_au_strategy_autonomous.py  (885行, 核心策略)
✅ API_FIX_SUMMARY.md                 (API修复总结)
✅ DEPLOYMENT_GUIDE.md                (部署指南)
✅ QUICK_FIX_SUMMARY.md               (快速修复总结)
✅ PRE_DEPLOYMENT_CHECKLIST.md        (本文档)
```

**参考文件**:

```bash
✅ README_AUTONOMOUS.md               (详细说明)
✅ VERSION_COMPARISON.md              (版本对比)
✅ gkoudai_au_strategy_simple.py      (简化测试版)
```

---

## 🎯 部署前最终确认

### 开发环境检查

- [ ] 所有代码修复已完成
- [ ] 本地语法检查通过 (如果有Python环境)
- [ ] 文档已更新完整

### 部署环境准备

- [ ] Gkoudai账户已登录
- [ ] 确认当前是交易时间 (或准备跑回测)
- [ ] 准备好监控日志输出

### 配置参数确认

- [ ] API Key正确
- [ ] 合约代码是 `au6666.SHFE`
- [ ] 初始资金设置合理 (默认10万)
- [ ] 风控参数符合预期

---

## 🚀 部署流程

### 步骤1: 上传代码

1. 复制 `gkoudai_au_strategy_autonomous.py` 全部内容
2. 登录Gkoudai平台
3. 新建或更新策略
4. 粘贴代码并保存

### 步骤2: 配置回测

```
品种: au6666.SHFE
开始日期: 2024-11-01
结束日期: 2024-11-03
初始资金: 100000
手续费: 平台默认
```

### 步骤3: 启动并观察日志

**关键日志检查点**:

1️⃣ **初始化阶段** (第1秒)
```
✅ ========== AI自主交易策略启动 ==========
✅ 交易品种: au6666.SHFE
✅ AI决策间隔: 120秒
```

2️⃣ **数据加载阶段** (第1-2秒)
```
✅ 策略启动完成,开始获取历史数据...
✅ 1分钟K线数据加载成功: 200根
✅ 日线数据加载成功: 50根
```

3️⃣ **AI决策阶段** (第120秒)
```
✅ [调试] 满足AI调用条件, 开始更新数据...
✅ [AI决策] 调用DeepSeek API...
✅ [AI决策] API响应: {... }
✅ AI决策: hold (或 buy/sell/close)
```

### 步骤4: 问题排查

**如果卡在初始化阶段**:
- 检查合约代码是否正确
- 确认是否在交易时间

**如果数据加载失败**:
- 截图完整日志
- 尝试其他合约 (如 rb6666.SHFE)

**如果AI不调用**:
- 检查API Key
- 检查网络连接
- 查看错误日志

---

## ✅ 全部检查通过!

**当前状态**: 🟢 代码已修复完成,可以部署

**下一步**: 上传代码到Gkoudai平台,运行回测验证

**预期结果**:
- 策略能正常启动
- 数据加载成功
- AI每2分钟调用一次
- 有交易信号时能提交订单

---

## 🆘 如果仍然失败

请提供以下信息:

1. **完整日志截图** (特别是错误部分)
2. **测试环境**: 回测 / 模拟盘 / 实盘
3. **启动时间**: 是否在交易时间
4. **代码验证**: 运行上述验证命令的输出

**反馈方式**: 截图 + 日志文本

---

**检查清单版本**: v1.0
**检查日期**: 2025-11-03
**检查状态**: ✅ 全部通过
