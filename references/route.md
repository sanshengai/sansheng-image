# 路由：按场景读哪几页

入口只做路由。先判断**场景**，再只读该场景的手册和它点名的库；画风、色系、参考图另走选择流程。不要一次读完所有参考文件。

## 第一步：场景

| 用户要的 | 场景手册 | 格式 |
|---|---|---|
| 公众号封面、系列封面、X 封面 | [scenes/wxcover.md](scenes/wxcover.md) | wechat-cover-square / wechat-cover-head / x-image |
| 给文章配图、文章插图、推特单图 | [scenes/wxillus.md](scenes/wxillus.md) | article-summary / article-summary-tall |
| 小红书、微博、图文笔记 | [scenes/xhs.md](scenes/xhs.md) | xhs-cover / xhs-carousel |
| 整页图片型 PPT、把文档做成 PPT | [scenes/ppt.md](scenes/ppt.md) | ppt |
| 信息图、干货图、一图看懂 | [scenes/info.md](scenes/info.md) | infographic |
| 漫画、四格、条漫、知识漫画 | [scenes/comic.md](scenes/comic.md) | comic-* |
| AI 短剧分镜、视频分镜首帧、角色三视图 | [scenes/storyboard.md](scenes/storyboard.md) | storyboard-frame |
| 绘本、音乐或播客封面、教材单词与语法、贴纸 | [quick.md](quick.md) 与 `scenes/scenes.json` | 见 formats |
| **仅当用户明确要动态版**（做成动的、动图、短视频版） | [motion-scenes.md](motion-scenes.md) | article / infographic / xiaohongshu；brand 只做原创品牌动态示范 |

动态版是附加流程：先按上表读对应静态手册；已有同一内容且已采用的静态图时直接复用，缺图或内容确需改变时才制作相应静态图。用户明确要动态才再读 motion-scenes.md。没有明确要求，一律走静态，不主动改成动态。PPT 与音乐封面的动态不开放，仍出静态图。

## 第二步：共享库（场景手册会点名，按需读）

- 哪几段值得配图、配几张：[lib/value-gate.md](lib/value-gate.md)
- 图型怎么选（对比、四象限、流程、人物指要点）：[lib/figure-types.md](lib/figure-types.md)
- 图内文字、字数预算、手机字号：[lib/text-budget.md](lib/text-budget.md)
- 平台尺寸与画幅：[lib/platform-specs.md](lib/platform-specs.md)
- PPT 页型、页规格卡、备注：[lib/ppt-pages.md](lib/ppt-pages.md)
- 出图后检查与返修：[lib/post-checks.md](lib/post-checks.md)

## 第三步：画风与一致性

- 选画风、色系：[selection.md](selection.md)；用途默认与偏好：[preferences.md](preferences.md)
- 保持画风一致（参考图分层、隔离说明、固定人物）：[consistency.md](consistency.md)
- 提示词编译与参考图职责（维护者读）：[compile.md](compile.md)
- 完整写法：[recipes.md](recipes.md)（各类图的页序速查）、[planning.md](planning.md)（发布档计划）、[text.md](text.md)、[qa.md](qa.md)

## 读的顺序

场景手册 → 它点名的库 → 画风选择 → `make`。已在上下文里且未变化的页不重读。
