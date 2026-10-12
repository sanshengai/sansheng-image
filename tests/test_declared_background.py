"""blank_background 深/浅平底预检的专用测试；全部走正式消费者的实际 render 路径。

通过正式 stylebook.overlay 消费者执行，使用仓内相对路径。
"""
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from stylebook import overlay as OL
_CONSUMER = SCRIPTS / "stylebook/overlay.py"

DARK = "#0E0E10"
LIGHT = "#F5DED0"
BOX = (40, 20, 360, 80)  # 400x200 画布上 box [0.1,0.1,0.8,0.3] 的像素范围，面积 19200


def item(**over):
    base = {"text": "必要信息", "box": [0.1, 0.1, 0.8, 0.3], "font_px": 25, "min_px": 25,
            "require_blank": True, "blank_background": DARK, "color": "#FFFFFF"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not _DROP}


_DROP = object()


def cfg(**over):
    return {"mode": "overlay", "items": [item(**over)]}


def canvas(color=DARK, size=(400, 200)):
    return Image.new("RGB", size, color)


def drew_text(before, after):
    return ImageChops.difference(before.crop(BOX), after.crop(BOX)).getbbox() is not None


def test_module_is_the_actual_overlay_consumer():
    assert Path(OL.__file__).resolve() == _CONSUMER
    assert OL.__name__ == "stylebook.overlay"


# ---- 通过路径 ----

def test_dark_plain_with_light_text_passes_and_draws():
    base = canvas(DARK)
    out = OL.render(base, cfg())
    assert drew_text(base, out)


def test_light_plain_with_dark_text_passes_and_draws():
    base = canvas(LIGHT)
    out = OL.render(base, cfg(blank_background=LIGHT, color="#263432"))
    assert drew_text(base, out)


def test_colored_spans_on_dark_pass_when_every_span_contrasts():
    base = canvas(DARK)
    c = cfg(color=_DROP, spans=[{"text": "必要", "color": "#FFFFFF"},
                                {"text": "信息", "color": "#0E926F"}])
    assert OL.validate(c) == []
    assert drew_text(base, OL.render(base, c))
    c["items"][0]["spans"][1]["color"] = "#202020"
    with pytest.raises(ValueError, match="亮度差"):
        OL.render(base, c)


def test_default_light_branch_is_unchanged_without_blank_background():
    plain_item = {"text": "必要信息", "box": [0.1, 0.1, 0.8, 0.3], "font_px": 25,
                  "min_px": 25, "require_blank": True}
    c = {"mode": "overlay", "items": [plain_item]}
    light = canvas("#f5ded0")
    assert OL.render(light, c)  # 浅底平板仍通过，且字色可不写
    obstructed = light.copy()
    ImageDraw.Draw(obstructed).rectangle((140, 30, 260, 65), fill="#292d2e")
    with pytest.raises(ValueError, match="预留文字区有深色物件"):
        OL.render(obstructed, c)
    with pytest.raises(ValueError, match=r"预留文字区有深色物件或线条（占比 100\.0%）"):
        OL.render(canvas(DARK), c)  # 复现：旧分支仍拒绝纯深底
    assert OL.validate(c) == []


# ---- 拒绝路径 ----

def test_dark_field_rejects_white_object():
    base = canvas(DARK)
    ImageDraw.Draw(base).rectangle((140, 30, 259, 65), fill="#FFFFFF")
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(base, cfg())


def test_dark_field_rejects_darker_object():
    base = canvas("#404040")
    ImageDraw.Draw(base).rectangle((140, 30, 259, 65), fill="#101010")
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(base, cfg(blank_background="#404040"))


def test_wrong_declared_background_is_rejected_not_guessed():
    dark_image = canvas("#101418")
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(dark_image, cfg(blank_background=LIGHT, color="#263432"))
    light_image = canvas(LIGHT)
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(light_image, cfg(blank_background=DARK, color="#FFFFFF"))


