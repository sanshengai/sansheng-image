"""官网「让图动起来」：清单严格校验、媒体来源核对、先验后写、只管自己的输出。
全部走正式模块 stylebook.motion_gallery 与 stylebook.build.picker；夹具只放在临时目录里。"""
import hashlib
import inspect
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import build as BD  # noqa: E402
from stylebook import motion_gallery as MG  # noqa: E402

SCENES = [{"id": "wxillus", "pool": ["C24", "C30"]}, {"id": "ppt", "pool": ["C79"]}]


# ---------------- 夹具 ----------------

def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def payload(iid: str, kind: str) -> bytes:
    return f"{kind}:{iid}".encode()


def make_item(iid: str, **over) -> dict:
    item = {"id": iid, "title": f"示例 {iid}", "style_code": None, "scene": "brand", "homepage_scene": None,
            "scene_label": "品牌示范", "width": 1600, "height": 900, "duration": 6.0,
            "poster": f"/tools/image/motion/{iid}/poster.png", "video": f"/tools/image/motion/{iid}/share.mp4",
            "poster_sha256": sha(payload(iid, "poster")), "video_sha256": sha(payload(iid, "video")),
            "note": "说明"}
    item.update(over)
    return item


def make_doc(*ids: str) -> dict:
    return {"schema_version": 1, "items": [make_item(i) for i in ids]}


def make_gallery(tmp_path: Path, doc, name: str = "gallery") -> Path:
    g = tmp_path / name
    g.mkdir()
    (g / "motion.js").write_text("// js\n", encoding="utf-8")
    (g / "motion.css").write_text("/* css */\n", encoding="utf-8")
    text = doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    (g / "motion-data.json").write_text(text, encoding="utf-8")
    return g


def make_media(tmp_path: Path, doc: dict, name: str = "media") -> Path:
    root = tmp_path / name
    for it in doc["items"]:
        d = root / it["id"]
        d.mkdir(parents=True)
        (d / "poster.png").write_bytes(payload(it["id"], "poster"))
        (d / "share.mp4").write_bytes(payload(it["id"], "video"))
    return root


@pytest.fixture(autouse=True)
def _no_motion_env(monkeypatch):
    monkeypatch.delenv(MG.MOTION_DIR_ENV, raising=False)


# ---------------- 正式数据与源码 ----------------

def test_real_source_data_passes_strict_schema():
    doc = MG.parse_text((ROOT / "gallery" / "motion-data.json").read_bytes())
    assert 0 < len(doc["items"]) <= MG.MAX_ITEMS
    assert len(MG.expected_media(doc)) == sum(len(MG.item_media(it)) for it in doc["items"])
    featured = [it for it in doc["items"] if it.get("placement", "featured") == "featured"]
    assert all(it["width"] * 9 == it["height"] * 16 for it in featured)
    # 用户否定的样板与尚未采用的候选不进入正式精选，背景轮播仍保留。
    by_id = {it["id"]: it for it in doc["items"]}
    assert all(by_id[i]["placement"] == "background" for i in ("paper-city", "travel-journal", "verify-source"))
    for it in doc["items"]:
        if it["homepage_scene"] is not None:
            assert it["style_code"], it["id"]


def test_real_sources_have_no_candidate_leftovers_and_opaque_buttons():
    js = (ROOT / "gallery" / "motion.js").read_text(encoding="utf-8")
    css = (ROOT / "gallery" / "motion.css").read_text(encoding="utf-8")
    html = BD.PICKER.read_text(encoding="utf-8")
    assert "var DATA_URL = '/tools/image/motion-data.json';" in js
    assert "var MEDIA_PREFIX = '/tools/image/motion/';" in js
    for text in (js, css, html):
        assert "motion-candidate" not in text and "motion-media" not in text
        assert ("_work" + "space") not in text
    assert "rgba(5, 8, 13, .86)" not in css      # 标签与按钮底色不透明，背景浮图不会透过来
    assert html.count('href="/tools/image/motion.css"') == 1
    assert html.count('src="/tools/image/motion.js"') == 1


