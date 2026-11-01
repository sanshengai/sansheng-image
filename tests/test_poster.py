"""海报用途：poster.py 的判据、提示词段、补 box，以及 compile / plan / stylecode / selection 的接线。"""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import compile as CP  # noqa: E402
from stylebook import contract as CT  # noqa: E402
from stylebook import data as D  # noqa: E402
from stylebook import plan as PL  # noqa: E402
from stylebook import poster as PO  # noqa: E402
from stylebook import selection as SEL  # noqa: E402
from stylebook import stylecode as SC  # noqa: E402

FREE = {
    "code": "C99", "revision": 2,
    "name": {"zh": "测试风格", "en": "Test Style"},
    "family": "测试", "fit": ["讲故事"], "visibility": "public",
    "source": {"from": "单元测试", "license": "original"},
    "essence": ["第一条命门特征", "第二条命门特征", "第三条命门特征"],
    "recipe": {"positive": "Soft watercolour storybook illustration with visible paper grain and loose ink lines.",
               "hard_constraints": ["Keep large calm areas of empty paper."]},
    "prompt_bans": ["neon", "dark background"],
    "palette": {"recolor": "free", "colors": [{"name": "测试蓝 test blue", "hex": "#1F6F8B"}]},
    "qa": {"must_see": ["必须看到的一", "必须看到的二"], "must_not_see": ["不许看到的一", "不许看到的二"]},
}


def poster_contract():
    c = copy.deepcopy(FREE)
    c["uses"] = ["海报"]
    c["recipe"].update({
        "poster_layouts": ["corner-embrace", "sky-title"],
        "ratio_range": [0.5, 1.0],
        "variants": {
            "classic": {"zh": "经典版", "en": "Classic technique: flat colour fields with a single hand-cut edge.", "default": True},
            "mirror": {"zh": "镜像版", "en": "Mirror technique: the composition is mirrored left to right."},
        },
        "region_skins": {
            "plain": {"en": "Plain paper material with neutral local motifs.", "lettering": "Lettering is a clean serif.", "default": True},
            "wild": {"en": "Rough hand-pressed material with bold regional patterns.", "experimental": True, "note": "只测过两张"},
        },
    })
    c["composition"] = {"negative_space": "60% calm negative space", "subject": "one subject in the lower corner",
                        "flow": "eye travels diagonally"}
    c["type"] = {"direction": ["vertical"], "face": "brush calligraphy face"}
    return c


BASE_TEXT = {"mode": "none"}


def manifest(**kw):
    m = {"style": "C99", "model": "gpt-image-2", "format": "poster-9x16", "text": BASE_TEXT,
         "content": {"subject": "a lone lighthouse on a cliff at dawn"}}
    m.update(kw)
    return m


def fmt(fid="poster-9x16"):
    return D.formats()[fid]


def probs(m, contract=None, fid=None):
    return PO.problems(contract or poster_contract(), m, fmt(fid or m.get("format", "poster-9x16")))


def comp(m, contract=None):
    return CP.compile_manifest(copy.deepcopy(m), contract=copy.deepcopy(contract or poster_contract()))


@pytest.fixture(autouse=True)
def _no_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "none"))
    monkeypatch.setattr(PL.CT.Path, "home", staticmethod(lambda: tmp_path))


# ---------- problems ----------

def test_valid_combination_passes():
    assert probs(manifest(poster_layout="corner-embrace", poster_info="mood", variant="mirror", region_skin="plain")) == []


def test_a_unknown_layout():
    out = probs(manifest(poster_layout="nope"))
    assert len(out) == 1 and "nope" in out[0] and "sky-title" in out[0]


def test_b_layout_not_for_format():
    out = probs(manifest(poster_layout="sky-title", format="poster-elevator"))
    assert out == []  # sky-title 支持电梯框架
    out = probs(manifest(poster_layout="corner-embrace", format="poster-elevator"))
    assert len(out) == 1 and "poster-elevator" in out[0] and "poster-9x16" in out[0]


