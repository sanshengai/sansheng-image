# 维护指南

[返回项目首页](./README.md) · [参与贡献](./CONTRIBUTING.md)

## README 与宣传视频

中文 `README.md` 与英文 `README_EN.md` 保持相同的信息层级：视觉看板入口、卖点、真实样图、动态示例、开始使用、能力边界与文档。

两份文件在看板入口后都有固定视频插入位：

```html
<!-- PROMO_VIDEO:START -->
<!-- PROMO_VIDEO:END -->
```

成片完成后，只替换这两个标记之间的内容，加入相应语言的标题、真实封面和可播放链接。准备好前保持隐藏，不放空播放器、虚构视频地址或失效的播放按钮。视频工程与媒体另行制作，不需要重写整份 README。

`assets/readme/` 是“完整样图库不进仓库”规则的有限展示例外：只收精选、已逐张查看的缩略图与一份原样 GIF，不导入完整网站图库。布局图只调整摆放和尺寸，不修图、不改字、不补画。`provenance.json` 记录实际源文件及 SHA-256、导出尺寸、处理方式与展示字节。它证明素材对应关系，不代替原始生成记录、用途验收或平台验证。

新增媒体时，同步更新：

- 中英文说明、替代文字和图下的真实验证范围。
- `assets/readme/provenance.json`、`ASSET_PROVENANCE.md` 与许可信息。
- `scripts/public_export.py` 的显式公开文件清单，避免安装包里的 README 引用缺图。
- 在 GitHub 实际渲染中检查亮色 / 暗色、窄屏、图片加载、锚点、看板链接及 GIF 播放。图中按钮指向整个可点击图块；图外必须保留纯文字入口。

## 样图与备份

完整展示样图 `styles/*/samples/` 只保留在本机和官网，已写入 `.gitignore`。合同里登记文件名与宽高比；生成器依据登记构建选择器。出图使用合同与参考锚点，不读完整展示图库。

```bash
python3 scripts/samples_sync.py status
python3 scripts/samples_sync.py backup --dest <仓库外目录>
python3 scripts/samples_sync.py restore --src <备份目录>
```

`status` 对比合同并报告旧修订待重出图；备份只增不删。2026-10-13 起登记的新样图须有 `generation` 记录：合同修订、提示词指纹、出图次序、候选挑选和后期编辑情况；早期缺记录的图继续标“历史样图”。参考锚点和出图范例仍随包提供。

## 页面构建与品牌

`gallery/picker.html` 是生成页面；布局修改从 `scripts/stylebook/build.py` 的对应生成逻辑开始，再重建。不要只改生成结果。

维护者可在构建时设置：

```bash
SANSHENG_IMAGE_BRAND_DIR=<品牌图片目录> python3 scripts/sb.py build --gallery
```

目录中提供可解码的 `logo.webp` 与 `logo-poster.webp`。未提供时只登记文件名；网站部署方提供品牌图片与展示图库。品牌字标为静态 Logo，动态演示与其分开。

## 本地检查

```bash
git config core.hooksPath .githooks
python3 -m pytest tests/test_docs_check.py tests/test_public_export.py -q
python3 scripts/public_export.py --refresh-manifest
python3 scripts/public_export.py --check-manifest
```

公开导出还会检查缺文件、私有画风、文本中的凭证形态与个人路径。全量测试见 `python3 -m pytest tests -q`；真实动画测试需要本机渲染依赖，缺工具的跳过不算实际渲染已通过。不要把源码目录检查代替实际下载包检查。