def test_picker_signature_adds_optional_motion_dir_last():
    params = inspect.signature(BD.picker).parameters
    assert list(params)[:3] == ["out_dir", "private", "brand_dir"]
    assert list(params)[3] == "motion_dir" and params["motion_dir"].default is None


# ---------------- 清单校验：空输入、坏类型、重复键 ----------------

@pytest.mark.parametrize("text", [
    "", "   ", "[]", "null", "{}", '{"schema_version": 1}', '{"schema_version": 1, "items": []}',
    '{"schema_version": 1, "items": {}}', '{"schema_version": 2, "items": []}'])
def test_empty_or_wrong_shape_is_rejected(text):
    with pytest.raises(ValueError):
        MG.parse_text(text)


@pytest.mark.parametrize("value", [5, None, ["x"], b"\xff\xfe"])
def test_non_text_input_is_rejected(value):
    with pytest.raises(ValueError):
        MG.parse_text(value)


def test_validate_document_never_raises_on_garbage():
    for junk in (None, 5, "x", [], [1], {"items": 5}, {"schema_version": True, "items": [1, None, "x", []]}):
        assert MG.validate_document(junk)


def test_good_documents_pass_including_the_limit():
    assert MG.validate_document(make_doc("a")) == []
    assert MG.validate_document(make_doc(*[f"i{n}" for n in range(MG.MAX_ITEMS)])) == []
    assert MG.parse_text(json.dumps(make_doc("a"))) == make_doc("a")


def test_more_than_twelve_items_rejected():
    assert MG.validate_document(make_doc(*[f"i{n}" for n in range(MG.MAX_ITEMS + 1)]))


def test_schema_version_must_be_integer_one():
    for bad in (True, 1.0, "1", 0, 2, None):
        doc = make_doc("a")
        doc["schema_version"] = bad
        assert MG.validate_document(doc), bad


def test_unknown_missing_and_duplicate_fields():
    doc = make_doc("a")
    doc["extra"] = 1
    assert MG.validate_document(doc)
    doc = make_doc("a")
    doc["items"][0]["home_scene"] = "ppt"        # 只认现有数据键 homepage_scene
    assert MG.validate_document(doc)
    for key in sorted(MG.REQUIRED_KEYS):
        doc = make_doc("a")
        doc["items"][0].pop(key, None)
        assert MG.validate_document(doc), key
    doc = make_doc("a", "a")
    assert any("重复" in p for p in MG.validate_document(doc))


def test_optional_fields_may_be_absent():
    doc = make_doc("a")
    for key in MG.OPTIONAL_KEYS:
        doc["items"][0].pop(key, None)
    assert MG.validate_document(doc) == []


def test_duplicate_json_keys_and_non_finite_numbers_rejected():
    good = json.dumps(make_doc("a"))
    with pytest.raises(ValueError, match="重复键"):
        MG.parse_text(good.replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1))
    with pytest.raises(ValueError, match="重复键"):
        MG.parse_text(good.replace('"id": "a",', '"id": "a", "id": "b",', 1))
    with pytest.raises(ValueError):
        MG.parse_text(good.replace('"duration": 6.0', '"duration": NaN'))
    with pytest.raises(ValueError):
        MG.parse_text(good.replace('"duration": 6.0', '"duration": Infinity'))


