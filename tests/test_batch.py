import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import backends as B  # noqa: E402
from stylebook import batch as BT  # noqa: E402
from stylebook import contract as CT  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "none"))
    monkeypatch.setattr(CT.Path, "home", staticmethod(lambda: tmp_path))


class Gen:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, prompt, out, *, size, aspect, refs, provider, model, quality, tag):
        from PIL import Image
        self.calls.append({"tag": tag, "refs": [str(r) for r in refs], "prompt": prompt})
        if self.fail:
            raise B.BackendError(self.fail, "模拟", retryable=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, (245, 240, 230)).save(out)
        return B.Result(b"", "fake", model, 1, 0.1, 0.04)


from stylebook import contract as _CT  # noqa: E402
BAD = _CT.load("C42")["qa"]["must_see"][0]  # 批量重试用例借用当前样式的第一条必须看到


def ok_review(img, c, e, m, bad=()):
    rv = {"must_see": [{"item": i, "ok": not any(i.startswith(b) for b in bad), "why": "看到"} for i in c["qa"]["must_see"]],
            "must_not_see": [{"item": i, "present": False, "why": "未见"} for i in c["qa"]["must_not_see"]],
            "transcribed_text": list(e),
            "content_match": {"ok": True, "why": "主体、动作与空间关系符合计划"}}
    if c.get("_content_point_expectations"):
        rv["point_match"] = [{"index": i, "ok": True, "why": f"第 {i} 条必要信息可见"}
                             for i in range(1, len(c["_content_point_expectations"]) + 1)]
    if c.get("_thumbnail_expectation"):
        rv["thumbnail_readable"] = {"ok": True, "why": "真实小图中主体仍清晰"}
    if c.get("_square_crop_expectation"):
        rv["square_crop"] = {"ok": True, "why": "实际方形图中标题、主体与信息标记完整且有边距"}
    if c.get("_panel_expectations"):
        rv["panel_match"] = [{"panel": i, "ok": True, "why": "逐格符合"}
                             for i in range(1, len(c["_panel_expectations"]) + 1)]
    return rv


def item(i, **kw):
    it = {"id": f"{i:02d}", "position": f"第 {i} 页「这一页讲完的那句话」之后", "shape": "steps", "form": "structure", "structure": "flow",
          "points": ["一", "二", "三"], "what": f"第 {i} 页内容", "subject": f"page {i} of a card series",
          "why": "这一页讲先后三步，读者要看到顺序", "text": {"mode": "native", "items": [{"role": "title", "text": f"第{i}页"}]}}
    it.update(kw)
    return it


def xhs_plan(n=3):
    items = [item(1)] + [item(i, shape="parts", structure="list") if i % 2 == 0 else item(i) for i in range(2, n + 1)]
    return {"scene": "xhs", "style": {"code": "C42", "source": "factory", "why": "小红书出厂默认"}, "items": items}


def run(tmp_path, plan, **kw):
    kw.setdefault("gen_fn", Gen())
    kw.setdefault("review_fn", ok_review)
    return BT.run(plan, tmp_path / "b", model="gpt-image-2", quality="normal", log=lambda *_: None, jobs=1, **kw), kw["gen_fn"]


def test_default_batch_reviewer_records_agent_plan_source(tmp_path, monkeypatch):
    from stylebook.qa import reviewer
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "ark_agent_plan")
    monkeypatch.setattr(reviewer, "review", ok_review)
    state = BT.run(xhs_plan(1), tmp_path / "b", gen_fn=Gen(), model="gpt-image-2", quality="normal",
                   jobs=1, log=lambda *_: None)
    assert state["items"]["01"]["review_source"] == "ark_agent_plan"


