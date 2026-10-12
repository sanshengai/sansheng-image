import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import data as D  # noqa: E402


def test_counts():
    assert len(D.structures()) == 24
    assert len(D.palettes()["palettes"]) == 20
    assert len(D.scenes()) == 12  # 动图已下架（旧版 motion 命令保留）
    assert "motion" not in D.scenes()
    fams = {f["family"] for f in D.formats().values()}
    assert fams == {"单张封面", "多张轮播", "叙事分格", "信息结构", "视频帧", "透明底多宫格", "透明底单图", "教材插图"}
    single = D.formats()["sticker-single"]
    assert single["transparent"] and single["export_px"] == [512, 512] and single["text"]["default"] == "none"
    assert D.scenes()["sticker"]["formats"] == ["sticker-grid", "sticker-single"]
    assert len(json.loads((ROOT / "styles/catalog.json").read_text())["styles"]) == 81
    for s in json.loads((ROOT / "styles/catalog.json").read_text())["styles"]:
        assert s["uses"], s["code"]


def test_removed_styles_absent():
    cat = json.loads((ROOT / "styles/catalog.json").read_text())
    codes = {s["code"] for s in cat["styles"]}
    assert not codes & {"C23", "C33", "C37", "C43", "C47", "C78"}


def test_scene_references_resolve():
    fmts, cat = D.formats(), {s["code"] for s in json.loads((ROOT / "styles/catalog.json").read_text())["styles"]}
    for sc in D.scenes().values():
        for f in sc["formats"]:
            assert f in fmts, (sc["id"], f)
        for code in [sc["default"], *sc["alternates"]]:
            assert code in cat, (sc["id"], code)
            assert code.startswith("C"), "公开出厂默认不得引用私有风格"


# 出厂默认（2026-09-25 定稿，2026-09-30 画风库 v2 按 sandy 的挑选更新）：含私有风格的原始选择，逐项机器比对
LEDGER_1247 = {"xhs": "S01", "wxillus": "S01", "wxcover": "C30", "ppt": "C73", "info": "C40",
               "comic4": "C58", "book": "C08", "board": "C01", "audio": "C30", "sticker": "C24"}


def test_public_defaults_follow_ledger():
    for sid, code in LEDGER_1247.items():
        sc = D.scenes()[sid]
        if sid == "xhs":  # 作者档案是 S01；公开默认取知识卡画风 C31
            assert sc["default"] == "C31"
        elif sid == "wxillus":
            assert sc["default"] == "C24"
        elif code.startswith("C"):
            assert sc["default"] == code, sid
        else:  # 私有风格在公开版里退到第一个公开备选
            assert sc["default"] != code and sc["default"].startswith("C"), sid


def test_author_profile_matches_ledger():
    import os
    import pytest
    if not os.environ.get("SANSHENG_IMAGE_PROFILE"):
        pytest.skip("未设置私有 profile")
    for sid, code in LEDGER_1247.items():
        d, _, src = D.scene_choice(sid)
        assert (d, src) == (code, "作者档案"), sid


def test_every_scene_style_is_in_its_use_pool():
    """反例锚点：把某个默认/备选画风的用途勾选去掉，这条测试必须失败。"""
    cat = json.loads((ROOT / "styles/catalog.json").read_text())["styles"]
    uses = {s["code"]: set(s["uses"]) for s in cat}
    for sc in D.scenes().values():
        use = sc.get("use")
        if not use:
            assert sc.get("hidden"), sc["id"]
            continue
        for code in [sc["default"], *sc["alternates"]]:
            assert use in uses[code], (sc["id"], code, use)


def test_use_pool_filters_and_excludes():
    pool = D.styles_for_use("PPT")
    assert "C73" in pool and "C01" not in pool  # C01 没有被标为适合 PPT
    assert set(D.styles_for_use("教材")) >= {"C30", "C42"}


def test_merged_and_retired_codes():
    from stylebook import contract as CT
    assert CT.canonical("C03@r2") == ("C01", "C03 已并入 C01")
    assert CT.load("C26")["code"] == "C24" and CT.load("C26")["_alias_from"] == "C26"
    import pytest
    with pytest.raises(KeyError, match="建议改用 C35"):
        CT.load("C10")
    with pytest.raises(KeyError, match="重新选择"):
        CT.load("C44")


def test_style_rules_of_library_v2():
    """原作名进提示词；有锚点的必须登记来源；没有锚点的不能声称有。
    2026-10-02 起原本没有原作样图的画风改用自制的无人物风格参考板当锚点，来源登记为本项目。"""
    from stylebook import contract as CT
    c = CT.load("C06")
    assert "Quentin Blake" in c["recipe"]["positive"] and c["inspiration"]["names"] == ["昆汀·布莱克"]
    assert c["anchor"]["origin"]["license"] == "MIT" and c["renderer"] == "codex"
    import json as _json
    ledger = {e["style"]: e for e in _json.loads((ROOT / "styles/anchor-provenance.json").read_text(encoding="utf-8"))["anchors"]}
    c30 = CT.load("C30")
    assert c30["anchor"]["sha256"] == ledger["C30"]["sha256"]
    assert ledger["C30"]["origin"]["repo"] == "sanshengai/sansheng-image" and "no people" in ledger["C30"]["derivation"]
    assert ledger["C06"]["origin"]["repo"] != "sanshengai/sansheng-image", "有开源原作样图的画风不被自制参考板替换"


def test_demoted_styles_keep_prior_admission_record():
    from stylebook import contract as CT
    for code in ("C01", "C08", "C25", "C32", "C58"):
        ev = CT.load(code)["evidence"]
        assert ev["admission"] == "pending" and ev["prior"]["revision"] < CT.load(code)["revision"], code
