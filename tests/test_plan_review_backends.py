"""配图计划复核：后端解析、提示词生成、缺项重问、错误分类、规则引用（全部 mock，不调用真实接口）。"""
import io
import json
import socket
import subprocess
import sys
import urllib.error
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import plan as PL
from stylebook.qa import coding_plan as CPL
from stylebook.qa import plan_review as PR
from stylebook.qa import reviewer as RV

PASSED = {k: True for k in PR.ITEM_BOOLS}


def plan_of(n=1, cover=False):
    p = {"items": [{"id": f"{i:02d}", "position": f"「第{i}句」之后"} for i in range(1, n + 1)], "coverage": []}
    if cover:
        p["cover_brief"] = {"content": {"subject": "文章主题"}, "text": {"mode": "none"}}
    return p


def verdict(n=1, cover=None, **extra):
    data = {"items": [{"id": f"{i:02d}", **PASSED, "why": "依据充分"} for i in range(1, n + 1)], "missed_positions": []}
    if cover is not None:
        data["cover_review"] = cover
    data.update(extra)
    return data


GOOD_COVER = {"fidelity_ok": True, "theme_ok": True, "no_unrequested_claims": True, "why": "忠实于全文"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in (*PR.BACKEND_ENV, PR.BUDGET_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ARK_CODING_PLAN_BASE_URL", CPL.BASE_URL)
    monkeypatch.setenv("ARK_CODING_PLAN_API_KEY", "test-key")
    monkeypatch.setenv("ARK_AGENT_PLAN_BASE_URL", PR.ARK_PLAN_URL)
    monkeypatch.setenv("ARK_AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setenv("SANSHENG_IMAGE_CONFIG_DIR", str(tmp_path / "cfg"))
    article = tmp_path / "article.md"
    article.write_text("来源全文：先冷却五分钟再密封。", encoding="utf-8")
    return article


def coding_replies(monkeypatch, replies):
    """按顺序返回 Coding Plan 回答；返回发出的请求体列表。"""
    sent, queue = [], list(replies)

    def urlopen(req, **kw):
        sent.append(json.loads(req.data))
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        return io.BytesIO(json.dumps({"model": "deepseek-v4-1-flash",
                                      "choices": [{"finish_reason": "stop", "message": {"content": text}}]}).encode())
    monkeypatch.setattr(CPL.urllib.request, "urlopen", urlopen)
    return sent


# ---------------------------------------------------------------- 1. 后端解析唯一

def test_resolve_backend_precedence_and_default(monkeypatch):
    for k in PR.BACKEND_ENV:
        monkeypatch.delenv(k, raising=False)
    assert PR.resolve_backend() == "ark_coding_plan"
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    assert PR.resolve_backend() == "claude_cli"
    monkeypatch.setenv("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND", "ark_agent_plan")
    assert PR.resolve_backend() == "ark_agent_plan"
    assert PR.resolve_backend("ark_coding_plan") == "ark_coding_plan"  # 显式参数最优先


def test_unknown_backend_fails_before_reading_article(env, monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="未知配图计划复核后端"):
        PR.review(plan_of(), tmp_path / "does-not-exist.md", backend="gpt_something")
    monkeypatch.setenv("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND", "typo")
    with pytest.raises(ValueError, match="ark_coding_plan"):  # 报错列出可选项
        PR.review(plan_of(), tmp_path / "does-not-exist.md")  # 文件不存在也没触发 FileNotFoundError


@pytest.mark.parametrize("backend", PR.BACKENDS)
def test_recorded_backend_equals_actual_backend(env, monkeypatch, backend):
    """结果里记录的 _backend 必须是真正发请求的那个后端；explicit 与环境变量走同一个解析。"""
    calls = []
    monkeypatch.setattr(PR, "_TRANSPORTS", {b: (lambda ask, _b=b, **kw: (calls.append(_b), (json.dumps(verdict()), f"{_b}:m", None))[1])
                                            for b in PR.BACKENDS})
    result = PR.review(plan_of(), env, backend=backend)
    assert calls == [backend] and result["_backend"] == backend
    monkeypatch.setenv("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND", backend)
    calls.clear()
    assert PR.review(plan_of(), env)["_backend"] == backend and calls == [backend]


def test_explicit_coding_plan_is_accepted_end_to_end(env, monkeypatch):
    sent = coding_replies(monkeypatch, [verdict()])
    result = PR.review(plan_of(), env, backend="ark_coding_plan")
    assert result["_backend"] == "ark_coding_plan" and result["_reviewer"].startswith("ark_coding_plan:")
    assert len(sent) == 1 and sent[0]["temperature"] == 0


# ---------------------------------------------------------------- 2. 提示词由代码生成，格式说明在最后

def test_cover_schema_is_inside_main_format_section_and_last(env):
    text = PR.prompt(plan_of(cover=True), env, "inline")
    assert text.rindex("## 输出格式") > text.index("## 独立封面主题复核") > 0
    tail = text[text.rindex("## 输出格式"):]
    assert '"cover_review"' in tail and "写进 notes" in tail  # 封面进入主 schema，并明确不能写进 notes
    assert text.index("来源全文：先冷却五分钟再密封。") < text.rindex("## 输出格式")  # 原文在格式说明之前
    # 没有封面时格式说明不出现 cover_review
    assert "cover_review" not in PR.prompt(plan_of(), env, "inline")
    # 旧的“先说只输出 JSON、后面再追加 cover_review”的矛盾写法不再出现
    assert "追加 cover_review" not in text


def test_notes_is_not_a_fallback_for_cover_review(env, monkeypatch):
    drifted = verdict(notes=json.dumps({"cover_review": GOOD_COVER}, ensure_ascii=False))
    coding_replies(monkeypatch, [drifted])
    with pytest.raises(PR.PlanReviewIncomplete) as err:
        PR.review(plan_of(cover=True), env)
    assert any("cover_review" in g for g in err.value.gaps)


def test_prompt_modes(env):
    inline = PR.prompt(plan_of(), env, "inline")
    read_tool = PR.prompt(plan_of(), env, "read_tool")
    assert "来源全文：先冷却五分钟再密封。" in inline and "Read 工具" not in inline
    assert "来源全文" not in read_tool and "先用 Read 工具完整读原文" in read_tool and str(env) in read_tool
    with pytest.raises(ValueError):
        PR.prompt(plan_of(), env, "other")


def test_inline_injection_does_not_depend_on_phrase_replacement(env, monkeypatch):
    """原文注入由 mode 决定，不靠替换某句固定措辞：把 read_tool 那句话改掉也不影响 inline 请求。"""
    sent = coding_replies(monkeypatch, [verdict()])
    PR.review(plan_of(), env)
    body = sent[0]["messages"][0]["content"][0]["text"]
    assert "来源全文：先冷却五分钟再密封。" in body and "Read 工具" not in body


# ---------------------------------------------------------------- 3. 缺项：保留原始回答、重问一次

def test_missing_cover_review_is_reasked_once_and_second_answer_passes(env, monkeypatch):
    sent = coding_replies(monkeypatch, [verdict(), verdict(cover=GOOD_COVER)])
    result = PR.review(plan_of(cover=True), env)
    assert len(sent) == 2 and result["_attempts"] == 2
    second = sent[1]["messages"][0]["content"][0]["text"]
    assert "上一次回答不合格" in second and "cover_review" in second.split("上一次回答不合格")[1].split("## 输出格式")[0]
    assert second.rindex("## 输出格式") > second.index("上一次回答不合格")  # 格式说明仍在最后
    assert result["cover_review"]["theme_ok"] is True


def test_two_incomplete_answers_raise_with_raw_answers_and_gaps(env, monkeypatch):
    sent = coding_replies(monkeypatch, [verdict(), verdict(extra_marker="x")])
    with pytest.raises(PR.PlanReviewIncomplete, match="没有给出完整结论") as err:
        PR.review(plan_of(cover=True), env)
    e = err.value
    assert len(sent) == 2 and e.attempts == 2 and e.backend == "ark_coding_plan"
    assert len(e.answers) == 2 and json.loads(e.answers[1])["extra_marker"] == "x"  # 原始回答原样保留
    assert any("cover_review" in g for g in e.gaps)
    assert isinstance(e, RuntimeError) and e.to_report()["raw_answers"] == e.answers


def test_boolean_fields_are_type_checked_and_named_in_retry(env, monkeypatch):
    bad = verdict()
    bad["items"][0]["fidelity_ok"] = "true"
    del bad["items"][0]["reader_value_ok"]
    sent = coding_replies(monkeypatch, [bad, verdict()])
    assert PR.review(plan_of(), env)["_attempts"] == 2
    assert "fidelity_ok" in sent[1]["messages"][0]["content"][0]["text"].split("上一次回答不合格")[1]
    assert not PR._complete(bad, plan_of())


@pytest.mark.parametrize("answer", ["不是 JSON 的纯文字", '{"items": [', "```json\n{\"items\": []}\n```后面还有说明"])
def test_unparseable_or_incomplete_text_is_reasked(env, monkeypatch, answer):
    sent = coding_replies(monkeypatch, [answer, answer])
    with pytest.raises(PR.PlanReviewIncomplete):
        PR.review(plan_of(), env)
    assert len(sent) == 2


def test_extract_survives_fences_prose_and_braces():
    good = verdict()
    wrapped = "好的，结论如下 {不是json}：\n```json\n" + json.dumps(good, ensure_ascii=False) + "\n```\n以上，{完毕}"
    assert PR._extract(wrapped) == good
    assert PR._extract(json.dumps({"result": json.dumps(good)})) == good  # claude 信封
    assert PR._extract(json.dumps({"structured_output": good})) == good
    assert PR._extract("") is None and PR._extract("没有花括号") is None


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("x", 429, "limit", {}, None),
    urllib.error.HTTPError("x", 401, "auth", {}, None),
    urllib.error.URLError("offline"),
    socket.timeout("timed out"),
])
def test_infrastructure_errors_are_not_reasked(env, monkeypatch, error):
    calls = []

    def fail(*a, **k):
        calls.append(1)
        raise error
    monkeypatch.setattr(CPL.urllib.request, "urlopen", fail)
    with pytest.raises((RV.QuotaExhausted, RV.ReviewerAuthUnavailable, RV.ReviewerNetworkUnavailable)):
        PR.review(plan_of(), env)
    assert calls == [1]


# ---------------------------------------------------------------- 4. 错误分类三后端一致

def test_timeout_is_classified_not_a_bare_traceback(env, monkeypatch):
    def slow(*a, **k):
        raise socket.timeout("timed out")
    monkeypatch.setattr(CPL.urllib.request, "urlopen", slow)
    with pytest.raises(RV.ReviewerNetworkUnavailable, match="超时"):
        PR.review(plan_of(), env, timeout=7)
    # URLError 包着的超时同样归类为超时
    monkeypatch.setattr(CPL.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError(socket.timeout("timed out"))))
    with pytest.raises(RV.ReviewerNetworkUnavailable, match="超时"):
        PR.review(plan_of(), env)


