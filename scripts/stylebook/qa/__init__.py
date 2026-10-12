"""出图验收：像素检查（确定性）+ 独立看图核对（模型只转述，结论由脚本判）+ 文字逐字比对 + 一致性对照网格。

判定口径（见 references/qa.md）：
- 必须看到的每一条都看到、不许看到的每一条都没出现；
- 模型先把图里的字逐字抄出来，脚本拿它和应有的字逐字比对（防止模型把糊字脑补成通顺句子）；
- 风格合同里有像素阈值的，按阈值判；
- 任何一项不过即不合格，给出具体原因，供单张返修。
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
    return [x for x in qa["must_see"] if x not in skip], [x for x in qa["must_not_see"] if x not in skip]


def content_expectations(content: dict) -> dict:
    """核对最终成品；生成底图时的留白指令不属于成品事实要求。"""
    relations = content.get("final_relations", content.get("relations"))
    general = "; ".join(str(value).strip() for value in
                        (content.get("final_subject", content.get("subject")), relations, content.get("inventory")) if value)
    if content.get("points"):
        general += "; 必须表达的要点（按顺序，保留条件、否定、数字与单位）: " + "；".join(content["points"])
    result = {"_content_expectation": general} if general else {}
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


def missing_items(review: dict | None, contract: dict, text_mode: str) -> list[str]:
    """看图结论里漏答的条目（漏答会被判不合格，所以先让看图员补答）。"""
    if not isinstance(review, dict) or not review:
        return ["(无结论)"]
    must_items, ban_items = active_items(contract, text_mode)
    def valid_answer(key: str, item: str, verdict_key: str) -> bool:
        rows = review.get(key)
        if not isinstance(rows, list):
            return False
        matches = [r for r in rows if isinstance(r, dict) and r.get("item") == item]
        return (len(matches) == 1 and type(matches[0].get(verdict_key)) is bool
                and isinstance(matches[0].get("why"), str) and bool(matches[0]["why"].strip()))

    missing = []
    for key, prefix, clauses in zip(("must_see", "must_not_see"), ("M", "B"),
                                    (must_items, ban_items)):
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
    if contract.get("_content_expectation"):
        cm = review.get("content_match")
        if not isinstance(cm, dict) or not isinstance(cm.get("ok"), bool) or not str(cm.get("why", "")).strip():
            missing.append("content_match：主体、动作与空间关系")
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


def judge(image: Path, contract: dict, review: dict | None, expected_text: list[str] | None = None,
          text_mode: str = "none", fmt: str | None = None, manifest: dict | None = None) -> Verdict:
    problems: list[str] = []
    process_errors, pending_evidence = [], []
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
        if gaps:
            problems.append("看图结论缺项或格式无效：" + "、".join(gaps))
            process_errors.append(problems[-1])
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
        for item in must_items:
            c = must.get(item)
            if c is None:
                problems.append(f"看图结论漏了一条必须特征：{item}")
            elif c.get("applicable") is False:
                na.append(item)
            elif not c.get("ok"):
                problems.append(f"没做到：{item}（{c.get('why', '')}）")
        banned = {c["item"]: c for c in (review.get("must_not_see") or [])
                  if isinstance(c, dict) and isinstance(c.get("item"), str)}
        for item in ban_items:
            c = banned.get(item)
            if c is None:
                problems.append(f"看图结论漏了一条禁止特征：{item}")
            elif c.get("present"):
                problems.append(f"出现了禁止特征：{item}（{c.get('why', '')}）")
        if contract.get("_content_expectation") or "content_match" in review:
            cm = review.get("content_match")
            if not isinstance(cm, dict) or not isinstance(cm.get("ok"), bool) or not str(cm.get("why", "")).strip():
                problems.append("缺少内容事实核对结论")
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
        transcribed = review.get("transcribed_text")
        if not isinstance(transcribed, list) or any(not isinstance(t, str) for t in transcribed):
            transcribed = []
        if text_mode in ("native", "overlay", "hybrid"):
            problems += compare_text(expected_text or [], transcribed, strict=text_mode in ("overlay", "hybrid"))
        elif text_mode == "none" and any(len(_norm(t)) >= 2 for t in transcribed):
            problems.append(f"本图不该有字，却出现了：{transcribed}")
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
        "automatic_regeneration": False,
    }
    return Verdict(str(image), not problems, problems, px, review_data, triage=triage)


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
        *[f"- [B{i}] {x}" if clause_ids else f"- {x}" for i, x in enumerate(ban_items, 1)],
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
    if text_mode in ("native", "overlay", "hybrid") and expected_text:
        lines.append("（本图应有的文字由脚本另行比对，你只需如实抄录。）")
    if any("ghost line behind the headline" in item and "no larger than the headline" in item
           for item in must_items):
        lines += ["", "## 英文 ghost 层的验收口径",
                  "这里的 no larger than the headline 指字高不超过中文主标题、视觉权重更低；"
                  "英文是一整行，可以比中文标题行更宽，也可以延伸到右侧拼贴下方。",
                  "纯白字以 8%–14% 不透明度叠在深炭底色上会呈灰色；不要仅因合成后的字呈灰色就判不是白字。",
                  "若字高超过主标题、对比度抢过主标题、或缩略图第一眼落在英文上，仍须判不合格。"]
    expectation = contract.get("_content_expectation")
    if expectation:
        lines += ["", "## 内容事实核对", f"计划要求：{expectation}",
                  "只根据图像判断主体、动作、数量、远近与左右关系是否符合计划；画风合格不能抵消情节或空间关系错误。",
                  "先列出实际可辨认的独立主体、道具和背景物件，再核对题面。原题或配方明确用Only、只能、限定等"
                  "限定物件来源或种类时，限定优先；额外的独立背景实物也须核对，不能仅因未改变主动作而豁免。"
                  "物件自身部件和表面/边框的装饰纹样不另算独立实物。没有此类明确限定时，"
                  "同一主体的正侧背面、局部细节等辅助技术视图，以及不改变情节的布景和道具，不算内容偏题；"
                  "技术分栏按上面的画风条目另行判断。只有额外元素与计划中的主体、动作、数量或空间关系冲突时才据此判内容不符。",
                  "给 content_match={ok: bool, why: str}。看不清关键关系时 ok=false，并说出无法核实的部分。"]
    points = contract.get("_content_point_expectations") or []
    if points:
        lines += ["", "## 必要信息逐条核对",
                  "每一条都根据画面独立判断，不用总主题通过代替条件、否定、数字和单位。"
                  "若某条需要精确文字而画面无法证实，按未通过说明原因。",
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
              + ("\"content_match\":{\"ok\":bool,\"why\":str}," if expectation else "")
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
    if contract.get("_content_expectation"):
        schema["required"].append("content_match")
        schema["properties"]["content_match"] = {"type": "object", "required": ["ok", "why"],
                                                  "properties": {"ok": {"type": "boolean"}, "why": {"type": "string"}}}
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


def write_report(verdicts: list[Verdict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([v.to_dict() for v in verdicts], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
