"""配图计划的独立复核：默认 Coding Plan deepseek-v4.1-flash 只读原文与计划，逐张判断是否合理。

判断口径（与 references/planning.md 一致）：这里值不值得配图；信息形状判得对不对；表达形式与结构合不合适；
依据句站不站得住；图中文字是否忠于原文、能否让读者理解或行动；
有没有把文中比喻按字面画出来、有没有要画真实人物的脸。

三个后端（唯一解析入口 resolve_backend）：
- ark_coding_plan（默认）：火山方舟 Coding Plan，deepseek-v4.1-flash，原文全文内联；
- ark_agent_plan：火山方舟 Agent Plan，doubao-seed-2.1-turbo，原文全文内联；
- claude_cli：本机 claude 命令行，用 Read 工具读原文；不设输出预算（由 CLI 自己管理）。
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .. import plan as PL
from . import coding_plan as CPL
from .reviewer import (ARK_MODEL, ARK_PLAN_URL, DEFAULT_MODEL, QuotaExhausted,
                       ReviewerAuthUnavailable, ReviewerNetworkUnavailable,
                       _ark_text, _auth_hit, _claude_bin, _quota_hit)

BACKENDS = ("ark_coding_plan", "ark_agent_plan", "claude_cli")
DEFAULT_BACKEND = "ark_coding_plan"
BACKEND_ENV = ("SANSHENG_IMAGE_PLAN_REVIEW_BACKEND", "SANSHENG_IMAGE_QA_BACKEND")
BUDGET_ENV = "SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS"
MAX_ATTEMPTS = 2  # 格式性缺口：首问 + 点名缺项重问 1 次
FIXED_MARGIN = 1500    # missed_positions / notes 的固定余量（tokens）
COVER_MARGIN = 800     # 有封面简报时 cover_review 的固定余量

ITEM_BOOLS = ("reasonable", "position_ok", "shape_ok", "form_ok", "basis_ok",
              "no_literal_metaphor_or_real_face", "fidelity_ok", "reader_value_ok")
COVER_BOOLS = ("fidelity_ok", "theme_ok", "no_unrequested_claims")

SCHEMA = {
    "type": "object", "required": ["items", "missed_positions"],
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "required": ["id", *ITEM_BOOLS, "why"], "properties": {
            "id": {"type": "string"}, "reasonable": {"type": "boolean"}, "why": {"type": "string"},
            "position_ok": {"type": "boolean"}, "shape_ok": {"type": "boolean"}, "form_ok": {"type": "boolean"},
            "basis_ok": {"type": "boolean"}, "no_literal_metaphor_or_real_face": {"type": "boolean"},
            "fidelity_ok": {"type": "boolean"}, "reader_value_ok": {"type": "boolean"}}}},
        "missed_positions": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
}


class PlanReviewIncomplete(RuntimeError):
    """模型答了，但缺项/格式不合格，重问后仍然如此。保留原始回答与逐条缺口，供消费者记录与诊断。"""

    def __init__(self, backend: str, gaps: list[str], answers: list[str], metadata=None, attempts: int = 1):
        self.backend, self.gaps, self.answers = backend, list(gaps), list(answers)
        self.metadata, self.attempts = metadata, attempts
        last = answers[-1] if answers else ""
        super().__init__(
            f"{backend} 配图计划复核没有给出完整结论（已问 {attempts} 次，第二次已点名缺项）。缺口：" + "；".join(gaps[:8])
            + (f"（共 {len(gaps)} 处）" if len(gaps) > 8 else "")
            + f"。最后一次原始回答（前 400 字）：{CPL.snippet(last, 400)}")

    def to_report(self) -> dict:
        return {"error": "plan_review_incomplete", "backend": self.backend, "attempts": self.attempts,
                "gaps": self.gaps, "raw_answers": self.answers, "response_metadata": self.metadata}


# ---------------------------------------------------------------- 后端解析

def resolve_backend(explicit: str | None = None) -> str:
    """唯一的后端解析：显式参数 > SANSHENG_IMAGE_PLAN_REVIEW_BACKEND > SANSHENG_IMAGE_QA_BACKEND > 默认 ark_coding_plan。

    未知值直接抛 ValueError。写作 Skill 等调用方记录的“后端”必须取自这里（或结果里的 `_backend`），不另设默认。
    """
    value = explicit or next((os.environ[k] for k in BACKEND_ENV if os.environ.get(k)), None) or DEFAULT_BACKEND
    if value not in BACKENDS:
        raise ValueError(f"未知配图计划复核后端：{value}；可选 {' / '.join(BACKENDS)}。{backend_guidance()}")
    return value


def backend_guidance(backend: str | None = None) -> str:
    """缺凭证/不可用时给用户的配置指引（三后端对照，详见 references/backends.md「独立复核后端」）。"""
    lines = ["可选的独立复核后端（环境变量 SANSHENG_IMAGE_PLAN_REVIEW_BACKEND 选择，未设置默认 ark_coding_plan）："]
    lines.append("- ark_coding_plan（默认）：需要 ARK_CODING_PLAN_BASE_URL=" + CPL.BASE_URL + " 与 ARK_CODING_PLAN_API_KEY（火山方舟 Coding Plan 套餐）")
    lines.append("- ark_agent_plan：需要 ARK_AGENT_PLAN_BASE_URL=" + ARK_PLAN_URL + " 与 ARK_AGENT_PLAN_API_KEY（火山方舟 Agent Plan 套餐）")
    lines.append("- claude_cli：本机已安装并登录 claude 命令行（Claude Code），不需要 Ark Key；用 SANSHENG_IMAGE_QA_CLAUDE 指向可执行文件")
    lines.append("密钥写进 ~/.config/sansheng-image/.env（运行 `python3 scripts/sb.py setup --env-template` 生成模板）或环境变量，不要发到对话里。")
    if backend:
        lines.insert(0, f"当前后端：{backend}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 提示词

def _item_lines() -> list[str]:
    r = PL.rule_no  # 全部按稳定 key 取当前编号；key 不存在会抛 KeyError
    return [
        f"1. position_ok：位置符合判别要点 {r('value', 'per_section', 'placement', 'existing_visual')}。",
        f"2. shape_ok：信息形状按上面的定义判得对；并列类别、分工的画法符合判别要点 {r('parallel')}。",
        f"3. form_ok：表达形式与结构适合这段内容；要点与画面描述符合判别要点 {r('data_basis', 'causal_chain', 'same_structure', 'title_text')}。",
        f"4. basis_ok：依据句（why）说清了为什么在这里配图、为什么是这个形状，而且站得住（判别要点 {r('core_points')}）。",
        f"5. no_literal_metaphor_or_real_face：符合判别要点 {r('metaphor_face')}。",
        f"6. fidelity_ok：对照 source_quote 所在段落及前后文，逐项检查 text.items、points、subject、inventory、relations（判别要点 {r('faithful', 'title_text', 'claims_evidence')}）。画面必需的题材线索不能被换成无关物件；标题保留原文核心信息；数字、效果与能力边界有原文依据；有条件的效果没有变成绝对保证。",
        f"7. reader_value_ok：缩成图后仍有足够的具体信息让读者理解这段的机制、判断或下一步行动（判别要点 {r('claims_evidence', 'title_text')}）；不能只剩泛化口号或类别名称。",
    ]


def _format_section(plan: dict) -> str:
    item = {"id": "<计划里的图片 id>", **{k: True for k in ITEM_BOOLS}, "why": "<具体依据；任一项为 false 时写明哪项不成立、应该怎么改>"}
    example: dict = {"items": [item], "missed_positions": ["<必配漏项：引用原文并说明应补出的关系/分叉/边界；没有就留空数组>"]}
    rules = [
        "items：计划里的每个 id 恰好一条，不增不减；每条的八个布尔字段（" + "、".join(ITEM_BOOLS) + "）都必须是 JSON 的 true 或 false，不是字符串，不得省略。",
        "why：每条必填、非空。",
        "missed_positions：字符串数组，每条非空；没有必配漏项就输出 []。",
        "notes：可选字符串，只放不计入通过判定的建议。",
    ]
    if "cover_brief" in plan:
        example["cover_review"] = {"fidelity_ok": True, "theme_ok": True, "no_unrequested_claims": True, "why": "<具体依据>"}
        rules.append("cover_review：本计划有封面简报，所以它是顶层必填对象，与 items 并列；三个布尔字段（" + "、".join(COVER_BOOLS)
                     + "）必须是 true/false，why 非空。封面结论写在 cover_review 里，写进 notes 或 items 都视为缺失。")
    example["notes"] = "<可选>"
    return ("## 输出格式（最后一步，务必遵守）\n"
            "只输出一个 JSON 对象：不要 Markdown 代码围栏，不要 JSON 之外的任何文字，不要输出多个 JSON。"
            "结构如下（值仅示意格式，真实取值按你的判断）：\n"
            + json.dumps(example, ensure_ascii=False, indent=1) + "\n" + "\n".join(f"- {x}" for x in rules))


def prompt(plan: dict, article: Path, mode: str = "read_tool", *, gaps: list[str] | None = None) -> str:
    """完整提示词由代码生成。

    mode="read_tool"：让模型用 Read 工具读 `article`（claude_cli）；mode="inline"：把原文全文附在提示词里（Ark 两个后端）。
    gaps 非空时在格式说明前插入「上一次回答的缺口」，用于重问。默认 read_tool 以保持旧调用不读文件。
    """
    if mode not in ("inline", "read_tool"):
        raise ValueError(f"prompt mode 只能是 inline 或 read_tool：{mode!r}")
    rows = []
    for it in plan["items"]:
        rows.append({k: it.get(k) for k in ("id", "position", "source_quote", "shape", "form", "structure", "points", "what", "subject", "inventory", "relations", "text", "why")})
    shapes = "\n".join(f"- {k}（{v['zh']}）：{v['def']}；表达为{ '、'.join(PL.FORM_ZH[f] for f in v['forms']) }"
                       + (f"，可用结构 {', '.join(v['structures'])}" if v.get("structures") else "") for k, v in PL.SHAPES.items())
    rules = "\n".join(f"{i}. {text}" for i, (_key, text) in enumerate(PL.PLANNING_RULE_ITEMS, 1))
    intro = ("你是一名严格的编辑，复核一份文章配图计划。先用 Read 工具完整读原文，再逐张判断。"
             if mode == "read_tool" else
             "你是一名严格的编辑，复核一份文章配图计划。原文全文附在后面「完整原文」一节，请先完整阅读，再逐张判断。")
    parts = [
        intro,
        "只按下面这套定义和判别要点来判——计划员用的是同一套，不要换成你自己的分类口径。",
    ]
    if mode == "read_tool":
        parts.append(f"原文：{article}")
    parts += [
        "",
        "## 信息形状的定义",
        shapes,
        "",
        "## 判别要点",
        rules,
        "",
        "## 每张七项（都成立才算 reasonable=true）",
        *_item_lines(),
        f"reasonable=false 时，why 写具体哪项不成立、应该怎么改。先独立提炼全文 2–5 个核心论点、关键机制、结论／行动建议和最具体操作（判别要点 {PL.rule_no('core_points', 'final_check')}），再检查每项是否已有能解决同一阅读问题的视觉、计划配图，或文字已经足够清楚。missed_positions 列出尚未覆盖且有明确独立视觉增益的核心项；每条引用原文，说明现有表格／截图／清单为什么仍不足，以及应补出的关系、分叉或边界。只属可选美化的建议放 notes，不计入 missed_positions。没有必配漏项就输出空数组。",
        "",
        "计划：",
        json.dumps(rows, ensure_ascii=False, indent=1),
        "",
        "计划员逐项覆盖判断（只当作待核对主张；请自己从原文重新提炼，不要直接采信 text_sufficient 或 existing_visual）：",
        json.dumps(plan.get("coverage", []), ensure_ascii=False, indent=1),
    ]
    if "cover_brief" in plan:
        parts += ["", "## 独立封面主题复核",
                  "这是独立封面，不是正文配图，不使用正文插图的位置或信息形状规则。"
                  "对照全文检查封面主题能否代表文章，文字与图形暗示是否忠实，是否新增无原文依据的承诺、身份或效果。"
                  "每项只按全文与封面简报判断，why 写具体依据；结论写进输出里的顶层 cover_review（见最后的输出格式）。",
                  json.dumps(plan["cover_brief"], ensure_ascii=False, indent=1)]
    if mode == "inline":
        parts += ["", "## 完整原文（只读）", Path(article).read_text(encoding="utf-8")]
    if gaps:
        parts += ["", "## 上一次回答不合格，必须修正",
                  *[f"- {g}" for g in gaps[:20]],
                  "请重新输出完整的 JSON（所有 items 与全部顶层字段），不要只补缺失部分。"]
    parts += ["", _format_section(plan)]
    return "\n".join(parts)


def schema(plan: dict) -> dict:
    result = copy.deepcopy(SCHEMA)
    if "cover_brief" in plan:
        result["required"].append("cover_review")
        result["properties"]["cover_review"] = {
            "type": "object", "required": [*COVER_BOOLS, "why"],
            "properties": {"fidelity_ok": {"type": "boolean"}, "theme_ok": {"type": "boolean"},
                           "no_unrequested_claims": {"type": "boolean"}, "why": {"type": "string"}}}
    return result


# ---------------------------------------------------------------- 回答提取与校验

def _extract(text: str) -> dict | None:
    """从模型输出里稳健地取出 JSON 对象：支持 claude 信封、代码围栏、前后夹带说明、含花括号的文字。"""
    text = (text or "").strip()
    try:
        env = json.loads(text)
    except json.JSONDecodeError:
        env = None
    if isinstance(env, dict):
        if isinstance(env.get("structured_output"), dict):
            return env["structured_output"]
        inner = env.get("result")
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, str):
            text = inner.strip()
        else:
            return env
    decoder = json.JSONDecoder()
    found, i = [], 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            found.append((end - i, obj))
        i = end
    if not found:
        return None
    with_items = [x for x in found if "items" in x[1]]
    return max(with_items or found, key=lambda x: x[0])[1]


def cover_gaps(data: dict | None, plan: dict) -> list[str]:
    if "cover_brief" not in plan:
        return []
    review = data.get("cover_review") if isinstance(data, dict) else None
    if not isinstance(review, dict):
        return ["缺少顶层 cover_review 对象（封面结论不能只写在 notes 里）"]
    gaps = [f"cover_review.{k} 必须是 true/false" for k in COVER_BOOLS if type(review.get(k)) is not bool]
    if not (isinstance(review.get("why"), str) and review["why"].strip()):
        gaps.append("cover_review.why 必须是非空字符串")
    return gaps


def cover_complete(data: dict | None, plan: dict) -> bool:
    return not cover_gaps(data, plan)


def gaps_of(data: dict | None, plan: dict) -> list[str]:
    """逐条列出回答的缺口；空列表才算完整。"""
    if not isinstance(data, dict):
        return ["回答里没有可解析的 JSON 对象"]
    gaps: list[str] = []
    expected = [it["id"] for it in plan["items"]]
    items = data.get("items")
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        gaps.append("items 必须是对象数组")
        items = []
    ids = [i.get("id") for i in items]
    if missing := [x for x in expected if x not in ids]:
        gaps.append(f"items 缺少这些 id：{', '.join(map(str, missing))}")
    if extra := sorted({str(x) for x in ids if x not in expected}):
        gaps.append(f"items 多出计划里没有的 id：{', '.join(extra)}")
    if dup := sorted({str(x) for x in ids if ids.count(x) > 1}):
        gaps.append(f"items 里 id 重复：{', '.join(dup)}")
    for item in items:
        label = f"item {item.get('id')}"
        bad = [k for k in ITEM_BOOLS if type(item.get(k)) is not bool]
        if bad:
            gaps.append(f"{label} 的 {', '.join(bad)} 必须是 true/false")
        if not (isinstance(item.get("why"), str) and item["why"].strip()):
            gaps.append(f"{label} 缺少非空 why")
    missed = data.get("missed_positions")
    if not (isinstance(missed, list) and all(isinstance(p, str) and p.strip() for p in missed)):
        gaps.append("missed_positions 必须是非空字符串数组（没有漏项就输出 []）")
    return gaps + cover_gaps(data, plan)


def _complete(data: dict | None, plan: dict) -> bool:
    return not gaps_of(data, plan)


def _output_budget(plan: dict) -> int:
    margin = FIXED_MARGIN + (COVER_MARGIN if "cover_brief" in plan else 0)
    default = min(32000, max(5000, 1500 + 1000 * len(plan["items"]) + margin))
    raw = os.environ.get(BUDGET_ENV, str(default))
    try:
        budget = int(raw)
    except ValueError as exc:
        raise ValueError(f"{BUDGET_ENV} 必须是 2000–32000 的整数") from exc
    if not 2000 <= budget <= 32000:
        raise ValueError(f"{BUDGET_ENV} 必须是 2000–32000 的整数")
    return budget


# ---------------------------------------------------------------- 三个后端的传输层（只负责“发一次、拿回文本”）

_LENGTH_HINT = f"调大环境变量 {BUDGET_ENV}（2000–32000）后重试"


def _ask_coding_plan(ask: str, *, model, timeout, budget, **_):
    answer, response = CPL.request([{"type": "text", "text": ask}], model=model, timeout=timeout,
                                   budget=budget, temperature=0, length_hint=_LENGTH_HINT)
    return answer, f"ark_coding_plan:{response['model']}", response


def _ask_agent_plan(ask: str, *, model, timeout, budget, **_):
    base = os.environ.get("ARK_AGENT_PLAN_BASE_URL", "").rstrip("/")
    key = os.environ.get("ARK_AGENT_PLAN_API_KEY", "")
    name = model or ARK_MODEL
    body = json.dumps({"model": name, "input": [{"role": "user", "content": [
        {"type": "input_text", "text": ask}]}], "max_output_tokens": budget,
        **({"reasoning": {"effort": "low"}} if name.startswith("doubao-seed-") else {})},
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(base + "/responses", body,
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    label = "Ark Agent Plan 配图计划复核"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as handle:
            response = json.loads(handle.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CPL.classify_http_error(exc, label) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise CPL.classify_transport_error(exc, label, timeout) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} 返回的不是 JSON：{exc}") from exc
    usage = response.get("usage") or {}
    details = response.get("incomplete_details") or {}
    metadata = {"status": response.get("status"), "max_output_tokens": budget,
                "usage": {k: usage[k] for k in ("input_tokens", "output_tokens", "total_tokens")
                          if isinstance(usage, dict) and type(usage.get(k)) is int}}
    if isinstance(details, dict) and isinstance(details.get("reason"), str):
        metadata["incomplete_reason"] = details["reason"][:200]
    if response.get("status") != "completed":
        hint = f"；{_LENGTH_HINT}" if metadata.get("incomplete_reason") == "max_output_tokens" else ""
        raise RuntimeError(f"{label}未完成：" + json.dumps(metadata, ensure_ascii=False) + hint)
    normalize = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
    returned = str(response.get("model") or "")
    if not normalize(returned).startswith(normalize(name)):
        raise RuntimeError(f"{label}模型漂移：{returned}")
    return _ark_text(response), f"ark_agent_plan:{returned}", metadata


def _claude_failure(cp) -> Exception | None:
    """claude 进程的额度/鉴权/退出码分类。只在失败信号（非零退出、is_error 信封、stderr）里找，
    不扫描正常回答正文——文章若正好谈到 rate limit，不能被误判成额度用尽。"""
    pools = [cp.stderr or ""]
    errored = cp.returncode != 0
    try:
        env = json.loads(cp.stdout or "")
        if isinstance(env, dict) and env.get("is_error"):
            errored = True
            pools.append(str(env.get("result") or ""))
    except json.JSONDecodeError:
        pass
    if errored:
        pools.append(cp.stdout or "")
    joined = "\n".join(pools)
    if errored and _quota_hit(joined):
        return QuotaExhausted("claude 配图计划复核额度用尽或被限流，稍后再试")
    if errored and _auth_hit(joined):
        return ReviewerAuthUnavailable("claude 命令行未登录或订阅访问被禁用；先在终端运行 claude 完成登录，或改用 ark_coding_plan 后端。"
                                       + CPL.SETUP_HINT)
    if cp.returncode != 0:
        return RuntimeError(f"claude 复核进程退出码 {cp.returncode}：{CPL.snippet(cp.stderr or cp.stdout)}")
    return None


def _ask_claude(ask: str, *, plan, article, model, timeout, **_):
    claude = _claude_bin()
    if not claude:
        raise ReviewerAuthUnavailable("找不到 claude 命令行。安装并登录 Claude Code，或用 SANSHENG_IMAGE_QA_CLAUDE 指向它；"
                                      "也可改用 ark_coding_plan / ark_agent_plan 后端。" + CPL.SETUP_HINT)
    work = Path(tempfile.mkdtemp(prefix="stylebook-plan-"))
    cmd = [claude, "-p", "--model", model or DEFAULT_MODEL, "--output-format", "json",
           "--json-schema", json.dumps(schema(plan), ensure_ascii=False), "--tools", "Read", "--add-dir", str(Path(article).parent),
           "--permission-mode", "dontAsk", "--no-session-persistence", "--strict-mcp-config"]
    try:
        cp = subprocess.run(cmd, cwd=str(work), input=ask, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CPL.classify_transport_error(TimeoutError(), "claude 配图计划复核", timeout) from exc
    except OSError as exc:
        raise RuntimeError(f"无法启动 claude 命令行：{exc}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if failure := _claude_failure(cp):
        raise failure
    return cp.stdout, model or DEFAULT_MODEL, None


_TRANSPORTS = {"ark_coding_plan": _ask_coding_plan, "ark_agent_plan": _ask_agent_plan, "claude_cli": _ask_claude}


def _preflight(backend: str) -> None:
    """凭证检查放在读原文、发请求之前；缺什么、怎么配直接写进错误。"""
    if backend == "ark_agent_plan":
        base = os.environ.get("ARK_AGENT_PLAN_BASE_URL", "").rstrip("/")
        key = os.environ.get("ARK_AGENT_PLAN_API_KEY", "")
        if base != ARK_PLAN_URL or not key:
            need = [n for n, bad in (("ARK_AGENT_PLAN_API_KEY", not key),
                                      (f"ARK_AGENT_PLAN_BASE_URL（必须是 {ARK_PLAN_URL}）", base != ARK_PLAN_URL)) if bad]
            raise ReviewerAuthUnavailable("配图计划复核只使用已配置的 Ark Agent Plan 套餐地址与 Key；缺少或配错：" + "、".join(need)
                                          + "。" + CPL.SETUP_HINT)


def _run(backend: str, plan: dict, article: Path, model: str | None, timeout: int) -> dict:
    _preflight(backend)
    mode = "read_tool" if backend == "claude_cli" else "inline"
    budget = None if backend == "claude_cli" else _output_budget(plan)  # claude_cli 不设输出预算
    answers: list[str] = []
    gaps: list[str] = []
    metadata = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ask = prompt(plan, article, mode) if attempt == 1 else prompt(plan, article, mode, gaps=gaps)
        answer, reviewer, metadata = _TRANSPORTS[backend](ask, plan=plan, article=article, model=model,
                                                          timeout=timeout, budget=budget)
        answers.append(answer)
        data = _extract(answer)
        gaps = gaps_of(data, plan)
        if not gaps:
            for item in data["items"]:
                item["reasonable"] = _item_passes(item)
            data["_reviewer"] = reviewer
            data["_backend"] = backend
            data["_attempts"] = attempt
            if metadata is not None:
                data["_response_metadata"] = metadata
            return data
    raise PlanReviewIncomplete(backend, gaps, answers, metadata, MAX_ATTEMPTS)


def _review_ark(plan: dict, article: Path, model: str | None, timeout: int) -> dict:
    return _run("ark_agent_plan", plan, Path(article).resolve(), model, timeout)


def review(plan: dict, article: Path, model: str | None = None, timeout: int = 600, *, backend: str | None = None) -> dict:
    """独立复核配图计划。backend 缺省走 resolve_backend()；未知后端在读原文之前就报错。
    返回结果带 `_backend`（实际使用的后端）、`_reviewer`（实际型号）、`_attempts`。"""
    backend = resolve_backend(backend)
    return _run(backend, plan, Path(article).resolve(), model, timeout)


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
