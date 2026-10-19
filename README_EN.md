# 叁笙生图 · Sansheng Image

[中文](./README.md) | **English**

**An image-generation Skill for your Agent.** Sansheng Image is an open-source Skill for Claude Code, Codex, and other Agents. Give your Agent an article or an idea, choose a style, and create a consistent set of images.

Use it for WeChat article covers and illustrations, Xiaohongshu image posts, X images, slide images, infographics, and static storyboard frames. Images are generated with your Agent’s image tool, a configured image service, or an external tool using the compiled prompts. Validation varies by style and use case; see the capability boundaries below.

[Explore styles and samples](https://sanshengai.top/tools/image/) · [Install](#install-and-try) · [Releases](https://github.com/sanshengai/sansheng-image/releases)

Try asking your Agent: “Create a cover and three illustrations for this article, using one consistent style.”

> Public beta: browse 81 public style contracts and 18 named palettes. Eight styles currently pass admission. Installation packages exclude the website sample library; see the capability boundaries for validation scope.

## Preview

| Use and style | Real single-image test output |
|---|---|
| Article illustration · C01 warm hand-painted animation | ![C01 story image](./assets/C01-story-sample.png) |
| Explanatory infographic · C32 aged-paper technical briefing | ![C32 infographic](./assets/C32-infographic-sample.png) |

These images show two separate successful tests, not a validated cross-format series. File hashes and known generation routes are documented in [ASSET_PROVENANCE.md](./ASSET_PROVENANCE.md).

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

## How decisions are made

1. Read the task and source text; extract evidence, relationships, and only the short labels needed in an image.
2. Select one style for the series. An explicit choice wins over a project lock, saved preference, or factory default. Palette changes must respect each style's recoloring rule.
3. Choose a scene, flow, comparison, or other structure **per image** according to its content. A preference for character art must not turn a numerical comparison into an unrelated portrait.
4. Compile, generate, and inspect the actual output. Failed or repaired images remain in the record and do not automatically count as style admission.

Temporary edits affect only the current task. Explicit long-term preferences live in a local file and can be viewed, undone, or forgotten. Observed preferences only improve ranking after consistent active choices in three independent tasks; silence and automatic scores are not used to infer taste.

## Choose and adjust

- `python3 scripts/sb.py style-for wxillus` shows the article illustration default and why it was selected.
- `python3 scripts/sb.py build --gallery` creates the offline selector at `gallery/build/index.html`. It exports versioned selection JSON that the Agent can parse. The public selector is live at https://sanshengai.top/tools/image/ (pick a use, a style and a palette, then copy one `sb2:` line for the Agent). Serve `gallery/build/` over a local static server to use the same page offline (`img/` holds lazily loaded samples); `--advanced` builds the old all-in-one gallery.
- `python3 scripts/sb.py preferences set --scene wxillus --field style --value C01` saves a style preference. See [preference controls](./references/preferences.md).
- For an article, create a source-grounded plan and run `sb.py plan` before generating. See [planning rules](./references/planning.md) and the [synthetic plan](./examples/content-plan-v2/plan.json); its C31 style remains pending admission.

Private styles and personal memory are excluded from the public package. Local memory defaults to `~/.config/sansheng-image/profile/` and can be relocated with `SANSHENG_IMAGE_PROFILE`. Provider support and its verified limits are listed in [backends.md](./references/backends.md).

## Capability boundaries

The 81 public style contracts and 18 named palettes are browsing and compilation choices. Only C91–C98 currently pass style admission; the other 73 remain pending. A successful single image does not establish reliable output across every scene or palette.

Slide images and optional PPTX assembly are distinct from editable slide elements; Chinese rendering still needs checking in the target presentation app. Storyboard output consists of static first frames and written motion instructions. Image generation requires an available Agent image tool or a configured service; a compiled prompt alone is not a generated image.

## Landscape motion illustrations

The first batch targets illustrations inside WeChat articles and posts on X: 16:9 output, a 1280×720 master, and a silent loop whose duration follows the action and reading time. The first three selected films are “A City in Three Inks,” “How to Verify a Claim,” and “Night Unfolds from a Book,” covering a material process, a three-step explanation, and a story illustration. Film review is complete; public release and platform upload checks are recorded separately. The gallery preserves the actual source image and offers pause, replay, enlargement, GIF, and MP4 downloads.

On 2026-10-19, all three selected films were uploaded and previewed as 960×540 GIFs and 1280×720 MP4s in X desktop drafts, without posting. Mobile playback and post-publication transcoding remain unverified. On 2026-10-18, a user confirmed that an earlier 960×540 GIF, “How to verify a claim,” played correctly with clear text in a WeChat article phone preview. That feedback applies only to the file tested then; phone-preview feedback for the three new films and automated article upload remain pending. Ordinary animated-GIF image posts on Xiaohongshu remain unverified. Slides, covers, and static video storyboard frames are outside this motion batch.

Use `python3 scripts/sb.py animate wake` for the opt-in motion workflow: the current Agent inherits article, style and palette, checks materials, directs the action, then previews, renders and reviews it. The Agent runs `animate wake setup --install` and `animate wake doctor` once; a pinned lemo-wake runtime lives outside Skill discovery, so users keep one Skill entry. Each render includes a side-by-side source/looping-film review page and an append-only review record. Technical success remains a candidate, not a guarantee that every image is suitable for animation. See [motion-wake.md](./references/motion-wake.md) for the workflow and experiment findings, [production methods](./references/motion-methods.md) for atmosphere, paper and explanation, and [three runnable research examples](./examples/motion/README.md) for complete assets, code and key states. User adoption of these examples is not recorded. This path needs Node.js 18+, upstream Playwright/browser setup, uv and ffmpeg/ffprobe, but not HyperFrames; still-image work does not load it. Upstream code/assets are not bundled, and their CC BY-NC 4.0 and other applicable licenses remain separate. Existing HyperFrames projects and `scripts/replay_wake.py` remain compatible.

Motion is opt-in; the default output remains a still image. The older `sb.py animate` route has its own [scene contracts](./references/motion-scenes.md) (Chinese). A scene name describes a composition contract, not proof of platform upload support. Brand animation is a separate demonstration.

For `sb.py animate`, install `requirements-motion.txt` (numpy; Pillow is already a baseline dependency), Node.js 22+, HyperFrames 0.8.140, ffmpeg/ffprobe and a local browser. `python3 scripts/sb.py animate doctor` checks them without downloading tools. HyperFrames is an external Apache 2.0 dependency and is not bundled. The original MIT `cutlib` color-key module does not call macOS Vision or provide an automatic Vision fallback. Select on the desktop website and copy the instruction into the Skill; platform upload compatibility remains unverified.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) (written in Chinese).

- Recommend a style: use the "推荐画风" (recommend a style) issue template.
- Report a problem: use the "报问题" (bug report) issue template.
- Code or documentation changes: open a pull request.

Website submission is not live yet and will arrive with the contribution feature; for now please use a GitHub issue or pull request.

## Credits and license

See [CHANGELOG.md](./CHANGELOG.md), [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md), and [MIT LICENSE](./LICENSE). Style and workflow ideas were adapted from [threerocks/hand-drawn-styles](https://github.com/threerocks/hand-drawn-styles), [JimLiu/baoyu-skills](https://github.com/JimLiu/baoyu-skills), and [yang0/handraw-style](https://github.com/yang0/handraw-style). Maintained by 叁笙 (sansheng).

Maintainers may set `SANSHENG_IMAGE_BRAND_DIR=<brand-image-directory>` when running `python3 scripts/sb.py build --gallery` to supply `logo.webp` and `logo-poster.webp`. Both must be decodable WebP files. Without this input, the builder only lists their filenames; website deployments supply brand and gallery images separately.