def test_b_layout_without_format_is_rejected():
    m = manifest(poster_layout="sky-title")
    del m["format"]
    assert any("format" in x for x in PO.problems(poster_contract(), m, None))


def test_c_info_unknown_or_not_in_layout():
    assert any("poster_info" in x for x in probs(manifest(poster_layout="sky-title", poster_info="gala")))
    out = probs(manifest(poster_layout="corner-embrace", poster_info="event"))  # corner-embrace 只适合 mood
    assert len(out) == 1 and "event" in out[0] and "mood" in out[0]
    assert probs(manifest(poster_layout="sky-title", poster_info="product")) == []


def test_d_contract_layout_compatibility():
    out = probs(manifest(poster_layout="axis"))  # 合同只列 corner-embrace 与 sky-title
    assert len(out) == 1 and "corner-embrace" in out[0]
    plain = copy.deepcopy(poster_contract())
    del plain["recipe"]["poster_layouts"]
    assert probs(manifest(poster_layout="axis"), plain) == []
    assert probs(manifest(poster_layout="sky-title"), plain) == []
    out = probs(manifest(poster_layout="type-led"), plain)
    assert len(out) == 1 and "sky-title" in out[0] and "axis" in out[0]


def test_e_layout_and_structure_conflict():
    out = probs(manifest(poster_layout="sky-title", structure="flow"))
    assert any("structure" in x and "poster_layout" in x for x in out)
    assert probs(manifest(structure="flow")) == []  # 单独的 structure 不归海报管


def test_f_ratio_range_rejects_wide_formats_and_lists_options():
    out = probs(manifest(format="poster-16x9", scene="poster"))
    assert len(out) == 1
    assert "C99 适用宽高比 0.5–1.0，当前 16:9 为 1.78" in out[0]
    assert "poster-9x16" in out[0] and "poster-16x9" not in out[0].split("可选格式：")[1]
    assert "xhs-cover" not in out[0]  # 有场景时只列该场景的格式
    all_formats = probs(manifest(format="poster-16x9"))[0].split("可选格式：")[1]
    assert "xhs-cover" in all_formats  # 没有场景时列全部落在范围内的格式


def test_f_ratio_range_aspect_override_wins_and_applies_to_non_poster():
    assert probs(manifest(format="poster-16x9", aspect="2:3")) == []
    assert probs(manifest(aspect="16:9"))  # 竖版格式被 aspect 改成横版
    assert probs(manifest(format="xhs-cover")) == []  # 3:4 在范围内，非海报格式也受检查
    out = probs(manifest(format="wechat-cover-head"))  # 2.35:1
    assert len(out) == 1 and "适用宽高比" in out[0]


def test_g_variant():
    plain = copy.deepcopy(poster_contract())
    del plain["recipe"]["variants"]
    assert any("variant" in x for x in probs(manifest(variant="mirror"), plain))
    out = probs(manifest(variant="sideways"))
    assert len(out) == 1 and "classic" in out[0] and "mirror" in out[0]
    assert probs(manifest(variant="classic")) == []


def test_h_region_skin():
    plain = copy.deepcopy(poster_contract())
    del plain["recipe"]["region_skins"]
    assert any("region_skin" in x for x in probs(manifest(region_skin="plain"), plain))
    out = probs(manifest(region_skin="mars"))
    assert len(out) == 1 and "plain" in out[0] and "wild" in out[0]
    assert probs(manifest(region_skin="wild")) == []


def test_i_custom_format_needs_size_and_dpi():
    c = copy.deepcopy(poster_contract())
    c["recipe"]["ratio_range"] = [0.3, 3.0]
    custom = {"format": "poster-custom"}
    assert any("size_cm" in x for x in probs(manifest(**custom), c))
    assert any("dpi" in x for x in probs(manifest(size_cm=[50, 70], **custom), c))
    assert any("自定义尺寸不可用" in x for x in probs(manifest(size_cm=[50, 70], dpi=0, **custom), c))
    assert any("自定义尺寸不可用" in x for x in probs(manifest(size_cm=[10, 100], dpi=150, **custom), c))  # 比例超 3:1
    assert any("自定义尺寸不可用" in x for x in probs(manifest(size_cm=["x", 70], dpi=150, **custom), c))
    assert probs(manifest(size_cm=[50, 70], dpi=150, **custom), c) == []
    assert probs(manifest(size_cm=[50, 70], dpi=150), c) == []  # 非自定义格式不要求


