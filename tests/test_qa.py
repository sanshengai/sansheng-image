import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook.qa import active_items, compare_text, content_expectations, judge, missing_items, review_prompt, review_schema  # noqa: E402
from stylebook import contract as CT  # noqa: E402
import stylebook.qa as QA  # noqa: E402

K = {"code": "C99", "qa": {"must_see": ["画面明亮", "主体居中", "标题是黏土字"], "must_not_see": ["黑色背景", "文字在底板里"],
                           "when_text": ["标题是黏土字", "文字在底板里"], "pixel": {"mean_luma_min": 150}}}


def _img(tmp_path, color, name="x.png"):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (64, 64), color).save(p)
    return p


def _rv(ms=True, bad=False, text=()):
    return {"must_see": [{"item": i, "ok": ms, "why": "看到"} for i in K["qa"]["must_see"]],
            "must_not_see": [{"item": i, "present": bad, "why": "无"} for i in K["qa"]["must_not_see"]],
            "transcribed_text": list(text)}


def test_edit_goal_and_advice_do_not_add_or_waive_hard_gates(tmp_path):
    image = _img(tmp_path, (240, 240, 235))
    review = _rv()
    review["edit_goals"] = [{"goal": "微小金点改为青色", "completed": False, "why": "仍是微小金点"}]
    review["suggestions"] = ["人物接近上限但未超限，可以缩小一点"]
    verdict = judge(image, K, review)
    assert verdict.passed and verdict.triage["next_action"] == "accept"
    assert verdict.triage["edit_goals"][0]["completed"] is False
    # Mutation along the actual judge path: advice/overall cannot waive a true ban.
    review["overall"] = "pass"
    review["must_not_see"][0]["present"] = True
    verdict = judge(image, K, review)
    assert not verdict.passed
    assert verdict.triage["next_action"] == "diagnose_before_repair"
    assert verdict.triage["automatic_regeneration"] is False


@pytest.mark.parametrize("ok", [True, False])
def test_unverified_is_pending_evidence_not_accepted_or_automatic_regeneration(tmp_path, ok):
    review = _rv()
    review["must_see"][0].update(ok=ok, observation="unverified", why="原尺寸局部才能确认")
    verdict = judge(_img(tmp_path, (240, 240, 235)), K, review)
    assert not verdict.passed
    assert verdict.triage["next_action"] == "collect_evidence"
    assert verdict.triage["pending_evidence"]
    assert verdict.triage["automatic_regeneration"] is False
    # Only confirmed evidence can make the same answer pass.
    review["must_see"][0].update(ok=True, observation="confirmed", why="原尺寸明确可见")
    assert judge(_img(tmp_path, (240, 240, 235)), K, review).passed


@pytest.mark.parametrize("review", [None, {}, []])
def test_empty_review_is_process_error_not_image_defect(tmp_path, review):
    verdict = judge(_img(tmp_path, (240, 240, 235)), K, review)
    assert not verdict.passed
    assert verdict.triage["next_action"] == "complete_review"
    assert verdict.triage["process_errors"]


def test_invalid_observation_cannot_silently_pass(tmp_path):
    review = _rv()
    review["must_see"][0]["observation"] = "probably"
    assert not judge(_img(tmp_path, (240, 240, 235)), K, review).passed
    assert "observation" in " ".join(missing_items(review, K, "none"))


def test_overlay_layout_gate_rejects_small_title_multiline_subtitle_and_empty_manifest(tmp_path, monkeypatch):
    image = _img(tmp_path, (240, 240, 235))
    contract = {**K, "qa": {**K["qa"], "overlay_layout": {
        "headline_min_font_px_ratio": 0.12, "subtitle_max_lines": 1}}}
    bad = {"text": {"mode": "overlay", "items": [
        {"role": "title", "text": "主标题", "font_px": 6},
        {"role": "subtitle", "text": "副标题\n第二行", "font_px": 4},
    ]}}
    review = _rv(text=["主标题", "副标题\n第二行"])
    verdict = judge(image, contract, review, ["主标题", "副标题\n第二行"], "overlay", manifest=bad)
    assert not verdict.passed
    assert any("主标题字号上限不足" in p for p in verdict.problems)
    assert any("副标题 2 行" in p for p in verdict.problems)
    assert any("缺少清单" in p for p in judge(image, contract, review, [], "overlay").problems)
    good = {"text": {"mode": "overlay", "items": [
        {"role": "title", "text": "主标题", "font_px": 12},
        {"role": "subtitle", "text": "副标题", "font_px": 4},
    ]}}
    assert judge(image, contract, _rv(text=["主标题", "副标题"]), ["主标题", "副标题"],
                 "overlay", manifest=good).passed
    monkeypatch.setattr(QA, "overlay_layout_problems", lambda *_: [])
    assert judge(image, contract, review, ["主标题", "副标题\n第二行"],
                 "overlay", manifest=bad).passed  # 变异：移除本闸门，反例会漏过


