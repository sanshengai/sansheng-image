"""看图验收的可靠性：多次采样聚合、已请求文字、任务单优先级、points 口径、内容分项与自相矛盾。

模型输出全部是 mock；每条规则都配一个必须被拒绝的反例。
"""
import copy
import io
import inspect
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook.qa import (MARK_BAN_ITEM, active_items, aggregate_reviews, check_conflict,  # noqa: E402
                          content_expectations, judge, missing_items, review_prompt, review_schema,
                          task_sheet_texts)
from stylebook.qa import coding_plan, reviewer  # noqa: E402

GHOST_BAN = "Behind the headline use only faint abstract lines, never ghost letters or any English words"
MUST_GHOST = "深炭底、左侧大标题加半隐英文影字"
CONTENT = {"subject": "磁盘清理封面，一台银色笔记本",
           "relations": GHOST_BAN + "; laptop centred in the square crop",
           "inventory": "开源徽章与 macOS 徽章各一个",
           "points": ["清理前先问四个问题", "缺一个证据就问用户"]}
BASE = {"code": "T1", "qa": {"must_see": ["画面明亮", MUST_GHOST, "笔记本完整"],
                              "must_not_see": ["未请求的文字或水印", "黑色背景"]}}
CONTRACT = {**BASE, **content_expectations(CONTENT)}
EXPECTED = ["磁盘清理", "开源", "macOS"]


def img(tmp_path, name="x.png"):
    path = tmp_path / name
    Image.new("RGB", (64, 64), (240, 240, 235)).save(path)
    return path


def rv(contract=CONTRACT, *, fail_must=(), present=(), transcript=None, cm_fail=(), schema=True, conflict=None,
       point_ok=True, why="实际看到"):
    out = {"must_see": [{"item": i, "ok": i not in fail_must, "applicable": True, "why": why}
                        for i in contract["qa"]["must_see"]],
           "must_not_see": [{"item": i, "present": i in present, "why": why}
                            for i in active_items(contract, "native")[1]],
           "transcribed_text": list(EXPECTED if transcript is None else transcript)}
    for item, claim in (conflict or {}).items():
        for row in out["must_see"] + out["must_not_see"]:
            if row["item"] == item:
                row["conflict"] = claim
    if contract.get("_content_parts"):
        parts = []
        for part in contract["_content_parts"]:
            bad = part["key"] in cm_fail
            row = {"key": part["key"], "ok": not bad, "why": "主体可辨" if not bad else "有明显缺失"}
            if bad:
                row["failed_clause"] = cm_fail[part["key"]] if isinstance(cm_fail, dict) else part["text"][:8]
            parts.append(row)
        out["content_match"] = {"ok": all(p["ok"] for p in parts), "why": "分项如下", "parts": parts}
    elif contract.get("_content_expectation"):
        out["content_match"] = {"ok": True, "why": "符合"}
    if contract.get("_content_point_expectations"):
        out["point_match"] = [{"index": i, "ok": point_ok, "why": "图标与连线传达了这层意思"}
                              for i in range(1, len(contract["_content_point_expectations"]) + 1)]
    if schema:
        out["_review_schema"] = 2
    return out


# ---- 2. 已请求文字 -------------------------------------------------------------

def test_prompt_lists_requested_text_and_marks_text_ban_as_reference():
    prompt = review_prompt(CONTRACT, EXPECTED, "native")
    section = prompt.split("## 本图已请求的文字（逐条）")[1].split("##")[0]
    for text in EXPECTED:
        assert f"「{text}」" in section
    assert "不算「未请求的文字」" in section
    assert "由脚本另行比对" not in prompt  # 旧的「你只需如实抄录」空话已替换
    assert "参考项" in prompt.split("## 不许看到")[1].split("##")[0]


def test_model_calling_requested_text_unrequested_does_not_fail(tmp_path):
    review = rv(present={"未请求的文字或水印"}, why="画面出现“开源”“macOS”")
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert verdict.passed, verdict.problems
    assert any("仅参考" in a for a in verdict.triage["advisory"])


