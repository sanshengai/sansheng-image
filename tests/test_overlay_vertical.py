"""竖排、印章、kai 与 light 字重的叠字测试。"""
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stylebook import overlay as OL  # noqa: E402
from stylebook.overlay import render, validate  # noqa: E402

WHITE = (255, 255, 255)


def canvas(w=400, h=400):
    return Image.new("RGB", (w, h), "#ffffff")


def vertical(text, box=(0.1, 0.05, 0.8, 0.9), **kw):
    item = {"text": text, "box": list(box), "direction": "vertical", "font_px": 40, "min_px": 20,
            "color": "#000000", "font_family": "serif", "weight": "bold"}
    item.update(kw)
    return {"mode": "overlay", "items": [item]}


def ink(img):
    """非白像素的 (bbox, 质心x, 质心y, 像素数)。"""
    mask = img.convert("L").point(lambda v: 255 if v < 200 else 0)
    box = mask.getbbox()
    assert box, "画面没有任何墨迹"
    xs = ys = n = 0
    w, h = mask.size
    data = mask.tobytes()
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            if data[y * w + x]:
                xs, ys, n = xs + x, ys + y, n + 1
    return box, xs / n, ys / n, n


# ---------------------------------------------------------------- 竖排版面

def test_vertical_single_column_runs_top_to_bottom():
    box, cx, cy, _ = ink(render(canvas(), vertical("天地玄黄宇宙洪荒")))
    assert box[2] - box[0] <= 46          # 只占一列
    assert box[3] - box[1] >= 7 * 40      # 8 字自上而下
    assert box[1] < 60                    # 从 box 顶部开始（align 默认 top）


def test_vertical_columns_go_right_to_left():
    full = render(canvas(), vertical("天地玄黄\n宇宙洪荒"))
    first = render(canvas(), vertical("天地玄黄"))
    # 第一行单独渲染时落在最右列；整段渲染里它必须仍在右列，第二行在它左侧。
    second_only = ImageChops.difference(full, first)
    assert second_only.getbbox()
    _, x_first, _, _ = ink(first)
    _, x_second, _, _ = ink(second_only)
    assert x_first > x_second + 30


def test_vertical_wraps_by_box_height_and_newline_forces_new_column():
    # box 高 160、字号 40：每列 4 字，8 字应 2 列
    wrapped = render(canvas(), vertical("天地玄黄宇宙洪荒", box=(0.05, 0.05, 0.9, 0.4)))
    box, *_ = ink(wrapped)
    assert box[2] - box[0] > 40 + 20
    forced = render(canvas(), vertical("天\n地", box=(0.05, 0.05, 0.9, 0.9)))
    fbox, *_ = ink(forced)
    assert fbox[2] - fbox[0] > 40 + 20 and fbox[3] - fbox[1] < 60


def test_vertical_align_inside_column():
    top = ink(render(canvas(), vertical("天地", align="top")))[0]
    mid = ink(render(canvas(), vertical("天地", align="center")))[0]
    bottom = ink(render(canvas(), vertical("天地", align="bottom")))[0]
    assert top[1] < mid[1] < bottom[1]
    assert bottom[3] > 340


def test_vertical_line_gap_widens_column_pitch():
    tight = ink(render(canvas(), vertical("天\n地", line_gap=1.0)))[0]
    wide = ink(render(canvas(), vertical("天\n地", line_gap=2.0)))[0]
    assert (wide[2] - wide[0]) > (tight[2] - tight[0]) + 25


# ---------------------------------------------------------------- 竖排标点

def check_vertical_punct():
    pairs = {"「": "﹁", "」": "﹂", "『": "﹃", "』": "﹄", "（": "︵", "）": "︶", "《": "︽", "》": "︾"}
    for src, dst in pairs.items():
        assert OL._vertical_form(src) == dst, f"{src} 应换成竖排形 {dst}"
        assert OL._vertical_tokens(src) == [("char", dst)]
    assert OL._vertical_form("天") == "天"
    kinds = dict((t[1], t[0]) for t in OL._vertical_tokens("。，、·：——……～"))
    assert kinds["。"] == kinds["，"] == kinds["、"] == "corner"
    assert kinds["·"] == kinds["："] == "center"
    assert kinds["—"] == kinds["…"] == kinds["～"] == "rotate"


