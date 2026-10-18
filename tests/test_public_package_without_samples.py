"""公开下载包不带样图：样图只在官网选择器里展示，出图只用合同与锚点。
无样图的包必须能重建出与完整仓库字节相同的选择器页面与图片清单（官网投影检查据此运作）。"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import build as BD  # noqa: E402
import public_export as PE  # noqa: E402


def test_export_selection_drops_samples_but_keeps_anchors_and_contracts():
    entries = [("100644", "styles/C30/contract.json"), ("100644", "styles/C30/anchor.png"),
               ("100644", "styles/C30/samples/cv.webp"), ("100644", "styles/C30/samples/ppt-12.webp"),
               ("100644", "SKILL.md"), ("100644", "README.md"), ("100644", "LICENSE"), ("100644", "requirements.txt"),
               ("100644", "styles/anchor-provenance.json"), ("100644", "registry.json")]
    entries += [("100644", f"styles/C{i}/contract.json") for i in range(1, 1)]
    cat = json.loads((ROOT / "styles/catalog.json").read_text(encoding="utf-8"))["styles"]
    entries += [("100644", f"styles/{s['code']}/contract.json") for s in cat if s["code"] != "C30"]
    chosen = PE.select(entries)
    assert "styles/C30/contract.json" in chosen and "styles/C30/anchor.png" in chosen
    assert not [n for n in chosen if "/samples/" in n]


def test_every_registered_sample_has_a_matching_ratio():
    """合同登记的宽高比必须与仓库里的实际样图一致：无样图重建时页面比例靠它。"""
    from PIL import Image
    bad = []
    for cj in sorted((ROOT / "styles").glob("C*/contract.json")):
        for s in json.loads(cj.read_text(encoding="utf-8")).get("samples", []):
            if "ratio" not in s:
                bad.append((cj.parent.name, s["file"], "缺 ratio"))
                continue
            f = cj.parent / s["file"]
            if f.is_file():
                with Image.open(f) as im:
                    if abs(round(im.width / im.height, 3) - s["ratio"]) > 0.002:
                        bad.append((cj.parent.name, s["file"], "比例不符"))
    assert bad == [], bad[:5]


def _bare_ignore(root):
    generic = shutil.ignore_patterns(".git", "__pycache__", "logs", "samples", ".pytest_cache")

    def ignore(directory, names):
        dropped = set(generic(directory, names))
        if Path(directory) == root / "gallery" and "build" in names:
            dropped.add("build")
        return dropped

    return ignore


def test_bare_copy_excludes_existing_build_media(tmp_path):
    root = tmp_path / "source"
    (root / "gallery" / "build" / "motion").mkdir(parents=True)
    (root / "gallery" / "build" / "motion" / "share.mp4").write_bytes(b"old-build")
    (root / "gallery" / "motion.js").write_text("// source")
    (root / "styles" / "C01" / "samples").mkdir(parents=True)
    (root / "styles" / "C01" / "samples" / "s1.png").write_bytes(b"sample")
    (root / "styles" / "C01" / "anchor.png").write_bytes(b"anchor")
    bare = tmp_path / "bare"
    shutil.copytree(root, bare, ignore=_bare_ignore(root))
    assert not (bare / "gallery" / "build").exists()
    assert not (bare / "styles" / "C01" / "samples").exists()
    assert (bare / "gallery" / "motion.js").read_text() == "// source"
    assert (bare / "styles" / "C01" / "anchor.png").read_bytes() == b"anchor"


@pytest.fixture(scope="module")
def full_and_bare(tmp_path_factory):
    full = tmp_path_factory.mktemp("full")
    # 完整构建提供真实品牌图片；公开包只登记名称，两边清单仍必须逐字节一致。
    from PIL import Image
    brand = tmp_path_factory.mktemp("brand")
    for name in BD.BRAND_IMAGES:
        Image.new("RGB", (64, 64), "#20402c").save(brand / name, format="WEBP")
    BD.picker(full, brand_dir=brand)
    bare_root = tmp_path_factory.mktemp("bare") / "sb"
    shutil.copytree(ROOT, bare_root, ignore=_bare_ignore(ROOT))
    assert not list(bare_root.glob("styles/C*/samples"))
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "SANSHENG_IMAGE_PROFILE": str(bare_root / "none"), "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "scripts/sb.py", "build", "--gallery"], cwd=bare_root, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]
    return full, bare_root / "gallery/build"


def test_bare_package_rebuilds_identical_page_and_image_manifest(full_and_bare):
    full, bare = full_and_bare
    assert (bare / "index.html").read_bytes() == (full / "index.html").read_bytes()
    assert (bare / "img-manifest.json").read_bytes() == (full / "img-manifest.json").read_bytes()
    full_names = {p.name for p in (full / "img").glob("*.webp")}
    bare_names = {p.name for p in (bare / "img").glob("*.webp")}
    assert bare_names < full_names, "无样图包只应有锚点缩略图"
    assert set(json.loads((full / "img-manifest.json").read_text())["images"]) == full_names


MOTION_SOURCES = ("gallery/motion.js", "gallery/motion.css", "gallery/motion-data.json")


def _required_entries():
    entries = [("100644", n) for n in ("SKILL.md", "README.md", "LICENSE", "requirements.txt",
                                       "styles/anchor-provenance.json", "registry.json")]
    cat = json.loads((ROOT / "styles/catalog.json").read_text(encoding="utf-8"))["styles"]
    return entries + [("100644", f"styles/{s['code']}/contract.json") for s in cat]


def test_export_whitelist_has_motion_sources_but_never_media():
    media = ["gallery/motion/brand/poster.png", "gallery/motion/brand/share.mp4", "gallery/motion/x/share.mp4",
             "gallery/motion/x/source.png", "gallery/motion/x/share.gif",
             "motion/brand/poster.png", "motion/x/share.gif", "gallery/motion-media/a.mp4"]
    chosen = PE.select(_required_entries() + [("100644", n) for n in (*MOTION_SOURCES, *media)])
    for name in MOTION_SOURCES:
        assert name in chosen and name in PE.PUBLIC_FILES
    assert not [n for n in chosen if n in media or n.endswith(".mp4")]
    # 没有登记的动态源码不会被带走，登记的源码若不是普通文件则拒绝
    assert "gallery/motion.js" not in PE.select(_required_entries())
    with pytest.raises(PE.ExportError):
        PE.select(_required_entries() + [("120000", "gallery/motion.js")])


def test_motion_sources_pass_the_public_text_scan():
    for name in MOTION_SOURCES:
        PE.scan_text(name, (ROOT / name).read_bytes())
    with pytest.raises(PE.ExportError):          # 反例：同一份扫描确实会拒绝带个人路径的源码
        PE.scan_text("gallery/motion.js", b"var p = '" + b"/Us" + b"ers/someone/x';")


def test_bare_package_rebuilds_identical_motion_resources(full_and_bare):
    full, bare = full_and_bare
    for name in ("motion.js", "motion.css", "motion-data.json", "motion-manifest.json", "index.html"):
        assert (bare / name).read_bytes() == (full / name).read_bytes(), name
    for name in ("motion.js", "motion.css", "motion-data.json"):
        assert (bare / name).read_bytes() == (ROOT / "gallery" / name).read_bytes(), name
    manifest = json.loads((bare / "motion-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["sources"]) == len(MOTION_SOURCES)
    registered = json.loads((ROOT / "gallery/motion-data.json").read_text(encoding="utf-8"))
    expected_media = {
        (item[field].removeprefix("/tools/image/"), item[f"{field}_sha256"])
        for item in registered["items"]
        for field in ("poster", "video", "source", "gif")
        if field in item
    }
    actual_media = [(m["path"], m["sha256"]) for m in manifest["media"]]
    assert len(actual_media) == len(expected_media), "媒体登记不能遗漏或重复"
    assert set(actual_media) == expected_media, "路径与摘要须逐项对应真实登记"
    assert not (bare / "motion").exists(), "无媒体包只登记清单，不造媒体"
    assert not list(bare.rglob("*.mp4"))
    html = (bare / "index.html").read_text(encoding="utf-8")
    assert 'href="/tools/image/motion.css"' in html and 'src="/tools/image/motion.js"' in html


def test_registered_sample_without_ratio_or_file_is_rejected(tmp_path, monkeypatch):
    """反例：样图既没有文件、合同里也没登记宽高比，构建必须报错而不是悄悄少一张。"""
    sample = {"topic": "x", "file": "samples/cv.webp"}
    monkeypatch.setattr(BD.CT, "load", lambda code: {"samples": [sample]})
    cp = tmp_path / "C99" / "contract.json"
    cp.parent.mkdir()
    files = BD._sample_files("C99", cp)
    assert files["cv"][1] is None and not files["cv"][0].is_file()
