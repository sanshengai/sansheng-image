import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import backends as B  # noqa: E402
from stylebook import compile as CP  # noqa: E402
from stylebook import contract as CT  # noqa: E402
from stylebook import matrix as MX  # noqa: E402

K = {
    "code": "C99", "revision": 1,
    "name": {"zh": "测试风格", "en": "Test Style"},
    "family": "测试", "fit": ["讲故事"], "visibility": "public",
    "source": {"from": "单元测试", "license": "original"},
    "essence": ["第一条命门特征", "第二条命门特征", "第三条命门特征"],
    "recipe": {"positive": "Soft watercolour storybook illustration with visible paper grain and loose ink lines."},
    "prompt_bans": ["neon"],
    "palette": {"recolor": "free"},
    "qa": {"must_see": ["画面是水彩质感", "纸纹可见"], "must_not_see": ["霓虹灯光", "黑色背景"]},
}


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LOG_DIR", tmp_path / "logs")


class Fake:
    def __init__(self, fail_on=None, kind="busy"):
        self.calls, self.fail_on, self.kind = [], fail_on, kind

    def __call__(self, prompt, out, *, size, aspect, refs, provider, model, quality, tag):
        from PIL import Image
        self.calls.append({"tag": tag, "refs": [str(r) for r in refs], "prompt": prompt, "size": size})
        if self.fail_on and tag.endswith(self.fail_on):
            raise B.BackendError(self.kind, "模拟失败", retryable=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 48), (240, 236, 228)).save(out)
        return B.Result(b"", "fake", model, 1, 0.1, 0.04)


def review_all_ok(image, contract, expected, mode, present=()):
    out = {"must_see": [{"item": i, "ok": True, "why": "看到"} for i in contract["qa"]["must_see"]],
           "must_not_see": [{"item": i, "present": any(i.startswith(p) for p in present), "why": "无"} for i in contract["qa"]["must_not_see"]],
           "transcribed_text": list(expected)}
    if contract.get("_content_expectation"):
        out["content_match"] = {"ok": True, "why": "主体与数量相符"}
    return out


def _run(tmp_path, **kw):
    kw.setdefault("gen_fn", Fake())
    kw.setdefault("review_fn", review_all_ok)
    return MX.run("C99", contract=copy.deepcopy(K), out_dir=tmp_path / "m", model="gpt-image-2", quality="normal",
                  log=lambda *_: None, **kw), kw["gen_fn"]


def test_questions_are_well_formed():
    qs = MX.questions()
    assert json.loads(MX.QUESTIONS_PATH.read_text())["version"] == 6
    assert [q["id"] for q in qs] == [f"T{i}" for i in range(1, 9)]
    by = {q["id"]: q for q in qs}
    assert "on her own left side (viewer right when she faces the camera)" in by["T1"]["subject"]
    assert any("on her own left side" in item for item in by["T1"]["checks"]["must_see"])
    assert by["T8"]["aspect"] == "3:4" and by["T8"]["text"]["mode"] == "native"
    assert by["T2"]["refs_from"] == [{"question": "T1", "role": "identity"}]
    assert "exactly two distinct people" in by["T2"]["inventory"].lower()
    assert "supplementary technical or detail panels" in by["T2"]["inventory"]
    assert "exactly one tiny sprig" in by["T5"]["inventory"].lower()
    for q in qs:
        assert q["checks"]["must_see"] and q["checks"]["must_not_see"], q["id"]
        assert all(len(x) >= 4 for x in q["checks"]["must_see"] + q["checks"]["must_not_see"])
        c = CP.compile_manifest(MX.manifest_for("C99", q, "gpt-image-2"), K)
        assert q["subject"] in c.prompt
        if q.get("inventory"):
            assert "Content inventory (literal): " + q["inventory"] in c.prompt
        for t in MX.expected_text(q):
            assert f"「{t}」" in c.prompt
    # 今天同题测试的三道题原样并入
    assert by["T3"]["subject"].startswith("A grandmother and her young grandson")
    assert by["T6"]["subject"].startswith("Early morning after rain")
    assert by["T7"]["subject"].startswith("An explainer graphic of the Pomodoro")
    assert "tomato-shaped kitchen timer at the visual centre" in by["T7"]["subject"]
    assert any("tomato-shaped kitchen timer as the visual centre" in item for item in by["T7"]["checks"]["must_see"])
    assert any("extra fourth process stop" in item for item in by["T7"]["checks"]["must_not_see"])


