# 给视频等下游导出已登记素材

只在找已有作品、维护素材或交给视频制作时使用；普通生图不必先建目录。命令从 Skill 根运行，不调用生图 API，也不自动安装依赖。

```sh
python3 scripts/sb.py catalog -o 新目录/catalog.json
python3 scripts/sb.py catalog --purpose 小红书 --aspect 3:4 -o 新目录/xhs.json
python3 scripts/sb.py catalog --previous 旧目录/catalog.json -o 新目录/catalog.json
```

导出从 `styles/*/contract.json` 的样图登记读取身份和生成信息，重新核对登记文件的实际 hash、尺寸与比例；不扫全盘、不另建数据库。`--style C31` 可重复；`--group <id>` 只选已有关系组；`--previous` 接收未过滤目录，输出新增、移除与变化的 ID。它仍核对登记文件，不以文件大小和时间未变冒充内容未变。

`purpose` 优先取单张登记的 `scene`，`purpose_label` 来自原有题材；用途查询匹配这两项或题材文字。合同的 `style_uses` 仅表示该画风可用范围，不作为单张用途证明。缺用途或色系保持空值，可按实际作品另做项目选择；不能自动改写成已验证的用途。画风准入、单张采用、当前／待更新／历史修订分别记录；公开安装没有媒体时输出 `missing`，不下载整库。

动态来源复用 `gallery/motion-data.json`。媒体在外部时显式传 `--motion-root <目录>`；未配置显示 `unconfigured`，指纹变化显示 `changed`。清单已经明确的原图与视频形成 `still_motion` 组；视频尺寸注明来自登记，实际取用时仍须检查媒体。配方许可与图片可用范围分开，未知授权保持 `unknown`。

同题换画风、同构图换色、系列关系不能由文件名推定。需要时另传项目关系文件 `--relations relations.json`：

```json
{
  "kind": "sample-relations/1",
  "groups": [{
    "id": "comparison-1",
    "type": "same_content_styles",
    "condition": "逐张核对的共同题材、布局及保留条件",
    "members": ["素材ID-A", "素材ID-B"],
    "member_sha256": {"素材ID-A": "实际文件摘要", "素材ID-B": "实际文件摘要"},
    "evidence": {"file": "真实生成记录或审查记录.md", "sha256": "记录文件摘要"}
  }]
}
```

支持 `same_content_styles`、`same_composition_palettes`、`series`、`still_motion`。工具拒绝缺来源、未知成员或已变化成员；有记录不等于语义已被机器证明，Agent 仍须核对原图和共同条件。没有依据就把它们当独立作品，不宣传为受控对比。

导出文件含本机根路径，属于项目输入，不提交到公开代码仓。下游按实际媒体选择有限候选，放大审看文字和细节；目录数量、文件可读与技术通过均不代表审美或用户采用。