def test_series_uses_first_page_as_style_reference_and_exports(tmp_path):
    from PIL import Image
    st, g = run(tmp_path, xhs_plan(3))
    anchor = str(ROOT / "styles" / "C42" / "anchor.png")
    assert g.calls[0]["tag"] == "batch:01" and g.calls[0]["refs"] == [anchor]
    first = str(tmp_path / "b" / "01.raw.png")
    assert all(c["refs"] == [first] for c in g.calls[1:])
    assert "style reference" in g.calls[1]["prompt"]
    assert st["summary"]["passed"] == 3
    assert Image.open(tmp_path / "b" / "01.png").size == (1242, 1656)  # 小红书导出像素
    assert (tmp_path / "b" / "01-square.png").is_file()  # 居中方形安全区
    assert (tmp_path / "b" / "report.md").is_file() and (tmp_path / "b" / "overview.png").is_file()


def test_failed_series_master_blocks_dependents_even_with_raw_file(tmp_path):
    def bad_review(img, c, e, m):
        review = ok_review(img, c, e, m)
        review["content_match"] = {"ok": False, "why": "缺少必要步骤"}
        return review

    state, gen = run(tmp_path, xhs_plan(3), review_fn=bad_review, retries=0)
    assert len(gen.calls) == 1
    assert (tmp_path / "b" / "01.raw.png").is_file()
    assert state["items"]["01"]["status"] == "failed"
    assert [state["items"][i]["status"] for i in ("02", "03")] == ["blocked", "blocked"]
    assert state["summary"]["passed"] == 0


def test_missing_condition_or_unit_rejects_content_on_reviewed_path(tmp_path):
    p = xhs_plan(1)
    p["items"][0]["points"] = ["先核对原文", "仅在确认后发送", "金额为 19 元"]

    def missing_unit(img, contract, expected, mode):
        review = ok_review(img, contract, expected, mode)
        review["point_match"][2] = {"index": 3, "ok": False, "why": "图上只有 19，缺少元这一单位"}
        return review

    state, _ = run(tmp_path, p, review_fn=missing_unit, retries=0)
    assert state["items"]["01"]["status"] == "failed"
    assert any("第 3 条必要信息不符" in problem for problem in state["items"]["01"]["problems"])

    def omitted_condition(img, contract, expected, mode):
        review = ok_review(img, contract, expected, mode)
        review["point_match"].pop(1)
        return review

    second, _ = run(tmp_path / "missing", p, review_fn=omitted_condition, retries=0)
    assert second["items"]["01"]["status"] == "failed"
    assert any("缺少逐条必要信息" in problem for problem in second["items"]["01"]["problems"])


def test_article_series_chooses_body_image_as_master(tmp_path):
    p = xhs_plan(2)
    p["scene"] = "wxillus"
    p["format"] = "article-illustration"
    p["items"][0].update(format="wechat-cover-head", shape="story", form="scene")
    p["items"][0].pop("points")
    p["items"][0].pop("structure")
    state, gen = run(tmp_path, p)
    assert [call["tag"] for call in gen.calls] == ["batch:02", "batch:01"]
    assert gen.calls[1]["refs"] == [str(tmp_path / "b" / "02.raw.png")]
    assert state["summary"]["passed"] == 2


def test_reference_image_bytes_invalidate_cached_result(tmp_path):
    from PIL import Image
    ref = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), "blue").save(ref)
    p = xhs_plan(1)
    p["items"][0]["references"] = [{"path": str(ref), "role": "composition"}]
    first, first_gen = run(tmp_path, p)
    cached, cached_gen = run(tmp_path, p)
    assert first["items"]["01"]["status"] == "passed" and len(first_gen.calls) == 1
    assert cached["items"]["01"]["status"] == "passed" and cached_gen.calls == []
    Image.new("RGB", (32, 32), "red").save(ref)
    changed, changed_gen = run(tmp_path, p)
    assert changed["items"]["01"]["status"] == "passed" and len(changed_gen.calls) == 1


def test_unreviewed_series_master_never_spreads(tmp_path):
    state, gen = run(tmp_path, xhs_plan(3), do_review=False)
    assert len(gen.calls) == 1
    assert state["items"]["01"]["status"] == "generated"
    assert all(state["items"][item]["status"] == "blocked" for item in ("02", "03"))