def test_matrix_passes_contract_anchor_and_invalidates_cached_images(tmp_path):
    from PIL import Image

    home = tmp_path / "styles" / "C99"
    home.mkdir(parents=True)
    anchor = home / "anchor.png"
    Image.new("RGB", (8, 8), "#eee9df").save(anchor)
    contract = copy.deepcopy(K)
    contract["_path"] = str(home / "contract.json")
    contract["anchor"] = {"file": "anchor.png", "sha256": hashlib.sha256(anchor.read_bytes()).hexdigest(),
                          "isolation": "Use the material only, not the reference subject."}
    out = tmp_path / "matrix"
    fake = Fake()
    MX.run("C99", contract=contract, out_dir=out, model="gpt-image-2", quality="normal", gen_fn=fake,
           review_fn=review_all_ok, only=["T1", "T2"], jobs=1, log=lambda *_: None)
    assert [c["refs"] for c in fake.calls] == [[str(anchor)], [str(anchor), str(out / "T1.png")]]

    Image.new("RGB", (8, 8), "#ddd7cc").save(anchor)
    contract["anchor"]["sha256"] = hashlib.sha256(anchor.read_bytes()).hexdigest()
    fake2 = Fake()
    MX.run("C99", contract=contract, out_dir=out, model="gpt-image-2", quality="normal", gen_fn=fake2,
           review_fn=review_all_ok, only=["T1", "T2"], jobs=1, log=lambda *_: None)
    assert [c["tag"] for c in fake2.calls] == ["matrix:C99@r1:T1", "matrix:C99@r1:T2"]


def test_matrix_rejects_missing_or_tampered_anchor(tmp_path):
    from PIL import Image

    anchor = tmp_path / "anchor.png"
    contract = copy.deepcopy(K)
    contract["anchor"] = {"file": str(anchor), "sha256": "0" * 64}
    for present in (False, True):
        if present:
            Image.new("RGB", (8, 8), "#eee9df").save(anchor)
        fake = Fake()
        state = MX.run("C99", contract=contract, out_dir=tmp_path / str(present), model="gpt-image-2",
                       quality="normal", gen_fn=fake, review_fn=review_all_ok, only=["T1"], jobs=1,
                       log=lambda *_: None)
        assert state["questions"]["T1"]["status"] == "compile_failed"
        assert fake.calls == []


def test_builtin_reviewer_cannot_claim_another_source(tmp_path, monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "claude_cli")
    fake = Fake()
    with pytest.raises(ValueError, match="来源与实际后端不符"):
        MX.run("C99", contract=copy.deepcopy(K), out_dir=tmp_path / "m", gen_fn=fake,
               review_source="ark_agent_plan", only=["T1"], log=lambda *_: None)
    assert fake.calls == []


def test_t1_left_side_failure_is_rejected(tmp_path):
    from PIL import Image
    from stylebook.qa import judge

    q = next(q for q in MX.questions() if q["id"] == "T1")
    contract = MX.merged_contract(copy.deepcopy(K), q)
    image = tmp_path / "t1.png"
    Image.new("RGB", (64, 64), (240, 236, 228)).save(image)
    left = next(item for item in contract["qa"]["must_see"] if "on her own left side" in item)
    review = {"must_see": [{"item": item, "ok": item != left, "why": "发夹在人物右侧" if item == left else "可见"}
                           for item in contract["qa"]["must_see"]],
              "must_not_see": [{"item": item, "present": False, "why": "未见"}
                               for item in contract["qa"]["must_not_see"]],
              "transcribed_text": []}
    verdict = judge(image, contract, review, [], "none")
    assert not verdict.passed and any(left in problem for problem in verdict.problems)