def test_vertical_punctuation_forms():
    check_vertical_punct()


def test_vertical_punctuation_mutation_is_caught(monkeypatch):
    """变异反例：把竖排标点映射关成恒等，标点测试必须失败，证明它不是空转。"""
    monkeypatch.setattr(OL, "VERTICAL_FORMS", {})
    with pytest.raises(AssertionError):
        check_vertical_punct()


def test_vertical_comma_sits_top_right_of_cell():
    # 句读墨迹应落在字格右上：x 靠右半，y 靠上半
    img = render(canvas(), vertical("，", font_px=80, min_px=80, align="top", box=(0.1, 0.1, 0.8, 0.8)))
    (x0, y0, x1, y1), *_ = ink(img)
    cell_left, cell_right = 400 - 40 - 80, 400 - 40          # 80px 字格，靠 box 右缘
    assert (x0 + x1) / 2 > (cell_left + cell_right) / 2       # 偏右
    assert (y0 + y1) / 2 < 40 + 40                           # 偏上


def test_vertical_rotated_dash_is_vertical_stroke():
    (x0, y0, x1, y1), *_ = ink(render(canvas(), vertical("——", font_px=60, min_px=60)))
    assert (y1 - y0) > 3 * (x1 - x0)


def test_missing_vertical_glyph_falls_back_to_rotated_horizontal(monkeypatch):
    monkeypatch.setattr(OL, "_has_glyph", lambda font, ch: False)
    img = render(canvas(), vertical("「", font_px=80, min_px=80))
    assert ink(img)[3] > 50


# ---------------------------------------------------------------- 纵中横

def test_tcy_tokens():
    assert OL._vertical_tokens("12") == [("tcy", "12")]
    assert OL._vertical_tokens("A") == [("tcy", "A")]
    assert OL._vertical_tokens("123") == [("run", "123")]
    assert OL._vertical_tokens("年12月") == [("char", "年"), ("tcy", "12"), ("char", "月")]


def test_tcy_is_horizontal_in_one_cell_and_long_run_is_rotated():
    (a0, b0, a1, b1), *_ = ink(render(canvas(), vertical("12", font_px=80, min_px=80)))
    assert (a1 - a0) > (b1 - b0)          # 横置：宽大于高
    assert (b1 - b0) < 80                 # 只占一格
    (c0, d0, c1, d1), *_ = ink(render(canvas(), vertical("ABCDEFG", font_px=40, min_px=40)))
    assert (d1 - d0) > 3 * (c1 - c0)      # 顺时针转 90° 后沿列方向延伸


# ---------------------------------------------------------------- 放不下 / 重叠

def test_vertical_rejects_when_columns_exceed_box_width():
    spec = vertical("天地玄黄宇宙洪荒" * 4, box=(0.4, 0.1, 0.1, 0.2), font_px=40, min_px=36)
    with pytest.raises(ValueError, match="放不进区域"):
        render(canvas(), spec)


def test_vertical_rejects_run_taller_than_column():
    spec = vertical("ABCDEFGHIJKLMNOP", box=(0.1, 0.1, 0.5, 0.2), font_px=40, min_px=36)
    with pytest.raises(ValueError, match="放不进区域"):
        render(canvas(), spec)


def test_vertical_box_overlapping_horizontal_box_is_rejected():
    spec = vertical("天地")
    spec["items"].append({"text": "横排文字", "box": [0.3, 0.3, 0.3, 0.1], "font_px": 30, "min_px": 20})
    assert any("重叠" in p for p in validate(spec))
    with pytest.raises(ValueError, match="重叠"):
        render(canvas(), spec)


def test_vertical_validation_messages():
    assert any("direction" in p for p in validate(vertical("天", direction="diagonal")))
    assert any("竖排 align" in p for p in validate(vertical("天", align="left")))
    assert any("line_gap" in p for p in validate(vertical("天", line_gap=0.5)))
    assert any("spans" in p for p in validate(vertical("天", spans=[{"text": "天", "color": "#000000"}])))
    assert validate(vertical("天", align="bottom")) == []