def test_really_extra_text_still_fails_by_deterministic_comparison(tmp_path):
    review = rv(present={"未请求的文字或水印"}, transcript=EXPECTED + ["DISKCLEANUP"])
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert not verdict.passed and any("多写了文字" in p for p in verdict.problems)


def test_other_ban_items_are_still_decided_by_the_model(tmp_path):
    verdict = judge(img(tmp_path), CONTRACT, rv(present={"黑色背景"}), EXPECTED, "native")
    assert not verdict.passed and any("出现了禁止特征：黑色背景" in p for p in verdict.problems)


def test_unrequested_text_item_cannot_hide_text_in_a_no_text_image(tmp_path):
    contract = {**BASE}
    review = rv(contract, transcript=["私自加的水印字"], present={"未请求的文字或水印"})
    verdict = judge(img(tmp_path), contract, review, [], "none")
    assert not verdict.passed and any("不该有字" in p for p in verdict.problems)


# ---- 3. 任务单优先于合同 -----------------------------------------------------------

def claim(quote=GHOST_BAN, reason="任务单明确禁止影字，合同要求影字"):
    return {"task_quote": quote, "reason": reason}


def test_task_sheet_texts_cover_requirements_but_not_requested_text():
    sheet = task_sheet_texts(CONTRACT, EXPECTED)
    assert any(GHOST_BAN in t for t in sheet) and "清理前先问四个问题" in sheet
    assert "开源" not in sheet  # 已请求文字不是「要求」，不能拿来充当冲突引用


def test_requested_text_cannot_serve_as_conflict_quote(tmp_path):
    expected = EXPECTED + ["叁笙清理一次腾出 23.9 GB"]
    review = rv(fail_must={MUST_GHOST}, conflict={MUST_GHOST: claim("叁笙清理一次腾出 23.9 GB")},
                transcript=expected)
    verdict = judge(img(tmp_path), CONTRACT, review, expected, "native")
    assert not verdict.passed and verdict.triage["conflicts"] == []


def test_contract_ban_cannot_be_waived_by_conflict(tmp_path):
    review = rv(present={"黑色背景"}, conflict={"黑色背景": claim()})
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert not verdict.passed and any("黑色背景" in p and "不能以任务单冲突豁免" in p for p in verdict.problems)


def test_graphic_watermark_still_fails_although_text_ban_is_advisory(tmp_path):
    review = rv(present={"未请求的文字或水印", MARK_BAN_ITEM}, why="右下角有半透明 logo 水印")
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert not verdict.passed and any(MARK_BAN_ITEM in p for p in verdict.problems)
    assert MARK_BAN_ITEM in review_prompt(CONTRACT, EXPECTED, "native")


def test_textless_image_rejects_even_a_single_letter(tmp_path):
    review = rv(transcript=["A"])
    verdict = judge(img(tmp_path), CONTRACT, review, [], "none")
    assert not verdict.passed and any("不该有字" in p for p in verdict.problems)


def test_verified_task_conflict_excuses_contract_item_and_is_recorded(tmp_path):
    review = rv(fail_must={MUST_GHOST}, conflict={MUST_GHOST: claim()})
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert verdict.passed, verdict.problems
    record, = verdict.triage["conflicts"]
    assert record["item"] == MUST_GHOST and record["task_quote"] == GHOST_BAN and "禁止影字" in record["reason"]


@pytest.mark.parametrize("quote,why_rejected", [
    ("Behind the headline always draw a big English ghost word", "没有出现在本次任务单"),  # 伪造原句
    ("影字", "过短"),  # 太短，等于没引用
    ("", "过短"),
])
def test_forged_or_trivial_conflict_quote_is_rejected(tmp_path, quote, why_rejected):
    review = rv(fail_must={MUST_GHOST}, conflict={MUST_GHOST: claim(quote)})
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert not verdict.passed
    assert any(MUST_GHOST in p and "冲突声明被拒" in p and why_rejected in p for p in verdict.problems)
    assert verdict.triage["conflicts_rejected"] and not verdict.triage["conflicts"]


