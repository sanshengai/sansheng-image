# 生图服务与报错

后端适配层：`scripts/stylebook/backends/`。默认首选 **Codex 内置生图**，走 ChatGPT / Codex 订阅额度，不按张付费，不需要 API Key。

## Codex CLI 适配（当前会话没有内置生图工具时）

- 条件：本机装了 `codex` 命令且已用 ChatGPT 账号登录（`codex login status`）。`sb.py make`、`generate`、`batch` 在没有指定服务时自动使用它。
- 做法：适配层无人值守地调用 `codex exec`，把编译好的完整提示词原样交给 Codex 的内置生图工具，参考图用 `-i` 传入（必须放在提示词之后），并明确要求目标画幅（16:9、2.35:1、3:4 等都实测可用，像素大小是近似值，导出时再裁切缩放）。
- 已知限制：**不能指定模型版本**（工具没有 model 参数；实际多为 Image 2，官方说 2.5 已上线但社区实测仍是 2）；**不支持透明底**；受订阅额度限制，额度用完会报 `quota`。编译输出里的 `model: gpt-image-2` 只是提示词方言，不是运行模型的证明。
- 速度与额度：单张约 60–120 秒（并行 4 路互不干扰），一张约 1.7–3 万 token 额度。默认用低推理强度（`SANSHENG_IMAGE_CODEX_EFFORT` 可改），比默认省约四成额度、耗时不变。
- 日志：`logs/cost.jsonl` 记 provider、耗时和 tokens；费用为空（订阅额度）。

### Codex 不可用时

默认**不会**自动改用按张付费的服务。想允许，在 `~/.config/sansheng-image/config.json` 写 `"allow_paid_fallback": true`，并可用 `"paid_fallback_cap_usd"` 设当天估算花费上限（默认 5 美元）；达到上限后不再退。自动退到付费服务时，日志里会有一条 `fallback_to` 记录。用户明确指定了 `--provider` 时不做自动备用。

## 当前会话直接生图（有内置生图工具时优先）

`sb.py make brief.json -o 目录 --prepare` 只编译并输出 `prepared.json`，由当前会话直接调用内置生图工具，再用 `--import-results` 导回；格式见 [quick.md](quick.md)。即使本机有 `codex` 命令，也使用当前会话的工具，不再启动另一个 Codex。上述 CLI 登录、子进程参数及日志说明仅适用于 CLI 适配路径；直接生图以实际工具返回为准，未知模型、费用和耗时保持未知。

## 外部服务

| 服务 | 环境变量 | 默认模型 | 参考图 | 实测 |
|---|---|---|---|---|
| Codex 内置生图 | 无（`codex login`） | codex-builtin | 支持 | 已实测 |
| OpenAI 及兼容中转 | `OPENAI_API_KEY`、`OPENAI_BASE_URL` | gpt-image-2 | 支持 | 已实测 |
| 火山方舟 Seedream | `ARK_API_KEY` | doubao-seedream-4-0-250828 | 支持 | 待实测 |
| 阿里云百炼（万相 / Qwen-Image） | `DASHSCOPE_API_KEY` | qwen-image-plus | 支持 | 待实测 |
| Google Gemini | `GEMINI_API_KEY`（可选 `GOOGLE_BASE_URL`） | gemini-3.1-flash-image | 支持 | 待实测 |
| OpenRouter | `OPENROUTER_API_KEY` | google/gemini-3.1-flash-image | 支持 | 待实测 |

使用 `sb.py generate` / `batch` 时，选服务顺序：命令行 `--provider` > 配置文件 `~/.config/sansheng-image/config.json` > 按上表顺序找第一个配好的（Codex 排第一）。当前会话没有内置工具且外部服务都未配置时，用 `sb.py inbox` 把编译好的提示词写成「待出图清单」，用户拿去任何工具出图，再回来验收。

## 独立复核后端（配图计划复核 `plan-review`；成图看图 `qa` 同一套后端名）