def test_compare_text():
    assert compare_text(["番茄工作法", "专注25分钟"], ["番茄工作法", "专注 25 分钟"]) == []
    assert any("缺字或错字" in p for p in compare_text(["番茄工作法"], ["番茄工作去"]))
    assert any("多写了文字" in p for p in compare_text(["番茄工作法"], ["番茄工作法", "每天进步一点点"]))
    assert any("PDF" in p for p in compare_text(["读长资料"], ["读长资料", "PDF"]))
    assert any("NotebookLM" in p for p in compare_text(["NotebookLM 读长资料"], ["NotebookLM 读长资料", "NotebookLM"]))


def test_final_relations_override_raw_overlay_instructions_without_dropping_points():
    content = {"subject": "两种账本", "relations": "底部留白，禁止文字",
               "final_relations": "左右两栏对比，底部有总结文字",
               "points": ["乘客不拥有车辆"]}
    actual = content_expectations(content)
    assert "左右两栏对比" in actual["_content_expectation"]
    assert "底部留白" not in actual["_content_expectation"]
    assert actual["_content_point_expectations"] == ["乘客不拥有车辆"]
    assert "底部留白" in content["relations"]  # 生图阶段仍使用原指令


def test_panel_content_gate_requires_each_panel_and_rejects_false_or_missing(tmp_path):
    assert content_expectations({}) == {}
    content = {"subject": "A sauce travels", "panels": ["Fujian jars", "Malacca harbour", "European cooks", "tomato ketchup"]}
    contract = {**K, **content_expectations(content)}
    prompt = review_prompt(contract, [], "none")
    assert "第 2 格：Malacca harbour" in prompt
    assert "明显相反的线索须拒绝" in prompt
    assert "panel_match" in review_schema(contract)["required"]
    review = _rv()
    review["content_match"] = {"ok": True, "why": "总主题符合"}
    image = _img(tmp_path, (240, 240, 235))
    assert any("panel_match" in item for item in missing_items(review, contract, "none"))
    assert any("缺少逐格" in item for item in judge(image, contract, review, [], "none").problems)
    review["panel_match"] = [{"panel": i, "ok": True, "why": f"看到第 {i} 格"} for i in range(1, 5)]
    assert judge(image, contract, review, [], "none").passed
    review["panel_match"][1] = {"panel": 2, "ok": False, "why": "只看到欧式港口，无法核实马六甲"}
    verdict = judge(image, contract, review, [], "none")
    assert not verdict.passed and any("第 2 格内容不符" in p for p in verdict.problems)
    review["panel_match"][1] = {"panel": 1, "ok": True, "why": "重复编号"}
    assert any("缺少逐格" in p for p in judge(image, contract, review, [], "none").problems)


def test_pass_and_fail(tmp_path):
    light = _img(tmp_path, (240, 240, 235))
    assert judge(light, K, _rv(text=["番茄工作法"]), ["番茄工作法"], "native").passed
    assert not judge(light, K, _rv(bad=True), [], "none").passed
    assert not judge(light, K, _rv(ms=False), [], "none").passed
    dark = _img(tmp_path, (20, 20, 20), "d.png")
    v = judge(dark, K, _rv(), [], "none")
    assert not v.passed and any("偏暗" in p for p in v.problems)


@pytest.mark.parametrize("break_review", [
    lambda r: r["must_see"][0].pop("ok"),
    lambda r: r["must_not_see"][0].pop("present"),
    lambda r: r["must_not_see"][0].update({"why": ""}),
    lambda r: r.pop("transcribed_text"),
])
def test_incomplete_manual_review_cannot_pass(tmp_path, break_review):
    image = _img(tmp_path, (240, 240, 235))
    review = _rv()
    break_review(review)
    assert missing_items(review, K, "none")
    assert not judge(image, K, review, [], "none").passed