def test_quote_from_the_style_contract_is_not_task_sheet_text(tmp_path):
    """合同条款本身不是任务单：拿合同原句充当『任务单原句』必须被拒。"""
    review = rv(fail_must={MUST_GHOST}, conflict={MUST_GHOST: claim(MUST_GHOST)})
    assert not judge(img(tmp_path), CONTRACT, review, EXPECTED, "native").passed


def test_conflict_claims_cannot_excuse_most_of_the_contract(tmp_path):
    contract = {**CONTRACT, "qa": {**CONTRACT["qa"], "must_see": [MUST_GHOST, "笔记本完整", "画面明亮"]}}
    review = rv(contract, fail_must={MUST_GHOST, "笔记本完整"},
                conflict={MUST_GHOST: claim(), "笔记本完整": claim("laptop centred in the square crop")})
    verdict = judge(img(tmp_path), contract, review, EXPECTED, "native")
    assert not verdict.passed and any("不适用的条目过多" in p for p in verdict.problems)


def test_conflict_claim_format_is_validated():
    review = rv(fail_must={MUST_GHOST}, conflict={MUST_GHOST: {"task_quote": GHOST_BAN}})
    assert any("冲突声明" in g for g in missing_items(review, CONTRACT, "native"))
    assert check_conflict({"conflict": claim()}, [GHOST_BAN])[0]
    assert not check_conflict({"conflict": claim()}, ["something else entirely"])[0]


def test_prompt_states_precedence_and_audit_rule():
    prompt = review_prompt(CONTRACT, EXPECTED, "native")
    assert "优先于画风合同" in prompt and "task_quote" in prompt and "校验不过则声明作废" in prompt


# ---- 4. points 口径 ----------------------------------------------------------------

def test_points_are_meaning_checks_not_literal_text_and_not_in_content_match():
    assert "清理前先问四个问题" not in CONTRACT["_content_expectation"]
    assert CONTRACT["_content_point_expectations"] == CONTENT["points"]
    prompt = review_prompt(CONTRACT, EXPECTED, "native")
    section = prompt.split("## 必要信息逐条核对")[1].split("##")[0]
    assert "不自动成为图中文字" in section and "精确字样只由" in section
    assert "按未通过" not in prompt and "需要精确文字而画面无法证实" not in prompt


def test_point_conveyed_by_icons_passes_even_when_words_are_not_on_the_image(tmp_path):
    review = rv(transcript=EXPECTED, point_ok=True)
    assert not any(point in "".join(review["transcribed_text"]) for point in CONTENT["points"])
    assert judge(img(tmp_path), CONTRACT, review, EXPECTED, "native").passed


def test_point_with_missing_meaning_still_fails(tmp_path):
    review = rv(point_ok=False)
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert not verdict.passed and any("必要信息不符" in p for p in verdict.problems)


def test_points_alone_no_longer_force_a_content_match_answer():
    contract = {**BASE, **content_expectations({"points": ["只有要点"]})}
    assert "_content_expectation" not in contract and "content_match" not in review_schema(contract)["required"]


# ---- 5. 内容分项、失败子句、自相矛盾 ------------------------------------------------

def test_content_match_is_split_into_atomic_parts():
    assert [p["key"] for p in CONTRACT["_content_parts"]] == ["subject", "relations", "inventory"]
    prompt = review_prompt(CONTRACT, EXPECTED, "native")
    assert "[relations]" in prompt and "failed_clause" in prompt
    props = review_schema(CONTRACT)["properties"]["content_match"]["properties"]
    assert "parts" in props and props["parts"]["items"]["properties"]["key"]["enum"] == ["subject", "relations", "inventory"]


def test_failed_part_must_cite_a_clause_from_the_requirement(tmp_path):
    ok = rv(cm_fail={"inventory": "macOS 徽章"})
    verdict = judge(img(tmp_path), CONTRACT, ok, EXPECTED, "native")
    assert not verdict.passed and any("内容不符（物件清单）" in p and "macOS 徽章" in p for p in verdict.problems)
    assert verdict.triage["next_action"] == "diagnose_before_repair"


