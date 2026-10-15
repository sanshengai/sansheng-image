"""计划卡与图上文字来源校验。

两件事：
1. 图上要印的文字（text.items）里，数字、引号内的引语、英文专名必须能在原文里找到；
   自己算出来的数字（合计、换算、占比）在该条文字上写 `derived`，说明怎么算的，才放行。
2. 出图前的计划卡（plan-card.md）落盘：不管是否跳过了确认，都留一份给人回看。
"""
from __future__ import annotations

import re
from pathlib import Path

CARD_NAME = "plan-card.md"

_NUM = re.compile(r"\d+(?:[.,，]\d+)*")
_QUOTE = re.compile(r"「([^」]{2,})」|“([^”]{2,})”|\"([^\"]{2,})\"|『([^』]{2,})』")
_PROPER = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]*(?:[.+\-&][A-Za-z0-9]+)*")   # 按单词查，多词专名逐词核对
# 引导语：图本身就是内容，不在图上招呼读者看图、往下翻
_GUIDE = re.compile(r"一张图(看懂|读懂|了解|搞懂)|如下图|如图所示|见下图|看下图|往下(看|滑|翻)|向下滑|点击|扫码|关注我|记得收藏|快收藏")
# 版式用语，不算专名
_PROPER_OK = {"VS", "OK", "DIY", "TOP", "NEW"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).replace(",", "").replace("，", "")


def entries(text_spec) -> list[dict]:
    """把各种写法的文字配置统一成 [{text, derived?}]。"""
    if isinstance(text_spec, str):
        return [{"text": text_spec}] if text_spec.strip() else []
    if isinstance(text_spec, list):
        return [x if isinstance(x, dict) else {"text": x} for x in text_spec if (x.get("text") if isinstance(x, dict) else x)]
    if isinstance(text_spec, dict):
        return entries(text_spec.get("items") or [])
    return []


def problems(text: str, source: str, *, derived: str | None = None) -> list[str]:
    """一条图上文字的来源问题；空列表表示都能在原文找到。"""
    src, src_low, out = _norm(source), _norm(source).lower(), []
    if not (isinstance(derived, str) and derived.strip()):
        for m in _NUM.finditer(text):
            raw = m.group(0)
            tail = text[m.end():m.end() + 1]
            if len(raw) == 1 and tail not in ("%", "％", ".", "‰"):   # 个位数多是序号、张数，不查
                continue
            if _norm(raw) not in src:
                out.append(f"数字「{raw}」在原文里找不到；若是算出来的，在这条文字上写 derived 说明算法")
    for m in _QUOTE.finditer(text):
        q = next(g for g in m.groups() if g)
        if _norm(q) not in src:
            out.append(f"引语「{q}」在原文里找不到，引号里只能放原话")
    for m in _PROPER.finditer(text):
        w = m.group(0)
        if len(w) < 2 or w.upper() in _PROPER_OK:
            continue
        if _norm(w).lower() not in src_low:
            out.append(f"英文专名「{w}」在原文里找不到")
    return out


def check_items(text_spec, source: str | None, tag: str = "") -> list[str]:
    """按条检查。引导语总是查；来源只在有原文时查（纯创作题不查）。"""
    errs = []
    for x in entries(text_spec):
        t = x.get("text")
        if not isinstance(t, str):
            continue
        m = _GUIDE.search(t)
        if m:
            errs.append(f"{tag}「{t}」图上不写引导语「{m.group(0)}」，图本身就是内容；招呼读者的话写在正文或发布文案里")
        if not source:
            continue
        derived = x.get("derived")
        if "derived" in x and not (isinstance(derived, str) and len(derived.strip()) >= 4):
            errs.append(f"{tag}「{t}」的 derived 要写清怎么算出来的")
            continue
        errs.extend(f"{tag}「{t}」{p}" for p in problems(t, source, derived=derived))
    return errs


CONFIRMED = {"pending": "待确认", "user": "用户已确认", "skipped": "已跳过确认（用户要求直接出图或由自动流程调用）",
             None: "未记录（计划里没写 confirmed）"}


def write(dest_dir: Path, body: str, *, warns: list[str] | None = None, confirmed: str | None = "pending",
          name: str = CARD_NAME) -> Path:
    """计划卡落盘。confirmed：pending（等用户确认）/ user（用户确认过）/ skipped（用户说直接出或自动流程调用）。"""
    zh = CONFIRMED
    if confirmed not in zh:
        raise ValueError(f"confirmed 只能是 {list(zh)}")
    lines = ["# 计划卡", "", f"确认状态：{zh[confirmed]}", "", body.rstrip()]
    if warns:
        lines += ["", "## 提醒", ""] + [f"- {w}" for w in warns]
    path = Path(dest_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def brief_table(prepared: dict) -> str:
    """make 流程的计划卡正文：每张图的位置、要表达什么、图上文字。"""
    sel = prepared.get("selection") or {}
    head = [f"样式：{sel.get('code', '—')}　场景：{prepared.get('scene', '—')}", f"理由：{prepared.get('reason', '—')}", "",
            "| # | 位置 | 要表达什么 | 图上文字 |", "|---|---|---|---|"]
    for i, t in enumerate(prepared.get("tasks", []), 1):
        texts = "；".join(x["text"] for x in entries((t.get("manifest") or {}).get("text")) if isinstance(x.get("text"), str)) or "—"
        cells = [str(i), t.get("position", ""), t.get("message", ""), texts]
        head.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in cells) + " |")
    if prepared.get("editorial") is not None:
        from .editorial import card
        head += ["", card(prepared["editorial"])]
    return "\n".join(head)