def test_manual_ban_verdict_gate_mutation_would_accept_missing_field(tmp_path, monkeypatch):
    image = _img(tmp_path, (240, 240, 235))
    review = _rv()
    review["must_not_see"][0].pop("present")
    assert not judge(image, K, review, [], "none").passed
    monkeypatch.setattr(QA, "missing_items", lambda *_: [])
    assert judge(image, K, review, [], "none").passed  # 移除缺字段闸门就会把未知误判为未出现


def test_c32_no_text_still_requires_technical_panel_hierarchy(tmp_path):
    contract = CT.load("C32@r4")
    structure = "a clear hierarchy of bounded technical panels or split sections, separated by visible aged-paper gutters"
    labels = "neat labeled explanatory or key-insight boxes when text is requested"
    no_text_prompt = review_prompt(contract, [], "none")
    assert structure in no_text_prompt
    assert labels not in no_text_prompt
    assert labels in review_prompt(contract, [], "native")
    assert "For title cards and other single-scene compositions" in contract["recipe"]["positive"]

    image = _img(tmp_path, (235, 225, 205))
    review = {
        "must_see": [{"item": item, "ok": True, "why": "可见"}
                     for item in contract["qa"]["must_see"] if item not in (structure, labels)],
        "must_not_see": [{"item": item, "present": False, "why": "未见"}
                         for item in contract["qa"]["must_not_see"]],
        "transcribed_text": [],
    }
    missing = judge(image, contract, review, [], "none")
    assert not missing.passed and any(structure in problem for problem in missing.problems)

    review["must_see"].append({"item": structure, "ok": False, "why": "主体满幅，侧图没有纸面间隔"})
    rejected = judge(image, contract, review, [], "none")
    assert not rejected.passed and any("没做到" in problem for problem in rejected.problems)

    review["must_see"][-1] = {"item": structure, "ok": True, "why": "主框、侧框与纸面间隔清楚"}
    assert judge(image, contract, review, [], "none").passed


def test_when_text_skipped_for_no_text(tmp_path):
    light = _img(tmp_path, (240, 240, 235))
    rv = _rv()
    rv["must_see"] = [c for c in rv["must_see"] if c["item"] != "标题是黏土字"]  # 无字图不问这条
    rv["must_not_see"] = [c for c in rv["must_not_see"] if c["item"] != "文字在底板里"]
    assert judge(light, K, rv, [], "none").passed
    assert not judge(light, K, rv, ["标题"], "native").passed  # 有字时这条就必须回答


def test_text_where_none_expected(tmp_path):
    light = _img(tmp_path, (240, 240, 235))
    v = judge(light, K, _rv(text=["乱码字样"]), [], "none")
    assert not v.passed and any("不该有字" in p for p in v.problems)


def test_hybrid_checks_native_title_and_command_punctuation(tmp_path):
    light = _img(tmp_path, (240, 240, 235))
    expected = ["第一步：安装", "uv tool install notebooklm-mcp-cli"]
    assert judge(light, K, _rv(text=expected), expected, "hybrid").passed
    wrong = ["第一步：安装", "uv tool install notebooklm mcp cli"]
    v = judge(light, K, _rv(text=wrong), expected, "hybrid")
    assert not v.passed and any("缺字或错字" in p for p in v.problems)


def test_hybrid_review_uses_scoped_rules_and_rejects_flat_title(tmp_path):
    k = {"code": "C99", "qa": {
        "must_see": ["high key pale clay scene", "all titles and labels are sculpted clay"],
        "must_not_see": ["flat operational labels", "dark background"],
        "mode_overrides": {"hybrid": {
            "must_see": ["high key pale clay scene", "the title is sculpted clay", "exact flat text in blank inset"],
            "must_not_see": ["flat decorative title", "dark background"],
        }},
    }}
    assert "all titles and labels are sculpted clay" in review_prompt(k, [], "native")
    assert "flat operational labels" not in review_prompt(k, [], "hybrid")
    assert active_items(k, "hybrid")[0][-1] == "exact flat text in blank inset"
    image = _img(tmp_path, (240, 240, 235))
    review = {"must_see": [{"item": item, "ok": True, "why": "可见"} for item in active_items(k, "hybrid")[0]],
              "must_not_see": [{"item": item, "present": False, "why": "未见"} for item in active_items(k, "hybrid")[1]],
              "transcribed_text": ["谁来做什么", "NotebookLM 读资料"]}
    assert judge(image, k, review, review["transcribed_text"], "hybrid").passed
    review["must_not_see"][0]["present"] = True
    assert not judge(image, k, review, review["transcribed_text"], "hybrid").passed


