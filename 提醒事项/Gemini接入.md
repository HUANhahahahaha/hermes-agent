# Gemini 接入指南（走 API key，合规路径）

> 结论：**用 API key，不要用 Gemini 订阅 OAuth**。
> 仓库自带的 `google-gemini-cli` OAuth 登录会在登录前弹警告：Google 认为用
> Gemini CLI 的 OAuth 客户端配合第三方软件属于违反政策，已有用户报告账号被限制
> （见 `hermes_cli/main.py:3842`）。API key 是零风险通道。
> 最后更新：2026-07-28

## 一、配置（3 步）

### 1. 拿 API key
https://aistudio.google.com/apikey → 创建 key（形如 `AIza...`）

### 2. 设环境变量
和 iCloud 一样，加到 Claude Code 网页端的 **Update cloud environment → Environment variables**：
```
GEMINI_API_KEY=你的key
```
（Hermes 也接受 `GOOGLE_API_KEY`。）

### 3. 网络白名单 —— **实测不需要加**
`generativelanguage.googleapis.com` 已在默认 cloud SDKs 白名单内。
2026-07-28 实测：用无效 key 请求返回的是 Google 自己的 `API_KEY_INVALID`
（而非代理的 `Host not in allowlist`），证明该域名本就可达。
若日后真被拦，再去 Allowed domains 补这一行即可。

### 4. 自检
```bash
python3 提醒事项/gemini_check.py          # 默认测 gemini-3.1-pro-preview
GEMINI_CHECK_MODEL=gemini-3.6-flash python3 提醒事项/gemini_check.py
```
三步依次验证：凭据 → 网络 → 真实调用（打印回复与 token 用量）。
任一步失败会直接告诉你是哪一环、怎么修。

### 5. 配置 provider
`~/.hermes/config.yaml`（或 `hermes` 交互式配置）：
```yaml
provider: "gemini"
model: "gemini-3.1-pro-preview"    # Google 当前最强
```

## 二、模型选择

| 模型 | 定位 | 输入/M | 输出/M | 缓存写/M | 缓存读/M |
|---|---|---|---|---|---|
| `gemini-3.1-pro-preview` | **当前最强**（前沿推理） | $2.00 | $12.00 | $0.50 | $0.20 |
| `gemini-3.6-flash` | 快、便宜（2026-07-21 发布） | $1.50 | $7.50 | $0.15 | $0.15 |
| `gemini-3.5-flash` | 上一代 Flash | $1.50 | $9.00 | $0.15 | $0.15 |

- 3.1 Pro 超过 200K 上下文转长文本档：$4/$18。
- **免费额度自 2026-04-01 起只覆盖 Flash / Flash-Lite，Pro 是纯付费。**
- Batch API 全模型五折（24 小时 SLA），适合非实时的批量活。

> 价格来自多个第三方追踪站交叉比对；抓取时 ai.google.dev 在本环境 403，
> **决策前请自行核对官方页** https://ai.google.dev/gemini-api/docs/pricing

## 三、⚠️ 实测修正：思考 token 吃掉了大部分优势

2026-07-28 用真实 key 实跑（任务＝把一条中文提醒解析成 JSON）：

| 模型 | 耗时 | 输入 | 输出 | **思考** | 思考/输出 |
|---|---|---|---|---|---|
| gemini-3.6-flash | 4.3s | 58 | 71 | **943** | 13.3× |
| gemini-3.1-pro-preview | 9.0s | 58 | 66 | **1287** | 19.5× |

**思考 token 占总量 88~91%，且按输出价计费。** 下面第四节最初那版对比
假设 token 对等、完全没算这块，因此严重低估了 Gemini 成本 —— 已作废，
以本节的修正数字为准。

### 修正后的月成本（同一份真实用量）

| 模型 | 有缓存 | 无缓存 | 对比现状 |
|---|---|---|---|
| **Claude Sonnet 5（现状基准）** | **$7.33** | — | — |
| Gemini 3.1 Pro | $8.89 | $17.82 | **更贵** |
| Gemini 3.6 Flash | $4.00 | $11.04 | 省约 45% |

**结论翻转：**
- **Gemini 3.1 Pro 不是省钱选项**，比 Sonnet 5 还贵。要不要用它是
  **能力问题，不是成本问题**。
- **Gemini 3.6 Flash 确实省**，但是约 45%，不是早先说的 86%。
- **延迟差距很大**：Pro 回答一句「你是哪个模型」耗时 54 秒（烧 386 思考
  token）。做微信机器人这种要即时回复的场景，Pro 的延迟是硬伤。

> 采样仅 2 次，思考量随任务复杂度波动很大；Gemini 的隐式缓存行为也与
> Anthropic 不同，实际账单需跑一段时间才准。

### 账号实测状态（key 已验证可用）
- ✅ `gemini-3.6-flash`、`gemini-3.5-flash` 等 Flash 全系可用
- ✅ `gemini-3.1-pro-preview` 可用（首次 429 是**瞬时限流**，非免费额度封锁）
- ❌ `gemini-3-pro-preview` 已下线（404）
- 账号可见 41 个支持 generateContent 的模型

## 四、（已作废）早先的成本对比 — 未计思考 token

从账单反推的月用量：缓存写 1.54M、缓存读 3.68M、输出 0.03M token。
该用量下同一份活各家要花多少：

| 模型 | 月成本 | 相对现状 |
|---|---|---|
| Fable 5 | $24.61 | +233% |
| Opus 5 | $12.31 | +67% |
| **Sonnet 5（现状基准）** | **$7.38** | — |
| Haiku 4.5 | $2.46 | −67% |
| **Gemini 3.1 Pro（最强）** | **$1.90** | **−74%** |
| Gemini 3.6 Flash | $1.03 | −86% |

**关键结论：Gemini 用最强的 3.1 Pro，比 Anthropic 最便宜的 Haiku 4.5 还便宜。**

原因是这个工作负载 **98% 的 token 是输入/缓存类**，而 Gemini 的缓存写只要
$0.50/M，Anthropic Sonnet 5 要 $3.75/M —— 差 7.5 倍，这一项就吃掉了绝大部分差价。

### 该数字的适用边界
- 按 **token 数**等量换算，未计两家 tokenizer 差异（同样文本 token 数会有出入）。
- Gemini 的缓存机制与 Anthropic 不同（显式 context caching + 隐式缓存，
  显式缓存另有**存储时长计费**未计入），实际账单会有偏差。
- 只比价格，**不比质量**。是否够用要你自己跑一轮实际任务判断。

## 四、建议用法
- **主力仍可留 Claude**（你熟悉、效果已验证）。
- **杂活分流 Gemini**：归类、摘要、转写、随手记整理 —— 这些不需要顶级模型，
  用 3.6 Flash 几乎等于白嫖。
- 想全面换 Gemini 3.1 Pro 也完全可行，先跑一周对比质量再决定。
</content>