def test_alpha_export_failure_is_reported_and_can_regenerate(tmp_path):
    from PIL import Image

    class AlphaOnce(Gen):
        def __call__(self, prompt, out, **kw):
            result = super().__call__(prompt, out, **kw)
            if len(self.calls) == 1:
                Image.new("RGBA", Image.open(out).size, (240, 240, 235, 0)).save(out)
            return result

    gen = AlphaOnce()
    st, _ = run(tmp_path, xhs_plan(1), gen_fn=gen)
    assert st["items"]["01"]["status"] == "export_failed"
    assert st["summary"]["failed"] == ["01"]
    assert "透明像素" in (tmp_path / "b" / "report.md").read_text()
    st, _ = run(tmp_path, xhs_plan(1), gen_fn=gen)
    assert st["items"]["01"]["status"] == "passed" and len(gen.calls) == 2


def test_failed_review_retries_with_fixes_then_passes(tmp_path):
    seen = {"n": 0}

    def flaky(img, c, e, m):
        seen["n"] += 1
        return ok_review(img, c, e, m, bad=(BAD,) if seen["n"] == 1 else ())
    st, g = run(tmp_path, xhs_plan(1), review_fn=flaky)
    rec = st["items"]["01"]
    assert rec["status"] == "passed" and rec["attempts"] == 2 and len(g.calls) == 2
    assert f"Correct these points from the previous attempt: make sure the image clearly shows: {BAD}" in g.calls[1]["prompt"]


def test_retry_limit_marks_failed(tmp_path):
    st, g = run(tmp_path, xhs_plan(1), review_fn=lambda i, c, e, m: ok_review(i, c, e, m, bad=(BAD,)), retries=2)
    assert st["items"]["01"]["status"] == "failed" and len(g.calls) == 3
    assert st["summary"]["failed"] == ["01"]


def test_resume_does_not_regenerate(tmp_path):
    run(tmp_path, xhs_plan(3))
    st, g = run(tmp_path, xhs_plan(3), gen_fn=Gen())
    assert g.calls == [] and st["summary"]["passed"] == 3


def test_auth_error_stops(tmp_path):
    st, g = run(tmp_path, xhs_plan(3), gen_fn=Gen(fail="auth"))
    assert len(g.calls) == 1 and st["summary"]["passed"] == 0


def test_reviewer_network_error_stops_batch(tmp_path):
    from stylebook.qa.reviewer import ReviewerNetworkUnavailable
    seen = []

    def unavailable(*_):
        seen.append(1)
        raise ReviewerNetworkUnavailable("网络不可用")

    st, g = run(tmp_path, xhs_plan(3), review_fn=unavailable)
    assert len(seen) == 1 and len(g.calls) == 1
    assert st["items"]["01"]["status"] == "review_failed"
    assert st["summary"]["passed"] == 0


def test_fixes_from_problems():
    fx = BT.fixes_from(["没做到：big round glasses（第三格没有）", "出现了禁止特征：neon signs（右上）", "缺字或错字：应为「番茄工作法」",
                        "整体偏暗：平均明度 180 < 192", "多写了文字：「abc」", "本图不该有字，却出现了：['00000']"])
    assert fx == ["make sure the image clearly shows: big round glasses", "the image must not contain: neon signs",
                  "the text must read exactly 「番茄工作法」 with every character correct and legible",
                  "make the whole image lighter and more high-key", "show only the text that was asked for; no other words or letters",
                  "leave every screen, meter, dial, sign and label blank: no letters or numbers anywhere in the image"]


def test_sticker_geometry_retries_have_targeted_fixes():
    assert BT.fixes_from(["单张透明表情：四边透明留白不足 10%：最窄 2.00%",
                          "单张透明表情：主体连通块 2 个，游离像素 1 个"]) == [
        "center the sticker character with at least 10% fully transparent margin on every side",
        "remove all detached pixels and fragments; keep one connected sticker character silhouette",
    ]