def test_na_overuse_flagged(tmp_path):
    light = _img(tmp_path, (240, 240, 235))
    rv = _rv()
    for c in rv["must_see"]:
        c["applicable"] = False
    v = judge(light, K, rv, [], "none")
    assert not v.passed and any("不适用的条目过多" in p for p in v.problems)


def test_missing_review(tmp_path):
    assert not judge(_img(tmp_path, (240, 240, 235)), K, None).passed


def test_center_square_gate_requires_preview_and_explicit_visual_verdict(tmp_path):
    from stylebook.qa import missing_items, review_schema
    image = _img(tmp_path, (240, 240, 235))
    square = _img(tmp_path, (240, 240, 235), "actual-square.png")
    contract = {**K, "_square_crop_expectation": str(square)}
    assert "实际读取方形图" in review_prompt(contract, [], "none")
    assert "square_crop" in review_schema(contract)["required"]
    assert any("square_crop" in x for x in missing_items(_rv(), contract, "none"))
    missing = judge(image, contract, _rv(), [], "none", "wechat-cover-head")
    assert not missing.passed and "缺少居中方形裁切看图结论" in missing.problems
    cropped = {**_rv(), "square_crop": {"ok": False, "why": "右侧徽章被截断"}}
    v = judge(image, contract, cropped, [], "none", "wechat-cover-head")
    assert not v.passed and any("右侧徽章被截断" in p for p in v.problems)
    complete = {**_rv(), "square_crop": {"ok": True, "why": "标题、主体与徽章完整且有边距"}}
    assert judge(image, contract, complete, [], "none", "wechat-cover-head").passed
    assert not judge(image, K, complete, [], "none", "wechat-cover-head").passed
    assert not judge(image, contract, {}, [], "none", "wechat-cover-head").passed


def test_square_margin_gate_checks_actual_crop_even_when_reviewer_accepts(tmp_path):
    from PIL import Image, ImageDraw
    square = tmp_path / "actual-square.png"
    image = tmp_path / "wide.png"
    Image.new("RGB", (235, 100), (14, 14, 16)).save(image)
    art = Image.new("RGB", (100, 100), (14, 14, 16))
    draw = ImageDraw.Draw(art)
    draw.rectangle((20, 20, 79, 79), fill="white")
    art.save(square)
    contract = {**K, "code": "S02", "qa": {**K["qa"], "pixel": {}, "center_square_margin_min": 0.15},
                "_square_crop_expectation": str(square)}
    review = {**_rv(), "square_crop": {"ok": True, "why": "标题、主体完整"}}
    assert judge(image, contract, review, [], "none", "wechat-cover-head").passed
    unsupported = judge(image, {**contract, "code": "C75"}, review, [], "none", "wechat-cover-head")
    assert not unsupported.passed
    assert unsupported.pixel["center_square_margin"]["status"] == "unsupported"
    draw.rectangle((5, 40, 10, 50), fill="white")
    art.save(square)
    verdict = judge(image, contract, review, [], "none", "wechat-cover-head")
    assert not verdict.passed
    assert any("实际居中方形：left" in p for p in verdict.problems)
    assert verdict.pixel["center_square_margin"]["margins"]["left"] == 0.05
    Image.new("RGB", (100, 100), (14, 14, 16)).save(square)
    assert not judge(image, contract, review, [], "none", "wechat-cover-head").passed


def test_paper_margin_gate_rejects_edge_mark_and_empty_image(tmp_path):
    from PIL import Image, ImageDraw

    image = tmp_path / "paper.png"
    paper = Image.new("RGB", (100, 100), (245, 240, 232))
    draw = ImageDraw.Draw(paper)
    draw.rectangle((20, 20, 79, 79), fill=(26, 26, 26))
    paper.save(image)
    contract = {**K, "qa": {**K["qa"], "pixel": {}, "paper_margin_min": 0.15}}
    assert judge(image, contract, _rv(), [], "none").passed
    draw.rectangle((5, 40, 10, 50), fill=(168, 216, 234))
    paper.save(image)
    verdict = judge(image, contract, _rv(), [], "none")
    assert not verdict.passed
    assert any("纸面留白：left" in p for p in verdict.problems)
    assert verdict.pixel["paper_margin"]["margins"]["left"] == 0.05
    Image.new("RGB", (100, 100), (245, 240, 232)).save(image)
    assert not judge(image, contract, _rv(), [], "none").passed


