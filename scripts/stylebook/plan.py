"""配图计划：宿主 Agent 读文章写出计划，脚本做确定性检查，再生成确认表与编译清单。

判断（哪里配图、信息是什么形状、画什么）交给宿主 Agent，按 references/planning.md 做；
本模块只管能机器检查的规则（改写自 baoyu-skills@1567581 baoyu-article-illustrator 的位置判定与大纲要求）：
- 样式一篇不变，来源按「本次指定 > 项目锁定 > 作者档案 > 出厂默认」，并写明理由；个别图手动指定只影响那一张；
- 信息形状决定可选的表达形式与结构（讲故事 → 场景；观点 → 单幅隐喻；步骤 → 流程类……）；
- 密度由要点数决定，并按格式封顶；
- 连续三张同结构时提醒复核，内容确实连续时允许保留；
- 每张都要有「画什么」和依据句；没有风格合同的样式不能出图。
"""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from . import contract as CT
from . import data as D
from .comic import validate_content as validate_comic_content
from .textspec import MODES, native_items, validate_hybrid

SHAPES: dict[str, dict] = {
    "story": {"zh": "讲故事", "def": "讲一件具体发生的事或一个人的经历（含真实人物的传记段）", "forms": ["scene"]},
    "opinion": {"zh": "观点", "def": "作者的判断或论点本身，没有可拆的步骤、组成或数据", "forms": ["metaphor"]},
    "steps": {"zh": "步骤", "def": "有先后顺序的步骤或一条单向因果链；原文明说首尾相接才算循环", "forms": ["structure"], "structures": ["flow", "timeline", "journey", "cycle"]},
    "decision": {"zh": "条件判断", "def": "先检查一个条件，再沿不同分支采取不同动作；分支条件、去向与不适用情形都要写清", "forms": ["structure"], "structures": ["decision-tree"]},
    "options": {"zh": "选项", "def": "摆在读者面前、可以任选其一的两三种方案", "forms": ["structure"], "structures": ["compare", "table", "dodont", "balance"]},
    "hierarchy": {"zh": "层级", "def": "有上下、内外或主次之分的层级", "forms": ["structure"], "structures": ["pyramid", "layers", "tree", "nested", "iceberg"]},
    "parts": {"zh": "组成", "def": "一个整体拆成并列的几块，或几类人各自的做法、分工（谁负责什么）", "forms": ["structure"], "structures": ["grid", "features", "list", "mindmap"]},
    "causes": {"zh": "多因", "def": "多个原因共同导致一个结果", "forms": ["structure"], "structures": ["fishbone"]},
    "overlap": {"zh": "交集", "def": "几个集合的交集", "forms": ["structure"], "structures": ["venn"]},
    "two-axis": {"zh": "两维排序", "def": "按两个维度排序或分区", "forms": ["structure"], "structures": ["quadrant"]},
    "filter": {"zh": "筛选", "def": "从多到少、层层筛掉（漏斗），不是按身份对号入座", "forms": ["structure"], "structures": ["funnel"]},
    "gap": {"zh": "现状到目标 / 前后变化", "def": "从现状到目标，或已经发生的前后变化（从 A 换成 B、接入前 vs 接入后）", "forms": ["structure"], "structures": ["bridge", "compare"]},
    "combination": {"zh": "组合", "def": "几样东西组合成一个结果（A + B = C）", "forms": ["structure"], "structures": ["equation"]},
    "data": {"zh": "数据对比", "def": "同一口径的数字（价格、倍数、百分比）的比较或随时间的变化；定性差异不算", "forms": ["structure"], "structures": ["compare", "table", "grid", "timeline"]},  # 随时间变化的数据用时间线
}
# 判别要点：(稳定 key, 原文)。复核提示词按 key 生成编号引用（rule_no），新增/删除/重排规则时引用随之更新或直接报错；
# 每条规则必须被 plan_review 的至少一个判定项引用（tests/test_plan_review_backends.py 守着）。
PLANNING_RULE_ITEMS = [
    ("value", "值得配图的位置：核心论点、抽象概念、数据对比、流程与操作过程；纯装饰、泛泛的氛围图不配。"),
    ("core_points", "先列出全文 2–5 个核心论点，以及结论或行动建议、最具体的操作过程；每项都要决定配图、已有视觉足够或文字足够，并写出针对该项的理由。不能只画一个例子就把其余核心机制视为已覆盖。"),
    ("per_section", "一节通常只放一张，除非它有两个互不相干的要点。"),
    ("placement", "图放在一个意思讲完的那段之后：不紧跟总起句，也不紧跟悬念式设问；位置引用原文那一句，写成「……」之后。"),
    ("existing_visual", "原文已有表格、清单、截图或作者供图时，逐项判断它是否真正回答读者的问题。已有视觉只覆盖字段、界面或局部步骤，不自动覆盖跨步骤关系、易错分叉、判断边界；新图若补出这些信息就保留，完全同义重复才删除。"),
    ("data_basis", "段落里有现成的同口径数据时，优先画数据，而不是氛围。比较前核对指标定义、分母、对象和时间范围；完播率与在看率等不同指标不是同口径，即使作者计算了倍数，也不能据此要求同一轴的倍数图或推断效果胜负。可保留明确标示口径差异的原表，或画指标区别；同一群体的独立指标仍可并列展示，但不能画成转化漏斗。"),
    ("faithful", "要点与画面细节都忠于原文：不漏环节、不编数字；原文说「可选」「这次没做」的照写；场景的动作和情绪与原文一致。"),
    ("causal_chain", "因果链原文没说首尾相接就不画成循环；只说“交易闭环”不足以证明末端会反馈到起点，须从原文另找循环依据。环节多于密度上限时合并相邻环节，不删环节。"),
    ("parallel", "并列类别、分工用组成与网格，不把它们画成上下等级；作者推断不画成官方承认的事实。"),
    ("metaphor_face", "不把文中比喻按字面画出来；不画真实人物（名人、受访者、作者家人）的相似脸，用背影、剪影或标志性实物。"),
    ("same_structure", "同一类对比在一篇里用同一种结构。"),
    ("title_text", "标题保留原文核心信息；改写不得改变对象、结论或行动。图中文字与要点保留读者理解机制或完成步骤所必需的条件，不用空泛标签代替具体内容。出图前逐字校对上图文案；画面描述只讲图形与关系，所有要印在图上的文字只列在 text.items。"),
    ("claims_evidence", "数字、费用、能力边界和效果声明须能在原文找到依据；不得把有条件的效果写成绝对保证。读完图应能理解这一段的核心判断，教程图应能知道下一步做什么。"),
    ("final_check", "提交前把核心论点、关键机制、文末结论／行动建议和最具体操作逐项对照：已配图、现有视觉已解决同一阅读问题，或文字足够且图没有增益。遗漏有独立视觉增益的核心机制时补图；不得因张数已满或只见一张示例图就忽略它。"),
]
PLANNING_RULES = [text for _key, text in PLANNING_RULE_ITEMS]  # 与 references/planning.md 逐条同序收录