@pytest.mark.parametrize("field,value", [
    ("id", 5), ("id", ""), ("id", "a/b"), ("id", "x" * 33), ("id", "a\n"),
    ("title", ["x"]), ("title", ""), ("title", " "), ("title", "x" * 61),
    ("scene", None), ("scene_label", "x" * 21),
    ("width", True), ("width", 1920.0), ("width", "1920"), ("width", 0), ("width", -5), ("width", 8001),
    ("height", True), ("height", None),
    ("duration", True), ("duration", 0), ("duration", -1), ("duration", 61), ("duration", "6"),
    ("duration", float("nan")), ("duration", float("inf")),
    ("note", ""), ("note", "   "), ("note", 5), ("note", "x" * 241),
    ("style_code", "c1"), ("style_code", 5), ("style_code", "C1234"),
    ("homepage_scene", "Wxi"), ("homepage_scene", 3),
    ("poster_sha256", "A" * 64), ("poster_sha256", "a" * 63), ("poster_sha256", None), ("video_sha256", "g" * 64),
])
def test_bad_field_types_and_ranges_are_rejected(field, value):
    doc = make_doc("a")
    doc["items"][0][field] = value
    assert MG.validate_document(doc)


@pytest.mark.parametrize("field,value", [
    ("poster", "https://evil.example/tools/image/motion/a/poster.png"),
    ("poster", "//evil.example/tools/image/motion/a/poster.png"),
    ("poster", "/tools/image/motion/../other/poster.png"),
    ("poster", "/tools/image/motion/a/../a/poster.png"),
    ("poster", "/tools/image/motion/%2e%2e/a/poster.png"),
    ("poster", "/tools/image/motion/a/poster.png?x=1"),
    ("poster", "/tools/image/motion/a/poster.png#f"),
    ("poster", "/tools/image/motion\\a/poster.png"),
    ("poster", "/tools/image/motion/a/poster.jpg"),
    ("poster", "/tools/image/motion/b/poster.png"),
    ("poster", "/tools/image/other/a/poster.png"),
    ("poster", "tools/image/motion/a/poster.png"),
    ("poster", "/tools/image/motion/a/share.mp4"),
    ("poster", None),
    ("video", "http://evil.example/tools/image/motion/a/share.mp4"),
    ("video", "/tools/image/motion/a/share.webm"),
    ("video", "/tools/image/motion/a/poster.png"),
    ("video", "/tools/image/motion/a/share.mp4?t=1"),
    ("video", "/tools/image/motion/../a/share.mp4"),
    ("video", 5),
])
def test_media_urls_must_be_exactly_the_same_origin_motion_paths(field, value):
    doc = make_doc("a")
    doc["items"][0][field] = value
    assert MG.validate_document(doc)


# ---------------- 与页面场景的对应 ----------------

def test_scene_link_needs_real_scene_and_static_pool_membership():
    doc = make_doc("a")
    doc["items"][0].update(style_code="C24", homepage_scene="wxillus")
    MG.check_scenes(doc, SCENES)
    for style, scene in (("C24", "no-such"), ("C99", "wxillus"), (None, "wxillus"), ("C79", "wxillus")):
        bad = make_doc("a")
        bad["items"][0].update(style_code=style, homepage_scene=scene)
        with pytest.raises(ValueError):
            MG.check_scenes(bad, SCENES)
    with pytest.raises(ValueError):
        MG.check_scenes(doc, [])


def test_demo_without_home_scene_stays_a_demo():
    doc = make_doc("a")
    doc["items"][0].update(style_code="C30", homepage_scene=None, scene="article", scene_label="画风动态示范")
    MG.check_scenes(doc, [])                    # 没有场景也不报错：不给任何场景增加静态筛选
    MG.check_scenes(doc, SCENES)


# ---------------- 媒体来源核对 ----------------

def test_without_motion_dir_only_registers_expected_manifest(tmp_path):
    doc = make_doc("a", "b")
    plan = MG.prepare(None, None, make_gallery(tmp_path, doc))
    assert plan.media is None
    assert [s["path"] for s in plan.manifest["sources"]] == list(MG.SOURCE_FILES)
    assert plan.manifest["media"] == [{"path": rel, "sha256": s} for rel, s in MG.expected_media(doc)]
    assert [m["path"] for m in plan.manifest["media"]] == [
        "motion/a/poster.png", "motion/a/share.mp4", "motion/b/poster.png", "motion/b/share.mp4"]