def test_custom_size_ratio_is_checked_against_ratio_range():
    out = probs(manifest(format="poster-custom", size_cm=[90, 50], dpi=72))  # 横版超出 0.5–1.0
    assert len(out) == 1 and "900:500" in out[0]


def test_warnings_for_experimental_skin():
    assert PO.warnings(poster_contract(), manifest(region_skin="plain")) == []
    assert PO.warnings(poster_contract(), manifest()) == []
    out = PO.warnings(poster_contract(), manifest(region_skin="wild"))
    assert len(out) == 1 and "试验皮肤" in out[0] and "wild" in out[0] and "只测过两张" in out[0]


# ---------- 变异反例：比例检查确实是拒绝的那一道 ----------

def test_mutation_ratio_check_is_what_rejects(monkeypatch):
    m = manifest(format="poster-16x9", poster_layout="sky-title")
    with pytest.raises(CP.CompileError, match="适用宽高比"):
        comp(m)
    monkeypatch.setattr(PO, "_ratio_problem", lambda *a, **k: None)
    assert PO.problems(poster_contract(), m, fmt("poster-16x9")) == []  # 比例检查被拿掉后不再报错，证明上面的拒绝来自它
    assert "Aspect ratio 16:9" in comp(m).prompt


def test_mutation_layout_check_is_what_rejects(monkeypatch):
    m = manifest(poster_layout="axis")
    with pytest.raises(CP.CompileError, match="corner-embrace"):
        comp(m)
    monkeypatch.setattr(PO, "_layout_problems", lambda *a, **k: [])
    assert "Poster layout" in comp(m).prompt


# ---------- sections ----------

def test_sections_order_and_content():
    m = manifest(poster_layout="corner-embrace", poster_info="mood", variant="mirror", region_skin="plain",
                 text={"mode": "native", "items": [{"text": "海", "role": "title"}]})
    secs = PO.sections(poster_contract(), m, fmt())
    assert [k for k, _ in secs] == ["composition", "style_variant", "region_skin", "poster_layout", "poster_info"]
    d = dict(secs)
    assert d["composition"].startswith("Composition lock (follow exactly): ")
    comp_text = d["composition"]
    # 固定字段顺序：留白 → 主体 → 动线（合同没写 figure_scale / big_shape，不出现）
    assert comp_text.index("Negative space: 60% calm negative space.") < comp_text.index("Subject treatment: one subject") < comp_text.index("Flow: eye travels diagonally.")
    assert "Figure scale" not in comp_text and "Big shape" not in comp_text
    assert "Lettering: brush calligraphy face; text direction vertical." in comp_text
    assert d["style_variant"] == "Technique variant (follow exactly): Mirror technique: the composition is mirrored left to right."
    assert d["region_skin"] == ("Regional material language (follow exactly): Plain paper material with neutral local motifs. "
                                "Lettering is a clean serif.")
    layout = D.layouts()["corner-embrace"]
    assert d["poster_layout"].startswith(layout["en"])
    assert layout["title_en"] in d["poster_layout"]
    assert "mood" in d["poster_info"] and "title, subtitle, line" in d["poster_info"] and "never invent" in d["poster_info"]


def test_sections_defaults_and_no_lettering_without_text():
    secs = dict(PO.sections(poster_contract(), manifest(), fmt()))
    assert list(secs) == ["composition", "style_variant", "region_skin"]
    assert "Classic technique" in secs["style_variant"]  # 清单没写，取合同 default
    assert "Plain paper material" in secs["region_skin"]
    assert "Lettering" not in secs["composition"]  # text.mode = none：本图无字


