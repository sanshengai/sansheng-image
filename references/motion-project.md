# 动态工程与恢复约定

所有命令从 Skill 根运行。`sb.py animate wake` 是同一 Skill 内的可选模块；已有 `sb.py animate doctor/plan/render/qa/assets/dialect` 与独立 replay 用法保持兼容。

## 工程文件

`init` 建立新目录，拒绝覆盖已有目录。`inputs/` 保留逐字节原图、可选原文与原生静态需求/编译计划；`inputs/prepared.json` 保存实际继承的静态编译结果；`motion.json` 记录用途、场景、继承项、输入指纹、输出规格和上游版本；`src/` 为上游规范的自包含工程。模型不复制到工程。

`director.json` 由当前 Agent 看图、检查材料后填写。例如：

```json
{
  "decision": "animate",
  "mode": "material",
  "why_motion": "照片随着纸页展开，呈现这段回忆被收进相册的过程",
  "preserve": ["原照片完整人物与色彩", "原文标题逐字保持"],
  "beats": [
    {"at": 0, "event": "纸页与照片已能辨认"},
    {"at": 1.4, "event": "沿书脊展开，完整照片随页面露出"},
    {"at": 4.2, "event": "落定并停留阅读，再由纸页自然收束"}
  ],
  "materials": [
    {"path": "src/assets/source.png", "motion": "move", "complete": true,
     "observation": "原图作为完整照片整体使用，无人物分件；背面使用纸材，不伪造新姿态"}
  ],
  "review_times": [0, 0.8, 1.4, 2.6, 4.2, 5.96],
  "loop": "纸页收束回首态；完整照片保留阅读时间",
  "limitations": []
}
```

示例时刻必须按实际时长和动作修改。`decision` 为 `pending/animate/static`；`static` 时 `why_motion` 记录不适合做动态的具体理由，其余制作字段可留空。`mode` 为 `atmosphere/narrative/explanation/material`。可选 `reset` 对象含 `kind`、`hold`（默认 0.7 秒）、`duration`（默认 0.6 秒），`kind` 使用上游 `reset.py --list` 中的名称。省略时由工程内部完成循环；帧级过渡会增加输出时长，审片须查看最终 `src/qa_sheet.jpg` 与完整成片，不能只看过渡前的预览。可取用上游全部过渡种类，不限于纸张。

移动或揭示的材料须完整，`observation` 是 Agent 的实际视觉判断，不是机器检查结果。固定背景可标 `motion: fixed`。三个以上不同审片时刻只是最低覆盖，不证明极值已选对。

## 预览与渲染

每次 `preview` 创建 `previews/001`、`002` 等新目录，复制本轮源码，检查协议和乱序 seek，再输出 `materials.jpg`、`timeline.jpg`、原尺寸静帧及 `preview.json`。失败的目录和日志保留；修复后生成新候选。

`render` 要求存在与**当前输入、规格、导演稿及全部源码资源**一致的有效预览；否则拒绝。渲染到 `renders/001` 等新目录，调用原工具并生成 `candidate.json` 和自动循环对照页。上游技术 FAIL 会中止；WARN 保留并在审片中解释，不能为消除提醒而加无关动作。

`replay_wake.py --fps N` 同时传给 reset、encode 和 QA，默认仍为旧工程的 30fps；统一新流程从工程配置传实际帧率。旧工程改变帧率时显式传入，不把 24fps 的帧序列当 30fps 编码。

## 制作审片

输出目录的 `review-template.json` 是待填表，不是通过报告。完整播放后另存一份实际审片 JSON，再调用：

```sh
python3 scripts/sb.py animate wake review 工程 --render 工程/renders/001 --assessment 审片.json
python3 scripts/sb.py animate wake status 工程
```

审片字段：

- `verdict`: `accept` 或 `revise`；`reviewer` 写实际执行者。
- `playback`: `cycles` 为实际连续播放轮数（采用至少 2），`method` 写实际播放方式。只抽帧时不能填写已完整播放；已发现明确缺陷可以在 0 轮时先记 `revise`，未观察项为 `pending`，至少保留一项实际失败及具体返修位置。
- `checks`: `motion_value`、`integrity`、`causality`、`composition`、`legibility`、`loop` 各含 `result: pass/fail/na` 与具体 `note`；只有未含文字的 `legibility` 可在采用时为 `na`。
- `evidence`: 输出目录内实际查看文件的相对路径，例如联系表和原尺寸帧；返修时 `issues` 记录问题和应改的材料、动作、时序或布局。
- `platform`: `state: untested/tested` 与 `note`；`tested` 另需 `evidence` 指向本成片的实际平台试测记录。不可沿用另一支 GIF 的反馈。

审片结果追加保存在 `renders/001/reviews/001/review.json` 等目录，不覆盖失败结论。`accept` 得到 `agent-reviewed-candidate`，表示当前 Agent 的制作审片；用户采用另记，脚本不伪造。修改成片、源码、素材、字体、导演或输入后旧证据失效，须重新预览、渲染和审片。不要手改指纹绕过检查。

恢复时先 `status`，读已有失败日志、当前导演稿和已查看材料，不重新跑静态出图或重做整组。制作决策与外部依赖的边界见 [完整流程](motion-wake.md)。
