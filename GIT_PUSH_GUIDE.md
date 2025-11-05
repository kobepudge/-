# Git 推送指南

## ✅ 当前状态

**本地仓库**: 已初始化并提交
**远程仓库**: https://github.com/kobepudge/-.git (已配置但未推送)
**当前分支**: main
**最新提交**: 3d303cd - Release v1.0: 多标的AI自主交易策略

---

## 📦 本次版本内容

### 版本号: lianghua-1.0

### 核心功能
- ✅ **多标的支持** (au2512.SHFE + lc2601.GFEX)
- ✅ **5分钟趋势聚合分析** (1分钟数据收集 → 5分钟技术指标)
- ✅ **AI决策频率优化** (1分钟/次)
- ✅ **启动优化** (主动加载历史数据, 秒级启动)
- ✅ **高级盘口分析** (流动性评分, 微价格, 五档深度)
- ✅ **智能仓位管理** (基于流动性与可交易性自适应)
- ✅ **完整风控系统** (单笔/单日亏损限制, 强制平仓, AI止损止盈)

### 技术亮点
- **平台适配器** (PlatformAdapter) - 自动兼容不同平台API
- **账户权益实时计算**
- **保证金率动态查询**
- **担保比校验** (MIN_GUARANTEE_RATIO)
- **冷却期机制** (防止频繁开仓)

### 文件统计
- **核心代码**: gkoudai_au_strategy_autonomous.py (1472行)
- **文档**: 11个 Markdown 文件
- **总提交**: 2个 commits

---

## ⚠️ 推送失败原因

```
fatal: unable to access 'https://github.com/kobepudge/-.git/':
Failed to connect to github.com port 443 after 75001 ms: Couldn't connect to server
```

**可能原因**:
1. ❌ 网络连接问题 (无法访问 GitHub)
2. ❌ 需要配置代理
3. ❌ 防火墙或网络限制

---

## 🔧 解决方案

### 方案1: 配置代理 (如果有代理服务器)

```bash
# 设置 HTTP 代理
git config --global http.proxy http://proxy.example.com:8080
git config --global https.proxy https://proxy.example.com:8080

# 或使用 SOCKS5 代理
git config --global http.proxy socks5://127.0.0.1:1080
git config --global https.proxy socks5://127.0.0.1:1080

# 然后重新推送
git push -u origin main
```

### 方案2: 使用 SSH 而非 HTTPS

```bash
# 1. 先生成 SSH 密钥 (如果还没有)
ssh-keygen -t ed25519 -C "your_email@example.com"

# 2. 复制公钥到 GitHub
cat ~/.ssh/id_ed25519.pub
# 然后到 GitHub Settings → SSH and GPG keys → New SSH key 粘贴

# 3. 修改远程仓库地址
git remote set-url origin git@github.com:kobepudge/-.git

# 4. 推送
git push -u origin main
```

### 方案3: 等待网络恢复后推送

```bash
# 直接重新推送
cd "/Users/caifang/Downloads/纯量化方向"
git push -u origin main
```

### 方案4: 使用 GitHub Desktop 或其他 GUI 工具

下载并安装 [GitHub Desktop](https://desktop.github.com/)，通过图形界面推送。

### 方案5: 手动上传到 GitHub

1. 打包本地代码:
```bash
cd "/Users/caifang/Downloads/纯量化方向"
tar -czf lianghua-v1.0.tar.gz *.py *.md VERSION .gitignore
```

2. 登录 GitHub → 进入仓库 → Upload files → 选择文件上传

---

## 📋 推送验证步骤

推送成功后，执行以下命令验证:

```bash
# 1. 检查远程分支
git branch -a

# 2. 查看推送日志
git log --oneline origin/main -5

# 3. 验证远程仓库
git remote show origin
```

**预期输出**:
```
* remote origin
  Fetch URL: https://github.com/kobepudge/-.git
  Push  URL: https://github.com/kobepudge/-.git
  HEAD branch: main
  Remote branch:
    main tracked
  Local branch configured for 'git pull':
    main merges with remote main
  Local ref configured for 'git push':
    main pushes to main (up to date)
```

---

## 🚀 后续推送流程

当代码有更新时，使用以下命令推送:

```bash
# 1. 添加修改
git add -A

# 2. 提交
git commit -m "你的提交信息"

# 3. 推送
git push
```

---

## 📂 当前仓库结构

```
纯量化方向/
├── .git/                              # Git 仓库元数据
├── .gitignore                         # Git 忽略规则
├── VERSION                            # 版本号文件
├── gkoudai_au_strategy_autonomous.py  # 核心策略 (1472行)
├── gkoudai_au_strategy.py             # 旧版策略
├── gkoudai_au_strategy_simple.py      # 简化测试版
├── config_template.py                 # 配置模板
├── README.md                          # 主文档
├── README_AUTONOMOUS.md               # AI自主策略说明
├── API_FIX_SUMMARY.md                 # API修复总结
├── DEPLOYMENT_GUIDE.md                # 部署指南
├── QUICK_FIX_SUMMARY.md               # 快速修复总结
├── PRE_DEPLOYMENT_CHECKLIST.md        # 部署前检查
├── 5MIN_AGGREGATION_SUMMARY.md        # 5分钟聚合实现
├── LATEST_UPDATE_SUMMARY.md           # 最新更新总结
├── QUICK_REFERENCE.md                 # 快速参考
├── STARTUP_OPTIMIZATION.md            # 启动优化说明
├── VERSION_COMPARISON.md              # 版本对比
└── GIT_PUSH_GUIDE.md                  # 本文档
```

---

## 🔍 常见问题

### Q1: 推送时要求输入用户名和密码?

**A**: GitHub 已经禁用密码认证，需要使用个人访问令牌 (Personal Access Token):

1. 登录 GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token → 勾选 `repo` 权限
3. 复制生成的 token
4. 推送时，用户名输入 GitHub 用户名，密码输入 token

### Q2: 如何查看当前配置的远程仓库?

```bash
git remote -v
```

### Q3: 如何查看本地提交历史?

```bash
git log --oneline --graph --all -10
```

### Q4: 如何撤销最后一次提交 (但保留修改)?

```bash
git reset --soft HEAD~1
```

---

## ✅ 检查清单

推送前确认:
- [ ] 本地代码已测试通过
- [ ] 敏感信息已移除 (API Key, 密码等)
- [ ] .gitignore 已正确配置
- [ ] 提交信息清晰明了
- [ ] 网络连接正常

推送后确认:
- [ ] GitHub 仓库能看到最新提交
- [ ] 文件完整无丢失
- [ ] 版本号与本地一致

---

## 📞 需要帮助?

如果遇到其他问题:
1. 检查网络连接: `ping github.com`
2. 查看 Git 配置: `git config --list`
3. 查看详细错误: `git push -v`
4. 截图错误信息并反馈

---

**文档版本**: v1.0
**创建日期**: 2025-11-04
**仓库地址**: https://github.com/kobepudge/-.git
**当前状态**: ⏳ 等待推送 (本地已提交)
