import copy
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sb  # noqa: E402
from stylebook import compile as CP  # noqa: E402
from stylebook import contract as CT  # noqa: E402
from stylebook import cover_flow as CF  # noqa: E402
from stylebook.cover_flow import check_square_master, compile_flow  # noqa: E402
from test_compile import FREE  # noqa: E402


CONTRACT = copy.deepcopy(FREE)
CONTRACT.update(code="S02", revision=3, visibility="private")
CONTRACT["recipe"]["positive"] = (
    "Deep-charcoal flat-vector evidence montage, canvas aspect set by the requested production stage. "
    "A distinct left title zone and right open evidence collage."
)
CONTRACT["recipe"]["text_mode"] = ["native"]
MANIFEST = {
    "style": "S02@r3", "format": "wechat-cover-head",
    "content": {"subject": "One clay sauce jar and three small story badges"},
    "text": {"mode": "native", "items": [
        {"role": "title", "text": "ketchup 的福建话旅程"},
        {"role": "subtitle", "text": "从鱼露到番茄酱"},
    ]},
}


def test_square_first_flow_compiles_both_stages_and_exact_text(monkeypatch):
    monkeypatch.setattr(CT, "load", lambda _: copy.deepcopy(CONTRACT))
    flow = compile_flow(copy.deepcopy(MANIFEST))
    assert flow == compile_flow(copy.deepcopy(MANIFEST))
    square = flow["square_master"]
    assert flow["style"] == "S02@r3"
    assert square["aspect"] == "1:1"
    assert "at least 15% quiet background padding" in square["prompt"]
    assert "left title zone and right open evidence collage" in square["prompt"]
    assert "A wide banner cover" not in square["prompt"]
    assert CONTRACT["recipe"]["positive"] in square["prompt"]
    assert flow["outpaint"]["aspect"] == "2.35:1"
    assert "ONLY to its left and right sides" in flow["outpaint"]["prompt"]
    for value in flow["expected_text"]:
        assert value in square["prompt"] and value in flow["outpaint"]["prompt"]


def test_overlay_square_flow_keeps_text_out_of_generated_art(monkeypatch):
    candidate = copy.deepcopy(CONTRACT)
    candidate["revision"] = 15
    candidate["recipe"]["text_mode"] = ["overlay"]
    manifest = copy.deepcopy(MANIFEST)
    manifest["style"] = "S02@r15"
    manifest["text"]["mode"] = "overlay"
    for item in manifest["text"]["items"]:
        item["box"] = [0.35, 0.25 if item["role"] == "title" else 0.65, 0.3, 0.2]
    monkeypatch.setattr(CT, "load", lambda _: copy.deepcopy(candidate))

    flow = compile_flow(manifest)
    prompt = flow["square_master"]["prompt"]
    assert flow["workflow"] == "s02-center-square-overlay-v1"
    assert flow["expected_text"] == ["ketchup 的福建话旅程", "从鱼露到番茄酱"]
    assert "Place ALL meaningful letters" not in prompt
    assert "no text, letters or captions anywhere" in prompt
    assert "empty space at the left title zone inside the central 70% safe area" in prompt
    assert "Preserve all exact original text" not in flow["outpaint"]["prompt"]
    assert flow["postprocess"] == "export_overlay"


def test_overlay_square_flow_rejects_missing_text_box_and_gate_bypass_would_pass(monkeypatch):
    candidate = copy.deepcopy(CONTRACT)
    candidate["revision"] = 15
    candidate["recipe"]["text_mode"] = ["overlay"]
    manifest = copy.deepcopy(MANIFEST)
    manifest["style"] = "S02@r15"
    manifest["text"]["mode"] = "overlay"
    monkeypatch.setattr(CT, "load", lambda _: copy.deepcopy(candidate))
    with pytest.raises(CP.CompileError, match="box"):
        compile_flow(manifest)
    monkeypatch.setattr(CF, "validate_overlay", lambda _: [])
    # The compiler independently validates overlay boxes; bypassing only the
    # entry gate must still fail. The mutation must disable both actual gates.
    with pytest.raises(CP.CompileError, match="box"):
        compile_flow(manifest)
    import stylebook.overlay as overlay
    monkeypatch.setattr(overlay, "validate", lambda _: [])
    assert compile_flow(manifest)["workflow"] == "s02-center-square-overlay-v1"