def test_sections_empty_for_plain_contract():
    assert PO.sections(copy.deepcopy(FREE), manifest(), fmt()) == []


def test_sections_landscape_uses_landscape_variant():
    c = copy.deepcopy(poster_contract())
    del c["recipe"]["ratio_range"]
    c["recipe"]["poster_layouts"] = ["magazine-scroll"]
    lay = D.layouts()["magazine-scroll"]
    wide = dict(PO.sections(c, manifest(poster_layout="magazine-scroll", format="poster-custom", size_cm=[90, 50], dpi=72), fmt("poster-custom")))
    tall = dict(PO.sections(c, manifest(poster_layout="magazine-scroll"), fmt()))
    assert wide["poster_layout"] == lay["landscape_en"] and tall["poster_layout"] == lay["en"]
    flat = dict(PO.sections(c, manifest(poster_layout="magazine-scroll", aspect="16:9"), fmt()))
    assert flat["poster_layout"] == lay["landscape_en"]  # 清单 aspect 优先
    sky = dict(PO.sections(poster_contract(), manifest(poster_layout="sky-title"), fmt()))
    assert sky["poster_layout"] == D.layouts()["sky-title"]["en"]


# ---------- fill_boxes ----------

def test_fill_boxes_portrait_and_does_not_mutate():
    text = {"mode": "overlay", "items": [{"text": "海", "role": "title"}, {"text": "一句", "role": "subtitle", "box": [0.1, 0.1, 0.2, 0.05]}]}
    before = copy.deepcopy(text)
    out = PO.fill_boxes(text, "sky-title", fmt("poster-9x16"))
    assert text == before
    assert out["items"][0]["box"] == D.layouts()["sky-title"]["zones"]["title"]
    assert out["items"][1]["box"] == [0.1, 0.1, 0.2, 0.05]  # 已有 box 不动
    out["items"][0]["box"][0] = 9
    assert D.layouts()["sky-title"]["zones"]["title"][0] != 9  # 返回的是副本


def test_fill_boxes_hybrid_only_fills_overlay_items():
    text = {"mode": "hybrid", "reserve": "the top", "items": [{"text": "海", "role": "title", "render": "native"},
                                                              {"text": "一句", "role": "subtitle", "render": "overlay"}]}
    out = PO.fill_boxes(text, "axis", fmt("poster-9x16"))
    assert "box" not in out["items"][0]
    assert out["items"][1]["box"] == D.layouts()["axis"]["zones"]["subtitle"]


def test_fill_boxes_landscape_uses_landscape_zones_and_errors():
    text = {"mode": "overlay", "items": [{"text": "海", "role": "title"}]}
    out = PO.fill_boxes(text, "sky-title", fmt("poster-16x9"))
    assert out["items"][0]["box"] == D.layouts()["sky-title"]["zones_landscape"]["title"]
    with pytest.raises(ValueError, match="没有横版分区"):
        PO.fill_boxes(text, "corner-embrace", fmt("poster-16x9"))
    with pytest.raises(ValueError, match="qr"):
        PO.fill_boxes({"mode": "overlay", "items": [{"text": "x", "role": "qr"}]}, "axis", fmt("poster-16x9"))  # axis 横版没有 qr
    with pytest.raises(ValueError, match="role='date'"):
        PO.fill_boxes({"mode": "overlay", "items": [{"text": "x", "role": "date"}]}, "sky-title", fmt("poster-9x16"))
    with pytest.raises(ValueError, match="未知海报版式"):
        PO.fill_boxes(text, "nope", fmt())


# ---------- compile ----------

def test_compile_poster_sections_in_prompt_order():
    m = manifest(poster_layout="corner-embrace", poster_info="mood",
                 content={"subject": "a lone lighthouse", "purpose": "a poster for a coastal festival"})
    prompt = comp(m).prompt
    at = [prompt.index(x) for x in ("Visual style (follow exactly)", "Composition lock (follow exactly)",
                                    "Technique variant (follow exactly)", "Regional material language (follow exactly)",
                                    "Subject: a lone lighthouse", "Poster layout", "Information level — mood",
                                    "Purpose: a poster for a coastal festival")]
    assert at == sorted(at)
    assert D.layouts()["corner-embrace"]["en"] in prompt