def test_failed_part_without_locatable_clause_is_pending_review_not_a_confirmed_failure(tmp_path):
    review = rv(cm_fail={"relations": "这句话要求里根本没有"})
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert not verdict.passed
    assert verdict.triage["next_action"] == "complete_review"
    assert any("待复核" in e for e in verdict.triage["process_errors"])
    assert not any(p.startswith("内容不符") for p in verdict.problems)


def test_strict_review_without_failed_clause_or_parts_is_incomplete():
    review = rv(cm_fail={"subject": "磁盘清理封面"})
    del review["content_match"]["parts"][0]["failed_clause"]
    assert any("failed_clause" in g for g in missing_items(review, CONTRACT, "native"))
    review = rv()
    del review["content_match"]["parts"]
    assert any("content_match.parts" in g for g in missing_items(review, CONTRACT, "native"))
    legacy = rv(schema=False)
    del legacy["content_match"]["parts"]
    assert not missing_items(legacy, CONTRACT, "native")  # 历史/手工结论仍按旧口径读取


def test_overall_false_with_all_parts_true_is_flagged_for_review_not_used_as_failure_reason(tmp_path):
    review = rv()
    review["content_match"]["ok"] = False
    review["content_match"]["why"] = "这一禁止性要求满足"
    verdict = judge(img(tmp_path), CONTRACT, review, EXPECTED, "native")
    assert not verdict.passed
    assert verdict.triage["next_action"] == "complete_review"
    assert not any(p.startswith("内容不符") for p in verdict.problems)
    assert any("不一致" in p and "待复核" in p for p in verdict.problems)


# ---- 1. 聚合 ---------------------------------------------------------------------

def three(*variants):
    return aggregate_reviews([copy.deepcopy(v) for v in variants])


def test_minority_failure_is_outvoted_but_recorded_and_majority_failure_fails(tmp_path):
    good, bad = rv(why="看到"), rv(fail_must={"笔记本完整"}, why="笔记本被裁切")
    merged = three(good, bad, good)
    row = next(r for r in merged["must_see"] if r["item"] == "笔记本完整")
    assert row["ok"] is True and row["sample_verdicts"] == ["pass", "fail", "pass"]
    assert merged["_aggregation"]["rule"] == "majority_after_escalation"
    assert {"field": "must_see", "item": "笔记本完整", "verdicts": ["pass", "fail", "pass"]} in merged["_aggregation"]["disagreements"]
    assert merged["_samples"][1]["must_see"][2]["why"] == "笔记本被裁切"  # 少数意见原样留在报告里
    assert judge(img(tmp_path), CONTRACT, merged, EXPECTED, "native").passed
    # 反例：过半判失败就失败，且保留所有判失败那几次的理由
    worse = three(bad, bad, good)
    row = next(r for r in worse["must_see"] if r["item"] == "笔记本完整")
    assert row["ok"] is False and "3 次采样中 2 次判失败" in row["why"]
    assert not judge(img(tmp_path), CONTRACT, worse, EXPECTED, "native").passed
    # 平票判失败（例如配置成 2 次采样）
    tie = aggregate_reviews([copy.deepcopy(good), copy.deepcopy(bad)])
    assert next(r for r in tie["must_see"] if r["item"] == "笔记本完整")["ok"] is False
    clean = three(good, good, good)
    assert judge(img(tmp_path), CONTRACT, clean, EXPECTED, "native").passed
    assert clean["_aggregation"]["disagreements"] == []

def test_hallucination_in_one_sample_cannot_be_voted_away(tmp_path):
    """通过的那次幻觉出不存在的影字：另两次未见则按保守规则失败，而不是 2:1 放行。"""
    hallucinated = rv(why="看到半隐英文影字")
    honest = rv(fail_must={MUST_GHOST}, why="标题后没有影字")
    merged = three(hallucinated, honest, honest)
    assert not judge(img(tmp_path), CONTRACT, merged, EXPECTED, "native").passed