def test_sticker_batch_rejects_detached_pixels_despite_positive_review(tmp_path):
    from PIL import Image, ImageDraw

    class StickerGen(Gen):
        def __init__(self, stray):
            super().__init__()
            self.stray = stray

        def __call__(self, prompt, out, *, size, **kw):
            result = super().__call__(prompt, out, size=size, **kw)
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((size[0] * .2, size[1] * .2, size[0] * .8, size[1] * .8), fill=(240, 170, 120, 255))
            if self.stray:
                draw.rectangle((size[0] * .01, size[1] * .01, size[0] * .04, size[1] * .04),
                               fill=(240, 170, 120, 255))
            image.save(out)
            return result

    plan = {"scene": "sticker", "format": "sticker-single",
            "style": {"code": "C25", "source": "explicit", "why": "九张独立透明表情沿用毛绒画风"},
            "items": [{"id": "01", "position": "表情包第一张", "shape": "story", "form": "scene",
                       "what": "小狐狸开心", "subject": "One plush fox smiling on transparent background",
                       "why": "一张图只表达开心，并留出透明边缘", "text": {"mode": "none"}}]}
    good, _ = run(tmp_path / "good", plan, gen_fn=StickerGen(False), retries=0)
    bad, _ = run(tmp_path / "bad", plan, gen_fn=StickerGen(True), retries=0)
    assert good["items"]["01"]["status"] == "passed"
    assert bad["items"]["01"]["status"] == "failed"
    assert any("游离像素" in issue for issue in bad["items"]["01"]["problems"])


def test_bad_plan_refused(tmp_path):
    p = xhs_plan(1)
    p["items"][0]["why"] = "短"
    with pytest.raises(ValueError):
        run(tmp_path, p)


def test_contract_pinned_and_revision_change_regenerates(tmp_path, monkeypatch):
    real = CT.load
    n = {"k": 0}

    def drifting(code):  # 模拟跑到一半有人改了合同：每次读都更高一版
        n["k"] += 1
        c = real(code)
        c["revision"] += n["k"]
        c["changelog"] = c["changelog"] + [{"revision": c["revision"], "date": "2026-09-26", "note": "测试"}]
        return c
    monkeypatch.setattr(CT, "load", drifting)
    st, g = run(tmp_path, xhs_plan(3))
    styles = {v["style"] for v in st["items"].values()}
    assert len(styles) == 1  # 整套只用一个修订号
    st, g2 = run(tmp_path, xhs_plan(3), gen_fn=Gen())
    assert len(g2.calls) == 3 and {v["style"] for v in st["items"].values()} != styles  # 合同升级后续跑整套重出


def test_no_text_items_judged_as_textless(tmp_path):
    p = xhs_plan(1)
    p["items"][0]["text"] = {"mode": "native", "items": []}
    seen = []

    def rv(img, c, e, m):
        seen.append(m)
        return ok_review(img, c, e, m)
    st, _ = run(tmp_path, p, review_fn=rv)
    assert seen == ["none"] and st["items"]["01"]["status"] == "passed"


def test_overlay_batch_exports_typeset_final_and_reviews_exact_commands(tmp_path):
    from PIL import Image, ImageChops
    p = xhs_plan(1)
    p["items"][0]["text"] = {"mode": "overlay", "reserve": "center", "items": [
        {"text": "nlm setup add claude-code", "box": [0.1, 0.3, 0.8, 0.1], "font_px": 48, "min_px": 40}]}
    seen = []

    def rv(img, contract, expected, mode):
        seen.append((expected, mode))
        return ok_review(img, contract, expected, mode)

    st, gen = run(tmp_path, p, review_fn=rv)
    assert st["items"]["01"]["status"] == "passed"
    assert seen == [(["nlm setup add claude-code"], "overlay")]
    raw = Image.open(tmp_path / "b" / "01.raw.png")
    final = Image.open(tmp_path / "b" / "01.png")
    assert ImageChops.difference(raw, final).getbbox()
    assert "no text, letters or captions" in gen.calls[0]["prompt"]


