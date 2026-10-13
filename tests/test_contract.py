import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import contract as C  # noqa: E402

GOOD = {
    "code": "C99", "revision": 1,
    "name": {"zh": "测试风格", "en": "Test Style"},
    "family": "测试", "fit": ["讲故事"], "visibility": "public",
    "source": {"from": "单元测试", "license": "original"},
    "essence": ["第一条命门特征", "第二条命门特征", "第三条命门特征"],
    "recipe": {"positive": "A test recipe that is long enough to pass the minimum length rule."},
    "palette": {"recolor": "free", "colors": [{"name": "测试蓝 test blue", "hex": "#1F6F8B"}]},
    "qa": {"must_see": ["必须看到的一", "必须看到的二"], "must_not_see": ["不许看到的一", "不许看到的二"]},
}


def test_good_contract_passes():
    assert C.validate(GOOD) == []


def test_overlay_layout_policy_validation():
    k = copy.deepcopy(GOOD)
    k["qa"]["overlay_layout"] = {"headline_min_font_px_ratio": 0.12, "subtitle_max_lines": 1}
    assert any("需要 recipe.text_mode" in p for p in C.validate(k))
    k["recipe"]["text_mode"] = ["overlay"]
    assert C.validate(k) == []
    k["qa"]["overlay_layout"]["headline_min_font_px_ratio"] = 0
    assert any("须在" in p for p in C.validate(k))
    k["qa"]["overlay_layout"] = {}
    assert any("至少需要一条" in p for p in C.validate(k))


def test_structure_prompt_rejects_unknown_key_and_empty_instruction():
    k = copy.deepcopy(GOOD)
    k["recipe"]["structure_prompts"] = {"fow": "Keep the layout in a straight reading order."}
    assert any("未知表达结构 fow" in p for p in C.validate(k))
    k["recipe"]["structure_prompts"] = {"flow": ""}
    assert any("太短" in p for p in C.validate(k))


def test_hybrid_override_requires_supported_mode_and_local_when_text():
    k = copy.deepcopy(GOOD)
    k["qa"]["mode_overrides"] = {"hybrid": {
        "must_see": ["黏土标题", "功能文字精确"], "must_not_see": ["平面装饰标题", "深色背景"],
        "when_text": ["黏土标题"],
    }}
    assert any("不在 recipe.text_mode" in x for x in C.validate(k))
    k["recipe"]["text_mode"] = ["native", "hybrid"]
    k["recipe"]["text_style_hybrid"] = "A sculpted title and a blank inset for exact operational text."
    assert C.validate(k) == []
    k["qa"]["mode_overrides"]["hybrid"]["when_text"] = ["原生规则不应混入"]
    assert any("条目不在该模式" in x for x in C.validate(k))
    k["qa"]["mode_overrides"] = []
    assert any("应为 object" in x for x in C.validate(k))
    k["qa"].pop("mode_overrides")
    k["recipe"]["text_mode"] = ["native"]
    assert any("需要 recipe.text_mode 包含 hybrid" in x for x in C.validate(k))


@pytest.mark.parametrize("mutate, expect", [
    (lambda c: c.pop("essence"), "essence 缺失"),
    (lambda c: c["essence"].__setitem__(slice(1, None), []), "至少 3 项"),
    (lambda c: c["palette"]["colors"][0].__setitem__("hex", "1F6F8B"), "格式不对"),
    (lambda c: c["palette"].__setitem__("recolor", "anything"), "不在"),
    (lambda c: c.__setitem__("visibility", "private"), "私有风格的风格码必须以 S 开头"),
    (lambda c: c.__setitem__("revision", 0), "小于 1"),
    (lambda c: c.__setitem__("typo_field", 1), "不是合同字段"),
    (lambda c: c["qa"].__setitem__("must_see", ["只有一条"]), "至少 2 项"),
    (lambda c: c.__setitem__("fit", ["讲笑话"]), "不在"),
    (lambda c: c.__setitem__("changelog", [{"revision": 2, "date": "2026-09-25", "note": "x"}]), "changelog 最高修订号"),
])
def test_bad_contracts_are_rejected(mutate, expect):
    bad = copy.deepcopy(GOOD)
    mutate(bad)
    problems = C.validate(bad)
    assert problems, "坏合同竟然通过了"
    assert any(expect in p for p in problems), problems


