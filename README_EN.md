# 叁笙生图 · Sansheng Image

[中文](./README.md) | **English**

Local v1.1 candidate status (2026-10-16): the selector has passed local browser checks; this round did not verify the deployed version. Storyboard generation in the current Agent session uses `sb.py board board.json -o frames --prepare`; this compiles tasks without invoking a backend and does not import or approve generated images. Long-article representative pages and a two-shot storyboard remain trials, with content and lighting limitations. See the [closure record](references/release-readiness.md) (Chinese). No release was published in this round.

**Choose one visual style and keep it across a set of static images.** This Agent skill plans illustrations from source content, compiles style and palette rules, generates images through an available image tool, and checks each result. Article covers and illustrations are its first use case. Slide images, cards, infographics, stories, comics, audio covers, and stickers are at varying trial stages.

> Public beta. The local maintainer version contains 81 public style contracts. C91, C92, C93, C94, C95, C96, C97 and C98 (8 styles) passed current style admission, with 344 scene-coverage entries and 108 palette-coverage entries completed; original failed trials, repair lineage and limitations are retained. The other 73 remain pending under rebuilt revisions and have not been retested. The new version is not yet published; the published website still has 73 styles. Installation packages exclude gallery samples. Style admission does not approve every actual use. See the capability boundaries below.

## Preview

| Style | Real single-image test output |
|---|---|
| C01 warm hand-painted animation | ![C01 story image](./assets/C01-story-sample.png) |
| C32 aged-paper technical briefing | ![C32 infographic](./assets/C32-infographic-sample.png) |

These images show two separate successful tests, not a validated cross-format series. File hashes and known generation routes are documented in [ASSET_PROVENANCE.md](./ASSET_PROVENANCE.md).

## How decisions are made

1. Read the task and source text; extract evidence, relationships, and only the short labels needed in an image.
2. Select one style for the series. An explicit choice wins over a project lock, saved preference, or factory default. Palette changes must respect each style's recoloring rule.
3. Choose a scene, flow, comparison, or other structure **per image** according to its content. A preference for character art must not turn a numerical comparison into an unrelated portrait.
4. Compile, generate, and inspect the actual output. Failed or repaired images remain in the record and do not automatically count as style admission.

Temporary edits affect only the current task. Explicit long-term preferences live in a local file and can be viewed, undone, or forgotten. Observed preferences only improve ranking after consistent active choices in three independent tasks; silence and automatic scores are not used to infer taste.

## Install and try

Install from GitHub with `npx skills add sanshengai/sansheng-image -g` (Node.js required), then restart your AI assistant. Use an assistant such as Claude Code or Codex that can run commands on your computer. Python 3.10+ is required.

For manual installation, clone the repository and link it into your Agent's skill directory. The repository also includes a Claude Code plugin manifest:

```bash
git clone --depth 1 https://github.com/sanshengai/sansheng-image.git
cd sansheng-image
python3 -m venv .venv
. .venv/bin/activate
python3 scripts/sb.py doctor
python3 scripts/sb.py compile examples/quickstart/manifest.json --json
```

A shallow clone (`--depth 1`) is recommended: the repository no longer contains sample images, so the only difference from a full clone is history, and a full clone also downloads the old sample images kept there; image generation only needs the contracts and anchors.

Contributors should run `git config core.hooksPath .githooks` to enable the staged-file redaction guard.

**Maintainers: sample images are not in the repository.** Gallery-only images (`styles/*/samples/`) live only on the maintainer's machine and the website and are in `.gitignore`; contract registrations (file names and ratios) stay, and the selector rebuilds from them when the files are absent. After adding samples run `python3 scripts/samples_sync.py status` (compare with contract registrations and list samples generated under an older contract revision) and `python3 scripts/samples_sync.py backup --dest <dir outside the repo>` (add-only backup); use `restore --src <backup dir>` on a new machine. Samples registered from 2026-10-13 on must carry a `generation` record in the contract (contract revision at generation time, prompt fingerprint, attempt number, how many candidates it was picked from, whether it was edited); the selector labels samples from it, and older ones are shown as historical. Anchor images (`styles/<code>/anchor.*`) and exemplar images used for generation are not under `samples/` and stay in the repository.