# ---------------------------------------------------------------- 印章

def seal(text, box=(0.3, 0.3, 0.3, 0.3), **kw):
    item = {"role": "seal", "text": text, "box": list(box)}
    item.update(kw)
    return {"mode": "overlay", "items": [item]}


def red_ink_box(img):
    mask = img.convert("RGB").point(lambda v: 255 if v < 235 else 0).convert("L")
    return mask.getbbox()


@pytest.mark.parametrize("text,box", [("叁笙", (0.3, 0.2, 0.2, 0.4)), ("叁笙画坊", (0.3, 0.3, 0.3, 0.3)),
                                       ("叁笙一页课桌", (0.2, 0.3, 0.5, 0.35))])
def test_seal_2_4_6_chars_render_inside_box(text, box):
    img = render(canvas(), seal(text, box))
    bbox = red_ink_box(img)
    assert bbox
    assert bbox[0] >= box[0] * 400 - 1 and bbox[2] <= (box[0] + box[2]) * 400 + 1
    assert bbox[1] >= box[1] * 400 - 1 and bbox[3] <= (box[1] + box[3]) * 400 + 1
    r, g, b = img.getpixel((round((box[0] + 0.01) * 400) + 8, round((box[1] + 0.01) * 400) + 8))
    assert r > 150 and g < 120   # 朱红


@pytest.mark.parametrize("text", ["叁", "叁笙一页课桌上", ""])
def test_seal_rejects_bad_char_count(text):
    spec = seal(text)
    assert validate(spec)
    with pytest.raises(ValueError):
        render(canvas(), spec)
    if text:
        assert any("2–6 个字" in p for p in validate(spec))


def test_seal_is_deterministic_and_text_seeded():
    spec = seal("叁笙画坊")
    a, b = render(canvas(), spec), render(canvas(), spec)
    assert ImageChops.difference(a, b).getbbox() is None
    other = render(canvas(), seal("叁笙画房"))
    assert ImageChops.difference(a, other).getbbox() is not None


def test_seal_styles_and_shapes():
    white = render(canvas(), seal("叁笙", box=(0.3, 0.2, 0.2, 0.4), seal={"style": "white"}))
    red = render(canvas(), seal("叁笙", box=(0.3, 0.2, 0.2, 0.4), seal={"style": "red"}))
    # 阴文实底墨量远大于阳文边框
    assert ink(white)[3] > ink(red)[3] * 1.5
    oval = render(canvas(), seal("叁笙", box=(0.3, 0.2, 0.2, 0.4), seal={"shape": "oval"}))
    assert oval.getpixel((122, 82)) == WHITE        # 椭圆外角无墨
    assert white.getpixel((122, 82)) != WHITE       # 方章角上有墨
    custom = render(canvas(), seal("叁笙", box=(0.3, 0.2, 0.2, 0.4), color="#1F4E8C"))
    r, g, b = custom.getpixel((125, 90))
    assert b > r


def test_seal_validation_messages():
    assert any("seal.shape" in p for p in validate(seal("叁笙", seal={"shape": "star"})))
    assert any("seal.style" in p for p in validate(seal("叁笙", seal={"style": "blue"})))
    assert any("印章字体" in p for p in validate(seal("叁笙", font_family="sans")))
    assert any("印章不支持 pill" in p for p in validate(seal("叁笙", pill={})))
    assert validate(seal("叁笙", font_family="kai")) == []   # kai 取值合法；字体是否存在由渲染期检查


def test_seal_box_overlapping_other_box_is_rejected():
    spec = seal("叁笙")
    spec["items"].append({"text": "标题", "box": [0.4, 0.4, 0.3, 0.1], "font_px": 30, "min_px": 20})
    assert any("重叠" in p for p in validate(spec))


# ---------------------------------------------------------------- light 与 kai

def _need(weight, family):
    try:
        return OL._font(weight, 40, family)
    except ValueError as e:
        pytest.skip(f"本机没有 {family}/{weight} 字体：{e}")


