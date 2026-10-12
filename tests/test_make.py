import copy
import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import make as MK, profile as P  # noqa: E402
from stylebook.backends.base import Result  # noqa: E402

BRIEF = {"version": 1, "scene": "wxillus", "style": "C42", "reason": "清楚表现文章中的选择",
         "items": [{"position": "段落之后", "message": "两个方案有不同用途", "visual": "Two cards on a table", "text": []}]}


@pytest.fixture(autouse=True)
def private_preferences_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_PREFERENCES", str(tmp_path / "prefs.json"))
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "absent-profile"))
    monkeypatch.setattr(P, "lookup", lambda *args, **kwargs: None)


def fake_generate(prompt, out, *, size, **kwargs):
    Image.new("RGB", size, "ivory").save(out)
    return Result(b"", "test", "test-image", 1, 0.1, None)


def test_prepare_exposes_exact_execution_card_without_changing_prepared(tmp_path):
    brief = copy.deepcopy(BRIEF)
    prepared = MK.prepare(brief, base_path=tmp_path)
    report = MK.run(brief, tmp_path / "out", base_path=tmp_path, prepare_only=True)
    assert report["status"] == "pending_host" and report["accepted"] is False
    saved = json.loads((tmp_path / "out/prepared.json").read_text())
    assert saved == prepared
    task = prepared["tasks"][0]
    card = json.loads((tmp_path / "out/01-execution-card.json").read_text())
    assert card["tool_arguments"]["prompt"] == task["compiled"]["prompt"]
    assert card["prompt_sha256"] == task["prompt_sha256"]
    assert card["text"] == task["manifest"]["text"]
    assert card["references"] == task["references"]
    assert card["requirements"]["must_see"] and card["accepted"] is False


def test_required_points_and_comic_panels_survive_make_to_qa(tmp_path):
    brief = copy.deepcopy(BRIEF)
    brief["items"][0].update(points=["只出现两张卡", "左卡在右卡之前"],
                            panels=["第一格把左卡交给朋友", "第二格朋友接过左卡"])
    task = MK.prepare(brief, base_path=tmp_path)["tasks"][0]
    card = MK.execution_card(task)
    assert "Panel 1: 第一格把左卡交给朋友" in task["compiled"]["prompt"]
    assert "只出现两张卡" in task["compiled"]["prompt"]
    from stylebook.qa import missing_items
    contract = {"qa": {"must_see": [], "must_not_see": []}, **card["content_expectations"]}
    # Actual QA consumer refuses an old total-theme-only review of these facts.
    gaps = missing_items({"must_see": [], "must_not_see": [], "transcribed_text": [],
                          "content_match": {"ok": True, "why": "总主题相符"}}, contract, "none")
    assert any("point_match" in gap for gap in gaps)
    assert any("panel_match" in gap for gap in gaps)
    brief["items"][0]["panels"] = []
    with pytest.raises(MK.BriefError, match="panels"):
        MK.prepare(brief, base_path=tmp_path)


def test_actual_comic_make_preserves_continuity_and_rejects_missing_relation(tmp_path):
    brief = copy.deepcopy(BRIEF)
    brief.update(scene="comic4", format="comic-4panel")
    item = brief["items"][0]
    item.update(panels=["同一男孩在厨房烧水", "同一男孩下入面条", "同一男孩加入青菜", "同一男孩在餐桌吃面"])
    from stylebook.compile import CompileError
    with pytest.raises(CompileError, match="relations"):
        MK.prepare(brief, base_path=tmp_path)
    item['relations'] = "The same boy, same hair and clothing across all four panels; actions follow chronological reading order."
    task = MK.prepare(brief, base_path=tmp_path)["tasks"][0]
    assert task["manifest"]["content"]["relations"] == item["relations"]
    assert "same hair and clothing" in task["compiled"]["prompt"]


@pytest.mark.parametrize("change", [lambda b: b.update(items=[]),
                                  lambda b: b["items"][0].update(id="../escape"),
                                  lambda b: b["items"][0].update(visual=""),
                                  lambda b: b.update(palette="blue"),
                                  lambda b: b.update(scene="invalid"),
                                  lambda b: b.update(unrecognized=True)])
def test_invalid_brief_rejected_before_generation_or_output(tmp_path, change):
    brief = copy.deepcopy(BRIEF)
    change(brief)
    with pytest.raises(MK.BriefError):
        MK.run(brief, tmp_path / "out", base_path=tmp_path, gen_fn=fake_generate)
    assert not (tmp_path / "out").exists()


