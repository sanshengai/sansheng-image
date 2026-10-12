import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stylebook import export as EX  # noqa: E402
from stylebook import overlay as OL  # noqa: E402
from stylebook.overlay import render, validate  # noqa: E402
from stylebook.qa import compare_text  # noqa: E402


def test_strict_transcription_accepts_visual_lines_without_losing_characters():
    expected = ["两周，从一颗种子\n开始", "第1周", "第2周"]
    assert compare_text(expected, ["两周，从一颗种子", "开始", "第1周", "第2周"], strict=True) == []
    assert compare_text(expected, ["两周，从一颗种子开始", "第1周", "第2周"], strict=True) == []
    assert compare_text(["油灯暖光与窗外冷色\n形成对比"], ["油灯暖光与窗外冷色形成对比"], strict=True) == []


def test_strict_transcription_rejects_real_mutations_and_empty_input():
    expected = ["两周，从一颗种子\n开始", "第1周", "第2周"]
    for observed in [
        [],
        ["两周，从一颗种子", "开始", "第1周"],
        ["两周，从一颗种子", "开使", "第1周", "第2周"],
        ["两周从一颗种子", "开始", "第1周", "第2周"],
        ["两周，从一颗种子", "开始", "第1周", "第3周"],
        ["两周，从一颗种子", "开始", "第1周", "第2周", "第2周"],
        ["开始", "两周，从一颗种子", "第1周", "第2周"],
        ["两周，从一颗种子", "无关文字", "开始", "第1周", "第2周"],
    ]:
        assert compare_text(expected, observed, strict=True)
    assert compare_text(["uv tool install cli"], ["uvtool install cli"], strict=True)
    assert compare_text(["ABC"], ["abc"], strict=True)
    assert compare_text(["第一块", "第二块"], ["第一块第二块"], strict=True)
    assert compare_text(["无关文字", "两周，从一颗种子\n开始"],
                        ["两周，从一颗种子", "无关文字", "开始"], strict=True)


def spec():
    return {"mode": "overlay", "items": [
        {"text": "NotebookLM 接入四步", "box": [0.1, 0.08, 0.8, 0.08], "font_px": 60, "min_px": 50, "weight": "bold"},
        {"text": "uv tool install notebooklm-mcp-cli", "box": [0.1, 0.3, 0.8, 0.07], "font_px": 45, "min_px": 40},
    ]}


def test_one_line_colored_spans_keep_one_subtitle_and_exact_text():
    item = {"role": "subtitle", "text": "手机云盘", "box": [0.1, 0.2, 0.8, 0.6],
            "font_px": 40, "min_px": 40, "weight": "bold",
            "spans": [{"text": "手机", "color": "#FFFFFF"},
                      {"text": "云盘", "color": "#0E926F"}]}
    cfg = {"mode": "overlay", "items": [item]}
    assert validate(cfg) == []
    out = render(Image.new("RGB", (400, 100), "#0E0E10"), cfg)
    assert any(min(out.getpixel((x, y))) > 230 for x in range(40, 120) for y in range(20, 80))
    assert any((lambda c: c[1] > c[0] * 3 and c[1] > c[2])(out.getpixel((x, y)))
               for x in range(120, 210) for y in range(20, 80))
    item["spans"][1]["text"] = "云"
    assert any("完全一致" in e for e in validate(cfg))
    with pytest.raises(ValueError, match="完全一致"):
        render(Image.new("RGB", (400, 100), "#0E0E10"), cfg)
    item["spans"][1] = {"text": "云盘", "color": "not-a-color"}
    assert any("颜色无效" in e for e in validate(cfg))