def test_other_http_errors_include_response_snippet(env, monkeypatch):
    err = urllib.error.HTTPError("x", 500, "boom", {}, io.BytesIO(b'{"error":"upstream exploded"}'))
    monkeypatch.setattr(CPL.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(err))
    with pytest.raises(RuntimeError, match="HTTP 500.*upstream exploded"):
        PR.review(plan_of(), env)


def test_length_finish_reason_points_at_budget_env(env, monkeypatch):
    monkeypatch.setattr(CPL.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(
        {"model": "deepseek-v4-1-flash", "choices": [{"finish_reason": "length", "message": {"content": "{"}}]}).encode()))
    with pytest.raises(RuntimeError, match=PR.BUDGET_ENV):
        PR.review(plan_of(), env)


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_agent_plan_errors_classified_same_way(env, monkeypatch):
    def raising(error):
        return lambda *a, **k: (_ for _ in ()).throw(error)
    for error, expected, pattern in [
        (urllib.error.HTTPError("x", 429, "limit", {}, None), RV.QuotaExhausted, "429"),
        (urllib.error.HTTPError("x", 403, "no", {}, None), RV.ReviewerAuthUnavailable, "403"),
        (socket.timeout("timed out"), RV.ReviewerNetworkUnavailable, "超时"),
        (urllib.error.HTTPError("x", 502, "bad", {}, io.BytesIO(b"gateway down")), RuntimeError, "gateway down"),
    ]:
        monkeypatch.setattr(PR.urllib.request, "urlopen", raising(error))
        with pytest.raises(expected, match=pattern):
            PR.review(plan_of(), env, backend="ark_agent_plan")