def test_overlay_plan_without_boxes_is_rejected(tmp_path):
    p = xhs_plan(1)
    p["items"][0]["text"] = {"mode": "overlay", "items": [{"text": "nlm login"}]}
    with pytest.raises(ValueError, match="box 必须"):
        run(tmp_path, p)


def test_hybrid_batch_keeps_title_native_and_typesets_exact_command(tmp_path):
    from PIL import Image, ImageChops
    p = xhs_plan(1)
    p["items"][0]["text"] = {"mode": "hybrid", "reserve": "one blank inset below the title", "items": [
        {"render": "native", "role": "title", "text": "第一步：安装", "position": "top"},
        {"render": "overlay", "role": "code", "text": "uv tool install notebooklm-mcp-cli",
         "box": [0.08, 0.35, 0.84, 0.08], "font_px": 55, "min_px": 55},
    ]}
    seen = []

    def rv(img, contract, expected, mode):
        seen.append((expected, mode))
        return ok_review(img, contract, expected, mode)

    state, gen = run(tmp_path, p, review_fn=rv)
    assert state["items"]["01"]["status"] == "passed"
    assert seen == [(["第一步：安装", "uv tool install notebooklm-mcp-cli"], "hybrid")]
    assert "第一步：安装" in gen.calls[0]["prompt"]
    assert "uv tool install notebooklm-mcp-cli" not in gen.calls[0]["prompt"]
    with Image.open(tmp_path / "b" / "01.raw.png") as raw, Image.open(tmp_path / "b" / "01.png") as final:
        assert ImageChops.difference(raw, final).getbbox()


def test_cover_and_independent_square_are_distinct_deliverables(tmp_path):
    p = {"scene": "book", "style": {"code": "C08", "source": "factory", "why": "绘本出厂默认"}, "items": [
        {"id": "cover", "position": "故事横幅封面", "format": "wechat-cover-head", "shape": "story", "form": "scene",
         "what": "狐狸看远处的灯", "subject": "a fox looking toward one distant lamp", "why": "横幅保留远景故事镜头"},
        {"id": "cover-thumbnail", "position": "列表方形识别图", "format": "wechat-cover-square", "shape": "story", "form": "scene",
         "what": "狐狸与亮窗", "subject": "the same fox and one large lit cottage window", "why": "列表小图独立呈现狐狸与亮窗"},
    ]}
    st, _ = run(tmp_path, p, review_source="maintainer_codex_native")
    assert st["summary"]["passed"] == 2
    out = tmp_path / "b"
    entries = {x["format"]: x for x in json.loads((out / "deliverables.json").read_text())["items"]}
    assert entries["wechat-cover-head"]["image"] == "cover.png"
    assert entries["wechat-cover-head"]["extras"] == ["cover-square.png"]
    assert entries["wechat-cover-square"]["image"] == "cover-thumbnail.png"
    from PIL import Image
    assert Image.open(out / "cover-thumbnail.thumb-46.png").size == (46, 46)
    assert all(x["ready"] for x in entries.values())
    assert all(x["review_source"] == "maintainer_codex_native" for x in entries.values())
    assert "| maintainer_codex_native |" in (out / "report.md").read_text()
    assert "| cover-thumbnail | wechat-cover-square | cover-thumbnail.png |" in (out / "report.md").read_text()

    # 续跑沿用缓存结论时保留原始验收来源，不能拿新传入的来源洗成独立复核。
    st_cached, gen_cached = run(tmp_path, p, review_source="claude_cli")
    assert st_cached["items"]["cover-thumbnail"]["review_source"] == "maintainer_codex_native"
    assert gen_cached.calls == []

    # 仅生成、没有看图验收时，路径可供复核，但不能作为已通过的交付图。
    st2, _ = run(tmp_path / "unreviewed", p, do_review=False)
    assert st2["summary"]["passed"] == 0
    pending = json.loads((tmp_path / "unreviewed" / "b" / "deliverables.json").read_text())["items"]
    assert all(x["status"] == "generated" and not x["ready"] for x in pending)
    assert all(x["review_source"] is None for x in pending)

    # 内容改变后重出图，旧图的验收来源不能留在新图上。
    changed = json.loads(json.dumps(p))
    changed["items"][1]["subject"] = "the same fox beside a distant pine tree"
    st_new, gen_new = run(tmp_path, changed, do_review=False)
    assert gen_new.calls
    assert st_new["items"]["cover-thumbnail"].get("review_source") is None