For an Agent with a built-in image generator, pass the full compiled `prompt` and any listed references to that tool. For an external image provider, configure credentials locally with `python3 scripts/sb.py setup --env-template` and then:

```bash
python3 scripts/sb.py setup
python3 scripts/sb.py generate examples/quickstart/manifest.json -o /tmp/stylebook-first.png
python3 scripts/sb.py qa /tmp/stylebook-first.png --style C32 --manifest examples/quickstart/manifest.json
```

The QA command requires a working independent vision reviewer, or a separately supplied review record. A missing reviewer is not a passing result. If no generator is configured, `python3 scripts/sb.py inbox examples/quickstart/manifest.json -d /tmp/stylebook-inbox` makes a portable prompt package. `doctor --deep` makes real provider calls and may incur charges. PPTX assembly needs the optional `requirements-ppt.txt` dependencies.

## Choose and adjust

- `python3 scripts/sb.py style-for wxillus` shows the article illustration default and why it was selected.
- `python3 scripts/sb.py build --gallery` creates the offline selector at `gallery/build/index.html`. It exports versioned selection JSON that the Agent can parse. The public selector is live at https://sanshengai.top/tools/image/ (pick a use, a style and a palette, then copy one `sb2:` line for the Agent). Serve `gallery/build/` over a local static server to use the same page offline (`img/` holds lazily loaded samples); `--advanced` builds the old all-in-one gallery.
- `python3 scripts/sb.py preferences set --scene wxillus --field style --value C01` saves a style preference. See [preference controls](./references/preferences.md).
- For an article, create a source-grounded plan and run `sb.py plan` before generating. See [planning rules](./references/planning.md) and the [synthetic plan](./examples/content-plan-v2/plan.json); its C31 style remains pending admission.

Private styles and personal memory are excluded from the public package. Local memory defaults to `~/.config/sansheng-image/profile/` and can be relocated with `SANSHENG_IMAGE_PROFILE`. Provider support and its verified limits are listed in [backends.md](./references/backends.md).

## Motion edition（补充说明）

动态版只在用户明确要求时使用，默认仍是静态图；覆盖公众号配图、信息图、小红书，品牌仅作原创动态示范，PPT 与音乐封面的动态不开放。流程见 [references/motion-scenes.md](./references/motion-scenes.md)（中文）。

额外依赖：`requirements-motion.txt`（只有 numpy；Pillow 已是基线依赖）；Node.js 22+、HyperFrames 0.8.140、ffmpeg/ffprobe、本机浏览器需自行安装，由 `python3 scripts/sb.py animate doctor` 检查，不会自动下载。HyperFrames 是 Apache 2.0 的外部运行依赖，不随仓分发；颜色键 `cutlib` 是原创 MIT 模块，不调用 macOS 的 Vision 工具，也没有自动 Vision 回退。主要流程为电脑网站点选、复制指令后在 Skill 运行，平台上传兼容性尚未验证，不承诺任何平台可直接发布。

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) (written in Chinese).

- Recommend a style: use the "推荐画风" (recommend a style) issue template.
- Report a problem: use the "报问题" (bug report) issue template.
- Code or documentation changes: open a pull request.

Website submission is not live yet and will arrive with the contribution feature; for now please use a GitHub issue or pull request.

## Credits and license

See [CHANGELOG.md](./CHANGELOG.md), [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md), and [MIT LICENSE](./LICENSE). Style and workflow ideas were adapted from [threerocks/hand-drawn-styles](https://github.com/threerocks/hand-drawn-styles), [JimLiu/baoyu-skills](https://github.com/JimLiu/baoyu-skills), and [yang0/handraw-style](https://github.com/yang0/handraw-style). Maintained by 叁笙 (sansheng).

Maintainers may set `SANSHENG_IMAGE_BRAND_DIR=<brand-image-directory>` when running `python3 scripts/sb.py build --gallery` to supply `logo.webp` and `logo-poster.webp`. Both must be decodable WebP files. Without this input, the builder only lists their filenames; website deployments supply brand and gallery images separately.