def test_paper_margin_text_mode_override_rejects_scene_inside_generic_margin(tmp_path):
    from PIL import Image, ImageDraw

    image = tmp_path / "paper.png"
    paper = Image.new("RGB", (100, 100), (245, 240, 232))
    ImageDraw.Draw(paper).rectangle((8, 20, 91, 79), fill=(26, 26, 26))
    paper.save(image)
    contract = {**K, "qa": {**K["qa"], "pixel": {}, "paper_margin_min": 0.05,
                             "paper_margin_min_by_text_mode": {"none": 0.10}}}
    scene = judge(image, contract, _rv(), [], "none")
    assert not scene.passed
    assert any("纸面留白：left" in problem for problem in scene.problems)
    assert scene.pixel["paper_margin"]["margins"]["left"] == 0.08
    card = judge(image, contract, _rv(), [], "native")
    assert not any("纸面留白：" in problem for problem in card.problems)


def test_content_gate_prompt_schema_and_missing_verdict(tmp_path):
    from stylebook.qa import missing_items, review_prompt, review_schema
    c = {**K, "_content_expectation": "狐狸遥望远处的灯，尚未抵达小屋"}
    assert "狐狸遥望远处的灯" in review_prompt(c, [], "none")
    assert "辅助技术视图" in review_prompt(c, [], "none")
    assert "只有额外元素与计划中的主体、动作、数量或空间关系冲突" in review_prompt(c, [], "none")
    assert "content_match" in review_schema(c)["required"]
    assert any("content_match" in x for x in missing_items(_rv(), c, "none"))
    v = judge(_img(tmp_path, (240, 240, 235)), c, _rv(), [], "none")
    assert not v.passed and "缺少内容事实核对结论" in v.problems


def test_review_prompt_distinguishes_same_subject_views_and_anatomical_left():
    contract = {**K, "_content_expectation": "one woman with a star clip on her left side; no second person"}
    prompt = review_prompt(contract, [], "none")
    assert "同一主体的正面、侧面、背面、表情或局部辅助视图不另算一个人" in prompt
    assert "不同身份的额外人物仍算第二人" in prompt
    assert "正面朝向观者的人物左侧在其脸部中线的观者右边" in prompt
    assert "不能拿整张画布的中线判断" in prompt
    assert "无法核实" in prompt


def test_ghost_review_guidance_uses_cap_height_and_visual_weight(tmp_path):
    c = {**K, "qa": {**K["qa"], "must_see": [
        "one subdued uppercase English ghost line behind the headline, white at roughly 8%-14% opacity, no larger than the headline, over a low-contrast abstract background"]}}
    prompt = review_prompt(c, [], "none")
    assert "no larger than the headline 指字高" in prompt
    assert "可以比中文标题行更宽" in prompt
    assert "缩略图第一眼落在英文上" in prompt
    assert "合成 PNG 无法唯一反推" in prompt
    assert "不强制与中文笔画交叠" in prompt
    assert "验收实际字高与视觉权重" in prompt
    assert "英文 ghost 层的验收口径" not in review_prompt(K, [], "none")
    oversized = {"must_see": [{"item": c["qa"]["must_see"][0], "ok": False,
                                "why": "英文 ghost 字高超过中文主标题"}],
                 "must_not_see": _rv()["must_not_see"], "transcribed_text": []}
    assert not judge(_img(tmp_path, (240, 240, 235)), c, oversized, [], "none").passed


def test_supplied_content_failure_is_rejected_without_manifest(tmp_path):
    rv = _rv()
    rv["content_match"] = {"ok": False, "why": "题目只有一只橘猫，图里多出第二只猫"}
    v = judge(_img(tmp_path, (240, 240, 235)), K, rv, [], "none")
    assert not v.passed and any("内容不符" in p and "第二只猫" in p for p in v.problems)


def test_supplied_content_verdict_cannot_be_empty(tmp_path):
    rv = _rv()
    rv["content_match"] = {"ok": True, "why": ""}
    v = judge(_img(tmp_path, (240, 240, 235)), K, rv, [], "none")
    assert not v.passed and "缺少内容事实核对结论" in v.problems