def test_export_overlays_exact_text_before_safe_zone_crop(tmp_path):
    raw = tmp_path / "raw.png"
    Image.new("RGB", (1242, 1656), "#faf7ef").save(raw)
    out = tmp_path / "card.png"
    result = EX.export(raw, "xhs-carousel", out, overlay=spec())
    assert out.is_file()
    assert ImageChops.difference(Image.open(out), Image.open(raw)).getbbox()
    assert result.extras and result.extras[0].is_file()
    assert compare_text([x["text"] for x in spec()["items"]],
                        ["NotebookLM 接入四步", "uv tool install notebooklm-mcp-cli"], strict=True) == []
    assert compare_text(["uv tool install notebooklm-mcp-cli"],
                        ["uv tool install notebooklm mcp cli"], strict=True)
    assert compare_text(["uv tool install notebooklm-mcp-cli"],
                        ["uv  tool install notebooklm-mcp-cli"], strict=True)


def test_hybrid_export_only_draws_precise_item_and_rejects_bad_split(tmp_path):
    from copy import deepcopy
    raw = tmp_path / "raw.png"
    Image.new("RGB", (1242, 1656), "#faf7ef").save(raw)
    mixed = {"mode": "hybrid", "reserve": "a blank inset below the title", "items": [
        {"render": "native", "role": "title", "text": "第一步：安装", "position": "top"},
        {"render": "overlay", "role": "code", "text": "uv tool install notebooklm-mcp-cli",
         "box": [0.08, 0.35, 0.84, 0.08], "font_px": 55, "min_px": 55},
    ]}
    out = tmp_path / "card.png"
    EX.export(raw, "xhs-carousel", out, overlay=mixed)
    with Image.open(out) as image, Image.open(raw) as base:
        diff = ImageChops.difference(image, base)
        assert diff.crop((0, 0, 1242, 500)).getbbox() is None  # 原生标题不由叠字器重复写
        assert diff.crop((0, 500, 1242, 740)).getbbox() is not None
    bad = deepcopy(mixed)
    bad["items"][1]["render"] = "natvie"
    assert any("render" in e for e in OL.validate(bad))
    bad = deepcopy(mixed)
    bad["items"].pop()
    assert any("同时有" in e for e in OL.validate(bad))


def test_overlay_rejects_missing_position_and_illegible_text():
    bad = {"mode": "overlay", "items": [{"text": "nlm login"}]}
    assert "box 必须" in "；".join(validate(bad))
    tiny = {"mode": "overlay", "items": [
        {"text": "uv tool install notebooklm-mcp-cli", "box": [0.1, 0.1, 0.1, 0.02], "font_px": 48, "min_px": 40}]}
    with pytest.raises(ValueError, match="放不进区域"):
        render(Image.new("RGB", (1242, 1656)), tiny)


def test_opt_in_blank_area_rejects_object_under_exact_text(monkeypatch):
    from PIL import ImageDraw
    item = {"text": "必要信息", "box": [0.1, 0.1, 0.8, 0.3],
            "font_px": 25, "min_px": 25, "require_blank": True}
    config = {"mode": "overlay", "items": [item]}
    clean = Image.new("RGB", (400, 200), "#f5ded0")
    assert render(clean, config)
    obstructed = clean.copy()
    ImageDraw.Draw(obstructed).rectangle((140, 30, 260, 65), fill="#292d2e")
    with pytest.raises(ValueError, match="预留文字区有深色物件"):
        render(obstructed, config)
    item["require_blank"] = False
    assert render(obstructed, config)  # 变异：移除闸门会让物件上的排字通过
    item["require_blank"] = "yes"
    assert any("require_blank 必须是布尔值" in p for p in validate(config))


def test_overlay_rejects_overlapping_boxes():
    bad = spec()
    bad["items"][1]["box"] = [0.5, 0.1, 0.4, 0.08]
    assert "重叠" in "；".join(validate(bad))