def test_ban_item_follows_the_majority_of_samples():
    row = lambda m: next(r for r in m["must_not_see"] if r["item"] == "黑色背景")["present"]
    assert row(three(rv(), rv(present={"黑色背景"}), rv())) is False
    assert row(three(rv(present={"黑色背景"}), rv(present={"黑色背景"}), rv())) is True

def test_na_in_one_sample_does_not_waive_a_failure_in_another():
    a = rv()
    a["must_see"][0]["applicable"] = False
    b = rv(fail_must={a["must_see"][0]["item"]})
    row = three(a, b)["must_see"][0]
    assert row["ok"] is False and row["applicable"] is True


def test_conflict_waiver_needs_every_failing_sample_to_claim_it(tmp_path):
    claimed = rv(fail_must={MUST_GHOST}, conflict={MUST_GHOST: claim()})
    silent = rv(fail_must={MUST_GHOST})
    passing_claim = rv(conflict={MUST_GHOST: claim()})
    row = lambda m: next(r for r in m["must_see"] if r["item"] == MUST_GHOST)
    # 一次采样判出缺陷却没提冲突：多数声明也不能盖掉它
    for merged in (three(claimed, claimed, silent), three(silent, silent, passing_claim)):
        assert "conflict" not in row(merged)
        assert not judge(img(tmp_path), CONTRACT, merged, EXPECTED, "native").passed
    unanimous = three(claimed, claimed, claimed)
    assert len(row(unanimous)["conflict"]["claims"]) == 3
    assert judge(img(tmp_path), CONTRACT, unanimous, EXPECTED, "native").passed
    # 每条声明都逐条核对原文：其中一条是伪造原句就整体不豁免
    forged = rv(fail_must={MUST_GHOST}, conflict={MUST_GHOST: claim("任务单里根本没有这一句话")})
    assert not judge(img(tmp_path), CONTRACT, three(claimed, forged, claimed), EXPECTED, "native").passed


def test_each_sample_transcript_is_compared_not_just_the_first(tmp_path):
    clean, noisy = rv(), rv(transcript=EXPECTED + ["多出来的字"])
    verdict = judge(img(tmp_path), CONTRACT, three(clean, noisy, clean), EXPECTED, "native")
    assert not verdict.passed and any(p.startswith("采样2：") and "多写了文字" in p for p in verdict.problems)
    assert judge(img(tmp_path), CONTRACT, three(clean, clean), EXPECTED, "native").passed


def test_content_parts_and_overall_are_aggregated_consistently_and_sample_contradiction_is_kept(tmp_path):
    fine, bad = rv(), rv(cm_fail={"inventory": "macOS 徽章"})
    assert three(fine, bad, fine)["content_match"]["ok"] is True  # 少数意见不定结论
    merged = three(bad, bad, fine)
    cm = merged["content_match"]
    assert cm["ok"] is False and [p["ok"] for p in cm["parts"]] == [True, True, False]
    assert cm["parts"][2]["failed_clause"] == "macOS 徽章"
    contradiction = rv()
    contradiction["content_match"]["ok"] = False
    merged = three(fine, contradiction, fine)
    assert merged["_aggregation"]["contradictions"][0]["sample"] == 2
    verdict = judge(img(tmp_path), CONTRACT, merged, EXPECTED, "native")
    assert not verdict.passed and verdict.triage["next_action"] == "complete_review"


def test_unverified_observation_keeps_pending_semantics():
    unsure = rv()
    unsure["must_see"][0]["observation"] = "unverified"
    assert three(unsure, rv(), rv())["must_see"][0]["observation"] == "unverified"
    failing = rv(fail_must={unsure["must_see"][0]["item"]})
    failing["must_see"][0]["observation"] = "unverified"
    confirmed_fail = rv(fail_must={unsure["must_see"][0]["item"]})
    assert three(failing, confirmed_fail)["must_see"][0]["observation"] == "confirmed"