def test_thumbnail_gate_prompt_schema_and_missing_verdict(tmp_path):
    from stylebook.qa import missing_items, review_prompt, review_schema
    c = {**K, "_thumbnail_expectation": "/tmp/cover.thumb-46.png"}
    assert "原生尺寸" in review_prompt(c, [], "none")
    assert "thumbnail_readable" in review_schema(c)["required"]
    assert any("thumbnail_readable" in x for x in missing_items(_rv(), c, "none"))
    v = judge(_img(tmp_path, (240, 240, 235)), c, _rv(), [], "none")
    assert not v.passed and "缺少缩略图可辨认结论" in v.problems


def test_nontransparent_matrix_rejects_alpha_even_when_review_says_pass(tmp_path):
    from PIL import Image
    p = tmp_path / "alpha.png"
    im = Image.new("RGBA", (64, 64), (240, 240, 235, 255))
    im.putpixel((0, 0), (0, 0, 0, 1))
    im.save(p)
    v = judge(p, K, _rv(), [], "none")
    assert not v.passed and any("含透明像素" in x for x in v.problems)
    assert v.pixel["alpha_nonopaque_ratio"] > 0
    assert judge(_img(tmp_path, (240, 240, 235), "opaque.png"), K, _rv(), [], "none").passed


def test_transparent_sticker_is_allowed(tmp_path):
    from PIL import Image
    p = tmp_path / "sticker.png"
    image = Image.new("RGBA", (64, 64), (240, 240, 235, 0))
    for x in range(20, 44):
        for y in range(20, 44):
            image.putpixel((x, y), (240, 240, 235, 255))
    image.save(p)
    assert judge(p, K, _rv(), [], "none", "sticker-grid").passed


def test_empty_sticker_grid_is_rejected_even_when_review_says_pass(tmp_path):
    from PIL import Image
    p = tmp_path / "empty.png"
    Image.new("RGBA", (64, 64), (240, 240, 235, 0)).save(p)
    verdict = judge(p, K, _rv(), [], "none", "sticker-grid")
    assert not verdict.passed and any("清晰可见的主体" in issue for issue in verdict.problems)


def test_sticker_single_requires_clean_alpha_geometry(tmp_path):
    from PIL import Image, ImageDraw
    contract = {"qa": {"must_see": [], "must_not_see": [], "pixel": {}}}
    review = {"must_see": [], "must_not_see": [], "transcribed_text": []}

    def verdict(name, box=None, stray=False, mode="RGBA"):
        image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        if box:
            ImageDraw.Draw(image).rectangle(box, fill=(230, 140, 100, 255))
        if stray:
            image.putpixel((2, 2), (230, 140, 100, 255))
        path = tmp_path / name
        image.convert(mode).save(path)
        return judge(path, contract, review, [], "none", "sticker-single")

    good = verdict("good.png", (80, 80, 430, 430))
    assert good.passed and good.pixel["sticker_single"]["passed"]
    assert not verdict("empty.png").passed
    assert any("四边透明留白不足" in p for p in verdict("edge.png", (20, 80, 430, 430)).problems)
    assert any("游离像素" in p for p in verdict("stray.png", (80, 80, 430, 430), stray=True).problems)
    assert any("需要 RGBA" in p for p in verdict("rgb.png", (80, 80, 430, 430), mode="RGB").problems)


def test_single_alpha_pixel_is_not_lost_to_ratio_rounding(tmp_path):
    from PIL import Image
    p = tmp_path / "one-alpha.png"
    im = Image.new("RGBA", (512, 512), (240, 240, 235, 255))
    im.putpixel((0, 0), (240, 240, 235, 254))
    im.save(p)
    v = judge(p, K, _rv(), [], "none")
    assert v.pixel["alpha_nonopaque_ratio"] == 0.0
    assert v.pixel["alpha_nonopaque_pixels"] == 1
    assert not v.passed


