# 动态版：公众号配图、信息图、小红书、品牌动态示范

默认出静态图。只有用户明确要动态版（“做成动的”“动图”“短视频版”）才读本页；没有这类要求，一律按 [route.md](route.md) 出静态图，本页不介入。旧版局部动效（[motion.md](motion.md)）仍保留供旧任务查阅，与本页是两套流程，不要混用。

## 先说清楚能做什么

| scene 值 | 对应静态手册 | 动态版做什么 |
|---|---|---|
| `article` | [wxillus.md](scenes/wxillus.md) | 文章里某个要点的动态版：顺序、对比、强调逐步出现 |
| `infographic` | [info.md](scenes/info.md) | 单结构图的结构逐层出现（流程按步、对比按边、矩阵按格） |
| `xiaohongshu` | [xhs.md](scenes/xhs.md) | 封面或某一页的动态版；3:4 源可用 `xhs-3x4-mp4` |
| `brand` | 无静态流程 | **只做原创品牌动态示范**：展示这套画风能动成什么样，不承载某篇文章的内容，也不是某个已有静态 Logo 的动态版 |

三个文章类场景沿同一篇真实原文：先出静态版，通过看图验收后，再出同一内容的动态版。

PPT 和音乐封面的动态版是另定的第二批，现在仍出静态图。已有的 PPT、音乐动态示范片可以拿来展示，但不等于动态功能支持这两类：`sb.py animate` 会拒绝这两类 scene，也拒绝 `ppt-mp4-candidate`、`music-loop-mp4-candidate` 两个 preset。

## 当前验证到哪

- 正式 `animate` 入口已验 `doctor` 与四场景 `plan`；底座的真实小工程 `render` / `qa` 已验。四场景（`brand`、`article`、`infographic`、`xiaohongshu`）的完整媒体已经在正式入口上真实复跑 `render` 与 `qa`：四个场景 `render` 均为 `ok`、`qa` 均为 `auto-pass-needs-manual-review`，母版与此前审片的母版一致，因此沿用当时的原生视频审片结论；复跑得到的新 GIF/WebP 宽度可与候选不同，这些新文件不复用旧的看图结论。
- 这些都是示范片的证据，**新作品仍要逐项人工审片**，不能由示范片替代；手机、平板及平台上传兼容性尚未验证；它们不作为电脑端 Skill 使用与发布的前置条件。
- 随包的原创动作库只验到“能导出、文件逐字节一致”这一步（`animate assets`），它不替新作品完成 `render` / `qa`。
- 平台上传兼容性尚未验证：不承诺任何平台的上传兼容、体积或时长限制，也不承诺“可直接发布”或“自动播放”。交付时写“平台上传兼容性未验证”。

## 第 0 步：先 doctor

```sh
python3 scripts/sb.py animate doctor --hyperframes-cli "$HYPERFRAMES_CLI"
```

`$HYPERFRAMES_CLI` 是你自己装好的 `hyperframes.mjs` 的绝对路径（例如项目内 `node_modules/hyperframes/bin/hyperframes.mjs` 展开后的路径）。要求：

- Node.js 22 以上；
- HyperFrames 0.8.140；
- ffmpeg 与 ffprobe；
- 一个本机渲染浏览器（Chrome 或 chrome-headless-shell）。

这些都由用户自行安装，封装不会下载或安装它们，缺项由 `doctor` 逐条写出。Python 侧只需 Pillow（静态出图已有）和 numpy（`python3 -m pip install -r requirements-motion.txt`，分件抠图用）。`doctor` 不读任何 manifest，也不写文件；没过就不要承诺能出动态版。

## 流程：从同一篇真实原文到静态加动态

