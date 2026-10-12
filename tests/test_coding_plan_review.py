import base64
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stylebook.qa import coding_plan, reviewer, plan_review


@pytest.fixture
def route(monkeypatch):
    monkeypatch.delenv("SANSHENG_IMAGE_QA_BACKEND", raising=False)
    monkeypatch.delenv("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND", raising=False)
    monkeypatch.setenv("ARK_CODING_PLAN_BASE_URL", coding_plan.BASE_URL)
    monkeypatch.setenv("ARK_CODING_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(reviewer, "_claude_bin", lambda: pytest.fail("default started Claude"))
    monkeypatch.setattr(plan_review, "_claude_bin", lambda: pytest.fail("default started Claude"))


def mock_response(monkeypatch, answer, *, model="deepseek-v4-1-flash", finish="stop"):
    requests = []
    response = {"model": model, "choices": [{"finish_reason": finish,
                "message": {"content": json.dumps(answer) if isinstance(answer, dict) else answer}}]}
    def open_request(req, **kwargs):
        requests.append((req.full_url, json.loads(req.data)))
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr(coding_plan.urllib.request, "urlopen", open_request)
    return requests


def test_default_visual_consumer_sends_exact_image_and_retains_source(route, tmp_path, monkeypatch):
    image = tmp_path / "x.png"
    Image.new("RGB", (97, 73), "red").save(image)
    recipe = {"positive": "compressed flat bands with no vanishing-point depth",
              "hard_constraints": ["single fluent brush contours"], "text_mode": ["none"]}
    isolation = "Take material only; never copy the board's people, panels or writing."
    contract = {"qa": {"must_see": ["红色主体"], "must_not_see": ["文字"]},
                "recipe": recipe, "anchor": {"isolation": isolation}}
    answer = {"must_see": [{"item": "红色主体", "ok": True, "why": "看到"}],
              "must_not_see": [{"item": "文字", "present": False, "why": "无"}], "transcribed_text": []}
    requests = mock_response(monkeypatch, answer)
    result = reviewer.review(image, contract)
    assert result["_reviewer"] == "ark_coding_plan:deepseek-v4-1-flash"
    url, payload = requests[0]
    assert url == coding_plan.BASE_URL + "/chat/completions"
    assert payload["model"] == coding_plan.MODEL
    prompt = payload["messages"][0]["content"][0]["text"]
    assert json.dumps(recipe, ensure_ascii=False) in prompt
    assert isolation in prompt
    uri = payload["messages"][0]["content"][2]["image_url"]["url"]
    assert base64.b64decode(uri.split(",", 1)[1]) == image.read_bytes()
    assert len(requests) == 1


def test_default_plan_consumer_includes_actual_article(route, tmp_path, monkeypatch):
    article = tmp_path / "a.md"
    article.write_text("来源：先冷却五分钟再密封。")
    requests = mock_response(monkeypatch, {"items": [], "missed_positions": []})
    result = plan_review.review({"items": []}, article)
    assert result["_reviewer"].startswith("ark_coding_plan:")
    assert article.read_text() in requests[0][1]["messages"][0]["content"][0]["text"]


@pytest.mark.parametrize("base,key,model", [("https://example.com/api/v3", "test", None),
                                            (coding_plan.BASE_URL, "", None),
                                            (coding_plan.BASE_URL, "test", "claude-sonnet-5")])
def test_bad_route_or_model_stops_before_request(route, monkeypatch, base, key, model):
    monkeypatch.setenv("ARK_CODING_PLAN_BASE_URL", base)
    monkeypatch.setenv("ARK_CODING_PLAN_API_KEY", key)
    monkeypatch.setattr(coding_plan.urllib.request, "urlopen", lambda *a, **k: pytest.fail("sent forbidden request"))
    with pytest.raises((reviewer.ReviewerAuthUnavailable, ValueError)):
        coding_plan.request([], model=model, timeout=1)


@pytest.mark.parametrize("answer,model,finish", [("", "deepseek-v4-1-flash", "stop"),
                                                ("{}", "other-model", "stop"),
                                                ("{}", "deepseek-v4-1-flash", "length")])
def test_empty_truncated_or_drift_response_fails(route, monkeypatch, answer, model, finish):
    requests = mock_response(monkeypatch, answer, model=model, finish=finish)
    with pytest.raises(RuntimeError):
        coding_plan.request([], model=None, timeout=1)
    assert len(requests) == 1


@pytest.mark.parametrize("error,expected", [
    (urllib.error.HTTPError("x", 429, "limit", {}, None), reviewer.QuotaExhausted),
    (urllib.error.HTTPError("x", 401, "auth", {}, None), reviewer.ReviewerAuthUnavailable),
    (urllib.error.URLError("offline"), reviewer.ReviewerNetworkUnavailable)])
def test_failure_does_not_retry_or_fallback(route, monkeypatch, error, expected):
    calls = []
    def fail(*a, **k):
        calls.append(1)
        raise error
    monkeypatch.setattr(coding_plan.urllib.request, "urlopen", fail)
    with pytest.raises(expected):
        coding_plan.request([], model=None, timeout=1)
    assert calls == [1]


def test_incomplete_visual_result_cannot_pass(route, tmp_path, monkeypatch):
    image = tmp_path / "x.png"
    Image.new("RGB", (32, 32), "red").save(image)
    requests = mock_response(monkeypatch, {"must_see": [], "must_not_see": []})
    with pytest.raises(reviewer.ReviewerIncompleteResult, match="完整逐项") as error:
        reviewer.review(image, {"qa": {"must_see": ["红色主体"], "must_not_see": ["文字"]}})
    assert len(requests) == 1
    assert error.value.review["_response_metadata"]["model"] == "deepseek-v4-1-flash"
    assert error.value.review["_incomplete_review"]
    assert error.value.review["must_see"] == []


def test_actual_qa_consumer_records_incomplete_review(route, tmp_path, monkeypatch):
    import sb

    image = tmp_path / "x.png"
    Image.new("RGB", (128, 128), "white").save(image)
    report = tmp_path / "failed-qa.json"
    requests = mock_response(monkeypatch, {"must_see": [], "must_not_see": []})
    monkeypatch.setattr(sys, "argv", ["sb.py", "qa", str(image), "--style", "C96",
                                    "--report", str(report)])
    assert sb.main() == 1
    result, = json.loads(report.read_text())
    assert result["passed"] is False
    assert result["triage"]["next_action"] == "complete_review"
    assert result["triage"]["automatic_regeneration"] is False
    assert result["binding"]
    assert result["review"]["_response_metadata"]["model"] == "deepseek-v4-1-flash"
    assert result["review"]["_incomplete_review"]
    assert len(requests) == 1


@pytest.mark.parametrize("answer", ["{}", "not-json"])
def test_nonempty_unparseable_visual_result_keeps_response(route, tmp_path, monkeypatch, answer):
    image = tmp_path / "x.png"
    Image.new("RGB", (32, 32), "red").save(image)
    requests = mock_response(monkeypatch, answer)
    with pytest.raises(reviewer.ReviewerIncompleteResult) as error:
        reviewer.review(image, {"qa": {"must_see": ["红色主体"], "must_not_see": ["文字"]}})
    assert error.value.review["_incomplete_review"]
    assert error.value.review["_response_metadata"]["choices"][0]["message"]["content"] == answer
    assert len(requests) == 1


def test_clause_ids_bind_full_requirements_without_changing_verdicts(route, tmp_path, monkeypatch):
    image = tmp_path / "x.png"
    Image.new("RGB", (32, 32), "red").save(image)
    label = "无字任务的完整长条款；禁止文字、假字、签名及边框中的额外文字"
    contract = {"qa": {"must_see": ["红色主体"], "must_not_see": [label]}}
    answer = {"must_see": [{"id": "M1", "ok": True, "why": "看到红色"}],
              "must_not_see": [{"id": "B1", "present": True, "why": "边框有签名"}],
              "transcribed_text": ["签名"]}
    requests = mock_response(monkeypatch, answer)
    result = reviewer.review(image, contract)
    assert result["must_not_see"][0]["item"] == label
    assert result["must_not_see"][0]["present"] is True
    prompt = requests[0][1]["messages"][0]["content"][0]["text"]
    assert "[B1] " + label in prompt
    assert "固定id" in prompt
    assert result["_response_metadata"]["choices"][0]["message"]["content"] == json.dumps(answer)
    assert len(requests) == 1


@pytest.mark.parametrize("bad", [
    [{"id": "B99", "present": False, "why": "无"}],
    [{"id": "B1", "present": False, "why": "无"}, {"id": "B1", "present": False, "why": "无"}],
    [{"id": "B1", "item": "其他条款", "present": False, "why": "无"}],
    [{"id": "M1", "present": False, "why": "无"}],
    [],
])
def test_bad_clause_ids_cannot_be_completed_by_guessing(route, tmp_path, monkeypatch, bad):
    image = tmp_path / "x.png"
    Image.new("RGB", (32, 32), "red").save(image)
    answer = {"must_see": [{"id": "M1", "ok": True, "why": "看到"}],
              "must_not_see": bad, "transcribed_text": []}
    requests = mock_response(monkeypatch, answer)
    with pytest.raises(reviewer.ReviewerIncompleteResult):
        reviewer.review(image, {"qa": {"must_see": ["红色主体"], "must_not_see": ["文字"]}})
    assert len(requests) == 1


def test_actual_consumer_rejects_extra_unknown_id_even_when_all_items_answered(route, tmp_path, monkeypatch):
    import sb
    from stylebook import contract as CT
    from stylebook.qa import active_items

    image = tmp_path / "x.png"
    Image.new("RGB", (128, 128), "red").save(image)
    must, banned = active_items(CT.load("C96"), "none")
    answer = {"must_see": [{"id": f"M{i}", "ok": True, "why": "实际可见"} for i in range(1, len(must)+1)],
              "must_not_see": [{"id": f"B{i}", "present": False, "why": "实际未见"} for i in range(1, len(banned)+1)],
              "transcribed_text": []}
    answer["must_not_see"].append({"id": "B99", "present": False, "why": "多余未知标识"})
    requests = mock_response(monkeypatch, answer)
    report = tmp_path / "failed.json"
    monkeypatch.setattr(sys, "argv", ["sb.py", "qa", str(image), "--style", "C96", "--report", str(report)])
    assert sb.main() == 1
    result, = json.loads(report.read_text())
    assert any("B99" in p for p in result["problems"])
    assert result["triage"]["next_action"] == "complete_review"
    assert len(requests) == 1