def test_source_quote_must_exist_and_source_is_bound(tmp_path):
    source = tmp_path / "article.md"
    source.write_text("段落之后", encoding="utf-8")
    brief = {**copy.deepcopy(BRIEF), "source": source.name}
    prepared = MK.prepare(brief, base_path=tmp_path)
    assert prepared["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    brief["items"][0]["position"] = "不存在的句子"
    with pytest.raises(MK.BriefError, match="原文"):
        MK.prepare(brief, base_path=tmp_path)


def test_user_reference_resolves_relative_to_brief_and_overrides_style_anchor(tmp_path):
    reference = tmp_path / "chosen.png"
    Image.new("RGB", (32, 32), "ivory").save(reference)
    brief = {**copy.deepcopy(BRIEF), "references": [{"role": "style", "path": reference.name}]}
    task = MK.prepare(brief, base_path=tmp_path)["tasks"][0]
    assert len(task["references"]) == 1
    assert task["references"][0]["path"] == str(reference)
    assert task["references"][0]["sha256"] == hashlib.sha256(reference.read_bytes()).hexdigest()


def test_code_overrides_style_and_palette(tmp_path):
    from stylebook import stylecode
    code = stylecode.StyleCode(style="C42", palette="orig", light=0, sat=0)
    # Use the actual encoder rather than guessing the wire format.
    encoded = code.format()
    prepared = MK.prepare({**BRIEF, "style": "C31", "code": encoded}, base_path=tmp_path)
    assert prepared["selection"]["source"] == "code"
    assert prepared["tasks"][0]["manifest"]["style"].startswith("C42@r")


def test_lightweight_uses_only_confirmed_preferences(tmp_path, monkeypatch):
    calls = []
    def lookup(field, **kwargs):
        calls.append(kwargs["include_inferred"])
        return {"value": "C42", "source": "explicit_memory", "evidence": ["confirmed"]} if field == "style" else None
    monkeypatch.setattr(P, "lookup", lookup)
    brief = copy.deepcopy(BRIEF)
    brief.pop("style")
    prepared = MK.prepare(brief, base_path=tmp_path)
    assert calls and all(v is False for v in calls)
    assert prepared["selection"]["source"] == "preference"


def test_parallel_group_export_and_receipts_never_claim_visual_acceptance(tmp_path):
    brief = copy.deepcopy(BRIEF)
    brief["items"] *= 4
    barrier = threading.Barrier(4)
    def gen(*args, **kwargs):
        barrier.wait(timeout=5)
        return fake_generate(*args, **kwargs)
    report = MK.run(brief, tmp_path / "out", base_path=tmp_path, gen_fn=gen)
    assert report["status"] == "pending_visual_review" and report["failed"] == 0
    assert report["accepted"] is False and report["visual_review"] == "not_run"
    for rec in report["items"]:
        assert rec["pixel_check"]["passed"]
        assert rec["final_sha256"] == hashlib.sha256(Path(rec["final"]).read_bytes()).hexdigest()
        assert not rec["accepted"]
    assert Path(report["overview"]).is_file()


def test_single_generation_failure_preserves_other_results(tmp_path):
    brief = copy.deepcopy(BRIEF)
    brief["items"] = [{**brief["items"][0], "id": "broken"}, {**brief["items"][0], "id": "good"}]
    def gen(prompt, out, **kwargs):
        if out.name.startswith("broken"):
            raise RuntimeError("provider unavailable")
        return fake_generate(prompt, out, **kwargs)
    report = MK.run(brief, tmp_path / "out", base_path=tmp_path, gen_fn=gen)
    assert report["status"] == "failed" and report["failed"] == 1
    assert report["items"][1]["status"] == "pending_visual_review"
    assert (tmp_path / "out" / "broken-receipt.json").is_file()


def test_existing_output_never_overwritten(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    sentinel = out / "prior.json"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        MK.run(BRIEF, out, base_path=tmp_path, gen_fn=fake_generate)
    assert sentinel.read_text() == "keep"


def test_source_mutation_during_generation_invalidates_group(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("段落之后", encoding="utf-8")
    brief = {**copy.deepcopy(BRIEF), "source": source.name}
    def gen(*args, **kwargs):
        source.write_text("changed", encoding="utf-8")
        return fake_generate(*args, **kwargs)
    report = MK.run(brief, tmp_path / "out", base_path=tmp_path, gen_fn=gen)
    assert report["status"] == "failed" and not report["source_unchanged"]


def test_reference_mutation_keeps_raw_but_rejects_candidate(tmp_path, monkeypatch):
    reference = tmp_path / "ref.png"
    Image.new("RGB", (32, 32), "white").save(reference)
    compile_real = MK.CP.compile_manifest
    def compile_with_reference(manifest):
        compiled = compile_real(manifest)
        compiled.references = [{"role": "style", "path": str(reference)}]
        return compiled
    monkeypatch.setattr(MK.CP, "compile_manifest", compile_with_reference)
    def gen(*args, **kwargs):
        result = fake_generate(*args, **kwargs)
        Image.new("RGB", (32, 32), "black").save(reference)
        return result
    report = MK.run(BRIEF, tmp_path / "out", base_path=tmp_path, gen_fn=gen)
    assert report["status"] == "failed"
    assert "参考图" in report["items"][0]["error"]["message"]
    assert (tmp_path / "out/01-raw.png").is_file()
    assert not (tmp_path / "out/01.png").exists()


def test_auth_failure_stops_queued_requests(tmp_path):
    from stylebook.backends.base import BackendError
    brief = copy.deepcopy(BRIEF)
    brief["items"] *= 5
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise BackendError("auth", "invalid", retryable=False)
    report = MK.run(brief, tmp_path / "out", base_path=tmp_path, gen_fn=fail, jobs=1)
    assert len(calls) == 1 and report["failed"] == 5


def test_cli_prepare_has_no_generation_and_binds_reference_files(tmp_path):
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(json.dumps(BRIEF))
    out = tmp_path / "out"
    run = subprocess.run([sys.executable, str(ROOT / "scripts/sb.py"), "make", str(brief_path),
                          "-o", str(out), "--prepare"], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "pending_host" and report["accepted"] is False
    assert not list(out.glob("*-raw.png"))
    prepared = json.loads((out / "prepared.json").read_text())
    for ref in prepared["tasks"][0]["references"]:
        assert ref["sha256"] == hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest()


def host_fixture(tmp_path):
    prepared_dir = tmp_path / "prepared"
    MK.run(BRIEF, prepared_dir, base_path=tmp_path, prepare_only=True)
    prepared_path = prepared_dir / "prepared.json"
    task = json.loads(prepared_path.read_text())["tasks"][0]
    actual = tmp_path / "tool.png"
    Image.new("RGB", (1536, 864), "ivory").save(actual)
    result = {"version": 1, "prepared_sha256": hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
              "images": {"01": {"path": str(actual), "sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
                                 "provider": "codex_builtin", "model": None, "est_usd": None, "seconds": 1.25,
                                 "prompt_sha256": task["prompt_sha256"],
                                 "reference_sha256s": [r["sha256"] for r in task["references"]],
                                 "tool_arguments": {"prompt": task["compiled"]["prompt"],
                                                    "referenced_image_paths": [r["path"] for r in task["compiled"]["references"]],
                                                    "transparent_background": False}}}}
    return prepared_path, actual, result


def test_host_import_keeps_original_and_does_not_invent_actual_model(tmp_path):
    prepared, actual, result = host_fixture(tmp_path)
    results = tmp_path / "results.json"
    results.write_text(json.dumps(result))
    out = tmp_path / "imported"
    report = MK.import_host(BRIEF, prepared, results, out, base_path=tmp_path)
    assert report["status"] == "pending_visual_review" and not report["accepted"]
    assert (out / "01-raw.png").read_bytes() == actual.read_bytes()
    assert report["items"][0]["generation"]["model"] is None
    assert report["items"][0]["generation"]["est_usd"] is None
    assert report["host_import"]["generation_seconds"] == 1.25


@pytest.mark.parametrize("mutate", [lambda r: r.update(images={}),
                                   lambda r: r.update(prepared_sha256="wrong"),
                                   lambda r: r["images"]["01"].update(prompt_sha256="wrong"),
                                   lambda r: r["images"]["01"].update(sha256="wrong"),
                                   lambda r: r["images"]["01"].update(reference_sha256s=["wrong"]),
                                   lambda r: r["images"]["01"]["tool_arguments"].update(prompt="different"),
                                   lambda r: r["images"]["01"].update(model="made-up-model"),
                                   lambda r: r["images"]["01"].update(seconds=float('nan'))])
def test_host_import_rejects_unbound_or_changed_inputs_before_export(tmp_path, mutate):
    prepared, actual, result = host_fixture(tmp_path)
    mutate(result)
    results = tmp_path / "bad.json"
    results.write_text(json.dumps(result))
    with pytest.raises(MK.BriefError):
        MK.import_host(BRIEF, prepared, results, tmp_path / "out", base_path=tmp_path)
    assert not (tmp_path / "out").exists()


def test_host_import_rejects_brief_changed_after_generation(tmp_path):
    prepared, actual, result = host_fixture(tmp_path)
    results = tmp_path / "results.json"
    results.write_text(json.dumps(result))
    brief = copy.deepcopy(BRIEF)
    brief["items"][0]["visual"] = "A different subject"
    with pytest.raises(MK.BriefError, match="变化"):
        MK.import_host(brief, prepared, results, tmp_path / "out", base_path=tmp_path)


def test_sb2_supplies_scene_and_compiles_brand_colors(tmp_path):
    brief = copy.deepcopy(BRIEF)
    del brief["scene"]
    brief["code"] = "sb2:wxcover/C42-hex.1F6F8B.F4F1E8"
    result = MK.prepare(brief, base_path=tmp_path)
    assert result["scene"] == "wxcover"
    assert result["tasks"][0]["manifest"]["format"] == "wechat-cover-head"
    assert result["palette"]["custom"] == ["#1F6F8B", "#F4F1E8"]
    assert "sb2:" not in result["tasks"][0]["compiled"]["prompt"]
    brief["scene"] = "wxillus"
    with pytest.raises(MK.SEL.SelectionError, match="不一致"):
        MK.prepare(brief, base_path=tmp_path)


def test_vocabulary_scene_uses_exact_overlay_and_preserves_raw(tmp_path):
    brief = copy.deepcopy(BRIEF)
    brief["scene"] = "tb-vocab"
    brief["items"][0].update(visual="Exactly one apple, on a plain light background", text=["apple"])
    prepared = MK.prepare(brief, base_path=tmp_path)
    task = prepared["tasks"][0]
    assert task["manifest"]["format"] == "textbook-vocab"
    assert task["manifest"]["text"]["mode"] == "overlay"
    assert "no text, letters or captions" in task["compiled"]["prompt"]
    assert "Chinese school uniforms" in task["compiled"]["prompt"]
    assert task["manifest"]["text"]["items"][0]["text"] == "apple"
    report = MK.run(brief, tmp_path / "out", base_path=tmp_path, gen_fn=fake_generate)
    assert report["status"] == "pending_visual_review"
    with Image.open(tmp_path / "out" / "01.png") as final:
        assert final.size == (1200, 1200)
        from PIL import ImageChops
        assert ImageChops.difference(final, Image.new("RGB", final.size, "ivory")).getbbox()
    assert not report["accepted"]


def test_grammar_scene_has_actions_time_and_cultural_constraints(tmp_path):
    brief = copy.deepcopy(BRIEF)
    brief["scene"] = "tb-grammar"
    task = MK.prepare(brief, base_path=tmp_path)["tasks"][0]
    assert task["compiled"]["aspect"] == "4:3"
    assert task["manifest"]["text"]["mode"] == "none"
    assert "visual time cues" in task["compiled"]["prompt"]
    assert "no Japanese sailor uniforms" in task["compiled"]["prompt"]


def test_overlay_bad_box_and_nonblank_generation_are_rejected(tmp_path):
    brief = copy.deepcopy(BRIEF)
    brief["scene"] = "tb-vocab"
    brief["items"][0]["text"] = {"mode": "overlay", "items": [{"text": "apple", "box": [0.9, 0, 0.3, 0.2]}]}
    with pytest.raises(MK.BriefError, match="超出画布"):
        MK.prepare(brief, base_path=tmp_path)
    brief["items"][0]["text"] = ["apple"]
    def nonblank(prompt, raw, **kwargs):
        Image.new("RGB", kwargs["size"], "black").save(raw)
        return Result(b"", "test", "fixture", 1, 0, 0)
    report = MK.run(brief, tmp_path / "out", base_path=tmp_path, gen_fn=nonblank)
    assert report["failed"] == 1 and not report["accepted"]
    assert (tmp_path / "out" / "01-raw.png").is_file()
    assert "深色物件" in report["items"][0]["error"]["message"]


def test_overlay_asset_change_is_bound_and_rejected(tmp_path):
    layer = tmp_path / "layer.png"
    Image.new("RGBA", (20, 20), "red").save(layer)
    brief = copy.deepcopy(BRIEF)
    brief["items"][0]["text"] = {"mode": "overlay", "items": [
        {"text": "label", "box": [0.1, 0.8, 0.8, 0.1]}],
        "image_layers": [{"path": "layer.png", "box": [0.1, 0.1, 0.2, 0.2]}]}
    task = MK.prepare(brief, base_path=tmp_path)["tasks"][0]
    assert task["overlay_assets"][0]["sha256"] == hashlib.sha256(layer.read_bytes()).hexdigest()
    def mutate(prompt, raw, **kwargs):
        result = fake_generate(prompt, raw, **kwargs)
        Image.new("RGBA", (20, 20), "blue").save(layer)
        return result
    report = MK.run(brief, tmp_path / "out", base_path=tmp_path, gen_fn=mutate)
    assert report["failed"] == 1
    assert "发生变化" in report["items"][0]["error"]["message"]
