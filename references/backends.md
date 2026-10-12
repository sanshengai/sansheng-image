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

## 报错怎么处理

Ark Agent Plan 的全文计划复核按图片数分配输出预算（最少 5,000、最多 32,000 tokens），可用 `SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS` 指定 2,000–32,000。服务返回 `incomplete` 时仍拒绝验收，并报告状态、预算、使用量和中断原因；先依据实际诊断判断是否需要增加预算，不把不完整结论记为通过。

| 类型 | 表现 | 处理 |
|---|---|---|
| busy | 429、5xx、「系统繁忙」「无可用渠道」 | 自动指数退避重试；连续两次繁忙且配了备用模型就切过去（用中转的 gpt-image-2 会自动加同模型备用线路 `gpt-image-2-c`） |
| auth | 401 / 403 | 密钥无效或没权限：不重试，请用户检查密钥；测试矩阵遇到它会整轮停下 |
| verify | 403 且提到组织验证 | OpenAI 要求先完成组织验证：platform.openai.com → Settings → Organization → Verify |
| policy | 内容被拒 | 不重试，换一种描述 |
| network | 连不上 | 重试；仍不行检查网络或 `*_BASE_URL` |
| quota | Codex 额度用完 | 不重试；等额度重置，或允许付费备用（见上） |

每次请求（成功与失败）都记进 `logs/cost.jsonl`（可用 `SANSHENG_IMAGE_LOG_DIR` 改位置），含估算费用、当前调用累计耗时 `seconds`（含此前重试等待）和本次尝试耗时 `attempt_seconds`。Codex 通道同样写入此日志（含 `tokens`）。
