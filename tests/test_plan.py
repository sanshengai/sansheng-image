import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import compile as CP  # noqa: E402
from stylebook import plan as PL  # noqa: E402


@pytest.fixture(autouse=True)
def _no_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "none"))
    monkeypatch.setattr(PL.CT.Path, "home", staticmethod(lambda: tmp_path))


def item(i, shape="steps", form="structure", structure="flow", points=3, **kw):
    it = {"id": f"{i:02d}", "position": f"第 {i} 节「这一节的最后一句话」之后", "shape": shape, "form": form, "what": "番茄钟三步",
          "subject": "the three steps of the Pomodoro technique", "why": "这一节讲的是先后三个步骤，读者需要看到顺序"}
    if form == "structure":
        it.update(structure=structure, points=[f"要点{k}" for k in range(points)])
    it.update(kw)
    return it


def plan(items, **kw):
    p = {"scene": "wxillus", "style": {"code": "C31", "source": "factory", "why": "公众号配图出厂默认"}, "items": items}
    p.update(kw)
    return p


def test_good_plan_passes_and_compiles():
    p = plan([item(1), item(2, shape="story", form="scene"), item(3, shape="options", structure="compare", points=2)])
    errs, _ = PL.check(p)
    assert errs == []
    ms = PL.manifests(p)
    assert [m.get("structure") for m in ms] == ["flow", None, "compare"]
    assert [m.get("density") for m in ms] == ["balanced", None, "sparse"]
    for m in ms:
        CP.compile_manifest(m)
    t = PL.table(p)
    assert "流程" in t and "左右对比" in t and "人物场景" in t and "出厂默认" in t


def test_required_points_reach_prompt_and_visual_review():
    from stylebook.qa import content_expectations
    p = plan([item(1)])
    p["items"][0]["points"] = ["仅在用户授权后上传", "超过 19 元才发提醒", "未授权时不上传"]
    assert PL.check(p)[0] == []
    m = PL.manifests(p)[0]
    assert m["content"]["points"] == p["items"][0]["points"]
    prompt = CP.compile_manifest(m).prompt
    expectation = content_expectations(m["content"])["_content_expectation"]
    for point in p["items"][0]["points"]:
        assert point in prompt and point in expectation


def test_story_source_facts_reach_prompt_and_review():
    from stylebook.qa import content_expectations
    fact = "手机里的缩略图是父亲发来的育儿教育视频，不是天气或风景"
    p = plan([item(1, shape="story", form="scene")])
    p["items"][0]["points"] = [fact]
    assert PL.check(p)[0] == []
    manifest = PL.manifests(p)[0]
    assert manifest["content"]["points"] == [fact]
    assert fact in CP.compile_manifest(manifest).prompt
    assert fact in content_expectations(manifest["content"])["_content_expectation"]
    p["items"][0]["points"] = [" "]
    assert any("必要事实" in error for error in PL.check(p)[0])


def test_plan_can_use_approved_series_image_instead_of_anchor(tmp_path):
    img = tmp_path / "approved-master.png"
    img.write_bytes(b"series reference")
    p = plan([item(1, references=[{"path": str(img), "role": "style"}], use_anchor=False)])
    manifest = PL.manifests(p)[0]
    compiled = CP.compile_manifest(manifest)
    assert manifest["use_anchor"] is False
    assert compiled.references == [{"path": str(img), "role": "style"}]


def test_overlay_plan_keeps_raw_generation_and_final_review_requirements_distinct():
    from stylebook.qa import content_expectations
    p = plan([item(1, subject="three blank cards without letters",
                   relations="keep headline band empty",
                   final_subject="three labelled cards showing the three source steps",
                   final_relations="headline above cards, labels inside cards")])
    m = PL.manifests(p)[0]
    prompt = CP.compile_manifest(m).prompt
    review = content_expectations(m["content"])["_content_expectation"]
    assert "three blank cards without letters" in prompt
    assert "keep headline band empty" in prompt
    assert "three labelled cards" not in prompt
    assert "three labelled cards" in review
    assert "labels inside cards" in review
    assert "keep headline band empty" not in review
    assert "without letters" not in review
    assert all(point in review for point in p["items"][0]["points"])