def test_only_ghost_before_title_may_overlap():
    ghost = {"role": "ghost", "layer": "behind_title", "text": "CLOUD PRICING",
             "box": [0.1, 0.1, 0.5, 0.1], "font_px": 24, "min_px": 24}
    title = {"role": "title", "text": "阿里云涨价", "box": [0.1, 0.15, 0.5, 0.2],
             "font_px": 48, "min_px": 48, "weight": "bold"}
    allowed = {"mode": "overlay", "items": [ghost, title]}
    assert validate(allowed) == []
    assert ImageChops.difference(Image.new("RGB", (900, 500), "#0E0E10"),
                                 render(Image.new("RGB", (900, 500), "#0E0E10"), allowed)).getbbox()

    # 变异反例：把 ghost 改成普通副标题、调换绘制顺序，或把层标给标题，均须被拒绝。
    from copy import deepcopy
    bad = deepcopy(allowed)
    bad["items"][0]["role"] = "subtitle"
    assert any("只有 ghost" in e for e in validate(bad))
    assert any("重叠" in e for e in validate(bad))
    bad = {"mode": "overlay", "items": [title, ghost]}
    assert any("重叠" in e for e in validate(bad))
    bad = deepcopy(allowed)
    bad["items"][0].pop("layer")
    bad["items"][1]["layer"] = "behind_title"
    assert any("只有 ghost" in e for e in validate(bad))
    assert any("重叠" in e for e in validate(bad))


def test_export_places_transparent_art_and_tag_pill_before_square_crop(tmp_path):
    raw = tmp_path / "background.png"
    Image.new("RGB", (900, 383), "#0E0E10").save(raw)
    art = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    from PIL import ImageDraw
    ImageDraw.Draw(art).ellipse((10, 10, 90, 90), fill="#0E926F")
    art.save(tmp_path / "art.png")
    config = {"mode": "overlay", "image_layers": [{"path": "art.png", "box": [0.5, 0.25, 0.14, 0.4]}],
              "items": [{"role": "tag", "text": "摄影 / 修复", "box": [0.35, 0.7, 0.14, 0.07],
                         "font_px": 11, "min_px": 11, "align": "center", "valign": "center",
                         "pill": {"fill": "#101114", "outline": "#0E926F", "radius_px": 12}}]}
    out = tmp_path / "cover.png"
    result = EX.export(raw, "wechat-cover-head", out, overlay=config, overlay_root=tmp_path)
    assert result.extras and result.extras[0].is_file()
    with Image.open(out) as full, Image.open(result.extras[0]) as square:
        assert full.size == (900, 383) and square.size == (383, 383)
        assert full.getpixel((510, 170)) == (14, 146, 111)  # 透明物件按盒放在正文右边
        assert square.getpixel((252, 170)) == (14, 146, 111)
        assert full.getpixel((400, 283)) != (14, 14, 16)  # 标签胶囊并非底图的一部分


def test_ghost_may_run_behind_art_but_title_may_not(tmp_path):
    art = Image.new("RGBA", (100, 100), "#0E926F")
    art.putpixel((0, 0), (0, 0, 0, 0))
    art.save(tmp_path / "art.png")
    ghost = {"role": "ghost", "layer": "behind_title", "text": "MMMMMMMM",
             "box": [0.4, 0.25, 0.6, 0.2], "font_px": 60, "min_px": 60,
             "weight": "bold", "color": "#303033"}
    base = Image.new("RGB", (900, 383), "#0E0E10")
    ghost_only = render(base, {"mode": "overlay", "items": [ghost]})
    overlap = (480, 105, 540, 145)
    assert ImageChops.difference(ghost_only.crop(overlap), base.crop(overlap)).getbbox()
    cfg = {"mode": "overlay", "items": [ghost],
           "image_layers": [{"path": "art.png", "box": [0.5, 0.25, 0.15, 0.4]}]}
    assert validate(cfg) == []
    out = render(base, cfg, tmp_path)
    covered_ghost_pixels = [(x, y) for x in range(480, 540) for y in range(125, 145)
                            if ghost_only.getpixel((x, y)) != base.getpixel((x, y))]
    assert covered_ghost_pixels
    assert all(out.getpixel(p) == (14, 146, 111) for p in covered_ghost_pixels)
    cfg["items"].append({"role": "title", "text": "标题", "box": [0.52, 0.3, 0.12, 0.2]})
    assert any("image_layers" in e and "重叠" in e for e in validate(cfg))