def test_t7_unlabeled_fourth_stop_is_rejected(tmp_path):
    from PIL import Image
    from stylebook.qa import judge

    q = next(q for q in MX.questions() if q["id"] == "T7")
    contract = MX.merged_contract(copy.deepcopy(K), q)
    image = tmp_path / "t7.png"
    Image.new("RGB", (64, 64), (240, 236, 228)).save(image)
    expected = MX.expected_text(q)
    review = review_all_ok(image, contract, expected, "native")
    assert judge(image, contract, review, expected, "native").passed

    extra = next(item for item in contract["qa"]["must_not_see"] if "extra fourth process stop" in item)
    for item in review["must_not_see"]:
        if item["item"] == extra:
            item.update(present=True, why="番茄计时器成了三阶段以外的无标签第四节点")
    verdict = judge(image, contract, review, expected, "native")
    assert not verdict.passed and any(extra in problem for problem in verdict.problems)


def test_inventory_is_reviewed_and_content_mismatch_is_rejected(tmp_path):
    from PIL import Image
    from stylebook.qa import judge

    q = next(q for q in MX.questions() if q["id"] == "T2")
    contract = MX.merged_contract(copy.deepcopy(K), q)
    assert q["inventory"] in contract["_content_expectation"]
    from stylebook.qa import review_prompt
    assert q["inventory"] in review_prompt(contract, [], "none")
    image = tmp_path / "t2.png"
    Image.new("RGB", (64, 64), (240, 236, 228)).save(image)
    review = review_all_ok(image, contract, [], "none")
    review["content_match"] = {"ok": False, "why": "背景多了一位同事"}
    verdict = judge(image, contract, review, [], "none")
    assert not verdict.passed and any("背景多了一位同事" in problem for problem in verdict.problems)


def test_full_run_dependency_and_outputs(tmp_path):
    st, fake = _run(tmp_path)
    out = tmp_path / "m"
    assert len(fake.calls) == 8
    t2 = next(c for c in fake.calls if c["tag"].endswith("T2"))
    assert t2["refs"] == [str(out / "T1.png")]
    assert "character identity reference" in t2["prompt"]
    assert all(v["result"] == "pass" for v in st["questions"].values())
    assert (out / "matrix.png").is_file() and (out / "report.md").is_file() and (out / "consistency-T1-T2.png").is_file()
    assert st["est_usd_total"] == pytest.approx(0.32)
    assert st["abilities"]["chinese_text"] == "good" and st["abilities"]["adds_people"] is False


def test_resume_skips_generation_and_review(tmp_path):
    _run(tmp_path, review_source="maintainer_codex_native")
    seen = []
    st, fake = _run(tmp_path, review_fn=lambda *a: seen.append(a) or review_all_ok(*a),
                    review_source="maintainer_codex_native")
    assert fake.calls == [] and seen == []
    assert all(v["result"] == "pass" for v in st["questions"].values())
    assert all(v["reviews"][0]["source"] == "maintainer_codex_native" for v in st["questions"].values())
    assert "| T1 半身表情 | 通过 | maintainer_codex_native |" in (tmp_path / "m" / "report.md").read_text()

    st, fake = _run(tmp_path, review_fn=lambda *a: seen.append(a) or review_all_ok(*a), review_source="claude_cli")
    assert fake.calls == [] and len(seen) == 8  # 旧维护者结论不能挡住补一次真正独立看图
    assert all([r["source"] for r in v["reviews"]] == ["maintainer_codex_native", "claude_cli"]
               for v in st["questions"].values())


def test_legacy_review_source_is_unknown_and_new_review_keeps_both_sources(tmp_path):
    _run(tmp_path)
    path = tmp_path / "m" / "state.json"
    state = json.loads(path.read_text())
    for rec in state["questions"].values():
        for review in rec["reviews"]:
            review.pop("source")
    path.write_text(json.dumps(state, ensure_ascii=False))
    st, fake = _run(tmp_path, reviews=2, review_source="maintainer_codex_native")
    assert fake.calls == []
    assert all([r["source"] for r in v["reviews"]] == ["legacy_unknown", "maintainer_codex_native"]
               for v in st["questions"].values())


