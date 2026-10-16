# 选择记录：自动推荐与手动调整

## 一行选择码

Skill 现已接受 `sb2:<用途>/<画风>[@r<修订>][-<色系>[.L<深浅>S<鲜灰>]][-hex.<RRGGBB>.<RRGGBB>…]`。

- `sb2:wxcover/C30`：公众号封面，原色。
- `sb2:xhs/C35-earth`：小红书卡片，大地色。
- `sb2:info/C42-hex.1F6F8B.F4F1E8`：信息图，指定两种品牌色。
- 指定修订必须等于当前合同；旧修订不静默替换。品牌 HEX 与命名色系二选一；锁色画风拒绝换色。
- 用途取自 `scenes/scenes.json`；它决定默认格式。码与任务用途不一致时明确拒绝，由用户或 Agent 根据原请求纠正。
- 表达结构、每张图的主体、文字仍由 Agent 读内容后决定；手动改动可以直接用语言提出。

```sh
python3 scripts/sb.py selection normalize --code 'sb2:info/C42-hex.1F6F8B.F4F1E8' -o selection.json
```

`sb1:` 继续兼容，但不含用途，须另传 `--scene`。官网详情的「复制给 AI」会复制带 `sb2:` 码的自然语言指令。Agent 先校验选择码，再结合内容形成逐图计划；轻量 `make` 的 brief 用 `"code": "sb2:wxillus/C91"` 接收完整选择码，`style` 字段只接收画风码（如 `C91`），不能把完整选择码塞进 `style`。


用户界面只需显示 **表达方式（默认自动）／画风／色调**；用途从任务得到。内部先读内容、逐图判信息形状，再推荐画风和配色，最后决定构图。画风卡片里的演示构图不是每张文章图的固定模板。

`scripts/stylebook/selection.py` 是选择的规范化入口。网页导出与 Agent 回传都用同一份 `version: 1` JSON；`sb1:` 只传简单选项，不能编码品牌 HEX、逐图修改或来源与锁定。

```json
{
  "version": 1,
  "scene": "wxillus",
  "series_id": "article-1",
  "style": {"code": "C31", "revision": 4, "source": "explicit", "scope": "series", "locked": true},
  "palette": {"family": "orig", "light": 0, "sat": 0, "source": "factory", "scope": "series", "locked": false},
  "expression": {"form": "auto", "structure": "auto", "source": "factory", "scope": "series", "locked": false},
  "items": {
    "03": {
      "expression": {"form": "structure", "structure": "compare", "source": "explicit", "scope": "item", "locked": true},
      "text_direction": "标签短一些，条件不能省"
    }
  }
}
```

`source` 允许 `explicit / project / preference / observed / author / factory`。优先级是本次指定、项目锁定、明确场景或通用偏好、观察倾向、作者档案、出厂默认；来源只用于解释，不能越过合同、原文事实或用户锁定。项目 ID、场景和单图 ID 必须按实际任务匹配。风格修订、色系、品牌 HEX、锁色规则、未知字段和未知版本都在入口校验，冲突明确拒绝。`expression.density` 可取 `auto / sparse / balanced / dense`；固定密度必须与计划中的要点数吻合，否则 `selection apply` 拒绝，不能靠删原文要点硬凑。

本地选择器由 `sb.py build --gallery` 生成，公开合同数量以当前目录数据为准；2026-10-16 实际官网显示 81 种。私有画廊需 `--private` 和本地 profile。默认画廊供人按用途挑选画风、色系并复制指令；旧的 JSON 往返界面通过 `--advanced` 生成。收到规范化 JSON 或选择码后用 `selection normalize` 校验；导入不支持的版本、过时修订、锁色冲突、缺少 HEX 的品牌色会给出诊断。网页只做选项和参考图，不读原文、不自行编译最终提示词。不要把页面可选、样图或色块当成当前任务的成图验收证据。

自动推荐用 `selection recommend --scene wxillus --shapes story,parts`，返回场景候选及每张信息形状的表达范围。目录没有声明某一用途时显示 `unverified`，不臆断“不能用”；旧正式准入与用途实测仍分开。用户锁定画风后，内容需要网格时允许在该画风内换构图，不暗换画风。密集文字与不擅长原生写字的画风，可建议少字或事后排字；最终以计划、编译和成图验收为准。

```bash
python3 scripts/sb.py selection normalize --scene wxillus --code 'sb1:C31@r4-orig' -o selection.json
python3 scripts/sb.py selection apply --plan plan.json --input selection.json -o plan-selected.json
python3 scripts/sb.py selection impact --plan plan.json --before before.json --after after.json
```

`apply` 只处理机器可确定的画风、色调、表达形式与结构。`text_direction` 是宿主 Agent 改写短文案的要求，必须对着原文和 `points` 修改 `text.items`，再跑计划、编译及看图核对；不能机械截断原文。局部选择只修改对应图片。选择作用后的 `plan` 保留原文 hash 与必要要点，不能凭选择记录重造事实。

`impact` 默认把修改系列母版当作**局部候选**：其余图继续引用此前通过的母版，不失效。要替换全系列母版并让其他图引用新图时加 `--propagate-master`，返回全组受影响 ID。执行者必须记录实际使用的母版图片及 hash；单独修第三张时不能悄悄把它传播到其他图。只改 `source/locked` 等解释元数据不会重画；整组换色或换画风会重画全组。参考图文件变动仍由 `batch` 的内容 hash 指纹处理。

选择记录不等于个人偏好。一次性修改只进本次选择；用户明确要求“以后如此”才写偏好设置。主动改选的观察事件按 `references/preferences.md` 处理，事实错误返修不计审美偏好。