def test_agent_plan_incomplete_with_max_tokens_names_budget_env(env, monkeypatch):
    monkeypatch.setattr(PR.urllib.request, "urlopen", lambda *a, **k: Reply(
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}))
    with pytest.raises(RuntimeError, match=PR.BUDGET_ENV):
        PR.review(plan_of(), env, backend="ark_agent_plan")


def _claude_run(monkeypatch, *, returncode=0, stdout="", stderr="", raises=None):
    monkeypatch.setattr(PR, "_claude_bin", lambda: "/fake/claude")

    def run(*a, **k):
        if raises:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(PR.subprocess, "run", run)


def test_claude_cli_timeout_quota_and_auth_are_classified(env, monkeypatch):
    _claude_run(monkeypatch, raises=subprocess.TimeoutExpired("claude", 5))
    with pytest.raises(RV.ReviewerNetworkUnavailable, match="超时"):
        PR.review(plan_of(), env, backend="claude_cli", timeout=5)
    _claude_run(monkeypatch, returncode=1, stderr="Claude usage limit reached")
    with pytest.raises(RV.QuotaExhausted):
        PR.review(plan_of(), env, backend="claude_cli")
    _claude_run(monkeypatch, returncode=1, stdout="Not logged in · Please run /login")
    with pytest.raises(RV.ReviewerAuthUnavailable, match="登录"):
        PR.review(plan_of(), env, backend="claude_cli")
    _claude_run(monkeypatch, returncode=2, stderr="segfault somewhere")
    with pytest.raises(RuntimeError, match="退出码 2.*segfault"):
        PR.review(plan_of(), env, backend="claude_cli")