def test_three_real_flows_warn_without_blocking():
    p = plan([item(1), item(2), item(3)])
    errors, warnings = PL.check(p)
    assert errors == []
    assert any("连续第 3 张" in warning for warning in warnings)


def test_conditional_decision_uses_branch_layout_instead_of_linear_steps():
    p = plan([item(1, shape="decision", structure="decision-tree", points=3,
                   what="先判断电视是否支持 ADB，再分别继续或停止",
                   subject="a conditional ADB applicability decision tree",
                   why="原文是先判断适用性，再按是与否分支行动，线性流程会误导读者")])
    p["items"][0]["points"] = ["能打开开发者选项？", "是：继续核对连接方式", "否：停止，不能按本教程操作"]
    assert PL.check(p)[0] == []
    manifest = PL.manifests(p)[0]
    assert manifest["structure"] == "decision-tree"
    assert "yes and no branches" in CP.compile_manifest(manifest).prompt
    p["items"][0]["structure"] = "flow"
    assert any("条件判断" in error and "decision-tree" in error for error in PL.check(p)[0])


def test_version_two_plan_binds_scene_steps_and_comparison_to_source(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("小杨在雨中走到车站。\n仅在用户授权后上传；未授权时不上传。\n方案甲适合手机，方案乙适合大屏。", encoding="utf-8")
    entries = [
        item(1, shape="story", form="scene", source_quote="小杨在雨中走到车站"),
        item(2, source_quote="仅在用户授权后上传", points=2),
        item(3, shape="options", structure="compare", source_quote="方案甲适合手机，方案乙适合大屏", points=2),
    ]
    entries[1]["points"] = ["仅在用户授权后上传", "未授权时不上传"]
    entries[2]["points"] = ["方案甲适合手机", "方案乙适合大屏"]
    p = plan(entries, version=2, source={"path": "article.md", "sha256": hashlib.sha256(article.read_bytes()).hexdigest()})
    assert PL.check(p, base_path=tmp_path)[0] == []
    assert len([CP.compile_manifest(m) for m in PL.manifests(p)]) == 3
    p["items"][2]["source_quote"] = "原文里没有这个判断"
    assert any("找不到" in error for error in PL.check(p, base_path=tmp_path)[0])
    p["items"][2]["source_quote"] = "方案甲适合手机，方案乙适合大屏"
    article.write_text(article.read_text() + "\n新段落", encoding="utf-8")
    assert any("sha256" in error for error in PL.check(p, base_path=tmp_path)[0])


def test_version_three_article_requires_reviewable_core_coverage(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("先看设备是否支持。界面截图只显示填写字段。服务器路径还差一级。", encoding="utf-8")
    entry = item(1, source_quote="服务器路径还差一级")
    p = plan([entry], version=3, source={"path": "article.md", "sha256": hashlib.sha256(article.read_bytes()).hexdigest()})
    assert any("coverage" in error for error in PL.check(p, base_path=tmp_path)[0])
    p["coverage"] = [
        {"claim": "设备支持判断", "source_quote": "先看设备是否支持", "decision": "text_sufficient",
         "image_ids": [], "why": "原文一句话已清楚说明检查动作，额外画图没有阅读增益"},
        {"claim": "服务器路径层级", "source_quote": "服务器路径还差一级", "decision": "image",
         "image_ids": ["01"], "why": "截图只显示字段值，层级关系需要新图说明"},
    ]
    assert PL.check(p, base_path=tmp_path)[0] == []
    assert PL.manifests(p)[0]["source"] == {"sha256": p["source"]["sha256"], "quote": entry["source_quote"]}
    p["coverage"][1]["image_ids"] = ["99"]
    assert any("不存在的图片 id" in error for error in PL.check(p, base_path=tmp_path)[0])
    p["coverage"][1]["image_ids"] = ["01"]
    p["coverage"][1]["source_quote"] = "原文里没有的结论"
    assert any("coverage[2] source_quote 在原文中找不到" in error for error in PL.check(p, base_path=tmp_path)[0])


def test_version_three_article_can_explicitly_choose_no_new_images(tmp_path):
    from stylebook import batch as BT
    from stylebook.qa import plan_review as PR
    article = tmp_path / "article.md"
    article.write_text("现有截图已经说明界面。正文也清楚说明下一步。", encoding="utf-8")
    p = plan([], version=3, source={"path": "article.md", "sha256": hashlib.sha256(article.read_bytes()).hexdigest()},
             coverage=[{"claim": "界面", "source_quote": "现有截图已经说明界面", "decision": "existing_visual",
                        "image_ids": [], "why": "现有截图准确展示了整段描述的界面，重新画只会重复"},
                       {"claim": "下一步", "source_quote": "正文也清楚说明下一步", "decision": "text_sufficient",
                        "image_ids": [], "why": "这句话只有一个明确动作，文字已经足够，不需要配图"}])
    assert PL.check(p, base_path=tmp_path)[0] == []
    assert PL.manifests(p) == []
    assert "无需新增配图" in PL.table(p)
    clean = PR.summarize([("article", {"items": [], "missed_positions": []})], allow_no_images=True)
    assert clean["qualified"] and clean["no_new_images"]
    assert not PR.summarize([("article", {"items": [], "missed_positions": ["遗漏一个必配位置"]})], allow_no_images=True)["qualified"]
    with pytest.raises(ValueError, match="无需运行 batch"):
        BT.run(p, tmp_path / "out", base_path=tmp_path)
    p["coverage"] = []
    assert any("coverage" in error for error in PL.check(p, base_path=tmp_path)[0])
    p["coverage"] = [{"claim": "界面", "source_quote": "现有截图已经说明界面", "decision": "existing_visual",
                      "image_ids": [], "why": "现有截图准确展示了整段描述的界面，重新画只会重复"}]
    p.pop("items")
    assert any("items 须明确" in error for error in PL.check(p, base_path=tmp_path)[0])


def test_unknown_plan_version_and_missing_source_fail_closed(tmp_path):
    p = plan([item(1)], version=9)
    assert any("不支持" in error for error in PL.check(p)[0])
    p["version"] = 2
    assert any("source" in error for error in PL.check(p, base_path=tmp_path)[0])


def test_plan_rejects_stale_group_and_local_style_revision():
    p = plan([item(1)])
    p["style"]["code"] = "C31@r999"
    assert any("样式修订" in e for e in PL.check(p)[0])
    p["style"]["code"] = "C31"
    p["items"][0]["manual"] = True
    p["items"][0]["style"] = "C31@r999"
    assert any("样式修订" in e for e in PL.check(p)[0])


@pytest.mark.parametrize("bad, expect", [
    (lambda p: p["items"][0].__setitem__("points", ["第一步", " "]), "结构图要列出要点"),
    (lambda p: p["items"][0].__setitem__("structure", "pyramid"), "可用的结构"),
    (lambda p: p["items"][0].__setitem__("form", "scene"), "应表达为"),
    (lambda p: p["items"][0].__setitem__("points", ["a"] * 6), "超出"),
    (lambda p: p["items"][0].__setitem__("density", "dense"), "对应密度"),
    (lambda p: p["items"][0].__setitem__("why", "好"), "依据句太短"),
    (lambda p: p["items"][0].pop("what"), "缺画什么"),
    (lambda p: p["items"][0].__setitem__("position", "第一节段首"), "引用原文的一句话"),
    (lambda p: p["items"][0].__setitem__("style", "C24"), "只用一个样式"),
    (lambda p: p["style"].__setitem__("code", "C23"), "还没有风格合同"),
    (lambda p: p["style"].__setitem__("why", ""), "理由"),
    (lambda p: p["items"][1].__setitem__("structure", "flow"), "不写结构"),
    (lambda p: p["items"][0].__setitem__("text", {"mode": "native", "items": [{"role": "title", "text": "这是一个非常非常长的配图标题文字"}]}), "超过该格式上限"),
])
def test_rules_reject(bad, expect):
    p = plan([item(1), item(2, shape="story", form="scene"), item(3, shape="options", structure="compare", points=2)])
    bad(p)
    errs, _ = PL.check(p)
    assert any(expect in e for e in errs), errs


def test_three_in_a_row_only_counts_consecutive():
    p = plan([item(1), item(2), item(3, shape="story", form="scene"), item(4), item(5)])
    assert PL.check(p)[0] == []


def test_manual_item_may_override_style():
    p = plan([item(1), item(2, shape="story", form="scene", style="C24", manual=True)])
    assert PL.check(p)[0] == []
    assert PL.manifests(p)[1]["style"] == "C24"


def test_plan_preserves_inventory_in_compiled_prompt():
    inventory = "Exactly one fox and one teacup; no other characters or lettering."
    p = plan([item(1, shape="story", form="scene", inventory=inventory)])
    assert PL.check(p)[0] == []
    manifest = PL.manifests(p)[0]
    assert manifest["content"]["inventory"] == inventory
    assert "Content inventory (literal): " + inventory in CP.compile_manifest(manifest).prompt


@pytest.mark.parametrize("inventory", ["", "  ", None, []])
def test_plan_rejects_empty_or_nontext_inventory(inventory):
    p = plan([item(1, shape="story", form="scene", inventory=inventory)])
    assert any("inventory 必须是非空文字" in e for e in PL.check(p)[0])


def test_hybrid_plan_rejects_missing_or_overlapping_precise_text():
    mixed = {"mode": "hybrid", "reserve": "two shallow insets below the title", "items": [
        {"render": "native", "role": "title", "text": "第一步：安装", "position": "top"},
        {"render": "overlay", "role": "code", "text": "uv tool install notebooklm-mcp-cli",
         "box": [0.07, 0.35, 0.86, 0.08], "font_px": 55, "min_px": 55},
    ]}
    p = plan([item(1, text=mixed)])
    assert PL.check(p)[0] == []
    p["items"][0]["text"]["items"].append({"render": "overlay", "role": "instruction", "text": "重启后输入 /mcp",
                                            "box": [0.5, 0.37, 0.4, 0.08]})
    assert any("重叠" in e for e in PL.check(p)[0])
    p["items"][0]["text"]["reserve"] = ""
    assert any("reserve" in e for e in PL.check(p)[0])


def test_style_priority(tmp_path):
    assert PL.resolve_style("wxillus", explicit="C24") == {"code": "C24", "source": "explicit"}
    lock = tmp_path / "project.json"
    lock.write_text(json.dumps({"style": "C31@r1"}), encoding="utf-8")
    assert PL.resolve_style("wxillus", project=lock)["source"] == "project"
    r = PL.resolve_style("wxillus")
    assert r["source"] == "factory" and r["code"] == PL.D.scenes()["wxillus"]["default"]


def test_plan_review_summary_and_prompt(tmp_path):
    from stylebook.qa import plan_review as PR
    p = plan([item(1), item(2, shape="story", form="scene")])
    text = PR.prompt(p, tmp_path / "a.md")
    assert "no_literal_metaphor_or_real_face" in text and "fidelity_ok" in text and "reader_value_ok" in text
    assert "只属可选美化的建议放 notes" in text
    assert "番茄钟三步" in text and '"subject"' in text and '"text"' in text and str(tmp_path / "a.md") in text
    assert '"source_quote"' in text and '"inventory"' in text and '"relations"' in text
    assert "画面必需的题材线索不能被换成无关物件" in text
    assert "核心论点、关键机制、结论／行动建议和最具体操作" in text
    assert "现有表格／截图／清单为什么仍不足" in text
    assert "不要直接采信 text_sufficient 或 existing_visual" in text
    passed = {key: True for key in ("reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
                                 "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok")}
    r1 = {"items": [{"id": "01", **passed, "why": ""}, {"id": "02", **passed, "reasonable": False, "why": "位置是装饰"}],
          "missed_positions": ["§3"]}
    r2 = {"items": [{"id": "01", **passed, "why": ""}], "missed_positions": []}
    s = PR.summarize([("A", r1), ("B", r2)])
    assert (s["total"], s["reasonable"], s["rate"]) == (3, 2, 0.667)
    assert (s["required_missed"], s["effective_rate"], s["qualified"]) == (1, 0.5, False)
    assert s["failed"] == [{"article": "A", "id": "02", "why": "位置是装饰"}] and s["missed"] == {"A": ["§3"], "B": []}


def test_plan_review_counts_unillustrated_core_mechanism_as_missed():
    from stylebook.qa import plan_review as PR
    passed = {key: True for key in ("reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
                                 "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok")}
    result = {"items": [{"id": "01", **passed, "why": "协议桥接图有效"}],
              "missed_positions": ["原文说明 QQ 邮箱的日历主页路径差一级；现有截图只显示字段值，缺少路径层级关系图"]}
    summary = PR.summarize([("教程", result)])
    assert summary["reasonable"] == 1 and summary["required_missed"] == 1
    assert summary["effective_rate"] == 0.5 and not summary["qualified"]


def test_plan_review_rejects_positive_overall_with_false_or_missing_content_gate():
    from stylebook.qa import plan_review as PR
    passed = {key: True for key in ("reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
                                 "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok")}
    false_claim = {"id": "01", **passed, "fidelity_ok": False, "why": "把有条件的节省写成零 token"}
    missing_detail = {"id": "02", **passed, "reader_value_ok": False, "why": "只剩五个泛化名词"}
    missing_gate = {"id": "03", **{k: v for k, v in passed.items() if k != "fidelity_ok"}, "why": "缺少忠实度结论"}
    summary = PR.summarize([("tutorial", {"items": [false_claim, missing_detail, missing_gate], "missed_positions": []})])
    assert summary["reasonable"] == 0
    assert [item["id"] for item in summary["failed"]] == ["01", "02", "03"]


def test_plan_review_cli_rejects_plan_when_fidelity_fails(tmp_path, monkeypatch, capsys):
    from argparse import Namespace
    import sb
    from stylebook.qa import plan_review as PR

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan([item(1)])), encoding="utf-8")
    article = tmp_path / "article.md"
    article.write_text("接入后可减少部分重复阅读，但仍需要核对原文。", encoding="utf-8")
    verdict = {key: True for key in ("reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
                                    "no_literal_metaphor_or_real_face", "reader_value_ok")}
    verdict.update(id="01", fidelity_ok=False, why="计划把有条件的节省写成零 token")
    monkeypatch.setattr(PR, "review", lambda *_args, **_kwargs: {"items": [verdict], "missed_positions": []})
    assert sb.cmd_plan_review(Namespace(plan=str(plan_path), article=str(article), model=None, report=None)) == 1
    assert '"reasonable": 0' in capsys.readouterr().out