def rule_no(*keys: str) -> str:
    """按稳定 key 取判别要点当前编号（「1、3、4」）；key 不存在就抛 KeyError，不静默错位。"""
    order = [key for key, _text in PLANNING_RULE_ITEMS]
    missing = [k for k in keys if k not in order]
    if missing:
        raise KeyError(f"判别要点不存在：{missing}")
    return "、".join(str(n) for n in sorted(order.index(k) + 1 for k in keys))


def rule_text(key: str) -> str:
    for k, text in PLANNING_RULE_ITEMS:
        if k == key:
            return text
    raise KeyError(f"判别要点不存在：{key}")

FORM_ZH = {"scene": "人物场景", "metaphor": "单幅隐喻", "structure": "结构图"}
DENSITY_ORDER = ["sparse", "balanced", "dense"]
DENSITY_ZH = {"sparse": "稀疏", "balanced": "均衡", "dense": "密集"}
SOURCE_ZH = {"explicit": "本次指定", "project": "项目锁定", "preference": "长期偏好", "observed": "观察倾向",
             "author": "作者档案", "factory": "出厂默认"}


def density_for(points: int) -> str:
    return "sparse" if points <= 2 else "balanced" if points <= 4 else "dense"


def resolve_style(scene_id: str, explicit: str | None = None, project: Path | None = None, *, include_inferred: bool = True) -> dict:
    """样式来源优先级：本次指定 > 项目锁定 > 长期偏好 > 作者档案 > 出厂默认。"""
    from . import profile as P
    warning = None
    try:
        palette = P.lookup("palette", scene=scene_id, include_inferred=include_inferred)
    except P.ProfileError as exc:
        warning, palette = f"偏好文件不可用，已退回作者档案/出厂默认：{exc}", None
    pal_value = palette["value"] if palette else None
    if explicit:
        return {"code": explicit, "source": "explicit", **({"palette": pal_value} if pal_value else {}),
                **({"warning": warning} if warning else {})}
    if project and Path(project).is_file():
        lock = json.loads(Path(project).read_text(encoding="utf-8"))
        if lock.get("style"):
            return {"code": lock["style"], "source": "project", "palette": lock.get("palette") or pal_value,
                    **({"warning": warning} if warning else {})}
    try:
        preferred = P.lookup("style", scene=scene_id, include_inferred=include_inferred) if warning is None else None
    except P.ProfileError as exc:
        warning, preferred = f"偏好文件不可用，已退回作者档案/出厂默认：{exc}", None
    if preferred:
        return {"code": preferred["value"], "source": "preference" if preferred["source"] == "explicit_memory" else "observed",
                "evidence": preferred["evidence"], **({"palette": pal_value} if pal_value else {})}
    code, _alts, src = D.scene_choice(scene_id)
    return {"code": code, "source": "author" if src == "作者档案" else "factory",
            **({"palette": pal_value} if pal_value else {}), **({"warning": warning} if warning else {})}