def test_compile_places_layout_after_structure_block():
    c = copy.deepcopy(poster_contract())
    del c["recipe"]["ratio_range"]
    m = manifest(poster_layout="sky-title", density="sparse")
    prompt = comp(m, c).prompt
    assert prompt.index("Information density: sparse") < prompt.index("Poster layout")
    assert prompt.index("Poster layout") < prompt.index("No text, letters or captions")


def test_compile_without_poster_fields_matches_legacy_sections():
    m = {"style": "C99", "model": "gpt-image-2", "format": "xhs-cover",
         "content": {"subject": "a grandmother and her grandson making dumplings"},
         "text": {"mode": "native", "items": [{"text": "番茄工作法", "role": "title", "position": "top"}]}}
    prompt = CP.compile_manifest(copy.deepcopy(m), contract=copy.deepcopy(FREE)).prompt
    lines = prompt.split("\n")
    heads = [ln.split(":")[0] for ln in lines if ln]
    assert heads[:2] == ["Visual style (follow exactly)", "Subject"]
    for banned in ("Composition lock", "Technique variant", "Regional material", "Poster layout", "Information level"):
        assert banned not in prompt
    assert lines[0].startswith("Visual style (follow exactly): Soft watercolour")
    assert lines[1] == "Subject: a grandmother and her grandson making dumplings"
    assert lines[2].startswith("Text in the image, exactly as written")
    assert "Aspect ratio 3:4." in lines[3]
    # 同一份清单换成带海报字段的合同，仍只在锁定层之后多出新段，其余段逐字不变
    rich = CP.compile_manifest(copy.deepcopy(m), contract=poster_contract()).prompt.split("\n")
    assert rich[0] == lines[0] and rich[-3:] == lines[-3:]
    assert [ln for ln in rich if ln not in lines][0].startswith("Composition lock")


def test_compile_rejects_what_problems_rejects():
    with pytest.raises(CP.CompileError, match="适用宽高比 0.5–1.0"):
        comp(manifest(format="poster-16x9"))
    with pytest.raises(CP.CompileError) as ei:
        comp(manifest(poster_layout="nope", variant="sideways"))
    assert "nope" in str(ei.value) and "sideways" in str(ei.value)  # 多个问题合并在一条错误里


def test_compile_custom_size_sets_aspect_and_export_px():
    c = copy.deepcopy(poster_contract())
    c["recipe"]["ratio_range"] = [0.3, 3.0]
    out = comp(manifest(format="poster-custom", size_cm=[50, 70], dpi=150), c)
    assert out.aspect == "500:700"
    assert list(out.export_px) == PO.export_px_from_cm(50, 70, 150)["preview_px"]
    assert "Aspect ratio 500:700." in out.prompt
    with pytest.raises(CP.CompileError, match="size_cm"):
        comp(manifest(format="poster-custom"), c)


# ---------- plan ----------

def fake_contract(monkeypatch, contract):
    real = CT.load

    def load(code, *a, **k):
        return copy.deepcopy(contract) if code.split("@")[0] == "C99" else real(code, *a, **k)
    monkeypatch.setattr(CT, "load", load)


def plan_item(i=1, **kw):
    it = {"id": f"{i:02d}", "position": f"海报 {i}", "shape": "opinion", "form": "metaphor", "what": "灯塔海报",
          "subject": "a lone lighthouse on a cliff at dawn", "why": "海报只要一个焦点，灯塔最合适",
          "text": {"mode": "none"}}
    it.update(kw)
    return it


def poster_plan(items, code="C31", **kw):
    p = {"scene": "poster", "style": {"code": code, "source": "factory", "why": "出厂默认就是这个画风"}, "items": items}
    p.update(kw)
    return p