def test_overlay_square_flow_rejects_duplicate_subject_layers(monkeypatch):
    candidate = copy.deepcopy(CONTRACT)
    candidate["revision"] = 15
    candidate["recipe"]["text_mode"] = ["overlay"]
    manifest = copy.deepcopy(MANIFEST)
    manifest["style"] = "S02@r15"
    manifest["text"]["mode"] = "overlay"
    manifest["text"]["items"][0]["box"] = [0.35, 0.25, 0.3, 0.2]
    manifest["text"]["items"][1]["box"] = [0.35, 0.65, 0.3, 0.2]
    manifest["text"]["image_layers"] = [{"path": "object.png", "box": [0.7, 0.3, 0.1, 0.2]}]
    monkeypatch.setattr(CT, "load", lambda _: copy.deepcopy(candidate))

    with pytest.raises(CP.CompileError, match="不能再叠加 image_layers"):
        compile_flow(manifest)
    del manifest["text"]["image_layers"]
    assert compile_flow(manifest)["workflow"] == "s02-center-square-overlay-v1"


@pytest.mark.parametrize("change,reason", [
    ({"format": "xhs-cover"}, "wechat-cover-head"),
    ({"aspect": "1:1"}, "最终画幅"),
    ({"text": {"mode": "overlay", "items": []}}, "合同允许"),
    ({"text": {"mode": "native", "items": [{"role": "tag", "text": "标签"}]}}, "缺少标题"),
])
def test_square_first_flow_rejects_invalid_briefs(monkeypatch, change, reason):
    monkeypatch.setattr(CT, "load", lambda _: copy.deepcopy(CONTRACT))
    with pytest.raises(CP.CompileError, match=reason):
        compile_flow({**copy.deepcopy(MANIFEST), **change})


def test_square_first_flow_rejects_aspect_locked_contract(monkeypatch):
    locked = copy.deepcopy(CONTRACT)
    locked["recipe"]["positive"] += " exact 2.35:1 landscape"
    monkeypatch.setattr(CT, "load", lambda _: locked)
    with pytest.raises(CP.CompileError, match="锁定层仍限定横版"):
        compile_flow(copy.deepcopy(MANIFEST))


def test_cover_flow_cli_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(CT, "load", lambda _: copy.deepcopy(CONTRACT))
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")
    assert sb.main(["cover-flow", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["square_master"]["aspect"] == "1:1"


def test_cover_check_rejects_edge_content_and_empty_square(tmp_path, capsys):
    path = tmp_path / "square.png"
    im = Image.new("RGB", (100, 100), (14, 14, 16))
    im.save(path)
    assert sb.main(["cover-check", str(path), "--style", "S02"]) == 1
    assert "空图" in capsys.readouterr().out
    draw = ImageDraw.Draw(im)
    draw.rectangle((20, 20, 79, 79), fill=(255, 255, 255))
    im.save(path)
    assert check_square_master(path, style="S02")["passed"]
    draw.rectangle((3, 40, 10, 50), fill=(255, 255, 255))
    im.save(path)
    result = check_square_master(path, style="S02")
    assert not result["passed"] and result["margins"]["left"] == 0.03


def test_cover_check_rejects_non_square(tmp_path):
    path = tmp_path / "wide.png"
    Image.new("RGB", (120, 100), "white").save(path)
    assert not check_square_master(path, style="S02")["passed"]


def test_zone_covers_c77_c90_do_not_use_the_square_first_flow():
    """C77/C90 的左文右图直接在居中方形内分区出 2.35:1；方形优先流程仍只属于 S02。"""
    for code in ("C77", "C90"):
        with pytest.raises(CP.CompileError, match="S02"):
            compile_flow({"style": code, "format": "wechat-cover-head",
                          "content": {"subject": "a camera"},
                          "text": {"mode": "native", "items": [{"role": "title", "text": "旧相机"}]}})


@pytest.mark.parametrize("style", [None, "C75", "C76@r1", "S020"])
def test_cover_check_rejects_unsupported_before_reading_image(tmp_path, style):
    result = check_square_master(tmp_path / "missing.png", style=style)
    assert result["passed"] is False
    assert result["status"] == "unsupported" and result["margins"] is None