def test_plan_review_cli_counts_required_omissions(tmp_path, monkeypatch, capsys):
    from argparse import Namespace
    import sb
    from stylebook.qa import plan_review as PR

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan([item(i) for i in range(1, 9)])), encoding="utf-8")
    article = tmp_path / "article.md"
    article.write_text("最后请按三个步骤操作。", encoding="utf-8")
    passed = {key: True for key in ("reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
                                  "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok")}
    verdict = {"items": [{"id": f"{i:02d}", **passed} for i in range(1, 9)],
               "missed_positions": ["结尾「最后请按三个步骤操作」之后：具体操作过程未配图"]}
    monkeypatch.setattr(PR, "review", lambda *_args, **_kwargs: verdict)
    assert sb.cmd_plan_review(Namespace(plan=str(plan_path), article=str(article), model=None, report=None)) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["rate"] == 1.0 and output["effective_rate"] == 0.889
    assert output["required_missed"] == 1 and output["qualified"] is False


def test_plan_review_missing_coverage_verdict_fails_closed():
    from stylebook.qa import plan_review as PR

    with pytest.raises(ValueError, match="missed_positions"):
        PR.summarize([("article", {"items": []})])
    empty = PR.summarize([("article", {"items": [], "missed_positions": []})])
    assert empty["rate"] == 0 and empty["effective_rate"] == 0 and empty["qualified"] is False


