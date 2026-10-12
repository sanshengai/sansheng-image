"""配图计划的独立复核：默认 Coding Plan deepseek-v4.1-flash 只读原文与计划，逐张判断是否合理。

判断口径（与 references/planning.md 一致）：这里值不值得配图；信息形状判得对不对；表达形式与结构合不合适；
依据句站不站得住；图中文字是否忠于原文、能否让读者理解或行动；
有没有把文中比喻按字面画出来、有没有要画真实人物的脸。
"""
from __future__ import annotations

import json
import copy
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .. import plan as PL
from .reviewer import (ARK_MODEL, ARK_PLAN_URL, DEFAULT_MODEL, QuotaExhausted,
                       ReviewerAuthUnavailable, ReviewerNetworkUnavailable,
                       _ark_text, _claude_bin, _extract)

SCHEMA = {
    "type": "object", "required": ["items", "missed_positions"],
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "required": ["id", "reasonable", "why", "position_ok", "shape_ok", "form_ok", "basis_ok", "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok"], "properties": {
            "id": {"type": "string"}, "reasonable": {"type": "boolean"}, "why": {"type": "string"},
            "position_ok": {"type": "boolean"}, "shape_ok": {"type": "boolean"}, "form_ok": {"type": "boolean"},
            "basis_ok": {"type": "boolean"}, "no_literal_metaphor_or_real_face": {"type": "boolean"},
            "fidelity_ok": {"type": "boolean"}, "reader_value_ok": {"type": "boolean"}}}},
        "missed_positions": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
}


def prompt(plan: dict, article: Path) -> str:
    rows = []
    for it in plan["items"]:
        rows.append({k: it.get(k) for k in ("id", "position", "source_quote", "shape", "form", "structure", "points", "what", "subject", "inventory", "relations", "text", "why")})
    shapes = "\n".join(f"- {k}（{v['zh']}）：{v['def']}；表达为{ '、'.join(PL.FORM_ZH[f] for f in v['forms']) }"
                       + (f"，可用结构 {', '.join(v['structures'])}" if v.get("structures") else "") for k, v in PL.SHAPES.items())
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(PL.PLANNING_RULES, 1))
    text = "\n".join([
        "你是一名严格的编辑，复核一份文章配图计划。先用 Read 工具完整读原文，再逐张判断。",
        "只按下面这套定义和判别要点来判——计划员用的是同一套，不要换成你自己的分类口径。",
        f"原文：{article}",
        "",
        "## 信息形状的定义",
        shapes,
        "",
        "## 判别要点",
        rules,
        "",
        "## 每张七项（都成立才算 reasonable=true）",
        "1. position_ok：位置符合判别要点 1、3、4、5。",
        "2. shape_ok：信息形状按上面的定义判得对。",
        "3. form_ok：表达形式与结构适合这段内容；要点与画面描述符合判别要点 6、7、8、10。",
        "4. basis_ok：依据句（why）说清了为什么在这里配图、为什么是这个形状，而且站得住。",
        "5. no_literal_metaphor_or_real_face：符合判别要点 9。",
        "6. fidelity_ok：对照 source_quote 所在段落及前后文，逐项检查 text.items、points、subject、inventory、relations。画面必需的题材线索不能被换成无关物件；标题保留原文核心信息；数字、效果与能力边界有原文依据；有条件的效果没有变成绝对保证。",
        "7. reader_value_ok：缩成图后仍有足够的具体信息让读者理解这段的机制、判断或下一步行动；不能只剩泛化口号或类别名称。",
        "reasonable=false 时，why 写具体哪项不成立、应该怎么改。先独立提炼全文 2–5 个核心论点、关键机制、结论／行动建议和最具体操作，再检查每项是否已有能解决同一阅读问题的视觉、计划配图，或文字已经足够清楚。missed_positions 列出尚未覆盖且有明确独立视觉增益的核心项；每条引用原文，说明现有表格／截图／清单为什么仍不足，以及应补出的关系、分叉或边界。只属可选美化的建议放 notes，不计入 missed_positions。没有必配漏项就输出空数组。",
        "",
        "计划：",
        json.dumps(rows, ensure_ascii=False, indent=1),
        "",
        "计划员逐项覆盖判断（只当作待核对主张；请自己从原文重新提炼，不要直接采信 text_sufficient 或 existing_visual）：",
        json.dumps(plan.get("coverage", []), ensure_ascii=False, indent=1),
        "",
        "只输出 JSON：{\"items\":[{\"id\",\"reasonable\",\"position_ok\",\"shape_ok\",\"form_ok\",\"basis_ok\",\"no_literal_metaphor_or_real_face\",\"fidelity_ok\",\"reader_value_ok\",\"why\"}],\"missed_positions\":[str],\"notes\":str}",
    ])
    if "cover_brief" in plan:
        text += ("\n\n## 独立封面主题复核\n"
                 "这是独立封面，不是正文配图，不使用正文插图的位置或信息形状规则。"
                 "对照全文检查封面主题能否代表文章，文字与图形暗示是否忠实，是否新增无原文依据的承诺、身份或效果。"
                 "在上述 JSON 追加 cover_review={fidelity_ok:bool,theme_ok:bool,no_unrequested_claims:bool,why:str}。"
                 "每项只按全文与封面简报判断，why 写具体依据。\n"
                 + json.dumps(plan["cover_brief"], ensure_ascii=False, indent=1))
    return text


