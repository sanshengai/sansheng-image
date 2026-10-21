# Sansheng Image · 叁笙生图

[中文](./README.md) | **English**

**An image Skill for Agents.** Give Claude Code, Codex, or another Agent your article or idea, choose a visual style, and create a series of images in that style.

<p align="center">
  <a href="https://sanshengai.top/tools/image/">
    <img src="./assets/readme/gallery-entry.webp" width="100%" alt="Open the visual gallery: browse 81 styles and 18 named palettes, with real pencil, screen-print, ink and woodblock examples">
  </a>
</p>

<h3 align="center"><a href="https://sanshengai.top/tools/image/">Open the visual gallery: choose a style and palette →</a></h3>

<p align="center">Browse examples by use → choose a style and palette → copy an instruction to your Agent</p>

<p align="center"><a href="#start-in-three-steps">Install and try</a> · <a href="#see-the-outputs">Still examples</a> · <a href="#let-a-paragraph-explain-itself-in-motion">Motion demo</a> · <a href="./CHANGELOG.md">Changelog</a></p>

> Public beta: 81 styles can be browsed and 18 named palettes selected. Eight styles, C91–C98, currently pass formal admission. Browsing, prompt compilation, and validation for an actual use are different states. See [capability boundaries](#current-capabilities-and-boundaries).

<!-- PROMO_VIDEO:START -->
<!-- Reserved for the promotional film: insert its heading, real poster and playback link between these markers when ready. Keep hidden until then. -->
<!-- PROMO_VIDEO:END -->

## Why use it

- **Content first**: read the source, identify scenes, steps, comparisons and relationships, then decide what to draw.
- **One workflow, many uses**: article and X covers/illustrations, Xiaohongshu cards, slides, infographics, comics, picture books and storyboard stills.
- **A defined style**: dedicated prompts and reference images guide the look; a series keeps its style while each composition follows its own content.
- **Palettes with character**: named palettes guide dominant colors, accents and negative space while respecting each style's recoloring rules.

## See the outputs

### Article illustrations and visual explanations

A character scene can carry a narrative; steps and relationships can explain a method. The content determines the structure.

<p>
  <a href="https://sanshengai.top/tools/image/#u=wxillus&s=C01"><img src="./assets/readme/article-story.webp" width="49%" alt="C01 warm hand-painted animation: a grandmother and child making dumplings"></a>
  <a href="https://sanshengai.top/tools/image/#u=info&s=C32"><img src="./assets/readme/article-explainer.webp" width="49%" alt="C32 aged-paper technical briefing: a Pomodoro technique infographic"></a>
</p>

<sub>Left: C01 Warm Hand-painted Animation. Right: C32 Aged-paper Technical Briefing. Both are actual generated single-image examples.</sub>

### Xiaohongshu: from cover to closing checklist

The same paper-sculpture treatment across a cover, an overview and a closing checklist, selected from a six-page afternoon-tea example.

[![C27 layered paper sculpture: three Xiaohongshu sample pages](./assets/readme/xiaohongshu-series.webp)](https://sanshengai.top/tools/image/#u=xhs&s=C27)

<sub>C27 Layered Paper Sculpture · three pages from the six-page afternoon-tea example. Click to browse the complete set.</sub>

### Slides: one style, different page structures

An agenda, an explanation and a data comparison organize different information while keeping the same visual style.

[![C42 airy flat vector: agenda, light-scattering explanation and data comparison slides](./assets/readme/ppt-pages.webp)](https://sanshengai.top/tools/image/#u=ppt&s=C42)

<sub>C42 Airy Flat Vector · selected image-based slides. Optional PPTX assembly does not make page elements editable; check Chinese rendering in the target presentation app.</sub>

The [visual gallery](https://sanshengai.top/tools/image/) also has comics, picture books, audio covers and storyboard examples. Storyboards produce static first frames, not complete videos.

## Let a paragraph explain itself in motion

For a process explanation, follow one message through receipt, a classification suggestion, program checks and a human handoff. Motion has a purpose beyond making objects enter the scene.

[![Motion explanation: a message travels through receipt, classification, checks and a human handoff](./assets/readme/article-motion.gif)](https://sanshengai.top/tools/image/#motion-showcase)

<sub>“How a message moves through the process” · the same GIF as the website. Click to compare the source and widescreen motion. The program checks shown validate format and allowed ranges, not classification correctness.</sub>

The gallery has four categories, **portraits and groups, visual explanations, landscape atmosphere, and albums**, with two works each. It pairs a 9:16 source with a 16:9 animation. Still output remains the default; motion is opt-in, and actual projects may retain an existing landscape source.

Use `python3 scripts/sb.py animate wake`: inherit the article, style and palette, assess whether the material suits motion, then prepare, direct, preview and review. The Agent sets up the environment using the [motion guide](./references/motion-wake.md); users keep one Skill entry. External lemo-wake code and related assets retain their own licenses, including **CC BY-NC 4.0**.

## Start in three steps

### 1. Install into your Agent

```bash
npx skills add sanshengai/sansheng-image -g
```

Requires Node.js, Python 3.10+, and an Agent that can run local commands. You can also give the repository URL to your Agent and ask it to install. [Manual installation and setup](./USAGE.md#安装与环境检查) (Chinese).

### 2. Choose your visuals in the gallery

Choose a use, style and palette, then copy one `sb2:` selection code to the Agent. Use it once or save a browser-local default.

### 3. Give it real source material

```text
Make one cover and three illustrations for this article.
Keep the style and palette I selected. Show me the illustration plan first,
then generate the images one by one.
```

Actual generation requires the host Agent's image tool or a configured image service. Codex's built-in tool uses its subscription allowance; external providers follow their own billing. Compiling a prompt alone does not produce an image.

<details>
<summary>Inspect a prompt and its dimensions before calling an image service</summary>

Run in the repository:

```bash
python3 scripts/sb.py compile examples/quickstart/manifest.json --json
```

This synthetic example prints the style, dimensions, full prompt and references. Generation and independent visual review are covered in the [usage guide](./USAGE.md#第一次编译与出图) (Chinese).

</details>

## Current capabilities and boundaries

| Capability | Current scope |
|---|---|
| Browse and compile styles | 81 public contracts; C91–C98 pass formal admission, the other 73 remain pending. A gallery choice is not validation for every use or palette. |
| Plan illustrations from an article | Source grounding, per-image structures, single/batch generation, local revisions and quality records. Check facts, text, cropping and series consistency in actual images. |
| Xiaohongshu, infographics, comics, books and audio covers | Creation workflows and samples are available. Check text, cropping and the complete series on the target platform before publishing. |
| Slides | Image pages and optional PPTX assembly; no editable page-element promise. |
| Storyboards | Static first frames and written motion notes. Check characters, light and continuity per project; no complete video generation. |
| Motion illustrations | Eight gallery works passed production review and browser playback. An older single GIF has WeChat phone feedback; three earlier films have X desktop-draft previews. Those results apply only to the files tested, not every current work or automatic-upload APIs. Ordinary Xiaohongshu GIF image posts remain unverified. |
| Preferences | Explicit local settings can be viewed, undone and forgotten. Long-term ranking effects still need continuous use; no cross-device sync. |

Factory defaults C24 (article illustrations/stickers), C31 (Xiaohongshu), C40 (infographics) and C30 (article covers) remain pending admission. Defaults, gallery availability and formal admission are recorded separately; explicit choices are supported.

The install package includes contracts, reference anchors and a few README previews, **not the complete website sample library**. See [image provenance](./ASSET_PROVENANCE.md) and [tested backend scope](./references/backends.md).

## Documentation

| Task | Guide |
|---|---|
| Install, check the environment, generate a first image | [Usage guide](./USAGE.md) (Chinese) |
| Lightweight image plans | [Quick workflow](./references/quick.md) |
| Plan an article's illustrations | [Planning](./references/planning.md) |
| Image services and independent visual review | [Backends](./references/backends.md) |
| Selection changes and reversible preferences | [Selection](./references/selection.md) · [Preferences](./references/preferences.md) |
| Comics, books and recurring characters | [Consistency](./references/consistency.md) |
| Static storyboard frames | [Storyboard guide](./references/scenes/storyboard.md) |
| Motion projects and reviewed counterexamples | [Motion workflow](./references/motion-wake.md) · [Examples](./examples/motion/README.md) |
| Gallery media and the film insertion point | [Maintainer guide](./MAINTAINERS.md) (Chinese) |

## Updates, contributions and licenses

[Changelog](./CHANGELOG.md) · [GitHub Releases](https://github.com/sanshengai/sansheng-image/releases) · [Contributing](./CONTRIBUTING.md)

Use the issue templates to recommend a style or report a problem, and pull requests for code or documentation. Website submission is not live yet.

Original code and project-owned content use the [MIT License](./LICENSE). Third-party code, fonts, reference images and optional motion dependencies retain their own licenses; see [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

Thanks to [threerocks/hand-drawn-styles](https://github.com/threerocks/hand-drawn-styles), [JimLiu/baoyu-skills](https://github.com/JimLiu/baoyu-skills), [yang0/handraw-style](https://github.com/yang0/handraw-style) and [Lemomo/lemo-wake](https://github.com/lemomo-ai/lemo-wake) for their methods and code. Style references imply no affiliation or endorsement. Maintained by 叁笙 (sansheng).
