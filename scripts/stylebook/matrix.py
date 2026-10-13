"""测试矩阵：一种样式 × 8 道标准题 → 出图 → 验收 → 矩阵图、报告与能力建议。

- 断点续跑：state.json 记每题的提示词指纹、图片指纹与看图结论。提示词没变且图还在就不重出；
  图没变就不重新看图（判定每次按最新规则重算，改了判据不用重跑看图）；
- 成本记账：每张图的估算费用写进 state；逐次请求另由后端写进 logs/cost.jsonl；
- 预算：max_usd 是本轮新增花费上限，下一张会超就停，不再发新请求；
- 依赖：T2 用 T1 的出图作角色参考，T1 没出来 T2 就跳过；
- 密钥无效、账号需验证、没有可用服务这类错误：整轮停下，不逐题空转。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import compile as CP
from . import contract as CT
from .qa import judge, missing_items, review_prompt
from .qa.reviewer import INDEPENDENT_SOURCES

QUESTIONS_PATH = CT.ROOT / "tests" / "matrix" / "questions.json"
OUT_ROOT = CT.ROOT / "out" / "matrix"
STOP_KINDS = {"auth", "verify", "unconfigured"}


def questions() -> list[dict]:
    return json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _compiled_reference_paths(compiled: CP.Compiled, contract: dict) -> list[Path]:
    """Use the references selected by the compiler, including the contract's style anchor."""
    anchor = contract.get("anchor") or {}
    anchor_file = anchor.get("file")
    anchor_path = Path(anchor_file) if anchor_file else None
    if anchor_path and not anchor_path.is_absolute() and contract.get("_path"):
        anchor_path = Path(contract["_path"]).parent / anchor_path
    paths = []
    for ref in compiled.references:
        path = Path(ref["path"])
        is_anchor = bool(anchor_path and ref["role"] == "style" and path == anchor_path)
        if not path.is_file():
            raise CP.CompileError(f"参考图不存在：{path}")
        if is_anchor and anchor.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != anchor["sha256"]:
            raise CP.CompileError(f"风格锚点与合同 sha256 不符：{path}")
        paths.append(path)
    return paths


# 合同要求、但题目本身没给的文字角色，用固定的短文字补齐，保证同一题对所有画风可比。
MATRIX_ROLE_TEXT = {"title": "慢慢来，比较快", "ghost": "SLOW IS FAST", "brand": "叁笙", "tag": "第 1 期"}


def _fill_required_roles(text: dict, contract: dict | None) -> dict:
    required = ((contract or {}).get("recipe") or {}).get("required_text_roles") or []
    if not required or text.get("mode", "none") == "none":
        return text
    present = {i.get("role", "text") for i in text.get("items") or [] if isinstance(i, dict)}
    extra = [{"role": r, "text": MATRIX_ROLE_TEXT.get(r, "叁笙")} for r in required if r not in present]
    return {**text, "items": list(text.get("items") or []) + extra} if extra else text


def expected_text(q: dict, contract: dict | None = None) -> list[str]:
    t = _fill_required_roles(q.get("text") or {}, contract)
    return [it["text"] for it in t.get("items", [])] if t.get("mode") == "native" else []


def manifest_for(style: str, q: dict, model: str, refs: list[tuple[Path, str]] | None = None,
                 contract: dict | None = None) -> dict:
    m: dict = {"style": style, "model": model, "aspect": q["aspect"],
               "text": _fill_required_roles(q["text"], contract),
               "content": {"subject": q["subject"]}}
    if q.get("inventory"):
        m["content"]["inventory"] = q["inventory"]
    if q.get("camera"):
        m["content"]["camera"] = q["camera"]
    for k in ("structure", "density"):
        if q.get(k):
            m[k] = q[k]
    if refs:
        m["references"] = [{"path": str(p), "role": role} for p, role in refs]
    return m