def _has_contract(code: str) -> bool:
    try:
        CT.load(code)
        return True
    except (KeyError, CT.ContractError):
        return False


def check(plan: dict, *, base_path: Path | None = None) -> tuple[list[str], list[str]]:
    """返回（错误，提醒）。错误要改完才能出图；提醒写进确认表。"""
    errs: list[str] = []
    warns: list[str] = []
    version = plan.get("version", 1)
    if version not in (1, 2, 3):
        errs.append(f"不支持的配图计划版本：{version!r}")
    source_text = None
    if version in (2, 3):
        source = plan.get("source") or {}
        if not isinstance(source, dict) or not source.get("path") or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", ""))):
            errs.append("v2 计划的 source 须包含原文 path 和 sha256")
        else:
            path = Path(source["path"])
            if not path.is_absolute():
                path = (base_path or Path.cwd()) / path
            if not path.is_file():
                errs.append(f"原文不存在：{path}")
            else:
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                    errs.append("原文 sha256 与计划不符，需重新规划或核对原文版本")
                else:
                    try:
                        source_text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        errs.append("原文不是 UTF-8 文本")
    fmts = D.formats()
    scene = D.scenes().get(plan.get("scene", ""))
    if not scene:
        errs.append(f"未知场景：{plan.get('scene')!r}")
    fmt_id = plan.get("format") or (scene or {}).get("formats", [None])[0]
    if fmt_id not in fmts:
        errs.append(f"未知格式：{fmt_id!r}")
    style = plan.get("style") or {}
    code = style.get("code", "")
    if style.get("source") not in SOURCE_ZH:
        errs.append("style.source 必须是 explicit / project / preference / observed / author / factory 之一")
    if not style.get("why") or len(style["why"]) < 6:
        errs.append("样式要写一句理由（style.why）")
    if code and not _has_contract(code.split("@")[0]):
        errs.append(f"样式 {code} 还没有风格合同，不能出图；换一个有合同的样式")
    elif code and "@" in code:
        current = CT.load(code)["revision"]
        if not re.fullmatch(rf"{re.escape(code.split('@')[0])}@r{current}", code):
            errs.append(f"样式修订 {code} 与当前合同 r{current} 不一致")
    raw_items = plan.get("items")
    if not isinstance(raw_items, list):
        errs.append("items 须明确写为图片列表；无需新增图时写 []")
        items = []
    elif any(not isinstance(it, dict) for it in raw_items):
        errs.append("items 每项须为对象")
        items = [it for it in raw_items if isinstance(it, dict)]
    else:
        items = raw_items
    if not items and not (version == 3 and plan.get("scene") == "wxillus"):
        errs.append("计划里一张图都没有")
    series = plan.get("series")
    if series is not None and series is not False:
        if not isinstance(series, dict) or not str(series.get("id", "")).strip():
            errs.append("series 必须包含非空 id，或设为 false 关闭系列参考")
        elif series.get("master_id") and series["master_id"] not in [it.get("id") for it in items]:
            errs.append(f"series.master_id {series['master_id']!r} 不在 items 中")
    ids, run_struct, run_len = set(), None, 0
    for i, it in enumerate(items, 1):
        tag = f"第 {i} 张（{it.get('id', '?')}）"
        if version in (2, 3):
            quote = it.get("source_quote")
            if not isinstance(quote, str) or len(quote.strip()) < 4:
                errs.append(f"{tag} 缺原文短引用（source_quote）")
            elif source_text is not None and re.sub(r"\s+", "", quote) not in re.sub(r"\s+", "", source_text):
                errs.append(f"{tag} source_quote 在原文中找不到")
        if it.get("id") in ids:
            errs.append(f"{tag} id 重复")
        ids.add(it.get("id"))
        for k, zh in (("position", "位置"), ("what", "画什么"), ("subject", "英文主体描述"), ("why", "依据句")):
            if not str(it.get(k, "")).strip():
                errs.append(f"{tag} 缺{zh}（{k}）")
        if "inventory" in it and (not isinstance(it["inventory"], str) or not it["inventory"].strip()):
            errs.append(f"{tag} inventory 必须是非空文字")
        pos = str(it.get("position", ""))
        item_fmt_id = it.get("format") or fmt_id
        errs.extend(f"{tag} {problem}" for problem in validate_comic_content(item_fmt_id, it))
        if item_fmt_id in {"article-illustration", "article-summary", "article-summary-tall"} and pos and not re.search(r"「[^」]{4,}」(之后|之前)", pos):
            errs.append(f"{tag} 位置要引用原文的一句话，写成「……」之后（不写「段首」「一节末」这类含糊位置）")
        if it.get("why") and len(it["why"]) < 8:
            errs.append(f"{tag} 依据句太短，要写清为什么在这里配图、为什么是这个形式")
        shape = it.get("shape")
        form = it.get("form")
        if shape not in SHAPES:
            errs.append(f"{tag} 信息形状 {shape!r} 不在 {list(SHAPES)}")
            continue
        if form not in SHAPES[shape]["forms"] and not it.get("manual"):
            errs.append(f"{tag} {SHAPES[shape]['zh']}类内容应表达为{ '、'.join(FORM_ZH[f] for f in SHAPES[shape]['forms']) }，不是{FORM_ZH.get(form, form)}")
        if form == "structure":
            st = it.get("structure")
            allowed = SHAPES[shape].get("structures", [])
            if st not in D.structures():
                errs.append(f"{tag} 未知结构 {st!r}")
            elif allowed and st not in allowed and not it.get("manual"):
                errs.append(f"{tag} {SHAPES[shape]['zh']}类内容可用的结构是 {allowed}，不是 {st}")
            pts = it.get("points") or []
            if not isinstance(pts, list) or not pts or any(not isinstance(p, str) or not p.strip() for p in pts):
                errs.append(f"{tag} 结构图要列出要点（points），密度由要点数决定")
            else:
                if len(pts) > 8:
                    errs.append(f"{tag} {len(pts)} 个要点超过单图 8 点上限，拆成多张或合并相邻环节")
                want = density_for(len(pts))
                if it.get("density") and it["density"] != want:
                    errs.append(f"{tag} {len(pts)} 个要点对应密度 {want}，不是 {it['density']}")
                item_fmt = fmts.get(it.get("format") or fmt_id) or {}
                cap = item_fmt.get("density_max", "dense")
                if DENSITY_ORDER.index(want) > DENSITY_ORDER.index(cap):
                    errs.append(f"{tag} {len(pts)} 个要点超出「{item_fmt.get('zh', fmt_id)}」的上限（{DENSITY_ZH[cap]}），拆成两张或精简要点")
            run_len = run_len + 1 if st == run_struct else 1
            run_struct = st
            if run_len == 3:
                warns.append(f"{tag} 连续第 3 张用「{st}」结构；请核对原文关系，确实都是此结构可保留")
        else:
            run_struct, run_len = None, 0
            if it.get("structure"):
                errs.append(f"{tag} {FORM_ZH.get(form, form)}不写结构")
            if "points" in it:
                pts = it["points"]
                if not isinstance(pts, list) or not pts or any(not isinstance(p, str) or not p.strip() for p in pts):
                    errs.append(f"{tag} 场景或隐喻图的必要事实（points）必须是非空文字列表")
        if it.get("style") and not it.get("manual"):
            errs.append(f"{tag} 一篇只用一个样式；只有用户手动指定的那张（manual: true）才能单独换样式")
        if it.get("style") and it.get("manual"):
            local_code = it["style"]
            if not _has_contract(local_code.split("@")[0]):
                errs.append(f"{tag} 样式 {local_code} 没有可用合同")
            elif "@" in local_code:
                current = CT.load(local_code)["revision"]
                if not re.fullmatch(rf"{re.escape(local_code.split('@')[0])}@r{current}", local_code):
                    errs.append(f"{tag} 样式修订 {local_code} 与当前合同 r{current} 不一致")
        t = it.get("text") or {}
        mode = t.get("mode")
        if mode is not None and mode not in MODES:
            errs.append(f"{tag} 未知文字档位 {mode!r}")
        if mode == "hybrid":
            errs.extend(f"{tag} {problem}" for problem in validate_hybrid(t))
        if t.get("mode") == "overlay" and (item_fmt_id not in ("ppt", "courseware") or
                                          any("box" in x for x in t.get("items", []))):
            from .overlay import validate as validate_overlay
            errs.extend(f"{tag} {problem}" for problem in validate_overlay(t))
        item_code = (it.get("style") if it.get("manual") and it.get("style") else code) or ""
        if item_code and _has_contract(item_code.split("@")[0]):
            from .compile import CompileError, _check_text_roles, text_mode_problem  # 与编译同一判据：计划里放行的组合，编译不会再拒
            c = CT.load(item_code.split("@")[0])
            problem = text_mode_problem(c, t, fmts.get(item_fmt_id))
            if problem:
                errs.append(f"{tag} {problem}")
            else:
                try:
                    _check_text_roles(c, {"text": t}, fmts.get(item_fmt_id))
                except CompileError as e:
                    errs.append(f"{tag} {e}")
        if mode in ("native", "hybrid") and code and _has_contract(code.split("@")[0]):
            limit = (fmts.get(it.get("format") or fmt_id) or {}).get("text", {}).get("title_max_chars")
            for x in native_items(t):
                if x.get("role") == "title" and limit and len(x["text"]) > limit:
                    errs.append(f"{tag} 标题「{x['text']}」{len(x['text'])} 字，超过该格式上限 {limit} 字")
    if version == 3 and plan.get("scene") == "wxillus":
        coverage = plan.get("coverage")
        if not isinstance(coverage, list) or not coverage:
            errs.append("v3 文章计划须逐项列出 coverage：核心论点、机制、结论或操作的配图决定")
        else:
            covered_ids: set[str] = set()
            valid_ids = {str(it.get("id")) for it in items if isinstance(it, dict)}
            for index, row in enumerate(coverage, 1):
                tag = f"coverage[{index}]"
                if not isinstance(row, dict):
                    errs.append(f"{tag} 必须是对象")
                    continue
                if not str(row.get("claim", "")).strip() or len(str(row.get("why", "")).strip()) < 12:
                    errs.append(f"{tag} 须有核心判断 claim 和针对性的理由 why")
                quote = row.get("source_quote")
                if not isinstance(quote, str) or len(quote.strip()) < 4:
                    errs.append(f"{tag} 缺原文短引用 source_quote")
                elif source_text is not None and re.sub(r"\s+", "", quote) not in re.sub(r"\s+", "", source_text):
                    errs.append(f"{tag} source_quote 在原文中找不到")
                decision = row.get("decision")
                ids = row.get("image_ids", [])
                if decision not in ("image", "existing_visual", "text_sufficient"):
                    errs.append(f"{tag} decision 须是 image / existing_visual / text_sufficient")
                if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
                    errs.append(f"{tag} image_ids 须是图片 id 列表")
                    continue
                if decision == "image":
                    if not ids:
                        errs.append(f"{tag} 选择 image 时须引用计划里的图片 id")
                    for value in ids:
                        if value not in valid_ids:
                            errs.append(f"{tag} 引用了不存在的图片 id：{value}")
                        covered_ids.add(value)
                elif ids:
                    errs.append(f"{tag} 未选择 image 时 image_ids 须为空")
            for value in valid_ids - covered_ids:
                errs.append(f"图片 {value} 未映射到 coverage 的核心判断")
    return errs, warns