def _fake_claude(tmp_path, answers):
    """假 claude：按顺序吐出预设的 JSON 结论，调用次数记在文件里。"""
    import json as _j
    import stat
    (tmp_path / "answers.json").write_text(_j.dumps(answers, ensure_ascii=False), encoding="utf-8")
    exe = tmp_path / "claude"
    exe.write_text(f"""#!/usr/bin/env python3
import json, sys, pathlib
d = pathlib.Path({str(tmp_path)!r})
n = int((d / "n").read_text()) if (d / "n").exists() else 0
(d / "n").write_text(str(n + 1))
(d / f"prompt{{n}}.txt").write_text(sys.stdin.read())
a = json.loads((d / "answers.json").read_text())
print(json.dumps({{"structured_output": a[min(n, len(a) - 1)]}}))
""", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_reviewer_reasks_when_items_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    from stylebook.qa import reviewer
    full = _rv()
    part = {**full, "must_not_see": full["must_not_see"][:1]}
    exe = _fake_claude(tmp_path, [part, full])
    monkeypatch.setenv("SANSHENG_IMAGE_QA_CLAUDE", str(exe))
    img = _img(tmp_path, (230, 230, 230))
    out = reviewer.review(img, K, [], "native")
    assert (tmp_path / "n").read_text() == "2"
    assert "文字在底板里" in (tmp_path / "prompt1.txt").read_text()
    assert len(out["must_not_see"]) == 2


def test_reviewer_still_missing_is_judged_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    from stylebook.qa import reviewer
    full = _rv()
    part = {**full, "must_not_see": full["must_not_see"][:1]}
    exe = _fake_claude(tmp_path, [part, part])
    monkeypatch.setenv("SANSHENG_IMAGE_QA_CLAUDE", str(exe))
    img = _img(tmp_path, (230, 230, 230))
    out = reviewer.review(img, K, [], "native")
    v = judge(img, K, out, [], "native")
    assert not v.passed and any("漏了一条禁止特征" in p for p in v.problems)


def test_agent_plan_reviewer_sends_image_and_checks_model(tmp_path, monkeypatch):
    import json as _j
    from stylebook.qa import reviewer

    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "ark_agent_plan")
    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", reviewer.ARK_PLAN_URL)
    monkeypatch.setenv("ARK_AGENT_PLAN_API_KEY", "test-key")
    calls = []

    class Reply:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return _j.dumps({"status": "completed", "model": "doubao-seed-2-1-turbo-260628",
                             "output": [{"content": [{"type": "output_text", "text": _j.dumps(_rv())}]}]}).encode()

    def open_fake(request, timeout):
        calls.append(request)
        assert request.full_url == reviewer.ARK_PLAN_URL + "/responses"
        body = _j.loads(request.data)
        assert body["model"] == reviewer.ARK_MODEL
        assert "thinking" not in body
        assert body["reasoning"] == {"effort": "low"}
        assert body["input"][0]["content"][2]["image_url"].startswith("data:image/png;base64,")
        return Reply()

    monkeypatch.setattr(reviewer.urllib.request, "urlopen", open_fake)
    result = reviewer.review(_img(tmp_path, (230, 230, 230)), K, [], "none")
    assert reviewer.source() == "ark_agent_plan"
    assert result["_reviewer"] == "doubao-seed-2-1-turbo-260628 via Ark Agent Plan"
    assert len(calls) == 1


def test_agent_plan_reviewer_reads_real_thumbnail_and_jpeg(tmp_path, monkeypatch):
    import json as _j
    from stylebook.qa import reviewer

    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "ark_agent_plan")
    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", reviewer.ARK_PLAN_URL)
    monkeypatch.setenv("ARK_AGENT_PLAN_API_KEY", "test-key")
    image = _img(tmp_path, (230, 230, 230), "large.jpg")
    thumb = _img(tmp_path, (230, 230, 230), "thumb.png")
    square = _img(tmp_path, (230, 230, 230), "square.png")
    contract = {**K, "_thumbnail_expectation": str(thumb), "_square_crop_expectation": str(square)}
    answer = {**_rv(), "thumbnail_readable": {"ok": True, "why": "小图仍能辨认"},
              "square_crop": {"ok": True, "why": "方形图完整"}}

    class Reply:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return _j.dumps({"status": "completed", "model": "doubao-seed-2-1-turbo-260628",
                             "output": [{"content": [{"type": "output_text", "text": _j.dumps(answer)}]}]}).encode()

    def open_fake(request, timeout):
        content = _j.loads(request.data)["input"][0]["content"]
        images = [part["image_url"] for part in content if part["type"] == "input_image"]
        assert len(images) == 3
        assert images[0].startswith("data:image/jpeg;base64,")
        assert images[1].startswith("data:image/png;base64,")
        assert images[2].startswith("data:image/png;base64,")
        assert "只依据下一张小图" in content[-4]["text"]
        assert "只依据下一张方形图" in content[-2]["text"]
        return Reply()

    monkeypatch.setattr(reviewer.urllib.request, "urlopen", open_fake)
    result = reviewer.review(image, contract, [], "none")
    assert result["thumbnail_readable"]["ok"] is True
    assert result["square_crop"]["ok"] is True


