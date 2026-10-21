# 使用指南

[返回项目首页](./README.md) · [能力边界](./README.md#当前能力与边界)

本指南放安装、命令与配置细节；日常使用可以直接向 Agent 描述任务。

## 安装与环境检查

推荐安装：

```bash
npx skills add sanshengai/sansheng-image -g
```

需要 Node.js、Python 3.10+ 和能执行本机命令的 Agent。手动安装时，克隆公开仓，让 Agent 的 Skill 目录指向整个仓目录。Claude Code 也可通过仓内 `.claude-plugin/marketplace.json` 安装。

```bash
git clone --depth 1 https://github.com/sanshengai/sansheng-image.git
cd sansheng-image
python3 -m venv .venv
. .venv/bin/activate
python3 scripts/sb.py doctor
```

首次运行检查基础依赖，缺少时由当前 Python 自动安装；失败会说明手动安装方法。首次成功完成整组图片后会提示一次 GitHub 点星，随后不再提示。

建议浅克隆，避免下载历史里的旧样图库。当前包只包含合同、参考锚点和精选展示图；官网数千张样图不参与出图运行。PPTX 组装另需：

```bash
python3 -m pip install -r requirements-ppt.txt
```

## 第一次编译与出图

先用仓内虚构示例检查尺寸、完整提示词和参考图，不调用生图服务：

```bash
python3 scripts/sb.py compile examples/quickstart/manifest.json --json
```

若宿主有内置生图工具，按 [生图路由](./references/backends.md) 把编译结果里的完整 `prompt` 和参考图交给它。轻量计划可用：

```bash
python3 scripts/sb.py make brief.json -o 新目录
```

内置工具先 `--prepare`；成图后用 `--prepared` 和 `--import-results` 导回。完整 `sb2:` 选择码写在 brief 的 `code` 字段，单独画风码写在 `style`。计划格式与来源绑定见 [轻量出图](./references/quick.md)。

使用外部图像服务时，把密钥保存在本机环境变量，或运行 `setup --env-template` 后自行填写本机配置；不要把密钥发给 Agent 或提交到仓库。

```bash
python3 scripts/sb.py setup
python3 scripts/sb.py generate examples/quickstart/manifest.json -o /tmp/stylebook-first.png
python3 scripts/sb.py qa /tmp/stylebook-first.png --style C32 --manifest examples/quickstart/manifest.json
```

没有服务时，以下命令生成可交给其他生图工具的任务包：

```bash
python3 scripts/sb.py inbox examples/quickstart/manifest.json -d /tmp/stylebook-inbox
```

真实图像服务调用可能收费；`doctor --deep` 会发起真实测试请求。`qa` 需要独立看图模型或可追溯的外部复核记录，像素检查不能代替整张图片的内容与审美复核。

## 内容规划、复核与返修

1. 读任务与原文，为每个图位摘出依据、关键关系及必要短文字。
2. 锁定本系列画风和色调。明确选择高于项目锁定、长期偏好和出厂默认；只对允许换色的样式改色。
3. 按每一段内容决定场景、流程、比较等结构，不用一种构图套所有内容。
4. 编译并生成后逐张检查事实、文字、裁切与整组一致性，保留失败与返修记录。

整篇文章先做内容计划，再用 `sb.py plan` 检查位置、依据和关系，后续用 `batch` 或逐张编译。见 [内容规划](./references/planning.md) 与 [虚构计划示例](./examples/content-plan-v2/plan.json)；其中 C31 仍待正式准入。

独立复核的 `plan-review` 和 `qa` 支持火山方舟 Coding Plan、Agent Plan、本机已登录的 Claude Code (`claude_cli`)。如何选后端、需要什么环境变量及实际验收范围，见 [后端说明](./references/backends.md)。缺少可用复核者时，不会把未复核结果当作通过。

## 画风、色系与本地偏好

官网 [视觉看板](https://sanshengai.top/tools/image/) 支持用途筛选、样图预览、换色、浏览器内默认与一次性选择；复制 `sb2:` 指令交给 Agent。

```bash
python3 scripts/sb.py style-for wxillus
python3 scripts/sb.py preferences set --scene wxillus --field style --value C01
python3 scripts/sb.py preferences show
```

还可使用 `clear`、`undo`、`forget`，见 [偏好规则](./references/preferences.md)。一次性修改不自动变成长效设置；长期推荐与成长记录的具体条件见 [成长规则](./references/flywheel.md)，不从沉默或自动评分推断喜好。

个人记忆默认留在 `~/.config/sansheng-image/profile/`，可用 `SANSHENG_IMAGE_PROFILE` 指向私有目录；显式目录不存在时，不回退读取默认私有目录。私有画风与个人记忆不在公开包中。

本地构建：

```bash
python3 scripts/sb.py build --gallery
```

在 `gallery/build/` 下启动静态服务器，打开 `index.html`。选择器导出版本化选择 JSON；`--advanced` 生成旧的完整画廊。干净安装没有官网完整样图库，样图备份和还原见 [维护指南](./MAINTAINERS.md)。

## 按用途继续

- 教材：`tb-vocab` 单词参考图与 `tb-grammar` 语法情景图。单词图默认无字，单词标签和精确文字可按排字规则处理；数量、动作、文化与画风需看实际图，教材用途尚未正式验收。
- PPT：先生成静态整页，按需组装 PPTX，在目标软件检查中文；图像页不等于可编辑元素。
- 视频分镜：使用 [分镜手册](./references/scenes/storyboard.md)；当前会话可用 `board --prepare` 保存实际提示词与参考图指纹，产出静态首帧与运动文字说明。不能把两镜探索当作所有跨镜头光线一致性已通过。
- 动态插图：明确要求动态才使用 `python3 scripts/sb.py animate wake`。首次由 Agent 执行 `animate wake setup --install`、`animate wake doctor`，再按 [动态手册](./references/motion-wake.md) 选图、备料、编排与逐片审阅。固定上游独立保存，静态任务不加载动态依赖。
- 旧 HyperFrames 工程：保持原有 `sb.py animate`、`scripts/replay_wake.py` 兼容，依赖与 scene 限制见 [兼容动态流程](./references/motion-scenes.md)。新入口与旧入口的 Node / 渲染器要求不同，按工程所用入口配置。

技术导出、制作审片、用户采用和平台预览分别记录。历史平台反馈不自动适用于新文件；阶段性验证记录见 [收口记录](./references/release-readiness.md)。