def test_prompt_change_regenerates_only_that_question(tmp_path):
    _run(tmp_path)
    k2 = copy.deepcopy(K)
    k2["recipe"]["positive"] += " Warmer paper."
    fake = Fake()
    MX.run("C99", contract=k2, out_dir=tmp_path / "m2", model="gpt-image-2", quality="normal", gen_fn=fake,
           review_fn=review_all_ok, log=lambda *_: None)
    assert len(fake.calls) == 8  # 新目录全出
    fake = Fake()
    MX.run("C99", contract=copy.deepcopy(K), out_dir=tmp_path / "m", model="gpt-image-2", quality="normal", gen_fn=fake,
           review_fn=review_all_ok, log=lambda *_: None, regen=False)
    assert fake.calls == []


def test_budget_stops_new_requests(tmp_path):
    st, fake = _run(tmp_path, max_usd=0.1)  # 每张估 0.053（方图）或 0.041，只够两张
    assert len(fake.calls) == 2
    assert sum(1 for v in st["questions"].values() if v.get("status") == "budget") >= 5


def test_auth_error_stops_whole_run(tmp_path):
    st, fake = _run(tmp_path, gen_fn=Fake(fail_on="T1", kind="auth"), jobs=1)
    assert len(fake.calls) == 1
    assert st["questions"]["T2"]["status"] in ("stopped", "blocked")
    assert all(v["result"] == "pending" for v in st["questions"].values())


def test_failed_question_is_reported_not_passed(tmp_path):
    st, _ = _run(tmp_path, review_fn=lambda img, c, e, m: review_all_ok(img, c, e, m, present=("any person",)))
    assert st["questions"]["T5"]["result"] == "fail"
    assert st["abilities"]["adds_people"] is True
    assert "不过" in (tmp_path / "m" / "report.md").read_text(encoding="utf-8")


def test_wrong_text_fails(tmp_path):
    def rv(img, c, e, m):
        r = review_all_ok(img, c, e, m)
        r["transcribed_text"] = [t.replace("快", "怏") for t in e]
        return r
    st, _ = _run(tmp_path, review_fn=rv)
    assert st["questions"]["T8"]["result"] == "fail"
    assert st["questions"]["T1"]["result"] == "pass"
    assert st["abilities"]["chinese_text"] in ("ok", "weak")


def test_compile_conflict_is_recorded(tmp_path, monkeypatch):
    k = copy.deepcopy(K)
    k["prompt_bans"] = ["corgi"]
    fake = Fake()
    st = MX.run("C99", contract=k, out_dir=tmp_path / "m", model="gpt-image-2", quality="normal", gen_fn=fake,
                review_fn=review_all_ok, log=lambda *_: None)
    assert st["questions"]["T4"]["status"] == "compile_failed"
    assert len(fake.calls) == 7


def test_other_style_output_dir_refused(tmp_path):
    _run(tmp_path)
    k = dict(copy.deepcopy(K), code="C98")
    with pytest.raises(ValueError):
        MX.run("C98", contract=k, out_dir=tmp_path / "m", model="gpt-image-2", gen_fn=Fake(), review_fn=review_all_ok,
               log=lambda *_: None)


def test_state_file_is_valid_json(tmp_path):
    _run(tmp_path)
    json.loads((tmp_path / "m" / "state.json").read_text(encoding="utf-8"))
    assert CT.validate(K) == []


def test_question_checks_reach_reviewer_and_verdict(tmp_path):
    def rv(img, c, e, m):
        r = review_all_ok(img, c, e, m)
        for x in r["must_see"]:
            if x["item"] == "one corgi puppy with a tilted head":
                x["ok"] = False
        return r
    st, _ = _run(tmp_path, review_fn=rv)
    assert st["questions"]["T4"]["result"] == "fail"
    assert any("corgi" in p for p in st["questions"]["T4"]["problems"])
    assert st["questions"]["T3"]["result"] == "pass"


def test_incomplete_cached_review_is_redone(tmp_path):
    def partial(img, c, e, m):
        r = review_all_ok(img, c, e, m)
        r["must_not_see"] = r["must_not_see"][:-1]
        return r
    st, _ = _run(tmp_path, review_fn=partial)
    assert all(v["result"] == "fail" for v in st["questions"].values())
    seen = []
    st, fake = _run(tmp_path, review_fn=lambda *a: seen.append(a) or review_all_ok(*a))
    assert fake.calls == [] and len(seen) == 8
    assert all(v["result"] == "pass" for v in st["questions"].values())