def test_plan_infers_default_layout_for_poster_scene():
    p = poster_plan([plan_item()])
    assert PL.check(p)[0] == []
    m = PL.manifests(p)[0]
    assert m["poster_layout"] == "sky-title" and m["scene"] == "poster"
    CP.compile_manifest(m)  # 计划放行的组合，编译不再拒


def test_plan_infers_default_layout_from_contract(monkeypatch):
    fake_contract(monkeypatch, poster_contract())
    p = poster_plan([plan_item()], code="C99")
    assert PL.check(p)[0] == []
    assert PL.manifests(p)[0]["poster_layout"] == "corner-embrace"


def test_plan_passes_through_fields_and_items_override_plan_defaults(monkeypatch):
    fake_contract(monkeypatch, poster_contract())
    p = poster_plan([plan_item(1), plan_item(2, poster_layout="sky-title", poster_info="product", variant="classic")], code="C99",
                    poster_layout="corner-embrace", poster_info="mood", variant="mirror", region_skin="wild")
    errs, warns = PL.check(p)
    assert errs == []
    assert len(warns) == 2 and all("试验皮肤" in w for w in warns)
    a, b = PL.manifests(p)
    assert (a["poster_layout"], a["poster_info"], a["variant"], a["region_skin"]) == ("corner-embrace", "mood", "mirror", "wild")
    assert (b["poster_layout"], b["poster_info"], b["variant"], b["region_skin"]) == ("sky-title", "product", "classic", "wild")
    for m in (a, b):
        CP.compile_manifest(m, contract=poster_contract())


def test_plan_passes_custom_size_through():
    c31_free = poster_plan([plan_item(format="poster-custom", size_cm=[50, 70], dpi=150)])
    assert PL.check(c31_free)[0] == []
    m = PL.manifests(c31_free)[0]
    assert m["size_cm"] == [50, 70] and m["dpi"] == 150
    CP.compile_manifest(m)
    missing = poster_plan([plan_item(format="poster-custom")])
    assert any("size_cm" in e for e in PL.check(missing)[0])


def test_plan_check_reports_poster_problems_with_item_tag(monkeypatch):
    fake_contract(monkeypatch, poster_contract())
    errs, _ = PL.check(poster_plan([plan_item(), plan_item(2, format="poster-16x9", poster_layout="sky-title")], code="C99"))
    assert len(errs) == 1 and errs[0].startswith("第 2 张（02）") and "适用宽高比" in errs[0]
    errs, _ = PL.check(poster_plan([plan_item(poster_layout="axis")], code="C99"))
    assert len(errs) == 1 and "corner-embrace" in errs[0]
    errs, _ = PL.check(poster_plan([plan_item(poster_layout="type-led")]))  # C31 没声明版式，只允许 sky-title / axis
    assert len(errs) == 1 and "sky-title" in errs[0]
    errs, _ = PL.check(poster_plan([plan_item(poster_layout="sky-title", format="poster-16x9", variant="x")]))
    assert any("variant" in e for e in errs)


def test_plan_non_poster_manifests_untouched():
    p = {"scene": "wxillus", "style": {"code": "C31", "source": "factory", "why": "公众号配图出厂默认"},
         "items": [plan_item(form="structure", shape="steps", structure="flow", points=["a", "b", "c"])]}
    p["items"][0].pop("text")
    p["items"][0]["position"] = "第 1 节「这一节的最后一句话」之后"
    assert PL.check(p)[0] == []
    m = PL.manifests(p)[0]
    assert not set(m) & {"poster_layout", "poster_info", "variant", "region_skin", "size_cm", "dpi", "scene"}


def test_plan_table_prefers_format_label():
    t = PL.table(poster_plan([plan_item()]))
    assert D.formats()["poster-9x16"]["label"] in t
    assert "格式：poster-9x16" not in t
    wx = PL.table({"scene": "wxillus", "style": {"code": "C31", "source": "factory", "why": "公众号配图出厂默认"},
                   "items": [plan_item(form="structure", shape="steps", structure="flow", points=["a"])]})
    assert D.formats()[D.scenes()["wxillus"]["formats"][0]]["label"] in wx