def table(plan: dict) -> str:
    """出图前给用户确认一次的计划表。"""
    style = plan["style"]
    src, why = SOURCE_ZH.get(style.get("source"), ""), style.get("why", "")
    head = [f"样式：{style['code']}（{why if why.startswith(src) else f'{src}：{why}'}）",
            f"场景：{D.scenes()[plan['scene']]['zh']}　格式：{plan.get('format') or D.scenes()[plan['scene']]['formats'][0]}", "",
            "| # | 位置 | 画什么 | 必要信息 | 形式 | 密度 | 依据 |", "|---|---|---|---|---|---|---|"]
    for i, it in enumerate(plan["items"], 1):
        form = FORM_ZH.get(it["form"], it["form"])
        if it["form"] == "structure":
            form += f"：{D.structures()[it['structure']]['zh']}"
        dens = DENSITY_ZH.get(density_for(len(it.get("points") or [])), "—") if it["form"] == "structure" else "—"
        cells = [str(i), it["position"], it["what"], "；".join(it.get("points") or []) or "—",
                 form + ("（手动）" if it.get("manual") else ""), dens, it["why"]]
        head.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    if not plan["items"]:
        head.extend(["", "本篇现有视觉或文字已覆盖核心内容，无需新增配图；逐项理由见 coverage。"])
    return "\n".join(head)


