# 测试矩阵与准入（维护者）

矩阵 `state.json` 的每次 `reviews[]` 和 `report.md` 逐题记录看图来源。默认为 `ark_coding_plan`：固定 Coding Plan 套餐和 `deepseek-v4.1-flash`，发送实际图片，核对返回型号及完整意见；配置、鉴权、限流或响应失败均停止，不自动回退。矩阵看图不调用 `claude_cli` 与 `ark_agent_plan`，它们仅保留显式兼容及历史记录；配图计划复核可显式选用它们，见 [backends.md](backends.md)。

各自无历史上下文的有效模型请求可计入现有脚本的独立双评；由调用者提供的结论须标明来源，未标明记作 `provided_unspecified`，旧缓存无来源记作 `legacy_unknown`，这些不能顶替独立双评。续跑沿用旧看图结论时不改写其来源。当前项目采用 Coding Plan 独立意见加当前 Codex 原生逐项裁决，争议保留双方原文及实际图证据。已有证据经下面的 `matrix-evidence` 专用入口核验，不向旧来源集合加入一个 Root 标签来制造双评通过。

## 当前 Codex 已有证据复用

```bash
python3 scripts/sb.py matrix-evidence <证据包.json> --contract <当前候选合同.json> -o <新输出目录>
```

该入口不调用模型、不生图、不修改合同或注册表。它核对八道当前标准题、完整编译与实际生成参数、原图与原尺寸看图编码摘要、Coding Plan 请求及实际完整响应、Codex 逐项裁决、三次同词独立原图和同题近邻盲审。意见分歧和失败题保留；错图、旧题、缺证据或不一致的解析意见拒绝导入，输出目录已存在也拒绝覆盖。

三次稳定测试的收据可用 `question` 指定当前标准题编号；未指定沿用 T3。三张必须使用该题完整相同参数、各有对应题面 QA 的通过裁决，并包含本轮该题原图，不能拼凑不同题或重复文件。八题准入中的 T3 必过及声明原生文字时 T8 必过独立判断，不随稳定测试题改变。

输出 `matrix-evidence-state.json`、`matrix.png`、`report.md` 和角色一致性并图；不写可触发生图续跑的 `state.json`。`evidence_verdict` 表示证据核验后的质量判据，`admission.verdict` 仍为 `pending`、`formal_admission=false`，必须另完成场景能力边界、正式登记和派生/样图一致性。此入口只验证绑定的记录，不能证明任意外部收据的作者身份，维护者须保留真实原图复审依据。

`admission.matrix_verdict` 只表示八题数量和指定题的自动判据；`admission.verdict` 是正式准入状态。即使前者为 `pass`，同词三次稳定及跨样式可区分仍须另附证据，后者保持 `pending`，不能凭八题数量把合同标为正式准入。来源标签不能代替检查原始看图记录。

## 8 道标准题

题目在 `tests/matrix/questions.json`（当前 v6）：T1 半身表情、T2 两人互动（用 T1 出图作角色参考）、T3 老人与孩子、T4 动物、T5 静物、T6 远景（16:9）、T7 抽象概念（含中文标签）、T8 3:4 中文标题卡。题目对样式保持中立：不指定颜色与材质，角色辨识靠发型、眼镜、发夹这类形状。v4 明确 T1 发夹位于人物自身左侧，并纳入验收条目；T1 和依赖其身份参考的 T2 旧分数不能沿用。v5 把 T7 验收中原有的“番茄计时器位于视觉中心”写进实际出图题面；旧 T7 图不能沿用。原生文字提示词也明确禁止未要求的步骤数字和道具刻度，受影响的文字题须按新编译词重出。v6 把 T2/T5 题面已有的人数与物件数量编进 `content.inventory`，并交独立看图核对；数量按主场景中的不同人物和物件计算，技术细节小窗重复展示同一实体不算新增。此前两题的出图词没有明示排除项，旧 T2/T5 分数不能沿用。改题要升 version，并重跑受影响的样式。

```bash
python3 scripts/sb.py matrix C31 --max-usd 1.0          # 出图 + 看图 + 判定 + 矩阵图与报告
python3 scripts/sb.py matrix C31 --only T7,T8            # 只跑几题
python3 scripts/sb.py matrix C31 --no-review             # 只出图（或只按新规则重算判定）
```

输出在 `out/matrix/<码>@r<修订>/`：每题的图、提示词、看图结论，`state.json`，`matrix.png`，`report.md`，`consistency-T1-T2.png`。
断点续跑：提示词没变且图还在就不重出；图和看图题目都没变、结论完整就不重看；判定每次按最新规则重算。
预算：`--max-usd` 是本轮新增花费上限。密钥无效、需要组织验证、没有服务时整轮停下。

## 准入线

8 题至少过 7；T3 必过；声称能直接写中文的（`text_mode` 含 native）T8 必过；同一提示词出 3 次风格一致；与已入库样式并排看不会混淆；配方里不出现在世画家与工作室名。**不过线就改配方或换掉，不降标准。** 前三条脚本自动判（报告「准入线」一节），后三条人工确认。看图结论有波动，准入时每题看两次，不一致交人复核。

边界缺陷可用 `sb.py qa-contrast` 对旧问题图与返修图做匿名、换序的**相对**比较；判据、原图哈希、两次判断须留档。裁局部时两图用同一裁切框，并保留全图。对照结论不能拼入八题矩阵，也不能代替每题独立双评、同词三次一致性或跨样式区分。

能力标签（`abilities`）只能来自实测：中文 ← T7、T8；角色一致 ← T2；儿童 ← T3；视频帧 ← T6；会不会给无人场景加人 ← T5。

## 样图入库与备份

`styles/*/samples/` 不进 Git 仓库（已写入 `.gitignore`），只在本机和官网。入库脚本（`ingest*.py` 等）照旧把文件写进工作区的 `styles/<码>/samples/`，写完后：

```bash
python3 scripts/samples_sync.py status                    # 与合同登记对比：登记了但缺文件、文件在但没登记
python3 scripts/samples_sync.py backup --dest <仓库外目录>   # 只增不删的备份，输出新增数
python3 scripts/samples_sync.py restore --src <备份目录>     # 换机器或误删后还原（不覆盖内容不同的文件，--overwrite 才覆盖）
```

合同 `samples` 里登记的 `ratio` 要与文件一致——没有样图文件时，选择器按登记比例重建。锚点图（`styles/<码>/anchor.*`）与范例图（合同 `exemplars`）不得放进 `samples/`，它们随仓库分发。

## 注册表与画廊

```bash
python3 scripts/sb.py build                  # 重建公开 registry.json（只含 C 码）
python3 scripts/sb.py build --check          # 只检查 registry.json 是否与目录、合同一致
python3 scripts/sb.py build --private --gallery --samples 样图目录   # 叠加私有 profile，生成本地画廊
```

画廊是单文件 HTML（`gallery/build/`，不进仓库），三页：出厂默认、全部样式（可去掉不要的）、选择器（给出风格码、给 Skill 的一句话、完整提示词）。样图优先取测试矩阵出图，其次 `--samples` 目录里的 `<码>-T3.png`（旧名 `<码>-q1/q2/q3` 分别对应 T3/T6/T7）。
