---
name: sansheng-image
description: 用户说“给这篇文章配几张图”“做个封面”“出一张图”“换个画风”，或要做小红书知识卡、信息图、教材插图、漫画、绘本、整页 PPT、音乐或播客封面时使用；给出 sb2/sb1 或 C31 等画风码，或配置、检查叁笙生图时也用。读内容后挑视觉要点，同一系列保持选定画风，默认轻量出图，出图走 Codex 订阅额度。默认出静态图；用户明确要动态版（做成动的、动图、短视频版）时见动态手册。
metadata:
  version: "2.4.0"
---

# 叁笙生图

先理解图片要帮读者看懂什么，再选画风。主体场景、流程、分类、对比可以在同一画风中共存；内容结构不能被审美偏好替代。支持 81 种公开画风合同（每种标注原作灵感来源，选了用途只列出适合的画风），正式准入与某次成图通过分别记账，范围见 [README](README.md#能力边界)。

维护或评估历史发布候选时，读 [本轮收口记录](references/release-readiness.md)，区分已验证的入口与尚未通过的整组、分镜光线及动态能力；日常单图无需加载这份维护记录。

## 先路由，再按场景读

先读 [route.md](references/route.md)：判断场景（封面、文章插图、小红书、PPT、信息图、漫画），只读该场景手册和它点名的库，不一次读完所有参考文件。日常单图、教材、音乐封面等读 [quick.md](references/quick.md)；各类图的页序速查在 [recipes.md](references/recipes.md)。

先按 [内容取舍与评审](references/editorial-review.md) 明确读者、展示环境和沟通目标；场景默认画风只是候选。

1. 完整读内容，挑真正值得画的观点与关系，再写 `brief.json`：整组用途、画风、色彩、选择理由；每张仅位置、读者应明白的一句话、画面、图上的字。有原文时保留实际源文件和准确位置引用。图上的数字、引号里的话、英文专名必须在原文里找得到；自己算出来的数字在该条文字上写 `derived` 说明算法，否则 `make`、`batch` 直接报错。已有视觉足够时可以不新增图。
2. **确认一次，只一次。** 默认先把计划卡（每张一行：位置、判断句标题、图型、比例、图上文字）发给用户确认，确认后在 brief 写 `"confirmed": "user"` 再出图；用户说「直接出」或由自动流程调用时跳过确认，写 `"confirmed": "skipped"`。两种情况 `make`、`batch` 都会把计划卡存成输出目录里的 `plan-card.md`。画风：用户有画风码时用码；没有时采用已确认偏好、作者档案或用途默认，简短解释推荐理由，不要逐层让用户选择。
3. 执行 `make` 前核对生成的执行卡：原题、准确文字、参考职责及保留项是否与修改目标冲突。当前 Agent 查看总览和需要放大的原图，一次核对文字、主体关系、裁切及整组画法。确认缺陷、待确认疑点、优化建议分开；第一次未通过先诊断审核、输入、生成或装配哪一环出错，再选择补证据、修配置、局部编辑或重画，见 [qa.md 的返修](references/qa.md#返修)。失败证据保留，不直接按 partial 重生。

所有命令从 Skill 根目录运行：

```sh
python3 scripts/sb.py make brief.json -o 新目录
```

出图默认走 Codex 内置生图（订阅额度）。当前会话已有内置生图工具时，使用 `make … --prepare` 编译，直接调用当前会话的内置工具，再通过 `--import-results` 导回（见 [quick.md](references/quick.md)）；即使本机有 `codex` 命令，也不再启动另一个 Codex 出图。Claude Code 等没有内置生图工具的助手才使用普通 `make`，由适配层调用已登录的 Codex CLI；没有可用 Codex 时用已配置的其他服务。首次配置读 [setup.md](references/setup.md)，后端行为与限制读 [backends.md](references/backends.md)。不得把 pending_host 当作已出图。

## 发布档与专项

动态作品需要进一步剪辑、字幕、配音或配乐时，交接给已安装的视频制作 Skill；使用 `sansheng-video` 时，每个新会话（含接手旧工程与局部改音）先运行它的 `python3 setup.py --show-profile`，读取返回的 `context_file`，涉及素材再读 `media_library.usage`。图像与动态生成仍沿用本 Skill 的已选方法和已有工程；按原动态手册交付的静音作品不强加声音，也不为补音更换渲染器。

- 用户要求严格验收、写作 Skill 正式发布调用：增加原文覆盖、数字条件与编造自检；教程、数据或条件判断文章再做独立计划复核。带字的成品及封面做独立看图，核对事实、逐字和缩略图吸引力。图上超过一个标题时用留白底图加精确排字。
- 发布档尚未整合成 make 的一键步骤。现阶段按 [planning.md](references/planning.md)、[text.md](references/text.md)、[qa.md](references/qa.md) 的实际命令保留证据；不能只凭轻量总览宣布正式发布验收完成。
- 固定人物或连续故事：读 [consistency.md](references/consistency.md)，复用已确认身份参考；服装和姿势来自本张剧情。
- 用户用语言换色、修改构图或某张：读 [selection.md](references/selection.md)，改本次选择和内容；结构与事实仍来自原文。
- 用户要求“以后都这样”、查看或忘记偏好：读 [preferences.md](references/preferences.md)。一次性改选不自动变成长期设置。成长飞轮由脚本自动记录，连续 3 个任务同向才问一次、确认才改默认，见 [flywheel.md](references/flywheel.md)；用户采用某组图后执行 `sb.py accept`。
- 用户明确要动态版（“做成动的”“动图”“短视频版”）才读 [原图到动态](references/motion-wake.md)，由本 Skill 接手选图、备料、导演、制作、审片返修与交付，统一入口为 `sb.py animate wake`。复用已采用的图、真实原文、画风与色系，不让用户先写 HTML 或再调用另一个 Skill；缺图才沿上述静态流程补图。先验证素材能否承担动作，再确定编排；可按需复跑动态手册链接的纸艺合影、文章留言、雨街和旅行路线示例，重新适配本图锚点，不直接套坐标；允许有明确观看价值的氛围、叙事、知识过程与媒材表现，不为大动作硬拆人物或物件。上游 lemo-wake 工具及指引固定版本、作为独立运行依赖保存，其许可单独适用；首次配置由当前 Agent 处理。静态默认不变，PPT 与音乐封面仍出静态图。已有 HyperFrames 工程继续按 [兼容动态流程](references/motion-scenes.md)，旧局部动效见 [motion.md](references/motion.md)，不为迁移入口重做已有工程。技术导出、制作审片、用户采用和平台试发分别记账。
- 维护合同、准入、矩阵或画廊才读 [matrix.md](references/matrix.md)。平台尺寸读 [formats.md](references/formats.md)。PPT 静态图可以生成；整套 PPT 文件另走现有组装工具及其运行环境验收。

## 五条硬规则

1. **用完整编译结果。** 画风、颜色、参考职责和文字留白由合同及清单共同生成，宿主不再改写锁定提示词，否则无法复核实际输入。生图前核对实际 `prompt` 中的原题、布局和准确文字，不以清单字段存在代替已编译；字段类型与未消费字段见 [编译说明](references/compile.md#生图前核对实际编译结果)。
2. **一组保持用户选定画风。** 构图按逐段内容变化；锁色合同不能换色，码中用途、修订冲突须先纠正，不能暗中换默认。
3. **事实与文字有来源。** 不补编数字、条件、结论；单词和精确标签可用程序排字，留白冲突必须修底图或布局，不能压住主体。
4. **保留输入和原图。** 参考图、原文、提示词或合同变化后旧验收失效；不覆盖失败候选，未知模型和费用保持未知，密钥不入文档或日志。
5. **看图结论按范围说。** 像素预检不证明内容或美观，单图采用不证明画风正式准入；失败、不完整和未验证步骤照实记录。

## 选择码与数据

`sb2:wxcover/C30`、`sb2:xhs/C35-earth`、`sb2:info/C42-hex.1F6F8B.F4F1E8` 可传用途、画风和色彩；`@rN` 锁当前修订。旧 sb1 继续可读。网站复制端升级前，语法与语言修改见 selection.md。

源数据是 `styles/`、`scenes/scenes.json`、`formats/formats.json`、`structures/structures.json`、`palettes/palettes.json`；`registry.json` 用 `python3 scripts/sb.py build` 重建，不手改。作者设置和私有样式放 `SANSHENG_IMAGE_PROFILE` 或 `~/.config/sansheng-image/profile/`，不进入公开包。