# ---------- 风格码 ----------

@pytest.mark.parametrize("code", [
    "sb2:poster/C101~corner-embrace", "sb2:poster/C103+mirror", "sb2:poster/C100~vertical-title",
    "sb2:poster/C100~vertical-title-ink", "sb2:poster/C100~axis+mirror-earth.L1S-1", "sb2:poster/C101@r2~sky-title-morandi",
    "sb2:poster/C100~axis-hex.1F6F8B.F4F1E8", "sb2:poster/C100~axis+mirror-hex.1F6F8B", "sb2:poster/C100~magazine-scroll",
    "sb2:xhs/C35-earth", "sb2:wxcover/C44",
])
def test_stylecode_round_trip(code):
    sc = SC.parse(code)
    assert sc.format() == code


def test_stylecode_fields_and_manifest_fields():
    sc = SC.parse("sb2:poster/C100~vertical-title-ink")
    assert (sc.style, sc.layout, sc.variant, sc.palette) == ("C100", "vertical-title", None, "ink")
    both = SC.parse("sb2:poster/C103~corner-embrace+mirror-earth.L1S0")
    assert (both.layout, both.variant, both.palette, both.light) == ("corner-embrace", "mirror", "earth", 1)
    out = both.manifest_fields()
    assert out["poster_layout"] == "corner-embrace" and out["variant"] == "mirror" and out["style"] == "C103"
    only_variant = SC.parse("sb2:poster/C103+mirror").manifest_fields()
    assert only_variant["variant"] == "mirror" and "poster_layout" not in only_variant
    old = SC.parse("sb2:xhs/C35-earth").manifest_fields()
    assert "poster_layout" not in old and "variant" not in old
    assert "poster_layout" not in SC.parse("sb1:C01-orig").manifest_fields()


def test_stylecode_old_codes_still_read():
    assert SC.parse("sb1:C31-flow-balanced-macaron.L1S0").format() == "sb1:C31-flow-balanced-macaron.L1S0"
    assert SC.parse("sb2:info/C42-hex.1F6F8B.F4F1E8").custom == ("#1F6F8B", "#F4F1E8")


@pytest.mark.parametrize("bad", ["sb2:poster/C100~", "sb2:poster/C100+", "sb2:poster/C100~Axis", "sb2:poster/C100+Mirror",
                                 "sb2:poster/C100+mirror~axis", "sb2:poster/C100~axis~sky-title", "sb2:poster/C100~axis-orig.L1S0",
                                 "sb1:C31~axis-orig"])
def test_stylecode_rejects_malformed_layout_or_variant(bad):
    with pytest.raises(SC.StyleCodeError):
        SC.parse(bad)


def test_stylecode_layout_on_sb1_cannot_format():
    with pytest.raises(SC.StyleCodeError):
        SC.StyleCode("C31", layout="axis").format()


def test_stylecode_find_catches_tilde_and_plus():
    s = "用叁笙生图 sb2:poster/C100~corner-embrace+mirror-earth 做海报，再来一张 sb2:poster/C103+mirror。旧码 sb1:C01-orig"
    assert SC.find(s) == ["sb2:poster/C100~corner-embrace+mirror-earth", "sb2:poster/C103+mirror", "sb1:C01-orig"]


def test_stylecode_validate_layout_and_variant(monkeypatch):
    assert SC.validate(SC.parse("sb2:poster/C31~axis")) == []
    assert any("海报版式" in x for x in SC.validate(SC.parse("sb2:poster/C31~nosuch")))
    assert any("没有手法变体" in x for x in SC.validate(SC.parse("sb2:poster/C31+mirror")))
    fake_contract(monkeypatch, poster_contract())
    real_catalog = D.catalog
    monkeypatch.setattr(D, "catalog", lambda: {**real_catalog(), "C99": {"code": "C99"}})
    assert SC.validate(SC.parse("sb2:poster/C99~corner-embrace+mirror")) == []
    assert any("可选" in x for x in SC.validate(SC.parse("sb2:poster/C99+sideways")))


