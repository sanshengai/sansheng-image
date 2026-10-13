import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stylebook.qa import plan_review as PR


class Reply:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.response).encode()


def setup_review(monkeypatch, tmp_path, response):
    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", PR.ARK_PLAN_URL)
    monkeypatch.setenv("ARK_AGENT_PLAN_API_KEY", "test-only-key")
    monkeypatch.delenv("SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS", raising=False)
    article = tmp_path / "article.md"
    article.write_text("只读原文")
    monkeypatch.setattr(PR, "prompt", lambda plan, path, mode="inline", **kw: "逐张检查")
    sent = []

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        return Reply(response)

    monkeypatch.setattr(PR.urllib.request, "urlopen", urlopen)
    return article, sent


def test_multi_page_truncation_is_rejected_with_diagnostics(monkeypatch, tmp_path):
    response = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 10000, "output_tokens": 9500, "total_tokens": 19500},
                "output": [{"content": [{"type": "output_text", "text": '{"items":[]}'}]}]}
    article, sent = setup_review(monkeypatch, tmp_path, response)
    with pytest.raises(RuntimeError, match="incomplete") as error:
        PR._review_ark({"items": [{"id": str(i)} for i in range(8)]}, article, None, 1)
    assert sent[0]["max_output_tokens"] > 5000
    assert '"output_tokens": 9500' in str(error.value)
    assert '"incomplete_reason": "max_output_tokens"' in str(error.value)
    assert "test-only-key" not in str(error.value)


def test_completed_but_missing_pages_still_rejected(monkeypatch, tmp_path):
    response = {"status": "completed", "model": PR.ARK_MODEL,
                "output": [{"content": [{"type": "output_text", "text":
                    json.dumps({"items": [], "missed_positions": []})}]}]}
    article, _ = setup_review(monkeypatch, tmp_path, response)
    with pytest.raises(RuntimeError, match="没有给出完整结论"):
        PR._review_ark({"items": [{"id": "01"}]}, article, None, 1)


def test_completed_full_review_retains_usage_and_explicit_budget(monkeypatch, tmp_path):
    item = {key: True for key in ("reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
                                 "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok")}
    item.update(id="01", why="原文与单页一致")
    response = {"status": "completed", "model": PR.ARK_MODEL, "usage": {"input_tokens": 100},
                "output": [{"content": [{"type": "output_text", "text":
                    json.dumps({"items": [item], "missed_positions": []})}]}]}
    article, sent = setup_review(monkeypatch, tmp_path, response)
    monkeypatch.setenv("SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS", "12000")
    result = PR._review_ark({"items": [{"id": "01"}]}, article, None, 1)
    assert sent[0]["max_output_tokens"] == 12000
    assert result["items"][0]["reasonable"] is True
    assert result["_response_metadata"]["usage"] == {"input_tokens": 100}


@pytest.mark.parametrize("value", ["", "abc", "0", "32001"])
def test_invalid_budget_does_not_send_request(monkeypatch, tmp_path, value):
    article, sent = setup_review(monkeypatch, tmp_path, {})
    monkeypatch.setenv("SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS", value)
    with pytest.raises(ValueError, match="2000–32000"):
        PR._review_ark({"items": []}, article, None, 1)
    assert sent == []