1. **读同一份真实原文。** 动态版和静态版必须来自同一篇文章、同一批要点。只拿现成演示片改字，不算这篇文章的动态版。
2. **选视觉要点。** 沿用静态手册的价值门和图型选择。动态只服务一个命题：顺序、因果、对比或强调；说不出“动起来帮读者看懂什么”，就不做动态。
3. **先出静态版。** `sb.py make brief.json -o <新目录>` 走完整编译。当前会话已有内置生图工具时，先 `--prepare`，用本会话的内置工具出图，再 `--import-results` 导回；不另起第二个 Codex。静态版通过看图验收后，才继续动态。
3b. **读这个画风的动作语言（动画工程准备）。** 写编排前先查具体合同里的运动材质与节奏：

   ```sh
   python3 scripts/sb.py animate dialect --style C24
   ```

   - 输出始终是 JSON：当前 `code`/`revision`、`motion_dialect`（`language` 运动材质与节奏、`primitives` 建议类别、`safety` 受保护内容的边界）、类别对应的动作库函数名、规范化合同的 `contract_sha256`。风格码写 `C24@r5` 时修订号必须与当前合同一致，不一致会被拒绝（退出码 2）。
   - 这只是导演建议：先读 `language` 与 `safety`，再按**这篇文章的原文因果**和画风材质选动作；不要照抄成固定动作，也不要因为类别列表里有某一类就必须用。十二个类别是建议类别，与 [motion-primitives.md](motion-primitives.md) 里的具体函数名不是同一回事，不会被自动调用。
   - 合同还没有 `motion_dialect` 时返回“尚未编写”（退出码 2），不会套用其它画风。这**不是**四场景拒绝：没有方言的画风仍可按 [审美判断](motion-taste.md) 的原则编排。
   - 它只读合同，不渲染、不验收，也不代表该画风的静态版已准入。

4. **同一内容、同一画风出动态素材。** 两条路选一条：
   - 分件：`sb.py layers brief.json -o <新目录> --prepare` 生成每件的提示词和绑定记录，由本会话的内置生图工具出每张图，再 `--import-results <结果.json> --prepared <prepared.json>` 导回。物件用纯绿幕出图，导入时用颜色键抠成透明层；brief 的格式可参考仓内 `tests/fixtures/layers-bookstore-brief.json`。机位是否真的一致只能人工叠看，导入结果的状态是“待看图”，不是已通过；
   - 合适的底图加代码字：留白底图配程序排字。数字、专名、引语一律用代码字，不让模型在动态素材里写。
   不为动态版偷偷调用按张收费的服务。
   - 程序化小动作（光扫、粒子、弹簧、逐字、数字滚动等）不是第三条素材路径，只在第 5 步写工程时按需使用：`python3 scripts/sb.py animate assets --out <尚不存在的新目录>` 把随包的原创动作库复制出来，再放进工程。这一步只是准备资源，不等于 `render` 或 `qa`；函数、保护规则和接线例子见 [motion-primitives.md](motion-primitives.md)。旧工程不必改。
5. **原创编排。** 在新目录里写一个 HyperFrames 工程：`index.html` 恰好一个根 composition，`data-width`、`data-height`、`data-duration` 与 manifest 完全一致，资源全是工程内本地文件，不引用远程地址。使用 GSAP 时，工程须包含已合法取得的本地 `gsap.min.js` 副本；远程 CDN 会被拒绝。编排按这篇文章的要点写，不套演示片的时间线。
6. **写 manifest，然后 plan → render → qa。**
7. **交付静态加动态。** 见下文“交付”。

manifest 示例（`project` 是相对 manifest 所在目录的子目录；除 `brand` 外 `style_code` 必填）：

```json
{"version": 1, "project": "motion-project", "scene": "article", "style_code": "C24",
 "width": 1280, "height": 720, "duration": 6, "fps": 30, "loop": true, "presets": ["preview-gif", "preview-webp"]}
```

尺寸只是本地参数，不是平台规格：`article` 常用 16:9，`infographic` 与 `xiaohongshu` 常用 3:4，宽高须是偶数。`xhs-3x4-mp4` 要求源本身就是 3:4，不会裁横图。

```sh
python3 scripts/sb.py animate plan   manifest.json --out ./motion-out-1 --hyperframes-cli "$HYPERFRAMES_CLI"
python3 scripts/sb.py animate render manifest.json --out ./motion-out-1 --hyperframes-cli "$HYPERFRAMES_CLI"
python3 scripts/sb.py animate qa     manifest.json --out ./motion-out-1
```