def test_claude_cli_normal_answer_mentioning_rate_limit_is_not_quota(env, monkeypatch):
    """文章正文讨论 rate limit 时，正常回答不能被误判成额度用尽。"""
    answer = verdict()
    answer["items"][0]["why"] = "文中谈到 usage limit 和 rate limit，位置合理"
    _claude_run(monkeypatch, stdout=json.dumps({"is_error": False, "result": json.dumps(answer, ensure_ascii=False)}))
    result = PR.review(plan_of(), env, backend="claude_cli")
    assert result["_backend"] == "claude_cli"


def test_claude_cli_missing_binary_tells_user_how(env, monkeypatch):
    monkeypatch.setattr(PR, "_claude_bin", lambda: None)
    with pytest.raises(RV.ReviewerAuthUnavailable, match="ark_coding_plan"):
        PR.review(plan_of(), env, backend="claude_cli")


def test_coding_plan_request_temperature_default_and_override(env, monkeypatch):
    sent = []

    def urlopen(req, **kw):
        sent.append(json.loads(req.data))
        return io.BytesIO(json.dumps({"model": "deepseek-v4-1-flash",
                                      "choices": [{"finish_reason": "stop", "message": {"content": "x"}}]}).encode())
    monkeypatch.setattr(CPL.urllib.request, "urlopen", urlopen)
    CPL.request([], model=None, timeout=1)
    CPL.request([], model=None, timeout=1, temperature=0.3)
    assert [s["temperature"] for s in sent] == [0, 0.3]


# ---------------------------------------------------------------- 5. 输出预算余量

def test_budget_has_fixed_margin_for_notes_and_cover(env, monkeypatch):
    base = PR._output_budget(plan_of(10))
    assert base == 1500 + 10 * 1000 + PR.FIXED_MARGIN
    assert PR._output_budget(plan_of(10, cover=True)) == base + PR.COVER_MARGIN
    assert PR._output_budget(plan_of(1)) == 5000  # 下限不变
    assert PR._output_budget(plan_of(100)) == 32000