def test_agent_plan_reviewer_fails_closed_on_endpoint_and_auth(tmp_path, monkeypatch):
    import io
    from stylebook.qa import reviewer

    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "ark_agent_plan")
    monkeypatch.setenv("ARK_AGENT_PLAN_API_KEY", "test-key")
    image = _img(tmp_path, (230, 230, 230))
    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    with pytest.raises(reviewer.ReviewerAuthUnavailable, match="套餐地址"):
        reviewer.review(image, K, [], "none")

    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", reviewer.ARK_PLAN_URL)
    calls = []
    def reject(request, timeout):
        calls.append(1)
        raise reviewer.urllib.error.HTTPError(request.full_url, 403, "denied", {}, io.BytesIO(b'{"error":"denied"}'))
    monkeypatch.setattr(reviewer.urllib.request, "urlopen", reject)
    with pytest.raises(reviewer.ReviewerAuthUnavailable, match="访问被拒"):
        reviewer.review(image, K, [], "none")
    assert len(calls) == 1


def test_agent_plan_reviewer_stops_on_network_error_without_retry(tmp_path, monkeypatch):
    from stylebook.qa import reviewer

    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "ark_agent_plan")
    monkeypatch.setenv("ARK_AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", reviewer.ARK_PLAN_URL)
    calls = []

    def unavailable(request, timeout):
        calls.append(1)
        raise reviewer.urllib.error.URLError(OSError(1, "Operation not permitted"))

    monkeypatch.setattr(reviewer.urllib.request, "urlopen", unavailable)
    with pytest.raises(reviewer.ReviewerNetworkUnavailable, match="网络不可用"):
        reviewer.review(_img(tmp_path, (230, 230, 230)), K, [], "none", tries=3)
    assert len(calls) == 1


def test_reviewer_stops_on_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    import stat
    from stylebook.qa import reviewer
    exe = tmp_path / "claude"
    exe.write_text('#!/bin/sh\necho \'{"is_error":true,"api_error_status":429}\'\n', encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SANSHENG_IMAGE_QA_CLAUDE", str(exe))
    with pytest.raises(reviewer.QuotaExhausted):
        reviewer.review(_img(tmp_path, (230, 230, 230)), K, [], "none")


def test_reviewer_stops_on_missing_login_without_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    import stat
    from stylebook.qa import reviewer
    exe = tmp_path / "claude"
    exe.write_text('#!/bin/sh\necho \'{"is_error":true,"result":"Not logged in · Please run /login"}\'\n', encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SANSHENG_IMAGE_QA_CLAUDE", str(exe))
    with pytest.raises(reviewer.ReviewerAuthUnavailable, match="未登录"):
        reviewer.review(_img(tmp_path, (230, 230, 230)), K, [], "none")


def test_reviewer_stops_on_disabled_subscription_without_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    import stat
    from stylebook.qa import reviewer
    exe = tmp_path / "claude"
    calls = tmp_path / "calls.txt"
    exe.write_text(f'''#!/bin/sh
echo called >> "{calls}"
echo 'Your organization has disabled Claude subscription access for Claude Code · Use an Anthropic API key instead, or ask your admin to enable access'
exit 1
''', encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SANSHENG_IMAGE_QA_CLAUDE", str(exe))
    with pytest.raises(reviewer.ReviewerAuthUnavailable, match="订阅访问被禁用"):
        reviewer.review(_img(tmp_path, (230, 230, 230)), K, [], "none")
    assert calls.read_text().splitlines() == ["called"]


@pytest.mark.parametrize("reason", ["英文字高超过中文标题", "英文对比抢过主标题", "缩略图第一眼落在英文上"])
def test_ghost_visual_failure_cannot_be_waived_by_opacity_guidance(tmp_path, reason):
    item = "one subdued uppercase English ghost line behind the headline, white at roughly 8%-14% opacity, no larger than the headline, over a low-contrast abstract background"
    c = {**K, "qa": {**K["qa"], "must_see": [item]}}
    rv = {"must_see": [{"item": item, "ok": True, "why": "小字低对比"}],
          "must_not_see": _rv()["must_not_see"], "transcribed_text": []}
    image = _img(tmp_path, (240, 240, 235))
    assert judge(image, c, rv, [], "none").passed
    rv["must_see"][0].update(ok=False, why=reason)
    assert not judge(image, c, rv, [], "none").passed
    assert not judge(image, c, {}, [], "none").passed
