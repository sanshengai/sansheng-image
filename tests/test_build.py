import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import build as BD  # noqa: E402
from stylebook import contract as CT  # noqa: E402
from stylebook import data as D  # noqa: E402

PRIV = {
    "code": "S77", "revision": 2,
    "name": {"zh": "测试自用", "en": "Private Test"},
    "family": "自用", "fit": ["讲道理"], "visibility": "private",
    "source": {"from": "单元测试", "license": "original"},
    "essence": ["第一条命门特征", "第二条命门特征", "第三条命门特征"],
    "recipe": {"positive": "A private test recipe that is long enough to pass the minimum length rule."},
    "palette": {"recolor": "locked", "colors": [{"name": "测试白 test white", "hex": "#F7F2E9"}]},
    "qa": {"must_see": ["必须看到的一", "必须看到的二"], "must_not_see": ["不许看到的一", "不许看到的二"]},
}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    prof = tmp_path / "profile"
    (prof / "styles" / "S77").mkdir(parents=True)
    (prof / "styles" / "S77" / "contract.json").write_text(json.dumps(PRIV, ensure_ascii=False), encoding="utf-8")
    (prof / "catalog.json").write_text(json.dumps({"version": 1, "styles": [
        {"code": "S77", "zh": "测试自用", "family": "自用", "fit": ["讲道理"], "origin": "单元测试", "status": "draft"}]},
        ensure_ascii=False), encoding="utf-8")
    (prof / "author.json").write_text(json.dumps({"scene_defaults": {"xhs": {"default": "S77", "alternates": ["C31"]}}},
                                                 ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(prof))
    return prof


@pytest.fixture
def no_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "none"))
    monkeypatch.setattr(CT.Path, "home", staticmethod(lambda: tmp_path))


def test_registry_file_is_fresh(no_profile):
    """仓库里的 registry.json 必须等于由目录与合同重新生成的结果（改了合同忘了重建会红）。"""
    on_disk = json.loads(BD.REGISTRY_PATH.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(BD.registry(False), ensure_ascii=False))


def test_public_registry_never_leaks_private(profile):
    reg = BD.registry(False)
    assert BD.check(reg) == []
    assert all(s["code"].startswith("C") for s in reg["styles"])
    assert "S77" not in json.dumps(reg, ensure_ascii=False)
    assert next(s for s in reg["scenes"] if s["id"] == "xhs")["default"] == D.scenes()["xhs"]["default"]


def test_private_registry_overlays_profile(profile):
    reg = BD.registry(True)
    assert BD.check(reg) == []
    s77 = next(s for s in reg["styles"] if s["code"] == "S77")
    assert s77["has_contract"] and s77["revision"] == 2 and s77["recolor"] == "locked" and s77["visibility"] == "private"
    xhs = next(s for s in reg["scenes"] if s["id"] == "xhs")
    assert xhs["default"] == "S77" and xhs["source"] == "作者档案"


def test_check_catches_mismatches(profile):
    reg = BD.registry(True)
    bad = copy.deepcopy(reg)
    next(s for s in bad["styles"] if s["code"] == "S77")["revision"] = 1
    assert any("对不上" in p for p in BD.check(bad))
    bad = copy.deepcopy(reg)
    bad["styles"] = [s for s in bad["styles"] if s["code"] != "S77"]
    probs = BD.check(bad)
    assert any("合同 S77 在目录里没有条目" in p for p in probs) and any("引用了目录里没有的 S77" in p for p in probs)
    bad = copy.deepcopy(reg)
    bad["styles"].append(dict(bad["styles"][0]))
    assert any("重复" in p for p in BD.check(bad))
    bad = copy.deepcopy(reg)
    bad["removed"] = [{"code": bad["styles"][0]["code"]}]
    assert any("已去掉" in p for p in BD.check(bad))
    bad = copy.deepcopy(reg)
    bad["styles"].append(dict(bad["styles"][0], code="C999", has_contract=True))
    assert any("找不到合同文件" in p for p in BD.check(bad))


def test_check_catches_leak_into_public(profile):
    pub = BD.registry(False)
    leaked = copy.deepcopy(pub)
    leaked["styles"][0]["origin"] = "迁自 S77"
    assert any("私有内容" in p for p in BD.check(leaked))
    leaked = copy.deepcopy(pub)
    leaked["styles"].append(dict(leaked["styles"][0], code="S78"))
    assert any("私有内容" in p for p in BD.check(leaked))


def test_gallery_builds_with_samples(profile, tmp_path):
    from PIL import Image
    sd = tmp_path / "samples"
    sd.mkdir()
    code = BD.registry(False)["styles"][0]["code"]
    Image.new("RGB", (32, 32), (200, 100, 50)).save(sd / f"{code}-q1.png")
    Image.new("RGB", (32, 32), (200, 100, 50)).save(sd / f"{code}-T8.png")
    Image.new("RGB", (32, 32), (200, 100, 50)).save(sd / f"{code}-bogus.png")
    out = BD.gallery(tmp_path / "g.html", private=True, samples=[sd])
    h = out.read_text(encoding="utf-8")
    assert "__IMGS__" not in h and "__META__" not in h
    imgs = json.loads(h.split('<script id="imgdata" type="application/json">')[1].split("</script>")[0])
    assert set(imgs) >= {f"{code}-T3", f"{code}-T8"} and not any(k.endswith("bogus") for k in imgs)
    meta = json.loads(h.split('<script id="meta" type="application/json">')[1].split("</script>")[0])
    assert meta["private"] and any(c["id"] == "S77" for c in meta["cands"])
    assert "tpl" not in meta  # 画廊只导出选择，不保留另一套提示词编译器
    assert next(c for c in meta["cands"] if c["id"] == "S77")["recolor"] == "locked"
    pub = BD.gallery(tmp_path / "p.html", private=False).read_text(encoding="utf-8")
    pmeta = pub.split('<script id="meta" type="application/json">')[1].split("</script>")[0]
    pimgs = json.loads(pub.split('<script id="imgdata" type="application/json">')[1].split("</script>")[0])
    assert "S77" not in pmeta and not any(re.match(r"S\d", k) for k in pimgs)  # 图片 base64 里可能碰巧出现这串字符，不看它
    assert "C24-anchor" in pimgs and "C01-example" in pimgs and "C32-example" in pimgs
    assert "画风材质参考（非成图验收）" in pub and "单张测试图（非整组验收）" in pub


def test_disabled_anchor_is_not_gallery_sample():
    reg = BD.registry()
    images = BD.collect_images(reg)
    assert "C30-anchor" not in images  # C30 的锚点是自制风格参考板，按风格档案展示，不当原作参考
    assert "C06-anchor" in images and "C06-s1" in images