def schema(plan: dict) -> dict:
    result = copy.deepcopy(SCHEMA)
    if "cover_brief" in plan:
        result["required"].append("cover_review")
        result["properties"]["cover_review"] = {
            "type": "object", "required": ["fidelity_ok", "theme_ok", "no_unrequested_claims", "why"],
            "properties": {"fidelity_ok": {"type": "boolean"}, "theme_ok": {"type": "boolean"},
                           "no_unrequested_claims": {"type": "boolean"}, "why": {"type": "string"}}}
    return result


def cover_complete(data: dict | None, plan: dict) -> bool:
    if "cover_brief" not in plan:
        return True
    review = data.get("cover_review") if isinstance(data, dict) else None
    return (isinstance(review, dict) and all(type(review.get(key)) is bool for key in
            ("fidelity_ok", "theme_ok", "no_unrequested_claims"))
            and isinstance(review.get("why"), str) and bool(review["why"].strip()))


def _complete(data: dict | None, plan: dict) -> bool:
    expected = {it["id"] for it in plan["items"]}
    actual = ([i.get("id") for i in data["items"]]
              if isinstance(data, dict) and isinstance(data.get("items"), list)
              and all(isinstance(i, dict) for i in data["items"]) else [])
    return bool(data and cover_complete(data, plan) and len(actual) == len(expected) and set(actual) == expected
                and isinstance(data.get("missed_positions"), list)
                and all(isinstance(pos, str) and pos.strip() for pos in data["missed_positions"])
                and all(isinstance(i.get("why"), str) and i["why"].strip() for i in data["items"]))


def _output_budget(plan: dict) -> int:
    default = min(32000, max(5000, 1500 + 1000 * len(plan["items"])))
    raw = os.environ.get("SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS", str(default))
    try:
        budget = int(raw)
    except ValueError as exc:
        raise ValueError("SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS 必须是 2000–32000 的整数") from exc
    if not 2000 <= budget <= 32000:
        raise ValueError("SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS 必须是 2000–32000 的整数")
    return budget