def test_claude_cli_has_no_output_budget(env, monkeypatch):
    monkeypatch.setenv(PR.BUDGET_ENV, "abc")  # 对 claude_cli 无意义，不应报错
    _claude_run(monkeypatch, stdout=json.dumps({"result": json.dumps(verdict())}))
    assert PR.review(plan_of(), env, backend="claude_cli")["_backend"] == "claude_cli"
    with pytest.raises(ValueError, match="2000–32000"):  # 另两个后端仍校验
        PR.review(plan_of(), env, backend="ark_coding_plan")


# ---------------------------------------------------------------- 7. 规则按稳定 key 引用

def test_item_lines_reference_rules_by_key_and_point_at_right_text():
    lines = "\n".join(PR._item_lines())
    assert f"判别要点 {PL.rule_no('metaphor_face')}" in lines
    assert "比喻" in PL.rule_text("metaphor_face") and "真实人物" in PL.rule_text("metaphor_face")
    # 旧版把第 9 条（并列类别）当成比喻/真人脸引用——现在 no_literal 项必须指向含“比喻”的那条
    no_literal = [l for l in PR._item_lines() if "no_literal_metaphor_or_real_face" in l][0]
    n = int(no_literal.rstrip("。").rsplit(" ", 1)[1])
    assert "比喻" in PL.PLANNING_RULES[n - 1]


def test_every_rule_is_referenced_by_some_item_or_coverage_instruction(env):
    text = "\n".join(PR._item_lines()) + PR.prompt(plan_of(), env)
    import re
    cited = set()
    for m in re.finditer(r"判别要点 ([\d、]+)", text):
        cited |= {int(x) for x in m.group(1).split("、") if x}
    assert cited == set(range(1, len(PL.PLANNING_RULE_ITEMS) + 1)), "有判别要点无人引用（或引用了不存在的编号）"


def test_removing_or_renaming_a_rule_breaks_prompt_generation(monkeypatch, env):
    items = [it for it in PL.PLANNING_RULE_ITEMS if it[0] != "metaphor_face"]
    monkeypatch.setattr(PL, "PLANNING_RULE_ITEMS", items)
    with pytest.raises(KeyError, match="metaphor_face"):
        PR.prompt(plan_of(), env)


def test_reordering_rules_moves_references_with_them(monkeypatch, env):
    items = list(PL.PLANNING_RULE_ITEMS)
    items.insert(0, items.pop([k for k, _ in items].index("metaphor_face")))  # 把比喻条挪到第 1
    monkeypatch.setattr(PL, "PLANNING_RULE_ITEMS", items)
    line = [l for l in PR._item_lines() if "no_literal_metaphor_or_real_face" in l][0]
    assert line.endswith("判别要点 1。")
    assert PR.prompt(plan_of(), env).count("1. 不把文中比喻按字面画出来") == 1


def test_rules_doc_is_numbered_in_the_same_order_as_code():
    doc = (ROOT / "references" / "planning.md").read_text(encoding="utf-8")
    for i, text in enumerate(PL.PLANNING_RULES, 1):
        assert f"\n{i}. {text}\n" in doc, f"planning.md 第 {i} 条与 PLANNING_RULES 不同序或不同文"
    assert [t for _, t in PL.PLANNING_RULE_ITEMS] == PL.PLANNING_RULES


# ---------------------------------------------------------------- 8. CLI：缺凭证不出 traceback，给指引与非零退出码

def _cli_setup(tmp_path, monkeypatch):
    import sb
    from test_plan import plan as make_plan, item
    article = tmp_path / "article.md"
    article.write_text("原文", encoding="utf-8")
    p = make_plan([item(1)])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(p), encoding="utf-8")
    import stylebook.backends.base as base
    monkeypatch.setattr(base, "CONFIG_DIR", tmp_path / "cfg")  # load_dotenv 只读这个临时目录，不碰本机真实配置
    monkeypatch.chdir(tmp_path)
    return sb, plan_path, article


