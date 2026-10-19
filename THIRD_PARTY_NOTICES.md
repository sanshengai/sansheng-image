# Third-party notices

Some style recipes, format knowledge and workflow ideas in this project are adapted
(rewritten, not copied wholesale) from the following MIT-licensed projects. Each adapted
file names its source in its header. Their copyright and permission notices are reproduced
below as required by the MIT License.

## threerocks/hand-drawn-styles

https://github.com/threerocks/hand-drawn-styles — style recipes used as a starting point for
several styles (e.g. C01, C02, C03, C04, C05, C27, C36, C45, C54, C55).

```
MIT License

Copyright (c) 2026 liulei

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## JimLiu/baoyu-skills

https://github.com/JimLiu/baoyu-skills — style descriptions (e.g. C11, C24, C29, C31, C32, C34,
C35, C38, C39, C41, C46, C48, C50), format knowledge (content analysis, outline strategies,
infographic layouts, slide outlines, comic storyboards, cover and Xiaohongshu style systems) and image-backend design ideas. Styles C79–C89 and the scene playbook were also informed by public design practice; only structure and ideas were taken, no prompt text or sample images were copied.

```
MIT License

Copyright (c) 2026 Jim Liu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## yang0/handraw-style

https://github.com/yang0/handraw-style — style descriptions used as a starting point, rewritten in our own words
with creator and studio names removed (e.g. C08, C24, C25).

```
MIT License

Copyright (c) 2026 yang0

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Reference images and style credits (library v2, 2026-09-30)

Thirty-eight `styles/C*/anchor.png` files are downsized samples from `yang0/handraw-style` and `threerocks/hand-drawn-styles` (both MIT; notices reproduced above). `styles/anchor-provenance.json` lists the upstream repository and item number of each. They are given to the image model only as technique references.

Artist, studio and work names in the contracts (`inspiration.names`) and in the prompts (for example Hayao Miyazaki and Studio Ghibli, Quentin Blake, Jimmy Liao, Hergé, Makoto Shinkai, Aardman, Mary Blair, Osamu Tezuka) are credits for where a look comes from. They are descriptive references to publicly known styles; this project is not affiliated with, endorsed by, or sponsored by any of them, and no artwork by them is included.

## Motion edition: runtime dependencies (not bundled)

The compatible HyperFrames motion edition (`sb.py animate`, see `references/motion-scenes.md`) uses these tools at run time. None of them is bundled in this repository or downloaded automatically; users install them and `sb.py animate doctor` checks they are present. The opt-in `animate wake` workflow below has its own runtime setup and does not require HyperFrames.

- **HyperFrames** (Apache License 2.0) — version 0.8.140 is the supported version. It is invoked as an external command-line program that renders a local HTML project. No HyperFrames source or binary is copied into this repository.
- **Node.js 22+**, **ffmpeg** and **ffprobe**, and a local render browser (Chrome or chrome-headless-shell) — separate programs under their own licenses, installed by the user.
- **GSAP** — optional local HTML animation dependency; not bundled. The local 3.14.2 distribution carries the GreenSock Standard License notice (`https://gsap.com/standard-license`); obtain and use your own copy under its license.
- **NumPy** (`requirements-motion.txt`) and **Pillow** — Python packages installed by the user; not bundled.

## Motion edition: bundled colour-key module

`scripts/stylebook/vendor/cutlib.py` is an original MIT-licensed module of this project (the chroma-key matte and edge statistics used by `sb.py layers`). It contains no third-party code, and `vendor/SOURCE.json` records its SHA-256, which the loader checks before use. The macOS-only binary `vision` helper that other tooling around it may have used is not bundled and is never called here; there is no automatic Vision fallback, and layer matting uses only this colour-key code.

## Ideas without copied text

Design ideas (no text or code copied) were also learned from: yanliudesign/mono-color-skill
(recipe manifest, stable seeds, measurable regeneration criteria), taxueseek/taxue-halftone
(required axes, ablation testing), jiemianduan/poster-style-transfer (style spec extraction,
weighted fidelity rubric), onebluecloud/book-to-comic (inbox fallback, series inheritance),
black-forest-labs/skills (negative-to-positive prompt rewriting).


## Lemomo / lemo-wake: optional external workflow (not bundled)

https://github.com/lemomo-ai/lemo-wake — author: Lemomo. The optional workflow in
`references/motion-wake.md` was validated against upstream commit
`f17890701dd3c782fe0e0a992fb5c2c12063194d`. Its Skill/code/docs are marked
CC BY-NC 4.0; consult the actual upstream files and asset licenses before use or
redistribution. Our MIT license does not relicense that external material.

`scripts/replay_wake.py`, `scripts/stylebook/wake_runtime.py`,
`scripts/stylebook/wake_workflow.py` and the protocol inspector are our own
invocation/orchestration code. `animate wake setup --install` optionally obtains
the fixed upstream source into a separate runtime directory, retains its original
LICENSE and verifies the selected source hashes in `references/wake-upstream.json`.
It is not registered as a second Skill. Node dependencies are installed there;
model downloads are separate, task-dependent upstream operations. No upstream
choreography, scripts, models, fonts or demonstration projects are copied into
this package. Generated projects record the upstream author, revision and license
in `UPSTREAM.md`; their copied templates retain the upstream terms. Local research
adaptations and their artwork are not included in the public package. Public
showcase films require their own source and license review; external execution
does not remove the noncommercial restriction or authorize redistribution.
