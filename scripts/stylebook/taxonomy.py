"""画风库的地域 / 时代 / 朝代 / 风格分类标签：词表校验与从评审标签并入目录。

- 词表（family / region / era / dynasty）写在 styles/catalog.json 顶层 taxonomy 里，是页面和校验的唯一来源；
- 每个现役画风必须带 family、legacyFamily、region、era、dynasty；缺字段、重复、未知取值一律报错，不静默补默认；
- 资料暂时定不下来的画风登记在 taxonomy.pending：region 与 era 同时留空，页面显示为「资料待定」，不伪造来源；
- 标签表示画风参考语汇的文化来源与视觉时期，不是对具体文物年代的考证。
"""
from __future__ import annotations

import copy
import re

FIELDS = ("region", "era", "dynasty")
VOCAB_KEYS = ("family", *FIELDS)
NEW_KEYS = ("legacyFamily", *FIELDS)
SENTINEL = {"region": "跨地域", "era": "未限定", "dynasty": "跨朝代"}
MAX_MULTI = 3
PENDING_LABEL = "资料待定"
CHINA = "中国"
ROW_KEYS = {"id", "name", "confidence", "notes", "family", "region", "era", "dynasty"}
CODE_RE = re.compile(r"^C\d{2,3}$")


class TaxonomyError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("；".join(problems))


def _vocab_problems(tax) -> list[str]:
    if not isinstance(tax, dict):
        return ["catalog.taxonomy 必须是对象"]
    problems: list[str] = []
    extra = set(tax) - {"version", "note", *VOCAB_KEYS, "pending"}
    if extra:
        problems.append(f"taxonomy 有未知键：{sorted(extra)}")
    for k in VOCAB_KEYS:
        v = tax.get(k)
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
            problems.append(f"taxonomy.{k} 必须是非空字符串列表")
        elif len(set(v)) != len(v):
            problems.append(f"taxonomy.{k} 词表有重复项")
        elif PENDING_LABEL in v:
            problems.append(f"taxonomy.{k} 不能含保留词「{PENDING_LABEL}」")
    pend = tax.get("pending", [])
    if not isinstance(pend, list) or not all(isinstance(x, str) for x in pend) or len(set(pend)) != len(pend):
        problems.append("taxonomy.pending 必须是不重复的画风码列表")
    return problems


def validate_styles(styles: list[dict], tax) -> list[str]:
    """校验一组公开画风的标签字段，返回问题列表（空＝通过）。"""
    problems = _vocab_problems(tax)
    if problems:
        return problems
    pending = set(tax.get("pending", []))
    codes = [s.get("code") for s in styles]
    for code in sorted(pending - set(codes)):
        problems.append(f"taxonomy.pending 里的 {code} 不在目录里")
    for s in styles:
        code = s.get("code")
        where = f"{code}"
        for k in ("family", "legacyFamily"):
            if not isinstance(s.get(k), str) or not s[k].strip():
                problems.append(f"{where} 缺少 {k}")
        if isinstance(s.get("family"), str) and s["family"] not in tax["family"]:
            problems.append(f"{where} 的 family「{s['family']}」不在词表里")
        got: dict[str, list] = {}
        for k in FIELDS:
            v = s.get(k)
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                problems.append(f"{where} 缺少 {k}（必须是字符串列表）")
                continue
            got[k] = v
            if len(set(v)) != len(v):
                problems.append(f"{where} 的 {k} 有重复值：{v}")
            bad = [x for x in v if x not in tax[k]]
            if bad:
                problems.append(f"{where} 的 {k} 有词表外取值：{bad}")
            if len(v) > MAX_MULTI:
                problems.append(f"{where} 的 {k} 超过 {MAX_MULTI} 个取值")
            if SENTINEL[k] in v and len(v) != 1:
                problems.append(f"{where} 的 {k}「{SENTINEL[k]}」不能和其他取值并列：{v}")
        if len(got) < 3:
            continue
        if "北欧" in got["region"] and "欧洲" in got["region"]:
            problems.append(f"{where} 的 region 北欧与欧洲重复")
        if got["dynasty"] and CHINA not in got["region"]:
            problems.append(f"{where} 有 dynasty 但 region 不含中国")
        for k in ("region", "era"):
            if not got[k] and code not in pending:
                problems.append(f"{where} 的 {k} 为空，但未登记在 taxonomy.pending")
        if code in pending and (got["region"] or got["era"] or got["dynasty"]):
            problems.append(f"{where} 登记为资料待定，region / era / dynasty 必须全部留空")
    return problems


def _row_problems(row, seen_ids) -> list[str]:
    if not isinstance(row, dict):
        return ["标签行不是对象"]
    rid = row.get("id")
    p = []
    if set(row) != ROW_KEYS:
        p.append(f"{rid} 标签行字段不符：缺 {sorted(ROW_KEYS - set(row))} 多 {sorted(set(row) - ROW_KEYS)}")
    if not isinstance(rid, str):
        p.append(f"标签行 id 无效：{rid!r}")
    return p


