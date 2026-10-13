# 图里的文字

三种基础档与一种混合档（合同 `recipe.text_mode` 标明样式适合哪几档；格式有默认档）：

| 档位 | 做法 | 适合 |
|---|---|---|
| `native` | 模型直接画字：清单 `text.items` 逐条给出原文，编译器用「」括起并要求逐字 | 标题短、样式本身能写中文（T8 过线） |
| `overlay` | 画面留出干净空白，导出时按坐标精确排字；PPT 插画模式另由 `pptx` 写成可编辑文字 | 字多、命令必须逐字正确、样式写字不稳 |
| `hybrid` | 短标题由模型按画风原生呈现，命令与操作说明留白后精确叠字 | 标题材质是画风一部分，但命令、网址等必须清晰且逐字正确 |
| `none` | 不许出现任何文字 | 插画、分镜、绘本页 |

要点：
- 合同可要求画面里出现某些文字角色（`recipe.required_text_roles`，如 C77 的 `ghost` 影字）；`text.items` 缺必需角色会在编译期报错，写上的条目就是该图的「已请求文字」，不会再被「不要未请求文字」条款挡掉。合同没声明的角色照旧只按清单逐字出现。
- 清单 `text.mode` 必须在合同 `recipe.text_mode` 之内（`none` 除外）；不在时编译和 `sb.py plan` 报错并给出可选档位。
- 原生写字每条尽量短（标题 ≤ 14 字，标签 ≤ 8 字），条数少；长句改 overlay。
- 验收时逐字比对，错一个字就返修；中文不稳的样式（能力标签 `chinese_text` 为 weak）默认走 overlay。
- 字体描述写在 `text.font`（例如「圆润的立体黏土字」），不写字体品牌名。

## 位图精确叠字

在 `text.items` 的每一项给出 `text` 和 `box: [x, y, w, h]`；坐标是最终导出图的 0–1 比例。可给 `font_px`、`min_px`、`weight`（regular / bold）、`font_family`（默认 `sans`，可选 `serif` 使用中文衬线字，或 `hand` 使用手写字）、`color`、`align`。区域重叠、文字在最低字号下放不进区域、中文字体缺失都会拒绝导出；不会静默缩成难读小字。`serif` 在 macOS 查找 Songti SC，在 Linux 查找 Noto Serif CJK，在 Windows 查找宋体常规体；`hand` 查找系统自带或用户安装的 Hannotate SC，目前只在装有该字体的环境可用。缺所需字体或字重时明确报错，不会悄悄换成黑体。叠字在裁切、缩放后执行，所以额外的方形安全区预览也包含最终文字。

同一行要给末尾词着色时，保留一条完整的 `text`，并加 `spans`：例如 `{"role":"subtitle","text":"手机 · 硬盘 · 云盘","spans":[{"text":"手机 · 硬盘 · ","color":"#FFFFFF"},{"text":"云盘","color":"#0E926F"}], ...}`。分段必须逐字拼回完整文字；目前只支持单行。这样验收仍将它视为一条副标题。

英文 ghost 要压在标题后方时，把 ghost 条目放在标题条目前，并标 `"role": "ghost", "layer": "behind_title"`；只有这对 ghost／标题的区域允许重叠。ghost 先画，透明物件随后覆盖它，最后再排主标题，因此长 ghost 也可从标题后延伸到右侧拼贴下。其他文字与透明物件的区域相交仍会拒绝，成品还须确认 ghost 仍可辨且不抢标题。

毛绒、毛毡画风可在单条文字上加 `"effect": "felt"`，叠字器会在原字形上加确定性的柔边、轻压纹和细小纤维点；默认 `flat` 保持原有纯色字。文字仍须在最终图逐字看图验收。

浅色干净底板上的精确文字可给单条 `"require_blank": true`：导出前检查该文字盒的深色物件或线条是否超过 2%，若是则拒绝把字盖在物件上。它是可选的保守预检，适合浅色底板；深色底、复杂纹理或浅色物件仍要人工看底图与成品。手绘样式如 C31 要把叠字字体设为 `hand` 才可能保持手写感，系统没有 Hannotate SC 时应明确失败并改用可用字体或原生短字方案。

批量出图时设 `text.mode = overlay`，编译器要求模型只画无字底图，导出后叠字，最后按成品逐字验收。命令中的 `-`、`/` 等字符在 overlay 档也必须吻合。单张已有底图可用：

```bash
python3 scripts/sb.py export 底图.png --format infographic --overlay 叠字.json -o 成品.png
```

可运行样例：`examples/overlay/notebooklm-base.png` ＋ `notebooklm-overlay.json` → `notebooklm-final.png`；`notebooklm-mobile-390.png` 是 390 px 手机预览。这个样例用于验证排字与命令，不能代替 C31 风格准入评审。