def test_changed_review_items_trigger_rereview(tmp_path):
    k0 = copy.deepcopy(K)
    k0["qa"]["must_see"].append("临时多出的一条特征")
    MX.run("C99", contract=k0, out_dir=tmp_path / "m", model="gpt-image-2", quality="normal", gen_fn=Fake(),
           review_fn=review_all_ok, log=lambda *_: None)
    k = copy.deepcopy(K)  # 去掉那一条：旧结论不缺任何条目，只有看图题目变了
    seen = []
    MX.run("C99", contract=k, out_dir=tmp_path / "m", model="gpt-image-2", quality="normal", gen_fn=Fake(),
           review_fn=lambda *a: seen.append(a) or review_all_ok(*a), log=lambda *_: None)
    assert len(seen) == 8


def test_admission_line(tmp_path):
    st, _ = _run(tmp_path)
    assert st["admission"]["matrix_verdict"] == "pass"
    assert st["admission"]["verdict"] == "pending"
    assert "T1" in " ".join(st["admission"]["reasons"])
    report = (tmp_path / "m" / "report.md").read_text()
    assert "八题自动判据：达标" in report and "正式准入：待定" in report

    def t3_fails(img, c, e, m):
        r = review_all_ok(img, c, e, m)
        if "grandmother" in open(str(img).replace(".png", ".prompt.txt")).read():
            r["must_see"][0]["ok"] = False
        return r
    st, _ = _run(tmp_path / "b", review_fn=t3_fails)
    assert st["questions"]["T3"]["result"] == "fail"
    assert st["admission"]["verdict"] == "fail" and st["admission"]["matrix_verdict"] == "fail"
    assert any("T3" in r for r in st["admission"]["reasons"])

    k = copy.deepcopy(K)
    k["recipe"]["text_mode"] = ["native"]

    def t8_fails(img, c, e, m):
        r = review_all_ok(img, c, e, m)
        if str(img).endswith("T8.png"):
            r["transcribed_text"] = ["错字"]
        return r
    st = MX.run("C99", contract=k, out_dir=tmp_path / "c", model="gpt-image-2", quality="normal", gen_fn=Fake(),
                review_fn=t8_fails, log=lambda *_: None)
    assert st["admission"]["verdict"] == "fail" and any("T8" in r for r in st["admission"]["reasons"])


def test_two_sourced_reviews_still_need_stability_and_distinction(tmp_path):
    st, _ = _run(tmp_path, reviews=2, review_source="claude_cli")
    assert st["admission"]["matrix_verdict"] == "pass"
    assert st["admission"]["verdict"] == "pending"
    assert not any("缺两次" in r for r in st["admission"]["reasons"])
    assert any("三次一致性" in r for r in st["admission"]["reasons"])


def test_agent_plan_double_review_counts_as_independent(tmp_path):
    st, _ = _run(tmp_path, reviews=2, review_source="ark_agent_plan")
    assert st["admission"]["matrix_verdict"] == "pass"
    assert st["admission"]["verdict"] == "pending"
    assert not any("缺两次" in r for r in st["admission"]["reasons"])
    assert all(len(q["reviews"]) == 2 and all(r["source"] == "ark_agent_plan" for r in q["reviews"])
               for q in st["questions"].values())


def test_two_maintainer_reviews_do_not_count_as_independent(tmp_path):
    st, _ = _run(tmp_path, reviews=2, review_source="maintainer_codex_native")
    assert st["admission"]["matrix_verdict"] == "pass"
    assert any("缺两次" in r for r in st["admission"]["reasons"])


def test_default_reviewer_records_selected_backend(tmp_path, monkeypatch):
    from stylebook.qa import reviewer
    monkeypatch.setenv("SANSHENG_IMAGE_QA_BACKEND", "ark_agent_plan")
    monkeypatch.setattr(reviewer, "review", review_all_ok)
    st = MX.run("C99", contract=K, out_dir=tmp_path / "m", model="gpt-image-2", quality="normal",
                gen_fn=Fake(), reviews=2, log=lambda *_: None)
    assert all(r["source"] == "ark_agent_plan" for q in st["questions"].values() for r in q["reviews"])
    assert not any("缺两次" in r for r in st["admission"]["reasons"])