def test_motion_dir_from_env_and_manifest_does_not_depend_on_media(tmp_path, monkeypatch):
    doc = make_doc("a", "b")
    gallery, media = make_gallery(tmp_path, doc), make_media(tmp_path, doc)
    monkeypatch.setenv(MG.MOTION_DIR_ENV, str(media))
    with_media = MG.prepare(None, None, gallery)
    assert sorted(with_media.media) == [m["path"] for m in with_media.manifest["media"]]
    monkeypatch.delenv(MG.MOTION_DIR_ENV)
    assert MG.prepare(None, None, gallery).manifest == with_media.manifest


def test_missing_media_directory_or_files_rejected(tmp_path):
    doc = make_doc("a", "b")
    gallery = make_gallery(tmp_path, doc)
    with pytest.raises(ValueError, match="不存在"):
        MG.prepare(tmp_path / "nope", None, gallery)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError) as e:
        MG.prepare(empty, None, gallery)
    assert str(e.value).count("缺少媒体") == 4         # 一次列全，不是遇到第一个就停
    media = make_media(tmp_path, doc)
    (media / "b" / "share.mp4").unlink()
    with pytest.raises(ValueError, match="缺少媒体 motion/b/share.mp4"):
        MG.prepare(media, None, gallery)


def test_sha_tamper_is_rejected_for_poster_and_video(tmp_path):
    doc = make_doc("a", "b")
    gallery, media = make_gallery(tmp_path, doc), make_media(tmp_path, doc)
    for rel in ("a/poster.png", "b/share.mp4"):
        path = media / rel
        original = path.read_bytes()
        path.write_bytes(original + b"x")
        with pytest.raises(ValueError, match="SHA-256"):
            MG.prepare(media, None, gallery)
        path.write_bytes(original)
    assert MG.prepare(media, None, gallery).media            # 还原后恢复通过，证明上面确是篡改导致


def test_symlinked_file_and_directory_and_non_regular_are_rejected(tmp_path):
    doc = make_doc("a", "b")
    gallery, media = make_gallery(tmp_path, doc), make_media(tmp_path, doc)
    real = tmp_path / "real.mp4"
    real.write_bytes(payload("a", "video"))                 # 内容与 SHA 都对，仍因是符号链接被拒
    target = media / "a" / "share.mp4"
    target.unlink()
    target.symlink_to(real)
    with pytest.raises(ValueError, match="符号链接"):
        MG.prepare(media, None, gallery)
    target.unlink()
    target.write_bytes(payload("a", "video"))

    moved = tmp_path / "b-real"
    (media / "b").rename(moved)
    (media / "b").symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        MG.prepare(media, None, gallery)
    (media / "b").unlink()
    moved.rename(media / "b")

    poster = media / "a" / "poster.png"
    poster.unlink()
    poster.mkdir()
    with pytest.raises(ValueError, match="不是普通文件"):
        MG.prepare(media, None, gallery)


def test_bad_source_files_in_gallery_rejected(tmp_path):
    doc = make_doc("a")
    gallery = make_gallery(tmp_path, doc)
    (gallery / "motion.css").unlink()
    with pytest.raises(ValueError, match="motion.css"):
        MG.prepare(None, None, gallery)
    dup = '{"schema_version": 1, "schema_version": 1, "items": []}'
    with pytest.raises(ValueError, match="重复键"):
        MG.prepare(None, None, make_gallery(tmp_path, dup, "gallery2"))


# ---------------- 先验后写、只管自己的输出 ----------------

def test_verification_failure_writes_nothing(tmp_path):
    doc = make_doc("a")
    gallery, media = make_gallery(tmp_path, doc), make_media(tmp_path, doc)
    (media / "a" / "poster.png").write_bytes(b"tampered")
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        MG.prepare(media, out, gallery)
    assert not out.exists()


