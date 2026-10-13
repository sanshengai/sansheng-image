"""Lightweight brief → compiled tasks → generation/export/pixel preflight.

Pixel checks never certify visual content. The host must inspect the overview.
Every invocation uses a new output directory; previous candidates are retained.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import time
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import compile as CP, data as D, export as EX, plan as PL, plan_card as PC
from . import selection as SEL
from .qa.sheet import matrix_sheet


class BriefError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(brief: dict, *, base_path: Path, model: str = "gpt-image-2") -> dict:
    if not isinstance(brief, dict) or brief.get("version") != 1:
        raise BriefError("brief.version 必须是 1")
    allowed = {"version", "scene", "style", "palette", "reason", "code", "source", "items", "format", "references",
               "chosen_by", "task_id", "confirmed"}
    if set(brief) - allowed:
        raise BriefError(f"brief 未知字段：{sorted(set(brief) - allowed)}")
    selected = SEL.normalize(brief["code"], scene=brief.get("scene")) if brief.get("code") else None
    scene = selected["scene"] if selected else brief.get("scene")
    if scene not in D.scenes():
        raise BriefError(f"未知用途：{scene}")
    reason = brief.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise BriefError("brief.reason 必须说明这组图的选择理由")
    items = brief.get("items")
    if not isinstance(items, list) or not items:
        raise BriefError("brief.items 不得为空")
    if brief.get("confirmed") not in (None, "user", "skipped"):
        raise BriefError("confirmed 只能是 user（用户看过计划卡并确认）或 skipped（用户说直接出、或由自动流程调用）")
    if brief.get("chosen_by", "agent") not in {"agent", "user"}:
        raise BriefError("chosen_by 只能是 agent 或 user（用户亲自挑的画风 / 色系才记为偏好证据）")
    chosen = PL.resolve_style(scene, explicit=brief.get("style"), include_inferred=False)
    palette = brief.get("palette") or chosen.get("palette") or {"family": "orig"}
    expression = {}
    if brief.get("code"):
        chosen = {"code": selected["style"]["code"] + f"@r{selected['style']['revision']}", "source": "code"}
        palette = {k: v for k, v in selected["palette"].items() if k in {"family", "light", "sat", "custom"}}
        expression = selected["expression"]
    if not isinstance(palette, dict):
        raise BriefError("palette 必须是色系配置对象")
    source = None
    source_text = None
    if brief.get("source"):
        if not isinstance(brief["source"], str):
            raise BriefError("source 必须是原文路径")
        path = (base_path / brief["source"]).resolve()
        source_bytes = path.read_bytes()
        source_text = source_bytes.decode("utf-8")
        if not source_text.strip():
            raise BriefError("原文不得为空")
        source = {"path": str(path), "sha256": hashlib.sha256(source_bytes).hexdigest()}
    tasks, seen = [], set()
    references = brief.get("references", [])
    if not isinstance(references, list) or any(not isinstance(r, dict) or set(r) != {"role", "path"}
                                              or not isinstance(r["path"], str) for r in references):
        raise BriefError("references 必须是 role/path 对象列表")
    references = [{"role": r["role"], "path": str((base_path / r["path"]).resolve())} for r in references]
    default_format = brief.get("format") or D.scenes()[scene]["formats"][0]
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) - {"id", "position", "message", "visual", "text", "format", "points", "panels", "relations", "final_relations"}:
            raise BriefError(f"第 {index} 张字段不合格")
        for field in ("position", "message", "visual"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise BriefError(f"第 {index} 张缺少 {field}")
        position = item["position"]
        if source_text is not None and position != "cover" and position not in source_text:
            raise BriefError(f"第 {index} 张 position 必须是原文中的准确引用，或 cover")
        ident = item.get("id", f"{index:02d}")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", ident) or ident in seen:
            raise BriefError(f"图片 id 非法或重复：{ident}")
        seen.add(ident)
        fmt = item.get("format") or ("wechat-cover-head" if scene == "wxillus" and position == "cover" else default_format)
        if fmt not in D.formats():
            raise BriefError(f"未知格式：{fmt}")
        text = item.get("text", [])
        if isinstance(text, dict):
            if text.get("mode") != "overlay":
                raise BriefError(f"第 {index} 张 text 对象仅支持 overlay 精确排字")
            text_spec = copy.deepcopy(text)
            from .overlay import validate as validate_overlay
            problems = validate_overlay(text_spec)
            if problems:
                raise BriefError("精确文字配置不合格：" + "；".join(problems))
        else:
            if isinstance(text, str):
                text = [text] if text else []
            if not isinstance(text, list) or any(not isinstance(t, str) or not t.strip() for t in text):
                raise BriefError(f"第 {index} 张 text 必须是文字、非空文字列表或 overlay 对象")
            if scene == "tb-vocab" and text:
                if len(text) != 1:
                    raise BriefError("教材单词图多个标签须提供 overlay 对象，明确每条文字的位置")
                text_spec = {"mode": "overlay", "reserve": "the bottom label band", "items": [
                    {"role": "label", "text": text[0], "box": [0.08, 0.80, 0.84, 0.14],
                     "font_px": 72, "min_px": 40, "align": "center", "valign": "center", "require_blank": True}]}
            else:
                text_spec = {"mode": "native" if text else "none", "items": [
                    {"role": "title" if n == 0 else "label", "text": t} for n, t in enumerate(text)]}
        text_errs = PC.check_items(text_spec, source_text, f"第 {index} 张")
        if text_errs:
            raise BriefError("图上文字来源不合格：" + "；".join(text_errs))
        purpose = item["message"]
        constraints = D.scenes()[scene].get("constraints", [])
        if constraints:
            purpose += "\nTextbook constraints: " + " ".join(constraints)
        manifest = {"style": chosen["code"], "format": fmt, "model": model, "palette": palette,
                    "references": references, **({"use_anchor": False} if any(r["role"] == "style" for r in references) else {}),
                    "content": {"subject": item["visual"], "purpose": purpose}, "text": text_spec}
        for field in ("points", "panels"):
            if field in item:
                values = item[field]
                if not isinstance(values, list) or not values or any(
                        not isinstance(value, str) or not value.strip() for value in values):
                    raise BriefError(f"第 {index} 张 {field} 必须是非空文字列表")
                manifest["content"][field] = list(values)
        for field in ("relations", "final_relations"):
            if field in item:
                if not isinstance(item[field], str) or not item[field].strip():
                    raise BriefError(f"第 {index} 张 {field} 必须是非空文字")
                manifest["content"][field] = item[field]
        for field in ("structure", "density"):
            if expression.get(field, "auto") != "auto":
                manifest[field] = expression[field]
        compiled = CP.compile_manifest(manifest)
        refs = [{**r, "sha256": _sha(Path(r["path"]))} for r in compiled.references]
        overlay_assets = []
        for layer in text_spec.get("image_layers", []):
            path = (base_path / layer["path"]).resolve()
            if not path.is_relative_to(base_path.resolve()):
                raise BriefError("overlay 图片图层越出 brief 目录")
            overlay_assets.append({"path": str(path), "sha256": _sha(path)})
        tasks.append({"id": ident, "position": position, "message": item["message"],
                      "prompt_sha256": hashlib.sha256(compiled.prompt.encode()).hexdigest(),
                      "manifest": manifest, "compiled": compiled.to_dict(), "references": refs, "overlay_assets": overlay_assets})
    return {"version": 1, "scene": scene, "selection": chosen, "palette": palette,
            "reason": reason, "source": source, "tasks": tasks}


def execution_card(task: dict, source: dict | None = None) -> dict:
    """Expose existing compiled inputs; never rewrite the prompt or old prepared hashes."""
    from . import contract as CT
    from .qa import active_items, content_expectations
    manifest, compiled = task["manifest"], task["compiled"]
    contract = CT.load(manifest["style"])
    mode = manifest["text"]["mode"]
    must, bans = active_items(contract, mode)
    return {
        "version": 1, "id": task["id"], "position": task["position"], "source": source,
        "message": task["message"], "style": manifest["style"], "format": manifest["format"],
        "content": manifest["content"], "content_expectations": content_expectations(manifest["content"]),
        "text": manifest["text"], "palette": manifest["palette"], "references": task["references"],
        "reference_isolation": (contract.get("anchor") or {}).get("isolation"),
        "requirements": {"must_see": must, "must_not_see": bans},
        "tool_arguments": {"prompt": compiled["prompt"],
                           "referenced_image_paths": [r["path"] for r in task["references"]],
                           "transparent_background": bool(D.formats()[manifest["format"]].get("transparent"))},
        "prompt_sha256": task["prompt_sha256"], "generation_size": compiled["size"],
        "export_px": D.formats()[manifest["format"]]["export_px"],
        "human_preflight": "核对原题遗漏、身份与材质参考职责、保留项与修改项冲突；此卡不证明已完成检查。",
        "visual_review": "not_run", "accepted": False,
    }


def run(brief: dict, out: Path, *, base_path: Path, provider: str | None = None,
        model: str | None = None, quality: str | None = None, jobs: int = 4,
        prepare_only: bool = False, gen_fn=None) -> dict:
    from . import backends as B
    from PIL import Image
    from .sticker import check_single

    if type(jobs) is not int or not 1 <= jobs <= 8:
        raise BriefError("jobs 必须是 1–8")
    started = time.monotonic()
    if not prepare_only and gen_fn is None:
        provider, model = B.choose(provider, model, need_refs=True)
        gen_fn = B.generate
    prepared = prepare(brief, base_path=base_path, model=model or "gpt-image-2")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    _write(out / "brief.json", brief)
    _write(out / "prepared.json", prepared)
    PC.write(out, PC.brief_table(prepared), confirmed=brief.get("confirmed"))
    for task in prepared["tasks"]:
        _write(out / f"{task['id']}-manifest.json", task["manifest"])
        _write(out / f"{task['id']}-compiled.json", task["compiled"])
        _write(out / f"{task['id']}-execution-card.json", execution_card(task, prepared["source"]))
    if prepare_only:
        report = {"status": "pending_host", "selection": prepared["selection"],
                  "prepared": str(out / "prepared.json"), "visual_review": "not_run", "accepted": False}
        _write(out / "report.json", report)
        return report

    stopped = Event()

    def generate_one(task):
        ident, compiled = task["id"], task["compiled"]
        raw, final = out / f"{ident}-raw.png", out / f"{ident}.png"
        rec = {"id": ident, "status": "failed", "visual_review": "not_run", "accepted": False}
        try:
            if stopped.is_set():
                raise BriefError("服务鉴权或配置失败，未启动剩余任务")
            for ref in task["references"] + task.get("overlay_assets", []):
                if _sha(Path(ref["path"])) != ref["sha256"]:
                    raise BriefError("参考图在编译后发生变化")
            result = gen_fn(compiled["prompt"], raw, size=tuple(compiled["size"]), aspect=compiled["aspect"],
                            refs=[Path(r["path"]) for r in task["references"]], provider=provider,
                            model=model or compiled["model"], quality=quality, tag=f"make:{ident}")
            rec["generation"] = {"provider": result.provider, "model": result.model,
                                 "attempts": result.attempts, "seconds": result.seconds,
                                 "est_usd": result.est_usd, "raw_sha256": _sha(raw)}
            for ref in task["references"] + task.get("overlay_assets", []):
                if _sha(Path(ref["path"])) != ref["sha256"]:
                    raise BriefError("出图期间参考图发生变化，候选不可接受")
            fmt = task["manifest"]["format"]
            text_spec = task["manifest"]["text"]
            exported = EX.export(raw, fmt, final, overlay=text_spec if text_spec["mode"] == "overlay" else None,
                                 overlay_root=base_path)
            with Image.open(final) as image:
                actual_size = list(image.size)
                alpha = image.convert("RGBA").getchannel("A").getextrema()
            problems = []
            target = D.formats()[fmt]["export_px"]
            if actual_size != target:
                problems.append(f"实际尺寸 {actual_size} 未达到配置目标 {target}，需核对平台要求")
            if not D.formats()[fmt].get("transparent") and alpha[0] != 255:
                problems.append("非透明格式含透明像素")
            if fmt == "sticker-single":
                sticker = check_single(final)
                rec["sticker_check"] = sticker
                problems.extend(sticker["problems"])
            rec.update(status="pending_visual_review" if not problems else "pixel_failed",
                       final=str(final), final_sha256=_sha(final), extras=[str(p) for p in exported.extras],
                       warnings=exported.warnings, pixel_check={"passed": not problems, "problems": problems,
                                                               "size": actual_size})
        except Exception as exc:
            if getattr(exc, "kind", None) in {"auth", "verify", "unconfigured"}:
                stopped.set()
            rec["error"] = {"type": type(exc).__name__, "message": str(exc)}
        _write(out / f"{ident}-receipt.json", rec)
        return rec

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(generate_one, prepared["tasks"]))
    overview = out / "overview.png"
    matrix_sheet([{"image": r.get("final"), "label": r["id"],
                   "status": "pending" if r["status"] == "pending_visual_review" else "failed"}
                  for r in results], overview, cols=min(jobs, len(results)), title="Visual review required")
    source_unchanged = prepared["source"] is None or _sha(Path(prepared["source"]["path"])) == prepared["source"]["sha256"]
    failed = sum(r["status"] != "pending_visual_review" for r in results)
    report = {"status": "failed" if failed or not source_unchanged else "pending_visual_review",
              "selection": prepared["selection"], "items": results, "failed": failed,
              "source_unchanged": source_unchanged, "overview": str(overview),
              "seconds": round(time.monotonic() - started, 3), "visual_review": "not_run", "accepted": False}
    _write(out / "report.json", report)
    return report


def import_host(brief: dict, prepared_path: Path, results_path: Path, out: Path, *, base_path: Path) -> dict:
    """Import actual built-in outputs without inventing a provider model or cost."""
    from .backends.base import Result
    prepared_bytes, results_bytes = prepared_path.read_bytes(), results_path.read_bytes()
    prepared_digest, results_digest = hashlib.sha256(prepared_bytes).hexdigest(), hashlib.sha256(results_bytes).hexdigest()
    prepared, results = json.loads(prepared_bytes), json.loads(results_bytes)
    if not isinstance(results, dict) or results.get("version") != 1 or results.get("prepared_sha256") != prepared_digest:
        raise BriefError("内置成图记录未绑定当前 prepared 文件")
    tasks = prepared.get("tasks", [])
    if not tasks:
        raise BriefError("prepared 没有任务")
    model = tasks[0]["compiled"]["model"]
    current = prepare(brief, base_path=base_path, model=model)
    if current != prepared:
        raise BriefError("原文、brief、合同或参考图已变化；旧内置成图记录不可复用")
    images = results.get("images")
    if not isinstance(images, dict) or set(images) != {t["id"] for t in tasks}:
        raise BriefError("内置成图必须逐一对应全部任务，不能缺图或多图")
    for task in tasks:
        image = images[task["id"]]
        if not isinstance(image, dict):
            raise BriefError("内置成图记录必须是对象")
        arguments = image.get("tool_arguments", {})
        if not isinstance(arguments, dict) or not isinstance(image.get("path"), str):
            raise BriefError("内置成图缺少工具参数对象或原图路径")
        transparent = bool(D.formats()[task["manifest"]["format"]].get("transparent"))
        expected_paths = [r["path"] for r in task["compiled"]["references"]]
        if (image.get("prompt_sha256") != task["prompt_sha256"]
                or arguments.get("prompt") != task["compiled"]["prompt"]
                or arguments.get("referenced_image_paths", []) != expected_paths
                or arguments.get("transparent_background") is not transparent
                or image.get("reference_sha256s") != [r["sha256"] for r in task["references"]]):
            raise BriefError(f"{task['id']} 的实际工具参数或参考图摘要与编译任务不一致")
        if image.get("provider") != "codex_builtin" or image.get("model") is not None or image.get("est_usd") is not None:
            raise BriefError("内置工具未提供模型和费用，记录必须保持未知")
        seconds = image.get("seconds")
        if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0):
            raise BriefError("实际耗时须是非负秒数或未知")
        path = Path(image["path"])
        if not path.is_absolute():
            path = results_path.parent / path
        if _sha(path) != image.get("sha256"):
            raise BriefError(f"{task['id']} 的工具原图摘要不一致")
        image["resolved_path"] = str(path)

    def copy_actual(prompt, raw, *, tag, **kwargs):
        ident = tag.split(":", 1)[1]
        image = images[ident]
        shutil.copyfile(image["resolved_path"], raw)
        if _sha(raw) != image["sha256"]:
            raise BriefError("复制期间原图发生变化")
        return Result(raw.read_bytes(), "codex_builtin", None, 1, image.get("seconds"), None)

    report = run(brief, out, base_path=base_path, model=model, gen_fn=copy_actual)
    (out / "host-results.json").write_bytes(results_bytes)
    (out / "host-prepared.json").write_bytes(prepared_bytes)
    report["host_import"] = {"prepared_sha256": prepared_digest, "results_sha256": results_digest,
                             "generation_seconds": sum(i["seconds"] for i in images.values())
                             if all(i.get("seconds") is not None for i in images.values()) else None,
                             "seconds_scope": "report.seconds measures import/export, not full generation workflow"}
    _write(out / "report.json", report)
    return report