def test_image_layers_reject_unsafe_or_opaque_assets(tmp_path):
    base = Image.new("RGB", (900, 383), "#0E0E10")
    spec = {"mode": "overlay", "items": [{"text": "标题", "box": [0.35, 0.2, 0.14, 0.2]}],
            "image_layers": [{"path": "../outside.png", "box": [0.5, 0.2, 0.14, 0.4]}]}
    assert any("相对路径" in e for e in validate(spec))
    spec["image_layers"][0]["path"] = "opaque.png"
    Image.new("RGB", (100, 100), "#0E926F").save(tmp_path / "opaque.png")
    with pytest.raises(ValueError, match="透明图片"):
        render(base, spec, asset_root=tmp_path)
    spec["image_layers"][0]["box"] = [0.4, 0.2, 0.14, 0.4]
    assert any("与文字区域重叠" in e for e in validate(spec))
    spec["image_layers"][0]["box"] = [0.5, 0.2, 0.14, 0.4]
    with pytest.raises(ValueError, match="asset_root"):
        render(base, spec)
    spec["items"][0]["pill"] = {"outline": "not-a-color"}
    assert any("pill 只可用于 tag" in e for e in validate(spec))


def test_overlay_invalid_font_setting_reports_error():
    bad = spec()
    bad["items"][0]["font_px"] = "small"
    assert "正整数" in "；".join(validate(bad))


def test_felt_effect_is_deterministic_and_opt_in():
    base = Image.new("RGB", (900, 900), "#fff5e7")
    s = {"items": [{"text": "番茄工作法", "box": [0.1, 0.1, 0.8, 0.15],
                    "font_px": 72, "min_px": 64, "weight": "bold", "effect": "felt"}]}
    a, b = render(base, s), render(base, s)
    assert not ImageChops.difference(a, b).getbbox()
    assert ImageChops.difference(base, a).getbbox()
    s["items"][0].pop("effect")
    flat = render(base, s)
    assert ImageChops.difference(a, flat).getbbox()


def test_overlay_rejects_unknown_effect():
    s = spec()
    s["items"][0]["effect"] = "glossy"
    assert "effect 只能" in "；".join(validate(s))


def test_overlay_serif_font_is_opt_in_and_keeps_exact_text():
    s = {"items": [{"text": "番茄工作法", "box": [0.1, 0.1, 0.8, 0.3],
                    "font_px": 64, "min_px": 60, "font_family": "serif"}]}
    base = Image.new("RGB", (900, 300), "#fffaf0")
    try:
        serif = render(base, s)
    except ValueError as exc:
        if "找不到可用的中文 serif" in str(exc):
            pytest.skip("系统未安装中文宋体 / Noto Serif CJK")
        raise
    assert ImageChops.difference(base, serif).getbbox()
    assert not ImageChops.difference(serif, render(base, s)).getbbox()
    s["items"][0]["font_family"] = "sans"
    assert ImageChops.difference(serif, render(base, s)).getbbox()


def test_overlay_rejects_unknown_font_family():
    s = spec()
    s["items"][0]["font_family"] = "missing"
    assert "font_family 只能" in "；".join(validate(s))


def test_hand_font_is_explicit_and_never_silently_replaced(monkeypatch):
    s = {"items": [{"text": "手写账本", "box": [0.1, 0.1, 0.8, 0.3],
                    "font_px": 64, "min_px": 60, "font_family": "hand"}]}
    assert validate(s) == []
    monkeypatch.setitem(OL.FONT_CANDIDATES["hand"], "regular", [])
    with pytest.raises(ValueError, match="请安装 Hannotate SC"):
        render(Image.new("RGB", (900, 300), "#fffaf0"), s)


def test_overlay_serif_font_missing_fails_explicitly(monkeypatch):
    monkeypatch.setitem(OL.FONT_CANDIDATES["serif"], "regular", [])
    s = {"items": [{"text": "番茄工作法", "box": [0.1, 0.1, 0.8, 0.3],
                    "font_px": 64, "min_px": 60, "font_family": "serif"}]}
    with pytest.raises(ValueError, match="找不到可用的中文 serif/regular 字体"):
        render(Image.new("RGB", (900, 300)), s)