def test_plan_review_threshold_uses_exact_counts_before_display_rounding():
    from stylebook.qa import plan_review as PR

    passed = {"id": "01", **{key: True for key in ("reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
                                         "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok")}}
    failed = {**passed, "reasonable": False}
    summary = PR.summarize([("article", {"items": [passed] * 4498 + [failed] * 502,
                                          "missed_positions": []})])
    assert summary["effective_rate"] == 0.9
    assert summary["qualified"] is False


@pytest.mark.parametrize("response", [
    {"items": [{"id": "01"}]},
    {"items": [{"id": "01"}, {"id": "99"}], "missed_positions": []},
])
def test_plan_review_rejects_missing_coverage_or_extra_item(tmp_path, monkeypatch, response):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    from types import SimpleNamespace
    from stylebook.qa import plan_review as PR

    article = tmp_path / "article.md"
    article.write_text("原文", encoding="utf-8")
    monkeypatch.setattr(PR, "_claude_bin", lambda: "/fake/claude")
    monkeypatch.setattr(PR, "_extract", lambda _output: response)
    monkeypatch.setattr(PR.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="{}", stderr=""))
    with pytest.raises(RuntimeError, match="没有给出完整结论"):
        PR.review(plan([item(1)]), article)


def test_plan_review_ark_includes_article_and_rejects_missing_verdict(tmp_path, monkeypatch):
    from stylebook.qa import plan_review as PR
    from stylebook.qa import reviewer as RV

    article = tmp_path / "article.md"
    article.write_text("仅在负责人确认后发送。未确认就不发送。", encoding="utf-8")
    monkeypatch.setenv("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND", "ark_agent_plan")
    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", RV.ARK_PLAN_URL)
    monkeypatch.setenv("ARK_AGENT_PLAN_API_KEY", "test-key")
    captured = []
    class Response:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps(self.payload).encode()
    verdict = {"items": [{"id": "01", "reasonable": True, "position_ok": True,
                          "shape_ok": True, "form_ok": True, "basis_ok": True,
                          "no_literal_metaphor_or_real_face": True, "fidelity_ok": False,
                          "reader_value_ok": True, "why": "遗漏未确认不得发送"}],
               "missed_positions": [], "notes": ""}
    def fake_open(request, timeout):
        body = json.loads(request.data)
        captured.append(body)
        return Response({"status": "completed", "model": RV.ARK_MODEL,
                         "output": [{"content": [{"type": "output_text", "text": json.dumps(verdict)}]}]})
    monkeypatch.setattr(PR.urllib.request, "urlopen", fake_open)
    result = PR.review(plan([item(1)]), article)
    assert result["items"][0]["reasonable"] is False
    assert result["_reviewer"].startswith("ark_agent_plan:")
    assert captured[0]["reasoning"] == {"effort": "low"}
    assert "未确认就不发送" in captured[0]["input"][0]["content"][0]["text"]
    verdict["missed_positions"] = [""]
    with pytest.raises(RuntimeError, match="没有给出完整结论"):
        PR.review(plan([item(1)]), article)


