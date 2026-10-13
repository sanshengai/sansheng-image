"""批量执行：一份配图计划从头跑到尾——出图 → 导出 → 验收 → 单张返修 → 报告。

- 多页版式（小红书轮播、PPT、课件、X 多图）先出第一页，其余页拿它当画风参考，整套一个味道；
- 验收不合格：把问题改写成正面修正意见（fixes）只重出这一张，最多重试 2 次，仍不合格交给用户；
- 断点续跑：state.json 记每张的清单指纹与结果，没变的不重出、不重验；
- 密钥无效、账号需验证、没有可用服务：整轮停下。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import compile as CP
from . import contract as CT
from . import data as D
from .textspec import overlay_items
from . import export as EX
from . import plan as PL
from . import plan_card as PC
from .qa import content_expectations, judge

SERIES_FAMILIES = {"多张轮播"}
ARTICLE_FORMATS = {"article-illustration", "article-summary", "article-summary-tall"}
STOP_KINDS = {"auth", "verify", "unconfigured"}


def fixes_from(problems: list[str]) -> list[str]:
    """验收问题 → 正面修正意见。"""
    out: list[str] = []
    for p in problems:
        m = re.match(r"没做到：(.+?)（", p) or re.match(r"没做到：(.+)$", p)
        if m:
            out.append(f"make sure the image clearly shows: {m.group(1).strip()}")
            continue
        m = re.match(r"出现了禁止特征：(.+?)（", p) or re.match(r"出现了禁止特征：(.+)$", p)
        if m:
            out.append(f"the image must not contain: {m.group(1).strip()}")
            continue
        m = re.match(r"缺字或错字：应为「(.+)」", p)
        if m:
            out.append(f"the text must read exactly 「{m.group(1)}」 with every character correct and legible")
            continue
        m = re.match(r"第 (\d+) 格内容不符：(.+)", p)
        if m:
            out.append(f"correct panel {m.group(1)} to match its specified location, action and objects; observed problem: {m.group(2)}")
            continue
        m = re.match(r"第 (\d+) 条必要信息不符：(.+)", p)
        if m:
            out.append(f"make required content point {m.group(1)} visibly correct; observed problem: {m.group(2)}")
            continue
        if p.startswith("多写了文字"):
            out.append("show only the text that was asked for; no other words or letters")
        elif p.startswith("本图不该有字"):  # 常见来源是屏幕、仪表、招牌上被顺手画的数字
            out.append("leave every screen, meter, dial, sign and label blank: no letters or numbers anywhere in the image")
        elif p.startswith("整体偏暗"):
            out.append("make the whole image lighter and more high-key")
        elif p.startswith("颜色过艳"):
            out.append("use softer, less saturated colours")
        elif p.startswith("暗部过多"):
            out.append("avoid large dark areas; keep shadows small and soft")
        elif p.startswith("整体偏亮"):
            out.append("keep the image darker, closer to the style's usual tone")
        elif p.startswith("内容不符"):
            out.append("correct the subject, action and spatial relationships to match the original content requirements exactly")
        elif p.startswith("缩略图主题不清"):
            out.append("make the primary subject larger and its silhouette clear at the required thumbnail size while preserving the style")
        elif p.startswith("居中方形裁切丢失关键内容"):
            out.append("move the full title, main subject and every required pictorial badge inside the centered square, with visible margin from all square edges")
        elif p.startswith("单张透明表情：四边透明留白不足"):
            out.append("center the sticker character with at least 10% fully transparent margin on every side")
        elif p.startswith("单张透明表情：主体连通块"):
            out.append("remove all detached pixels and fragments; keep one connected sticker character silhouette")
        elif p.startswith("单张透明表情：没有完全透明的背景像素"):
            out.append("make the background fully transparent")
        elif p.startswith("单张透明表情：没有足够清晰的不透明主体") or p.startswith("单张透明表情：没有可见主体"):
            out.append("show one clearly visible opaque sticker character on a transparent background")
    return list(dict.fromkeys(out))


def _source_text(plan: dict, base_path: Path | None) -> str | None:
    """v2/v3 计划的原文（PL.check 已核过 sha256）；没有原文返回 None。"""
    src = plan.get("source") if isinstance(plan.get("source"), dict) else None
    if not src or not src.get("path"):
        return None
    path = Path(src["path"])
    if not path.is_absolute():
        path = (base_path or Path.cwd()) / path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _fp(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _base_fp(m: dict) -> str:
    """参考图内容也属于出图输入；同路径替换图片必须使断点缓存失效。"""
    refs = []
    for ref in m.get("references") or []:
        path = Path(ref["path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        refs.append({"role": ref["role"], "path": str(path), "sha256": digest})
    return _fp({**m, "_reference_files": refs})


def run(plan: dict, out_dir: Path, *, provider: str | None = None, model: str | None = None, quality: str | None = None,
        retries: int = 2, jobs: int = 3, do_review: bool = True, gen_fn: Callable | None = None,
        review_fn: Callable | None = None, review_source: str | None = None, log: Callable = print,
        base_path: Path | None = None) -> dict:
    from . import backends as B
    errs, warns = PL.check(plan, base_path=base_path)
    if errs:
        raise ValueError("计划不合格：" + "；".join(errs))
    if not plan["items"]:
        raise ValueError("本篇计划不需要新增图片，无需运行 batch")
    text_errs = [e for i, it in enumerate(plan["items"], 1)
                 for e in PC.check_items(it.get("text") or {}, _source_text(plan, base_path), f"第 {i} 张（{it.get('id', '?')}）")]
    if text_errs:
        raise ValueError("图上文字来源不合格：" + "；".join(text_errs))
    if gen_fn is None:
        gen_fn = B.generate
        provider, model = B.choose(provider, model, need_refs=True)
    model = model or "gpt-image-2"
    if do_review and review_fn is None:
        from .qa.reviewer import source
        actual_source = source()
        if review_source is not None and review_source != actual_source:
            raise ValueError(f"独立看图来源与实际后端不符：{review_source} != {actual_source}")
        review_source = actual_source
    elif do_review and review_source is None:
        review_source = "provided_unspecified"
    if review_fn is None and do_review:
        from .qa.reviewer import review as review_fn  # noqa: N813
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    PC.write(out, PL.table(plan), warns=warns, confirmed=plan.get("confirmed"))
    sf = out / "state.json"
    state = json.loads(sf.read_text(encoding="utf-8")) if sf.is_file() else {"items": {}}
    items = state.setdefault("items", {})
    ms = PL.manifests(plan, model)
    fmt0 = D.formats()[ms[0]["format"]]
    setting = plan.get("series")
    formats_in_plan = {m["format"] for m in ms}
    auto_series = (fmt0["family"] in SERIES_FAMILIES or plan["scene"] in {"xhs", "ppt"}
                   or (plan["scene"] == "wxillus" and bool(formats_in_plan & ARTICLE_FORMATS))
                   or (plan["scene"] == "book" and "picturebook-page" in formats_in_plan))
    series = len(ms) > 1 and setting is not False and (setting is not None or auto_series)
    stop = {"flag": False}
    pinned: dict[str, dict] = {}  # 一轮只读一次合同并锁定修订号：中途改合同也不会让一套图混用两个版本

    def pin(code: str) -> dict:
        key = code.split("@")[0]
        if key not in pinned:
            pinned[key] = CT.load(code)
        return pinned[key]

    def save():
        sf.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def do(m: dict, ref: Path | None) -> None:
        iid = m["_id"]
        rec = items.setdefault(iid, {})
        base = copy.deepcopy(m)
        if ref:
            base.setdefault("references", []).append({"path": str(ref), "role": "style"})
            base["use_anchor"] = False  # 后续页取首张成图的画风，避免合同锚点覆盖系列参考
        contract = pin(base["style"])
        base["style"] = f"{contract['code']}@r{contract['revision']}"  # 修订号进指纹：合同升级后续跑会整张重出
        fmt = D.formats()[base["format"]]
        text = base.get("text") or {}
        mode = text.get("mode") or (fmt.get("text") or {}).get("default", "none")
        raster_overlay = mode in ("overlay", "hybrid") and any("box" in x for x in overlay_items(text))
        if mode == "overlay" and not raster_overlay:  # PPTX 插画模式的字由 pptx_build 另外写成可编辑文字
            mode = "none"
        expected = [x["text"] for x in text.get("items", [])] if mode in ("native", "overlay", "hybrid") else []
        content = base.get("content") or {}
        review_contract = {**contract, **content_expectations(content)}
        expectation = review_contract.get("_content_expectation", "")
        points = review_contract.get("_content_point_expectations") or []
        point_scope = hashlib.sha256(json.dumps(points, ensure_ascii=False).encode("utf-8")).hexdigest()[:16] if points else None
        panels = review_contract.get("_panel_expectations") or []
        panel_scope = hashlib.sha256(json.dumps(panels, ensure_ascii=False).encode("utf-8")).hexdigest()[:16] if panels else None
        if mode == "native" and not expected:
            mode = "none"  # 格式默认原生写字、但这张没给任何字：编译写的是「不要文字」，验收也按无字图查
        base_fp = _base_fp({**base, "_renderer": {"provider": provider, "model": model, "quality": quality}})
        fixes: list[str] = list(rec.get("fixes", [])) if rec.get("base_fp") == base_fp else []
        attempt = rec.get("attempt_idx", 0) if rec.get("base_fp") == base_fp else 0  # 当前这张图是第几次返修出的
        if rec.get("base_fp") != base_fp:
            rec.clear()
        rec["base_fp"] = base_fp
        while True:
            if stop["flag"]:
                rec["status"] = "stopped"
                return
            man = dict(base, fixes=fixes) if fixes else base
            fp = _fp({**man, "_base_fp": base_fp, "_attempt": attempt})  # 参考图内容变更或返修都重出
            raw = out / f"{iid}.raw.png"
            c = CP.compile_manifest({k: v for k, v in man.items() if k != "_id"}, contract)
            prompt_file = out / f"{iid}.prompt.txt"
            prompt_bytes = (c.prompt + "\n").encode("utf-8")
            prompt_sha = hashlib.sha256(prompt_bytes).hexdigest()
            if (rec.get("fp") != fp or rec.get("prompt_sha256") != prompt_sha
                    or _sha(prompt_file) != prompt_sha or _sha(raw) != rec.get("raw_sha256")
                    or not raw.is_file()):
                prompt_file.write_bytes(prompt_bytes)
                refs = [Path(r["path"]) for r in c.references]
                try:
                    r = gen_fn(c.prompt, raw, size=c.size, aspect=c.aspect, refs=refs, provider=provider, model=model,
                               quality=quality, tag=f"batch:{iid}")
                except B.BackendError as e:
                    rec.update(status="gen_failed", error=f"{e.kind}：{e}")
                    if e.kind in STOP_KINDS:
                        stop["flag"] = True
                    log(f"{iid} 出图失败：{e.kind}")
                    return
                rec.update(fp=fp, style=base["style"], attempt_idx=attempt,
                           provider=r.provider, model=r.model, prompt_sha256=prompt_sha,
                           raw_sha256=_sha(raw), est_usd=round((rec.get("est_usd") or 0) + (r.est_usd or 0), 4), review=None)
                rec.pop("review_source", None)
                log(f"{iid} 出图完成（第 {attempt + 1} 次）")
            final = out / f"{iid}.png"
            prior_final_sha = rec.get("image_sha256")
            try:
                ex = EX.export(raw, base["format"], final, overlay=text if raster_overlay else None)
            except ValueError as e:
                rec.update(status="export_failed", error=str(e), problems=[str(e)], passed=False,
                           attempts=attempt + 1, fp=None)
                save()
                log(f"{iid} 导出失败：{e}")
                return
            image_sha = _sha(final)
            if prior_final_sha != image_sha:
                rec.pop("review", None)
                rec.pop("review_source", None)
            rec.update(image=final.name, image_sha256=image_sha,
                       extras=[p.name for p in ex.extras], export_warnings=ex.warnings)
            if (fmt.get("safe_zone") or {}).get("center_square"):
                review_contract["_square_crop_expectation"] = str(ex.extras[0])
            if fmt.get("thumbnail_px"):
                from PIL import Image
                px = int(fmt["thumbnail_px"])
                thumb = out / f"{iid}.thumb-{px}.png"
                with Image.open(final) as im:
                    im.convert("RGB").resize((px, px), Image.Resampling.LANCZOS).save(thumb)
                rec["thumbnail"] = thumb.name
                review_contract["_thumbnail_expectation"] = str(thumb)
            if not do_review:
                rec["status"] = "generated"
                save()
                return
            if rec.get("review") and ((expectation and not isinstance(rec["review"].get("content_match"), dict))
                                       or (points and (rec.get("point_review_scope") != point_scope
                                                       or not isinstance(rec["review"].get("point_match"), list)))
                                       or (panels and (rec.get("panel_review_scope") != panel_scope
                                                       or not isinstance(rec["review"].get("panel_match"), list)))
                                       or (fmt.get("thumbnail_px") and not isinstance(rec["review"].get("thumbnail_readable"), dict))
                                       or (review_contract.get("_square_crop_expectation")
                                           and not isinstance(rec["review"].get("square_crop"), dict))):
                rec["review"] = None  # 旧版只验画风/大图的结论不能沿用为新闸门已过
                rec.pop("review_source", None)
            if not rec.get("review"):
                try:
                    rec["review"] = review_fn(final, review_contract, expected, mode)
                    rec["review_source"] = review_source
                    if panels:
                        rec["panel_review_scope"] = panel_scope
                    if points:
                        rec["point_review_scope"] = point_scope
                except Exception as e:  # 看图失败：记下，不拖垮整批；额度用尽则整批停下
                    rec.update(status="review_failed", error=str(e)[:300])
                    if type(e).__name__ in ("QuotaExhausted", "ReviewerAuthUnavailable", "ReviewerNetworkUnavailable"):
                        stop["flag"] = True
                    return
            v = judge(final, review_contract, rec["review"], expected, mode, base["format"], man)
            rec.update(passed=v.passed, problems=v.problems, attempts=attempt + 1)
            save()
            if v.passed or attempt >= retries:
                rec["status"] = "passed" if v.passed else "failed"
                log(f"{iid} {'通过' if v.passed else '仍不合格，交给用户'}")
                return
            fixes = fixes_from(v.problems) or fixes
            rec["fixes"] = fixes
            attempt += 1
            log(f"{iid} 不合格，第 {attempt} 次返修：{'；'.join(fixes)[:120]}")

    if series:  # 先验收母版，只有通过的图片可向整组传播
        requested_master = setting.get("master_id") if isinstance(setting, dict) else None
        master = next((m for m in ms if m["_id"] == requested_master), None) if requested_master else None
        if master is None:
            master = next((m for m in ms if m["format"] in ARTICLE_FORMATS | {"picturebook-page"}), ms[0])
        do(master, None)
        first = out / f"{master['_id']}.raw.png"
        accepted = do_review and items[master["_id"]].get("status") == "passed" and bool(items[master["_id"]].get("review_source")) and first.is_file()
        rest = [m for m in ms if m["_id"] != master["_id"]]
        if not accepted:
            for m in rest:
                items[m["_id"]] = {"status": "blocked", "blocked_by": master["_id"],
                                   "reason": "系列母版未通过独立验收，不能作为画风参考"}
            rest = []
        ref = first if accepted else None
    else:
        rest, ref = ms, None
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        list(ex.map(lambda m: do(m, ref), rest))
    save()
    state["summary"] = {"total": len(ms), "passed": sum(1 for v in items.values() if v.get("status") == "passed"),
                        "failed": [k for k, v in items.items() if v.get("status") in {"failed", "export_failed", "blocked"}],
                        "est_usd": round(sum(v.get("est_usd") or 0 for v in items.values()), 3), "warnings": warns}
    save()
    _report(plan, out, state)
    return state


def _report(plan: dict, out: Path, state: dict) -> None:
    from .qa.sheet import matrix_sheet
    items = state["items"]
    cells = []
    deliverables = []
    for it in plan["items"]:
        rec = items.get(it["id"], {})
        img = out / rec["image"] if rec.get("image") else None
        st = {"passed": "pass", "failed": "fail", "export_failed": "fail"}.get(rec.get("status", ""), "pending")
        cells.append({"image": img if img and img.is_file() else None, "label": f"{it['id']} {it['what'][:14]}", "status": st})
        deliverables.append({"id": it["id"], "format": it.get("format") or plan.get("format") or D.scenes()[plan["scene"]]["formats"][0],
                             "image": rec.get("image") if img and img.is_file() else None,
                             "extras": rec.get("extras") or [], "status": rec.get("status", "pending"),
                             "ready": rec.get("status") == "passed" and bool(img and _sha(img) == rec.get("image_sha256")
                                                                              and rec.get("prompt_sha256") and rec.get("raw_sha256")),
                             "provider": rec.get("provider"), "model": rec.get("model"),
                             "prompt_sha256": rec.get("prompt_sha256"), "image_sha256": rec.get("image_sha256"),
                             "review_source": rec.get("review_source", "legacy_unknown" if rec.get("review") else None)})
    (out / "deliverables.json").write_text(json.dumps({"items": deliverables}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    matrix_sheet(cells, out / "overview.png", cols=min(4, max(1, len(cells))), title=f"{plan['style']['code']} · {len(cells)} 张")
    lines = [f"# 出图报告：{plan['style']['code']}", "", PL.table(plan), "",
             f"- 通过 {state['summary']['passed']} / {state['summary']['total']}；估算费用 {state['summary']['est_usd']} 美元", "",
             "| # | 格式 | 主图文件 | 结果 | 验收来源 | 出图次数 | 问题 |", "|---|---|---|---|---|---|---|"]
    for it in plan["items"]:
        rec = items.get(it["id"], {})
        res = {"passed": "通过", "failed": "不合格（已返修到上限）", "export_failed": "导出失败"}.get(rec.get("status", ""), rec.get("status", "未完成"))
        fmt = it.get("format") or plan.get("format") or D.scenes()[plan["scene"]]["formats"][0]
        source = rec.get("review_source", "legacy_unknown" if rec.get("review") else "—") or "—"
        lines.append(f"| {it['id']} | {fmt} | {rec.get('image') or '—'} | {res} | {source} | {rec.get('attempts', 0)} | {'；'.join(rec.get('problems') or [])[:200].replace('|', '/') or '—'} |")
    lines += ["", "![总览](overview.png)", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
