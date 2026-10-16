# 叁笙生图 · Sansheng Image

**中文** | [English](./README_EN.md)

**选一个画风，让一组静态图沿用同一套画法。** 适用于文章封面和插图，也提供课件、知识卡、信息图、故事、漫画、AI 短剧分镜首帧、音频封面与表情包的试用流程。它是给 Agent 使用的 Skill，不是独立生图模型；实际出图需要当前 Agent 的生图能力、你配置的图像服务，或把编译好的提示词交给其他工具。

> 当前为公开 beta。本机维护版本有 81 种公开画风合同；C91 浮世绘木版多色印、C92 敦煌矿物彩壁画、C93 年画木版敷彩、C94 青花蓝白装饰、C95 汉画像石拓片、C96 童话钢笔水彩民俗边框、C97 新艺术植物曲线、C98 装饰艺术几何共 8 款已通过当前准入，场景覆盖 344 项、配色覆盖 108 项已完成，保留原始失败题、编辑链与能力限制；其余 73 种按重建后的修订待准入，尚未重测。本版画风库默认展示封面，其他用途可切换；原有 12 个动态示例在独立动态区展示。安装包不含画廊样图；样式准入不等于每个实际用途都已验收。进展以 [能力边界](#能力边界)为准。

## 先看产出

| 画风 | 实际生成的单张测试图 | 能说明什么 |
|---|---|---|
| C01 暖光手绘动画 | ![C01 家庭场景测试图](./assets/C01-story-sample.png) | 人物叙事与暖光画法的一个成功样本 |
| C32 旧纸技术简报 | ![C32 番茄工作法信息图测试图](./assets/C32-infographic-sample.png) | 结构化信息与双语文字的一个成功样本 |

这两张图分别来自同题测试，不代表同一画风的整组跨用途已通过。参考图与预览图的来源、哈希和范围见 [ASSET_PROVENANCE.md](./ASSET_PROVENANCE.md)。

需要短剧或视频的分镜时，官网「AI 短剧分镜」用途下有 10 个画风，每个画风各 4 张样图：一张角色三视图（正、侧、背加三种表情），两张竖屏 9:16 镜头（中景与特写），一张横屏 16:9 电影感远景。分镜只出每个镜头的静态首帧，人物靠三视图跨镜头固定，运动交给视频模型。做法见 [references/scenes/storyboard.md](./references/scenes/storyboard.md)。

旧版动图入口已下架；旧命令与 [motion.md](./references/motion.md) 保留供旧任务查阅。新的动态版见 [动态场景](./references/motion-scenes.md)：覆盖公众号配图、信息图、小红书，品牌仅作原创动态示范；只在你明确要动态版时才用，静态版仍是默认。

## 它怎样决定一张图

1. **先理解任务和原文**：分清封面、解释、比较、步骤、人物叙事等用途；为每个位置摘出原文依据、关键关系和需要出现的短文字。没有根据的数字和结论不能补编。
2. **再锁定系列画风和色调**：本次明确选择高于项目锁定、长期偏好和出厂默认。一个系列只用一种样式；允许换色的样式才接受色调调整。
3. **逐张决定表达结构**：同一篇文章可以有主体场景、流程图和对比图。结构取决于该段内容，不能因为用户喜欢人物画就把数字关系都画成人物。
4. **编译、生成、逐张检查**：提示词保留风格合同；出图后核对事实、关系、文字、裁切和整组一致性。失败图与返修记录不冒充准入。

用户可以说“以后文章配图用 C01”“这张改成对比图”“整组颜色更柔和”。一次性改图只改当前任务；明确的长期设置保存在本机，可查看、撤销和忘记。系统只在三个独立任务里观察到一致的主动改选时提高推荐排序，不会从沉默或自动打分推断喜好。

## 安装

推荐从 GitHub 安装：

```bash
npx skills add sanshengai/sansheng-image -g
```

需要电脑里有 Node.js 和 Python 3.10+，并使用 Claude Code、Codex 这类能执行本机命令的 AI 助手。不会用命令行，可把仓库链接交给 AI 助手，请它替你安装。

手动安装时，克隆公开仓并让 Agent 的 Skill 目录指向整个仓目录；Claude Code 也可通过仓内 `.claude-plugin/marketplace.json` 安装。建议在虚拟环境运行：

```bash
git clone --depth 1 https://github.com/sanshengai/sansheng-image.git
cd sansheng-image
python3 -m venv .venv
. .venv/bin/activate
python3 scripts/sb.py doctor
```

首次运行会检查基础依赖，缺少时由当前 Python 自动安装；失败会说明手动安装方法。首次成功完成整组图片后会提示一次 GitHub 点星，随后不再提示。

推荐用 `--depth 1` 浅克隆：现在仓库本身不含样图，浅克隆与完整克隆的差别只在历史，完整克隆会带上历史里的旧样图，体积大得多；出图只用合同与锚点，不读样图。

参与修改时运行 `git config core.hooksPath .githooks` 启用提交前脱敏检查。PPTX 组装另需 `python3 -m pip install -r requirements-ppt.txt`。

**维护者：样图不进仓库。** 只给人在官网选择器里看的样图（`styles/*/samples/`）只放在本机和官网，已写入 `.gitignore`；合同里登记的文件名与宽高比保留，选择器没有样图文件时按登记重建。新样图入库后运行 `python3 scripts/samples_sync.py status`（对比合同登记，并报告哪些样图是用旧修订生成、待重出的）和 `python3 scripts/samples_sync.py backup --dest <仓库外目录>`（只增不删的备份）；换机器用 `restore --src <备份目录>` 还原。2026-10-13 起登记的样图必须在合同里带 `generation` 生成记录（出图时的合同修订、提示词指纹、第几次出图、从几张中挑、是否后期编辑），选择器据此标注；更早的样图标为「历史样图」。锚点图（`styles/<码>/anchor.*`）和出图用的范例图不在 `samples/` 下，仍随仓库。

## 日常轻量入口

现在可用四项逐图计划交给 `python3 scripts/sb.py make brief.json -o 新目录`；内置工具先 `--prepare`，实际成图用 `--prepared` 与 `--import-results` 导回。计划格式和来源绑定见 [轻量出图](references/quick.md)。Skill 接收端和本地选择器均支持 `sb2`；2026-10-16 已实际核验官网的配图筛选、C91 搜索与复制，完整选择码在轻量 brief 中放入 `code` 字段。

教材新增 `tb-vocab`（单词参考图）和 `tb-grammar`（语法情景图）。单词图默认无字；传一个单词时预留底部标签带，生成后程序排字。其他精确标签可给 overlay 位置配置。单词数量、人物文化、动作和画风仍须看实际图，教材用途尚未正式验收。自动成长事件已实现，长期有效性尚未验证。

## 十分钟试用

先编译仓内的虚构示例，检查画风、尺寸和完整提示词：

```bash
python3 scripts/sb.py compile examples/quickstart/manifest.json --json
```

若当前 Agent 有内置生图工具，按 [生图路由](./references/backends.md) 将编译结果里的完整 `prompt` 和参考图交给它。若使用外部图像服务，把密钥放在本机环境变量或运行 `python3 scripts/sb.py setup --env-template` 后自行填写本机配置文件；不要把密钥发给 Agent，也不要提交到仓库。然后运行：

```bash
python3 scripts/sb.py setup
python3 scripts/sb.py generate examples/quickstart/manifest.json -o /tmp/stylebook-first.png
python3 scripts/sb.py qa /tmp/stylebook-first.png --style C32 --manifest examples/quickstart/manifest.json
```

没有图像服务时，`python3 scripts/sb.py inbox examples/quickstart/manifest.json -d /tmp/stylebook-inbox` 会生成可交给其他生图工具的任务包。真实服务调用可能收费；`doctor --deep` 会发起测试图请求。

`qa` 需要可用的独立看图模型，或另外提供可追溯的复核记录；缺少复核者时不能把像素检查结果当作整张通过。

独立复核（`plan-review` 配图计划复核、`qa` 看图）有三个后端可选：火山方舟 Coding Plan（默认）、火山方舟 Agent Plan、本机已登录的 Claude Code（`claude_cli`，不需要 Ark Key）。选择方式、各自需要的环境变量和适用场景见 [后端说明「独立复核后端」](./references/backends.md)；没有任何一个可用时，计划复核会停下并告诉你怎么配，不会把没复核的结果当作通过。

## 选画风、手动调整与记忆

- `python3 scripts/sb.py style-for wxillus`：查看公众号插图的当前默认、候选、选择来源及画风准入状态。当前出厂默认 C24（公众号插图／表情包）、C31（小红书）、C40（信息图）、C30（公众号封面）仍待正式准入；默认能出图不代表成图已通过验收。
- 官网选择器 <https://sanshengai.top/tools/image/>：先逛 81 种画风（每种标原作出处），按用途筛选并换成该用途最需要看的样图——封面看「标题写在图上」、文章插图看「概括图」、小红书看一组 3:4 知识卡、PPT 看三页、竖版信息图、漫画有四格 / 日漫标准页 / 日漫大格页 / 长条漫四种版式；每个风格都配了贴合它气质的主题，点开能按组看整套图；点开一种画风，选用途和色彩，再选「设为我的默认」（记在浏览器里，攒几个后一次复制给 Agent）或「只用这一次」（直接复制一行 `sb2:` 码）。「看色卡」里是 18 个有名字、有故事的色系。本地用 `python3 scripts/sb.py build --gallery` 生成同一份页面（`gallery/build/index.html` 加 `img/`，请在 `gallery/build/` 里起一个静态服务器打开，色调示意需要同源）；`--advanced` 生成旧的完整画廊（内嵌全部样图、含偏好页与选择 JSON 往返）。
- `python3 scripts/sb.py preferences set --scene wxillus --field style --value C01`：设置以后公众号插图的默认画风；`preferences show`、`clear`、`undo`、`forget` 管理记录。详见 [偏好规则](./references/preferences.md)。
- 对整篇文章，先写内容计划并用 `sb.py plan` 检查位置、依据和关系，再使用 `sb.py batch` 或逐张编译。规则见 [内容规划](./references/planning.md)；[合成示例](./examples/content-plan-v2/plan.json)演示计划形状，其中 C31 仍是待准入样式。

## 能力边界

| 功能 | 当前状态 |
|---|---|
| 81 种公开画风合同、色调规则、内容结构编译 | 本机可调用；C95/C96/C98/C93/C91/C92/C94/C97当前准入，其余73款待准入；新款未发版，实际用途须另验 |
| 文章内容规划、单张和批量出图、局部返修、质量记录 | 本地可用；配套写作 Skill 已接管出图与排版 |
| 本地画廊与选择 JSON 往返、可撤销的本地偏好 | 已实现；真实长期学习效果仍需连续使用验证 |
| PPT、小红书、信息图、漫画、绘本、音频封面、表情包 | 有实现和旧样例；完整实际消费者验收按格式逐项推进，PPT 中文渲染尚需在目标软件核对 |
| 官网选择器（逛画风 → 选用途与色彩 → 设为默认 / 只用一次，复制一行 sb2 码）、按用途的样图、18 个命名色系 | 本地候选有此前的浏览器行为检查；2026-10-16 实际官网显示 81 款，配图筛选、C91 搜索与 sb2 复制通过。本轮未重新验证本地改版 UI。正式准入仍以上方 8 款为准，不能由页面可选推定 |
| AI 短剧分镜 | 出静态首帧，运动只写成文字；当前会话用 `board --prepare`，见分镜手册。已做两镜跨画幅探索，角色、持物基本接续；光源仍有偏差，不保证跨镜头一致，不出视频 |
| Codex 订阅额度出图（默认）、按张付费服务（显式备用） | 可用；Codex 单张约 45–100 秒，比中转服务慢，但不额外花钱 |
| 成长飞轮（自动记录选择 / 采用 / 返修，同向 3 个任务问一次） | 已实现；长期效果仍需连续使用验证 |
| 动态版（公众号配图、信息图、小红书；品牌仅作原创动态示范） | 正式入口已验环境检查和四场景计划；底座真实渲染与 QA 已验，四场景完整媒体为候选阶段证据；主要流程为电脑网站点选、复制指令后在 Skill 运行，平台上传兼容性尚未验证，不承诺任何平台上传兼容或可直接发布。PPT 与音乐封面的动态另定第二批，`animate` 仍会拒绝，已有示范片可展示 |
| 跨设备记忆同步、视频、Logo | 当前未提供 |

两种私有画风及其默认值不在公开包中。个人记忆默认留在 `~/.config/sansheng-image/profile/`，可用 `SANSHENG_IMAGE_PROFILE` 指向自己的私有目录；显式指定的目录若不存在，本次不读取默认私有目录。图像服务适配的实测范围见 [后端说明](./references/backends.md)；未实测的适配器不会写成已验收。

本地重建候选的验证范围、发布余项与停止条件见 [本轮收口记录](references/release-readiness.md)。历史候选记录不代替当前 Release、设备验证或用户采用。

## 动态版：依赖与命令边界

- 只在你明确要动态版时使用，且必须先有同一内容的静态版。流程与正反例见 [动态场景](./references/motion-scenes.md)。
- 额外依赖（静态出图不需要）：Python 侧 `python3 -m pip install -r requirements-motion.txt`（只有 numpy，Pillow 已是基线依赖）；其余由你自行安装：Node.js 22 或更新、HyperFrames 0.8.140、ffmpeg 与 ffprobe、本机 Chrome 或 chrome-headless-shell。缺哪一项 `python3 scripts/sb.py animate doctor --hyperframes-cli <hyperframes.mjs 的绝对路径>` 会逐条指出，封装不会自动下载。
- 命令：`python3 scripts/sb.py animate doctor|plan|render|qa ...`。`doctor` 不读输入；`plan` 只读预演，不创建文件；`render` 与 `qa` 需要 manifest；`render` 的输出目录必须尚不存在，`qa` 使用该次已渲染的现有输出目录。均不覆盖已有文件，也不写进输入工程。
- 只接受 `brand`（原创品牌动态示范）/ `article` / `infographic` / `xiaohongshu` 四种 scene；其他 scene 与 PPT、音乐封面格式一律拒绝。
- 渲染在本机完成，不联网、不调用付费出图服务；静态图与分件图仍按原有方式出图，有内置生图工具时先 `--prepare`、用当前会话的内置工具出图、再 `--import-results`。分件抠图只用项目自带的颜色键代码，不调用系统自带的 Vision 工具，也没有自动 Vision 回退。
- 许可：HyperFrames 为 Apache 2.0 的外部运行依赖，不随本仓分发；颜色键模块 `cutlib` 是本项目原创的 MIT 代码。详见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。
- 本地测试：`python3 -I -m unittest discover -s tests -v`。真实渲染的可选测试读取环境变量 `HYPERFRAMES_CLI`（或项目自装的 `node_modules`），缺工具会明确跳过。真实动画测试还需本地 GSAP，可用 `GSAP_JS_PATH` 指定 `gsap.min.js`；不会从 CDN 下载。

## 参与贡献

详见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

- 推荐画风：使用仓库的「推荐画风」Issue 模板。
- 报问题：使用「报问题」Issue 模板。
- 代码或文档改进：提交 Pull Request。

网站投稿入口随共建功能上线，目前尚待上线；当前请使用 GitHub Issue 或 PR。

## 更新、致谢与许可

变更见 [CHANGELOG.md](./CHANGELOG.md)。风格描述、排版思路和工作流参考过 [threerocks/hand-drawn-styles](https://github.com/threerocks/hand-drawn-styles)、[JimLiu/baoyu-skills](https://github.com/JimLiu/baoyu-skills) 与 [yang0/handraw-style](https://github.com/yang0/handraw-style)；具体改写范围和许可原文见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。感谢这些作者公开方法与代码。

本仓采用 [MIT License](./LICENSE)。维护者：叁笙（sansheng）。

维护者构建首页时，可用 `SANSHENG_IMAGE_BRAND_DIR=<品牌图片目录> python3 scripts/sb.py build --gallery` 提供 `logo.webp` 与 `logo-poster.webp`。两张图必须是可解码的 WebP；不指定目录时只登记图片清单，品牌图与画风样图都需由网站部署方提供。