def test_cli_without_credentials_prints_guidance_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    sb, plan_path, article = _cli_setup(tmp_path, monkeypatch)
    for k in ("ARK_CODING_PLAN_BASE_URL", "ARK_CODING_PLAN_API_KEY", *PR.BACKEND_ENV):
        monkeypatch.delenv(k, raising=False)
    code = sb.cmd_plan_review(Namespace(plan=str(plan_path), article=str(article), model=None, report=None))
    err = capsys.readouterr().err
    assert code == 2
    assert "ARK_CODING_PLAN_API_KEY" in err and "claude_cli" in err and "ark_agent_plan" in err and ".env" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("exc,needle", [
    (RV.QuotaExhausted("额度用尽"), "额度用尽"),
    (RV.ReviewerNetworkUnavailable("请求超时"), "请求超时"),
    (ValueError("未知配图计划复核后端：x"), "未知配图计划复核后端"),
    (RuntimeError("Coding Plan HTTP 500：boom"), "HTTP 500"),
])
def test_cli_service_failures_exit_2_without_traceback(tmp_path, monkeypatch, capsys, exc, needle):
    sb, plan_path, article = _cli_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(PR, "review", lambda *a, **k: (_ for _ in ()).throw(exc))
    assert sb.cmd_plan_review(Namespace(plan=str(plan_path), article=str(article), model=None, report=None)) == 2
    assert needle in capsys.readouterr().err


def test_cli_incomplete_result_writes_raw_answers_to_report(tmp_path, monkeypatch, capsys):
    sb, plan_path, article = _cli_setup(tmp_path, monkeypatch)
    err = PR.PlanReviewIncomplete("ark_coding_plan", ["缺少顶层 cover_review 对象"], ["第一次", "第二次原始回答"], None, 2)
    monkeypatch.setattr(PR, "review", lambda *a, **k: (_ for _ in ()).throw(err))
    report = tmp_path / "r.json"
    assert sb.cmd_plan_review(Namespace(plan=str(plan_path), article=str(article), model=None, report=str(report))) == 2
    saved = json.loads(report.read_text())
    assert saved["raw_answers"] == ["第一次", "第二次原始回答"] and saved["gaps"] == ["缺少顶层 cover_review 对象"]


def test_cli_dotenv_credentials_are_used_but_not_leaked_into_process(tmp_path, monkeypatch, capsys):
    sb, plan_path, article = _cli_setup(tmp_path, monkeypatch)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / ".env").write_text("ARK_CODING_PLAN_API_KEY=from-dotenv\nSANSHENG_IMAGE_PLAN_REVIEW_BACKEND=claude_cli\n")
    monkeypatch.delenv("ARK_CODING_PLAN_API_KEY", raising=False)
    monkeypatch.delenv("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND", raising=False)
    seen = {}

    def fake(plan, article_path, model=None, **kw):
        import os
        seen["key"] = os.environ.get("ARK_CODING_PLAN_API_KEY")
        seen["backend"] = PR.resolve_backend()
        return verdict()
    monkeypatch.setattr(PR, "review", fake)
    sb.cmd_plan_review(Namespace(plan=str(plan_path), article=str(article), model=None, report=None))
    assert seen == {"key": "from-dotenv", "backend": "claude_cli"}
    import os
    assert "ARK_CODING_PLAN_API_KEY" not in os.environ  # 不污染进程环境


def test_error_snippet_never_echoes_the_api_key(monkeypatch):
    from stylebook.qa import coding_plan
    monkeypatch.setenv("ARK_CODING_PLAN_API_KEY", "ark-secret-1234567890")
    text = coding_plan.snippet('{"error":"bad header Authorization: Bearer ark-secret-1234567890 and Bearer zzzzzzzzzzzz"}')
    assert "ark-secret-1234567890" not in text and "zzzzzzzzzzzz" not in text and "***" in text


def test_text_placement_repetition_is_not_a_content_failure_but_conflict_remains():
    from stylebook.plan import PLANNING_RULE_ITEMS
    rule = dict(PLANNING_RULE_ITEMS)["title_text"]
    assert "text.items 是上图文字的权威清单" in rule
    assert "完全一致" in rule and "不据此否定内容或形式" in rule
    assert "清单之外的新文字" in rule and "仍须拒绝" in rule