def merged_contract(contract: dict, q: dict) -> dict:
    """样式合同的验收条目 + 本题自己的检查条目，一起交给看图。"""
    c = copy.deepcopy(contract)
    chk = q.get("checks") or {}
    c["qa"]["must_see"] = list(c["qa"]["must_see"]) + [x for x in chk.get("must_see", []) if x not in c["qa"]["must_see"]]
    c["qa"]["must_not_see"] = list(c["qa"]["must_not_see"]) + [x for x in chk.get("must_not_see", []) if x not in c["qa"]["must_not_see"]]
    if q.get("inventory"):
        c["_content_expectation"] = q["subject"] + "\n" + q["inventory"]
    return c


def _levels(results: dict[str, str], ids: list[str]) -> str:
    got = [results.get(i) for i in ids]
    if any(g not in ("pass", "fail") for g in got):
        return "unknown"
    n = sum(g == "pass" for g in got)
    if len(ids) == 1:
        return "ok" if n else "weak"
    return "good" if n == len(ids) else ("ok" if n else "weak")


def abilities(state: dict) -> dict:
    """由实测结果给出能力建议（准入环节再定稿写进合同）。"""
    res = {k: v.get("result", "pending") for k, v in state.get("questions", {}).items()}
    out = {"chinese_text": _levels(res, ["T7", "T8"]), "character_consistency": _levels(res, ["T2"]),
           "kids": _levels(res, ["T3"]), "video_frame": _levels(res, ["T6"])}
    t5 = state.get("questions", {}).get("T5") or {}
    rvs = [r["review"] for r in t5.get("reviews") or []] or ([t5["review"]] if t5.get("review") else [])
    flags = [bool(c.get("present")) for rv in rvs for c in rv.get("must_not_see", []) if c.get("item", "").startswith("any person")]
    if flags:
        out["adds_people"] = any(flags)
    return out


def admission(state: dict, contract: dict) -> dict:
    """矩阵自动判据与正式准入分开：后者还要独立双评、三次一致性和跨样式区分。"""
    res = {k: v.get("result", "pending") for k, v in state.get("questions", {}).items()}
    reasons = []
    if any(res.get(f"T{i}") == "pending" for i in range(1, 9)) or len(res) < 8:
        return {"verdict": "pending", "matrix_verdict": "pending", "reasons": ["还有题没出图或没看图"]}
    n = sum(1 for v in res.values() if v == "pass")
    split = sorted(k for k, v in res.items() if v == "split")
    native = "native" in (contract.get("recipe") or {}).get("text_mode", [])
    # 分歧题先当作通过：这样都凑不够线，就是确定不达标；凑得够，才轮到人复核分歧
    if n + len(split) < 7:
        reasons.append(f"只过了 {n} / 8 题（至少 7{'；另有分歧 ' + '、'.join(split) if split else ''}）")
    if res.get("T3") == "fail":
        reasons.append("T3 多人题没过（必过）")
    if native and res.get("T8") == "fail":
        reasons.append("声称能直接写中文，但 T8 中文标题卡没过")
    todo = ["每题两次独立看图", "同一提示词出 3 次，风格一致", "与已入库样式并排看，不会混淆"]
    if reasons:
        return {"verdict": "fail", "matrix_verdict": "fail", "reasons": reasons, "todo": todo}
    if split:
        return {"verdict": "review", "matrix_verdict": "review", "reasons": [f"{'、'.join(split)} 看图结论不一致，交人复核后再定"], "todo": todo}
    qs = state.get("questions") or {}
    independent_missing = [qid for qid in (f"T{i}" for i in range(1, 9))
                           if sum(r.get("source") in INDEPENDENT_SOURCES for r in qs.get(qid, {}).get("reviews") or []) < 2]
    if independent_missing:
        reasons.append(f"{'、'.join(independent_missing)} 缺两次有来源记录的独立看图")
    reasons.append("矩阵自动判据达线；同词三次一致性与跨样式区分尚待人工证据")
    return {"verdict": "pending", "matrix_verdict": "pass", "reasons": reasons, "todo": todo}


class _Budget:
    def __init__(self, cap: float | None):
        self.cap, self.spent, self.lock = cap, 0.0, threading.Lock()

    def reserve(self, est: float) -> bool:
        with self.lock:
            if self.cap is not None and self.spent + est > self.cap + 1e-9:
                return False
            self.spent += est
            return True


