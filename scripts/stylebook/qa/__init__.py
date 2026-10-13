"""出图验收：像素检查（确定性）+ 独立看图核对（模型只转述，结论由脚本判）+ 文字逐字比对 + 一致性对照网格。

判定口径（见 references/qa.md）：
- 必须看到的每一条都看到、不许看到的每一条都没出现；
- 模型先把图里的字逐字抄出来，脚本拿它和应有的字逐字比对（防止模型把糊字脑补成通顺句子）；
- 风格合同里有像素阈值的，按阈值判；
- 任何一项不过即不合格，给出具体原因，供单张返修。
- 看图是概率性的：同一张图要做多次独立采样，有分歧时加看并逐项多数决（见 aggregate_reviews），各次原始结果与分歧项都保留在报告里；
- 本次任务单（内容计划、必要信息、分镜；不含已请求文字）的显式要求/禁止优先于画风合同的「必须看到」条目；冲突须逐字引用任务单原句，脚本校验原句确实存在，且每一次判失败的采样都须提出成立的声明。「不许看到」条目不能以冲突豁免。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from PIL import Image

from .pixel import check_paper_margin, check_pixels, measure  # noqa: F401


def _norm(s: str) -> str:
    return re.sub(r"[\s「」『』\"'“”‘’：:，,。.!！?？、·\-—]", "", s or "")


def compare_text(expected: list[str], transcribed: list[str], *, strict: bool = False) -> list[str]:
    """逐字比对：每条应有文字出现一次；剩余字符均为额外文字。"""
    if strict:
        # OCR按可见行转录，overlay按文字块保存；换行不是漏字。
        # 只归一化换行，内部空格、标点、数值、大小写和重复次数仍严格比对。
        block = lambda s: s.strip().replace("\r\n", "\n").replace("\n", "")
        remaining = [block(t) for t in transcribed if t.strip()]
        positions = [i for i, t in enumerate(transcribed) if t.strip()]
        problems = []
        for e in expected:
            want = block(e)
            if not want:
                continue
            if want in remaining:
                match = remaining.index(want)
                del remaining[match]
                del positions[match]
            else:
                found = False
                # 仅合并相邻转录行，逐字且保序；不能从散落字词拼凑正确原文。
                for start in range(len(remaining)):
                    joined = ""
                    for end in range(start, len(remaining)):
                        if end > start and positions[end] != positions[end - 1] + 1:
                            break
                        joined += remaining[end]
                        if joined == want:
                            del remaining[start:end + 1]
                            del positions[start:end + 1]
                            found = True
                            break
                        if not want.startswith(joined):
                            break
                    if found:
                        break
                if not found:
                    problems.append(f"缺字或错字：应为「{e}」")
        if remaining:
            problems.append(f"多写了文字：「{' / '.join(remaining)}」")
        return problems
    norm = _norm
    got = [norm(t) for t in transcribed if norm(t)]
    blob = "".join(got)
    remaining = blob
    problems = []
    for e in expected:
        want = norm(e)
        if not want:
            continue
        at = remaining.find(want)
        if at < 0:
            problems.append(f"缺字或错字：应为「{e}」")
        else:
            remaining = remaining[:at] + remaining[at + len(want):]
    if remaining:
        problems.append(f"多写了文字：「{remaining}」")
    return problems


# 「多写文字」由 compare_text / 无字检查依据逐字转录确定性判定；合同里这一条只作参考，不单独决定失败。
# 只认合同里逐字相同的标准条款，不做关键词猜测。
TEXT_BAN_ITEMS = frozenset({"未请求的文字或水印"})
# 文字部分交给逐字比对后，图形水印/logo/签名仍须由看图判定，不能随文字一起降为参考
MARK_BAN_ITEM = "水印、logo、签名或印章等未请求的标记（不论是否含字）"
REVIEW_SCHEMA = 2  # 2 = 多次采样聚合 + 内容分项 + 冲突声明；缺此标记的结论按旧口径宽松读取（手工结论、历史缓存）
CONTENT_PART_KEYS = ("subject", "relations", "inventory")
CONTENT_PART_LABELS = {"subject": "主体", "relations": "动作与空间关系", "inventory": "物件清单"}
MIN_CONFLICT_QUOTE = 6  # 冲突引用至少这么多个有效字符，防止拿一两个字当「原句」


def _loose(s: str) -> str:
    return _norm(str(s or "")).lower()


def task_sheet_texts(contract: dict, expected_text: list[str] | None = None) -> list[str]:
    """本次任务单的原文：内容计划分项、逐条要点、分镜、已请求文字（以及调用方额外给出的 _task_sheet）。"""
    texts: list[str] = []
    parts = contract.get("_content_parts")
    if isinstance(parts, list) and parts:
        texts += [str(p.get("text", "")) for p in parts if isinstance(p, dict)]
    elif contract.get("_content_expectation"):
        texts.append(str(contract["_content_expectation"]))
    texts += [str(x) for x in contract.get("_content_point_expectations") or []]
    texts += [str(x) for x in contract.get("_panel_expectations") or []]
    extra = contract.get("_task_sheet")
    if isinstance(extra, str):
        texts.append(extra)
    elif isinstance(extra, list):
        texts += [str(x) for x in extra]
    # 已请求文字（标题等）不算「要求」，不能拿来充当冲突引用
    return [t for t in texts if t.strip()]


def check_conflict(entry: dict, sheet: list[str]) -> tuple[bool, str]:
    """冲突声明是否成立：必须有理由，且引用的任务单原句逐字出现在任务单里。

    多次采样聚合后，每一次判失败的采样都必须各自提出成立的声明（claims），缺一次就不豁免。
    """
    claim = entry.get("conflict")
    if not isinstance(claim, dict):
        return False, "无冲突声明"
    claims = claim.get("claims")
    if claims is not None:
        if not isinstance(claims, list) or not claims:
            return False, "冲突声明不完整"
        reasons = []
        for one in claims:
            ok, detail = check_conflict({"conflict": one}, sheet)
            if not ok:
                return False, detail
            reasons.append(detail)
        return True, reasons[0]
    quote, reason = claim.get("task_quote"), claim.get("reason")
    if not isinstance(quote, str) or not isinstance(reason, str) or not reason.strip():
        return False, "冲突声明缺少任务单原句或理由"
    needle = _loose(quote)
    if len(needle) < MIN_CONFLICT_QUOTE:
        return False, f"引用原句过短（少于 {MIN_CONFLICT_QUOTE} 个有效字符）"
    if not any(needle in _loose(t) for t in sheet):
        return False, "引用的原句没有出现在本次任务单里"
    return True, reason.strip()


@dataclass
class Verdict:
    image: str
    passed: bool
    problems: list[str] = field(default_factory=list)
    pixel: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    binding: dict = field(default_factory=dict)
    triage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__


def _pixel_applies(contract: dict, fmt: str | None) -> bool:
    scope = (contract.get("qa") or {}).get("pixel_scope")
    if not scope:
        return True
    if not fmt:
        return False
    from ..data import formats
    f = formats().get(fmt, {})
    return fmt in scope or f.get("family") in scope


def active_items(contract: dict, text_mode: str) -> tuple[list[str], list[str]]:
    """没字的图不查「有字时才查」的条目。"""
    qa = contract["qa"]
    qa = (qa.get("mode_overrides") or {}).get(text_mode, qa)
    skip = set(qa.get("when_text", [])) if text_mode not in ("native", "overlay", "hybrid") else set()
    bans = [x for x in qa["must_not_see"] if x not in skip]
    if TEXT_BAN_ITEMS & set(bans) and MARK_BAN_ITEM not in bans:
        bans.append(MARK_BAN_ITEM)
    return [x for x in qa["must_see"] if x not in skip], bans


def content_expectations(content: dict) -> dict:
    """核对最终成品；生成底图时的留白指令不属于成品事实要求。

    points 是「要表达的意思」，不自动成为图中文字，也不并入内容事实总述：它们只进逐条 point_match，
    精确字样只由 text.items 判定（避免同一条要点在两处重复计罚）。
    """
    relations = content.get("final_relations", content.get("relations"))
    sources = {"subject": content.get("final_subject", content.get("subject")),
               "relations": relations, "inventory": content.get("inventory")}
    parts = [{"key": key, "label": CONTENT_PART_LABELS[key], "text": str(sources[key]).strip()}
             for key in CONTENT_PART_KEYS if sources[key] and str(sources[key]).strip()]
    general = "; ".join(p["text"] for p in parts)
    result = {"_content_expectation": general, "_content_parts": parts} if general else {}
    if content.get("points"):
        result["_content_point_expectations"] = [str(point).strip() for point in content["points"]]
    panels = content.get("panels") or []
    if panels:
        result["_panel_expectations"] = [str(panel).strip() for panel in panels]
    return result


def _panel_verdicts(review: dict, contract: dict) -> tuple[list[dict], list[int]]:
    expected = contract.get("_panel_expectations") or []
    raw = review.get("panel_match")
    if not isinstance(raw, list):
        return [], list(range(1, len(expected) + 1))
    by_index: dict[int, dict] = {}
    for entry in raw:
        if not isinstance(entry, dict) or type(entry.get("panel")) is not int:
            continue
        index = entry["panel"]
        if index in by_index:  # 重复编号不能充作两格结论
            by_index[index] = {}
        else:
            by_index[index] = entry
    missing = [i for i in range(1, len(expected) + 1)
               if not isinstance(by_index.get(i, {}).get("ok"), bool)
               or not str(by_index[i].get("why", "")).strip()]
    return [by_index[i] for i in range(1, len(expected) + 1) if i in by_index], missing


def _point_verdicts(review: dict, contract: dict) -> tuple[list[dict], list[int]]:
    expected = contract.get("_content_point_expectations") or []
    raw = review.get("point_match")
    if not isinstance(raw, list):
        return [], list(range(1, len(expected) + 1))
    by_index: dict[int, dict] = {}
    for entry in raw:
        if not isinstance(entry, dict) or type(entry.get("index")) is not int:
            continue
        index = entry["index"]
        by_index[index] = {} if index in by_index else entry
    missing = [i for i in range(1, len(expected) + 1)
               if type(by_index.get(i, {}).get("ok")) is not bool
               or not str(by_index[i].get("why", "")).strip()]
    return [by_index[i] for i in range(1, len(expected) + 1) if i in by_index], missing


def _strict(review: dict) -> bool:
    """由多次采样聚合器产出（或同等格式）的结论：内容分项、失败子句必须齐全。"""
    return type(review.get("_review_schema")) is int and review["_review_schema"] >= REVIEW_SCHEMA


def _content_part_gaps(cm: dict, contract: dict, strict: bool) -> list[str]:
    parts = cm.get("parts")
    expected = [p["key"] for p in contract.get("_content_parts") or [] if isinstance(p, dict) and "key" in p]
    if parts is None:
        return ([f"content_match.parts：{CONTENT_PART_LABELS.get(k, k)}分项" for k in expected]
                if strict and expected else [])
    if not isinstance(parts, list):
        return ["content_match.parts：须为分项数组"]
    gaps, seen = [], {}
    for row in parts:
        key = row.get("key") if isinstance(row, dict) else None
        if not isinstance(key, str) or key in seen or (expected and key not in expected):
            gaps.append(f"content_match.parts：未知或重复的分项 {key!r}")
            continue
        seen[key] = row
    for key in expected:
        row = seen.get(key)
        label = CONTENT_PART_LABELS.get(key, key)
        if row is None or type(row.get("ok")) is not bool or not str(row.get("why", "")).strip():
            gaps.append(f"content_match.parts：{label}分项")
        elif strict and row["ok"] is False and not (isinstance(row.get("failed_clause"), str)
                                                    and row["failed_clause"].strip()):
            gaps.append(f"content_match.parts：{label}分项判失败须原样引用要求中未满足的具体子句（failed_clause）")
    return gaps


def missing_items(review: dict | None, contract: dict, text_mode: str) -> list[str]:
    """看图结论里漏答的条目（漏答会被判不合格，所以先让看图员补答）。"""
    if not isinstance(review, dict) or not review:
        return ["(无结论)"]
    must_items, ban_items = active_items(contract, text_mode)
    id_clauses = (must_items, list(ban_items))  # 标识核对永远按完整清单，模型答了新增条款不算未知
    if not _strict(review):  # 旧格式结论（历史缓存、手工结论）产生时还没有这一条，不要求它必答
        ban_items = [x for x in ban_items if x != MARK_BAN_ITEM]
    def valid_answer(key: str, item: str, verdict_key: str) -> bool:
        rows = review.get(key)
        if not isinstance(rows, list):
            return False
        matches = [r for r in rows if isinstance(r, dict) and r.get("item") == item]
        return (len(matches) == 1 and type(matches[0].get(verdict_key)) is bool
                and isinstance(matches[0].get("why"), str) and bool(matches[0]["why"].strip()))

    missing = []
    for key, prefix, clauses in zip(("must_see", "must_not_see"), ("M", "B"), id_clauses):
        lookup = {f"{prefix}{i}": label for i, label in enumerate(clauses, 1)}
        seen = set()
        rows = review.get(key)
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or "id" not in row:
                continue
            ident = row["id"]
            label = lookup.get(ident) if isinstance(ident, str) else None
            if label is None or ident in seen or row.get("item") != label:
                missing.append(f"{key}：未知、重复或冲突的条款标识 {ident!r}")
            if isinstance(ident, str):
                seen.add(ident)
    missing += [x for x in must_items if not valid_answer("must_see", x, "ok")]
    missing += [x for x in ban_items if not valid_answer("must_not_see", x, "present")]
    transcribed = review.get("transcribed_text")
    if not isinstance(transcribed, list) or any(not isinstance(t, str) for t in transcribed):
        missing.append("transcribed_text：逐字转录")
    for key in ("must_see", "must_not_see"):
        for row in review.get(key) if isinstance(review.get(key), list) else []:
            claim = row.get("conflict") if isinstance(row, dict) else None
            if isinstance(row, dict) and "conflict" in row and not (
                    isinstance(claim, dict) and isinstance(claim.get("task_quote"), str)
                    and isinstance(claim.get("reason"), str)):
                missing.append(f"{key}：冲突声明须含 task_quote 与 reason（{row.get('item', row.get('id'))!r}）")
    if contract.get("_content_expectation"):
        cm = review.get("content_match")
        if not isinstance(cm, dict) or not isinstance(cm.get("ok"), bool) or not str(cm.get("why", "")).strip():
            missing.append("content_match：主体、动作与空间关系")
        else:
            missing.extend(_content_part_gaps(cm, contract, _strict(review)))
    if contract.get("_content_point_expectations"):
        _, missing_points = _point_verdicts(review, contract)
        missing.extend(f"point_match：第 {i} 条必要信息" for i in missing_points)
    if contract.get("_panel_expectations"):
        _, missing_panels = _panel_verdicts(review, contract)
        missing.extend(f"panel_match：第 {i} 格内容事实" for i in missing_panels)
    if contract.get("_thumbnail_expectation"):
        tm = review.get("thumbnail_readable")
        if not isinstance(tm, dict) or not isinstance(tm.get("ok"), bool) or not str(tm.get("why", "")).strip():
            missing.append("thumbnail_readable：实际缩略尺寸主题识别")
    if contract.get("_square_crop_expectation"):
        square = review.get("square_crop")
        if not isinstance(square, dict) or not isinstance(square.get("ok"), bool) or not str(square.get("why", "")).strip():
            missing.append("square_crop：实际居中方形裁切")
    for entry in _observations(review):
        if entry.get("observation", "confirmed") not in ("confirmed", "unverified"):
            missing.append("observation：只能为 confirmed 或 unverified")
    if "suggestions" in review and (not isinstance(review["suggestions"], list)
                                   or any(not isinstance(s, str) for s in review["suggestions"])):
        missing.append("suggestions：优化建议须为文字列表")
    if "edit_goals" in review and (not isinstance(review["edit_goals"], list) or any(
            not isinstance(e, dict) or not isinstance(e.get("goal"), str) or not e["goal"].strip()
            or type(e.get("completed")) is not bool or not isinstance(e.get("why"), str)
            or not e["why"].strip() for e in review["edit_goals"])):
        missing.append("edit_goals：编辑目标须有目标原文、布尔完成状态和依据")
    return missing


def _observations(review: dict):
    """Only recognised contract/content answers carry verification state."""
    for key in ("must_see", "must_not_see", "point_match", "panel_match"):
        for entry in review.get(key, []) if isinstance(review.get(key), list) else []:
            if isinstance(entry, dict):
                yield entry
    for key in ("content_match", "thumbnail_readable", "square_crop"):
        if isinstance(review.get(key), dict):
            yield review[key]


def overlay_layout_problems(image: Path, contract: dict, manifest: dict | None) -> list[str]:
    """叠字清单的确定性下限；真实字高和视觉层级仍由看图验收。"""
    policy = (contract.get("qa") or {}).get("overlay_layout")
    if not policy:
        return []
    if not isinstance(manifest, dict) or not isinstance(manifest.get("text"), dict):
        return ["叠字版式闸门缺少清单文字配置"]
    text = manifest["text"]
    if text.get("mode") not in ("overlay", "hybrid"):
        return ["叠字版式闸门需要 overlay 或 hybrid 清单"]
    items = text.get("items")
    if not isinstance(items, list) or not items:
        return ["叠字版式闸门缺少文字项"]
    problems: list[str] = []
    with Image.open(image) as im:
        height = im.height
    roles = []
    if "headline_min_font_px_ratio" in policy:
        roles.append("title")
    if "subtitle_max_lines" in policy:
        roles.append("subtitle")
    for role in roles:
        matches = [item for item in items if isinstance(item, dict) and item.get("role") == role]
        if len(matches) != 1 or matches[0].get("render", "overlay") != "overlay":
            problems.append(f"叠字版式需要恰好一个 overlay {role} 项")
            continue
        item = matches[0]
        value = item.get("text")
        if not isinstance(value, str) or not value.strip():
            problems.append(f"叠字版式 {role} 文字为空")
            continue
        if role == "title" and "headline_min_font_px_ratio" in policy:
            size = item.get("font_px")
            minimum = policy["headline_min_font_px_ratio"]
            if type(size) is not int or size / height < minimum:
                problems.append(f"叠字主标题字号上限不足画布高度的 {minimum:.0%}")
        if role == "subtitle" and "subtitle_max_lines" in policy:
            lines = len(value.splitlines())
            if lines > policy["subtitle_max_lines"]:
                problems.append(f"叠字副标题 {lines} 行，超过 {policy['subtitle_max_lines']} 行")
    return problems


def _transcriptions(review: dict) -> list[list[str]]:
    """每次采样各自的逐字转录；任一次与应有文字对不上都算（多次采样不允许挑一次好的）。"""
    valid = lambda t: isinstance(t, list) and all(isinstance(x, str) for x in t)
    samples = review.get("_samples")
    if isinstance(samples, list):
        found = [s["transcribed_text"] for s in samples if isinstance(s, dict) and valid(s.get("transcribed_text"))]
        if found:
            return found
    main = review.get("transcribed_text")
    return [main if valid(main) else []]


def judge(image: Path, contract: dict, review: dict | None, expected_text: list[str] | None = None,
          text_mode: str = "none", fmt: str | None = None, manifest: dict | None = None) -> Verdict:
    problems: list[str] = []
    process_errors, pending_evidence = [], []
    conflicts: list[dict] = []
    conflicts_rejected: list[dict] = []
    advisory: list[str] = []
    disagreements: list = []
    problems.extend(overlay_layout_problems(image, contract, manifest))
    px = measure(image)
    if fmt == "sticker-single":
        from ..sticker import check_single
        sticker = check_single(image)
        px["sticker_single"] = sticker
        problems.extend("单张透明表情：" + problem for problem in sticker["problems"])
    elif fmt == "sticker-grid":
        from PIL import Image
        with Image.open(image) as source:
            has_alpha = "A" in source.getbands() or "transparency" in source.info
            low, high = source.convert("RGBA").getchannel("A").getextrema()
            if not has_alpha or low != 0 or high < 200:
                problems.append("表情包宫格需要完全透明的背景和清晰可见的主体")
    if px["alpha_nonopaque_pixels"] > 0:
        from ..data import formats
        if not fmt or not formats().get(fmt, {}).get("transparent"):
            problems.append(f"非透明画面含透明像素：{px['alpha_nonopaque_pixels']} 个（{px['alpha_nonopaque_ratio']:.1%}）；须重出或先用已核定背景完成合成")
    if _pixel_applies(contract, fmt):
        problems += check_pixels(px, (contract.get("qa") or {}).get("pixel") or {})
    else:
        px["skipped"] = "像素阈值不适用于该格式"
    qa_rules = contract.get("qa") or {}
    paper_margin = (qa_rules.get("paper_margin_min_by_text_mode") or {}).get(
        text_mode, qa_rules.get("paper_margin_min")
    )
    if paper_margin is not None:
        margin_check = check_paper_margin(image, min_margin=float(paper_margin))
        px["paper_margin"] = margin_check
        problems.extend("纸面留白：" + problem for problem in margin_check["problems"])
    from ..data import formats
    square_required = bool((formats().get(fmt, {}).get("safe_zone") or {}).get("center_square")) if fmt else False
    square_preview = contract.get("_square_crop_expectation")
    if (square_required or square_preview) and (not square_preview or not Path(square_preview).is_file()):
        problems.append("缺少实际导出图的居中方形裁切预览")
    min_margin = (contract.get("qa") or {}).get("center_square_margin_min")
    if min_margin is not None and square_preview and Path(square_preview).is_file():
        from ..cover_flow import check_square_master
        margin_check = check_square_master(Path(square_preview), min_margin=float(min_margin))
        px["center_square_margin"] = margin_check
        problems.extend("实际居中方形：" + p for p in margin_check["problems"])
    if not isinstance(review, dict):
        problems.append("缺少看图核对结论")
        process_errors.append(problems[-1])
    else:
        gaps = missing_items(review, contract, text_mode)
        if isinstance(review.get("_incomplete_review"), list):  # 看图员侧已判定不完整（含只有严格口径才查得出的缺项）
            gaps += [g for g in review["_incomplete_review"] if isinstance(g, str) and g not in gaps]
        if gaps:
            problems.append("看图结论缺项或格式无效：" + "、".join(gaps))
            process_errors.append(problems[-1])
        agg = review.get("_aggregation")
        if isinstance(agg, dict):
            disagreements = list(agg.get("disagreements") or [])
            for c in agg.get("contradictions") or []:
                process_errors.append("看图结论自相矛盾，待复核：" + json.dumps(c, ensure_ascii=False, sort_keys=True))
                problems.append(process_errors[-1])
        sheet = task_sheet_texts(contract, expected_text)
        for entry in _observations(review):
            if entry.get("observation") == "unverified":
                label = entry.get("item", entry.get("index", entry.get("panel", "内容/裁切核对")))
                pending_evidence.append(f"{label}：{entry.get('why', '')}")
        if pending_evidence:
            problems.append("尚未确认，先补原尺寸或局部证据：" + "；".join(pending_evidence))
        must = {c["item"]: c for c in (review.get("must_see") or [])
                if isinstance(c, dict) and isinstance(c.get("item"), str)}
        na: list[str] = []
        must_items, ban_items = active_items(contract, text_mode)
        if not _strict(review):
            ban_items = [x for x in ban_items if x != MARK_BAN_ITEM]
        for item in must_items:
            c = must.get(item)
            if c is None:
                problems.append(f"看图结论漏了一条必须特征：{item}")
            elif c.get("applicable") is False:
                na.append(item)
            elif not c.get("ok"):
                honored, detail = check_conflict(c, sheet)
                if honored:  # 任务单显式要求优先于画风合同：该条不适用，原因进报告
                    na.append(item)
                    conflicts.append({"kind": "must_see", "item": item,
                                      "task_quote": c["conflict"]["task_quote"], "reason": detail})
                else:
                    note = f"；冲突声明被拒：{detail}" if "conflict" in c else ""
                    if "conflict" in c:
                        conflicts_rejected.append({"kind": "must_see", "item": item, "detail": detail})
                    problems.append(f"没做到：{item}（{c.get('why', '')}{note}）")
        banned = {c["item"]: c for c in (review.get("must_not_see") or [])
                  if isinstance(c, dict) and isinstance(c.get("item"), str)}
        for item in ban_items:
            c = banned.get(item)
            if c is None:
                problems.append(f"看图结论漏了一条禁止特征：{item}")
            elif c.get("present"):
                if item in TEXT_BAN_ITEMS:
                    # 多写文字由逐字转录比对确定性判定；模型此项只作参考，防止它把「已请求文字」误判成未请求
                    advisory.append(f"模型提示可能有未请求文字（仅参考，以逐字比对为准）：{c.get('why', '')}")
                    continue
                # 画风合同的禁止项不能靠冲突声明豁免：真有冲突应改任务单或换画风，而不是放行
                note = ""
                if "conflict" in c:
                    note = "；冲突声明被拒：禁止特征不能以任务单冲突豁免"
                    conflicts_rejected.append({"kind": "must_not_see", "item": item,
                                               "detail": "禁止特征不能以任务单冲突豁免"})
                problems.append(f"出现了禁止特征：{item}（{c.get('why', '')}{note}）")
        if contract.get("_content_expectation") or "content_match" in review:
            cm = review.get("content_match")
            if not isinstance(cm, dict) or not isinstance(cm.get("ok"), bool) or not str(cm.get("why", "")).strip():
                problems.append("缺少内容事实核对结论")
            elif isinstance(cm.get("parts"), list):
                parts = [r for r in cm["parts"] if isinstance(r, dict) and type(r.get("ok")) is bool]
                content_text = _loose(" ".join(str(p.get("text", "")) for p in contract.get("_content_parts") or []
                                               if isinstance(p, dict)) or str(contract.get("_content_expectation", "")))
                for row in parts:
                    if row["ok"]:
                        continue
                    label = CONTENT_PART_LABELS.get(row.get("key"), str(row.get("key")))
                    clauses = [c for c in ([row.get("failed_clause")] if isinstance(row.get("failed_clause"), str) else
                               row.get("failed_clauses") or []) if isinstance(c, str) and len(_loose(c)) >= 2]
                    located = [c for c in clauses if _loose(c) in content_text]
                    if located:
                        problems.append(f"内容不符（{label}）：未满足「{'」「'.join(located)}」——{row.get('why', '')}")
                    elif _strict(review) or clauses:
                        # 判了失败却指不出要求里的具体子句：不当作确认的失败，也不放行，交人复核
                        process_errors.append(f"内容分项「{label}」判失败但未能引用要求原文中的具体子句，待复核：{row.get('why', '')}")
                        problems.append(process_errors[-1])
                    else:
                        problems.append(f"内容不符（{label}）：{row.get('why', '')}")
                if parts and cm["ok"] is False and all(r["ok"] for r in parts):
                    # 整体判失败，各分项却全部通过：自相矛盾，不作为失败理由，待复核
                    process_errors.append(f"内容核对整体结论与分项不一致（整体 ok=false，分项全部 ok=true），待复核：{cm['why']}")
                    problems.append(process_errors[-1])
            elif not cm["ok"]:
                problems.append(f"内容不符：{cm['why']}")
        if contract.get("_content_point_expectations"):
            point_reviews, missing_points = _point_verdicts(review, contract)
            if missing_points:
                problems.append("缺少逐条必要信息核对结论：" + "、".join(str(i) for i in missing_points))
            else:
                for entry in point_reviews:
                    if entry["index"] in range(1, len(contract["_content_point_expectations"]) + 1) and not entry["ok"]:
                        problems.append(f"第 {entry['index']} 条必要信息不符：{entry['why']}")
        if contract.get("_panel_expectations"):
            panel_reviews, missing_panels = _panel_verdicts(review, contract)
            if missing_panels:
                problems.append("缺少逐格内容事实核对结论：" + "、".join(str(i) for i in missing_panels))
            else:
                for entry in panel_reviews:
                    if entry["panel"] in range(1, len(contract["_panel_expectations"]) + 1) and not entry["ok"]:
                        problems.append(f"第 {entry['panel']} 格内容不符：{entry['why']}")
        if contract.get("_thumbnail_expectation"):
            tm = review.get("thumbnail_readable")
            if not isinstance(tm, dict) or not isinstance(tm.get("ok"), bool) or not str(tm.get("why", "")).strip():
                problems.append("缺少缩略图可辨认结论")
            elif not tm["ok"]:
                problems.append(f"缩略图主题不清：{tm['why']}")
        if square_required or square_preview:
            square = review.get("square_crop")
            if not isinstance(square, dict) or not isinstance(square.get("ok"), bool) or not str(square.get("why", "")).strip():
                problems.append("缺少居中方形裁切看图结论")
            elif not square["ok"]:
                problems.append(f"居中方形裁切丢失关键内容：{square['why']}")
        if na and len(na) * 3 > len(must_items):
            problems.append(f"不适用的条目过多（{len(na)}/{len(must_items)}），交人复核：{na}")
        if text_mode in ("native", "overlay", "hybrid", "none"):
            variants = _transcriptions(review)
            groups: dict[tuple, list[int]] = {}
            for index, lines in enumerate(variants, 1):
                groups.setdefault(tuple(lines), []).append(index)
            for lines, who in groups.items():
                tag = f"采样{'、'.join(map(str, who))}：" if len(groups) > 1 else ""
                lines = list(lines)
                if text_mode in ("native", "overlay", "hybrid"):
                    problems += [tag + p for p in compare_text(expected_text or [], lines,
                                                               strict=text_mode in ("overlay", "hybrid"))]
                elif any(len(_norm(t)) >= 1 for t in lines):
                    problems.append(f"{tag}本图不该有字，却出现了：{lines}")
    # passed remains strict. This routes a failed check to diagnosis/evidence,
    # never to automatic regeneration. Advice/edit completion cannot waive a gate.
    review_data = review if isinstance(review, dict) else {}
    triage = {
        "next_action": ("accept" if not problems else "complete_review" if process_errors
                        else "collect_evidence" if pending_evidence else "diagnose_before_repair"),
        "pending_evidence": pending_evidence,
        "process_errors": process_errors,
        "suggestions": review_data.get("suggestions", []),
        "edit_goals": review_data.get("edit_goals", []),
        "conflicts": conflicts,
        "conflicts_rejected": conflicts_rejected,
        "advisory": advisory,
        "sample_disagreements": disagreements,
        "automatic_regeneration": False,
    }
    return Verdict(str(image), not problems, problems, px, review_data, triage=triage)


TEXT_BAN_NOTE = "（参考项：多写/漏写文字由脚本拿你的逐字转录与「本图已请求的文字」确定性比对，此项不单独决定失败；仍须如实抄录并如实回答）"


def review_prompt(contract: dict, expected_text: list[str] | None, text_mode: str,
                  *, clause_ids: bool = False) -> str:
    must_items, ban_items = active_items(contract, text_mode)
    lines = [
        "你是一名严格的出图验收员。只转述你在图里实际看到的东西，不猜、不补、不美化。",
        "只用原题和正式画风合同验收；修图时临时提出的比例、坐标和抛光目标不自动成为成品硬要求。",
        "同一次检查收齐内容、画风、文字、系列和裁切问题。逐项区分已确认与看不清："
        "能证实的结论写 observation=confirmed；看不清或证据不足写 observation=unverified，"
        "why写明需要哪处原尺寸证据。未确认项不能当成已证实缺陷，也不能当成已通过。",
        "优化意见只放 suggestions，不因建议更漂亮而把合规项写成失败；接近上限但未超限不等于超限。",
        "必须看到的正向特征要说明实际出现的形态和位置；只说某物没有喧宾夺主、没有违规或没有缺陷，"
        "不能证明要求的特征存在。复合条款的各部分须分别核对；‘可行时’只限定它修饰的要求，"
        "不能据此豁免其他要求，也不能把条件要求变成所有场景必需或禁止的元素。",
        "计数时按独立身份判断：同一主体的正面、侧面、背面、表情或局部辅助视图不另算一个人；"
        "不同身份的额外人物仍算第二人。无法确认是否同一身份时明确说看不清，不要猜。",
        "流程节点按独立图形停靠点和箭头连接关系计数，不只数文字标签。无标签的图形若有独立围框、"
        "位于流程链中且被箭头接入，也可能构成额外节点；先描述实际连接关系，再按原题判断。",
        "题目描述人物自身的左／右侧时，以人物自身为准；正面朝向观者的人物左侧在其脸部中线的观者右边。"
        "判断发夹等局部位置要相对人物的鼻子／头部中心，不能拿整张画布的中线判断。"
        "侧面或背面难以判断时明确说无法核实，不要凭画面左右替代。",
        "",
        "## 必须看到（逐条判断 ok=true/false，并用一句话写出你看到的依据；某条在本图确实不适用时——例如图里只有一个标题，谈不上标题与小节的层级——写 applicable=false 并说明，不要滥用）",
        *[f"- [M{i}] {x}" if clause_ids else f"- {x}" for i, x in enumerate(must_items, 1)],
        "",
        "## 不许看到（逐条判断 present=true/false，并写依据）",
        *[(f"- [B{i}] {x}" if clause_ids else f"- {x}")
          + (TEXT_BAN_NOTE if x in TEXT_BAN_ITEMS else "") for i, x in enumerate(ban_items, 1)],
        "",
        "## 图里的文字",
        ("把图里所有能看到的文字**逐字抄录**到 transcribed_text，独立文字块一条，块内可见换行用\\n保留；"
         if text_mode in ("overlay", "hybrid") else
         "把图里所有能看到的文字**逐字抄录**到 transcribed_text，一行一条；") + "看不清的字用 □ 代替，"
        "不要猜测、不要改成通顺的句子。没有文字就给空数组。",
    ]
    recipe = contract.get("recipe")
    if isinstance(recipe, dict) and recipe:
        lines += ["", "## 完整画法配方（验收上下文，不以只满足题材代替画法）",
                  json.dumps(recipe, ensure_ascii=False),
                  "上述逐条条目应结合这份配方判断；不添加合同之外的限制。",
                  "如果配方明确限制物件只能来自 brief，这条明确限制优先于下面的一般布景豁免；"
                  "没有此类明确限制时，仍按一般内容事实口径判断。"]
    isolation = (contract.get("anchor") or {}).get("isolation")
    if isinstance(isolation, str) and isolation.strip():
        lines += ["", "## 参考图职责与隔离约束", isolation,
                  "只根据实际提供的图片判断；未提供参考图时，不猜测其内容或来源。"]
    requested = [t for t in (expected_text or []) if isinstance(t, str) and t.strip()]
    if text_mode in ("native", "overlay", "hybrid") and requested:
        lines += ["", "## 本图已请求的文字（逐条）",
                  *[f"- 「{t}」" for t in requested],
                  "以上每一条都是本次任务明确要求出现的文字（含品牌名、产品名、徽章/标签上的字），"
                  "不算「未请求的文字」，不能据此判任何条目失败，也不能把它们当作多写的文字。"
                  "只有不在这份列表里的字才可能是多余文字；但多写、漏写、错字由脚本拿你的逐字转录与这份列表确定性比对，"
                  "你无需自己下结论，必须做的是完整、如实地抄录（含看不清的 □）。"]
    elif text_mode == "none":
        lines += ["", "## 本图已请求的文字", "本次任务没有请求任何图中文字；图里出现的任何可读文字都要如实抄录。"]
    if any("ghost line behind the headline" in item and "no larger than the headline" in item
           for item in must_items):
        lines += ["", "## 英文 ghost 层的验收口径",
                  "这里的 no larger than the headline 指字高不超过中文主标题、视觉权重更低；"
                  "英文是一整行，可以比中文标题行更宽，也可以延伸到右侧拼贴下方。",
                  "纯白字以 8%–14% 不透明度叠在深炭底色上会呈灰色；不要仅因合成后的字呈灰色就判不是白字。",
                  "若字高超过主标题、对比度抢过主标题、或缩略图第一眼落在英文上，仍须判不合格。"]
    expectation = contract.get("_content_expectation")
    content_parts = [p for p in contract.get("_content_parts") or [] if isinstance(p, dict) and p.get("text")]
    if expectation:
        base = ["只根据图像判断主体、动作、数量、远近与左右关系是否符合计划；画风合格不能抵消情节或空间关系错误。",
                "先列出实际可辨认的独立主体、道具和背景物件，再核对题面。原题或配方明确用Only、只能、限定等"
                "限定物件来源或种类时，限定优先；额外的独立背景实物也须核对，不能仅因未改变主动作而豁免。"
                "物件自身部件和表面/边框的装饰纹样不另算独立实物。没有此类明确限定时，"
                "同一主体的正侧背面、局部细节等辅助技术视图，以及不改变情节的布景和道具，不算内容偏题；"
                "技术分栏按上面的画风条目另行判断。只有额外元素与计划中的主体、动作、数量或空间关系冲突时才据此判内容不符。"]
        if content_parts:
            lines += ["", "## 内容事实核对（按分项逐一判断）",
                      "计划要求拆成下列独立分项，每项单独给结论，不要把几个子句混进同一个布尔：",
                      *[f"- [{p['key']}] {p.get('label', p['key'])}：{p['text']}" for p in content_parts],
                      *base,
                      "给 content_match={ok:bool, why:str, parts:[{key:分项名, ok:bool, why:实际观察, failed_clause:str}]}，"
                      "parts 必须覆盖上面每个分项各一条，key 只能原样使用方括号里的分项名（"
                      + "、".join(p["key"] for p in content_parts) + "），不得自拟新名、不得把一个分项再拆成几条；"
                      "content_match.ok 必须等于所有分项 ok 的逻辑与。"
                      "某分项 ok=false 时，failed_clause 必须原样摘出该分项要求里未被满足的具体子句，why 说明画面实际如何不满足它；"
                      "要求已满足的子句不得作为失败理由（why 若写的是「已满足」，ok 就不能是 false）。"
                      "看不清关键关系时该分项 ok=false，并说出无法核实的部分。"]
        else:
            lines += ["", "## 内容事实核对", f"计划要求：{expectation}", *base,
                      "给 content_match={ok: bool, why: str}。看不清关键关系时 ok=false，并说出无法核实的部分。"]
    points = contract.get("_content_point_expectations") or []
    if points:
        lines += ["", "## 必要信息逐条核对（按含义，不按字面）",
                  "points 是要表达的意思，不自动成为图中文字。每一条独立判断：画面能否通过图形、图标、位置与连线关系，"
                  "或「本图已请求的文字」把这层意思传达出来。不要求把要点原句逐字写在图上，"
                  "也不能因为图上没有出现某句原话就判失败；精确字样只由「本图已请求的文字」判定，这里不重复计罚。"
                  "要点里的条件、否定、数字、单位属于含义的一部分：画面传达的数量、先后、因果或否定关系缺失、错误或相反才判失败；"
                  "是否必须以文字形式出现，只看该文字是否在已请求文字里。看不清或无法核实时 ok=false 并说明。",
                  *[f"第 {i} 条：{point}" for i, point in enumerate(points, 1)],
                  "给 point_match 数组，每条一项 {index:序号, ok:bool, why:实际观察}；缺失或无法核实时 ok=false。"]
    panels = contract.get("_panel_expectations") or []
    if panels:
        lines += ["", "## 分镜逐格事实核对",
                  "按从左到右、从上到下的格序逐格核对；总主题符合不代表每一格都符合。"
                  "只根据实际画面判断要求的地点、动作、物件和先后关系；无字画面不必证明精确地名或液体成分，"
                  "但可见的地域、时代与动作线索须与要求相容，明显相反的线索须拒绝。"
                  "不能拿同组的后续路线图替某一格补画缺失或相反的内容。",
                  "连续性按题面要求的身份、物件、数量和关系判断，不自行要求相邻时点的道具锁死画面坐标。"
                  "同一物件在不同格分别出现，是跨格复用；计数先看每一格内的独立物件，不能把跨格复用累加成额外物件。"
                  "原题若明确要求位置不变、特定左右/远近关系、固定数量或不移动，仍须逐格核对，不能以镜头变化豁免。"
                  "一个时点只需画要求的可见动作；原题明确的已完成动作可用其可见结果状态表达，不能额外强求互斥动作同时出现。"
                  "状态缺失、与原题相反、单格额外物件或身份不一致仍不通过；判断倾斜、湿土等状态须实际观察，不擅自要求更夸张的程度。",
                  *[f"第 {i} 格：{item}" for i, item in enumerate(panels, 1)],
                  "给 panel_match 数组，每格一条 {panel:序号, ok:bool, why:实际观察}；看不清或缺失时 ok=false。"]
    if task_sheet_texts(contract, expected_text):
        lines += ["", "## 任务单与画风合同冲突时的优先级",
                  "「任务单」指上面的内容事实核对、必要信息和分镜逐格里的原句；「本图已请求的文字」不算要求，不能引用。"
                  "任务单里显式的要求或禁止，优先于画风合同的「必须看到」条目；「不许看到」条目出现就是失败，不能声明冲突。",
                  "若某条「必须看到」条目与任务单原文明确冲突（照合同做就会违反任务单明确写出的要求或禁止），"
                  "该条仍如实回答 ok／present，并在该条里加 conflict={\"task_quote\":从任务单逐字摘出的冲突原句"
                  f"（至少 {MIN_CONFLICT_QUOTE} 个字，必须能在任务单里原样找到）, \"reason\":冲突原因" "}。"
                  "脚本会校验 task_quote 确实出现在任务单里，校验不过则声明作废，该条按原判定计。"
                  "任务单只是没提到某条、或你觉得合同条目不合理，都不是冲突，不得声明。"]
    thumb = contract.get("_thumbnail_expectation")
    if thumb:
        lines += ["", "## 实际缩略尺寸", f"另有按真实展示尺寸缩小的预览：{thumb}",
                  "请实际读取这张小图，在其原生尺寸判断主要主体与主题是否仍可辨认；不能凭大图想象小图细节。",
                  "给 thumbnail_readable={ok: bool, why: str}。只有微小色点、无法辨认主体或关键主题时 ok=false。"]
    square = contract.get("_square_crop_expectation")
    if square:
        lines += ["", "## 实际居中方形裁切", f"另有从最终导出图截取的方形预览：{square}",
                  "必须实际读取方形图，逐一核对标题、主体、题目要求的每个图形标记和承载信息的次要元素。",
                  "检查文字和物件是否完整、是否被边缘切断，以及关键元素与边缘是否有可见余量；不能凭横版大图推断。",
                  "给 square_crop={ok: bool, why: str}。任何关键内容被截断或贴边看不完整时 ok=false，并点名具体元素。"]
    edits = contract.get("_edit_goals")
    if edits:
        lines += ["", "## 本轮局部编辑目标（与原题成品验收分开）",
                  json.dumps(edits, ensure_ascii=False),
                  "给edit_goals数组，每项{goal:目标原文, completed:bool, why:实际观察}。"
                  "编辑目标未完成本身不等于原题或画风失败；真正违规仍在对应硬条目中如实报告。"]
    answer_key = "\"id\":原样标识" if clause_ids else "\"item\":原文"
    if clause_ids:
        lines += ["逐项回答用条目方括号内的固定id，例如M1/B1，不改写标识；"
                  "不用复述item长文本。每个标识恰好回答一次，必须按它对应的完整条款判断，不能省略条款内容。"]
    lines += ["", "只输出 JSON：{\"must_see\":[{" + answer_key + ",\"ok\":bool,\"applicable\":bool,\"why\":str}],"
              "\"must_not_see\":[{" + answer_key + ",\"present\":bool,\"why\":str}],\"transcribed_text\":[str],"
              + (("\"content_match\":{\"ok\":bool,\"why\":str,\"parts\":[{\"key\":str,\"ok\":bool,\"why\":str,\"failed_clause\":str}]},"
                  if content_parts else "\"content_match\":{\"ok\":bool,\"why\":str},") if expectation else "")
              + ("\"point_match\":[{\"index\":int,\"ok\":bool,\"why\":str}]," if points else "")
              + ("\"panel_match\":[{\"panel\":int,\"ok\":bool,\"why\":str}]," if panels else "")
              + ("\"thumbnail_readable\":{\"ok\":bool,\"why\":str}," if thumb else "")
              + ("\"square_crop\":{\"ok\":bool,\"why\":str}," if square else "")
              + "\"suggestions\":[str],\"edit_goals\":[{\"goal\":str,\"completed\":bool,\"why\":str}],\"notes\":str}"]
    lines.append("逐项对象可增加 observation（confirmed/unverified），上述JSON中的既有布尔字段仍须完整填写。")
    return "\n".join(lines)


def review_schema(contract: dict) -> dict:
    schema = {
        "type": "object", "required": ["must_see", "must_not_see", "transcribed_text"],
        "properties": {
            "must_see": {"type": "array", "items": {"type": "object", "required": ["item", "ok", "why"],
                                                     "properties": {"item": {"type": "string"}, "ok": {"type": "boolean"}, "applicable": {"type": "boolean"}, "why": {"type": "string"}}}},
            "must_not_see": {"type": "array", "items": {"type": "object", "required": ["item", "present", "why"],
                                                         "properties": {"item": {"type": "string"}, "present": {"type": "boolean"}, "why": {"type": "string"}}}},
            "transcribed_text": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
    }
    conflict = {"type": "object", "required": ["task_quote", "reason"],
                "properties": {"task_quote": {"type": "string"}, "reason": {"type": "string"}}}
    for key in ("must_see", "must_not_see"):
        schema["properties"][key]["items"]["properties"]["conflict"] = conflict
    if contract.get("_content_expectation"):
        schema["required"].append("content_match")
        schema["properties"]["content_match"] = {"type": "object", "required": ["ok", "why"],
                                                  "properties": {"ok": {"type": "boolean"}, "why": {"type": "string"}}}
        if contract.get("_content_parts"):
            schema["properties"]["content_match"]["required"].append("parts")
            schema["properties"]["content_match"]["properties"]["parts"] = {"type": "array", "items": {
                "type": "object", "required": ["key", "ok", "why"],
                "properties": {"key": {"type": "string", "enum": list(CONTENT_PART_KEYS)}, "ok": {"type": "boolean"},
                               "why": {"type": "string"}, "failed_clause": {"type": "string"}}}}
    if contract.get("_content_point_expectations"):
        schema["required"].append("point_match")
        schema["properties"]["point_match"] = {"type": "array", "items": {
            "type": "object", "required": ["index", "ok", "why"],
            "properties": {"index": {"type": "integer"}, "ok": {"type": "boolean"}, "why": {"type": "string"}}}}
    if contract.get("_panel_expectations"):
        schema["required"].append("panel_match")
        schema["properties"]["panel_match"] = {"type": "array", "items": {
            "type": "object", "required": ["panel", "ok", "why"],
            "properties": {"panel": {"type": "integer"}, "ok": {"type": "boolean"}, "why": {"type": "string"}}}}
    if contract.get("_thumbnail_expectation"):
        schema["required"].append("thumbnail_readable")
        schema["properties"]["thumbnail_readable"] = {"type": "object", "required": ["ok", "why"],
                                                       "properties": {"ok": {"type": "boolean"}, "why": {"type": "string"}}}
    if contract.get("_square_crop_expectation"):
        schema["required"].append("square_crop")
        schema["properties"]["square_crop"] = {"type": "object", "required": ["ok", "why"],
                                               "properties": {"ok": {"type": "boolean"}, "why": {"type": "string"}}}
    observation = {"type": "string", "enum": ["confirmed", "unverified"]}
    for key in ("must_see", "must_not_see", "point_match", "panel_match"):
        if key in schema["properties"]:
            schema["properties"][key]["items"]["properties"]["observation"] = observation
    for key in ("content_match", "thumbnail_readable", "square_crop"):
        if key in schema["properties"]:
            schema["properties"][key]["properties"]["observation"] = observation
    schema["properties"]["suggestions"] = {"type": "array", "items": {"type": "string"}}
    schema["properties"]["edit_goals"] = {"type": "array", "items": {
        "type": "object", "required": ["goal", "completed", "why"], "properties": {
            "goal": {"type": "string"}, "completed": {"type": "boolean"}, "why": {"type": "string"}}}}
    return schema


# ---- 多次独立采样的聚合 ---------------------------------------------------------------
# 规则 majority_after_escalation：每个判定项按各次采样多数决（平票判失败）；reviewer.review 在默认 3 次
# 出现任何分歧时会再独立看 2 次，于是有分歧的条目按 5 次多数定。
# 为什么不用 any_fail：真实看图里单次采样有约两三成概率在某一条上看错（实测：明摆着的代码文件图标
# 被一次采样说成没有），any_fail 让合格图三次里约一半被卡；纯 3 次多数又会让一次漏看真缺陷的概率偏高。
# 分歧时加看到 5 次，两类错误都压到约 5%。逐字转录仍是每次采样各自比对，任何一次对不上都算。
# 冲突豁免另有更严的规则：每一次判失败的采样都须提出成立的声明（见 _merge_rows）。
AGGREGATION_RULE = "majority_after_escalation"


def _verdict_must(e: dict) -> str:
    return "na" if e.get("applicable") is False else ("pass" if e.get("ok") is True else "fail")


def _verdict_ban(e: dict) -> str:
    return "fail" if e.get("present") is True else "pass"


def _verdict_ok(e: dict) -> str:
    return "pass" if e.get("ok") is True else "fail"


def _merge_rows(rows: list[dict], verdict, *, field: str, fail_value: bool) -> tuple[dict, list[str]]:
    """rows 是同一判定项在各次采样里的回答。返回合并后的回答与各次结论。"""
    n = len(rows)
    verdicts = [verdict(r) for r in rows]
    fails = verdicts.count("fail")
    if fails and fails * 2 >= n:  # 多数决，平票判失败
        agg = "fail"
    else:  # 没过半的失败之外，有判通过的就是通过，否则全是不适用
        agg = "pass" if "pass" in verdicts else "na"
    matching = [r for r, v in zip(rows, verdicts) if v == agg]
    merged = {k: v for k, v in matching[0].items() if k not in ("conflict", "failed_clause", "failed_clauses")}
    merged[field] = (agg == "fail") if fail_value else (agg != "fail")
    if field == "ok" and "applicable" in merged:
        merged["applicable"] = agg != "na"
    # 理由：失败时保留所有判失败那几次的理由；通过时取一次即可，其余在 _samples 里
    if n > 1 and agg == "fail":
        failing = [(i + 1, r) for i, (r, v) in enumerate(zip(rows, verdicts)) if v == "fail"]
        merged["why"] = "；".join(f"[采样{i}] {r.get('why', '')}" for i, r in failing)
        if len(failing) < n:
            merged["why"] += f"（{n} 次采样中 {len(failing)} 次判失败：采样{'、'.join(str(i) for i, _ in failing)}；其余采样未判失败，已留在 _samples）"
    # 证据状态：失败要「所有判失败的采样都说看不清」才算未确认；通过要「所有判通过的采样都已确认」
    states = [r.get("observation", "confirmed") for r in matching]
    if any("observation" in r for r in rows):
        unverified = all(x == "unverified" for x in states) if agg == "fail" else any(x == "unverified" for x in states)
        merged["observation"] = "unverified" if unverified else "confirmed"
    # 冲突豁免只在「每一次判失败的采样都提出了声明」时保留，且每条声明都交 judge 逐条核对原文；
    # 少数采样的声明不能盖掉另一次采样判出的真实缺陷
    failing_rows = [(i + 1, r) for i, (r, v) in enumerate(zip(rows, verdicts)) if v == "fail"]
    if agg == "fail" and failing_rows and all(isinstance(r.get("conflict"), dict) for _, r in failing_rows):
        first = failing_rows[0][1]["conflict"]
        merged["conflict"] = {**first, "claims": [r["conflict"] for _, r in failing_rows],
                              "claimed_by_samples": [i for i, _ in failing_rows]}
    clauses = [r["failed_clause"] for r, v in zip(rows, verdicts)
               if v == "fail" and isinstance(r.get("failed_clause"), str) and r["failed_clause"].strip()]
    if clauses:
        merged["failed_clause"] = clauses[0]
        merged["failed_clauses"] = clauses
    merged["sample_verdicts"] = verdicts
    return merged, verdicts


def aggregate_reviews(samples: list[dict]) -> dict:
    """把同一张图的多次完整看图结论合成一份（形状与单次结论一致，judge 可直接判）。"""
    if not samples:
        raise ValueError("没有可聚合的看图采样")
    n = len(samples)
    base = samples[0]
    out: dict = {k: v for k, v in base.items() if not k.startswith("_") or k in ("_reviewer", "_response_metadata", "_criteria_id_map")}
    disagreements: list[dict] = []
    contradictions: list[dict] = []

    def keyed(key: str, idx: str) -> list[dict]:
        return [{r[idx]: r for r in s.get(key, []) if isinstance(r, dict) and idx in r} for s in samples]

    def merge_list(key: str, idx: str, verdict, field: str, fail_value: bool) -> None:
        tables = keyed(key, idx)
        merged_rows = []
        for r in base.get(key, []):
            if not isinstance(r, dict) or idx not in r:
                continue
            rows = [t[r[idx]] for t in tables if r[idx] in t]
            merged, verdicts = _merge_rows(rows, verdict, field=field, fail_value=fail_value)
            if len(set(verdicts)) > 1:
                disagreements.append({"field": key, idx: r[idx], "verdicts": verdicts})
            merged_rows.append(merged)
        out[key] = merged_rows

    merge_list("must_see", "item", _verdict_must, "ok", False)
    merge_list("must_not_see", "item", _verdict_ban, "present", True)
    for key, idx in (("point_match", "index"), ("panel_match", "panel")):
        if isinstance(base.get(key), list):
            merge_list(key, idx, _verdict_ok, "ok", False)
    for key in ("thumbnail_readable", "square_crop"):
        if isinstance(base.get(key), dict):
            merged, verdicts = _merge_rows([s[key] for s in samples], _verdict_ok, field="ok", fail_value=False)
            merged.pop("sample_verdicts", None)
            if len(set(verdicts)) > 1:
                disagreements.append({"field": key, "verdicts": verdicts})
            out[key] = merged
    if isinstance(base.get("content_match"), dict):
        cms = [s["content_match"] for s in samples]
        merged, verdicts = _merge_rows(cms, _verdict_ok, field="ok", fail_value=False)
        merged.pop("sample_verdicts", None)
        if all(isinstance(c.get("parts"), list) for c in cms):
            by_key = [{p["key"]: p for p in c["parts"] if isinstance(p, dict) and "key" in p} for c in cms]
            merged_parts = []
            for p in base_parts(cms[0]):
                rows = [t[p["key"]] for t in by_key if p["key"] in t]
                mp, pv = _merge_rows(rows, _verdict_ok, field="ok", fail_value=False)
                if len(set(pv)) > 1:
                    disagreements.append({"field": "content_match", "key": p["key"], "verdicts": pv})
                merged_parts.append(mp)
            merged["parts"] = merged_parts
            for i, c in enumerate(cms, 1):  # 整体结论与自己的分项不一致：记下，judge 会标待复核
                own = [p.get("ok") for p in c["parts"] if isinstance(p, dict)]
                if own and c.get("ok") is not all(x is True for x in own):
                    contradictions.append({"field": "content_match", "sample": i, "overall_ok": c.get("ok"),
                                           "parts_ok": own})
            merged["ok"] = all(p["ok"] is True for p in merged_parts)
        elif len(set(verdicts)) > 1:
            disagreements.append({"field": "content_match", "verdicts": verdicts})
        out["content_match"] = merged
    out["suggestions"] = list(dict.fromkeys(x for s in samples for x in s.get("suggestions", []) if isinstance(x, str)))
    goals: dict[str, list[dict]] = {}
    for s in samples:
        for g in s.get("edit_goals", []):
            if isinstance(g, dict) and isinstance(g.get("goal"), str):
                goals.setdefault(g["goal"], []).append(g)
    out["edit_goals"] = [{"goal": goal, "completed": all(g.get("completed") is True for g in rows),
                          "why": "；".join(f"[采样{i}] {g.get('why', '')}" for i, g in enumerate(rows, 1))}
                         for goal, rows in goals.items()]
    notes = [f"[采样{i}] {s['notes']}" for i, s in enumerate(samples, 1) if isinstance(s.get("notes"), str) and s["notes"].strip()]
    if notes:
        out["notes"] = "；".join(notes)
    out["_samples"] = [dict(s) for s in samples]
    out["_aggregation"] = {"rule": AGGREGATION_RULE, "samples": n, "disagreements": disagreements,
                           "contradictions": contradictions}
    out["_review_schema"] = REVIEW_SCHEMA
    return out


def base_parts(cm: dict) -> list[dict]:
    return [p for p in cm.get("parts", []) if isinstance(p, dict) and "key" in p]


def write_report(verdicts: list[Verdict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([v.to_dict() for v in verdicts], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
