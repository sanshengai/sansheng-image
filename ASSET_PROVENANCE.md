# Style reference provenance

Since 2026-09-30 (library v2) the bundled `styles/C*/anchor.png` files are **reference images taken from MIT-licensed upstream repositories** (`yang0/handraw-style`, `threerocks/hand-drawn-styles`), resized to at most 768 px. They are used only to convey technique, line, material and colour to the image model; the compiler forbids copying their subjects. Thirty-eight of the 57 styles carry such a reference; the rest are described by text only. The earlier self-generated anchors were retired (they remain in Git history).

Since 2026-10-02 the 34 styles that had no upstream sample use a **project-made style reference sheet** as their anchor (palette, materials, motifs, composition; drawn with no people or animals so that no subject leaks into new pictures), resized to 1024 px. These are recorded with origin `sanshengai/sansheng-image` under the project's MIT license. Every style also ships a display version of its style sheet as `samples/bd.webp`.

`styles/anchor-provenance.json` records, for each distributed reference, its SHA-256, upstream repository, item number and license. `python3 scripts/stylebook/anchor_provenance.py` checks the ledger against the files and the contracts, and rejects a reference that has no MIT upstream entry.

Style names in contracts (`inspiration.names`) credit the artists, studios and works that inspired a look. They describe where an image style comes from; they do not imply affiliation with or endorsement by those creators. The per-style same-topic samples in `styles/C*/samples/` (a person scene, an object scene and a small infographic, all generated on 2026-09-30 with the Codex built-in image tool, whose underlying model ID is not exposed) show one successful result each, not a guarantee for other subjects.

Image provenance does not replace content and visual review before reuse.

The three `examples/motion/` research candidates retain AI-generated experiment images, verbatim animation HTML returned by `claude-opus-5-5`, director notes and rendered key states. `examples/motion/manifest.json` binds the actual files and common output specification. Full original image-generation receipts are not included in this bundle; the examples do not certify user adoption, platform playback or a general quality improvement. The separately licensed font retains its OFL notice. No upstream lemo-wake source code is bundled.

The original README preview files remain unedited copies of project same-topic outputs: `assets/C01-story-sample.png` is C01-q1 (SHA-256 `acc2a9767da459bd743a59bb49833f043755b7eecf810e540a6792bf7b11aee9`), and `assets/C32-infographic-sample.png` is C32-q3 (SHA-256 `f41571c36d6a6a222c63464ea2ea6c3cff0b86e42f8635a7c7db4c43c1b1277b`). They show particular successful test images, not a guarantee for a different topic or a full series.

## Selected README display media

`assets/readme/provenance.json` records the source and output SHA-256, dimensions, byte sizes and display processing for six selected media files. These are a small display exception to the complete website sample library being excluded from the repository and install package. Compositions use the actual output images intact; they only arrange, resize and encode them, without retouching or altering their lettering. Source file records for website-only samples describe their origin, not a promise that the entire sample library is bundled.

| Display file | Actual project outputs shown | Scope |
|---|---|---|
| `gallery-entry.webp` | C30, C74 and C19 cover samples; C91 `cv-wave22.png` | A designed link card to the live gallery, not a screenshot or a new generated image. C91 is admitted; availability of the other styles does not imply admission. |
| `article-story.webp` | Resized C01-q1 | Single successful narrative image. |
| `article-explainer.webp` | Resized C32-q3 | Single successful explanation image. |
| `xiaohongshu-series.webp` | C27 afternoon-tea pages 1, 2 and 6 | Selected cover, overview and closing checklist; not a new consumer-validation result. |
| `ppt-pages.webp` | C42 slide pages 2, 3 and 5 | Selected image-based agenda, explanation and data-comparison pages; no editable-element claim. |
| `article-motion.webp` | Resized website `article-message/poster.png`; full GIF linked on the website | The project's AI-generated clay-style message-journey film. Production/browser reviewed; no individual platform phone-preview result for this exact file. |

The displayed motion film retains the font notice above. Project-authored media do not relicense external lemo-wake tools or assets: the optional upstream workflow keeps its separate CC BY-NC 4.0 and applicable asset licenses, as described in `THIRD_PARTY_NOTICES.md`.

The MIT license in `LICENSE` covers the repository's original code, text, and bundled project images to the extent the contributor has rights to license them. Third-party code and text retain the notices in `THIRD_PARTY_NOTICES.md`.

New style studies for C93 and C98 are project-generated with the Codex built-in image tool. Their original bytes and source records are verified before ledger registration. The C93 board contains example subjects; the compiler isolates its technique from its layout, subjects and lettering. The C98 study supplies material qualities only; its written recipe controls ornamental order. Neither reference certifies full scene coverage.