def test_non_flat_gradient_is_rejected():
    base = canvas(DARK)
    d = ImageDraw.Draw(base)
    for x in range(base.width):
        d.line((x, 0, x, base.height - 1), fill=(16 + x // 2,) * 3)
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(base, cfg(blank_background="#101010"))


def test_small_foreign_area_passes_but_larger_one_is_rejected():
    small = canvas(DARK)
    ImageDraw.Draw(small).rectangle((100, 40, 109, 49), fill="#FFFFFF")  # 100px ≈ 0.5%
    assert OL.render(small, cfg())
    large = canvas(DARK)
    ImageDraw.Draw(large).rectangle((100, 40, 129, 59), fill="#FFFFFF")  # 600px ≈ 3.1%
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(large, cfg())


def test_check_uses_original_resolution_so_thin_lines_are_not_averaged_away():
    base = canvas(DARK, (1600, 800))
    d = ImageDraw.Draw(base)
    for x in range(0, 1600, 20):  # 1px 白竖线，文字盒内约占 5%；缩到 160px 会被 BOX 平均稀释
        d.line((x, 0, x, 799), fill="#FFFFFF")
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(base, cfg(font_px=40, min_px=40))


def test_channel_delta_threshold_boundary_is_fixed_at_32():
    ok = canvas("#404040")
    ImageDraw.Draw(ok).rectangle((100, 30, 300, 70), fill="#202020")  # 差恰为 32，不算异物
    assert OL.render(ok, cfg(blank_background="#404040"))
    bad = canvas("#404040")
    ImageDraw.Draw(bad).rectangle((100, 30, 300, 70), fill="#1F1F1F")  # 差 33
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(bad, cfg(blank_background="#404040"))


def test_low_contrast_text_is_rejected():
    with pytest.raises(ValueError, match="亮度差"):
        OL.render(canvas(DARK), cfg(color="#303030"))
    with pytest.raises(ValueError, match="亮度差"):
        OL.render(canvas(LIGHT), cfg(blank_background=LIGHT, color="#D0D0D0"))


@pytest.mark.parametrize("bad", ["", None, "#FFF", "#12345G", "#11223344", "red", "112233",
                                 0x112233, " #112233", "#112233\n", "rgb(1,2,3)", [], {}])
def test_invalid_or_empty_blank_background_is_rejected(bad):
    c = cfg(blank_background=bad)
    assert any("blank_background" in p for p in OL.validate(c))
    with pytest.raises(ValueError, match="blank_background"):
        OL.render(canvas(DARK), c)


@pytest.mark.parametrize("flag", [_DROP, False, None, "yes", 1])
def test_blank_background_requires_require_blank_true(flag):
    c = cfg(require_blank=flag)
    assert any("blank_background" in p or "require_blank" in p for p in OL.validate(c))
    with pytest.raises(ValueError):
        OL.render(canvas(DARK), c)


@pytest.mark.parametrize("color", [_DROP, "", None, "#FFF", "white", "#FFFFFFFF", 255])
def test_text_color_must_be_explicit_strict_rgb(color):
    c = cfg(color=color)
    assert any("字色" in p for p in OL.validate(c))
    with pytest.raises(ValueError, match="字色"):
        OL.render(canvas(DARK), c)


def test_pill_is_not_allowed_with_blank_background():
    c = cfg(role="tag", pill={"fill": "#FFFFFF"})
    assert any("pill" in p for p in OL.validate(c))
    with pytest.raises(ValueError, match="pill"):
        OL.render(canvas(DARK), c)


def test_empty_inputs_are_still_rejected():
    assert any("至少需要一条" in p for p in OL.validate({"mode": "overlay", "items": []}))
    with pytest.raises(ValueError, match="至少需要一条"):
        OL.render(canvas(DARK), {"mode": "overlay", "items": []})
    with pytest.raises(ValueError, match="留白区域为空"):
        OL.render(Image.new("RGB", (0, 0)), cfg())  # 空画布
    tiny_box = cfg(box=[0.1, 0.1, 0.001, 0.001])  # 400x200 上取整后宽高为 0 的 crop
    with pytest.raises(ValueError, match="留白区域为空"):
        OL.render(canvas(DARK), tiny_box)


# ---- 变异：证明被测路径真的是闸门，且无法靠关掉 require_blank 绕过 ----

def obstructed_dark():
    base = canvas(DARK)
    ImageDraw.Draw(base).rectangle((140, 30, 259, 65), fill="#FFFFFF")
    return base


def test_mutation_disabling_require_blank_does_not_open_the_gate():
    base = obstructed_dark()
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(base, cfg())  # 基线：被挡
    for flag in (False, _DROP):
        with pytest.raises(ValueError, match="blank_background"):
            OL.render(base, cfg(require_blank=flag))  # 搭配 false/缺省仍拒绝
    # 直接调用检查函数同样不认 require_blank=false
    with pytest.raises(ValueError, match="blank_background"):
        OL._check_reserved_blank(base, item(require_blank=False), 1)


def test_control_without_both_fields_the_obstructed_image_is_drawn_over():
    base = obstructed_dark()
    control = cfg(require_blank=_DROP, blank_background=_DROP)
    assert OL.validate(control) == []
    out = OL.render(base, control)
    assert drew_text(base, out)  # 去掉两者后字被画到物件上，说明被测路径确实能画


def test_mutation_noop_check_lets_obstruction_through(monkeypatch):
    base = obstructed_dark()
    c = cfg()
    assert OL.validate(c) == []  # 配置本身合法，拦截完全来自像素检查
    with pytest.raises(ValueError, match="与声明底色"):
        OL.render(base, c)
    monkeypatch.setattr(OL, "_check_reserved_blank", lambda image, it, n: None)
    assert drew_text(base, OL.render(base, c))


def test_compile_carries_declared_background_into_generation_prompt():
    from stylebook.compile import _text_section
    spec = cfg(blank_background="#124B48", color="#F1E7CE")
    manifest = {"text": spec}
    prompt = _text_section(manifest, None, "gpt-image")
    assert '"color":"#124B48"' in prompt
    assert 'do not add pale panels' in prompt
    assert '[0.1,0.1,0.8,0.3]' in prompt


def test_compile_rejects_invalid_declared_background():
    from stylebook.compile import _text_section, CompileError
    with pytest.raises(CompileError, match="blank_background"):
        _text_section({"text": cfg(blank_background="")}, None, "gpt-image")


def test_compile_legacy_does_not_add_declared_background_instruction():
    from stylebook.compile import _text_section
    spec = cfg(blank_background=_DROP, color=_DROP)
    prompt = _text_section({"text": spec}, None, "gpt-image")
    assert 'Explicit reserved background colors' not in prompt
    assert 'free of outlines, arrows, icons, letters and dark marks' in prompt