def test_serif_light_weight_is_selected_by_face_name():
    font = _need("light", "serif")
    assert "Light" in font.getname()[1], f"light 字重选到了 {font.getname()}"
    light = ink(render(canvas(), vertical("永", font_family="serif", weight="light", font_px=120, min_px=120)))[3]
    bold = ink(render(canvas(), vertical("永", font_family="serif", weight="bold", font_px=120, min_px=120)))[3]
    assert light < bold


def test_light_weight_missing_is_explicit(monkeypatch):
    monkeypatch.setitem(OL.FONT_CANDIDATES["serif"], "light", [])
    with pytest.raises(ValueError, match="serif/light.*light 字重"):
        render(canvas(), vertical("永", font_family="serif", weight="light"))
    assert any("没有 light 字重" in p for p in validate(vertical("永", font_family="sans", weight="light")))
    assert any("weight 只能" in p for p in validate(vertical("永", weight="heavy")))


def test_kai_available_or_clear_error(monkeypatch):
    try:
        font = OL._font("regular", 40, "kai")
    except ValueError as e:
        assert "kai/regular" in str(e) and "楷体" in str(e)
        pytest.skip("本机没有楷体字体")
    assert "kai" in " ".join(font.getname()).lower() or "wenkai" in " ".join(font.getname()).lower()
    assert ink(render(canvas(), vertical("永", font_family="kai", font_px=100, min_px=100)))[3] > 100
    assert ink(render(canvas(), seal("叁笙", font_family="kai")))[3] > 100
    monkeypatch.setitem(OL.FONT_CANDIDATES["kai"], "regular", [])
    with pytest.raises(ValueError, match="找不到可用的中文 kai/regular 字体.*楷体"):
        render(canvas(), vertical("永", font_family="kai", weight="regular"))
    with pytest.raises(ValueError, match="kai/regular"):
        render(canvas(), seal("叁笙", font_family="kai", weight="regular"))


def test_kai_missing_reports_error_even_without_system_font(monkeypatch):
    monkeypatch.setitem(OL.FONT_CANDIDATES["kai"], "regular", [])
    monkeypatch.setitem(OL.FONT_CANDIDATES["kai"], "bold", [])
    with pytest.raises(ValueError, match="找不到可用的中文 kai/bold 字体.*楷体"):
        render(canvas(), seal("叁笙", font_family="kai"))


# ---- 2026-11 首批海报验收后补：竖排均分、自动字色、文字区干净度 ----
from stylebook import overlay as _OV


def _vspec(text, box, **kw):
    return {"mode": "overlay", "items": [dict({"role": "subtitle", "text": text, "box": box, "direction": "vertical",
                                                "font_px": 40, "min_px": 40, "font_family": "serif"}, **kw)]}


def test_vertical_balances_columns_instead_of_orphan():
    font = _OV._font("regular", 40, "serif")
    lines = [_OV._vertical_tokens("一根竹编出四季")]
    cap = 40 * 6  # 一列最多 6 字 → 未均分会是 6+1
    raw = _OV._pack_vertical(lines, font, 40, cap)
    assert [len(c) for c in raw] == [6, 1]
    balanced = _OV._balance_vertical(lines, font, 40, cap, raw)
    assert sorted(len(c) for c in balanced) == [3, 4]


def test_auto_color_picks_light_text_on_dark_background():
    dark = Image.new("RGB", (400, 400), (20, 30, 70))
    light = Image.new("RGB", (400, 400), (240, 236, 228))
    item = {"text": "x", "box": [0.1, 0.1, 0.5, 0.2], "color": "auto"}
    assert _OV._resolve_auto_color(dark, item)["color"] == _OV.AUTO_LIGHT
    assert _OV._resolve_auto_color(light, item)["color"] == _OV.AUTO_DARK
    assert item["color"] == "auto"  # 不改原配置


def test_auto_color_renders_visible_text_on_dark():
    img = Image.new("RGB", (600, 600), (20, 30, 70))
    spec = {"mode": "overlay", "items": [{"role": "subtitle", "text": "EDGE", "box": [0.1, 0.1, 0.8, 0.2],
                                          "font_px": 60, "min_px": 30, "color": "auto"}]}
    out = _OV.render(img, spec)
    crop = out.crop((60, 60, 540, 180)).convert("L")
    assert crop.getextrema()[1] > 200  # 出现了浅色笔画