def manifests(plan: dict, model: str = "gpt-image-2") -> list[dict]:
    """计划 → 每张图的编译清单。"""
    style = plan["style"]
    out = []
    for it in plan["items"]:
        m: dict = {"style": it.get("style") if it.get("manual") and it.get("style") else style["code"], "model": model,
                   "format": it.get("format") or plan.get("format") or D.scenes()[plan["scene"]]["formats"][0],
                   "content": {"subject": it["subject"]}, "_id": it["id"]}
        if plan.get("version") in (2, 3):
            m["source"] = {"sha256": plan["source"]["sha256"], "quote": it["source_quote"]}
        for k in ("inventory", "relations", "final_subject", "final_relations", "camera", "background", "mood", "purpose", "characters", "panels"):
            if it.get(k):
                m["content"][k] = it[k]
        for k in ("references", "aspect"):
            if it.get(k):
                m[k] = it[k]
        if it.get("points"):
            m["content"]["points"] = [point.strip() for point in it["points"]]
        if it.get("use_anchor") is False:
            m["use_anchor"] = False
        if it["form"] == "structure":
            m["structure"] = it["structure"]
            m["density"] = density_for(len(it["points"]))
        if plan.get("palette"):
            m["palette"] = plan["palette"]
        if it.get("text"):
            m["text"] = it["text"]
        out.append(m)
    return out