封面若需要严格控制居中方图里的物件位置，可先用生图入口生成透明 PNG 物件，再在同一叠字 JSON 增加 `image_layers`。每层的 `path` 相对 JSON 所在目录，必须在该目录内且图片真的带透明像素；`box` 是最终横图的相对坐标。导出器把物件按比例完整放进盒子，先合成物件、后排准确文字，并拒绝物件盒与文字盒相交。标签可在 `role: tag` 条目中用 `pill` 绘制低调胶囊，配 `align: center`、`valign: center` 让文字居中。真实居中方图仍须用 `wechat-cover-head` 导出的 `*-square.png` 逐张验收；盒子通过不代表生图物件的材质、徽章数量或全文内容合格。

```json
{"mode":"overlay","image_layers":[{"path":"camera-cutout.png","box":[0.50,0.27,0.145,0.42]}],"items":[
  {"role":"title","text":"旧相机\n再拍一卷","box":[0.355,0.22,0.14,0.25],"font_px":31,"min_px":31,"weight":"bold","color":"#FFFFFF"},
  {"role":"tag","text":"摄影 / 修复","box":[0.355,0.70,0.135,0.065],"font_px":11,"min_px":11,"align":"center","valign":"center","pill":{"fill":"#101114","outline":"#0E926F","radius_px":12}}
]}
```

同一页要保留风格化原生标题，又要放准确命令时，设 `text.mode = hybrid`；每条文字标 `render: native` 或 `render: overlay`，并写 `reserve` 指定后者的留白。原生条目只进入生图提示词；叠字条目须给最终画布的 `box`、字号与颜色，只在导出后写入。`sb.py plan` 会拒绝缺任一类文字、缺留白或叠字区域冲突；`sb.py export --overlay` 可直接读取该页的 `text` 对象，只绘制 `render: overlay` 的条目。批量流程按最终成品核对所有文字，混合档用严格逐字比对，命令的空格、连字符和斜杠均不能错。

若某样式的原生黏土字条款会误拒后期排入的功能文字，可在合同中单列 `recipe.text_style_hybrid` 和 `qa.mode_overrides.hybrid`（完整的 `must_see` / `must_not_see`，可选 `when_text`）。编译与验收仅在 hybrid 档使用这组规则；native、none 仍使用原合同条款。混合档必须继续核对标题材质、功能文字所在留白区、逐字正确性和内容事实，不能借模式覆盖放宽全图画风要求。

```json
{"mode":"hybrid","reserve":"a shallow blank clay inset below the title","items":[
  {"render":"native","role":"title","text":"第一步：安装","position":"top"},
  {"render":"overlay","role":"code","text":"uv tool install notebooklm-mcp-cli","box":[0.07,0.37,0.86,0.09],"font_px":58,"min_px":58,"weight":"bold","color":"#4A342D","align":"center"}
]}
```

PPT / 课件如需可编辑文字，继续用 `text.mode = overlay` 和 `reserve`，不填 `box`，之后运行 `sb.py pptx --mode illustration`；批量导出的中间插画仍按无字图验收。


## 深色或指定色平底的精确文字预检（`blank_background`）

`require_blank: true` 单独使用时，仍是原来的浅底检查：文字盒缩小后，亮度低于 170 的像素超过 2% 就拒绝。这条分支没有改动，纯深底仍会被它拒绝。

要在深色底，或任何已知颜色的平底上排字，在同一条文字上显式加入 `blank_background`：

```json
{"role":"code","text":"uv tool install notebooklm-mcp-cli","box":[0.07,0.37,0.86,0.09],
 "font_px":50,"min_px":50,"require_blank":true,"blank_background":"#0E0E10","color":"#FFFFFF"}
```

规则：

- `blank_background` 只接受 `#RRGGBB` 六位十六进制，不接受 alpha、简写、颜色名、空值或带空白的写法。
- 必须同时写 `require_blank: true`。缺省或 `false` 会被拒绝，不会因此退回无检查。
- 字色必须显式写出：普通条目写 `color`，带 `spans` 的条目每一段都写 `color`，都要是 `#RRGGBB`。
- 字色与声明底色的亮度差至少 80（0.2126R + 0.7152G + 0.0722B）。它只保证基本明暗区分，不代表 WCAG 对比度。
- 不能与 `pill` 同用，因为胶囊会改变字下面实际的底色。
- 检查直接在原尺寸的文字盒 crop 上做，不缩小。只要有任一 RGB 通道与声明色的绝对差大于 32，该像素就算异物。异物像素占文字盒面积超过 2% 即拒绝。深底上的白物件、更深的物件，以及明显偏离声明色的渐变或底色，都会被拒绝。
- 工具不会自动猜背景色，也不会取最常见色。阈值固定，没有配置项，也没有关闭开关。
- 文字盒为空时拒绝，包括空画布和取整后宽或高为 0 的盒子。

限制：这只是像素预检。微弱纹理，或与声明底色相差不超过 32 的同色物件，不保证能被识别。通过预检不代表风格或内容合格，原图和成品仍要人工逐张复审，包括文字清晰度、内容和事实。