def test_aggregated_review_round_trips_through_json_and_rejudges_identically(tmp_path):
    """写作 Skill 会用报告里的 review 重新 judge 并要求 verdict 逐字相同。"""
    merged = three(rv(), rv(fail_must={"笔记本完整"}), rv(present={"未请求的文字或水印"}))
    first = judge(img(tmp_path), CONTRACT, merged, EXPECTED, "native").to_dict()
    again = judge(img(tmp_path), CONTRACT, json.loads(json.dumps(merged)), EXPECTED, "native").to_dict()
    assert json.loads(json.dumps(first)) == first == again
    assert not missing_items(merged, CONTRACT, "native")


# ---- reviewer 层：采样次数、温度、不完整采样 ------------------------------------------

@pytest.fixture
def route(monkeypatch):
    monkeypatch.delenv("SANSHENG_IMAGE_QA_BACKEND", raising=False)
    monkeypatch.setenv("ARK_CODING_PLAN_BASE_URL", coding_plan.BASE_URL)
    monkeypatch.setenv("ARK_CODING_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(reviewer, "_claude_bin", lambda: pytest.fail("default started Claude"))


def serve(monkeypatch, answers):
    """依次返回 answers 里的回答（dict 或字符串）；记录每次请求体。"""
    sent, queue = [], list(answers)

    def open_request(req, **kwargs):
        sent.append(json.loads(req.data))
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        content = json.dumps(answer, ensure_ascii=False) if isinstance(answer, dict) else answer
        body = {"model": "deepseek-v4-1-flash", "choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(coding_plan.urllib.request, "urlopen", open_request)
    return sent


def model_answer(contract=CONTRACT, **kw):
    answer = rv(contract, schema=False, **kw)
    for prefix, key in (("M", "must_see"), ("B", "must_not_see")):
        for i, row in enumerate(answer[key], 1):
            row["id"] = f"{prefix}{i}"
            del row["item"]
    return answer


def test_default_is_three_independent_samples_aggregated(route, tmp_path, monkeypatch):
    monkeypatch.delenv("SANSHENG_IMAGE_QA_SAMPLES")
    sent = serve(monkeypatch, [model_answer()] * 3)
    result = reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")
    assert len(sent) == 3 and len(result["_samples"]) == 3  # 一致就不加看
    assert result["_aggregation"]["samples"] == 3 and result["_review_schema"] == 2
    assert result["_reviewer"] == "ark_coding_plan:deepseek-v4-1-flash"
    if "temperature" in inspect.signature(coding_plan.request).parameters:
        assert all(body["temperature"] == reviewer.DEFAULT_SAMPLE_TEMPERATURE for body in sent)


def test_disagreement_escalates_to_five_samples_and_majority_decides(route, tmp_path, monkeypatch):
    monkeypatch.delenv("SANSHENG_IMAGE_QA_SAMPLES")
    bad = model_answer(fail_must={"笔记本完整"}, why="被裁")
    sent = serve(monkeypatch, [model_answer(), bad, model_answer(), bad, model_answer()])
    result = reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")
    assert len(sent) == 5 and result["_aggregation"]["escalated_from"] == 3
    assert next(r for r in result["must_see"] if r["item"] == "笔记本完整")["ok"] is True  # 2/5
    sent = serve(monkeypatch, [model_answer(), bad, model_answer(), bad, bad])
    result = reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")
    assert next(r for r in result["must_see"] if r["item"] == "笔记本完整")["ok"] is False  # 3/5
    monkeypatch.setenv("SANSHENG_IMAGE_QA_ESCALATE", "0")
    sent = serve(monkeypatch, [model_answer(), bad, model_answer()])
    reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")
    assert len(sent) == 3

@pytest.mark.parametrize("value,count", [("1", 1), ("0", 1), ("-4", 1), ("2", 2)])
def test_sample_count_is_configurable_with_minimum_one(route, tmp_path, monkeypatch, value, count):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_SAMPLES", value)
    sent = serve(monkeypatch, [model_answer()])
    assert len(reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")["_samples"]) == count == len(sent)


def test_bad_sample_count_setting_is_an_error_not_a_silent_default(route, tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_SAMPLES", "many")
    serve(monkeypatch, [model_answer()])
    with pytest.raises(ValueError, match="SANSHENG_IMAGE_QA_SAMPLES"):
        reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")


def test_single_sample_sends_no_temperature_override(route, tmp_path, monkeypatch):
    sent = serve(monkeypatch, [model_answer()])
    reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")
    assert len(sent) == 1 and "temperature" not in sent[0] or sent[0]["temperature"] == 0


def test_an_incomplete_later_sample_stops_the_round_and_keeps_earlier_ones(route, tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_SAMPLES", "3")
    sent = serve(monkeypatch, [model_answer(), {"must_see": [], "must_not_see": []}])
    with pytest.raises(reviewer.ReviewerIncompleteResult) as error:
        reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")
    assert len(sent) == 2 and len(error.value.review["_completed_samples"]) == 1
    assert error.value.review["_incomplete_review"]


def test_model_answer_without_content_parts_or_failed_clause_is_incomplete(route, tmp_path, monkeypatch):
    answer = model_answer()
    del answer["content_match"]["parts"]
    serve(monkeypatch, [answer])
    with pytest.raises(reviewer.ReviewerIncompleteResult) as error:
        reviewer.review(img(tmp_path), CONTRACT, EXPECTED, "native")
    assert any("content_match.parts" in g for g in error.value.review["_incomplete_review"])
    # 带着缺项交给 judge 时仍被拒绝，不会因为缺 _review_schema 走宽松口径
    assert not judge(img(tmp_path), CONTRACT, error.value.review, EXPECTED, "native").passed


def test_end_to_end_default_flow_passes_clean_image_and_rejects_one_bad_sample(route, tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_SAMPLES", "3")
    image = img(tmp_path)
    serve(monkeypatch, [model_answer()])
    assert judge(image, CONTRACT, reviewer.review(image, CONTRACT, EXPECTED, "native"), EXPECTED, "native").passed
    serve(monkeypatch, [model_answer(), model_answer(transcript=EXPECTED + ["影"], present={"黑色背景"}), model_answer()])
    verdict = judge(image, CONTRACT, reviewer.review(image, CONTRACT, EXPECTED, "native"), EXPECTED, "native")
    # 逐字转录仍是每次各自比对：一次多写了字就不通过；禁止项只被一次看到则按多数不算
    assert not verdict.passed and any("多写了文字" in p for p in verdict.problems)
    assert not any("黑色背景" in p for p in verdict.problems)


def test_default_model_reports_the_backend_that_is_actually_used(route, monkeypatch):
    assert reviewer.default_model() == coding_plan.MODEL
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    assert reviewer.default_model() == reviewer.DEFAULT_MODEL
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "ark_agent_plan")
    assert reviewer.default_model() == reviewer.ARK_MODEL


def test_new_reviews_must_answer_the_mark_item_but_legacy_ones_need_not(tmp_path):
    strict = rv()
    strict["must_not_see"] = [r for r in strict["must_not_see"] if r["item"] != MARK_BAN_ITEM]
    assert MARK_BAN_ITEM in missing_items(strict, CONTRACT, "native")
    legacy = {k: v for k, v in strict.items() if k != "_review_schema"}
    assert MARK_BAN_ITEM not in missing_items(legacy, CONTRACT, "native")


def test_answer_to_the_mark_item_is_a_known_id_even_for_unmarked_reviews():
    answer = model_answer()
    legacy = {k: v for k, v in answer.items() if k != "_review_schema"}
    for row, label in zip(legacy["must_not_see"], active_items(CONTRACT, "native")[1]):
        row["item"] = label
    assert not any("B4" in gap or "B3" in gap for gap in missing_items(legacy, CONTRACT, "native"))


def test_prompt_pins_content_part_keys():
    prompt = review_prompt(CONTRACT, EXPECTED, "native")
    assert "key 只能原样使用方括号里的分项名（subject、relations、inventory）" in prompt