- `plan` 只读预演，不创建文件、不执行外部命令。先 plan 再 render。
- `plan` / `render` 的 `--out` 必须是尚不存在的新目录；已存在（含空目录、符号链接）、位于输入工程内都会被拒绝，成品不会被覆盖。重做就换新目录名。
- `qa` 的 `--out` 指向已有渲染结果目录；该子命令不接收 `--hyperframes-cli`。
- 渲染占 CPU 和时间。先 plan，确认命令和路径再 render。
- 没有的参数会被拒绝。想改 preset、尺寸或帧率，改 manifest，不要往命令行加参数。

## 人工验收项（逐项看，逐项记录）

1. **首态完整。** 第 0 帧就是一张读得懂的图：标题和主体都在，不依赖动画播完才完整。
2. **字形稳定。** 文字不变形、不重排、不抖动；动的是形状和位置，不是字本身逐帧重画。
3. **因果。** 每个动作对应原文里的一个关系；顺序、因果、强调能在原文里找到依据，不为热闹加装饰动效。
4. **重复 seek。** 同一时间点反复 seek 画面一致，不依赖无种子随机或累积状态。
5. **源码 input 变更，旧证据失效。** `index.html`、工程资源、manifest 或原文任一变化，旧 render、旧 qa、旧看图结论全部作废，重新 render 到新目录再验。

`qa` 只复验真实文件：格式、时长、首末帧接缝、首帧非空白、联系表。固定阈值，**不做 OCR，不能声称任意文字都经过校验**；文字清晰度和 logo、标签文字保护必须人工看联系表和原尺寸帧。`qa` 状态为 `auto-pass-needs-manual-review` 时仍须人工审片，`probe-only` 不算通过。

## 交付

- 静态版：按静态手册的结果交付；需要平台比例用 `sb.py export 图 --format <格式> -o <输出>`。
- 动态版：交付 render 输出目录里 manifest 所列 preset 对应的文件（`preview.gif`、`preview.webp`、`share.mp4`、`xhs-3x4.mp4`）、`poster.png`、`render_report.json` 和 `qa/` 下的 QA 报告与联系表。动态版没有第二套 export 命令，要别的规格就改 manifest 重新 render。
- 同一篇的静态和动态一起给，并写明对应关系（哪张静态对哪个动态）、`qa` 状态、哪些项仍待人工确认。
- `brand` 的交付标明“原创品牌动态示范，不含任何文章内容”。

## 平台状态

不写任何平台的精确体积、时长、比例限制，也不承诺“可直接发布”或“自动播放”。`preview-gif` 自带的 10 MiB 体积自检和 `preview-webp` 的 8 MiB 默认值是本地参数，不是平台限额。平台上传兼容性验证前，交付时写“平台上传兼容性未验证”。

## 正例与反例

正例：
- 一篇讲“三步复盘法”的文章，信息图按步出现，三步各自对应原文一段，静态版同样是这三步。
- 小红书封面做成 3:4 源，首帧就是完整封面，标题用代码字，动的是背景元素。
- `brand` 用某画风做 6 秒品牌示范，交付时标明“示范，不含任何文章内容”。

反例：
- 把现成演示片改个标题，当成这篇文章的动态版。
- 把 `brand` 示范说成某个已有静态 Logo 的动态版。
- 数字或专名交给模型在动态素材里直接写。
- 静态版还没通过就先做动态。
- 给 PPT 或音乐封面生成动态，或把已有 PPT、音乐演示当成正式支持。
- 往命令行里加 `--scene ppt` 之类参数试图绕过场景限制（会被拒绝）。
- `qa` 通过就宣布“文字都校验过”或“可以直接发”。
- 改了 `index.html` 却沿用上一轮的 render 和 qa 结果。

## 审美复盘

编排动作前查 [审美判断](motion-taste.md)，按场景参考 [十二支原创示范的经验卡](motion-experience.md)。单支作品的审片通过只对应已审文件，新作品重新验收；手机试发仍待完成。