def apply_labels(catalog_doc: dict, tags_doc: dict, *, vocab: dict, pending: dict[str, str] | None = None,
                 allow_extra: bool = True) -> tuple[dict, dict]:
    """把评审标签并入目录，返回（新目录, 报告）。只处理目录里现有的画风，其余标签行记为 extras。

    - 目录已含标签字段或 taxonomy 时拒绝（不静默覆盖）；
    - 标签缺行、重复行、字段缺失、名称对不上、词表外取值都抛 TaxonomyError；
    - pending：{画风码: 原因}，这些画风的 region / era 置空；原因只写进报告，不进目录。"""
    pending = dict(pending or {})
    problems: list[str] = []
    styles = catalog_doc.get("styles")
    if not isinstance(styles, list) or not styles:
        raise TaxonomyError(["目录没有 styles"])
    if "taxonomy" in catalog_doc:
        problems.append("目录已含 taxonomy，拒绝覆盖")
    for s in styles:
        if any(k in s for k in NEW_KEYS):
            problems.append(f"{s.get('code')} 已含标签字段，拒绝覆盖")
    rows = tags_doc.get("rows") if isinstance(tags_doc, dict) else None
    if not isinstance(rows, list):
        raise TaxonomyError(["标签文件缺少 rows 列表"])
    by: dict[str, dict] = {}
    for r in rows:
        problems += _row_problems(r, by)
        rid = r.get("id") if isinstance(r, dict) else None
        if isinstance(rid, str):
            if rid in by:
                problems.append(f"标签 {rid} 出现多行（重复）")
            by[rid] = r
    cat_codes = [s["code"] for s in styles]
    if len(set(cat_codes)) != len(cat_codes):
        problems.append("目录里画风码有重复")
    extras = sorted(i for i in by if i not in set(cat_codes))
    bad_extra = [i for i in extras if not CODE_RE.match(i)]
    if bad_extra:
        problems.append(f"标签里有格式不对的未知 id：{bad_extra}")
    if extras and not allow_extra:
        problems.append(f"标签里有目录没有的 id（严格模式拒绝）：{extras}")
    for c in cat_codes:
        if c not in by:
            problems.append(f"目录里的 {c} 在标签里没有对应行")
    for pid in pending:
        if pid not in by or pid not in cat_codes:
            problems.append(f"资料待定登记的 {pid} 不在目录或标签里")
    if problems:
        raise TaxonomyError(problems)

    out = copy.deepcopy(catalog_doc)
    new_styles = []
    for s in out["styles"]:
        r = by[s["code"]]
        if r.get("name") != s.get("zh"):
            problems.append(f"{s['code']} 名称对不上：目录「{s.get('zh')}」标签「{r.get('name')}」")
        legacy = s["family"]
        ns: dict = {}
        for k, v in s.items():
            if k == "family":
                ns["family"] = r.get("family")
                ns["legacyFamily"] = legacy
                for f in FIELDS:
                    ns[f] = [] if s["code"] in pending and f != "dynasty" else copy.deepcopy(r.get(f))
                if s["code"] in pending:
                    ns["dynasty"] = []
            else:
                ns[k] = v
        new_styles.append(ns)
    out["styles"] = new_styles
    out["taxonomy"] = {"version": 1, **{k: list(vocab[k]) for k in VOCAB_KEYS}, "pending": sorted(pending)}
    problems += validate_styles(out["styles"], out["taxonomy"])
    if problems:
        raise TaxonomyError(problems)
    report = {"styles": len(out["styles"]), "extras_ignored": extras, "pending": sorted(pending),
              "tag_rows": len(rows)}
    return out, report


def unchanged_problems(src: dict, new: dict) -> list[str]:
    """除 family 被替换、新增标签字段与顶层 taxonomy 外，目录其余内容必须逐项相同。"""
    p: list[str] = []
    if {k: v for k, v in src.items() if k != "styles"} != {k: v for k, v in new.items() if k not in ("styles", "taxonomy")}:
        p.append("目录顶层（除 taxonomy）被改动")
    if [s["code"] for s in src["styles"]] != [s["code"] for s in new["styles"]]:
        p.append("画风顺序或数量变了")
        return p
    for a, b in zip(src["styles"], new["styles"]):
        strip = {k: v for k, v in b.items() if k not in NEW_KEYS}
        strip["family"] = b.get("legacyFamily")
        if a != strip:
            p.append(f"{a['code']} 除标签字段外的内容被改动")
        if list(a) != [k for k in b if k not in NEW_KEYS]:
            p.append(f"{a['code']} 原字段顺序变了")
    return p
