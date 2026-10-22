from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from scripts.stylebook.qa import check_wide_content_span
from scripts.stylebook import compile as CP, contract as CT


def test_span_rejects_blank_center_lockup_and_footer_logo(tmp_path):
    p = tmp_path / "banner.png"
    im = Image.new("RGB", (940, 400), "#0e0e10")
    im.save(p)
    assert not check_wide_content_span(p, .65)["passed"]
    d = ImageDraw.Draw(im)
    d.rectangle((350, 130, 590, 270), fill="white")
    d.rectangle((850, 370, 930, 390), fill="white")
    im.save(p)
    assert not check_wide_content_span(p, .65)["passed"]
    d.rectangle((80, 100, 400, 260), fill="white")
    d.rectangle((540, 90, 860, 300), fill="#0e926f")
    im.save(p)
    assert check_wide_content_span(p, .65)["passed"]


def test_wide_contract_rejects_center_crop_format(monkeypatch):
    c = CT.load("C90")
    c["recipe"]["layout"]["wide_canvas_only"] = True
    monkeypatch.setattr(CT, "load", lambda *a, **kw: c)
    manifest = {"style": f"C90@r{c['revision']}", "format": "wechat-cover-head", "use_anchor": False,
                "content": {"subject": "dark editorial cover"},
                "text": {"mode": "native", "items": [{"role": "title", "text": "生图"},
                  {"role": "brand", "text": "叁笙"}, {"role": "tag", "text": "开源"},
                  {"role": "ghost", "text": "IMAGE SKILL"}]}}
    with pytest.raises(CP.CompileError, match="整幅宽画布"):
        CP.compile_manifest(manifest)
    manifest["format"] = "wechat-cover-brand-wide"
    result = CP.compile_manifest(manifest)
    assert "central square" not in result.prompt or "Do not compress" in result.prompt
    assert result.export_px == (1880, 800)