# ---------- selection ----------

def test_selection_carries_layout_and_variant_into_plan(monkeypatch):
    sel = SEL.normalize("sb2:poster/C31~axis")
    assert sel["poster_layout"] == "axis" and "variant" not in sel
    plan = poster_plan([plan_item()])
    out = SEL.apply(plan, sel)
    assert out["poster_layout"] == "axis"
    assert PL.manifests(out)[0]["poster_layout"] == "axis"
    with pytest.raises(SEL.SelectionError, match="没有手法变体"):
        SEL.normalize("sb2:poster/C31+mirror")
    with pytest.raises(SEL.SelectionError, match="未知海报版式"):
        SEL.normalize({"version": 1, "scene": "poster", "style": {"code": "C31"}, "poster_layout": "nope"})
    assert "poster_layout" not in SEL.normalize("sb2:poster/C31")
    changed = SEL.affected("sb2:poster/C31~axis", "sb2:poster/C31~sky-title", plan)
    assert changed == ["01"]
    assert SEL.affected("sb2:poster/C31~axis", "sb2:poster/C31~axis", plan) == []


def test_title_placement_and_reserved_areas_in_layout_section():
    fmt = D.formats()["poster-9x16"]
    base = {"poster_layout": "sky-title", "text": {"mode": "hybrid", "items": [
        {"render": "native", "role": "title", "text": "篾间"},
        {"render": "overlay", "role": "subtitle", "text": "一根竹", "box": [0.1, 0.8, 0.8, 0.05]}]}}
    d = dict(PO.sections({}, base, fmt))
    assert "HORIZONTALLY" in d["poster_layout"]
    assert "x 10–90%, y 80–85%" in d["poster_layout"]
    assert "must sit entirely within x 10–90%, y 7–19%" in d["poster_layout"]
    # 反例：无文字时不提标题位置与留字区
    d2 = dict(PO.sections({}, {"poster_layout": "sky-title", "text": {"mode": "none"}}, fmt))
    assert "Main title placement" not in d2["poster_layout"] and "clean, empty" not in d2["poster_layout"]


def test_every_layout_has_title_placement():
    for lid, layout in D.layouts().items():
        assert layout.get("title_en", "").startswith("Main title placement"), lid


def test_overlay_title_gets_no_native_title_area():
    fmt = D.formats()["poster-9x16"]
    m = {"poster_layout": "sky-title", "text": {"mode": "overlay", "items": [
        {"role": "title", "text": "篾间", "box": [0.1, 0.07, 0.8, 0.12]}]}}
    d = dict(PO.sections({}, m, fmt))
    assert "must sit entirely within" not in d["poster_layout"]
    assert "x 10–90%, y 7–19%" in d["poster_layout"]


def test_fill_boxes_zone_overrides_role_and_is_removed():
    fmt = D.formats()["poster-9x16"]
    text = {"mode": "overlay", "items": [{"role": "seal", "text": "刺桐"}, {"role": "seal", "text": "中国泉州", "zone": "seal_place"}]}
    out = PO.fill_boxes(text, "magazine-scroll", fmt)
    zones = D.layouts()["magazine-scroll"]["zones"]
    assert out["items"][0]["box"] == zones["seal"] and out["items"][1]["box"] == zones["seal_place"]
    assert "zone" not in out["items"][1]


def test_fill_boxes_centres_horizontal_items_in_centred_layouts():
    fmt = D.formats()["poster-9x16"]
    out = PO.fill_boxes({"mode": "overlay", "items": [{"role": "subtitle", "text": "a"},
                                                      {"role": "subtitle", "text": "b", "align": "right"}]}, "sky-title", fmt)
    assert out["items"][0]["align"] == "center" and out["items"][1]["align"] == "right"
    # 反例：左对齐版式不加居中
    out2 = PO.fill_boxes({"mode": "overlay", "items": [{"role": "subtitle", "text": "a"}]}, "big-shape-band", fmt)
    assert "align" not in out2["items"][0]
