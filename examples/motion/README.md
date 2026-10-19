# 三个完整动态工程候选

这些工程保留实际实验的原图、字体、完整动作代码、导演稿及关键状态联系表。它们用于理解制作和迁移，未记录用户采用；不替换官网已采用作品。不能从三例推断任意图片都适合做成动态。

| 工程 | 学习内容 | 已知边界 |
|---|---|---|
| [city-light](city-light/DIRECTOR.md) | 保留完整人物，局部灯源与河面反射联动 | 原图与局部并置是本例的构图选择，不是默认模板；连续观看后再判断灯光节奏 |
| [spring-album](spring-album/DIRECTOR.md) | 完整相纸承载照片，按顺序展开并留出观看时间 | 相纸移动仍需判断支撑感；其他题材不强制翻书或弹出照片 |
| [rainwater](rainwater/DIRECTOR.md) | 固定场景，按真实路径高亮并放大当前环节 | 路径锚点只适用于本图，知识和文字仍须逐图核对 |

每例均为 1280×720、24 fps、8 秒、静音。HTML 是实验 API 的实际输出，未由协调者改写；无需再次调用生成模型即可复跑。原图为 AI 生成的实验素材，完整原始生图收据未随此包分发。`manifest.json` 记录文件指纹，不能用指纹证明审美通过。

在 Skill 根目录运行，先按 [动态手册](../../references/motion-wake.md) 配置可选运行环境。然后任选一例，输出必须是全新、独立目录：

```sh
python3 examples/motion/prepare.py rainwater --out /tmp/rainwater-example
python3 scripts/sb.py animate wake preview /tmp/rainwater-example
# 实际查看素材、时间联系表和原尺寸关键帧后再渲染。
python3 scripts/sb.py animate wake render /tmp/rainwater-example
```

也可将 `rainwater` 换成 `city-light` 或 `spring-album`。`prepare.py` 通过正式 `animate wake init` 建工程后装入该例源码，不执行后台模型调用，不覆盖已有输出。渲染后使用返回的 `review.html` 连续看至少两轮，再按手册填写实际审片记录；预备工程、技术导出和用户采用是不同状态。

迁移时读取 [制作方法](../../references/motion-methods.md)，更换本图材料后重新确定锚点、支点、留白、动作极值和节奏。不要只替换文件名却沿用旧坐标。

本目录不包含上游 lemo-wake 源码。运行依赖保持其作者、固定版本与 CC BY-NC 4.0 许可，见 [许可边界](../../references/motion-wake.md#来源与许可边界)。字体为 Noto Serif CJK SC Bold 的既有子集，名称与版权可在字体 name 表读取，遵循 [SIL Open Font License 1.1](FONT-LICENSE.txt)；[字体上游](https://github.com/notofonts/noto-cjk/tree/main/Serif)。不要将字体或外部运行环境改标为本项目的 MIT 代码。