出图之外还需要一双独立的眼睛：`plan-review` 让另一个模型读完整原文，逐张判断配图计划合不合理。后端**只有三个**，由 `stylebook.qa.plan_review.resolve_backend()` 统一解析，顺序是：命令行 `--backend`（或 `review(..., backend=)`）> `SANSHENG_IMAGE_PLAN_REVIEW_BACKEND` > `SANSHENG_IMAGE_QA_BACKEND` > 默认 `ark_coding_plan`。值不在三者之内直接报错，不会悄悄换一个。结果里的 `_backend` 是实际使用的后端，调用方要记录后端请取它，不要自己再设默认。

| 后端 | 所需凭证 / 环境变量 | 默认 | 适合谁 | 说明 |
|---|---|---|---|---|
| `ark_coding_plan` | `ARK_CODING_PLAN_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3`、`ARK_CODING_PLAN_API_KEY` | 是 | 订阅了火山方舟 Coding Plan 的用户 | 固定 `deepseek-v4.1-flash`，原文全文随请求发送；地址或型号不符即停，不切别的通道 |
| `ark_agent_plan` | `ARK_AGENT_PLAN_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3`、`ARK_AGENT_PLAN_API_KEY` | 否 | 订阅了火山方舟 Agent Plan 的用户 | `doubao-seed-2.1-turbo`，原文全文随请求发送 |
| `claude_cli` | 无 Key；本机装好并登录 `claude` 命令行，可用 `SANSHENG_IMAGE_QA_CLAUDE` 指向可执行文件 | 否 | 没有任何方舟套餐、但有 Claude Code 订阅的用户 | 模型用 Read 工具读原文；**没有输出预算设置**（`SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS` 对它无效），由 CLI 自己管理 |

配置方式：把变量写进 `~/.config/sansheng-image/.env`（`python3 scripts/sb.py setup --env-template` 生成模板；已有 `.env` 的老用户需自己把复核段落补上）或导出到环境。`sb.py plan-review` 会自动读取这个 `.env`；密钥不要发到对话里。缺凭证时命令会列出三个选项并以退出码 2 结束。

- 输出预算（两个 Ark 后端）：默认按计划里的图片数分配，下限 5,000、上限 32,000 tokens，并给 `missed_positions` / `notes` 留固定余量（有封面简报时再加 `cover_review` 的余量）；可用 `SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS` 指定 2,000–32,000。服务返回 `incomplete` 或 `finish_reason=length` 时仍拒绝验收，并报告状态、预算、使用量和中断原因，同时提示调大这个变量；不把不完整结论记为通过。
- 缺项重问：模型答了但缺字段、布尔值类型不对或漏了某张图，会点名缺项重问 **1 次**；仍不完整就报错 `PlanReviewIncomplete`，里面保留每次的原始回答和逐条缺口（`sb.py plan-review --report` 会把它们写进报告文件）。额度、鉴权、网络、超时类故障不重问。
- 错误分类对三个后端一致：429 或额度语 -> 额度用尽；401/403 或未登录 -> 鉴权/套餐不可用；连不上或超时 -> 网络不可用（超时单独说明）；其它 HTTP 错误会附服务返回的片段。

## 报错怎么处理

配图计划复核的输出预算见上一节「独立复核后端」。

| 类型 | 表现 | 处理 |
|---|---|---|
| busy | 429、5xx、「系统繁忙」「无可用渠道」 | 自动指数退避重试；连续两次繁忙且配了备用模型就切过去（用中转的 gpt-image-2 会自动加同模型备用线路 `gpt-image-2-c`） |
| auth | 401 / 403 | 密钥无效或没权限：不重试，请用户检查密钥；测试矩阵遇到它会整轮停下 |
| verify | 403 且提到组织验证 | OpenAI 要求先完成组织验证：platform.openai.com → Settings → Organization → Verify |
| policy | 内容被拒 | 不重试，换一种描述 |
| network | 连不上 | 重试；仍不行检查网络或 `*_BASE_URL` |
| quota | Codex 额度用完 | 不重试；等额度重置，或允许付费备用（见上） |

每次请求（成功与失败）都记进 `logs/cost.jsonl`（可用 `SANSHENG_IMAGE_LOG_DIR` 改位置），含估算费用、当前调用累计耗时 `seconds`（含此前重试等待）和本次尝试耗时 `attempt_seconds`。Codex 通道同样写入此日志（含 `tokens`）。
