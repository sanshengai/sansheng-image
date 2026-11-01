import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import place_card as PC  # noqa: E402


def el(v, conf="high"):
    return {"value": v, "source": "https://example.org/" + v, "confidence": conf}


def good():
    return {
        "version": 1, "place": "测试城", "place_en": "TESTCITY", "theme": "海丝", "country_label": "CHINA",
        "season_light": el("初夏清晨薄雾"),
        "landscape": [el("甲湾")],
        "landmarks": [el("甲塔"), el("乙桥"), el("丙寺")],
        "architecture": el("红砖燕尾脊"),
        "heritage": [el("甲戏")], "crafts": [el("乙绣")],
        "figure": {"mode": "artisan", "role": "绣娘", "action": "穿针", "costume": "蓝布衫",
                   "ethnicity_region": "汉族 · 测试镇", "source": "https://example.org/f", "confidence": "high"},
        "foods": [el("甲面线")], "flora_fauna": [el("乙花")],
        "palette": {"main": ["#aa3322", "#223344"], "accent": ["#ffcc00"]},
        "text": {"year": "2026", "seal": "海丝印象", "name_en": "TESTCITY · CHINA", "title": "刺桐花开",
                 "seal_place": "中国测试", "subtitle": ["一", "二"], "intro_zh": ["甲", "乙"],
                 "place_en": "TESTCITY, CHINA", "intro_en": ["a", "b"], "closing": ["四字 · 四字", "english"]},
    }


def test_good_passes():
    assert PC.validate(good()) == ([], [])


@pytest.mark.parametrize("n", [2, 4])
def test_landmark_count_rejected(n):
    c = good()
    c["landmarks"] = [el(f"地标{i}") for i in range(n)]
    errs, _ = PC.validate(c)
    assert any("恰好 3" in e for e in errs)


def test_duplicate_landmark_rejected():
    c = good()
    c["landmarks"][1] = el("甲 塔")
    errs, _ = PC.validate(c)
    assert any("重复" in e for e in errs)


def test_artisan_needs_ethnicity():
    c = good()
    del c["figure"]["ethnicity_region"]
    errs, _ = PC.validate(c)
    assert any("ethnicity_region" in e for e in errs)


def test_figure_none_ok():
    c = good()
    c["figure"] = {"mode": "none"}
    assert PC.validate(c)[0] == []
    assert "no people" in PC.to_manifest_fields(c)["content"]["subject"]


def test_unverified_warns():
    c = good()
    c["foods"][0] = {"value": "丁糕", "source": "", "confidence": "high"}
    c["crafts"][0] = el("戊雕", "unverified")
    errs, warns = PC.validate(c)
    assert errs == []
    assert any("丁糕" in w for w in warns)
    assert set(PC.unverified(c)) == {"美食 · 丁糕", "工艺 · 戊雕"}


def test_bad_source_and_confidence():
    c = good()
    c["foods"][0]["source"] = "http://bad url"
    c["landscape"][0]["confidence"] = "sure"
    errs, _ = PC.validate(c)
    assert len(errs) == 2


def test_special_region_warns():
    c = good()
    c["place"] = "香港"
    _, warns = PC.validate(c)
    assert any("由用户确认" in w for w in warns)


def test_cross_category_duplicate_warns():
    c = good()
    c["architecture"] = el("甲塔")
    _, warns = PC.validate(c)
    assert any("甲塔" in w for w in warns)


def test_title_and_seal_length():
    c = good()
    c["text"]["title"] = "一"
    c["text"]["seal"] = "一二三四五六七"
    errs, _ = PC.validate(c)
    assert len(errs) == 2


def test_manifest_inventory_only_card_items():
    f = PC.to_manifest_fields(good())
    inv = f["content"]["inventory"]
    for v in ("甲塔", "乙桥", "丙寺", "红砖燕尾脊", "甲面线"):
        assert v in inv
    assert "北京故宫" not in inv + f["content"]["subject"]
    assert inv.endswith("Do not add any landmark, building, costume or food that is not listed.")
    assert f["content"]["lighting"] == "初夏清晨薄雾"
    assert f["poster_layout"] == "magazine-scroll" and f["poster_info"] == "travel"


def test_text_render_modes():
    items = PC.to_manifest_fields(good())["text"]["items"]
    by = {i["role"] + i["text"]: i for i in items}
    title = [i for i in items if i["text"] == "刺桐花开"][0]
    assert title["render"] == "native" and title["direction"] == "vertical"
    assert all(i["render"] == "overlay" for i in items if i is not title)
    assert [i for i in items if i["text"] == "中国测试"][0]["seal"] == {"shape": "oval"}
    assert [i for i in items if i["text"] == "一\n二"]


def test_plan_rows_marks_unverified():
    c = good()
    c["foods"][0]["source"] = ""
    assert "⚠ 待确认" in PC.plan_rows(c)


def run_cli(path):
    return subprocess.run([sys.executable, str(ROOT / "scripts/sb.py"), "place-card", "check", str(path)],
                          capture_output=True, text=True)


def test_cli_exit_codes(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(good(), ensure_ascii=False), encoding="utf-8")
    assert run_cli(p).returncode == 0
    bad = good()
    bad["landmarks"].pop()
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    r = run_cli(p)
    assert r.returncode == 1 and "恰好 3" in r.stdout


def test_mutation_landmark_check_disabled(monkeypatch):
    c = good()
    c["landmarks"].append(el("丁阁"))
    assert PC.validate(c)[0]
    monkeypatch.setattr(PC, "_check_landmarks", lambda card, errors: None)
    assert not any("恰好" in e for e in PC.validate(c)[0])


def _card_brief(scene, **item):
    card = str(Path(__file__).resolve().parents[1] / "examples" / "place-cards" / "quanzhou.json")
    return {"version": 1, "scene": scene, "style": "C102", "reason": "测试", "confirmed": "skipped",
            "place_card": card, "items": [{"id": "a", "position": "cover", "message": "泉州", "visual": "scroll", **item}]}


def test_place_card_layout_only_applies_to_poster_scene():
    from stylebook import make as MK
    poster = MK.prepare(_card_brief("poster", format="poster-9x16"), base_path=Path("."))["tasks"][0]["manifest"]
    assert poster["poster_layout"] == "magazine-scroll" and poster["text"]["mode"] == "hybrid"
    # 反例：小红书场景只借用资料卡的画面内容，不套杂志栏版式与整套文旅文字
    xhs = MK.prepare(_card_brief("xhs", format="xhs-cover", text=["海丝簪花"]), base_path=Path("."))["tasks"][0]["manifest"]
    assert xhs.get("poster_layout") is None
    assert [i["text"] for i in xhs["text"]["items"]] == ["海丝簪花"]
    assert "Literal inventory" in xhs["content"]["inventory"]