def test_delivery_receipt_binds_prompt_pixels_and_renderer(tmp_path):
    plan = xhs_plan(1)
    state, _ = run(tmp_path, plan)
    out = tmp_path / "b"
    record = state["items"]["01"]
    delivery = json.loads((out / "deliverables.json").read_text())["items"][0]
    assert delivery["ready"] and delivery["provider"] == "fake" and delivery["model"] == "gpt-image-2"
    assert delivery["prompt_sha256"] == record["prompt_sha256"]
    assert delivery["image_sha256"] == record["image_sha256"]

    # 实际缓存路径上的反例：即使指纹未变，原图字节或提示词被改也必须重出、重验。
    (out / "01.raw.png").write_bytes(b"altered")
    reviewed = []

    def review_again(*args):
        reviewed.append(True)
        return ok_review(*args)

    _, regenerated = run(tmp_path, plan, review_fn=review_again)
    assert len(regenerated.calls) == 1 and reviewed
    (out / "01.prompt.txt").write_text("different prompt")
    _, regenerated = run(tmp_path, plan, review_fn=review_again)
    assert len(regenerated.calls) == 1 and len(reviewed) == 2

    # 缺来源的旧状态不能借新的交付格式自动升级为 ready。
    old = json.loads((out / "state.json").read_text())
    old["items"]["01"].pop("raw_sha256")
    (out / "state.json").write_text(json.dumps(old))
    _, regenerated = run(tmp_path, plan, review_fn=review_again)
    assert len(regenerated.calls) == 1 and len(reviewed) == 3


def test_content_error_rejected_even_when_style_and_text_pass(tmp_path):
    p = xhs_plan(1)
    seen, seen_points = [], []

    def wrong_story(img, contract, expected, mode):
        seen.append(contract.get("_content_expectation"))
        seen_points.append(contract.get("_content_point_expectations"))
        rv = ok_review(img, contract, expected, mode)
        rv["content_match"] = {"ok": False, "why": "狐狸已站在屋门口，计划要求它遥望远处的灯"}
        return rv

    p["items"][0]["subject"] = "A fox looks toward a distant lamp, not standing by the cottage"
    st, gen = run(tmp_path, p, review_fn=wrong_story, retries=0)
    rec = st["items"]["01"]
    assert len(gen.calls) == 1 and seen[0].startswith(p["items"][0]["subject"])
    # points 逐条进 point_match，不再并入内容事实总述（避免同一条要点重复计罚）
    assert seen_points[0] == p["items"][0]["points"]
    assert not any(point in seen[0] for point in p["items"][0]["points"])
    assert rec["status"] == "failed" and any("内容不符" in x for x in rec["problems"])
    assert not json.loads((tmp_path / "b" / "deliverables.json").read_text())["items"][0]["ready"]


def test_missing_content_verdict_cannot_pass(tmp_path):
    def old_review(img, contract, expected, mode):
        rv = ok_review(img, contract, expected, mode)
        rv.pop("content_match")
        return rv

    st, _ = run(tmp_path, xhs_plan(1), review_fn=old_review, retries=0)
    assert st["items"]["01"]["status"] == "failed"
    assert "缺少内容事实核对结论" in st["items"]["01"]["problems"]


