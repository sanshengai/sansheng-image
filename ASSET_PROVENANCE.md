# Style reference provenance

Since 2026-09-30 (library v2) the bundled `styles/C*/anchor.png` files are **reference images taken from MIT-licensed upstream repositories** (`yang0/handraw-style`, `threerocks/hand-drawn-styles`), resized to at most 768 px. They are used only to convey technique, line, material and colour to the image model; the compiler forbids copying their subjects. Thirty-eight of the 57 styles carry such a reference; the rest are described by text only. The earlier self-generated anchors were retired (they remain in Git history).

Since 2026-10-02 the 34 styles that had no upstream sample use a **project-made style reference sheet** as their anchor (palette, materials, motifs, composition; drawn with no people or animals so that no subject leaks into new pictures), resized to 1024 px. These are recorded with origin `sanshengai/sansheng-image` under the project's MIT license. Every style also ships a display version of its style sheet as `samples/bd.webp`.

`styles/anchor-provenance.json` records, for each distributed reference, its SHA-256, upstream repository, item number and license. `python3 scripts/stylebook/anchor_provenance.py` checks the ledger against the files and the contracts, and rejects a reference that has no MIT upstream entry.

Style names in contracts (`inspiration.names`) credit the artists, studios and works that inspired a look. They describe where an image style comes from; they do not imply affiliation with or endorsement by those creators. The per-style same-topic samples in `styles/C*/samples/` (a person scene, an object scene and a small infographic, all generated on 2026-09-30 with the Codex built-in image tool, whose underlying model ID is not exposed) show one successful result each, not a guarantee for other subjects.

Image provenance does not replace content and visual review before reuse.

The README previews are unedited copies of project same-topic outputs: `assets/C01-story-sample.png` is C01-q1 (SHA-256 `acc2a9767da459bd743a59bb49833f043755b7eecf810e540a6792bf7b11aee9`), and `assets/C32-infographic-sample.png` is C32-q3 (SHA-256 `f41571c36d6a6a222c63464ea2ea6c3cff0b86e42f8635a7c7db4c43c1b1277b`). They show particular successful test images, not a guarantee for a different topic or a full series.

The MIT license in `LICENSE` covers the repository's original code, text, and bundled project images to the extent the contributor has rights to license them. Third-party code and text retain the notices in `THIRD_PARTY_NOTICES.md`.

New style studies for C93 and C98 are project-generated with the Codex built-in image tool. Their original bytes and source records are verified before ledger registration. The C93 board contains example subjects; the compiler isolates its technique from its layout, subjects and lettering. The C98 study supplies material qualities only; its written recipe controls ornamental order. Neither reference certifies full scene coverage.
