import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sb  # noqa: E402


def test_generate_refuses_to_overwrite(tmp_path, capsys):
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"style": "C99", "content": {"subject": "x"}}), encoding="utf-8")
    out = tmp_path / "done.png"
    out.write_bytes(b"accepted")
    assert sb.main(["generate", str(m), "-o", str(out)]) == 2
    assert out.read_bytes() == b"accepted"
    assert "不覆盖" in capsys.readouterr().err


def test_errors_become_chinese_messages(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "none"))
    assert sb.main(["compile", str(tmp_path / "nope.json")]) == 2
    assert "文件不存在" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert sb.main(["compile", str(bad)]) == 2
    assert "JSON 格式有误" in capsys.readouterr().err
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"style": "C99", "content": {"subject": "x"}}), encoding="utf-8")
    assert sb.main(["compile", str(m)]) == 2
    assert "找不到" in capsys.readouterr().err


def test_single_qa_manifest_requires_content_verdict(tmp_path, capsys, monkeypatch):
    from PIL import Image
    from stylebook import contract as CT
    image = tmp_path / "image.png"
    Image.new("RGB", (64, 64), (240, 240, 235)).save(image)
    contract = {"code": "C99", "revision": 1, "qa": {"must_see": ["画面明亮"], "must_not_see": [], "pixel": {}}}
    monkeypatch.setattr(CT, "load", lambda _: contract)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"style": "C99@r1", "content": {"subject": "一只橘猫", "relations": "远处有早餐摊"}}), encoding="utf-8")
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"must_see": [{"item": "画面明亮", "ok": True, "why": "可见"}],
                                  "must_not_see": [], "transcribed_text": []}), encoding="utf-8")
    args = ["qa", str(image), "--style", "C99", "--manifest", str(manifest), "--review-json", str(review)]
    assert sb.main(args) == 1
    assert "缺少内容事实核对结论" in capsys.readouterr().out
    data = json.loads(review.read_text(encoding="utf-8"))
    data["content_match"] = {"ok": False, "why": "多出第二只猫"}
    review.write_text(json.dumps(data), encoding="utf-8")
    assert sb.main(args) == 1
    assert "多出第二只猫" in capsys.readouterr().out
    data["content_match"] = {"ok": True, "why": "主体与远近正确"}
    review.write_text(json.dumps(data), encoding="utf-8")
    assert sb.main(args) == 0
    plan = json.loads(manifest.read_text(encoding="utf-8"))
    plan["text"] = {"mode": "native", "items": [{"text": "正确标题"}]}
    manifest.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    data["transcribed_text"] = ["错误标题"]
    review.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert sb.main(args) == 1
    assert "正确标题" in capsys.readouterr().out
    plan["style"] = "C98@r1"
    manifest.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert sb.main(args) == 2
    assert "样式须为 C99@r1" in capsys.readouterr().err
    plan["style"] = "C99@r1"
    plan["content"]["subject"] = "  "
    manifest.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert sb.main(args) == 2
    assert "缺少 content.subject" in capsys.readouterr().err


def test_single_qa_uses_candidate_contract_instead_of_official(tmp_path, capsys):
    from PIL import Image
    image = tmp_path / "candidate.png"
    Image.new("RGB", (64, 64), (240, 240, 235)).save(image)
    contract = json.loads((ROOT / "styles" / "C58" / "contract.json").read_text(encoding="utf-8"))
    contract["revision"] += 1
    contract["changelog"].append({"revision": contract["revision"], "date": "2026-09-28", "note": "测试候选合同"})
    forbidden = "dense continuous knitted clothing"
    contract["qa"]["must_not_see"].append(forbidden)
    candidate = tmp_path / "contract.json"
    candidate.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"style": f"C58@r{contract['revision']}", "content": {"subject": "一位人物穿毛衣"}}), encoding="utf-8")
    review = tmp_path / "review.json"
    review.write_text(json.dumps({
        "must_see": [{"item": x, "ok": True} for x in contract["qa"]["must_see"]],
        "must_not_see": [{"item": x, "present": x == forbidden} for x in contract["qa"]["must_not_see"]],
        "content_match": {"ok": True, "why": "人物与服装可见"}, "transcribed_text": []
    }), encoding="utf-8")
    args = ["qa", str(image), "--style", "C58", "--contract", str(candidate), "--manifest", str(manifest), "--review-json", str(review)]
    assert sb.main(args) == 1
    assert forbidden in capsys.readouterr().out
    assert sb.main([*args[:3], "C99", *args[4:]]) == 2
    assert "验收合同风格码须为 C99" in capsys.readouterr().err


def test_single_qa_square_requires_real_thumbnail_verdict(tmp_path, capsys, monkeypatch):
    from PIL import Image
    from stylebook import contract as CT
    image = tmp_path / "square.png"
    Image.new("RGB", (900, 900), (240, 240, 235)).save(image)
    monkeypatch.setattr(CT, "load", lambda _: {"code": "C99", "revision": 1,
                                                  "qa": {"must_see": [], "must_not_see": [], "pixel": {}}})
    review = tmp_path / "review.json"
    data = {"must_see": [], "must_not_see": [], "transcribed_text": []}
    review.write_text(json.dumps(data), encoding="utf-8")
    args = ["qa", str(image), "--style", "C99", "--format", "wechat-cover-square", "--review-json", str(review)]
    assert sb.main(args) == 1
    assert "缺少缩略图可辨认结论" in capsys.readouterr().out
    thumb = tmp_path / "square.thumb-46.png"
    with Image.open(thumb) as im:
        assert im.size == (46, 46)
    data["thumbnail_readable"] = {"ok": False, "why": "主体缩成色点"}
    review.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert sb.main(args) == 1
    assert "主体缩成色点" in capsys.readouterr().out
    data["thumbnail_readable"] = {"ok": True, "why": "主体仍可辨认"}
    review.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert sb.main(args) == 0


def test_pptx_editable_text(tmp_path):
    import shutil
    import zipfile
    import pytest
    from PIL import Image
    try:
        import pptx  # noqa: F401
    except ImportError:
        if not shutil.which("uv"):
            pytest.skip("没有 python-pptx，也没有 uv")
    (tmp_path / "img").mkdir()
    items = [{"id": "01", "what": "第一页", "why": "依据", "points": ["要点一"], "text": {"mode": "overlay", "items": [{"role": "title", "text": "标题一"}]}}]
    (tmp_path / "plan.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    Image.new("RGB", (1920, 1080), (240, 236, 228)).save(tmp_path / "img" / "01.png")
    assert sb.main(["pptx", str(tmp_path / "plan.json"), "--images", str(tmp_path / "img"), "-o", str(tmp_path / "d.pptx")]) == 0
    xml = zipfile.ZipFile(tmp_path / "d.pptx").read("ppt/slides/slide1.xml").decode("utf-8")
    assert "标题一" in xml and "要点一" in xml


def test_motion_commands_do_not_run_automatic_dependency_installer(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("动态入口不得自动安装依赖")
    monkeypatch.setattr(sb.FR, "ensure_dependencies", forbidden)
    monkeypatch.setattr(sb, "cmd_animate", lambda args: 0)
    monkeypatch.setattr(sb, "cmd_layers", lambda args: 0)
    assert sb.main(["animate", "doctor"]) == 0
    assert sb.main(["layers", "brief.json", "-o", "new-output", "--prepare"]) == 0
