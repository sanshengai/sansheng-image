# 成长飞轮：越用越顺手，但不静默改你的默认

飞轮由脚本自动记录，不靠 Agent 记得去调用。规范版本 1，写作 Skill 带同版本副本。

## 记什么、怎么记

| 事件 | 谁写 | 含义 |
|---|---|---|
| 选择（choice） | `make` 结束时自动写 | 用户亲自挑了与推荐不同的画风或色系。brief 里写 `"chosen_by": "user"`，或用户给的是 sb2 / sb1 码；Agent 自己选的不算 |
| 返修（fix） | `make` 的像素预检失败时自动写 | 只记画风、用途和失败类别，不记原文 |
| 采用（accept） | `sb.py accept <make 输出目录> [--ids 01,02]` | 用户说“就这张 / 就用这些”后执行 |
| 任务（task） | `make` 每次自动写 | 用来发现飞轮断流 |

数据放在 `<profile>/flywheel/`（`outcomes.jsonl`、`tasks.jsonl`）和 `memory.json`（选择与确认的偏好）。只存字段和产物路径，不存对话原文。

## 什么时候问一次

同一维度、同一方向，在 **3 个不同任务**里出现，才生成待问；同一任务里改多次只算一次；方向不一致不问。`make` 结束会在标准错误里打印“【待问】…”，并写进 `report.json` 的 `flywheel.pending`。Agent 在**下一次任务开头**把它用一句话问用户，三个答案：

- 设为默认 → `sb.py flywheel answer --scene wxcover --field style --value C19 --choice default`
- 只在这个项目 → 加 `--project 项目名 --choice project`
- 不用 → `--choice no`，60 天内不再问

不确认就不改默认。`make` 选画风只看：码 → 本次指定 → 已确认的偏好 → 作者档案 → 出厂默认；观察中的倾向不参与。

## 看得见

- 用到已记住的默认时，`report.json` 的 `flywheel.notes` 有一行“沿用你的默认：C19”。
- `sb.py flywheel status` 列出已生效的默认、观察事件数、已拒绝项、用途×画风的采用 / 返修账。
- 连续 5 次任务没有留下任何记录，会提示“飞轮可能断了”。
- 想忘掉某条：`sb.py preferences forget --event-id …`；暂停记录：`sb.py preferences pause`。

## 返修沉淀成禁止项

同一画风的同类返修原因（如「人物画大了」「纸像厚纸板」「出现暗角」）出现 2 次及以上时，提示维护者把它写进该合同的 `qa.must_not_see`，必要时补一句 `qa.fix_phrases`。只提示，不自动改合同；人工确认后升合同 revision，并在 changelog 记来源任务。

## 不做

不静默改默认；不把 Agent 的选择、事实纠错或自动返修当成审美偏好；不存对话原文。