class Flip:
    """第 1 次看 T8 判过、第 2 次判不过；其余题都判过。"""
    def __init__(self):
        self.calls = []

    def __call__(self, img, c, e, m):
        self.calls.append(Path(img).stem)
        r = review_all_ok(img, c, e, m)
        if Path(img).stem == "T8" and self.calls.count("T8") % 2 == 0:
            r["must_see"][0]["ok"] = False
        return r


def test_two_reviews_split_goes_to_human(tmp_path):
    flip = Flip()
    st, _ = _run(tmp_path, review_fn=flip, reviews=2)
    assert len(flip.calls) == 16
    assert st["questions"]["T8"]["result"] == "split"
    assert "交人复核" in st["questions"]["T8"]["problems"][0]
    assert st["admission"]["verdict"] == "review"
    assert (tmp_path / "m" / "T8.review-2.json").is_file()
    assert "分歧" in (tmp_path / "m" / "report.md").read_text(encoding="utf-8")
    flip2 = Flip()
    _run(tmp_path, review_fn=flip2, reviews=2)
    assert flip2.calls == []  # 两次都齐了，续跑不再看
    flip3 = Flip()
    _run(tmp_path, review_fn=flip3, reviews=3)
    assert len(flip3.calls) == 8  # 每题只补第 3 次


def test_split_cannot_rescue_a_failing_style(tmp_path):
    def mostly_fail(img, c, e, m):
        r = review_all_ok(img, c, e, m)
        if Path(img).stem in ("T1", "T4"):
            r["must_see"][0]["ok"] = False
        return r
    flip = Flip()

    def combo(img, c, e, m):
        return flip(img, c, e, m) if Path(img).stem == "T8" else mostly_fail(img, c, e, m)
    st, _ = _run(tmp_path, review_fn=combo, reviews=2)
    assert st["questions"]["T8"]["result"] == "split"
    assert st["admission"]["verdict"] == "fail"  # 5 过 + 1 分歧 < 7


def test_quota_exhausted_stops_reviews(tmp_path):
    from stylebook.qa.reviewer import QuotaExhausted
    seen = []

    def rv(*a):
        seen.append(1)
        raise QuotaExhausted("额度")
    st, _ = _run(tmp_path, review_fn=rv, jobs=1)
    assert len(seen) == 1 and all(v["result"] == "pending" for v in st["questions"].values())


def test_missing_reviewer_login_stops_reviews(tmp_path):
    from stylebook.qa.reviewer import ReviewerAuthUnavailable
    seen = []

    def rv(*a):
        seen.append(1)
        raise ReviewerAuthUnavailable("未登录")
    st, _ = _run(tmp_path, review_fn=rv, jobs=1)
    assert len(seen) == 1 and all(v["result"] == "pending" for v in st["questions"].values())


def test_network_unavailable_stops_reviews(tmp_path):
    from stylebook.qa.reviewer import ReviewerNetworkUnavailable
    seen = []

    def rv(*a):
        seen.append(1)
        raise ReviewerNetworkUnavailable("网络不可用")
    st, _ = _run(tmp_path, review_fn=rv, jobs=1)
    assert len(seen) == 1 and all(v["result"] == "pending" for v in st["questions"].values())


def test_matrix_fills_contract_required_text_roles():
    """合同要求的文字角色（如 C77 影字）由矩阵补齐，编译不报错且逐字进入应有文字。"""
    import json
    q = {"id": "T8", "aspect": "3:4", "subject": "A quiet title card.",
         "text": {"mode": "native", "items": [{"role": "title", "text": "慢慢来，比较快"}]}}
    c77 = json.loads((Path(__file__).resolve().parents[1] / "styles/C77/contract.json").read_text())
    with pytest.raises(CP.CompileError):
        CP.compile_manifest(MX.manifest_for("C77", q, "gpt-image-2"), c77)
    compiled = CP.compile_manifest(MX.manifest_for("C77", q, "gpt-image-2", contract=c77), c77)
    assert "「SLOW IS FAST」" in compiled.prompt
    assert MX.expected_text(q, c77) == ["慢慢来，比较快", "SLOW IS FAST"]
    assert MX.expected_text(q) == ["慢慢来，比较快"]