def test_require_clean_rejects_busy_box_and_accepts_plain_dark():
    busy = Image.new("RGB", (600, 600), (240, 236, 228))
    from PIL import ImageDraw as _D
    _D.Draw(busy).rectangle((60, 60, 300, 120), fill=(180, 40, 40))  # 飘带压进文字区
    spec = {"mode": "overlay", "items": [{"role": "subtitle", "text": "把一点光挂在路口", "box": [0.1, 0.1, 0.8, 0.2],
                                          "font_px": 30, "min_px": 20, "font_family": "serif", "require_clean": True}]}
    with pytest.raises(ValueError, match="不干净"):
        _OV.render(busy, spec)
    plain_dark = Image.new("RGB", (600, 600), (20, 30, 70))
    _OV.render(plain_dark, spec)  # 深色但干净的底：放行


def test_require_clean_mutation_disabled_lets_busy_box_through(monkeypatch):
    busy = Image.new("RGB", (600, 600), (240, 236, 228))
    from PIL import ImageDraw as _D
    _D.Draw(busy).rectangle((60, 60, 300, 120), fill=(180, 40, 40))
    spec = {"mode": "overlay", "items": [{"role": "subtitle", "text": "把一点光", "box": [0.1, 0.1, 0.8, 0.2],
                                          "font_px": 30, "min_px": 20, "require_clean": True}]}
    monkeypatch.setattr(_OV, "CLEAN_MAX_SHARE", 1.0)  # 变异：关掉判据，原本应拒绝的不再拒绝
    _OV.render(busy, spec)


def test_wrap_breaks_long_english_into_box():
    from PIL import Image
    long = ("Istanbul spreads across both sides of the Bosphorus, where domes and minarets rise above the water "
            "and tea is poured in small tulip glasses all day long.")
    item = {"text": long, "box": [0.05, 0.5, 0.3, 0.3], "font_px": 14, "min_px": 10, "wrap": True}
    assert OL.validate({"mode": "overlay", "items": [item]}) == []
    OL.render(Image.new("RGB", (1080, 1920), "white"), {"mode": "overlay", "items": [item]})
    # 反例：不折行时同一段放不进
    flat = {k: v for k, v in item.items() if k != "wrap"}
    with pytest.raises(ValueError, match="放不进"):
        OL.render(Image.new("RGB", (1080, 1920), "white"), {"mode": "overlay", "items": [flat]})


def test_wrap_must_be_bool():
    assert OL.validate({"mode": "overlay", "items": [{"text": "a", "box": [0, 0, 0.5, 0.1], "wrap": "yes"}]})


def test_missing_glyph_falls_back_instead_of_tofu():
    """中文正文字体缺土耳其文 İ：必须换用含该字形的字体，不能渲染成方块。"""
    from PIL import Image
    item = {"text": "ISTANBUL · TÜRKİYE", "box": [0.05, 0.5, 0.6, 0.05], "font_px": 24, "min_px": 12}
    face = OL._face_for_text("regular", "sans", item["text"])
    font = OL.ImageFont.truetype(face[0], 24, index=face[1]) if face else OL._font("regular", 24, "sans")
    assert OL._has_glyph(font, "İ")
    OL.render(Image.new("RGB", (1080, 1920), "white"), {"mode": "overlay", "items": [item]})


def test_unrenderable_char_raises(monkeypatch):
    """反例：所有候选都缺字时报错，而不是静默出方块。"""
    monkeypatch.setattr(OL, "LATIN_FALLBACKS", [])
    monkeypatch.setattr(OL, "FONT_CANDIDATES", {"sans": OL.FONT_CANDIDATES["sans"]})
    OL._face_for_text.cache_clear()
    try:
        with pytest.raises(ValueError, match="找不到包含这些字符的字体"):
            OL._face_for_text("regular", "sans", "\U0001F9FF\u0F00")
    finally:
        OL._face_for_text.cache_clear()