def test_dir_name_must_match_code(tmp_path):
    p = tmp_path / "C98" / "contract.json"
    p.parent.mkdir()
    p.write_text(json.dumps(GOOD, ensure_ascii=False), encoding="utf-8")
    assert any("目录名" in x for x in C.validate(GOOD, where=p))


def test_contract_rejects_missing_and_changed_anchor(tmp_path):
    p = tmp_path / "C99" / "contract.json"
    p.parent.mkdir()
    anchor = p.parent / "anchor.png"
    k = copy.deepcopy(GOOD)
    k["anchor"] = {"file": anchor.name, "sha256": hashlib.sha256(b"original").hexdigest()}
    assert any("风格锚点不存在" in x for x in C.validate(k, where=p))
    anchor.write_bytes(b"original")
    assert C.validate(k, where=p) == []
    anchor.write_bytes(b"changed")
    assert any("sha256 不符" in x for x in C.validate(k, where=p))


def test_all_repo_contracts_valid():
    for c in C.load_all(strict=True):  # 任何一份不合格都会抛错
        assert c["code"]



def test_loaded_contract_compiles(tmp_path, monkeypatch):
    """load() 会附带 _path；加载结果直接交给编译器不能被当成非法字段拦下（09-25 实跑矩阵时踩到）。"""
    from stylebook import compile as CP
    d = tmp_path / "styles" / "C99"
    d.mkdir(parents=True)
    (d / "contract.json").write_text(json.dumps(GOOD, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(C, "STYLES_DIR", tmp_path / "styles")
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "none"))
    monkeypatch.setattr(C.Path, "home", staticmethod(lambda: tmp_path))
    loaded = C.load("C99")
    assert loaded["_path"].endswith("C99/contract.json")
    CP.compile_manifest({"style": "C99", "content": {"subject": "a cat"}}, loaded)


def test_anchor_enabled_requires_boolean():
    k = copy.deepcopy(GOOD)
    k["anchor"] = {"file": "anchor.png", "enabled": False}
    assert C.validate(k) == []
    k["anchor"]["enabled"] = "false"
    assert C.validate(k)


def test_text_role_and_layout_declarations_are_cross_checked():
    k = copy.deepcopy(GOOD)
    k["recipe"].update(required_text_roles=["title", "ghost"], optional_text_roles=["tag"],
                       role_styles={"ghost": "A faint all-caps English line behind the headline."},
                       layout={"text_zone": "left", "hero_zone": "right"}, text_mode=["native", "none"])
    assert C.validate(k) == []
    bad = copy.deepcopy(k)
    bad["recipe"]["optional_text_roles"] = ["ghost"]
    assert any("不能同时是必需和可选" in p for p in C.validate(bad))
    bad = copy.deepcopy(k)
    bad["recipe"]["role_styles"]["nothing"] = "A style for a role nobody declared at all."
    assert any("没有在 required_text_roles" in p for p in C.validate(bad))
    bad = copy.deepcopy(k)
    bad["recipe"]["text_mode"] = ["none"]
    assert any("required_text_roles 需要" in p for p in C.validate(bad))
    bad = copy.deepcopy(k)
    bad["recipe"]["layout"]["hero_zone"] = "left"
    assert any("不能在同一侧" in p for p in C.validate(bad))
    bad = copy.deepcopy(k)
    bad["recipe"]["layout"]["unknown"] = True
    assert C.validate(bad)  # 未声明的版式字段被 Schema 拒绝


def test_shipped_text_bearing_contracts_declare_their_text_roles():
    """合同要求画面里出现的文字必须声明成角色，配方正文不得自带只有 text.items 才能提供的文字。"""
    for code, role in (("C77", "ghost"), ("C90", "brand"), ("C90", "tag"), ("C76", "issue"), ("C81", "note")):
        r = C.load(code)["recipe"]
        assert role in (r.get("required_text_roles", []) + r.get("optional_text_roles", [])), (code, role)
        assert role in r.get("role_styles", {}), (code, role)
    for code in ("C77", "C90"):
        assert C.load(code)["recipe"]["layout"] == {"text_zone": "left", "hero_zone": "right"}