def test_write_copies_verified_media_and_leaves_other_outputs_alone(tmp_path):
    doc = make_doc("a", "b")
    gallery, media = make_gallery(tmp_path, doc), make_media(tmp_path, doc)
    out = tmp_path / "out"
    (out / "img").mkdir(parents=True)
    (out / "img" / "C01-s1.webp").write_bytes(b"img")
    (out / "index.html").write_text("page", encoding="utf-8")
    (out / "motion" / "stale").mkdir(parents=True)
    (out / "motion" / "stale" / "old.mp4").write_bytes(b"old")
    plan = MG.prepare(media, out, gallery)
    MG.write(plan, out)
    assert (out / "img" / "C01-s1.webp").read_bytes() == b"img" and (out / "index.html").read_text() == "page"
    assert not (out / "motion" / "stale").exists()
    manifest = json.loads((out / MG.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert len(manifest["sources"]) == 3 and len(manifest["media"]) == 4
    for entry in manifest["sources"] + manifest["media"]:
        assert not Path(entry["path"]).is_absolute() and ".." not in Path(entry["path"]).parts
        assert sha((out / entry["path"]).read_bytes()) == entry["sha256"]
    on_disk = sorted(p.relative_to(out).as_posix() for p in (out / "motion").rglob("*") if p.is_file())
    assert on_disk == [m["path"] for m in manifest["media"]]
    for name in MG.SOURCE_FILES:
        assert (out / name).read_bytes() == (gallery / name).read_bytes()

    # 再用「没给媒体目录」的计划写一遍：清单与源码照常，已有的 motion/ 不被动
    MG.write(MG.prepare(None, out, gallery), out)
    assert sorted(p.relative_to(out).as_posix() for p in (out / "motion").rglob("*") if p.is_file()) == on_disk


def test_output_location_traps_are_refused_before_any_write(tmp_path):
    doc = make_doc("a")
    gallery = make_gallery(tmp_path, doc)
    out = tmp_path / "out"
    out.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (out / "motion").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError):
        MG.prepare(None, out, gallery)
    (out / "motion").unlink()
    (out / "motion.js").mkdir()
    with pytest.raises(ValueError):
        MG.prepare(None, out, gallery)
    assert list(elsewhere.iterdir()) == []


# ---------------- 经 picker 的真实消费者路径 ----------------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("motion-built")
    doc = make_doc("alpha", "beta")
    gallery, media = make_gallery(tmp, doc), make_media(tmp, doc)
    out = tmp / "out"
    (out / "motion" / "stale").mkdir(parents=True)
    (out / "motion" / "stale" / "old.mp4").write_bytes(b"old")
    (out / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(MG, "GALLERY_DIR", gallery)
        mp.delenv(MG.MOTION_DIR_ENV, raising=False)
        BD.picker(out, motion_dir=media)
    return out, gallery, media, doc


def test_picker_writes_motion_resources_and_keeps_unrelated_files(built):
    out, gallery, _media, doc = built
    assert (out / "keep.txt").read_text() == "keep"
    assert not (out / "motion" / "stale").exists()
    manifest = json.loads((out / MG.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert [m["path"] for m in manifest["media"]] == [rel for rel, _ in MG.expected_media(doc)]
    for name in MG.SOURCE_FILES:
        assert (out / name).read_bytes() == (gallery / name).read_bytes()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="/tools/image/motion.css"' in html and 'src="/tools/image/motion.js"' in html
    for rel, digest in MG.expected_media(doc):
        assert sha((out / rel).read_bytes()) == digest


def test_picker_with_tampered_media_fails_before_creating_any_output(tmp_path, monkeypatch):
    doc = make_doc("a")
    gallery, media = make_gallery(tmp_path, doc), make_media(tmp_path, doc)
    (media / "a" / "share.mp4").write_bytes(b"tampered")
    out = tmp_path / "out"
    monkeypatch.setattr(MG, "GALLERY_DIR", gallery)
    with pytest.raises(ValueError, match="SHA-256"):
        BD.picker(out, motion_dir=media)
    assert not out.exists()


def test_picker_with_unknown_home_scene_writes_no_outputs(tmp_path, monkeypatch):
    doc = make_doc("a")
    doc["items"][0].update(style_code="C24", scene="article", homepage_scene="no-such-scene")
    gallery = make_gallery(tmp_path, doc)
    out = tmp_path / "out"
    monkeypatch.setattr(MG, "GALLERY_DIR", gallery)
    with pytest.raises(ValueError, match="页面场景"):
        BD.picker(out)
    for name in (*MG.SOURCE_FILES, MG.MANIFEST_NAME, "index.html"):
        assert not (out / name).exists(), name
    assert not (out / "motion").exists()
    assert not out.exists()


def test_symlinked_media_root_is_rejected(tmp_path):
    doc = make_doc("a")
    media = make_media(tmp_path, doc)
    link = tmp_path / "media-link"
    link.symlink_to(media, target_is_directory=True)
    with pytest.raises(ValueError):
        MG.read_media(doc, link)


def test_original_source_is_bound_and_copied(tmp_path):
    doc = make_doc("ink")
    raw = b"original-source"
    doc["items"][0].update(source="/tools/image/motion/ink/source.png", source_sha256=sha(raw))
    assert MG.validate_document(doc) == []
    media = make_media(tmp_path, doc)
    (media / "ink" / "source.png").write_bytes(raw)
    assert MG.read_media(doc, media)["motion/ink/source.png"] == raw
    assert ("motion/ink/source.png", sha(raw)) in MG.expected_media(doc)
    (media / "ink" / "source.png").write_bytes(b"wrong")
    with pytest.raises(ValueError, match="SHA-256"):
        MG.read_media(doc, media)


@pytest.mark.parametrize("over", [
    {"source": "/tools/image/motion/ink/source.png"},
    {"source_sha256": "0" * 64},
    {"source": "https://evil.test/source.png", "source_sha256": "0" * 64},
    {"source": "/tools/image/motion/ink/../source.png", "source_sha256": "0" * 64},
    {"source": "/tools/image/motion/ink/source.png", "source_sha256": ""},
])
def test_original_source_rejects_unbound_or_unsafe_input(over):
    doc = make_doc("ink")
    doc["items"][0].update(over)
    assert MG.validate_document(doc)


def test_gif_is_hash_bound_and_copied_with_other_media(tmp_path):
    raw = b"GIF89a-test-media"
    doc = make_doc("ink")
    doc["items"][0].update(gif="/tools/image/motion/ink/share.gif", gif_sha256=sha(raw), placement="featured")
    assert not MG.validate_document(doc)
    media = make_media(tmp_path, doc)
    (media / "ink/share.gif").write_bytes(raw)
    assert MG.read_media(doc, media)["motion/ink/share.gif"] == raw
    assert ("motion/ink/share.gif", sha(raw)) in MG.expected_media(doc)
    (media / "ink/share.gif").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        MG.read_media(doc, media)


@pytest.mark.parametrize("over", [
    {"gif": "/tools/image/motion/ink/share.gif"},
    {"gif_sha256": "0" * 64},
    {"gif": "", "gif_sha256": "0" * 64},
    {"gif": "/tools/image/motion/ink/../share.gif", "gif_sha256": "0" * 64},
    {"gif": "https://evil.test/share.gif", "gif_sha256": "0" * 64},
    {"gif": "/tools/image/motion/ink/share.mp4", "gif_sha256": "0" * 64},
    {"gif": "/tools/image/motion/ink/share.gif", "gif_sha256": False},
    {"placement": "unknown"}, {"placement": ""}, {"placement": []},
])
def test_optional_downloads_and_placement_reject_invalid_input(over):
    doc = make_doc("ink")
    doc["items"][0].update(over)
    assert MG.validate_document(doc)