def run(style: str, *, only: list[str] | None = None, out_dir: Path | None = None, provider: str | None = None,
        model: str | None = None, quality: str | None = None, max_usd: float | None = None, jobs: int = 3,
        do_review: bool = True, regen: bool = False, contract: dict | None = None, reviews: int = 1,
        gen_fn: Callable | None = None, review_fn: Callable | None = None, review_source: str | None = None,
        log: Callable = print) -> dict:
    from . import backends as B
    contract = contract or CT.load(style)
    code = f"{contract['code']}@r{contract['revision']}"
    if gen_fn is None:
        gen_fn = B.generate
        provider, model = B.choose(provider, model, need_refs=True)
    model = model or "gpt-image-2"
    quality = quality or B.config().get("quality", "normal")
    if do_review and review_fn is None:
        from .qa.reviewer import source
        actual_source = source()
        if review_source is not None and review_source != actual_source:
            raise ValueError(f"独立看图来源与实际后端不符：{review_source} != {actual_source}")
        review_source = actual_source
    elif do_review and review_source is None:
        review_source = "provided_unspecified"
    if review_fn is None:
        from .qa.reviewer import review as review_fn  # noqa: N813
    out = Path(out_dir) if out_dir else OUT_ROOT / code
    out.mkdir(parents=True, exist_ok=True)
    sf = out / "state.json"
    state = json.loads(sf.read_text(encoding="utf-8")) if sf.is_file() else {}
    if state.get("style") not in (None, code):
        raise ValueError(f"{out} 里是 {state['style']} 的结果，不是 {code}；换一个输出目录")
    state.update({"style": code, "provider": provider, "model": model, "quality": quality})
    qs = state.setdefault("questions", {})
    for rec in qs.values():
        for old_review in rec.get("reviews") or []:
            old_review["source"] = old_review.get("source") or "legacy_unknown"
    allq = questions()
    todo = [q for q in allq if not only or q["id"] in only]
    budget, stop, lock = _Budget(max_usd), threading.Event(), threading.RLock()

    def save():
        with lock:
            sf.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def upd(qid: str, *, clear: bool = False, **kw) -> None:
        with lock:
            rec = qs.setdefault(qid, {})
            if clear:
                rec.clear()
            rec.update(kw)

    def gen_one(q: dict) -> None:
        with lock:
            rec = copy.deepcopy(qs.get(q["id"], {}))
        if stop.is_set():
            upd(q["id"], status="stopped")
            return
        refs: list[tuple[Path, str]] = []
        for r in q.get("refs_from", []):
            src = qs.get(r["question"], {})
            p = out / src["image"] if src.get("image") else None
            if not p or not p.is_file():
                upd(q["id"], status="blocked", error=f"依赖的 {r['question']} 没有出图")
                return
            refs.append((p, r["role"]))
        try:
            c = CP.compile_manifest(manifest_for(contract["code"], q, model, refs, contract), contract)
            compiled_refs = _compiled_reference_paths(c, contract)
        except CP.CompileError as e:  # 题目内容撞上样式冲突词：记下，不拖垮整轮
            upd(q["id"], status="compile_failed", error=f"编译被拦：{e}")
            log(f"{q['id']} 编译被拦：{e}")
            return
        ref_fp = [(str(p), _sha(p)) for p in compiled_refs]
        fp = hashlib.sha256(json.dumps([c.prompt, model, list(c.size), ref_fp], ensure_ascii=False).encode()).hexdigest()[:16]
        img = out / f"{q['id']}.png"
        (out / f"{q['id']}.prompt.txt").write_text(c.prompt + "\n", encoding="utf-8")
        if not regen and rec.get("prompt_fp") == fp and img.is_file():
            if rec.get("status") not in ("generated", "reviewed"):
                upd(q["id"], status="generated")
            log(f"{q['id']} 沿用上次出图")
            return
        est = B._estimate(model, quality, c.size) or 0.0
        if not budget.reserve(est):
            upd(q["id"], status="budget", error=f"预算到顶（本轮上限 {max_usd} 美元）")
            return
        try:
            r = gen_fn(c.prompt, img, size=c.size, aspect=c.aspect, refs=compiled_refs, provider=provider,
                       model=model, quality=quality, tag=f"matrix:{code}:{q['id']}")
        except B.BackendError as e:
            upd(q["id"], status="gen_failed", error=f"{e.kind}：{e}")
            if e.kind in STOP_KINDS:
                stop.set()
            log(f"{q['id']} 出图失败：{e.kind}")
            return
        upd(q["id"], clear=True, status="generated", image=img.name, image_sha=_sha(img), prompt_fp=fp, size=list(c.size),
                   provider=r.provider, model=r.model, attempts=r.attempts, seconds=r.seconds, est_usd=r.est_usd,
                   at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        save()
        log(f"{q['id']} 出图完成（{r.seconds}s，{r.attempts} 次）")

    # 按依赖分层：没有依赖的先并行出，再出依赖它们的
    done: set[str] = set()
    pending = list(todo)
    while pending:
        layer = [q for q in pending if all(r["question"] in done or r["question"] not in {x["id"] for x in pending}
                                           for r in q.get("refs_from", []))]
        if not layer:
            raise ValueError("测试题依赖成环")
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            list(ex.map(gen_one, layer))
        done |= {q["id"] for q in layer}
        pending = [q for q in pending if q["id"] not in done]
    save()

    def review_one(q: dict) -> None:
        if stop.is_set():
            return
        with lock:
            rec = copy.deepcopy(qs.get(q["id"], {}))
        img = out / rec["image"] if rec.get("image") else None
        if not img or not img.is_file() or rec.get("status") not in ("generated", "reviewed"):
            return
        sha = _sha(img)
        mc = merged_contract(contract, q)
        rfp = hashlib.sha256(review_prompt(mc, expected_text(q, contract), q["text"]["mode"]).encode()).hexdigest()[:16]
        mode = q["text"]["mode"]
        stored = rec.get("reviews") or ([{"review": rec["review"], "for": rec.get("review_for"), "fp": rec.get("review_fp"),
                                           "source": "legacy_unknown"}]
                                        if rec.get("review") else [])
        valid = [dict(r, source=r.get("source") or "legacy_unknown") for r in stored
                 if r.get("for") == sha and r.get("fp") == rfp and not missing_items(r["review"], mc, mode)]
        counted = ([r for r in valid if r.get("source") in INDEPENDENT_SOURCES]
                   if review_source in INDEPENDENT_SOURCES else valid)
        need = max(1, reviews) - len(counted)
        if need <= 0:
            return
        new, err = [], None
        for _ in range(need):  # 每次都是全新的独立看图进程
            if stop.is_set():
                break
            try:
                new.append({"review": review_fn(img, mc, expected_text(q, contract), mode), "for": sha, "fp": rfp,
                            "source": review_source})
            except Exception as e:  # 普通单题失败继续；额度或登录不可用则整轮停下
                err = str(e)[:300]
                if type(e).__name__ in ("QuotaExhausted", "ReviewerAuthUnavailable", "ReviewerNetworkUnavailable"):
                    stop.set()
                log(f"{q['id']} 看图失败：{err[:120]}")
                break
        if not new:
            upd(q["id"], review_error=err)
            return
        allr = valid + new
        upd(q["id"], reviews=allr, review=allr[0]["review"], review_for=sha, review_fp=rfp, status="reviewed", review_error=err)
        for i, r in enumerate(allr):
            name = f"{q['id']}.review.json" if i == 0 else f"{q['id']}.review-{i + 1}.json"
            (out / name).write_text(json.dumps(r["review"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        save()
        log(f"{q['id']} 看图完成（{len(allr)} 次）")

    if do_review:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            list(ex.map(review_one, todo))

    # 判定每次重算：规则改了不必重跑看图
    for q in todo:
        rec = qs.setdefault(q["id"], {})
        img = out / rec["image"] if rec.get("image") else None
        rvs = [r["review"] for r in rec.get("reviews") or []] or ([rec["review"]] if rec.get("review") else [])
        if img and img.is_file() and rvs:
            vs = [judge(img, merged_contract(contract, q), rv, expected_text(q, contract), q["text"]["mode"]) for rv in rvs]
            npass = sum(v.passed for v in vs)
            probs: list[str] = []
            for v in vs:
                probs += [x for x in v.problems if x not in probs]
            if npass == len(vs):
                rec.update(result="pass", problems=[], pixel=vs[0].pixel)
            elif npass == 0:
                rec.update(result="fail", problems=probs, pixel=vs[0].pixel)
            else:  # 独立看图结论不一致：不自动判，交人复核
                rec.update(result="split", problems=[f"看图结论不一致（{npass}/{len(vs)} 次判过），交人复核"] + probs, pixel=vs[0].pixel)
        else:
            rec.update(result="pending", problems=[rec.get("error") or rec.get("review_error") or "未出图或未看图"])
    state["abilities"] = abilities(state)
    state["admission"] = admission(state, contract)
    state["est_usd_total"] = round(sum((v.get("est_usd") or 0) for v in qs.values()), 3)
    state["spent_this_run"] = round(budget.spent, 3)
    save()
    render(out, state, allq)
    return state


STATUS_ZH = {"pass": "通过", "fail": "不过", "split": "分歧", "pending": "待定"}


def render(out: Path, state: dict, allq: list[dict]) -> tuple[Path, Path]:
    from .qa.sheet import consistency_sheet, matrix_sheet
    qs = state.get("questions", {})
    cells = []
    for q in allq:
        rec = qs.get(q["id"], {})
        img = out / rec["image"] if rec.get("image") else None
        res = rec.get("result", "pending")
        cells.append({"image": img if img and img.is_file() else None, "label": f"{q['id']} {q['zh']} · {STATUS_ZH[res]}", "status": res})
    png = matrix_sheet(cells, out / "matrix.png", title=f"{state['style']} · 8 题测试矩阵")
    t1, t2 = qs.get("T1", {}), qs.get("T2", {})
    if t1.get("image") and t2.get("image") and (out / t1["image"]).is_file() and (out / t2["image"]).is_file():
        consistency_sheet(out / t1["image"], [out / t2["image"]], out / "consistency-T1-T2.png", labels=["T2 两人互动"])
    lines = [f"# {state['style']} 测试矩阵", "",
             f"- 出图：{state.get('provider') or '-'} / {state.get('model')} / 质量 {state.get('quality')}",
             f"- 估算费用：累计 {state.get('est_usd_total', 0)} 美元（本轮新增 {state.get('spent_this_run', 0)}）",
             f"- 通过：{sum(1 for q in allq if qs.get(q['id'], {}).get('result') == 'pass')} / {len(allq)}"
             + (f"（另有看图分歧 {n_split} 题）" if (n_split := sum(1 for q in allq if qs.get(q['id'], {}).get('result') == 'split')) else ""), "",
             "| 题 | 结果 | 看图来源 | 主要问题 |", "|---|---|---|---|"]
    for q in allq:
        rec = qs.get(q["id"], {})
        probs = "；".join(rec.get("problems", [])[:3]).replace("|", "/") or "—"
        sources = [r.get("source") or "legacy_unknown" for r in rec.get("reviews") or []]
        if not sources and rec.get("review"):
            sources = ["legacy_unknown"]
        lines.append(f"| {q['id']} {q['zh']} | {STATUS_ZH[rec.get('result', 'pending')]} | {'、'.join(sources) or '—'} | {probs} |")
    adm = state.get("admission") or {}
    lines += ["", "## 准入线", "",
              f"- 八题自动判据：{ {'pass': '达标', 'fail': '不达标', 'review': '交人复核', 'pending': '待定'}.get(adm.get('matrix_verdict'), '待定') }",
              f"- 正式准入：{ {'fail': '不达标', 'review': '交人复核', 'pending': '待定'}.get(adm.get('verdict'), '待定') }"]
    lines += [f"- {r}" for r in adm.get("reasons", [])]
    lines += [f"- 还需人工确认：{t}" for t in adm.get("todo", [])]
    lines += ["", "## 能力建议（准入时定稿）", ""]
    lines += [f"- {k}: {v}" for k, v in (state.get("abilities") or {}).items()]
    lines += ["", "![矩阵](matrix.png)", ""]
    md = out / "report.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return png, md
