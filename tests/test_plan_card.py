"""图上文字来源校验与计划卡落盘（B0）。每条规则都配一个会被拦下的反例。"""
import copy
import hashlib
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import backends as B  # noqa: E402
from stylebook import batch as BT  # noqa: E402
from stylebook import contract as CT  # noqa: E402
from stylebook import make as MK, profile as P  # noqa: E402
from stylebook import plan_card as PC  # noqa: E402

ARTICLE = "去年团队把交付周期从 21 天缩到 9 天。负责人说：「先把最慢的一步找出来」。我们用 Notion 管需求，增长了 37.5%。"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setenv("SANSHENG_IMAGE_PREFERENCES", str(tmp_path / "prefs.json"))
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "absent-profile"))
    monkeypatch.setattr(P, "lookup", lambda *args, **kwargs: None)
    monkeypatch.setattr(CT.Path, "home", staticmethod(lambda: tmp_path))


@pytest.mark.parametrize("text", ["交付从 21 天缩到 9 天", "增长 37.5%", "「先把最慢的一步找出来」", "用 Notion 管需求", "3 步提速", "A VS B"])
def test_text_found_in_source_passes(text):
    assert PC.problems(text, ARTICLE) == []


@pytest.mark.parametrize("text, word", [
    ("交付缩短了 57%", "57"),                 # 自己算的数字
    ("周期缩到 7 天以内", None),              # 个位数不查，不算反例
    ("「先找最慢的一步」", "先找最慢的一步"),  # 改写过的引语
    ("我们用 Jira 管需求", "Jira"),           # 原文没有的英文专名
    ("增长 1,200 人", "1,200"),
])
def test_text_missing_from_source_is_rejected(text, word):
    got = PC.problems(text, ARTICLE)
    if word is None:
        assert got == []
    else:
        assert got and word in got[0]


def test_derived_number_needs_a_method():
    spec = {"items": [{"role": "title", "text": "交付缩短了 57%", "derived": "(21-9)/21≈57%"}]}
    assert PC.check_items(spec, ARTICLE) == []
    assert PC.check_items({"items": [{"text": "交付缩短了 57%", "derived": " "}]}, ARTICLE)   # 空说明不放行
    assert PC.check_items({"items": [{"text": "交付缩短了 57%"}]}, ARTICLE)
    # derived 只豁免数字，不豁免编造的引语
    assert PC.check_items({"items": [{"text": "「先找最慢的一步」57%", "derived": "(21-9)/21≈57%"}]}, ARTICLE)


def test_no_source_means_no_check():
    assert PC.check_items({"items": [{"text": "增长 999%"}]}, None) == []


@pytest.mark.parametrize("text", ["一张图看懂交付提速", "如下图所示", "往下滑看答案", "记得收藏"])
def test_guide_phrases_are_rejected_even_without_source(text):
    assert PC.check_items({"items": [{"text": text}]}, None)
    assert PC.check_items({"items": [{"text": text}]}, ARTICLE)


def test_plain_conclusion_is_not_a_guide_phrase():
    assert PC.check_items({"items": [{"text": "先找最慢的一步，再谈提速"}]}, None) == []


BRIEF = {"version": 1, "scene": "wxillus", "style": "C42", "reason": "清楚表现文章中的提速",
         "items": [{"position": "去年团队把交付周期从 21 天缩到 9 天", "message": "交付变快", "visual": "Two calendars", "text": ["从 21 天到 9 天"]}]}


def _src(tmp_path):
    (tmp_path / "article.md").write_text(ARTICLE, encoding="utf-8")
    return {**copy.deepcopy(BRIEF), "source": "article.md"}


def test_make_rejects_fabricated_number_and_writes_plan_card(tmp_path):
    brief = _src(tmp_path)
    MK.run(brief, tmp_path / "ok", base_path=tmp_path, prepare_only=True)
    card = (tmp_path / "ok" / PC.CARD_NAME).read_text(encoding="utf-8")
    assert "从 21 天到 9 天" in card and "未记录" in card
    bad = copy.deepcopy(brief)
    bad["items"][0]["text"] = ["缩短了 57%"]
    with pytest.raises(MK.BriefError, match="57"):
        MK.prepare(bad, base_path=tmp_path)
    skipped = {**copy.deepcopy(brief), "confirmed": "skipped"}
    MK.run(skipped, tmp_path / "skip", base_path=tmp_path, prepare_only=True)
    assert "已跳过确认" in (tmp_path / "skip" / PC.CARD_NAME).read_text(encoding="utf-8")
    with pytest.raises(MK.BriefError, match="confirmed"):
        MK.prepare({**copy.deepcopy(brief), "confirmed": "yes"}, base_path=tmp_path)


def _v2_plan(tmp_path, text):
    (tmp_path / "article.md").write_text(ARTICLE, encoding="utf-8")
    sha = hashlib.sha256((tmp_path / "article.md").read_bytes()).hexdigest()
    it = {"id": "01", "position": "第 1 页", "shape": "steps", "form": "structure", "structure": "flow",
          "points": ["找最慢一步", "改掉", "复盘"], "what": "提速三步", "subject": "three steps on a card",
          "why": "这一页讲先后三步，读者要看到顺序", "source_quote": "先把最慢的一步找出来",
          "text": {"mode": "native", "items": [{"role": "title", "text": text}]}}
    return {"version": 2, "scene": "xhs", "source": {"path": "article.md", "sha256": sha},
            "style": {"code": "C42", "source": "factory", "why": "小红书出厂默认"}, "items": [it]}


def _gen(prompt, out, *, size, aspect, refs, provider, model, quality, tag):
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (245, 240, 230)).save(out)
    return B.Result(b"", "fake", model, 1, 0.1, 0.04)


def test_batch_rejects_fabricated_quote_and_writes_plan_card(tmp_path):
    bad = _v2_plan(tmp_path, "「先找最慢的一步」")
    with pytest.raises(ValueError, match="图上文字来源"):
        BT.run(bad, tmp_path / "bad", gen_fn=_gen, do_review=False, base_path=tmp_path, log=lambda *a: None)
    assert not (tmp_path / "bad").exists()   # 拦在任何落盘之前
    good = _v2_plan(tmp_path, "从 21 天到 9 天")
    BT.run(good, tmp_path / "good", gen_fn=_gen, do_review=False, base_path=tmp_path, log=lambda *a: None)
    card = (tmp_path / "good" / PC.CARD_NAME).read_text(encoding="utf-8")
    assert card.startswith("# 计划卡") and "提速三步" in card