def _review_ark(plan: dict, article: Path, model: str | None, timeout: int) -> dict:
    base = os.environ.get("ARK_AGENT_PLAN_BASE_URL", "").rstrip("/")
    key = os.environ.get("ARK_AGENT_PLAN_API_KEY", "")
    if base != ARK_PLAN_URL or not key:
        raise ReviewerAuthUnavailable("配图计划复核只使用已配置的 Ark Agent Plan 套餐地址与 Key")
    name = model or ARK_MODEL
    text = prompt(plan, article).replace("先用 Read 工具完整读原文，再逐张判断。",
                                         "原文全文已附在后面，请先完整阅读，再逐张判断。")
    text += "\n\n## 原文全文（只读）\n" + article.read_text(encoding="utf-8")
    budget = _output_budget(plan)
    body = json.dumps({"model": name, "input": [{"role": "user", "content": [
        {"type": "input_text", "text": text}]}], "max_output_tokens": budget,
        **({"reasoning": {"effort": "low"}}
           if name.startswith("doubao-seed-") else {})},
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(base + "/responses", body,
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as handle:
            response = json.loads(handle.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise QuotaExhausted("Ark Agent Plan 配图计划复核额度用尽或限流") from exc
        if exc.code in (401, 403):
            raise ReviewerAuthUnavailable("Ark Agent Plan 配图计划复核鉴权或套餐访问被拒") from exc
        raise RuntimeError(f"Ark Agent Plan 配图计划复核 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ReviewerNetworkUnavailable(f"Ark Agent Plan 配图计划复核网络不可用：{exc.reason}") from exc
    usage = response.get("usage") or {}
    details = response.get("incomplete_details") or {}
    metadata = {"status": response.get("status"), "max_output_tokens": budget,
                "usage": {key: usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")
                          if isinstance(usage, dict) and type(usage.get(key)) is int}}
    if isinstance(details, dict) and isinstance(details.get("reason"), str):
        metadata["incomplete_reason"] = details["reason"][:200]
    if response.get("status") != "completed":
        raise RuntimeError("Ark Agent Plan 配图计划复核未完成：" + json.dumps(metadata, ensure_ascii=False))
    normalize = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
    returned = str(response.get("model") or "")
    if not normalize(returned).startswith(normalize(name)):
        raise RuntimeError(f"Ark Agent Plan 配图计划复核模型漂移：{returned}")
    data = _extract(_ark_text(response))
    if not _complete(data, plan):
        raise RuntimeError("Ark Agent Plan 配图计划复核没有给出完整结论")
    for item in data["items"]:
        item["reasonable"] = _item_passes(item)
    data["_reviewer"] = f"ark_agent_plan:{returned}"
    data["_response_metadata"] = metadata
    return data


def review(plan: dict, article: Path, model: str | None = None, timeout: int = 600) -> dict:
    article = Path(article).resolve()
    backend = os.environ.get("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND") or os.environ.get("SANSHENG_IMAGE_QA_BACKEND", "ark_coding_plan")
    if backend == "ark_coding_plan":
        from .coding_plan import request
        text = prompt(plan, article).replace("先用 Read 工具完整读原文，再逐张判断。", "根据下附完整原文逐张判断。")
        text += "\n\n## 完整原文\n" + article.read_text(encoding="utf-8")
        answer, response = request([{"type": "text", "text": text}], model=model,
                                   timeout=timeout, budget=_output_budget(plan))
        data = _extract(answer)
        if not _complete(data, plan):
            raise RuntimeError("Coding Plan 配图计划复核缺少完整结论；不自动重试")
        for item in data["items"]:
            item["reasonable"] = _item_passes(item)
        data["_reviewer"] = f"ark_coding_plan:{response['model']}"
        data["_response_metadata"] = response
        return data
    if backend == "ark_agent_plan":
        return _review_ark(plan, article, model, timeout)
    if backend != "claude_cli":
        raise ValueError(f"未知配图计划复核后端：{backend}")
    claude = _claude_bin()
    if not claude:
        raise RuntimeError("找不到 claude 命令行")
    work = Path(tempfile.mkdtemp(prefix="stylebook-plan-"))
    cmd = [claude, "-p", "--model", model or DEFAULT_MODEL, "--output-format", "json",
           "--json-schema", json.dumps(schema(plan), ensure_ascii=False), "--tools", "Read", "--add-dir", str(article.parent),
           "--permission-mode", "dontAsk", "--no-session-persistence", "--strict-mcp-config"]
    last = ""
    for _ in range(2):
        cp = subprocess.run(cmd, cwd=str(work), input=prompt(plan, article), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout, check=False)
        data = _extract(cp.stdout) if cp.returncode == 0 else None
        if _complete(data, plan):
            for item in data["items"]:
                item["reasonable"] = _item_passes(item)
            data["_reviewer"] = model or DEFAULT_MODEL
            return data
        last = (cp.stdout or cp.stderr)[-300:]
    raise RuntimeError(f"复核进程没有给出完整结论：{last}")


def summarize(results: list[tuple[str, dict]], *, allow_no_images: bool = False) -> dict:
    for article, result in results:
        missed = result.get("missed_positions")
        if not isinstance(missed, list) or any(not isinstance(pos, str) or not pos.strip() for pos in missed):
            raise ValueError(f"{article} 缺少有效的必配漏项结论（missed_positions）")
    total = sum(len(r["items"]) for _, r in results)
    ok = sum(1 for _, r in results for i in r["items"] if _item_passes(i))
    missed_count = sum(len(r["missed_positions"]) for _, r in results)
    denominator = total + missed_count
    return {"total": total, "reasonable": ok, "rate": round(ok / total, 3) if total else 0.0,
            "required_missed": missed_count,
            "effective_rate": round(ok / denominator, 3) if denominator else 0.0,
            "no_new_images": bool(allow_no_images and denominator == 0),
            "qualified": (denominator == 0 and allow_no_images) or
                         (denominator > 0 and 10 * ok >= 9 * denominator),
            "failed": [{"article": a, "id": i["id"], "why": i.get("why", "")} for a, r in results for i in r["items"] if not _item_passes(i)],
            "missed": {a: r.get("missed_positions", []) for a, r in results}}


def _item_passes(item: dict) -> bool:
    return all(item.get(key) is True for key in (
        "reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
        "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok",
    ))
