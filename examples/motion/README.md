# 已审动态工程与反例

这些工程保留实际实验的图片、字体、完整动作代码、导演稿及关键状态联系表。新增四类制作工程经过当前 Agent 的成片审片，尚未记录用户采用或本片平台上传。2026-10-20 实际用户审片后，相册与雨水解说可作制作参考；城灯两版均未通过，保留为反例。审片评价不等于官网或平台发布采用，不能从三例推断任意图片都适合做成动态。

| 工程 | 审片结果与用途 | 已知边界 |
|---|---|---|
| [spring-album](spring-album/DIRECTOR.md) | 两版均可，差异不明显；参考完整相纸及观看顺序 | 没有证明新增方法明显更好；其他题材不强制翻书或弹出照片 |
| [rainwater](rainwater/DIRECTOR.md) | 用户认为增加方法的本版更好；参考真实路径与逐步解说 | 路径锚点只适用于本图，知识和文字仍须逐图核对 |
| [city-light](city-light/DIRECTOR.md) | **反例：两版均未通过**；研究无意义的分割及过弱变化 | 不作为氛围模板推荐；人物与城市应在有表达收益的完整场景中呈现 |

历史三例为 1280×720、24 fps、8 秒、静音，HTML 是当时 API 实际输出。新增四例为 9～16 秒，含协调者返修；无需再次调用生成模型即可复跑。新例素材为同尺寸 WebP 压缩衍生，旧例原文件保持不变，指纹及来源见各例 ORIGIN.json 与 manifest.json。原图为 AI 生成的实验素材，完整原始生图收据未随此包分发。`manifest.json` 记录文件指纹，不能用指纹证明审美通过。

新增制作参考：

| 工程 | 可以学到的处理 | 本图特有条件 |
|---|---|---|
| `paper-diorama` | 完整合影作为纸艺景片，脚边支点与前后纸台共同开合；异步等 DOM 完成绘制 | 三人全身完整，另有无人草地；不是人物转身或走路 |
| `message-journey` | 同一条信息保留原文，依次分类、程序核对、人工接手；文字与高亮共用位置 | 分类与核对职责来自原文，程序不能保证语义正确 |
| `rain-bookshop` | 远近雨幕、雨棚遮挡、檐口落水、落地点水圈及对应反光 | 完整横向雨街舞台；不是随意给任何照片加雨 |
| `travel-route` | 同一旅行标记走到一站，才打开该站的独立照片；纸盖有实际铰链与背面 | 三张不同照片可完整提取，不是把同一风景撕开 |

在 Skill 根目录运行，先按 [动态手册](../../references/motion-wake.md) 配置可选运行环境。然后任选一例（例如 message-journey），输出必须是全新、独立目录：

```sh
python3 examples/motion/prepare.py message-journey --out /tmp/message-journey-example
python3 scripts/sb.py animate wake preview /tmp/message-journey-example
# 实际查看素材、时间联系表和原尺寸关键帧后再渲染。
python3 scripts/sb.py animate wake render /tmp/message-journey-example
```

另一个制作参考可选 `spring-album`；`city-light` 仅供复现失败和对照分析。`prepare.py` 通过正式 `animate wake init` 建工程后装入该例源码，不执行后台模型调用，不覆盖已有输出。渲染后使用返回的 `review.html` 连续看至少两轮，再按手册填写实际审片记录。新工程不继承参考片的用户评价；预备工程、技术导出和发布采用是不同状态。

迁移时读取 [制作方法](../../references/motion-methods.md)，更换本图材料后重新确定锚点、支点、留白、动作极值和节奏。不要只替换文件名却沿用旧坐标。

本目录不包含上游 lemo-wake 源码。运行依赖保持其作者、固定版本与 CC BY-NC 4.0 许可，见 [许可边界](../../references/motion-wake.md#来源与许可边界)。字体为 Noto Serif CJK SC Bold 的既有子集，名称与版权可在字体 name 表读取，遵循 [SIL Open Font License 1.1](FONT-LICENSE.txt)；[字体上游](https://github.com/notofonts/noto-cjk/tree/main/Serif)。不要将字体或外部运行环境改标为本项目的 MIT 代码。