def test_legacy_style_only_review_is_rechecked_without_regeneration(tmp_path):
    run(tmp_path, xhs_plan(1))
    state_path = tmp_path / "b" / "state.json"
    old = json.loads(state_path.read_text())
    old["items"]["01"]["review"].pop("content_match")
    state_path.write_text(json.dumps(old))
    seen = []

    def updated_review(img, contract, expected, mode):
        seen.append(contract["_content_expectation"])
        return ok_review(img, contract, expected, mode)

    st, gen = run(tmp_path, xhs_plan(1), gen_fn=Gen(), review_fn=updated_review)
    assert gen.calls == [] and len(seen) == 1
    assert st["items"]["01"]["status"] == "passed"
    assert st["items"]["01"]["review"]["content_match"]["ok"] is True


def test_old_panel_review_cache_is_rechecked_without_regenerating(tmp_path):
    plan = xhs_plan(1)
    plan["items"][0].update(shape="story", form="scene", panels=["Fujian", "Malacca", "Europe", "tomato ketchup"])
    plan["items"][0].pop("structure")
    plan["items"][0].pop("points")
    first, _ = run(tmp_path, plan)
    assert first["items"]["01"]["status"] == "passed"
    state_path = tmp_path / "b" / "state.json"
    old = json.loads(state_path.read_text())
    old["items"]["01"].pop("panel_review_scope")
    old["items"]["01"]["review"].pop("panel_match")
    state_path.write_text(json.dumps(old))
    seen = []

    def reviewed(img, contract, expected, mode):
        seen.append(contract["_panel_expectations"])
        return ok_review(img, contract, expected, mode)

    state, gen = run(tmp_path, plan, gen_fn=Gen(), review_fn=reviewed)
    assert gen.calls == [] and seen == [["Fujian", "Malacca", "Europe", "tomato ketchup"]]
    assert state["items"]["01"]["status"] == "passed"
    assert state["items"]["01"]["review"]["panel_match"][1]["panel"] == 2


def test_center_square_rejects_cut_content_and_rechecks_legacy_review(tmp_path):
    plan = xhs_plan(1)

    def cropped(img, contract, expected, mode):
        rv = ok_review(img, contract, expected, mode)
        rv["square_crop"] = {"ok": False, "why": "标题右半与图形标记被方形右边截断"}
        return rv

    st, _ = run(tmp_path, plan, review_fn=cropped, retries=0)
    rec = st["items"]["01"]
    assert rec["status"] == "failed"
    assert any("居中方形裁切丢失关键内容" in p for p in rec["problems"])
    assert (tmp_path / "b" / "01-square.png").is_file()
    old = json.loads((tmp_path / "b" / "state.json").read_text())
    old["items"]["01"]["review"].pop("square_crop")
    (tmp_path / "b" / "state.json").write_text(json.dumps(old))
    seen = []

    def renewed(img, contract, expected, mode):
        seen.append(contract["_square_crop_expectation"])
        return ok_review(img, contract, expected, mode)

    st, gen = run(tmp_path, plan, gen_fn=Gen(), review_fn=renewed)
    assert gen.calls == [] and seen == [str(tmp_path / "b" / "01-square.png")]
    assert st["items"]["01"]["status"] == "passed"


def test_square_thumbnail_fails_even_when_full_image_passes(tmp_path):
    p = {"scene": "book", "style": {"code": "C08", "source": "factory", "why": "绘本出厂默认"}, "items": [
        {"id": "cover", "position": "故事列表方图", "format": "wechat-cover-square", "shape": "story", "form": "scene",
         "what": "狐狸看远灯", "subject": "a fox looking at a distant lamp", "why": "方图要能在列表中认出故事"}]}

    def tiny(img, contract, expected, mode):
        rv = ok_review(img, contract, expected, mode)
        rv["thumbnail_readable"] = {"ok": False, "why": "46 px 只有一个橘色点，无法辨认狐狸"}
        return rv

    st, _ = run(tmp_path, p, review_fn=tiny, retries=0)
    assert st["items"]["cover"]["status"] == "failed"
    assert any("缩略图主题不清" in x for x in st["items"]["cover"]["problems"])
    assert not json.loads((tmp_path / "b" / "deliverables.json").read_text())["items"][0]["ready"]