def test_plan_review_ark_requires_bundled_endpoint(tmp_path, monkeypatch):
    from stylebook.qa import plan_review as PR
    from stylebook.qa.reviewer import ReviewerAuthUnavailable
    article = tmp_path / "article.md"
    article.write_text("原文", encoding="utf-8")
    monkeypatch.setenv("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND", "ark_agent_plan")
    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    monkeypatch.setenv("ARK_AGENT_PLAN_API_KEY", "test-key")
    with pytest.raises(ReviewerAuthUnavailable):
        PR.review(plan([item(1)]), article)


def test_rules_and_shape_defs_shared_with_docs_and_reviewer(tmp_path):
    from stylebook.qa import plan_review as PR
    doc = (ROOT / "references" / "planning.md").read_text(encoding="utf-8")
    text = PR.prompt(plan([item(1)]), tmp_path / "a.md")
    for r in PL.PLANNING_RULES:
        assert r in doc and r in text
    for k, v in PL.SHAPES.items():
        assert v["def"] in text and f"`{k}`" in doc


def test_story_formats_pass_characters_panels_and_refs(tmp_path):
    it = item(1, shape="story", form="scene", position="第 1 格", format="comic-4panel",
              characters=[{"id": "xiaoyu", "desc": "a girl with two round hair buns"}],
              panels=["She reads.", "She spills flour.", "The cake falls.", "She laughs."],
              relations="The girl stays the same and eats a slice of the collapsed cake from panel 3.",
              references=[{"path": str(tmp_path / "sheet.png"), "role": "identity"}])
    p = {"scene": "comic4", "style": {"code": "C58", "source": "factory", "why": "四格漫画出厂默认"}, "items": [it]}
    assert PL.check(p)[0] == []  # 非文章配图的位置不要求引用原文
    m = PL.manifests(p)[0]
    assert m["content"]["panels"][3] == "She laughs." and m["content"]["characters"][0]["id"] == "xiaoyu"
    assert m["references"][0]["role"] == "identity"
    c = CP.compile_manifest({k: v for k, v in m.items() if k != "_id"})
    assert "Panel 4: She laughs." in c.prompt and "2×2 grid" in c.prompt and "character identity reference" in c.prompt
